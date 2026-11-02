"""Заготовка по содержимому модели .m3d, без базы и без чертежа.

В потоке Contents рядом с телом лежит запись с габаритным параллелепипедом
(размеры по трём осям модели). Встречается в двух видах:
  A) (X/2, y, Z/2, X, Y, Z)    центр и размеры (файлы старых версий КОМПАС)
  B) (x0, y0, z0, x1, y1, z1)  два угла, один из углов в начале координат,
                               запись повторяется три раза
Для листа размер H x L берётся из этих размеров за вычетом толщины, для
линейного проката (круг, квадрат, труба, профиль, швеллер, уголок) длина L
это размер, не совпадающий с сечением из сортамента.
Параметры выдавливания (A, t, B) из core.cdw_contour остаются главным
источником для листа, габарит модели его проверяет и поправляет, когда
эскиз меньше тела (гибка, вырезы по краю).
"""
from __future__ import annotations

import collections
import math
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core import cdw_geometry

LO, HI = 0.01, 1e5

# кг/м по таблицам ГОСТ 8240 (швеллер) и по встретившимся типоразмерам
KG_PER_M_CHANNEL = {5: 4.84, 6.5: 5.90, 8: 7.05, 10: 8.59, 12: 10.4, 14: 12.3, 16: 14.2,
                    18: 16.3, 20: 18.4, 22: 21.0, 24: 24.0, 27: 27.7, 30: 31.8,
                    33: 36.5, 36: 41.9, 40: 48.3}
KG_PER_M = {"труба 159x7": 26.27, "профиль 100x50x5": 10.483, "профиль 120x60x5": 12.84,
            "труба 60x60x3": 5.19, "уголок 160x100x10": 19.83}


@dataclass
class Stock:
    kind: str                       # "sheet" | "lin"
    h_mm: float = 0.0               # лист: меньший размер
    l_mm: float = 0.0               # лист: больший размер; линейный: длина
    thickness_mm: float = 0.0
    section: str = ""               # линейный: «Круг 80», «Швеллер 12У»
    blank_mass_kg: Optional[float] = None
    method: str = ""
    alts: List[Tuple] = field(default_factory=list)

    @property
    def size_text(self) -> str:
        if self.kind == "sheet":
            return f"HxL = {self.h_mm:g}x{self.l_mm:g}"
        return f"{self.section}, L={self.l_mm:g}"


# --------------------------------------------------------------------------- #
#  Габаритные записи
# --------------------------------------------------------------------------- #

def _ok(v: float) -> bool:
    return v == v and LO <= v < HI


def body_boxes(path) -> Tuple[collections.Counter, collections.Counter]:
    """Счётчики записей вида A и B: {(dx, dy, dz): повторов}."""
    a_form: collections.Counter = collections.Counter()
    b_form: collections.Counter = collections.Counter()
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                        # noqa: BLE001
        return a_form, b_form
    for chunk in chunks:
        for sh in range(8):
            n = (len(chunk) - sh) // 8
            if n < 6:
                continue
            a = struct.unpack_from("<%dd" % n, chunk, sh)
            for i in range(n - 5):
                x0, y0, z0, x1, y1, z1 = a[i:i + 6]
                if x0 != x0 or y0 != y0 or z0 != z0 or x1 != x1 or y1 != y1 or z1 != z1:
                    continue
                if (x0 >= 0.005 and z0 >= 0.005 and _ok(x1) and _ok(y1) and _ok(z1)
                        and abs(x0 * 2 - x1) < 1e-6 and abs(z0 * 2 - z1) < 1e-6
                        and -1e-6 <= y0 <= y1 + 1e-6):
                    a_form[(round(x1, 2), round(y1, 2), round(z1, 2))] += 1
                d = (x1 - x0, y1 - y0, z1 - z0)
                if _ok(d[0]) and _ok(d[1]) and _ok(d[2]):
                    if (min(abs(x0), abs(x1)) < 1e-6 and min(abs(y0), abs(y1)) < 1e-6
                            and min(abs(z0), abs(z1)) < 1e-6
                            and max(abs(x0), abs(y0), abs(z0), abs(x1), abs(y1), abs(z1)) < HI):
                        b_form[tuple(round(v, 2) for v in d)] += 1
    return a_form, b_form


def strong_boxes(path) -> List[Tuple[Tuple[float, float, float], int]]:
    """Надёжные габариты тела: вид A или вид B с тремя и более повторами.
    Список ((dx, dy, dz), вес), вес выше у более надёжных."""
    a, b = body_boxes(path)
    out = [(k, v + 10) for k, v in a.items()]
    out += [(k, v) for k, v in b.items() if v >= 3]
    return out


