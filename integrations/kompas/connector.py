# -*- coding: utf-8 -*-
"""
integrations/kompas/connector.py — подключение к уже запущенному КОМПАС-3D.

ВАЖНО: это подключение к работающему приложению (GetActiveObject), а не
запуск нового экземпляра. КОМПАС должен быть открыт пользователем заранее —
приложение "подсматривает" за уже работающей программой, как и просил
заказчик (открыл модель в КОМПАСе — OTK AI её увидел и подключился).

Поддерживаются два семейства API КОМПАС-3D:
    API7 — v18 и новее, в т.ч. v25 (ProgID "KOMPAS.Application.7")
    API5 — более старый объектный API, актуален для v22 (ProgID
           "KOMPAS.Application.5")

Какие именно свойства и методы доступны у активного документа — отличается
между API5/API7 и даже между сборками одной версии. Эти детали нельзя
проверить без реального Windows-компьютера с установленным КОМПАСом,
поэтому здесь есть:
    connect()  — подключение и определение того, что реально ответило;
    probe.py   — отдельный диагностический скрипт (см. рядом), который
                 печатает всё, что отдаёт COM-объект, чтобы откалибровать
                 полноценный адаптер по факту, а не по документации вслепую.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# От новых версий к старым: подключение перебирает по очереди.
PROG_IDS = [
    ("API7", "KOMPAS.Application.7"),
    ("API5", "KOMPAS.Application.5"),
]


@dataclass
class ConnectionStatus:
    connected: bool
    api: str = ""              # "API7" / "API5" / ""
    prog_id: str = ""
    kompas_version: str = ""
    error: str = ""
    app: Any = None             # COM-объект приложения, если подключились


def is_available() -> bool:
    """На Mac/Linux pywin32 нет и быть не может — подключение просто недоступно."""
    return sys.platform == "win32"


def connect() -> ConnectionStatus:
    """Пытается подключиться к уже запущенному КОМПАСу (любой известный API)."""
    if not is_available():
        return ConnectionStatus(
            False, error="Подключение к КОМПАС доступно только в Windows-сборке"
        )

    try:
        import pythoncom
        import win32com.client as wc
    except ImportError:
        return ConnectionStatus(
            False, error="Не установлен pywin32 (pip install pywin32)"
        )

    pythoncom.CoInitialize()

    last_error = ""
    for api_name, prog_id in PROG_IDS:
        try:
            app = wc.GetActiveObject(prog_id)
        except Exception as e:                            # noqa: BLE001
            last_error = f"{prog_id}: {e}"
            continue
        version = _safe_version(app)
        return ConnectionStatus(
            True, api=api_name, prog_id=prog_id, kompas_version=version, app=app
        )

    return ConnectionStatus(
        False,
        error=(
            "КОМПАС не найден. Убедитесь, что он запущен "
            f"(последняя ошибка: {last_error})"
        ),
    )


def _safe_version(app) -> str:
    # Имя свойства версии отличается между API5/API7 — пробуем известные
    # варианты и не падаем, если ни один не подошёл.
    for attr in ("ApplicationVersion", "Version", "KompasApplicationVersion"):
        try:
            v = getattr(app, attr)
            if v:
                return str(v)
        except Exception:                                 # noqa: BLE001
            continue
    return "неизвестна"


def get_active_document(app):
    """
    Возвращает активный документ из приложения, перебирая известные
    названия свойства (отличаются между API5/API7).
    Возвращает None, если ничего не открыто или ни одно имя не подошло.
    """
    for attr in ("ActiveDocument", "ActiveDocument3D", "Document"):
        try:
            doc = getattr(app, attr)
            if doc is not None:
                return doc
        except Exception:                                 # noqa: BLE001
            continue
    return None
