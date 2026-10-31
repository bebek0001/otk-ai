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
    m = re.search(r"лист\s*(?:[A-Za-zА-Яа-я][\wА-Яа-я.\-]*\s+)?(\d+(?:[.,]\d+)?)", sortament_head or "", re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def describe(path, sortament_head: str, clean_mass_kg: Optional[float],
             density: Optional[float] = None) -> List[str]:
    """Что программа видит в геометрии листа: кандидаты габарита и проверка по массе."""
    pts = _pairs(path)
    t = sheet_thickness(sortament_head)
    rho = (density / 1000.0 if density and density > 100 else density) if density and 1 < (density / 1000.0 if density > 100 else density) < 25 else DENSITY_DEFAULT
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
    rho = (density / 1000.0 if density and density > 100 else density) if density and 1 < (density / 1000.0 if density > 100 else density) < 25 else DENSITY_DEFAULT
    if str(path).lower().endswith(".m3d"):
        g = part_gabarit_m3d(path, sortament_head, clean_mass_kg, density)
        if not g:
            last_debug.append("в модели не найден контур, подходящий по массе")
            return None
        a, b = g[0], g[1]
        h, l = sorted((a, b))
        return ContourSize(h_mm=h, l_mm=l, blank_mass_kg=round(a * b * t * rho * 1e-6, 3),
                           method=g[2],
                           confidence="высокая" if g[2] == "параметры модели" else "средняя",
                           note="припуск не добавлен")
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
    rho = (density / 1000.0 if density and density > 100 else density) if density and 1 < (density / 1000.0 if density > 100 else density) < 25 else DENSITY_DEFAULT
    if str(path).lower().endswith(".m3d"):
        return part_gabarit_m3d(path, sortament_head, clean_mass_kg, density)
    pts = _pairs(path)
    if not pts:
        return None
    c = _candidates(pts, t, rho, clean_mass_kg)
    if not c:
        return None
    (a, b), method, _conf, _ratio = c[0]
    return float(a), float(b), method


# --------------------------------------------------------------------------- #
#  Модель .m3d: контур из отрезков эскиза
# --------------------------------------------------------------------------- #
# В модели нет рамки и штампа, а контур эскиза лежит явными отрезками
# (x1, y1, x2, y2 подряд после тега 47 08). Отрезки, связанные общими концами,
# образуют контур; габарит его вершин и есть габарит листовой детали.
# Выбор среди контуров по массе: площадь габарита не меньше площади детали
# (масса / (плотность * толщина)) и не более чем в 2.2 раза больше. Берётся
# контур с наименьшим запасом. Дуги в отрезки не попадают, поэтому у деталей
# со скруглением габарит может получиться меньше настоящего: такой ответ всегда
# идёт на проверку.

M3D_AREA_RATIO = (0.95, 2.2)


def _segments(path) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    out = set()
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                        # noqa: BLE001
        return []
    for b in chunks:
        i = b.find(cdw_geometry.TAG_COORD)
        while i >= 0:
            if i >= 14 and i + 34 <= len(b):
                v = struct.unpack_from("<4d", b, i + 2)
                if all(x == x and abs(x) < 1e5 for x in v) and all(abs(t * 1000 - round(t * 1000)) < 1e-6 for t in v):
                    x1, y1, x2, y2 = (round(t, 3) for t in v)
                    if (x1, y1) != (x2, y2):
                        out.add(tuple(sorted(((x1, y1), (x2, y2)))))
            i = b.find(cdw_geometry.TAG_COORD, i + 1)
    return list(out)


def _drop_diagonals(segs):
    """Диагонали прямоугольника по двум точкам не являются рёбрами контура."""
    verts = {p for sg in segs for p in sg}
    return [(p, q) for p, q in segs
            if not (p[0] != q[0] and p[1] != q[1] and (p[0], q[1]) in verts and (q[0], p[1]) in verts)]


def _component_boxes(segs) -> List[Tuple[float, float]]:
    adj: dict = {}
    for p, q in segs:
        adj.setdefault(p, []).append(q)
        adj.setdefault(q, []).append(p)
    seen, boxes = set(), []
    for v in adj:
        if v in seen:
            continue
        stack, comp = [v], []
        seen.add(v)
        while stack:
            a = stack.pop()
            comp.append(a)
            for b in adj[a]:
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        if len(comp) >= 3:
            xs = [p[0] for p in comp]
            ys = [p[1] for p in comp]
            boxes.append(tuple(sorted((max(xs) - min(xs), max(ys) - min(ys)))))
    return boxes


def _block_triples(path, t: float) -> dict:
    """Параметры выдавливания листа: подряд три числа (A, t, B), где t равно
    толщине листа (в файле с шумом порядка 1e-14). A и B лежат в записи
    параметрического тела, это габарит заготовки. Возвращает
    {(мин, макс): сколько раз встретилось}."""
    out: dict = {}
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                        # noqa: BLE001
        return out
    for b in chunks:
        for shift in range(8):
            n = (len(b) - shift) // 8
            if n < 3:
                continue
            vals = struct.unpack_from("<%dd" % n, b, shift)
            for j in range(1, n - 1):
                if abs(vals[j] - t) < 1e-6:
                    a, c = vals[j - 1], vals[j + 1]
                    if all(x == x and t < x <= 5000 and abs(x * 2 - round(x * 2)) < 1e-6 for x in (a, c)):
                        key = (round(min(a, c), 1), round(max(a, c), 1))
                        out[key] = out.get(key, 0) + 1
    return out


def part_gabarit_m3d(path, sortament_head: str, clean_mass_kg: Optional[float],
                     density: Optional[float] = None) -> Optional[Tuple[float, float, str]]:
    t = sheet_thickness(sortament_head)
    if not t or not clean_mass_kg or clean_mass_kg <= 0:
        return None
    if density and density > 100:
        density = density / 1000.0
    rho = (density / 1000.0 if density and density > 100 else density) if density and 1 < (density / 1000.0 if density > 100 else density) < 25 else DENSITY_DEFAULT
    area_part = clean_mass_kg / (rho * 1e-6 * t)
    # 1) прямоугольник выдавливания (A, толщина, B) в параметрах модели
    best_t = None
    for (a, b), n in _block_triples(path, t).items():
        ratio = a * b / area_part
        last_debug.append(f"параметры тела {a:g}x{b:g} (x{n}): площадь/деталь {ratio:.2f}")
        if 0.95 <= ratio <= 4.0 and (best_t is None or (n, -ratio) > (best_t[0], -best_t[1])):
            best_t = (n, ratio, a, b)
    if best_t:
        return (best_t[2], best_t[3], "параметры модели")
    lo, hi = M3D_AREA_RATIO
    best = None
    for a, b in _component_boxes(_drop_diagonals(_segments(path))):
        if min(a, b) < 3:
            continue
        ratio = a * b / area_part
        last_debug.append(f"контур эскиза {a:g}x{b:g}: площадь габарита/детали {ratio:.2f}, нужно {lo}-{hi}")
        if lo <= ratio <= hi and (best is None or ratio < best[0]):
            best = (ratio, round(a, 1), round(b, 1))
    return (best[1], best[2], "эскиз модели") if best else None
