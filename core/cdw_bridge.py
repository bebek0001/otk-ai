# -*- coding: utf-8 -*-
"""
CDW_BRIDGE.PY — мост между чертежами КОМПАС (.cdw) и движком OTK.AI

Зачем нужен: engine.py умеет работать только с объектом Extracted,
собранным из текста PDF. Этот модуль собирает такой же Extracted из
структуры .cdw — и весь остальной конвейер (база эталонов, RAG, расчёт,
экспорт) работает без единой правки.

ВАЖНО: engine.py этот файл НЕ меняет и не требует изменений.

Что даёт CDW по сравнению с PDF:
    обозначение, наименование, материал, плотность, масса чистовая,
    литера, формат, шероховатость, техтребования — читаются ТОЧНО,
    а не распознаются регулярками из штампа.

Что CDW пока НЕ даёт: размер заготовки. Длина берётся из
параметрических переменных чертежа, если конструктор их вёл.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from core import cdw_reader, cdw_geometry
from core import logger

# Extracted и DiameterFeature берём из движка — структура одна на всех
from core.engine import (
    Extracted,
    DiameterFeature,
    normalize_mark,
    normalize_gost,
    extract_from_pdf,
)


# ============================================================
# Разбор поля «Материал» из штампа КОМПАСа
# ============================================================

_MAT_PAT = re.compile(
    r"^\s*(.+?)\s+(ГОСТ\s*[\d\-–]+|ТУ\s*[\d\-–\.]+)\s*$",
    flags=re.IGNORECASE,
)


def split_material(material: str) -> Tuple[Optional[str], Optional[str]]:
    """
    'Ст2пс ГОСТ 380-2005'            → ('Ст2пс',  'ГОСТ380-2005')
    'Сталь 45 ГОСТ 1050-2013'        → ('Сталь45','ГОСТ1050-2013')
    'Сталь 10Х11Н23Т3МР ГОСТ 5632-2014' → ('Сталь10Х11Н23Т3МР', 'ГОСТ5632-2014')
    'АМг5 ГОСТ 4784-2019'            → ('АМг5',   'ГОСТ4784-2019')
    """
    if not material:
        return None, None
    m = _MAT_PAT.match(material.strip())
    if not m:
        return normalize_mark(material.strip()) or None, None
    mark = normalize_mark(m.group(1))
    gost = normalize_gost(m.group(2).replace("–", "-"))
    return (mark or None), (gost or None)


# ============================================================
# Синтетический «текст чертежа»
# ============================================================

def build_pseudo_text(card: dict, geom: dict) -> str:
    """
    Собирает текстовый блок, похожий на штамп PDF-чертежа.

    Нужен, чтобы без правок работали существующие функции:
    lookup_etalon(), get_gost_context_for_query(), RAG-поиск,
    extract_drawing_no() и т.д. Все они ждут на вход текст.
    """
    lines = []
    if card.get("marking"):
        lines.append(card["marking"])
    if card.get("name"):
        lines.append(card["name"])
    if card.get("revision"):
        lines.append(card["revision"])
    if card.get("material"):
        lines.append(card["material"])
    if card.get("mass") is not None:
        lines.append("Масса")
        lines.append(str(card["mass"]).replace(".", ","))
    if card.get("format"):
        lines.append(card["format"])
    if card.get("roughness"):
        lines.append(card["roughness"])

    for ref in card.get("library_refs", []):
        # 'Уголок неравнопол. ГОСТ 8510.frw' → сортамент прямо из библиотеки
        lines.append(os.path.splitext(ref)[0])

    if card.get("tech_demands"):
        lines.append("Технические требования")
        lines.extend(card["tech_demands"])

    dims = geom.get("dimensions") or []
    if dims:
        lines.append("Размеры чертежа:")
        lines.append(" ".join(_fmt_num(d) for d in dims))

    return "\n".join(lines)


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 1e-9 else f"{f:g}"


# ============================================================
# Оценка длины детали
# ============================================================

# Сколько размерных переменных должно быть в чертеже, чтобы поверить,
# что он вёлся параметрически и наибольшая переменная — это длина детали.
#
# Почему порог нужен. На проверенной выборке из 10 чертежей полностью
# параметрическими оказались два: «Ось правая» (9 переменных, максимум 103 —
# точная длина) и «Направляющая» (6 переменных, максимум 160 — точная длина).
# У остальных восьми переменных 1–4 штуки, и максимум среди них — случайный
# размер (толщина, фаска, диаметр отверстия), а НЕ длина. Без порога такой
# мусор молча ушёл бы в расчёт заготовки и дал бы правдоподобный, но неверный
# результат — худший вид ошибки. Лучше честно вернуть None.
MIN_PARAM_VARS = 5

MIN_LENGTH_MM = 10.0
MAX_LENGTH_MM = 12000.0


def guess_length_mm(geom: dict, looks_rotational: bool = False) -> Optional[float]:
    """
    Длина детали — наибольшая параметрическая переменная чертежа.

    Возвращает значение ТОЛЬКО для чертежей, которые конструктор вёл
    параметрически. Иначе None — и вызывающий код честно сообщит,
    что длина не определена, вместо того чтобы подставить случайное число.

    ИСКЛЮЧЕНИЕ для тел вращения с РОВНО двумя переменными (вал/штырь/ось,
    где конструктор проставил только Ø и длину, больше ничего).
    Порог MIN_PARAM_VARS=5 придуман против чертежей с 1–4 ПОСТОРОННИМИ
    переменными (фаска, резьба, диаметр отверстия), среди которых максимум
    случаен. Но когда переменных ровно две и деталь по имени — тело
    вращения, других кандидатов просто нет: это Ø и длина, и большее
    значение почти всегда длина (на реальных файлах — «Штырь» 290426.00.00.001:
    48 и 55.426 → 55.426 действительно длина). Подтверждено на реальных
    чертежах клиента, не угадано.
    """
    dims = [float(d) for d in (geom.get("dimensions") or []) if d]
    dims = [d for d in dims if MIN_LENGTH_MM <= d <= MAX_LENGTH_MM]
    if len(dims) >= MIN_PARAM_VARS:
        return max(dims)
    if looks_rotational and len(dims) == 2:
        return max(dims)
    return None


def guess_diameter_mm(geom: dict, looks_rotational: bool = False) -> Optional[float]:
    """
    Диаметр тела вращения в той же узкой, подтверждённой ситуации, что и
    guess_length_mm: ровно две параметрические переменные, деталь по имени —
    тело вращения. Тогда меньшее значение — Ø, большее — длина (длина вала/
    штыря/оси почти всегда больше его диаметра).

    ВАЖНО: это НЕ общее решение для диаметра — для чертежей с 5+ переменными
    (сложные валы с несколькими ступенями) диаметр здесь по-прежнему не
    определяется, это отдельная, более сложная задача (нужно знать, какая
    именно переменная — наружный Ø, а не диаметр отверстия/фаска/резьба).
    """
    dims = [float(d) for d in (geom.get("dimensions") or []) if d]
    dims = [d for d in dims if MIN_LENGTH_MM <= d <= MAX_LENGTH_MM]
    if looks_rotational and len(dims) == 2:
        return min(dims)
    return None


# ============================================================
# Главная функция: .cdw → Extracted
# ============================================================

def extract_from_cdw(cdw_path: str) -> Extracted:
    """
    Читает чертёж КОМПАС структурно и отдаёт Extracted —
    тот же объект, что возвращает engine.extract_from_pdf().
    """
    logger.info(f"Читаю CDW структурно: {os.path.basename(cdw_path)}")

    if not cdw_reader.is_cdw_supported(cdw_path):
        raise ValueError(
            "Файл не является ZIP-контейнером: скорее всего КОМПАС ниже v18 "
            "(старый формат OLE). Нужен PDF этого чертежа."
        )

    card = cdw_reader.read_cdw(cdw_path)
    if not card.get("ok"):
        raise ValueError(card.get("error") or "CDW не разобран")

    try:
        geom = cdw_geometry.read_geometry(cdw_path)
    except Exception as e:                              # noqa: BLE001
        logger.warn(f"Геометрия CDW не разобрана: {e}")
        geom = {"dimensions": [], "variables": {}, "views": [], "candidates": []}

    mark, gost = split_material(card.get("material", ""))
    pseudo = build_pseudo_text(card, geom)

    part_name = card.get("name") or os.path.splitext(os.path.basename(cdw_path))[0]
    nl = part_name.lower()
    looks_rot = any(w in nl for w in ("ось", "вал", "втулк", "ролик", "фланец", "штырь", "шайб"))

    length = guess_length_mm(geom, looks_rot)
    diameter = guess_diameter_mm(geom, looks_rot)

    logger.info(
        f"CDW: {card.get('marking')} / {part_name} / {card.get('material')} / "
        f"масса {card.get('mass')} кг / размеров из переменных: {len(geom.get('dimensions') or [])}"
    )

    ex = Extracted(
        pdf_text=pseudo,
        part_name=part_name,
        looks_rotational=looks_rot,

        material_mark=mark,
        material_gost=gost,

        # масса чистовая из CDW — точная, не распознанная
        stamp_mass_kg=card.get("mass"),

        diameter_features=([DiameterFeature(d_mm=diameter, tol=None)] if diameter else []),
        max_d_mm=diameter,
        max_d_tol=None,

        square_features_mm=[],
        max_square_mm=None,

        ra_values=_parse_ra(card.get("roughness", "")),
        length_mm=length,
        pdf_path=None,          # Vision по CDW не запускаем — картинки нет
    )

    # Дополнительные поля кладём как атрибуты: dataclass их не объявляет,
    # но Python это позволяет, а batch_tool ими пользуется.
    ex.cdw_card = card          # type: ignore[attr-defined]
    ex.cdw_geometry = geom      # type: ignore[attr-defined]
    return ex


def _parse_ra(rough: str) -> list:
    """'Ra 12,5' → [12.5]"""
    if not rough:
        return []
    m = re.search(r"Ra\s*(\d+(?:[.,]\d+)?)", rough, flags=re.IGNORECASE)
    if not m:
        return []
    try:
        return [float(m.group(1).replace(",", "."))]
    except ValueError:
        return []


# ============================================================
# Диспетчер: единая точка входа для любого чертежа
# ============================================================

SUPPORTED_EXT = (".cdw", ".pdf")


def is_cdw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".cdw"


def read_any(path: str, max_pages: int = 3) -> Extracted:
    """
    .cdw — читаем структурно (точнее и быстрее),
    всё остальное — старым путём через PDF.
    """
    if is_cdw(path):
        return extract_from_cdw(path)
    return extract_from_pdf(path, max_pages=max_pages)