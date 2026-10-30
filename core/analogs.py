# -*- coding: utf-8 -*-
"""
Поиск аналога в базе эталонов.

Серийные детали («Стенка», «Ребро», «Заглушка») при одинаковом сортаменте и
почти одинаковой массе обычно режутся из одной и той же заготовки. Если в базе
уже есть такая деталь с проверенным ответом, он предлагается как ответ для
новой, с пометкой «проверить»: аналог не равен эталону по номеру чертежа.

Совпадение требует всех условий:
  * то же наименование (без регистра и хвостовых цифр);
  * тот же сортамент с размером («Лист 4», «Круг 30»);
  * чистая масса отличается не более чем на tol (по умолчанию 5%);
  * ответ технолога по аналогу содержит размер заготовки.
Если найденные аналоги расходятся между собой по размеру, берётся самый частый,
а уверенность понижается.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MASS_TOL = 0.05


@dataclass
class Analog:
    size_text: str = ""
    stock_mass_kg: Optional[float] = None
    n: int = 0                 # сколько аналогов найдено
    n_agree: int = 0           # сколько из них дают выбранный размер
    sources: List[str] = field(default_factory=list)
    confidence: str = "нет"    # высокая | низкая | нет
    reason: str = ""           # почему аналога нет (для журнала)


def norm_name(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"@.*$", "", s)                  # «Корпус@/Сборочный чертеж»
    s = re.sub(r"[\d\W_]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def sort_head(sortament: str) -> str:
    """«Лист 4 ГОСТ 19903-2015» -> «лист 4»."""
    t = (sortament or "").split(";")[0].split("/")[0]
    t = re.split(r"\s*(?:ГОСТ|ТУ)\b", t, flags=re.I)[0]
    t = t.lower().replace("х", "x").replace(",", ".")
    return re.sub(r"\s+", " ", t).strip()


def _size_line(result_line: str) -> str:
    lines = [x.strip().rstrip(";") for x in (result_line or "").splitlines() if x.strip()]
    return lines[1] if len(lines) > 1 else ""


def _norm_size(s: str) -> str:
    return re.sub(r"[\s,]+", "", (s or "").lower().replace("х", "x").replace("×", "x"))


def _mass_from_line(result_line: str) -> Optional[float]:
    m = re.search(r"Масса заготовки,?\s*кг:\s*([\d.,]+)", result_line or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


GAB_MARGIN_MM = 30.0     # заготовка больше габарита детали не более чем на столько


def _blank_dims(size: str):
    """«HxL = 170x530» -> (170, 530) по возрастанию; «L=94» -> ('L', 94)."""
    t = (size or "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[xх×]\s*(\d+(?:\.\d+)?)", t)
    if m:
        a, b = sorted((float(m.group(1)), float(m.group(2))))
        return ("HL", a, b)
    m = re.search(r"L\s*=\s*(\d+(?:\.\d+)?)", t)
    return ("L", float(m.group(1)), None) if m else None


def _fits(base_size: str, part_dims, part_length) -> Optional[bool]:
    """Согласуется ли заготовка из базы с габаритами детали.
    True/False; None, если проверить нечем."""
    bd = _blank_dims(base_size)
    if not bd:
        return None
    if bd[0] == "HL" and part_dims:
        a, b = part_dims
        return (bd[1] >= a - 1 and bd[2] >= b - 1
                and bd[1] - a <= GAB_MARGIN_MM and bd[2] - b <= GAB_MARGIN_MM)
    if bd[0] == "L" and part_length:
        return part_length - 1 <= bd[1] <= part_length + GAB_MARGIN_MM
    return None


def find_analog(part_name: str, stamp_sortament: str, clean_mass_kg: Optional[float],
                db: List[Dict[str, Any]], drawing_no: str = "",
                tol: float = MASS_TOL, part_dims=None, part_length=None) -> Analog:
    """Аналог ищется по сортаменту, чистой массе и ГАБАРИТАМ детали; наименование
    не используется. Без габаритов допуск по массе вдвое жёстче, уверенность низкая."""
    head_k = sort_head(stamp_sortament)
    no_k = re.sub(r"\s+", "", drawing_no or "").lower()
    if not head_k or not clean_mass_kg:
        return Analog(reason="нет сортамента или массы для поиска")
    have_geo = bool(part_dims or part_length)
    tol_eff = tol if have_geo else tol / 2
    hits = []
    n_head = n_mass = 0
    for e in db:
        if re.sub(r"\s+", "", e.get("drawing_no", "")).lower() == no_k:
            continue
        line = e.get("result_line", "")
        if sort_head(line.split("\n")[0]) != head_k:
            continue
        n_head += 1
        mc = e.get("mass_clean_kg")
        if not mc or abs(mc - clean_mass_kg) / max(mc, clean_mass_kg) > tol_eff:
            continue
        n_mass += 1
        size = _size_line(line)
        if not size:
            continue
        if have_geo:
            ok = _fits(size, part_dims, part_length)
            if ok is False:
                continue
        hits.append((size, _mass_from_line(line) or e.get("mass_kg"), e.get("drawing_no", "")))
    if not hits:
        geo = (f", габарит {part_dims[0]:g}x{part_dims[1]:g}" if part_dims
               else (f", длина детали {part_length:g}" if part_length else ", габаритов детали нет"))
        return Analog(reason=(f"в базе {n_head} деталей с сортаментом «{head_k}», с массой в допуске "
                              f"{n_mass}{geo}: подходящей заготовки нет"))
    cnt = Counter(_norm_size(h[0]) for h in hits)
    best_key, n_agree = cnt.most_common(1)[0]
    if not have_geo and len(cnt) > 1:
        return Analog(reason=f"габаритов детали нет, а аналоги по массе расходятся по размеру ({len(cnt)} вариантов)")
    pick = next(h for h in hits if _norm_size(h[0]) == best_key)
    conf = "высокая" if (have_geo and n_agree == len(hits)) else "низкая"
    return Analog(size_text=pick[0], stock_mass_kg=pick[1], n=len(hits), n_agree=n_agree,
                  sources=[h[2] for h in hits if _norm_size(h[0]) == best_key][:5],
                  confidence=conf)
