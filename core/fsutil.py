# -*- coding: utf-8 -*-
"""Безопасная запись JSON: сначала во временный файл, потом атомарная замена.
Сбой или выключение питания посреди записи не оставляют полупустой файл."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


def write_json_atomic(path, data: Any, *, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def quarantine_corrupt(path) -> str:
    """Копия повреждённого файла рядом (имя .corrupt-ГГГГММДД-ЧЧММСС), чтобы
    следующая запись не затёрла единственную копию данных. Возвращает путь копии."""
    path = Path(path)
    dst = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        shutil.copy2(path, dst)
        return str(dst)
    except OSError:
        return ""
