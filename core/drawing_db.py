# ===========================
# DRAWING_DB.PY — база эталонных чертежей
# ===========================
#
# Логика:
#   При каждом успешном расчёте пользователь может нажать
#   "Сохранить эталон" — и чертёж + правильный ответ сохраняются в JSON.
#
#   При следующем расчёте engine сначала ищет похожий чертёж в базе.
#   Если находит — берёт оттуда готовые параметры и подтверждает через ИИ.
#
# Формат записи:
#   {
#     "drawing_no": "23.14.02.004",
#     "part_name": "Тяга",
#     "stock_type": "Круг",
#     "gost_stock": "ГОСТ2590-2006",
#     "d_blank_std_mm": 130,
#     "l_part_mm": 4660.0,
#     "l_blank_mm": 4680.0,
#     "material_mark": "40Х",
#     "material_gost": "ГОСТ4543-2016",
#     "mass_kg": 487.63,
#     "result_line": "Круг 130 ГОСТ2590-2006;\nL=4680;\nМасса заготовки, кг: 487,63",
#     "pdf_text_hash": "abc123...",   # для быстрого поиска
#     "saved_at": "2026-04-23T23:00:00"
#   }

import json
import os
import re
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from core import logger


from core.paths import DB_PATH as USER_DB_PATH

DB_PATH = str(USER_DB_PATH)


# ============================================================
# УТИЛИТЫ
# ============================================================

def _text_hash(pdf_text: str) -> str:
    """Хэш текста PDF для быстрого поиска дублей."""
    normalized = re.sub(r"\s+", " ", (pdf_text or "")).strip().lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _normalize_drawing_no(s: str) -> str:
    """Нормализует номер чертежа: убирает пробелы и приводит к нижнему регистру."""
    return re.sub(r"\s+", "", (s or "")).lower()


def _extract_drawing_no(pdf_text: str, part_name: str) -> str:
    """
    Пытается найти номер чертежа в тексте штампа.
    Например: 23.14.02.004 — ищем паттерн с 3+ сегментами через точку
    где хотя бы один сегмент > 2 символов (чтобы не путать с датами 26.02.2020)
    """
    # Ищем паттерн вида NN.NN.NN.NNN — минимум 4 сегмента (дата имеет только 3)
    m = re.search(r"\b(\d{2,3}\.\d{2}\.\d{2,3}\.\d{3,})\b", pdf_text)
    if m:
        return m.group(1).strip()
    # Fallback: 3 сегмента но не дата (дата: NN.NN.NNNN)
    m2 = re.search(r"\b(\d{2,3}\.\d{2,3}\.\d{3,})\b", pdf_text)
    if m2:
        candidate = m2.group(1)
        # Исключаем даты типа 26.02.2020
        if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", candidate):
            return candidate.strip()
    return part_name or ""


# ============================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
# ============================================================

