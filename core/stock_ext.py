# -*- coding: utf-8 -*-
"""
STOCK_EXT.PY — расширение движка новыми сортаментами.

Зачем отдельный файл. В engine.py 1400 строк рабочего кода с RAG и Vision.
Чтобы добавить четыре типа проката, его не нужно переписывать: этот модуль
при импорте дополняет справочники движка и подменяет две функции.
engine.py остаётся нетронутым.

Что добавляется (эти сортаменты есть в эталонной таблице заказчика,
но движок про них не знал и падал в дефолт «Круг»):

    Шестигранник   ГОСТ 2879-2006
    Полоса         ГОСТ 103-2006
    Уголок         ГОСТ 8509-93   (равнополочный)
    УголокНеравн   ГОСТ 8510-86   (неравнополочный)

Подключение — одна строка в batch_tool.py:
    from core.stock_ext import calculate_stock_line as calculate_missing_stock_line
"""

from __future__ import annotations

import math
import re
from typing import List, Optional

from core import engine
from core import logger
from core.engine import CalcResult, Extracted


# ============================================================
# 1. Расширяем справочники движка
# ============================================================

engine.STOCKTYPE_TO_GOST.update({
    "Шестигранник": "ГОСТ2879-2006",
    "Полоса":       "ГОСТ103-2006",
    "Уголок":       "ГОСТ8509-93",
    "УголокНеравн": "ГОСТ8510-86",
})

# Эти номера — ГОСТы на СОРТАМЕНТ. Без них «ГОСТ 103-2006» будет ошибочно
# принят за ГОСТ на материал, и марка стали определится неверно.
engine.STOCK_GOSTS |= {"2879", "103", "8509", "8510"}

# Порядок важен: «уголок неравнополочный» должен проверяться ДО «уголок»,
# иначе неравнополочный распознается как равнополочный.
engine.STOCK_TYPE_KEYWORDS = {
    "УголокНеравн": ["уголок неравнопол", "неравнополочн"],
    "Уголок":       ["уголок равнопол", "уголок"],
    "Шестигранник": ["шестигранник", "шестигран"],
    "Полоса":       ["полоса"],
    "ТрубаПроф":    ["труба проф", "профильная труба"],
    "Труба":        ["труба", "трубы"],
    "ЛистГК":       ["лист гк", "листгк", "лист горячекатан"],
    "ЛистХК":       ["лист хк", "листхк", "лист холоднокатан"],
    "Швеллер":      ["швеллер"],
    "Двутавр":      ["двутавр", "балка"],
    "Круг":         ["круг"],
    "Квадрат":      ["квадрат"],
}


# ============================================================
# 2. Определение типа заготовки из штампа
# ============================================================

_STAMP_PAT = re.compile(
    r"\b(лист|круг|квадрат|труба(?:\s*проф)?|швеллер|двутавр|балка"
    r"|шестигранник|полоса|уголок)"
    r"\s*\d+",
    re.IGNORECASE,
)


def detect_stock_type_from_stamp(pdf_text: str) -> Optional[str]:
    """
    Читает тип заготовки из штампа. Заменяет одноимённую функцию движка:
    добавлены шестигранник, полоса и уголок.
    """
    if not pdf_text:
        return None

    normalized = re.sub(r"\n+", " ", pdf_text)
    low = normalized.lower()

    # Приоритет 1: «<тип> <число>» — так пишут в штампе
    m = _STAMP_PAT.search(normalized)
    if m:
        kw = m.group(1).lower().strip()
        if kw == "уголок":
            return "УголокНеравн" if "неравнопол" in low else "Уголок"
        for stock_type, keywords in engine.STOCK_TYPE_KEYWORDS.items():
            if any(kw == k or kw.startswith(k.split()[0]) for k in keywords):
                return stock_type

    # Приоритет 2: слово встречается в тексте
    for stock_type, keywords in engine.STOCK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                return stock_type
    return None


engine.detect_stock_type_from_stamp = detect_stock_type_from_stamp


