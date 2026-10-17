# -*- coding: utf-8 -*-
"""
Мост «рабочий поток -> главный поток Tk».

Tk нельзя трогать из рабочих потоков: на macOS это приводит к вылету
приложения, на Windows к редким зависаниям. Рабочий поток вызывает
post(fn, *args, **kwargs); функция выполняется в главном потоке, где её
подхватывает опрос по таймеру. install(root) вызывается один раз при запуске.
"""

from __future__ import annotations

import queue
from typing import Any, Callable, Optional

from core import logger

_q: "queue.Queue[tuple]" = queue.Queue()
_root: Optional[Any] = None
_POLL_MS = 60


def install(root) -> None:
    global _root
    _root = root
    _poll()


def post(fn: Callable, *args, **kwargs) -> None:
    """Безопасно из любого потока: выполнить fn(*args, **kwargs) в главном."""
    _q.put((fn, args, kwargs))


def _poll() -> None:
    try:
        while True:
            fn, args, kwargs = _q.get_nowait()
            try:
                fn(*args, **kwargs)
            except Exception as e:                            # noqa: BLE001
                logger.error(f"UI: ошибка обновления окна: {e}")
    except queue.Empty:
        pass
    if _root is not None:
        try:
            _root.after(_POLL_MS, _poll)
        except Exception:                                     # noqa: BLE001
            pass


def drain_for_tests() -> int:
    """Выполнить всё накопленное (для тестов без Tk). Возвращает число вызовов."""
    n = 0
    while True:
        try:
            fn, args, kwargs = _q.get_nowait()
        except queue.Empty:
            return n
        fn(*args, **kwargs)
        n += 1
