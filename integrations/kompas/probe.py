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

    # Только разведка (ничего не открываем и не закрываем!) — коллекция
    # Documents нужна для будущей пакетной обработки .m3d через живой
    # КОМПАС (открыть файл В ФОНЕ, не трогая то, что уже видно пользователю).
    # Вызывать Open/Close вслепую, не зная точной сигнатуры, рискованно —
    # поэтому здесь только dir() и Count, без единого реального вызова.
    try:
        docs = getattr(app, "Documents", None)
    except Exception as e:                                 # noqa: BLE001
        print(f"\napp.Documents -> ошибка: {e}", file=out)
    else:
        if docs is not None:
            _dump_attrs("app.Documents (коллекция, для будущей пакетной обработки)", docs, out)
            try:
                print(f"  Count = {docs.Count}", file=out)
            except Exception as e:                         # noqa: BLE001
                print(f"  Count -> ошибка: {e}", file=out)

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

    _probe_typed_3d_api(app, doc, out)

    print("\n=== Конец отчёта ===", file=out)
    return out.getvalue()


def _probe_typed_3d_api(app, doc, out: io.StringIO) -> None:
    """
    Пробует ОФИЦИАЛЬНЫЙ путь к геометрии/материалу по документации АСКОН:
    документ приводится (QueryInterface) к типизированному интерфейсу
    IKompasDocument3D, у него берётся TopPart (IPart7), а на нём уже есть
    простые свойства Material/Mass; для Volume/Density/Area нужен ещё один
    кастинг — к IMassInertiaParam7.

    Это не догадка "в лоб" по именам (как выше), а конкретный документированный
    путь — https://help.ascon.ru/KOMPAS_SDK/22/ru-RU/ipart7_props.html и
    https://help.ascon.ru/KOMPAS_SDK/22/ru-RU/imassinertiaparam7_props.html.
    Каждый шаг обёрнут отдельно, чтобы увидеть, где именно он ломается —
    без реального запуска на вашей версии КОМПАСа это нельзя проверить иначе.
    """
    print("\n--- Попытка типизированного доступа (IKompasDocument3D → TopPart) ---", file=out)
    try:
        from win32com.client import gencache, CastTo
    except Exception as e:                                 # noqa: BLE001
        print(f"Не удалось импортировать gencache/CastTo: {e}", file=out)
        return

    # Генерируем typed-обёртку из типобиблиотеки САМОГО уже запущенного
    # приложения (без запуска нового экземпляра и без угадывания GUID).
    try:
        type_info = app._oleobj_.GetTypeInfo()
        type_lib, _ = type_info.GetContainingTypeLib()
        lib_attr = type_lib.GetLibAttr()
        gencache.EnsureModule(lib_attr[0], lib_attr[1], lib_attr[3], lib_attr[4])
        print("Типобиблиотека КОМПАСа успешно считана и обёрнута (gencache).", file=out)
    except Exception as e:                                 # noqa: BLE001
        print(f"Не удалось получить типобиблиотеку приложения: {e}", file=out)
        return

    try:
        doc3d = CastTo(doc, "IKompasDocument3D")
    except Exception as e:                                 # noqa: BLE001
        print(f"CastTo(doc, 'IKompasDocument3D') -> ошибка: {e}", file=out)
        return
    print("doc → IKompasDocument3D: успешно", file=out)

    try:
        top_part = doc3d.TopPart
    except Exception as e:                                 # noqa: BLE001
        print(f"doc3d.TopPart -> ошибка: {e}", file=out)
        return
    if top_part is None:
        print("doc3d.TopPart вернул пусто (None)", file=out)
        return
    print("doc3d.TopPart: получен объект", file=out)

    for attr in ("Material", "Mass", "Density", "Marking"):
        try:
            value = getattr(top_part, attr)
            print(f"  TopPart.{attr} = {value}", file=out)
        except Exception as e:                             # noqa: BLE001
            print(f"  TopPart.{attr} -> ошибка: {e}", file=out)

    try:
        mip = CastTo(top_part, "IMassInertiaParam7")
    except Exception as e:                                 # noqa: BLE001
        print(f"CastTo(TopPart, 'IMassInertiaParam7') -> ошибка: {e}", file=out)
        mip = None
    else:
        print("TopPart → IMassInertiaParam7: успешно", file=out)
        for attr in ("Mass", "Volume", "Area", "Density", "Material", "Xc", "Yc", "Zc"):
            try:
                value = getattr(mip, attr)
                print(f"  MassInertiaParam.{attr} = {value}", file=out)
            except Exception as e:                         # noqa: BLE001
                print(f"  MassInertiaParam.{attr} -> ошибка: {e}", file=out)

    # Официальная документация АСКОН не даёт прямого свойства "габарит
    # детали" (bounding box) — значит, угадывать имя вслепую бессмысленно.
    # Вместо этого печатаем ВСЕ свойства TopPart и IMassInertiaParam7 через
    # dir() — так реальные имена (Shapes/Bodies/GabaritObj/что угодно ещё)
    # будут видны прямо в отчёте, без повторного похода к компьютеру.
    _dump_attrs("TopPart — ВСЕ свойства (dir())", top_part, out)
    if mip is not None:
        _dump_attrs("IMassInertiaParam7 — ВСЕ свойства (dir())", mip, out)

    # Если среди свойств TopPart найдётся контейнер тел/геометрии (по
    # распространённым названиям), заглянем на один уровень внутрь —
    # это и есть кандидат на габаритные размеры детали.
    for container_attr in ("Shapes", "Bodies", "Solids", "Model", "Models", "MassInertiaParams"):
        try:
            container = getattr(top_part, container_attr)
        except Exception:                                  # noqa: BLE001
            continue
        if container is None:
            continue
        _dump_attrs(f"TopPart.{container_attr}", container, out)
        try:
            count = len(container)
        except Exception:                                  # noqa: BLE001
            count = 0
        if count:
            try:
                first = container[0]
            except Exception as e:                         # noqa: BLE001
                print(f"  {container_attr}[0] -> ошибка: {e}", file=out)
            else:
                _dump_attrs(f"TopPart.{container_attr}[0]", first, out)


def main() -> int:
    """Запуск из командной строки: python -m integrations.kompas.probe"""
    report = generate_report()
    print(report)
    return 0 if "Подключено: True" in report else 1


if __name__ == "__main__":
    sys.exit(main())