# ============================================================
# 3. Сортаменты и формулы массы
# ============================================================

# Шестигранник по ГОСТ 2879-2006, размер под ключ S, мм
HEX_SIZES_MM = [8, 9, 10, 11, 12, 13, 14, 17, 19, 22, 24, 27, 30, 32,
                36, 41, 46, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]

# Полоса по ГОСТ 103-2006
STRIP_THICKNESS_MM = [4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22,
                      25, 28, 30, 32, 36, 40, 45, 50, 56, 60]
STRIP_WIDTH_MM = [10, 11, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 36, 40,
                  45, 50, 56, 60, 63, 65, 70, 75, 80, 85, 90, 95, 100,
                  110, 120, 125, 130, 140, 150, 160, 170, 180, 190, 200]


def _pick_up(value: float, table: List[int]) -> int:
    """Ближайший стандартный размер не меньше требуемого."""
    for v in table:
        if v >= value:
            return int(v)
    return int(table[-1])


def calc_hex_bar_mass_kg(s_mm: float, l_mm: float, density_kg_m3: float) -> float:
    """
    Шестигранник. Площадь сечения = (√3/2)·S², где S — размер под ключ.
    Проверка: S=24, L=150, ρ=7850 → 0,587 кг (у заказчика 0,6).
    """
    area_m2 = (math.sqrt(3) / 2.0) * (s_mm / 1000.0) ** 2
    return density_kg_m3 * area_m2 * (l_mm / 1000.0)


def calc_strip_mass_kg(t_mm: float, b_mm: float, l_mm: float,
                       density_kg_m3: float) -> float:
    """
    Полоса: толщина × ширина × длина.
    Проверка: 12×45, L=110, ρ=7850 → 0,466 кг (у заказчика 0,47).
    """
    return density_kg_m3 * (t_mm / 1000.0) * (b_mm / 1000.0) * (l_mm / 1000.0)


def calc_angle_mass_kg(b_mm: float, b2_mm: float, t_mm: float, l_mm: float,
                       density_kg_m3: float) -> float:
    """
    Уголок. Площадь ≈ (B + b − t)·t.

    ВНИМАНИЕ: это приближение. Оно игнорирует скругления в углу и на
    кромках полок, поэтому занижает массу примерно на 1–2 %.
    Проверка: 100×63×10, L=170, ρ=7850 → 2,04 кг (у заказчика 2,1).

    Для точности нужна таблица погонного веса по ГОСТ 8509/8510 —
    так же, как сделано для швеллера в engine.SHVELLER_KG_PER_M.
    Пока таблицы нет, значение помечается в протоколе как приближённое.
    """
    area_m2 = ((b_mm + b2_mm - t_mm) * t_mm) / 1e6
    return density_kg_m3 * area_m2 * (l_mm / 1000.0)


engine.calc_hex_bar_mass_kg = calc_hex_bar_mass_kg
engine.calc_strip_mass_kg = calc_strip_mass_kg
engine.calc_angle_mass_kg = calc_angle_mass_kg


# ============================================================
# 4. Чтение размеров профиля из штампа
# ============================================================

def detect_hex_size(text: str) -> Optional[float]:
    """'Шестигранник 24 ГОСТ 2879-2006' → 24.0"""
    if not text:
        return None
    m = re.search(r"шестигран\w*\s*(\d+(?:[.,]\d+)?)", text, flags=re.IGNORECASE)
    return engine.safe_float(m.group(1)) if m else None


def detect_strip_size(text: str):
    """'Полоса 12х45 ГОСТ 103-2006' → (12.0, 45.0)"""
    if not text:
        return None, None
    m = re.search(r"полоса\s*(\d+(?:[.,]\d+)?)\s*[xхXХ*]\s*(\d+(?:[.,]\d+)?)",
                  text, flags=re.IGNORECASE)
    if not m:
        return None, None
    return engine.safe_float(m.group(1)), engine.safe_float(m.group(2))


