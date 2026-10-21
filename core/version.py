# -*- coding: utf-8 -*-
"""Версия сборки: чтобы сразу видеть, какая версия программы стоит на компьютере.

В установщике сборки (GitHub Actions) рядом лежит build_info.txt с коротким
номером коммита и датой. При запуске из исходников берётся номер из git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _candidates():
    here = Path(__file__).resolve().parent.parent
    yield here / "build_info.txt"
    base = getattr(sys, "_MEIPASS", None)
    if base:
        yield Path(base) / "build_info.txt"


def get_build() -> str:
    for p in _candidates():
        try:
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
        except Exception:                                      # noqa: BLE001
            continue
    try:
        root = Path(__file__).resolve().parent.parent
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=3).stdout.strip()
        if sha:
            return f"{sha} (исходники)"
    except Exception:                                          # noqa: BLE001
        pass
    return "неизвестно"
