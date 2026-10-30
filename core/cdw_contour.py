# -*- coding: utf-8 -*-
"""
cdw_contour.py — габарит контура листовой детали из чертежа КОМПАС (.cdw).

Что установлено на 21 листовой детали набора 935028 (ответы технолога):
  * контур детали хранится в тех же координатных парах (тег 47 08), что и
    размеры; у простых деталей это четыре угла прямоугольника (±L/2; ±H/2);
  * у деталей с развёрткой (гнутые «Стенки» 865x1005) габарит заготовки
    в файле не задан: контур лишь частично, предложение не строится.

Метод:
  1. Основной: наибольший прямоугольник, все четыре угла которого есть
     среди координатных пар.
  2. Запасной: габарит точек, у которых обе координаты не нулевые.
Оба кандидата проходят проверку по массе: масса листа габарита
(A * B * толщина * плотность) должна лежать в узком диапазоне относительно
чистой массы детали. Прямоугольник из углов допускает больший запас
(на вырезы), габарит точек только близкий к чистой массе.

Результат это ПРЕДЛОЖЕНИЕ для технолога, автоматически не принимается.
Зависимости: только стандартная библиотека.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from core import cdw_geometry

DENSITY_DEFAULT = 7.85           # г/см3
RECT_MASS_RANGE = (0.90, 1.30)   # масса листа габарита / чистая масса
BBOX_MASS_RANGE = (0.98, 1.10)
ROUND_TO_MM = 1.0

last_debug: list = []        # причины отказа последнего вызова (для журнала)


# Правило заказчика (письмо 22.10): листовые детали режутся лазером без припуска
# по периметру при Ra 50 и грубее; при Ra 25 и точнее добавляется 5 мм на сторону.
RA_ALLOWANCE_MAX = 25.0
SIDE_ALLOWANCE_MM = 5.0


def parse_ra(text: str) -> Optional[float]:
    """«Ra 12,5» -> 12.5; Rz и пустое значение дают None."""
    m = re.search(r"Ra\s*(\d+(?:[.,]\d+)?)", text or "", re.I)
    return float(m.group(1).replace(",", ".")) if m else None


@dataclass
class ContourSize:
    h_mm: float = 0.0
    l_mm: float = 0.0
    blank_mass_kg: float = 0.0
    method: str = ""          # "прямоугольник" | "габарит точек"
    confidence: str = "низкая"
    note: str = ""


def _pairs(path) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                        # noqa: BLE001
        return out
    for b in chunks:
        i = b.find(cdw_geometry.TAG_COORD)
        while i >= 0:
            if i >= 24 and i + 18 <= len(b):
                x, y = struct.unpack_from("<dd", b, i + 2)
                if cdw_geometry._finite(x) and cdw_geometry._finite(y):
                    out.append((x, y))
            i = b.find(cdw_geometry.TAG_COORD, i + 1)
    return out


def _largest_rect(pts) -> Optional[Tuple[float, float]]:
    S = {(round(x, 2), round(y, 2)) for x, y in pts}
    by_x = {}
    for x, y in S:
        by_x.setdefault(x, set()).add(y)
    xs = sorted(by_x)
    best = None
    for i, x1 in enumerate(xs):
        for x2 in xs[i + 1:]:
            common = sorted(by_x[x1] & by_x[x2])
            if len(common) < 2:
                continue
            w = x2 - x1
            h = common[-1] - common[0]          # крайние y дают наибольшую площадь
            if best is None or w * h > best[0]:
                best = (w * h, w, h)
    return (best[1], best[2]) if best else None


def _bbox_nonzero(pts) -> Optional[Tuple[float, float]]:
    q = [(x, y) for x, y in pts if abs(x) > 0.5 and abs(y) > 0.5]
    if len(q) < 3:
        return None
    return (max(a for a, _ in q) - min(a for a, _ in q),
            max(b for _, b in q) - min(b for _, b in q))


# Каскад кандидатов (подобран на 186 листах с ответами технолога). В файле, кроме
# контура детали, лежат координаты рамки и текста штампа (сотни и тысячи мм):
# они раздувают габарит, поэтому сначала берутся только точки в пределах T.
# (способ, предел T мм, диапазон массы габарита/чистой, уверенность).
# Точность на этих листах: «высокая» 29 из 31, «средняя» 15 из 37; шаг с диапазоном 0.9-1.5
# давал лишь 6 из 25 и убран.
CASCADE = [
    ("габарит контура", 600.0, (0.98, 1.10), "высокая"),
    ("габарит контура", 1000.0, (0.98, 1.10), "высокая"),
    ("габарит контура", 4000.0, (0.98, 1.10), "высокая"),
    ("прямоугольник", 600.0, (0.90, 1.30), "средняя"),
    ("прямоугольник", 1e9, (0.90, 1.30), "средняя"),
]


def _trim(pts, limit: float):
    return [(x, y) for x, y in pts if abs(x) <= limit and abs(y) <= limit]


def _candidates(pts, t: float, rho: float, clean_mass_kg: float):
    """Подходящие кандидаты габарита по порядку каскада:
    [((a, b) по возрастанию, способ, уверенность, отношение масс, пояснение)]."""
    out = []
    seen = set()
    for method, limit, (lo, hi), conf in CASCADE:
        sub = _trim(pts, limit)
        r = _largest_rect(sub) if method == "прямоугольник" else _bbox_nonzero(sub)
        if not r or min(r) < 5:
            continue
        a, b = sorted((round(r[0], 1), round(r[1], 1)))
        ratio = a * b * t * rho * 1e-6 / clean_mass_kg
        last_debug.append(f"{method} (точки до {limit:g} мм) {a:g}x{b:g}: масса габарита/чистая {ratio:.2f}, "
                          f"нужно {lo}-{hi}")
        if lo <= ratio <= hi and (a, b) not in seen:
            seen.add((a, b))
            out.append(((a, b), method, conf, ratio))
    return out



def sheet_thickness(sortament_head: str) -> Optional[float]:
    m = re.search(r"лист\s*(\d+(?:[.,]\d+)?)", sortament_head or "", re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def describe(path, sortament_head: str, clean_mass_kg: Optional[float],
             density: Optional[float] = None) -> List[str]:
    """Что программа видит в геометрии листа: кандидаты габарита и проверка по массе."""
    pts = _pairs(path)
    t = sheet_thickness(sortament_head)
    rho = density if density and 1 < density < 25 else DENSITY_DEFAULT
    out = [f"Координатных пар в файле: {len(pts)}"]
    for title, fn in (("Наибольший прямоугольник из углов", _largest_rect),
                      ("Габарит точек с ненулевыми координатами", _bbox_nonzero)):
        r = fn(pts) if pts else None
        if not r:
            out.append(f"{title}: не найден")
            continue
        a, b = sorted((round(r[0], 1), round(r[1], 1)))
        line = f"{title}: {a:g} x {b:g} мм"
        if t and clean_mass_kg:
            m = a * b * t * rho * 1e-6
            line += f", масса листа {m:.3f} кг, к чистой массе {m / clean_mass_kg:.2f}"
        out.append(line)
    if not t:
        out.append("Толщина листа не определена (сортамент не «Лист»): контур не применяется")
    return out


def propose_sheet(path, sortament_head: str, clean_mass_kg: Optional[float],
                  density: Optional[float] = None, ra: Optional[float] = None) -> Optional[ContourSize]:
    last_debug.clear()
    t = sheet_thickness(sortament_head)
    if not t or not clean_mass_kg or clean_mass_kg <= 0:
        last_debug.append("нет толщины листа или массы детали")
        return None
    rho = density if density and 1 < density < 25 else DENSITY_DEFAULT
    pts = _pairs(path)
    if not pts:
        last_debug.append("в файле нет координатных пар")
        return None

    def mass(a, b):
        return a * b * t * rho * 1e-6

    cands = _candidates(pts, t, rho, clean_mass_kg)
    if not cands:
        last_debug.append("ни один кандидат габарита не подошёл по массе")
        return None
    (a, b), method, conf, ratio = cands[0]
    add = 0.0
    ra_note = "шероховатость не прочитана, припуск не добавлен"
    if ra is not None:
        if ra <= RA_ALLOWANCE_MAX:
            add = 2 * SIDE_ALLOWANCE_MM
            ra_note = f"Ra {ra:g} (точнее Ra 25): припуск {SIDE_ALLOWANCE_MM:g} мм на сторону"
        else:
            ra_note = f"Ra {ra:g}: без припуска"
    h, l = sorted((round(a / ROUND_TO_MM) * ROUND_TO_MM + add,
                   round(b / ROUND_TO_MM) * ROUND_TO_MM + add))
    return ContourSize(h_mm=h, l_mm=l, blank_mass_kg=round(mass(h, l), 3),
                       method=method, confidence=conf,
                       note=f"масса габарита/чистая = {ratio:.2f}; {ra_note}")


def part_gabarit(path, sortament_head: str, clean_mass_kg: Optional[float],
                 density: Optional[float] = None) -> Optional[Tuple[float, float, str]]:
    """Габарит детали-листа (a <= b, мм) без припуска и округления: тот же
    отбор по массе, что и в propose_sheet. Нужен для поиска аналога по габаритам."""
    t = sheet_thickness(sortament_head)
    if not t or not clean_mass_kg or clean_mass_kg <= 0:
        return None
    rho = density if density and 1 < density < 25 else DENSITY_DEFAULT
    pts = _pairs(path)
    if not pts:
        return None
    c = _candidates(pts, t, rho, clean_mass_kg)
    if not c:
        return None
    (a, b), method, _conf, _ratio = c[0]
    return float(a), float(b), method