def detect_angle_size(text: str):
    """'Уголок 100х63х10 ГОСТ 8510-86' → (100.0, 63.0, 10.0)"""
    if not text:
        return None, None, None
    m = re.search(
        r"уголок\s*(\d+(?:[.,]\d+)?)\s*[xхXХ]\s*(\d+(?:[.,]\d+)?)"
        r"\s*[xхXХ]\s*(\d+(?:[.,]\d+)?)",
        text, flags=re.IGNORECASE)
    if m:
        return (engine.safe_float(m.group(1)), engine.safe_float(m.group(2)),
                engine.safe_float(m.group(3)))
    # равнополочный пишут двумя числами: «Уголок 63х6»
    m = re.search(r"уголок\s*(\d+(?:[.,]\d+)?)\s*[xхXХ]\s*(\d+(?:[.,]\d+)?)",
                  text, flags=re.IGNORECASE)
    if m:
        b = engine.safe_float(m.group(1))
        return b, b, engine.safe_float(m.group(2))
    return None, None, None


# ============================================================
# 5. Расчёт для новых типов + делегирование движку
# ============================================================

NEW_TYPES = {"Шестигранник", "Полоса", "Уголок", "УголокНеравн"}


def _density_for(ex: Extracted) -> float:
    """
    Плотность материала. Если чертёж пришёл из КОМПАСа, берём её прямо
    из файла — там она задана для конкретной марки (нержавейка 7900,
    алюминий 2650 и т.д.), а не усреднённая сталь.
    """
    card = getattr(ex, "cdw_card", None)
    if isinstance(card, dict):
        d = card.get("density")
        try:
            d = float(d)
            if 500.0 < d < 25000.0:
                return d
        except (TypeError, ValueError):
            pass
    return engine.DEFAULT_STEEL_DENSITY_KG_M3


