import threading
from datetime import datetime
from typing import Callable, List

_listeners: List[Callable[[str], None]] = []
_lock = threading.Lock()
_history: List[str] = []


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str, level: str = "INFO") -> None:
    line = f"[{_ts()}] [{level}] {msg}"
    with _lock:
        _history.append(line)
        if len(_history) > 2000:
            _history.pop(0)
        for fn in list(_listeners):
            try:
                fn(line)
            except Exception:
                pass


def info(msg: str) -> None:
    log(msg, "INFO")


def warn(msg: str) -> None:
    log(msg, "WARN")


def error(msg: str, exc: Exception = None) -> None:
    if exc:
        msg = f"{msg} → {type(exc).__name__}: {exc}"
    log(msg, "ERROR")


def debug(msg: str) -> None:
    log(msg, "DEBUG")


def subscribe(fn: Callable[[str], None]) -> None:
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def unsubscribe(fn: Callable[[str], None]) -> None:
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def get_history() -> List[str]:
    with _lock:
        return list(_history)


def clear() -> None:
    with _lock:
        _history.clear()