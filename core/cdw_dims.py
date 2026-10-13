# -*- coding: utf-8 -*-
"""
cdw_dims.py — длина детали из размерных объектов чертежа КОМПАС (.cdw)
и предложение длины заготовки, проверенное по массе.

Что установлено на реальных чертежах (12 деталей с ответами технолога и
30 деталей клиента):
  * Размерная надпись хранится как тройка координатных пар в системе самой
    размерной линии; значение размера лежит в паре «конец размера», ноль в
    паре «начало». Значения в натуральных миллиметрах, масштаб вида на них
    не влияет.
  * Среди размеров бывают диаметры, фаски, ширины и длины. Длина детали
    обычно наибольший размер, но на чертежах с осевой симметрией
    проставляется половина размера.

Поэтому результат это ПРЕДЛОЖЕНИЕ, а не расчёт:
  * кандидаты на длину детали проверяются по массе: заготовка того же
    сечения и длины детали не может весить меньше чистой детали;
  * если подходит больше одного кандидата, уверенность «низкая»;
  * длина заготовки = длина детали + припуск, округление до 5 мм.
Результат всегда отправляется технологу на проверку.

Зависимости: только стандартная библиотека.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from core import cdw_geometry

# Служебная приставка перед координатной парой размера (стиль размерной линии)
_DIM_MARK = bytes.fromhex("00001626")

MIN_LEN_MM = 10.0
MAX_LEN_MM = 12000.0
DEFAULT_ALLOWANCE_MM = 10.0
ROUND_TO_MM = 5.0
HIGH_CONF_MAX_RATIO = 3.5   # выше: уверенность понижается до «низкой»

# Типы с известным постоянным сечением: масса считается как сечение * длина
LINEAR_TYPES = ("Круг", "Квадрат", "Шестигранник", "Труба", "Уголок", "Профиль")


# --------------------------------------------------------------------------- #
#  Размерные значения из файла
# --------------------------------------------------------------------------- #

def dimension_values(path: str | Path) -> List[float]:
    """Значения размеров чертежа в мм (уникальные, по возрастанию)."""
    out = set()
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                        # noqa: BLE001
        return []
    for b in chunks:
        i = b.find(cdw_geometry.TAG_COORD)
        while i >= 0:
            if i >= 14 and b[i - 14:i - 10] == _DIM_MARK and i + 18 <= len(b):
                x, y = struct.unpack_from("<dd", b, i + 2)
                if cdw_geometry._finite(x) and cdw_geometry._finite(y):
                    for v in (abs(x), abs(y)):
                        if v >= 1.0 and abs(v * 1000 - round(v * 1000)) < 1e-3:
                            out.add(round(v, 3))
            i = b.find(cdw_geometry.TAG_COORD, i + 1)
    return sorted(out)


# --------------------------------------------------------------------------- #
#  Сортамент из штампа и площадь сечения
# --------------------------------------------------------------------------- #

_NUM = r"(\d+(?:[.,]\d+)?)"
_SEP = r"\s*[xх×*]\s*"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass
class Section:
    kind: str                    # Круг | Квадрат | Шестигранник | Труба | Уголок | Профиль | Лист
    sizes: Tuple[float, ...]     # размеры сечения (Лист: толщина)
    text: str = ""

    @property
    def area_mm2(self) -> Optional[float]:
        k, s = self.kind, self.sizes
        if k == "Круг":
            return math.pi * s[0] ** 2 / 4
        if k == "Квадрат":
            return s[0] ** 2
        if k == "Шестигранник":
            return math.sqrt(3) / 2 * s[0] ** 2
        if k == "Труба":
            d_in = s[0] - 2 * s[1]
            return math.pi / 4 * (s[0] ** 2 - max(d_in, 0) ** 2)
        if k == "Уголок":
            a, b, t = s[:3]
            return (a + b - t) * t
        if k == "Профиль":          # прямоугольная труба a x b x t
            a, b, t = s[:3]
            return a * b - (a - 2 * t) * (b - 2 * t)
        return None


def parse_section(material: str) -> Optional[Section]:
    """Сечение из строки материала штампа: 'Круг 40 ГОСТ ...', 'Труба 108x20 ...',
    'Уголок 40х40х5 ...', 'Профиль 80х60х5 ...', 'Шестигранник 55 ...'."""
    text = (material or "").split(";")[0].replace("/", " ").strip()
    m = re.match(r"\s*(Круг|Квадрат|Шестигранник)\s+" + _NUM, text, re.I)
    if m:
        return Section(m.group(1).capitalize(), (_f(m.group(2)),), text)
    m = re.match(r"\s*Труба\s+" + _NUM + _SEP + _NUM, text, re.I)
    if m:
        return Section("Труба", (_f(m.group(1)), _f(m.group(2))), text)
    m = re.match(r"\s*Уголок\s+" + _NUM + _SEP + _NUM + _SEP + _NUM, text, re.I)
    if m:
        return Section("Уголок", (_f(m.group(1)), _f(m.group(2)), _f(m.group(3))), text)
    m = re.match(r"\s*Профиль\s+" + _NUM + _SEP + _NUM + _SEP + _NUM, text, re.I)
    if m:
        return Section("Профиль", (_f(m.group(1)), _f(m.group(2)), _f(m.group(3))), text)
    m = re.match(r"\s*Лист\s+" + _NUM, text, re.I)
    if m:
        return Section("Лист", (_f(m.group(1)),), text)
    return None


# --------------------------------------------------------------------------- #
#  Предложение длины
# --------------------------------------------------------------------------- #

@dataclass
class LengthProposal:
    part_length_mm: Optional[float] = None
    blank_length_mm: Optional[float] = None
    blank_mass_kg: Optional[float] = None
    confidence: str = "нет"                 # высокая | низкая | нет
    candidates: List[float] = field(default_factory=list)
    note: str = ""


def min_part_length_mm(section: Section, clean_mass_kg: float,
                       density_kg_m3: float) -> Optional[float]:
    """Нижняя граница длины детали: объём детали / сечение заготовки."""
    area = section.area_mm2
    if not area or not clean_mass_kg or clean_mass_kg <= 0:
        return None
    volume_mm3 = clean_mass_kg / density_kg_m3 * 1e9
    return volume_mm3 / area


def _round_to(x: float, step: float = ROUND_TO_MM) -> float:
    return round(x / step) * step


def blank_mass_kg(section: Section, length_mm: float, density_kg_m3: float) -> Optional[float]:
    area = section.area_mm2
    if not area:
        return None
    return area * length_mm / 1e9 * density_kg_m3


def propose_length(section: Section, dims: List[float], clean_mass_kg: float,
                   density_kg_m3: float = 7850.0,
                   allowance_mm: float = DEFAULT_ALLOWANCE_MM) -> LengthProposal:
    """
    Кандидаты на длину детали: наибольшие размеры чертежа и их удвоенные
    значения (половинный размер на симметричном виде). Берётся кандидат, для
    которого масса заготовки не меньше массы детали. Уверенность «высокая»,
    если подходит ровно один такой вариант; иначе «низкая», выбирается
    наибольший размер.
    """
    prop = LengthProposal()
    if section.kind not in LINEAR_TYPES or not section.area_mm2:
        prop.note = f"тип «{section.kind}»: длина по одному размеру не определяется"
        return prop
    # Половина размера сечения (радиус) длиной не бывает
    section_like = set()
    for v in section.sizes:
        section_like.add(round(v / 2, 3))
    dims = [d for d in dims
            if MIN_LEN_MM <= d <= MAX_LEN_MM and round(d, 3) not in section_like]
    if not dims:
        prop.note = "размеров в чертеже нет"
        return prop

    l_min = min_part_length_mm(section, clean_mass_kg, density_kg_m3) or 0.0
    top = max(dims)
    # Кандидаты: самый большой размер и его удвоение
    cands = sorted({top, top * 2})
    ok = [c for c in cands if c >= l_min * 0.97]
    prop.candidates = ok

    if not ok:
        prop.note = (f"наибольший размер {top:g} мм короче минимально возможной "
                     f"длины детали {l_min:.0f} мм по массе")
        return prop

    # Удвоение подходит по массе, только если вся деталь при этой длине
    # не вылезает слишком сильно за чистую массу (иначе вариант нереалистичен)
    def plausible(c: float) -> bool:
        full = blank_mass_kg(section, c, density_kg_m3)
        return full is not None and full <= clean_mass_kg * 6.0

    ok = [c for c in ok if plausible(c)]
    if not ok:
        prop.note = ("масса заготовки по найденным размерам слишком велика "
                     "относительно массы детали: длина не определена")
        return prop
    prop.candidates = ok
    chosen = ok[0]
    prop.part_length_mm = chosen
    prop.blank_length_mm = _round_to(chosen + allowance_mm)
    prop.blank_mass_kg = blank_mass_kg(section, prop.blank_length_mm, density_kg_m3)
    ratio = (prop.blank_mass_kg / clean_mass_kg) if (prop.blank_mass_kg and clean_mass_kg) else 99
    prop.confidence = "высокая" if (len(ok) == 1 and ratio <= HIGH_CONF_MAX_RATIO) else "низкая"
    if len(ok) == 1 and ratio > HIGH_CONF_MAX_RATIO:
        prop.note = (f"заготовка тяжелее детали в {ratio:.1f} раза: "
                     "проверьте длину, возможна деталь с большими выборками")
    if len(ok) > 1:
        prop.note = ("два допустимых варианта длины детали: "
                     + " или ".join(f"{c:g}" for c in ok)
                     + " мм (возможен половинный размер)")
    return prop