def calculate_stock_line(ex: Extracted, l_part_mm: float,
                         material_mark: str, material_gost: str) -> CalcResult:
    """
    Замена engine.calculate_missing_stock_line().

    Новые сортаменты считает сам, всё остальное передаёт движку без изменений
    (включая слои RAG и Vision — они внутри движка и работают как раньше).
    """
    stock_type = engine.detect_stock_type(ex)
    if stock_type not in NEW_TYPES:
        return engine.calculate_missing_stock_line(
            ex, l_part_mm, material_mark, material_gost)

    logger.info(f"stock_ext: считаю новый сортамент «{stock_type}»")

    text = ex.pdf_text or ""
    gost_stock = engine.pick_gost_for_stock(stock_type)
    gost_pdf = engine.find_gost_pdf(gost_stock, engine.GOST_DIR)

    density = _density_for(ex)
    l_blank = float(l_part_mm) + engine.ADD_LENGTH_MM
    mass_stamp = ex.stamp_mass_kg

    material_out = material_mark
    if engine.MATERIAL_NORMALIZE_TO_BASE and material_out:
        material_out = re.sub(r"(Ст\s*\d+)(?:пс|сп)\b", r"\1",
                              material_out, flags=re.IGNORECASE)
        material_out = engine.normalize_mark(material_out)

    prot: List[str] = [
        "Протокол подбора заготовки (расширенные сортаменты)",
        f"- Файл: {ex.part_name}",
        f"- Тип заготовки: {stock_type}",
        f"- ГОСТ сортамента: {gost_stock}"
        + (f" (файл: {gost_pdf})" if gost_pdf else " (PDF ГОСТа не найден)"),
        f"- Материал: {material_mark} {material_gost}",
        f"- Плотность: {density} кг/м³"
        + (" (из файла КОМПАС)" if density != engine.DEFAULT_STEEL_DENSITY_KG_M3 else ""),
        f"- Длина детали: {l_part_mm} мм, Lзаг = {l_blank} мм "
        f"(+{engine.ADD_LENGTH_MM})",
    ]

    square_mm = None
    approx = False

    # ---------- Шестигранник ----------
    if stock_type == "Шестигранник":
        s = detect_hex_size(text)
        if s is None:
            raise ValueError(
                "Не определён размер под ключ для шестигранника. "
                "Нужен размер из чертежа или строка вида «Шестигранник 24» в штампе.")
        s_std = _pick_up(s, HEX_SIZES_MM)
        mass_physics = calc_hex_bar_mass_kg(s_std, l_blank, density)
        stock_desc = f"Шестигранник {s_std} {gost_stock}"
        square_mm = float(s_std)
        prot.append(f"- Размер под ключ: {s} мм → по сортаменту {s_std} мм")

    # ---------- Полоса ----------
    elif stock_type == "Полоса":
        t, b = detect_strip_size(text)
        if t is None or b is None:
            raise ValueError(
                "Не определены толщина и ширина полосы. "
                "Нужна строка вида «Полоса 12х45» в штампе или размеры из чертежа.")
        t_std = _pick_up(t, STRIP_THICKNESS_MM)
        b_std = _pick_up(b, STRIP_WIDTH_MM)
        mass_physics = calc_strip_mass_kg(t_std, b_std, l_blank, density)
        stock_desc = f"Полоса {t_std}х{b_std} {gost_stock}"
        prot.append(f"- Сечение полосы: {t}х{b} мм → по сортаменту {t_std}х{b_std} мм")

    # ---------- Уголок ----------
    else:
        b1, b2, t = detect_angle_size(text)
        if b1 is None or t is None:
            raise ValueError(
                "Не определены размеры уголка. "
                "Нужна строка вида «Уголок 100х63х10» в штампе.")
        mass_physics = calc_angle_mass_kg(b1, b2, t, l_blank, density)
        approx = True
        if abs(b1 - b2) < 1e-6:
            stock_desc = f"Уголок {int(b1)}х{int(t)} {gost_stock}"
        else:
            stock_desc = f"Уголок {int(b1)}х{int(b2)}х{int(t)} {gost_stock}"
        prot.append(f"- Полки уголка: {b1}х{b2} мм, толщина {t} мм")
        prot.append("- ВНИМАНИЕ: площадь сечения посчитана приближённо "
                    "(без скруглений), масса занижена примерно на 1–2 %. "
                    "Для точности нужна таблица погонного веса ГОСТ 8509/8510.")

    # ---------- Масса ----------
    if engine.MASS_MODE == "stamp_first" and mass_stamp is not None:
        mass_final = float(mass_stamp)
        mass_note = f"stamp_first (расчёт: {mass_physics:.3f})"
    else:
        mass_final = mass_physics
        mass_note = "расчёт по сечению" + (" — приближённо" if approx else "")

    result_line = engine.build_result_block_bar(stock_desc, l_blank, mass_final)
    prot.append(f"- Масса заготовки: {mass_final:.3f} кг ({mass_note})")

    preview = re.sub(r"\s+", " ", text).strip()
    prot += ["", "Текст чертежа (фрагмент):",
             preview[:520] + (" ..." if len(preview) > 520 else "")]

    logger.info(f"stock_ext: {result_line.replace(chr(10), ' | ')}")

    return CalcResult(
        stock_type=stock_type,
        gost_stock=gost_stock,
        gost_pdf_path=gost_pdf,

        material_out=material_out,
        material_gost=material_gost,

        d_part_mm=ex.max_d_mm,
        tol=ex.max_d_tol,
        ra=engine.choose_ra_to_use(ex.ra_values),

        tube_od_mm=None,
        tube_wall_mm=None,
        square_mm=square_mm,

        allowance_side_mm=0.0,
        d_blank_calc_mm=0.0,
        d_blank_std_mm=0,

        l_part_mm=float(l_part_mm),
        l_blank_mm=float(l_blank),

        density_kg_m3=density,
        mass_kg=mass_final,
        mass_physics_kg=mass_physics,
        mass_stamp_kg=mass_stamp,

        result_line=result_line,
        protocol="\n".join(prot),
    )


logger.info("stock_ext: сортаменты расширены — "
            "шестигранник, полоса, уголок равно- и неравнополочный")