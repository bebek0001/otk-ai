# -*- coding: utf-8 -*-
"""
core/activity_log.py — локальный журнал обработки чертежей.

Назначение: на компьютере пользователя (например, на предприятии)
накапливается история того, какие файлы ему скормили в пакетную
обработку, что приложение из них прочитало и чем закончилась обработка
каждого (успех / предупреждение / ошибка). Это не база эталонов и не
влияет на расчёты — чисто журнал для последующего разбора (например,
удалённо, когда разработчика нет рядом).

Файлы лежат по одному на день:
    <папка пользовательских данных>/processing_log/YYYY-MM-DD.jsonl

Формат — JSON Lines (одна запись — одна строка), чтобы можно было
дописывать построчно, не перечитывая и не переписывая весь файл.

Запись в журнал никогда не должна ронять обработку чертежа — любая
ошибка здесь только логируется через core.logger и подавляется.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from core.paths import PROCESSING_LOG_DIR
from core import logger


def _today_log_path():
    day = datetime.now().strftime("%Y-%m-%d")
    return PROCESSING_LOG_DIR / f"{day}.jsonl"


def log_processing(
    *,
    source_path: str,
    mode: str,
    status: str,
    source_kind: str = "",
    message: str = "",
    used_ai: bool = False,
    result: Optional[Dict[str, Any]] = None,
    raw_text: str = "",
    raw_text_limit: int = 2000,
) -> None:
    """
    Добавляет одну запись в журнал обработки за сегодня.

    source_path     — путь к обработанному файлу
    mode            — "batch" (пакетная обработка) или "single"
    status          — как в таблице результатов ("ОК", "⚠ ...", "❌ ошибка")
    source_kind     — "pdf" / "cdw" и т.п.
    message         — текст ошибки/предупреждения, если был
    used_ai         — обращались ли к ИИ при обработке этого файла
    result          — итоговый словарь результата (как в batch_tool.py),
                      из него вытаскиваются основные поля для записи
    raw_text        — сырой текст, прочитанный из чертежа (для разбора,
                       что реально было в файле), обрезается до raw_text_limit
    """
    try:
        PROCESSING_LOG_DIR.mkdir(parents=True, exist_ok=True)

        record: Dict[str, Any] = {
            "timestamp":   datetime.now().isoformat(timespec="seconds"),
            "source_file": os.path.basename(source_path) if source_path else "",
            "source_path": source_path or "",
            "source_kind": source_kind,
            "mode":        mode,
            "status":      status,
            "message":     message,
            "used_ai":     bool(used_ai),
        }

        if result:
            record.update({
                "drawing_no":        result.get("drawing_no", ""),
                "part_name":         result.get("part_name", ""),
                "material":          result.get("material", ""),
                "drawing_sortament": result.get("drawing_sortament", ""),
                "new_sortament":     result.get("new_sortament", ""),
                "stock_size":        result.get("stock_size", ""),
                "stock_mass_kg":     result.get("stock_mass_kg", ""),
                "clean_mass_kg":     result.get("clean_mass_kg", ""),
                "source":            result.get("source", ""),
            })

        if raw_text:
            record["raw_text_excerpt"] = raw_text[:raw_text_limit]

        with open(_today_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception as e:                                  # noqa: BLE001
        # Журнал — вспомогательная вещь, из-за него обработка падать не должна.
        logger.warn(f"Не удалось записать в журнал обработки: {e}")
