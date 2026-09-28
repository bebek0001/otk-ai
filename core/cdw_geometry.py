# -*- coding: utf-8 -*-
"""
cdw_geometry.py — извлечение геометрии, размерных переменных и превью
из чертежей КОМПАС-3D (.cdw) БЕЗ запуска КОМПАСа.

Дополняет cdw_reader.py, который читает паспортные свойства (обозначение,
наименование, материал, плотность, масса).

Формат потока Contents:
    "KF" + цепочка независимых zlib-чанков (без внешнего заголовка длин).

Найденные записи (реверс-инжиниринг, КОМПАС v22):
    02 00 2B 2C   объект «точка»: далее две записи-значения (x, y)
    02 80 37 29   значение double: 3 байта id + float64
    02 80 26 48   имя переменной: 3 байта id + uint32 len + UTF-16LE имя
                  + 3 байта ссылки на id значения
    xx xx 47 08   координатная пара (x, y) внутри других объектов

ВАЖНО про надёжность: паспортные свойства читаются точно и всегда.
Геометрия — частично: разобраны не все типы объектов КОМПАСа, поэтому
габарит следует считать ОЦЕНКОЙ и проверять глазами, а не подставлять
в отчёт молча. Подробности — в README_cdw.md.

Зависимости: стандартная библиотека. Pillow — только для превью в PNG.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

TAG_POINT = b"\x02\x00\x2b\x2c"     # объект «точка»
TAG_VALUE = b"\x02\x80\x37\x29"     # значение double
TAG_NAME = b"\x02\x80\x26\x48"      # имя переменной
TAG_COORD = b"\x47\x08"             # координатная пара внутри объекта

VALUE_HEADER = 4 + 3                # тег + id, дальше float64
NAME_ID_LEN = 3


# --------------------------------------------------------------------------- #
#  Распаковка Contents
# --------------------------------------------------------------------------- #

def unpack_contents(path: str | Path) -> list[bytes]:
    """Вернуть список распакованных чанков потока Contents."""
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("Contents")
    except (KeyError, zipfile.BadZipFile, OSError):
        return []
    if data[:2] == b"KF":
        data = data[2:]
    out: list[bytes] = []
    pos, n = 0, len(data)
    while pos < n - 1:
        if data[pos] == 0x78 and data[pos + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            obj = zlib.decompressobj()
            try:
                chunk = obj.decompress(bytes(data[pos:]))
            except zlib.error:
                pos += 1
                continue
            if chunk:
                out.append(chunk)
                pos = n - len(obj.unused_data)
                continue
        pos += 1
    return out


def _finite(v: float, limit: float = 1e4) -> bool:
    return v == v and abs(v) < limit


# --------------------------------------------------------------------------- #
#  Размерные переменные
# --------------------------------------------------------------------------- #

def extract_variables(chunks: list[bytes]) -> dict[str, float]:
    """
    Параметрические переменные чертежа: {'v207': 103.0, ...}.

    Если конструктор вёл чертёж параметрически, здесь лежат ТОЧНЫЕ значения
    размеров, проставленных на чертеже. Если нет — словарь будет почти пуст,
    и это нормально: значит размеры не параметризованы.
    """
    values: dict[bytes, float] = {}
    names: dict[str, bytes] = {}

    for b in chunks:
        i = b.find(TAG_VALUE)
        while i >= 0:
            if i + VALUE_HEADER + 8 <= len(b):
                v = struct.unpack_from("<d", b, i + VALUE_HEADER)[0]
                if v == v:
                    values[b[i + 4:i + 7]] = v
            i = b.find(TAG_VALUE, i + 1)

        i = b.find(TAG_NAME)
        while i >= 0:
            if i + 11 <= len(b):
                ln = struct.unpack_from("<I", b, i + 7)[0]
                end = i + 11 + ln * 2
                if 0 < ln < 64 and end + NAME_ID_LEN <= len(b):
                    try:
                        nm = b[i + 11:end].decode("utf-16-le")
                    except UnicodeDecodeError:
                        nm = ""
                    if nm:
                        names[nm] = b[end:end + NAME_ID_LEN]
            i = b.find(TAG_NAME, i + 1)

    return {nm: values[ref] for nm, ref in names.items() if ref in values}


# --------------------------------------------------------------------------- #
#  Точки
# --------------------------------------------------------------------------- #

def extract_points(chunks: list[bytes]) -> list[tuple[float, float]]:
    """Все координатные пары, которые удалось разобрать (обе схемы записи)."""
    pts: list[tuple[float, float]] = []

    for b in chunks:
        # схема 1: объект «точка» -> две записи-значения
        i = b.find(TAG_POINT)
        while i >= 0:
            if b[i + 4:i + 8] == TAG_VALUE and b[i + 19:i + 23] == TAG_VALUE:
                x = struct.unpack_from("<d", b, i + 11)[0]
                y = struct.unpack_from("<d", b, i + 26)[0]
                if _finite(x) and _finite(y):
                    pts.append((x, y))
            i = b.find(TAG_POINT, i + 1)

        # схема 2: координатная пара внутри объекта
        i = b.find(TAG_COORD)
        while i >= 0:
            if i + 2 + 16 <= len(b):
                x, y = struct.unpack_from("<dd", b, i + 2)
                if _finite(x) and _finite(y):
                    pts.append((x, y))
            i = b.find(TAG_COORD, i + 1)

    return pts


def cluster_views(pts: list[tuple[float, float]], eps: float = 25.0
                  ) -> list[dict[str, float]]:
    """
    Разбить облако точек на виды (виды разнесены по листу) и вернуть
    габаритные прямоугольники, отсортированные по числу точек.
    """
    if not pts:
        return []
    remaining = list(dict.fromkeys(pts))
    groups: list[list[tuple[float, float]]] = []
    while remaining:
        group = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            for q in list(remaining):
                for r in group:
                    if abs(q[0] - r[0]) < eps and abs(q[1] - r[1]) < eps:
                        group.append(q)
                        remaining.remove(q)
                        changed = True
                        break
        groups.append(group)

    out = []
    for g in sorted(groups, key=len, reverse=True):
        xs = [q[0] for q in g]
        ys = [q[1] for q in g]
        out.append({
            "points": len(g),
            "width": round(max(xs) - min(xs), 3),
            "height": round(max(ys) - min(ys), 3),
        })
    return out


# --------------------------------------------------------------------------- #
#  Превью
# --------------------------------------------------------------------------- #

def extract_preview(path: str | Path, out_file: str | Path | None = None
                    ) -> bytes | None:
    """
    Достать растровое превью чертежа. Внутри потока Preview лежит TIFF:
        "KF" + 4 нуля + int32 (ширина) + int32 (цвет фона)
             + int32 (длина TIFF) + сам TIFF.

    Разрешение ~234x331 — годится только для миниатюры в интерфейсе,
    для распознавания размеров слишком мелко.
    """
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("Preview")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    if len(data) < 22 or data[:2] != b"KF":
        return None
    size = struct.unpack_from("<i", data, 14)[0]
    tif = data[18:18 + size]
    if tif[:2] not in (b"MM", b"II"):
        return None
    if out_file:
        out_file = Path(out_file)
        if out_file.suffix.lower() in (".png", ".jpg", ".jpeg"):
            try:
                from PIL import Image
                Image.open(io.BytesIO(tif)).convert("RGB").save(out_file)
            except Exception:                       # noqa: BLE001
                out_file.with_suffix(".tif").write_bytes(tif)
        else:
            out_file.write_bytes(tif)
    return tif


# --------------------------------------------------------------------------- #
#  Публичный API
# --------------------------------------------------------------------------- #

def read_geometry(path: str | Path) -> dict[str, Any]:
    """
    Геометрическая часть чертежа.

    Возвращает:
        variables   — {'v207': 103.0, ...} параметрические размеры (точно)
        dimensions  — отсортированные значения variables (кандидаты в размеры)
        candidates  — все «круглые» числа из файла (шире, грязнее)
        points      — сколько координатных пар разобрано
        views       — габариты по кластерам точек, крупнейший первым
        bbox        — общий габарит облака точек
        note        — предупреждение о точности
    """
    chunks = unpack_contents(path)
    if not chunks:
        return {"error": "Поток Contents не читается", "variables": {},
                "dimensions": [], "candidates": [], "points": 0,
                "views": [], "bbox": None}

    variables = extract_variables(chunks)
    pts = extract_points(chunks)

    candidates = set()
    for b in chunks:
        for i in range(len(b) - 8):
            v = struct.unpack_from("<d", b, i)[0]
            if v == v and 1.0 <= v < 2000 and abs(v - round(v, 1)) < 1e-6:
                candidates.add(round(v, 1))
    # 210/297 — формат A4, 128/350 — системные константы КОМПАСа
    candidates -= {210.0, 297.0, 128.0, 350.0}

    bbox = None
    if pts:
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        bbox = {"width": round(max(xs) - min(xs), 3),
                "height": round(max(ys) - min(ys), 3)}

    return {
        "error": "",
        "variables": variables,
        "dimensions": sorted({round(v, 3) for v in variables.values() if v > 0}),
        "candidates": sorted(candidates),
        "points": len(pts),
        "views": cluster_views(pts),
        "bbox": bbox,
        "note": ("Геометрия разобрана частично: не все типы объектов КОМПАСа "
                 "поддержаны. variables — точные значения, если чертёж вёлся "
                 "параметрически. views/bbox/candidates — оценка, требует "
                 "проверки человеком."),
    }


if __name__ == "__main__":
    import sys, json
    for arg in sys.argv[1:]:
        g = read_geometry(arg)
        g.pop("note", None)
        print(Path(arg).name)
        print(json.dumps(g, ensure_ascii=False, indent=2))