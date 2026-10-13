# -*- coding: utf-8 -*-
"""
Сверка массы заготовки с массой детали.

Заготовка не может весить меньше готовой детали. И вес заготовки не должен
быть во много раз больше детали: на 74 проверенных эталонах отношение
заготовка/деталь лежит в пределах 1,01...7,2 (медиана 1,9), поэтому порог
подозрительного отношения взят с запасом.
"""

from __future__ import annotations

from typing import Optional, Tuple

MIN_RATIO = 0.97      # ниже: заготовка легче детали (с допуском на округление)
MAX_RATIO = 10.0      # выше: слишком много металла, вероятна ошибка типа/размера


def to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 0 else None


def check(blank_mass, clean_mass) -> Tuple[bool, str]:
    """(ok, сообщение). Если одной из масс нет, проверка не выполняется (ok=True)."""
    b, c = to_float(blank_mass), to_float(clean_mass)
    if b is None or c is None:
        return True, ""
    ratio = b / c
    if ratio < MIN_RATIO:
        return False, (f"масса заготовки {b:g} кг меньше массы детали {c:g} кг: "
                       f"вероятно неверно определены тип или размер заготовки")
    if ratio > MAX_RATIO:
        return False, (f"масса заготовки {b:g} кг больше массы детали {c:g} кг в {ratio:.1f} раза: "
                       f"проверьте тип и размер заготовки")
    return True, ""
