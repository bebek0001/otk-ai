# -*- coding: utf-8 -*-
"""
integrations/kompas/probe.py — диагностика живого подключения к КОМПАС-3D.

Нужна, чтобы узнать РЕАЛЬНЫЕ имена свойств активного документа на вашей
версии КОМПАСа — без этого адаптер для чтения модели писать нельзя,
можно только гадать.

Есть два способа запустить:

1) Прямо из приложения — вкладка «КОМПАС» → кнопка «Считать диагностику».
   Отчёт появится там же, в текстовом поле. Это самый простой способ.

2) Из терминала (на Windows, КОМПАС уже открыт, в нём открыта деталь/сборка):

    cd "путь\к\проекту"
    python -m integrations.kompas.probe > kompas_probe_report.txt

Отчёт пришлите обратно — по нему будет написан точный адаптер под вашу
версию КОМПАСа.

Скрипт только ЧИТАЕТ — ничего не меняет и не сохраняет в самом КОМПАСе.
"""

from __future__ import annotations

import io
import sys

from .connector import connect, get_active_document, is_available


def _dump_attrs(label: str, obj, out: io.StringIO) -> None:
    print(f"\n--- {label} ---", file=out)
    if obj is None:
        print("(нет объекта)", file=out)
        return
    try:
        names = sorted(n for n in dir(obj) if not n.startswith("_"))
    except Exception as e:                                 # noqa: BLE001
        print(f"dir() не сработал: {e}", file=out)
        return
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception as e:                             # noqa: BLE001
            print(f"  {name} = <ошибка чтения: {e}>", file=out)
            continue
        if callable(value):
            print(f"  {name}(...)   [метод]", file=out)
        else:
            text = str(value)
            if len(text) > 120:
                text = text[:120] + "…"
            print(f"  {name} = {text}", file=out)


def generate_report() -> str:
    """
    Строит диагностический отчёт строкой. Не бросает исключений наружу —
    любая ошибка попадает в сам текст отчёта, чтобы вкладка в приложении
    могла просто показать результат, не падая.
    """
    out = io.StringIO()
    print("=== OTK AI: диагностика подключения к КОМПАС-3D ===", file=out)

    if not is_available():
        print("Эта диагностика работает только в Windows-сборке (нужен pywin32).", file=out)
        return out.getvalue()

    try:
        status = connect()
    except Exception as e:                                 # noqa: BLE001
        print(f"Непредвиденная ошибка подключения: {e}", file=out)
        return out.getvalue()

    print(f"Подключено: {status.connected}", file=out)
    print(
        f"API: {status.api}   ProgID: {status.prog_id}   "
        f"Версия КОМПАС: {status.kompas_version}",
        file=out,
    )

    if not status.connected:
        print(f"Ошибка подключения: {status.error}", file=out)
        return out.getvalue()

    app = status.app
    _dump_attrs("Приложение (app)", app, out)

    try:
        doc = get_active_document(app)
    except Exception as e:                                 # noqa: BLE001
        print(f"\nНе удалось получить активный документ: {e}", file=out)
        return out.getvalue()

    if doc is None:
        print(
            "\nНе удалось получить активный документ.\n"
            "Убедитесь, что в КОМПАСе открыт чертёж, деталь или сборка, "
            "и запустите диагностику ещё раз.",
            file=out,
        )
        return out.getvalue()

    _dump_attrs("Активный документ (doc)", doc, out)

    # Пробуем распространённые пути к геометрии/материалу/массе —
    # какие из них реально сработают, покажет только вывод на вашей версии.
    for attr in ("TopPart", "Part", "MassInertiaParams", "DocumentType"):
        try:
            value = getattr(doc, attr)
        except Exception as e:                             # noqa: BLE001
            print(f"\ndoc.{attr} -> ошибка: {e}", file=out)
            continue
        if callable(value):
            print(f"\ndoc.{attr} — метод (не свойство), пропускаю", file=out)
            continue
        _dump_attrs(f"doc.{attr}", value, out)

    print("\n=== Конец отчёта ===", file=out)
    return out.getvalue()


def main() -> int:
    """Запуск из командной строки: python -m integrations.kompas.probe"""
    report = generate_report()
    print(report)
    return 0 if "Подключено: True" in report else 1


if __name__ == "__main__":
    sys.exit(main())
