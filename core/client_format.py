# -*- coding: utf-8 -*-
"""
Выгрузка в формате таблицы заказчика (образец: акт 03.08.2026).

Колонки: № | № чертежа | Наименование | Заготовка | Размер заготовки |
Масса заг., кг | Масса чист., кг  (+ необязательно «Ошибка» для PLM).
Без цветов, без служебных строк и колонки «Источник».
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

HEADERS = ["№", "№ чертежа", "Наименование", "Заготовка",
           "Размер заготовки", "Масса заг., кг", "Масса чист., кг"]
ERROR_HEADER = "Ошибка"


def _gost_spaced(s: str) -> str:
    return re.sub(r"ГОСТ\s*(?=\d)", "ГОСТ ", s or "")


def _mass(v: Any) -> str:
    """0,60 -> 0,6; точка -> запятая (как в таблице заказчика)."""
    s = str(v if v is not None else "").strip().replace(".", ",")
    if re.fullmatch(r"\d+,\d+", s):
        s = s.rstrip("0").rstrip(",")
    return s


def _zagotovka(r: Dict[str, Any]) -> str:
    s = _gost_spaced(r.get("new_sortament", "")).strip()
    if s and "/" not in s:
        mat = _gost_spaced(r.get("material", "")).strip()
        if mat:
            s = f"{s}/ {mat}"
    return s


def _size(r: Dict[str, Any]) -> str:
    size = str(r.get("stock_size", "") or "").strip()
    if not size:
        return ""
    if re.match(r"[A-Za-zА-Яа-яЁё]{3,}", size):          # уже "Шестигранник 24, L=150"
        return size
    head = re.split(r"\s*ГОСТ", r.get("new_sortament", ""))[0].strip()
    return f"{head}, {size}" if head else size


def _drawing_sort_key(drawing_no: str):
    """Натуральная сортировка по номеру чертежа; без номера — в конец."""
    if not drawing_no:
        return (1, ())
    return (0, tuple(int(x) for x in re.findall(r"\d+", drawing_no)))


def rows_for_client(results: List[Dict[str, Any]], include_errors: bool = True):
    """Строки таблицы. Позиции с ошибкой остаются в таблице с пустой заготовкой."""
    rows = []
    results = [r for r in results if r.get("status") != "Сборка (пропуск)"]
    for i, r in enumerate(sorted(results, key=lambda x: _drawing_sort_key(x.get("drawing_no", ""))), 1):
        ok = r.get("status") in ("ОК", "⚠ проверить длину")
        row = [i, r.get("drawing_no", ""), r.get("part_name", ""),
               _zagotovka(r) if ok else "", _size(r) if ok else "",
               _mass(r.get("stock_mass_kg")) if ok else "",
               _mass(r.get("clean_mass_kg"))]
        if include_errors:
            row.append("" if ok else (r.get("error") or r.get("status", "")))
        rows.append(row)
    return rows


def export_client_xlsx(results: List[Dict[str, Any]], save_path: str,
                       include_errors: bool = True) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Заготовки"
    hdr = HEADERS + ([ERROR_HEADER] if include_errors else [])
    ws.append(hdr)
    for row in rows_for_client(results, include_errors):
        ws.append(row)

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    font = Font(name="Times New Roman", size=11)
    for ri, row in enumerate(ws.iter_rows(), 1):
        for c in row:
            c.font = Font(name="Times New Roman", size=11, bold=(ri == 1))
            c.border = border
            c.alignment = Alignment(
                horizontal="center" if (ri == 1 or c.column in (1, 6, 7)) else "left",
                vertical="center", wrap_text=True)
    for col, w in zip("ABCDEFGH", (5, 18, 22, 48, 30, 14, 14, 36)):
        ws.column_dimensions[col].width = w
    wb.save(save_path)
