# -*- coding: utf-8 -*-
"""
Трассировка обработки: что решила программа по каждому файлу и почему.

Каждый этап (чтение чертежа, база, сверка, аналог, контур, размеры, припуск,
формулы, итог, решение агента) пишет одну строку в панель разработчика
и в файл processing_log/trace_ГГГГ-ММ-ДД.jsonl. Файл можно отправить
разработчику для разбора. Трассировка никогда не роняет обработку.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

from core import logger
from core.paths import PROCESSING_LOG_DIR

_local = threading.local()
_lock = threading.Lock()


def begin(path: str) -> None:
    _local.name = os.path.basename(path or "")


def collect_start() -> None:
    """Начать сбор этапов текущего потока в памяти (для вкладки «Обзор»)."""
    _local.steps = []


def collect_end() -> list:
    steps = getattr(_local, "steps", None) or []
    _local.steps = None
    return steps


def current() -> str:
    return getattr(_local, "name", "")


def step(stage: str, msg: str = "", **data: Any) -> None:
    name = current()
    buf = getattr(_local, "steps", None)
    if buf is not None:
        buf.append((stage, msg))
    try:
        logger.info(f"[{name}] {stage}: {msg}" if msg else f"[{name}] {stage}")
        rec = {"t": datetime.now().isoformat(timespec="seconds"),
               "file": name, "stage": stage, "msg": msg}
        if data:
            rec["data"] = data
        PROCESSING_LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = PROCESSING_LOG_DIR / f"trace_{datetime.now():%Y-%m-%d}.jsonl"
        with _lock, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:                                          # noqa: BLE001
        pass


def export_text(path: str) -> int:
    """Сохранить в один текстовый файл журнал панели и трассировку за сегодня.
    Возвращает число записанных строк."""
    lines = ["=== Журнал панели разработчика ==="] + logger.get_history()
    p = PROCESSING_LOG_DIR / f"trace_{datetime.now():%Y-%m-%d}.jsonl"
    lines += ["", "=== Трассировка за сегодня (JSON) ==="]
    try:
        lines += p.read_text(encoding="utf-8").splitlines()
    except Exception:                                          # noqa: BLE001
        lines.append("(нет файла трассировки)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)
