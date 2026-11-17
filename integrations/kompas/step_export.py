# -*- coding: utf-8 -*-
"""
integrations/kompas/step_export.py — экспорт активного документа 3D из
уже запущенного КОМПАСа в STEP-файл, для живого 3D-просмотра во вкладке
«КОМПАС» (вьюпорт подтягивает модель прямо из открытого в КОМПАСе
документа, без ручного выбора файла).

ВАЖНО — честно о границах, в том же духе, что adapter.py и probe.py:
  Какой именно COM-метод экспортирует активный документ в STEP, отличается
  между API5/API7 и сборками КОМПАСа, а без реальной Windows-машины с
  установленным КОМПАСом это нельзя проверить напрямую. Поэтому здесь
  пробуются НЕСКОЛЬКО известных путей по очереди, и используется первый,
  который реально создал файл. Если ни один не сработал — в result.attempts
  полная история того, что пробовалось и что каждый вариант вернул, чтобы
  можно было прислать её для калибровки точного метода под вашу версию
  КОМПАСа — так же, как было сделано с чтением материала/массы.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from core import logger
from .connector import connect, get_active_document, is_available


@dataclass
class StepExportResult:
    ok: bool
    path: str = ""
    error: str = ""
    attempts: List[str] = field(default_factory=list)


def _file_ready(path: str) -> bool:
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception:                                      # noqa: BLE001
        return False


def _is_step(path: str) -> bool:
    """Файл действительно STEP (первая строка ISO-10303-21), а не родной формат КОМПАС."""
    try:
        with open(path, "rb") as f:
            return f.read(64).lstrip().upper().startswith(b"ISO-10303-21")
    except Exception:                                      # noqa: BLE001
        return False


def _head_text(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return repr(f.read(48))
    except Exception:                                      # noqa: BLE001
        return "?"


def api_names(obj, pattern: str, limit: int = 50) -> List[str]:
    """Имена методов и свойств COM-объекта по шаблону: нужны, чтобы подобрать вызов под версию."""
    import re
    names = set()
    try:
        names.update(dir(obj))
        for attr in ("_prop_map_get_", "_prop_map_put_", "_dispid_to_func_"):
            m = getattr(obj, attr, None)
            if isinstance(m, dict):
                names.update(str(k) for k in m)
    except Exception:                                      # noqa: BLE001
        pass
    pat = re.compile(pattern, re.I)
    return sorted(n for n in names if pat.search(str(n)) and not str(n).startswith("_"))[:limit]


def export_doc_to_step(app, doc, out_path: str):
    """Сохраняет ОТКРЫТЫЙ документ в STEP (для пакетной обработки). Пробует те же
    способы, что и export_active_to_step. Возвращает (успех, список попыток)."""
    attempts: List[str] = []
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except Exception:                                      # noqa: BLE001
        pass
    try:
        doc.SaveAs(out_path)
        if _is_step(out_path):
            attempts.append("doc.SaveAs(path) -> успех (STEP)")
            return True, attempts
        attempts.append("doc.SaveAs(path) -> файл не STEP: "
                        + (_head_text(out_path) if _file_ready(out_path) else "файл не появился"))
        try:
            os.remove(out_path)
        except Exception:                                  # noqa: BLE001
            pass
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"doc.SaveAs(path) -> ошибка: {str(e)[:100]}")
    try:
        converter = getattr(app, "Converter3D", None)
        if converter is not None:
            converter.Export3DToSTEP(doc, out_path)
            if _is_step(out_path):
                attempts.append("app.Converter3D.Export3DToSTEP -> успех")
                return True, attempts
            attempts.append("app.Converter3D.Export3DToSTEP -> файл не STEP")
        else:
            attempts.append("app.Converter3D -> нет такого свойства")
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"app.Converter3D.Export3DToSTEP -> ошибка: {str(e)[:100]}")
    try:
        from win32com.client import CastTo
        from .adapter import _typed_module_from_running_app
        _typed_module_from_running_app(app)
        doc3d = CastTo(doc, "IKompasDocument3D")
        method = getattr(doc3d, "ksSaveToSTEP", None) or getattr(doc3d, "SaveToSTEP", None)
        if method is not None:
            method(out_path)
            if _is_step(out_path):
                attempts.append("IKompasDocument3D.(ks)SaveToSTEP -> успех")
                return True, attempts
            attempts.append("IKompasDocument3D.(ks)SaveToSTEP -> файл не STEP")
        else:
            attempts.append("IKompasDocument3D -> нет метода (ks)SaveToSTEP")
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"IKompasDocument3D.(ks)SaveToSTEP -> ошибка: {str(e)[:100]}")
    pat = r"step|stp|export|convert|save|format|iges|acis"
    for label, obj in (("app", app), ("doc", doc)):
        attempts.append(f"имена {label}: " + ", ".join(api_names(obj, pat)))
    try:
        from win32com.client import CastTo
        from .adapter import _typed_module_from_running_app
        _typed_module_from_running_app(app)
        attempts.append("имена doc3d: " + ", ".join(api_names(CastTo(doc, "IKompasDocument3D"), pat)))
        conv = app.Converter
        attempts.append("тип app.Converter: " + type(conv).__name__)
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"имена doc3d -> ошибка: {str(e)[:90]}")
    return False, attempts


def export_active_to_step(out_path: str) -> StepExportResult:
    """Экспортирует активный 3D-документ уже запущенного КОМПАСа в STEP."""
    if not is_available():
        return StepExportResult(False, error="Доступно только в Windows-сборке")

    status = connect()
    if not status.connected:
        return StepExportResult(False, error=status.error)

    app = status.app
    doc = get_active_document(app)
    if doc is None:
        return StepExportResult(
            False,
            error="Нет активного документа. Откройте деталь/сборку в КОМПАСе и повторите.",
        )

    attempts: List[str] = []

    # Убираем файл заранее — чтобы случайно не принять старый файл за
    # результат нового экспорта, если какой-то из вариантов "тихо" не сработает.
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except Exception:                                      # noqa: BLE001
        pass

    # Вариант 1: SaveAs с расширением .stp/.step — многие COM-интерфейсы
    # КОМПАС определяют формат экспорта по расширению целевого файла.
    try:
        doc.SaveAs(out_path)
        if _file_ready(out_path):
            attempts.append("doc.SaveAs(path) -> успех")
            return StepExportResult(True, path=out_path, attempts=attempts)
        attempts.append("doc.SaveAs(path) -> файл не появился")
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"doc.SaveAs(path) -> ошибка: {e}")

    # Вариант 2: конвертер 3D через IApplication.Converter3D (если такое
    # свойство у этой версии приложения есть).
    try:
        converter = getattr(app, "Converter3D", None)
        if converter is not None:
            converter.Export3DToSTEP(doc, out_path)
            if _file_ready(out_path):
                attempts.append("app.Converter3D.Export3DToSTEP(doc, path) -> успех")
                return StepExportResult(True, path=out_path, attempts=attempts)
            attempts.append("app.Converter3D.Export3DToSTEP(doc, path) -> файл не появился")
        else:
            attempts.append("app.Converter3D -> недоступно (нет такого свойства)")
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"app.Converter3D.Export3DToSTEP(doc, path) -> ошибка: {e}")

    # Вариант 3: типизированный IKompasDocument3D с методом (ks)SaveToSTEP.
    try:
        from win32com.client import CastTo
        from .adapter import _typed_module_from_running_app
        _typed_module_from_running_app(app)
        doc3d = CastTo(doc, "IKompasDocument3D")
        method = getattr(doc3d, "ksSaveToSTEP", None) or getattr(doc3d, "SaveToSTEP", None)
        if method is not None:
            method(out_path)
            if _file_ready(out_path):
                attempts.append("IKompasDocument3D.(ks)SaveToSTEP(path) -> успех")
                return StepExportResult(True, path=out_path, attempts=attempts)
            attempts.append("IKompasDocument3D.(ks)SaveToSTEP(path) -> файл не появился")
        else:
            attempts.append("IKompasDocument3D -> нет метода (ks)SaveToSTEP")
    except Exception as e:                                 # noqa: BLE001
        attempts.append(f"IKompasDocument3D.(ks)SaveToSTEP(path) -> ошибка: {e}")

    logger.error(
        "КОМПАС(live): экспорт в STEP не удался ни одним известным способом:\n"
        + "\n".join(attempts)
    )
    return StepExportResult(
        False,
        error=(
            "Не удалось подтянуть модель из КОМПАСа ни одним из известных способов "
            "экспорта в STEP. Технические детали — в панели разработчика. Нужно будет "
            "откалибровать точный метод под вашу версию КОМПАСа (как и с чтением "
            "материала/массы) — пришлите эти детали."
        ),
        attempts=attempts,
    )
