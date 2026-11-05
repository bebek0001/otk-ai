# -*- coding: utf-8 -*-
"""
integrations/kompas/worker.py — отдельный процесс, в котором живёт вся работа с КОМПАС.

ЗАЧЕМ. COM-объекты КОМПАС нельзя безопасно передавать между потоками, а сбой в
самом КОМПАС или в COM (нарушение доступа) убивает тот процесс, где он случился.
Если это окно приложения, пропадает всё: вылет программы. Поэтому пакетная
обработка запускает ЭТОТ процесс (`OTK_AI --kompas-worker`), общается с ним
строками JSON через stdin/stdout, а при зависании или падении просто
перезапускает: приложение продолжает работать, файл читается по содержимому.

Протокол: одна строка JSON на запрос {"id": N, "cmd": "detect" | "body" | "quit", ...},
одна строка JSON в ответ {"id": N, ...}. Всё лишнее идёт в stderr.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Callable, Dict, IO, Optional


def _handlers() -> Dict[str, Callable[[dict], dict]]:
    from integrations.kompas import batch as kb

    state: Dict[str, Optional[object]] = {"session": None}

    def detect(_req: dict) -> dict:
        return asdict(kb.detect())

    def body(req: dict) -> dict:
        ses = state["session"]
        if ses is None:
            ses = kb.KompasSession().__enter__()
            state["session"] = ses
        r = ses.body(req["path"])
        return {"dims": list(r.dims) if r.dims else None, "error": r.error,
                "attempts": r.attempts}

    def close(_req: dict) -> dict:
        ses = state["session"]
        if ses is not None:
            ses.__exit__(None, None, None)
            state["session"] = None
        return {}

    return {"detect": detect, "body": body, "quit": close}


def serve(inp: IO[str], out: IO[str], handlers: Dict[str, Callable[[dict], dict]]) -> None:
    """Главный цикл. Любое исключение в обработчике превращается в ответ с error."""
    try:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            rid, cmd = req.get("id"), req.get("cmd")
            try:
                resp = handlers[cmd](req) if cmd in handlers else {"error": f"команда {cmd}"}
            except Exception as e:                             # noqa: BLE001
                resp = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
            resp["id"] = rid
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
            if cmd == "quit":
                return
    finally:
        h = handlers.get("quit")
        if h:
            try:
                h({})
            except Exception:                                  # noqa: BLE001
                pass


def _std_streams():
    """stdin/stdout процесса. В оконной сборке (без консоли) Python может отдать
    None вместо потоков, тогда берём дескрипторы 0 и 1, которые выдал родитель."""
    inp = sys.stdin
    out = sys.stdout
    if inp is None:
        inp = open(0, "r", encoding="utf-8", closefd=False)
    elif hasattr(inp, "reconfigure"):
        inp.reconfigure(encoding="utf-8")
    if out is None:
        out = open(1, "w", encoding="utf-8", closefd=False)
    elif hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")
    return inp, out


def main() -> None:
    inp, out = _std_streams()
    sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w")
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:                                          # noqa: BLE001
        pass
    serve(inp, out, _handlers())
    os._exit(0)                                                # не ждать зависших COM-потоков


if __name__ == "__main__":
    main()