def load_db() -> List[Dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warn(f"Ошибка загрузки базы чертежей: {e}")
        return []


def save_db(records: List[Dict[str, Any]]) -> None:
    try:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info(f"База чертежей сохранена: {len(records)} записей → {DB_PATH}")
    except Exception as e:
        logger.error(f"Ошибка сохранения базы чертежей: {e}")
        raise


# ============================================================
# ДОБАВЛЕНИЕ ЭТАЛОНА
# ============================================================

def save_etalon(
    pdf_text: str,
    part_name: str,
    stock_type: str,
    gost_stock: str,
    d_blank_std_mm: int,
    l_part_mm: float,
    l_blank_mm: float,
    material_mark: str,
    material_gost: str,
    mass_kg: float,
    result_line: str,
    mass_clean_kg: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Сохраняет эталонный расчёт в базу.
    Если чертёж с таким хэшем уже есть — обновляет запись.

    mass_clean_kg — чистовая масса детали (необязательно). Если не передана,
    запись сохраняется без неё (обратная совместимость со старым кодом).
    """
    records = load_db()
    h = _text_hash(pdf_text)
    drawing_no = _extract_drawing_no(pdf_text, part_name)

    record: Dict[str, Any] = {
        "drawing_no": drawing_no,
        "part_name": part_name,
        "stock_type": stock_type,
        "gost_stock": gost_stock,
        "d_blank_std_mm": int(d_blank_std_mm),
        "l_part_mm": float(l_part_mm),
        "l_blank_mm": float(l_blank_mm),
        "material_mark": material_mark,
        "material_gost": material_gost,
        "mass_kg": round(float(mass_kg), 3),
        "mass_clean_kg": (round(float(mass_clean_kg), 3) if mass_clean_kg is not None else None),
        "result_line": result_line,
        "pdf_text_hash": h,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    # Обновляем если уже есть
    updated = False
    for i, r in enumerate(records):
        if r.get("pdf_text_hash") == h:
            records[i] = record
            updated = True
            break

    if not updated:
        records.append(record)

    save_db(records)
    logger.info(f"Эталон сохранён: {drawing_no} / {part_name} ({'обновлён' if updated else 'новый'})")
    return record


# ============================================================
# ПОИСК В БАЗЕ
# ============================================================

def lookup_by_hash(pdf_text: str) -> Optional[Dict[str, Any]]:
    """Точное совпадение по хэшу текста PDF."""
    h = _text_hash(pdf_text)
    for r in load_db():
        if r.get("pdf_text_hash") == h:
            logger.info(f"База: точное совпадение по хэшу для '{r.get('part_name')}'")
            return r
    return None


def lookup_by_drawing_no(drawing_no: str) -> Optional[Dict[str, Any]]:
    """Поиск по номеру чертежа (нечувствителен к регистру/пробелам)."""
    key = _normalize_drawing_no(drawing_no)
    for r in load_db():
        if _normalize_drawing_no(r.get("drawing_no", "")) == key:
            logger.info(f"База: совпадение по номеру чертежа '{drawing_no}'")
            return r
    return None


def lookup_etalon(pdf_text: str, part_name: str = "") -> Optional[Dict[str, Any]]:
    """
    Главная функция поиска: сначала по хэшу, потом по номеру чертежа.
    Возвращает эталонную запись или None.
    """
    # 1. Точный поиск по хэшу PDF-текста
    r = lookup_by_hash(pdf_text)
    if r:
        return r

    # 2. Поиск по номеру чертежа из текста штампа
    drawing_no = _extract_drawing_no(pdf_text, part_name)
    if drawing_no:
        r = lookup_by_drawing_no(drawing_no)
        if r:
            return r

    return None


# ============================================================
# УПРАВЛЕНИЕ БАЗОЙ
# ============================================================

def get_all_etalons() -> List[Dict[str, Any]]:
    """Возвращает все записи базы."""
    return load_db()


def delete_etalon(pdf_text_hash: str) -> bool:
    """Удаляет запись по хэшу. Возвращает True если удалена."""
    records = load_db()
    before = len(records)
    records = [r for r in records if r.get("pdf_text_hash") != pdf_text_hash]
    if len(records) < before:
        save_db(records)
        logger.info(f"Эталон удалён: hash={pdf_text_hash}")
        return True
    return False


def db_stats() -> Dict[str, Any]:
    """Статистика базы."""
    records = load_db()
    types: Dict[str, int] = {}
    for r in records:
        t = r.get("stock_type", "?")
        types[t] = types.get(t, 0) + 1
    return {
        "total": len(records),
        "by_type": types,
        "db_path": DB_PATH,
    }


# ============================================================
# ПОИСК ПОХОЖИХ ЭТАЛОНОВ (для обучения ИИ)
# ============================================================

def find_similar_etalons(
    stock_type: str,
    material_mark: str = "",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Возвращает до `limit` эталонов с таким же типом заготовки.
    Приоритет — совпадение по марке материала.
    Используется для подстановки примеров в промпт ИИ.
    """
    records = load_db()
    if not records:
        return []

    mat_norm = (material_mark or "").strip().lower()

    exact: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []

    for r in records:
        if r.get("stock_type", "") != stock_type:
            continue
        if mat_norm and mat_norm in (r.get("material_mark", "") or "").lower():
            exact.append(r)
        else:
            other.append(r)

    result = (exact + other)[:limit]
    logger.debug(f"Похожие эталоны ({stock_type}): найдено {len(result)}")
    return result


def build_etalon_examples_block(stock_type: str, material_mark: str = "") -> str:
    """
    Формирует текстовый блок с примерами из базы для вставки в промпт ИИ.
    """
    examples = find_similar_etalons(stock_type, material_mark, limit=3)
    if not examples:
        return ""

    lines = ["ПРИМЕРЫ ИЗ БАЗЫ ЭТАЛОННЫХ ЧЕРТЕЖЕЙ (реальные проверенные случаи):"]
    for i, r in enumerate(examples, 1):
        lines.append(
            f"\n[Пример {i}]"
            f"\n  Деталь: {r.get('part_name', '—')} (чертёж {r.get('drawing_no', '—')})"
            f"\n  Материал: {r.get('material_mark', '—')} {r.get('material_gost', '—')}"
            f"\n  Заготовка: {r.get('result_line', '—').replace(chr(10), ' | ')}"
            f"\n  L детали: {r.get('l_part_mm', '—')} мм → L заготовки: {r.get('l_blank_mm', '—')} мм"
            f"\n  Масса: {r.get('mass_kg', '—')} кг"
        )
    lines.append("")
    return "\n".join(lines)