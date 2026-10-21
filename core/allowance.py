# -*- coding: utf-8 -*-
"""
Обучаемый припуск на длину.

Припуск = длина заготовки у технолога минус длина детали по чертежу. Он
различается по типу проката (по данным заказчика: у труб +10, у круга и профиля
около 0). Программа запоминает расхождение каждый раз, когда технолог
исправляет предложенную длину, и по накопленным значениям предлагает свой
припуск для каждого типа. Значение «выучено», только если накоплено не менее
MIN_N наблюдений и они согласованы (разброс MAD не больше MAX_MAD мм); иначе
используется припуск из настроек.
"""

from __future__ import annotations

import json
import statistics
from typing import Dict, List, Tuple

from core import fsutil
from core.paths import AGENT_DIR

MIN_N = 5
MAX_MAD = 2.5
KEEP = 200


def _path():
    return AGENT_DIR / "allowance.json"


def _load() -> Dict[str, List[float]]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def record(kind: str, diff_mm: float) -> None:
    """Наблюдение: у технолога заготовка длиннее детали на diff_mm."""
    if not kind or diff_mm is None or not (-50 <= diff_mm <= 100):
        return
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    d = _load()
    d.setdefault(kind, []).append(round(float(diff_mm), 1))
    d[kind] = d[kind][-KEEP:]
    fsutil.write_json_atomic(_path(), d)


_DB_KIND = {"ТрубаПроф": "Профиль"}


def from_db(db: List[dict] | None = None) -> Dict[str, List[float]]:
    """Припуски из базы эталонов: длина заготовки минус длина детали.
    Эталоны из PDF содержат обе длины, это готовые наблюдения технолога."""
    if db is None:
        try:
            from core import drawing_db
            db = drawing_db.load_db()
        except Exception:                                      # noqa: BLE001
            return {}
    out: Dict[str, List[float]] = {}
    for e in db or []:
        a, b = e.get("l_part_mm"), e.get("l_blank_mm")
        kind = _DB_KIND.get(e.get("stock_type"), e.get("stock_type"))
        if not (kind and a and b) or kind.startswith("Лист"):
            continue
        diff = round(float(b) - float(a), 1)
        if -50 <= diff <= 100:
            out.setdefault(kind, []).append(diff)
    return out


def _consistent(vals: List[float]):
    if len(vals) < MIN_N:
        return None
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    return float(med) if mad <= MAX_MAD else None


def get(kind: str, default_mm: float, db: List[dict] | None = None) -> Tuple[float, int, bool]:
    """(припуск, число наблюдений, выучен ли).

    Приоритет: исправления технолога в таблицах (самые свежие), затем припуск,
    накопленный в базе эталонов, затем значение из настроек. Если наблюдения
    расходятся (разные заказы дают +10 и +20), значение не «выучено»."""
    vals = _load().get(kind, [])
    m = _consistent(vals)
    if m is not None:
        return m, len(vals), True
    prior = from_db(db).get(kind, [])
    m = _consistent(prior)
    if m is not None:
        return m, len(prior), True
    return float(default_mm), len(vals), False
