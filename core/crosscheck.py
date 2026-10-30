# -*- coding: utf-8 -*-
"""
Сверка данных чертежа КОМПАС (CDW) с записью в базе эталонов (из PDF или
из таблицы технолога). Одна деталь должна читаться одинаково из любого
источника, иначе где-то ошибка: в штампе, в базе или в ответе технолога.

Проверяется:
  * вид и размер проката в штампе («Лист 4», «Круг 130») и в ответе базы;
  * чистая масса из штампа и масса детали в базе (допуск MASS_TOL).
Расхождение не отменяет ответ базы, а только помечает строку для проверки.
Если в базе нет чистой массы, она берётся из чертежа (дополнение записи).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.analogs import sort_head

MASS_TOL = 0.10


def _mass(v) -> Optional[float]:
    try:
        x = float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None
    return x if x and x > 0 else None


def _decimals(v) -> int:
    """Число знаков после запятой у значения в штампе (0.02 -> 2, 62 -> 0)."""
    t = str(v).replace(",", ".")
    return len(t.split(".")[1].rstrip("0")) if "." in t else 0


def _norm_head(h: str) -> str:
    """«шестигранник h11-13» и «лист а5.м 1.5» приводятся к одному виду."""
    h = re.sub(r"(?<=\s)h(?=\d)", "", h)
    return h.replace(".м", "м").replace(" ", "")


_STOCK_WORDS = ("лист", "круг", "квадрат", "шестигран", "труба", "уголок", "швеллер",
                "балка", "двутавр", "профиль", "полоса", "лента", "пруток")


def compare(card: Dict[str, Any], etalon: Dict[str, Any]) -> List[str]:
    """Список расхождений; пустой, если данные согласованы."""
    notes: List[str] = []
    stamp = (card.get("material") or "").split(";")[0].strip()
    first = (etalon.get("result_line") or "").split("\n")[0]
    sh, eh = sort_head(stamp), sort_head(first)
    # В штампе может стоять только марка стали без вида проката: это не расхождение
    if (sh and eh and sh.startswith(_STOCK_WORDS) and _norm_head(sh) != _norm_head(eh)):
        notes.append(f"в штампе «{stamp}», в базе «{first.strip(' ;')}»")
    m_card, m_db = _mass(card.get("mass")), _mass(etalon.get("mass_clean_kg"))
    if m_card and m_db:
        # масса в штампе округлена до последнего знака: допуск не меньше этого шага
        step = 10 ** -_decimals(card.get("mass"))
        if abs(m_card - m_db) > max(MASS_TOL * max(m_card, m_db), step):
            notes.append(f"масса детали в штампе {m_card:g} кг, в базе {m_db:g} кг")
    return notes


def missing_clean_mass(card: Dict[str, Any], etalon: Dict[str, Any]) -> Optional[float]:
    """Чистая масса из чертежа, если в записи базы её нет."""
    if _mass(etalon.get("mass_clean_kg")):
        return None
    return _mass(card.get("mass"))
