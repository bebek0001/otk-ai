"""Работа с КОМПАС вынесена в отдельный процесс: сбой там не должен ронять приложение."""
import io
import json
import sys
import textwrap

from integrations.kompas import worker
from integrations.kompas.client import KompasWorker

FAKE = textwrap.dedent('''
    import json, sys, time
    for line in sys.stdin:
        r = json.loads(line)
        if r["cmd"] == "quit":
            print(json.dumps({"id": r["id"]}), flush=True); break
        if r["cmd"] == "body":
            p = r["path"]
            if p == "crash":
                import os; os._exit(3)            # как нарушение доступа в COM
            if p == "hang":
                time.sleep(60)
            print("посторонний вывод", flush=True)  # не JSON: клиент должен пропустить
            print(json.dumps({"id": r["id"], "dims": [120, 5, 230], "error": "", "attempts": []}), flush=True)
        else:
            print(json.dumps({"id": r["id"], "installed": True}), flush=True)
''')


def _client(tmp_path, **kw):
    f = tmp_path / "fake.py"
    f.write_text(FAKE, encoding="utf-8")
    return KompasWorker(argv=[sys.executable, str(f)], **kw)


def test_normal_flow_and_noise_lines(tmp_path):
    c = _client(tmp_path)
    assert c.detect()["installed"] is True
    assert c.body("x.m3d")["dims"] == [120, 5, 230]
    c.close()


def test_crash_is_contained_and_process_restarts(tmp_path):
    c = _client(tmp_path)
    assert "error" in c.body("crash")               # процесс упал: ошибка, не исключение
    assert c.body("x.m3d")["dims"] == [120, 5, 230]  # следующий запрос поднимает процесс заново
    c.close()


def test_hang_is_killed_by_timeout(tmp_path):
    c = _client(tmp_path, first_timeout=1.0, next_timeout=1.0)
    r = c.body("hang")
    assert "не ответил" in r["error"]
    c.close()


def test_serve_protocol_and_handler_errors():
    inp = io.StringIO('{"id":1,"cmd":"ok"}\n{"id":2,"cmd":"bad"}\nмусор\n{"id":3,"cmd":"nope"}\n{"id":4,"cmd":"quit"}\n')
    out = io.StringIO()
    closed = []

    def bad(_):
        raise RuntimeError("сбой COM")

    worker.serve(inp, out, {"ok": lambda r: {"v": 1}, "bad": bad, "quit": lambda r: closed.append(1) or {}})
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0] == {"v": 1, "id": 1}
    assert "RuntimeError" in lines[1]["error"] and lines[1]["id"] == 2
    assert lines[2]["id"] == 3 and "error" in lines[2]
    assert lines[3]["id"] == 4 and closed
