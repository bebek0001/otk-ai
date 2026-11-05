# -*- coding: utf-8 -*-
"""
core/m3d_preview.py — готовая картинка детали из модели КОМПАС (.m3d/.a3d).

В контейнере лежит поток «Preview»: небольшое TIFF-изображение 3D-вида детали
(то, что КОМПАС показывает в проводнике), перед ним короткий заголовок «KF».
Геометрию это не заменяет, но даёт реальный вид детали без КОМПАС.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

_SIGNATURES = (b"MM\x00*", b"II*\x00")           # TIFF: big- и little-endian


def preview_bytes(path: str) -> Optional[bytes]:
    """Байты TIFF-картинки из потока Preview или None."""
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("Preview")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    for sig in _SIGNATURES:
        i = data.find(sig)
        if 0 <= i < 64:
            return data[i:]
    return None


def read_preview(path: str, max_side: int = 420):
    """PIL.Image (RGB, не больше max_side по большей стороне) или None."""
    raw = preview_bytes(path)
    if not raw:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert("RGB")
    except Exception:                                          # noqa: BLE001
        return None
    k = max(im.size) / float(max_side)
    if k > 1:
        im = im.resize((max(1, round(im.size[0] / k)), max(1, round(im.size[1] / k))))
    return im
