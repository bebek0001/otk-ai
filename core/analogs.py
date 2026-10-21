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


def find_analog(part_name: str, stamp_sortament: str, clean_mass_kg: Optional[float],
                db: List[Dict[str, Any]], drawing_no: str = "",
                tol: float = MASS_TOL) -> Analog:
    name_k = norm_name(part_name)
    head_k = sort_head(stamp_sortament)
    no_k = re.sub(r"\s+", "", drawing_no or "").lower()
    if not name_k or not head_k or not clean_mass_kg:
        return Analog(reason="нет названия, сортамента или массы для поиска")
    hits = []
    n_name = n_head = 0
    for e in db:
        if re.sub(r"\s+", "", e.get("drawing_no", "")).lower() == no_k:
            continue
        if norm_name(e.get("part_name", "")) != name_k:
            continue
        n_name += 1
        line = e.get("result_line", "")
        if sort_head(line.split("\n")[0]) != head_k:
            continue
        n_head += 1
        mc = e.get("mass_clean_kg")
        if not mc or abs(mc - clean_mass_kg) / max(mc, clean_mass_kg) > tol:
            continue
        size = _size_line(line)
        if not size:
            continue
        hits.append((size, _mass_from_line(line) or e.get("mass_kg"), e.get("drawing_no", "")))
    if not hits:
        return Analog(reason=(f"в базе {n_name} деталей с названием «{name_k}», из них "
                              f"{n_head} с сортаментом «{head_k}», но масса не сходится (допуск {tol:.0%})"
                              if n_name else f"в базе нет деталей с названием «{name_k}»"))
    cnt = Counter(_norm_size(h[0]) for h in hits)
    best_key, n_agree = cnt.most_common(1)[0]
    pick = next(h for h in hits if _norm_size(h[0]) == best_key)
    conf = "высокая" if (n_agree == len(hits) and len(hits) >= 2) else "низкая"
    return Analog(size_text=pick[0], stock_mass_kg=pick[1], n=len(hits), n_agree=n_agree,
                  sources=[h[2] for h in hits if _norm_size(h[0]) == best_key][:5],
                  confidence=conf)