# --------------------------------------------------------------------------- #
#  Разбор сортамента
# --------------------------------------------------------------------------- #

def _head(material: str) -> str:
    m = (material or "").replace("$d", " ").replace("$", " ")
    m = re.split(r";|\s+ГОСТ", m)[0]
    return re.sub(r"\s+", " ", m).strip()


def _nums(s: str) -> List[float]:
    return [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", s)]


def _near(a: float, b: float, tol: float = 0.6) -> bool:
    return abs(a - b) <= tol


def _norm(head: str) -> str:
    return head.lower().replace("х", "x").replace("×", "x")


def _sheet_thickness(head: str) -> Optional[float]:
    from core import cdw_contour
    w = head.lower()
    if w.startswith("жесть"):
        n = _nums(head)
        return n[0] / 100.0 if n else None
    return cdw_contour.sheet_thickness(head)


def _linear_section(head: str):
    """(вид, подпись, список размеров сечения) или None."""
    w = _norm(head)
    n = _nums(w)
    if not n:
        return None
    if w.startswith("пруток") or w.startswith("круг"):
        return "round", f"Круг {n[0]:g}", [n[0]]
    if w.startswith("квадрат"):
        return "square", f"Квадрат {n[0]:g}", [n[0]]
    if w.startswith("труба"):
        need = [n[0], n[0]] if len(n) == 2 else n[:2]
        return "tube", head, need
    if w.startswith(("профиль", "уголок")) and len(n) >= 2:
        return "profile", head, n[:2]
    if w.startswith("швеллер"):
        return "channel", head, [n[0] * 10]
    return None


def _length_from(kind: str, need: List[float], dims) -> Optional[float]:
    ds = list(dims)
    if kind in ("round", "square"):
        D = need[0]
        for i in range(3):
            for j in range(i + 1, 3):
                if _near(ds[i], ds[j], 0.05) and ds[i] <= D + 0.5 and ds[i] >= 0.3 * D:
                    return ds[3 - i - j]
        return None
    rest = list(ds)
    for s in need:
        idx = [i for i, d in enumerate(rest) if d is not None and _near(d, s)]
        if not idx:
            return None
        rest[idx[0]] = None
    left = [d for d in rest if d is not None]
    return max(left) if left else None


# --------------------------------------------------------------------------- #
#  Основная функция
# --------------------------------------------------------------------------- #

def _rho(density) -> float:
    d = float(density) if density else 7850.0
    return d / 1000.0 if d > 100 else d          # г/см3


def read_stock(path, material: str, mass_kg: Optional[float], density=None) -> Optional[Stock]:
    """Заготовка по модели. None, если по содержимому файла размер не определить."""
    head = _head(material)
    w = head.lower()
    rho = _rho(density)
    if w.startswith(("лист", "жесть")):
        return _sheet(path, head, mass_kg, density, rho)
    sec = _linear_section(head)
    if sec is None:
        return None
    kind, label, need = sec
    cands = strong_boxes(path)
    vmin = (mass_kg or 0) / rho * 1e6 * 0.98            # мм3
    ok = [c for c in cands if c[0][0] * c[0][1] * c[0][2] >= vmin] or cands
    good = []
    for dims, wgt in ok:
        L = _length_from(kind, need, dims)
        if L:
            good.append((wgt, dims, L))
    if not good:
        L = _dominant_length(cands, kind, head, need, mass_kg)
        if not L:
            return None
        st = Stock("lin", l_mm=L, section=label, method="габарит модели")
        st.blank_mass_kg = _lin_mass(kind, head, need, L, rho)
        return st
    good.sort(key=lambda g: (-g[0], g[1][0] * g[1][1] * g[1][2]))
    L = good[0][2]
    alts = sorted({g[2] for g in good if abs(g[2] - L) > 0.5})
    st = Stock("lin", l_mm=L, section=label, method="габарит модели", alts=[(a,) for a in alts])
    st.blank_mass_kg = _lin_mass(kind, head, need, L, rho)
    return st


def _dominant_length(cands, kind: str, head: str, need: List[float], mass_kg) -> Optional[float]:
    """Сечение в записях не нашлось (деталь собрана из эскизов): длина это размер,
    общий почти для всех габаритных записей. Принимается, только если масса
    детали не больше массы заготовки такой длины."""
    if kind in ("round", "square") or not cands:
        return None
    weight: collections.Counter = collections.Counter()
    for dims, wgt in cands:
        for v in set(dims):
            if v >= 20:
                weight[v] += wgt
    if not weight:
        return None
    ranked = sorted(weight.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    best = ranked[0]
    if len(ranked) > 1 and best[1] < 1.5 * ranked[1][1]:
        return None                     # явного лидера нет, длину не угадываем
    L = best[0]
    mass_blank = _lin_mass(kind, head, need, L, 7.85)
    if mass_kg and mass_blank and mass_kg > mass_blank * 1.01:
        return None
    return L


def with_material(stock: "Stock", material: str, density=None) -> Optional["Stock"]:
    """Тот же габарит модели для другого исполнения (другой сортамент у той же
    геометрии): длина остаётся, меняются подпись сечения и масса заготовки."""
    if stock is None or stock.kind != "lin":
        return None
    head = _head(material)
    sec = _linear_section(head)
    if sec is None:
        return None
    kind, label, need = sec
    st = Stock("lin", l_mm=stock.l_mm, section=label, method=stock.method)
    st.blank_mass_kg = _lin_mass(kind, head, need, stock.l_mm, _rho(density))
    return st


def _lin_mass(kind: str, head: str, need: List[float], L: float, rho: float) -> Optional[float]:
    per_m = KG_PER_M.get(_norm(head))
    if kind == "channel":
        per_m = KG_PER_M_CHANNEL.get(need[0] / 10)
        if per_m is None and need[0] / 10 == int(need[0] / 10):
            per_m = KG_PER_M_CHANNEL.get(int(need[0] / 10))
    if per_m:
        return round(per_m * L / 1000.0, 6)
    if kind == "round":
        area = math.pi * need[0] ** 2 / 4
    elif kind == "square":
        area = need[0] ** 2
    else:
        return None
    return round(area * L * rho * 1e-6, 6)


def _sheet_by_edges(boxes, t: float, enough) -> Optional[tuple]:
    """Запасной путь: целого габарита листа в модели нет, но обе его стороны
    записаны отдельными кромками (t x d x t), по 5-6 раз каждая. Берутся две
    наибольшие такие стороны, если площадь не меньше площади детали."""
    edges = {}
    for dims, wgt in boxes:
        idx = [i for i, d in enumerate(dims) if abs(d - t) < 0.011 + 0.001 * t]
        if len(idx) == 2 and wgt >= 5:
            d = max(dims)
            if d > t * 3:
                edges[round(d, 2)] = max(edges.get(round(d, 2), 0), wgt)
    if len(edges) < 2:
        return None
    a, b = sorted(edges, reverse=True)[:2]
    h, l = min(a, b), max(a, b)
    return (h, l) if enough(h, l) else None


def _sheet(path, head: str, mass_kg, density, rho: float) -> Optional[Stock]:
    from core import cdw_contour
    t = _sheet_thickness(head)
    if not t:
        return None
    tri = None
    try:
        g = cdw_contour.part_gabarit_m3d(path, head, mass_kg, density) if mass_kg else None
    except Exception:                                        # noqa: BLE001
        g = None
    # заготовка не может быть меньше детали: площадь H x L >= площади, взятой из массы
    area_part = (mass_kg / (t * rho * 1e-6)) if mass_kg else 0.0

    def enough(h, l, k=0.99):
        return h * l >= k * area_part

    if g:
        tri = (min(g[0], g[1]), max(g[0], g[1]))
        if not enough(*tri):
            tri = None
    method = g[2] if g else ""
    strong = []
    for dims, wgt in strong_boxes(path):
        idx = [i for i, d in enumerate(dims) if abs(d - t) < 0.011 + 0.001 * t]
        if idx:
            rest = sorted(d for i, d in enumerate(dims) if i != idx[0])
            if enough(rest[0], rest[1], 0.95):       # масса может быть задана вручную
                strong.append((wgt, (rest[0], rest[1])))
    pick = tri
    if tri:
        dom = [c for c in strong if c[1][0] >= tri[0] - 0.5 and c[1][1] >= tri[1] - 0.5
               and (abs(c[1][0] - tri[0]) > 0.5 or abs(c[1][1] - tri[1]) > 0.5)]
        if dom:
            dom.sort(key=lambda c: c[1][0] * c[1][1])
            pick, method = dom[0][1], "габарит модели"
    elif strong:
        strong.sort(key=lambda c: (-c[0], c[1][0] * c[1][1]))
        pick, method = strong[0][1], "габарит модели"
    if not pick:
        pick = _sheet_by_edges(strong_boxes(path), t, enough)
        method = "габарит модели"
    if not pick:
        return None
    h, l = pick
    st = Stock("sheet", h_mm=h, l_mm=l, thickness_mm=t, method=method)
    st.blank_mass_kg = round(h * l * t * rho * 1e-6, 6)
    return st
