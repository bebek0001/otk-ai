# -*- coding: utf-8 -*-
"""
integrations/kompas/probe.py — ОДНОРАЗОВАЯ диагностика живого подключения
к КОМПАС-3D. Нужна, чтобы узнать РЕАЛЬНЫЕ имена свойств активного документа
на вашей версии КОМПАСа — без этого адаптер для чтения модели писать нельзя,
можно только гадать.

Как запустить (на Windows, КОМПАС уже открыт, в нём открыта деталь/сборка):

    cd "путь\к\проекту"
    python -m integrations.kompas.probe > kompas_probe_report.txt

Файл kompas_probe_report.txt пришлите обратно — по нему будет написан
точный адаптер под вашу версию КОМПАСа.

Скрипт только ЧИТАЕТ — ничего не меняет и не сохраняет в самом КОМПАСе.
"""

from __future__ import annotations

import sys

from .connector import connect, get_active_document, is_available


def _dump_attrs(label: str, obj) -> None:
    print(f"\n--- {label} ---")
    if obj is None:
        print("(нет объекта)")
        return
    try:
        names = sorted(n for n in dir(obj) if not n.startswith("_"))
    except Exception as e:                                 # noqa: BLE001
        print(f"dir() не сработал: {e}")
        return
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception as e:                             # noqa: BLE001
            print(f"  {name} = <ошибка чтения: {e}>")
            continue
        if callable(value):
            print(f"  {name}(...)   [метод]")
        else:
            text = str(value)
            if len(text) > 120:
                text = text[:120] + "…"
            print(f"  {name} = {text}")


def main() -> int:
    print("=== OTK AI: диагностика подключения к КОМПАС-3D ===")

    if not is_available():
        print("Эта диагностика работает только в Windows-сборке (нужен pywin32).")
        return 1

    status = connect()
    print(f"Подключено: {status.connected}")
    print(f"API: {status.api}   ProgID: {status.prog_id}   "
          f"Версия КОМПАС: {status.kompas_version}")

    if not status.connected:
        print(f"Ошибка подключения: {status.error}")
        return 1

    app = status.app
    _dump_attrs("Приложение (app)", app)

    doc = get_active_document(app)
    if doc is None:
        print(
            "\nНе удалось получить активный документ.\n"
            "Убедитесь, что в КОМПАСе открыт чертёж, деталь или сборка, "
            "и запустите ещё раз."
        )
        return 1

    _dump_attrs("Активный документ (doc)", doc)

    # Пробуем распространённые пути к геометрии/материалу/массе —
    # какие из них реально сработают, покажет только вывод на вашей версии.
    for attr in ("TopPart", "Part", "MassInertiaParams", "DocumentType"):
        try:
            value = getattr(doc, attr)
        except Exception as e:                             # noqa: BLE001
            print(f"\ndoc.{attr} -> ошибка: {e}")
            continue
        if callable(value):
            print(f"\ndoc.{attr} — метод (не свойство), пропускаю")
            continue
        _dump_attrs(f"doc.{attr}", value)

    print("\n=== Конец отчёта ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
