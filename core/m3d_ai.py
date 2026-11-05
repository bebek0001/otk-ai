# -*- coding: utf-8 -*-
"""
core/m3d_ai.py — запрос к Claude для моделей, у которых размер заготовки не
определился правилами.

ЧТО ЗДЕСЬ ПРОИСХОДИТ. Модель .m3d закрытый бинарный формат, и Claude по API её
не «видит». Видит он то же, что видел бы я в чате: сводку, которую собирает
код (обозначение, сортамент, масса, найденные габаритные параллелепипеды,
самые частые размеры в файле). Ответ Claude проверяется кодом по массе
(заготовка не может быть легче детали и не может быть нелепо тяжёлой) и в
таблицу попадает с пометкой «ИИ: проверьте». Подтверждённых правилами
строк (с размером) запрос не касается, их ИИ не меняет.
"""

from __future__ import annotations

import collections
import json
import math
import os
import re
import struct
from typing import Callable, Optional

from core import cdw_geometry, logger
from core import m3d_stock as ms

M3D_AI_MODEL = "claude-sonnet-5-5"

SYSTEM = (
    "Ты помощник технолога машиностроительного предприятия. По сводке данных "
    "из файла 3D-модели КОМПАС определи размер заготовки (по ГОСТ листа и проката). "
    "Отвечай только JSON без пояснений вне него: "
    '{"kind":"sheet"|"lin"|"unknown","h":число|null,"l":число|null,'
    '"confidence":"high"|"medium"|"low","reason":"одна короткая фраза"}. '
    "Для листа h и l это размеры в плоскости листа (h не больше l), мм. "
    "Для проката l это длина заготовки, мм. Если данных недостаточно, верни "
    '"kind":"unknown". Размеры не выдумывай: бери только из сводки.'
)


def frequent_values(path: str, top: int = 12):
    """Самые частые круглые значения (15..20000 мм) в геометрии модели."""
    cnt: collections.Counter = collections.Counter()
    try:
        chunks = cdw_geometry.unpack_contents(path)
    except Exception:                                          # noqa: BLE001
        return []
    for chunk in chunks:
        for sh in range(8):
            n = (len(chunk) - sh) // 8
            if n < 1:
                continue
            for x in struct.unpack_from("<%dd" % n, chunk, sh):
                if x == x and 15.0 <= x <= 20000.0 and abs(x - round(x, 1)) < 1e-6:
                    cnt[round(x, 1)] += 1
    skip = ms._POW2
    return [(v, c) for v, c in cnt.most_common(top + 8) if v not in skip][:top]


def collect_facts(path: str, row: dict) -> dict:
    """Сводка для запроса: только то, что реально нашлось в файле."""
    boxes = ms.strong_boxes(path)
    boxes.sort(key=lambda b: -b[1])
    return {
        "обозначение": row.get("drawing_no", ""),
        "наименование": row.get("part_name", ""),
        "заготовка (сортамент)": row.get("zagotovka_full") or row.get("new_sortament", ""),
        "масса детали, кг": row.get("clean_mass_kg", ""),
        "габаритные параллелепипеды тела (мм, повторов)": [
            [list(d), w] for d, w in boxes[:12]],
        "самые частые размеры в геометрии (мм, повторов)": [
            [v, c] for v, c in frequent_values(path)],
    }


def _to_float(x) -> Optional[float]:
    try:
        v = float(str(x).replace(",", "."))
        return v if v > 0 and math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def validate(answer: dict, material: str, mass_kg: float, density) -> Optional[ms.Stock]:
    """Ответ ИИ принимается, только если проходит проверку по массе."""
    if not isinstance(answer, dict) or answer.get("kind") not in ("sheet", "lin"):
        return None
    head = ms._head(material)
    rho = ms._rho(density)
    if answer["kind"] == "sheet":
        t = ms._sheet_thickness(head)
        h, l = _to_float(answer.get("h")), _to_float(answer.get("l"))
        if not (t and h and l):
            return None
        h, l = min(h, l), max(h, l)
        bm = h * l * t * rho * 1e-6
        st = ms.Stock("sheet", h_mm=h, l_mm=l, thickness_mm=t, method="ИИ")
        st.blank_mass_kg = round(bm, 6)
    else:
        sec = ms._linear_section(head)
        L = _to_float(answer.get("l"))
        if not (sec and L):
            return None
        kind, label, need = sec
        bm = ms._lin_mass(kind, head, need, L, rho)
        if not bm:
            return None
        st = ms.Stock("lin", l_mm=L, section=label, method="ИИ")
        st.blank_mass_kg = bm
    if mass_kg and not (0.98 * mass_kg <= bm <= 6.0 * mass_kg):
        return None
    return st


def _default_client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def ask(facts: dict, client=None) -> Optional[dict]:
    """Один запрос к Claude. Возвращает разобранный JSON или None."""
    try:
        client = client or _default_client()
        msg = client.messages.create(
            model=M3D_AI_MODEL, max_tokens=400, system=SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(facts, ensure_ascii=False, indent=1)}])
        text = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:                                     # noqa: BLE001
        logger.warn(f"M3D-ИИ: запрос не удался: {str(e)[:120]}")
        return None


def suggest(path: str, row: dict, material: str, mass_kg, density,
            client=None, facts_fn: Callable = collect_facts) -> Optional[tuple]:
    """(Stock, reason) или None. Для строки без размера."""
    facts = facts_fn(path, row)
    ans = ask(facts, client)
    if not ans:
        return None
    st = validate(ans, material, mass_kg, density)
    if st is None:
        logger.info(f"M3D-ИИ: ответ не прошёл проверку по массе: {ans}")
        return None
    return st, str(ans.get("reason", ""))[:120]
