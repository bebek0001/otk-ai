# -*- coding: utf-8 -*-
"""Проверка цепочки КОМПАС без интерфейса.

Запуск из папки проекта:
    python tools\\kompas_check.py "C:\\путь\\к\\модели.m3d"
Результат печатается и пишется в kompas_check.txt (рядом с проектом).
Шаги: 1) прямой вызов в этом процессе, 2) подробный отчёт selftest,
3) тот же путь, что использует вкладка: отдельный процесс КОМПАС.
"""
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = []


def say(s):
    s = str(s)
    print(s, flush=True)
    OUT.append(s)


def step(title, fn):
    say(f"\n=== {title} ===")
    t = time.time()
    try:
        fn()
    except Exception:                                          # noqa: BLE001
        say(traceback.format_exc())
    say(f"({time.time() - t:.1f} с)")


extra = sys.argv[2:]
path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    try:
        path = input("Перетащите сюда файл M3D (или вставьте путь) и нажмите Enter: ")
    except EOFError:
        path = ""
path = path.strip()
if path.startswith("&"):
    path = path[1:].strip()
path = path.strip("\"'").strip()
if os.path.isdir(path):                                        # дали папку: берём первый M3D
    found = sorted(f for f in os.listdir(path) if f.lower().endswith(".m3d"))
    path = os.path.join(path, found[0]) if found else ""
say(f"Python: {sys.version.split()[0]}, платформа: {sys.platform}, файл: {path or '(не задан)'}")


def direct_detect():
    from integrations.kompas import batch as kb
    say(kb.detect())


def selftest():
    from integrations.kompas import batch as kb
    if not path:
        say("файл не задан, пропуск")
        return
    for p in [path] + extra:
        say(kb.selftest(p, os.path.join(ROOT, "kompas_selftest.txt")))
        say("")


def via_worker():
    from integrations.kompas.client import KompasWorker
    k = KompasWorker()
    try:
        say("detect: " + json.dumps(k.detect(), ensure_ascii=False))
        for p in ([path] + extra) if path else []:
            say("body: " + json.dumps(k.body(p), ensure_ascii=False))
    finally:
        k.close()


def registry():
    import winreg
    pats = ("kompas", "ascon", "kompas3d")
    found = []
    try:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(winreg.HKEY_CLASSES_ROOT, i)
            except OSError:
                break
            i += 1
            if any(p in name.lower() for p in pats):
                found.append(name)
    except Exception as e:                                     # noqa: BLE001
        say(f"перебор HKCR: {e}")
    say("HKCR, ключи с KOMPAS/ASCON: " + (", ".join(found[:40]) or "нет"))
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ASCON"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ASCON"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\ASCON")):
        try:
            with winreg.OpenKey(root, sub) as k:
                names = []
                j = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(k, j))
                    except OSError:
                        break
                    j += 1
                say(f"{sub}: {names}")
        except OSError:
            say(f"{sub}: нет")
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramW6432", "")):
        a = os.path.join(base, "ASCON") if base else ""
        if a and os.path.isdir(a):
            for d in os.listdir(a):
                bin_ = os.path.join(a, d, "Bin")
                exes = [f for f in os.listdir(bin_) if f.lower().startswith(("kompas", "kstart"))][:6] \
                    if os.path.isdir(bin_) else []
                say(f"{os.path.join(a, d)}: {exes}")


def running():
    import pythoncom
    import win32com.client as wc
    pythoncom.CoInitialize()
    for prog in ("KOMPAS.Application.7", "KOMPAS.Application.5", "Kompas.Application.7"):
        try:
            app = wc.GetActiveObject(prog)
            say(f"GetActiveObject({prog}) -> работает, версия: "
                f"{getattr(app, 'ApplicationVersion', None)}")
        except Exception as e:                                 # noqa: BLE001
            say(f"GetActiveObject({prog}) -> {str(e)[:90]}")


step("0a. реестр и папки КОМПАС", registry)
step("0b. запущен ли КОМПАС сейчас", running)
step("1. detect в этом процессе", direct_detect)
step("2. selftest (КОМПАС скрыто, этот процесс)", selftest)
step("3. через отдельный процесс (как во вкладке)", via_worker)
with open(os.path.join(ROOT, "kompas_check.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
say("\nГотово: kompas_check.txt")
