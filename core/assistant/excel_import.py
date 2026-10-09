# -*- coding: utf-8 -*-
"""
Импорт исправленного Excel: строки "деталь -> правильный сортамент" становятся
обучающими примерами. Колонки ищутся по заголовкам; формат клиента уточняется.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.assistant import store

_COLS = {
    "name":  ("наименование", "название", "деталь"),
    "no":    ("обозначение", "номер чертеж", "децимальн"),
    "mat":   ("материал",),
    "len":   ("длина",),
    "stock": ("сортамент", "заготовка"),
}


def stock_type_from_text(text: str) -> Optional[str]:
    t = (text or "").lower().replace("ё", "е")
    if "шестигран" in t: return "Шестигранник"
    if "швеллер" in t:   return "Швеллер"
    if "двутавр" in t or "балка" in t: return "Двутавр"
    if "уголок" in t:    return "УголокНеравн" if "неравн" in t else "Уголок"
    if "полос" in t:     return "Полоса"
    if "квадрат" in t:   return "Квадрат"
    if "круг" in t:      return "Круг"
    if "труба" in t:
        return "ТрубаПроф" if ("проф" in t or "8639" in t or "8645" in t) else "Труба"
    if "лист" in t:
        return "ЛистХК" if ("19904" in t or "хк" in t) else "ЛистГК"
    return None


def _find_cols(header: List[str]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for i, h in enumerate(header):
        hl = (h or "").lower()
        for key, subs in _COLS.items():
            if key not in found and any(s in hl for s in subs):
                found[key] = i
    return found


def import_corrected_excel(path: str) -> Tuple[int, int]:
    """Возвращает (добавлено решений, пропущено строк)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    added = skipped = 0
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hdr_i, cols = None, {}
        for i, r in enumerate(rows[:15]):
            c = _find_cols([str(x) if x is not None else "" for x in r])
            if "name" in c and "stock" in c:
                hdr_i, cols = i, c
                break
        if hdr_i is None:
            continue
        for r in rows[hdr_i + 1:]:
            get = lambda k: (r[cols[k]] if k in cols and cols[k] < len(r) else None)
            st = stock_type_from_text(str(get("stock") or ""))
            name = str(get("name") or "").strip()
            if not st or not name:
                skipped += 1
                continue
            ln = get("len")
            try:
                ln = float(str(ln).replace(",", ".")) if ln not in (None, "") else None
            except ValueError:
                ln = None
            store.add_decision(name, str(get("mat") or ""), ln, st,
                               drawing_no=str(get("no") or "").strip(),
                               source="excel_import")
            added += 1
    return added, skipped
