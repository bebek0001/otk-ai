# -*- coding: utf-8 -*-
"""
integrations/kompas/client.py — приложение разговаривает с КОМПАС только через
отдельный процесс (см. worker.py). Падение, зависание или ошибка COM не
затрагивают окно приложения: запрос просто возвращает error, а следующий
запрос запускает процесс заново.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import List, Optional

from core import logger

_FIRST_TIMEOUT = 240.0      # первый запрос: КОМПАС стартует долго
_NEXT_TIMEOUT = 120.0       # открытие одной модели


def default_argv() -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--kompas-worker"]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [sys.executable, os.path.join(root, "main.py"), "--kompas-worker"]


class KompasWorker:
    def __init__(self, argv: Optional[List[str]] = None,
                 first_timeout: float = _FIRST_TIMEOUT, next_timeout: float = _NEXT_TIMEOUT):
        self._argv = argv
        self._first, self._next = first_timeout, next_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._n = 0
        self._started = False

    # --- процесс ---

    def _spawn(self) -> None:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000                   # CREATE_NO_WINDOW
        self._q = queue.Queue()
        self._proc = subprocess.Popen(
            self._argv or default_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, **kw)
        q, out = self._q, self._proc.stdout

        def pump() -> None:
            try:
                for line in out:                               # type: ignore[union-attr]
                    q.put(line)
            finally:
                q.put(None)                                    # процесс закрыл вывод

        threading.Thread(target=pump, daemon=True).start()
        self._started = False

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.kill()
            except Exception:                                  # noqa: BLE001
                pass

    # --- запросы ---

    def request(self, cmd: str, **fields) -> dict:
        """Ответ процесса или {"error": ...}. Никогда не бросает исключений."""
        try:
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
            self._n += 1
            rid = self._n
            self._proc.stdin.write(json.dumps({"id": rid, "cmd": cmd, **fields},
                                              ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            timeout = self._next if self._started else self._first
            while True:
                try:
                    line = self._q.get(timeout=timeout)
                except queue.Empty:
                    self._kill()
                    return {"error": f"КОМПАС не ответил за {int(timeout)} с, процесс остановлен"}
                if line is None:
                    self._kill()
                    return {"error": "Процесс КОМПАС завершился (сбой в КОМПАС или COM)"}
                try:
                    resp = json.loads(line)
                except ValueError:
                    continue
                if resp.get("id") == rid:
                    self._started = True
                    return resp
        except Exception as e:                                 # noqa: BLE001
            self._kill()
            return {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    def detect(self) -> dict:
        return self.request("detect")

    def body(self, path: str) -> dict:
        return self.request("body", path=path)

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.write(json.dumps({"id": 0, "cmd": "quit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=10)
            except Exception:                                  # noqa: BLE001
                pass
        self._kill()
        logger.info("КОМПАС(фон): процесс закрыт")
