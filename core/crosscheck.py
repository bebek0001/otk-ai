# -*- coding: utf-8 -*-
"""Сверка штампа CDW с записью базы эталонов (PDF или таблица технолога)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.analogs import sort_head

MASS_TOL = 0.10


def _mass(v) -> Optional[float]:
    try:
        x = float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None
    return x if x and x > 0 else None


def compare(card: Dict[str, Any], etalon: Dict[str, Any]) -> List[str]:
    """Список расхождений; пустой, если данные согласованы."""
    notes: List[str] = []
    stamp = (card.get("material") or "").split(";")[0].strip()
    first = (etalon.get("result_line") or "").split("\n")[0]
    sh, eh = sort_head(stamp), sort_head(first)
    if sh and eh and sh != eh:
        notes.append(f"в штампе «{stamp}», в базе «{first.strip(' ;')}»")
    m_card, m_db = _mass(card.get("mass")), _mass(etalon.get("mass_clean_kg"))
    if m_card and m_db and abs(m_card - m_db) / max(m_card, m_db) > MASS_TOL:
        notes.append(f"масса детали в штампе {m_card:g} кг, в базе {m_db:g} кг")
    return notes


def missing_clean_mass(card: Dict[str, Any], etalon: Dict[str, Any]) -> Optional[float]:
    """Чистая масса из чертежа, если в записи базы её нет."""
    if _mass(etalon.get("mass_clean_kg")):
        return None
    return _mass(card.get("mass"))
