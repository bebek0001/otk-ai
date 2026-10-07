# -*- coding: utf-8 -*-
"""
CONFIRM_QUEUE.PY — очередь подтверждения для результатов RAG/ИИ-добора.

Зачем. В базу эталонов (core/drawing_db.py) чертежи должны попадать
только как ПРОВЕРЕННЫЕ образцы — на них учится весь дальнейший RAG-слой.
Детерминированный расчёт (по ГОСТ/формулам, в т.ч. CDW+расчёт) — это
точная арифметика по точным данным, его можно сохранять в базу сразу
по решению технолога, как и раньше.

Но когда ответ сформирован RAG-слоем (найден похожий чертёж в базе) или
Vision (GPT посмотрел на картинку и угадал) — это ПРЕДПОЛОЖЕНИЕ, а не
факт. Если такой ответ тут же уйдёт в базу эталонов как новый образец,
ошибка размножится: следующий RAG-поиск найдёт уже не исходный точный
чертёж, а чужую угадку, выданную за эталон.

Поэтому такие результаты сначала попадают сюда, в очередь подтверждения,
и становятся эталоном только после того, как технолог явно подтвердит
их во вкладке «Очередь подтверждения».

Формат записи в очереди:
    {
        "id": "a1b2c3...",            # уникальный id записи в очереди
        "added_at": "2026-10-07T12:00:00",
        "source_kind": "rag" | "vision",
        "source_file": "...pdf" | "...cdw" | "",   # необязательно
        "part_name": "...",
        "drawing_no": "...",            # необязательно, для информации
        "pdf_text": "...",              # нужен save_etalon для хэша/поиска
        "stock_type": "...",
        "gost_stock": "...",
        "d_blank_std_mm": 130,
        "l_part_mm": 4660.0,
        "l_blank_mm": 4680.0,
        "material_mark": "40Х",
        "material_gost": "ГОСТ4543-2016",
        "mass_kg": 487.63,
        "mass_clean_kg": None,
        "result_line": "...",
        "protocol": "...",              # для показа технологу, не идёт в базу
    }
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core import logger
from core.paths import CONFIRM_QUEUE_PATH
from core import drawing_db

QUEUE_PATH = str(CONFIRM_QUEUE_PATH)


# ============================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
# ============================================================

def load_queue() -> List[Dict[str, Any]]:
    if not os.path.exists(QUEUE_PATH):
        return []
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:                                    # noqa: BLE001
        logger.warn(f"Ошибка загрузки очереди подтверждения: {e}")
        return []


def _save_queue(items: List[Dict[str, Any]]) -> None:
    try:
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:                                    # noqa: BLE001
        logger.error(f"Ошибка сохранения очереди подтверждения: {e}")
        raise


# ============================================================
# ДОБАВЛЕНИЕ / ЧТЕНИЕ
# ============================================================

def add_pending(fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Кладёт результат RAG/ИИ-добора в очередь подтверждения.
    `fields` — те же поля, что принимает drawing_db.save_etalon(),
    плюс необязательные source_kind/source_file/drawing_no/protocol.
    """
    items = load_queue()

    record = dict(fields)
    record["id"] = uuid.uuid4().hex[:12]
    record["added_at"] = datetime.now().isoformat(timespec="seconds")
    record.setdefault("source_kind", "rag")
    record.setdefault("source_file", "")
    record.setdefault("drawing_no", "")
    record.setdefault("protocol", "")

    items.append(record)
    _save_queue(items)
    logger.info(
        f"Очередь подтверждения: добавлено «{record.get('part_name', '?')}» "
        f"({record.get('source_kind')}), всего в очереди: {len(items)}"
    )
    return record


def list_pending() -> List[Dict[str, Any]]:
    return load_queue()


def get_pending(item_id: str) -> Optional[Dict[str, Any]]:
    for r in load_queue():
        if r.get("id") == item_id:
            return r
    return None


def count_pending() -> int:
    return len(load_queue())


# ============================================================
# ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ
# ============================================================

_SAVE_ETALON_FIELDS = (
    "pdf_text", "part_name", "stock_type", "gost_stock", "d_blank_std_mm",
    "l_part_mm", "l_blank_mm", "material_mark", "material_gost",
    "mass_kg", "result_line", "mass_clean_kg",
)


def approve(item_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Технолог подтвердил запись: она уходит в базу эталонов через
    drawing_db.save_etalon() и убирается из очереди.

    `overrides` — если технолог перед подтверждением что-то поправил
    (например, марку материала), эти значения перекрывают угаданные.
    """
    items = load_queue()
    idx = next((i for i, r in enumerate(items) if r.get("id") == item_id), None)
    if idx is None:
        raise ValueError(f"Запись {item_id} не найдена в очереди подтверждения")

    record = dict(items[idx])
    if overrides:
        record.update(overrides)

    kwargs = {k: record.get(k) for k in _SAVE_ETALON_FIELDS}
    saved = drawing_db.save_etalon(**kwargs)

    del items[idx]
    _save_queue(items)
    logger.info(
        f"Очередь подтверждения: «{record.get('part_name', '?')}» подтверждён "
        f"и сохранён в базу эталонов технологом"
    )
    return saved


def reject(item_id: str, reason: str = "") -> bool:
    """Технолог отклонил угадку — просто убираем из очереди, в базу ничего не пишем."""
    items = load_queue()
    before = len(items)
    removed = [r for r in items if r.get("id") == item_id]
    items = [r for r in items if r.get("id") != item_id]
    if len(items) == before:
        return False
    _save_queue(items)
    name = removed[0].get("part_name", "?") if removed else "?"
    logger.info(
        f"Очередь подтверждения: «{name}» отклонён технологом"
        + (f" ({reason})" if reason else "")
    )
    return True


def calc_result_to_pending_fields(
    res, pdf_text: str, part_name: str, source_file: str = "",
    drawing_no: str = "",
) -> Dict[str, Any]:
    """
    Собирает словарь полей для add_pending() из engine.CalcResult.
    Общее место для drawing_tool.py и batch_tool.py, чтобы не дублировать.
    """
    return {
        "pdf_text": pdf_text,
        "part_name": part_name,
        "stock_type": res.stock_type,
        "gost_stock": res.gost_stock,
        "d_blank_std_mm": res.d_blank_std_mm,
        "l_part_mm": res.l_part_mm,
        "l_blank_mm": res.l_blank_mm,
        "material_mark": res.material_out,
        "material_gost": res.material_gost,
        "mass_kg": res.mass_kg,
        "mass_clean_kg": getattr(res, "mass_stamp_kg", None),
        "result_line": res.result_line,
        "protocol": res.protocol,
        "source_kind": getattr(res, "source_kind", "rag"),
        "source_file": source_file,
        "drawing_no": drawing_no,
    }
