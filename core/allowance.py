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


def get(kind: str, default_mm: float) -> Tuple[float, int, bool]:
    """(припуск, число наблюдений, выучен ли)."""
    vals = _load().get(kind, [])
    if len(vals) >= MIN_N:
        med = statistics.median(vals)
        mad = statistics.median(abs(v - med) for v in vals)
        if mad <= MAX_MAD:
            return float(med), len(vals), True
    return float(default_mm), len(vals), False
