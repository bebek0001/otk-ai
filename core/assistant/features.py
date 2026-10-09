# -*- coding: utf-8 -*-
"""Признаки детали для модели: название, материал, длина -> одна строка."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", s).strip()


def length_bucket(length_mm: Optional[float]) -> str:
    try:
        v = float(length_mm)
    except (TypeError, ValueError):
        return "len_none"
    if v <= 0:
        return "len_none"
    return f"len_{int(math.log2(v))}"


def sample_text(name: str, material: str = "",
                length_mm: Optional[float] = None) -> str:
    """Текст для TF-IDF: слова названия + марка материала + корзина длины."""
    n = _norm(name)
    m = _norm(material).replace(" ", "")
    return f"{n} mat_{m} {length_bucket(length_mm)}"


def sample_from_record(r: Dict[str, Any]) -> Dict[str, Any]:
    """Запись базы эталонов / решения -> образец для обучения."""
    return {
        "name": r.get("part_name") or "",
        "material": r.get("material_mark") or r.get("material") or "",
        "length_mm": r.get("l_part_mm") or r.get("length_mm"),
        "label": r.get("stock_type") or "",
        "key": (r.get("drawing_no") or r.get("pdf_text_hash") or
                f"{r.get('part_name','')}|{r.get('material_mark','')}|{r.get('l_part_mm')}"),
        "drawing_no": r.get("drawing_no") or "",
    }
