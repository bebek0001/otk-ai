# -*- coding: utf-8 -*-
"""
integrations/kompas/batch.py — фоновая пакетная обработка моделей через КОМПАС-3D.

Свой экземпляр КОМПАС запускается скрытым (без окна), каждая модель открывается
только для чтения, у детали берётся габарит тела, затем документ закрывается.
Окно КОМПАС пользователю не показывается, работающий у него КОМПАС не
затрагивается (отдельный процесс).

ЧЕСТНО О ГРАНИЦАХ. Имена методов габарита отличаются между версиями API, а без
Windows с КОМПАС их нельзя проверить заранее, поэтому для габарита пробуется
несколько известных вариантов вызова, а в BodyResult.attempts остаётся история
того, что пробовалось. Если ни один не сработал, вызывающий код читает модель
по содержимому файла (core.m3d_stock) — результат не пропадает.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from core import logger

PROG_IDS = [
    ("API7", "KOMPAS.Application.7"),
    ("API5", "KOMPAS.Application.5"),
]


@dataclass
class BodyResult:
    dims: Optional[Tuple[float, float, float]] = None     # габарит тела, мм
    error: str = ""
    attempts: List[str] = field(default_factory=list)


@dataclass
class KompasInfo:
    installed: bool
    prog_id: str = ""
    api: str = ""
    running: bool = False
    note: str = ""


def is_available() -> bool:
    return sys.platform == "win32"


def detect() -> KompasInfo:
    """Есть ли КОМПАС на этом компьютере (зарегистрирован в системе) и запущен ли он."""
    if not is_available():
        return KompasInfo(False, note="КОМПАС доступен только на Windows")
    try:
        import pythoncom
        import win32com.client as wc
    except ImportError:
        return KompasInfo(False, note="Не установлен pywin32")
    try:
        pythoncom.CoInitialize()
    except Exception:                                          # noqa: BLE001
        pass
    for api, prog in PROG_IDS:
        try:
            pythoncom.CLSIDFromProgID(prog)
        except Exception:                                      # noqa: BLE001
            continue
        running = False
        try:
            wc.GetActiveObject(prog)
            running = True
        except Exception:                                      # noqa: BLE001
            pass
        return KompasInfo(True, prog, api, running)
    return KompasInfo(False, note="КОМПАС-3D не найден на этом компьютере")


# --------------------------------------------------------------------------- #
#  Разбор ответа КОМПАС
# --------------------------------------------------------------------------- #

def _numbers(value) -> List[float]:
    """Все числа из ответа COM (кортеж, вложенный кортеж, bool не считается)."""
    out: List[float] = []
    if isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_numbers(v))
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        out.append(float(value))
    return out


def dims_from_gabarit(value) -> Optional[Tuple[float, float, float]]:
    """Ответ вида (x1,y1,z1,x2,y2,z2) возможно с флагом впереди -> три размера."""
    nums = _numbers(value)
    if len(nums) < 6:
        return None
    x1, y1, z1, x2, y2, z2 = nums[-6:]
    d = (abs(x2 - x1), abs(y2 - y1), abs(z2 - z1))
    if min(d) <= 0 or max(d) > 1e6:
        return None
    return tuple(round(v, 4) for v in d)                       # type: ignore[return-value]


_GABARIT_CALLS: List[Tuple[str, Callable]] = [
    ("GetGabarit(True)", lambda p: p.GetGabarit(True)),
    ("GetGabarit(False)", lambda p: p.GetGabarit(False)),
    ("GetGabarit(True, 0 x6)", lambda p: p.GetGabarit(True, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
    ("GetGabarit()", lambda p: p.GetGabarit()),
]


def read_body_dims(part, attempts: Optional[List[str]] = None) -> Optional[Tuple[float, float, float]]:
    """Габарит детали. Пробует известные варианты вызова, пока один не вернёт размеры."""
    attempts = attempts if attempts is not None else []
    for name, call in _GABARIT_CALLS:
        try:
            res = call(part)
        except Exception as e:                                 # noqa: BLE001
            attempts.append(f"{name} -> ошибка: {str(e)[:80]}")
            continue
        dims = dims_from_gabarit(res)
        if dims:
            attempts.append(f"{name} -> успех")
            return dims
        attempts.append(f"{name} -> нет размеров в ответе")
    return None


def _similar_names(obj, limit: int = 40) -> List[str]:
    """Имена методов объекта, похожие на «габарит»: подсказка для настройки вызова."""
    import re
    names = set()
    try:
        names.update(dir(obj))
        for attr in ("_prop_map_get_", "_prop_map_put_", "_dispid_to_func_"):
            m = getattr(obj, attr, None)
            if isinstance(m, dict):
                names.update(str(k) for k in m)
    except Exception:                                          # noqa: BLE001
        pass
    pat = re.compile(r"gabar|box|bound|size|dimension|extent", re.I)
    return sorted(n for n in names if pat.search(str(n)))[:limit]


def selftest(path: str, report_path: str) -> str:
    """Проверка на рабочем компьютере: открыть одну модель в скрытом КОМПАС и записать
    подробный отчёт. Этот отчёт нужен, чтобы подстроить вызовы под вашу версию КОМПАС."""
    lines = [f"Файл: {path}", f"Платформа: {sys.platform}"]
    info = detect()
    lines.append(f"КОМПАС: установлен={info.installed}, API={info.api}, запущен={info.running}, {info.note}")
    if info.installed:
        with KompasSession() as ks:
            if not ks.ok:
                lines.append(f"Запуск не удался: {ks.error}")
            else:
                r = ks.body(path)
                lines.append(f"Габарит тела: {r.dims}")
                lines.append(f"Ошибка: {r.error}")
                lines.extend(f"  {a}" for a in r.attempts)
    text = "\n".join(lines)
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    return text


def measure(app, path: str) -> BodyResult:
    """Открывает модель в КОМПАС (только чтение, скрыто) и читает габарит тела."""
    res = BodyResult()
    doc = None
    try:
        docs = app.Documents
        try:
            doc = docs.Open(path, False, True)
        except Exception as e:                                 # noqa: BLE001
            res.attempts.append(f"Documents.Open(path, False, True) -> ошибка: {str(e)[:80]}")
            doc = docs.Open(path)
        if doc is None:
            res.error = "КОМПАС не открыл модель"
            return res
        top = None
        try:
            from win32com.client import CastTo
            from .adapter import _typed_module_from_running_app
            _typed_module_from_running_app(app)
            top = CastTo(doc, "IKompasDocument3D").TopPart
        except Exception as e:                                 # noqa: BLE001
            res.attempts.append(f"TopPart через IKompasDocument3D -> ошибка: {str(e)[:80]}")
            try:
                top = doc.TopPart
            except Exception as e2:                            # noqa: BLE001
                res.attempts.append(f"doc.TopPart -> ошибка: {str(e2)[:80]}")
        if top is None:
            res.error = "Не удалось получить деталь из документа"
            return res
        res.dims = read_body_dims(top, res.attempts)
        if res.dims is None:
            res.error = "КОМПАС не отдал габарит тела"
            res.attempts.append("Похожие имена у объекта детали: " + ", ".join(_similar_names(top)))
    except Exception as e:                                     # noqa: BLE001
        res.error = f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        if doc is not None:
            try:
                doc.Close(0)                                   # без сохранения
            except Exception:                                  # noqa: BLE001
                pass
    return res


class KompasSession:
    """Скрытый экземпляр КОМПАС на время пакета."""

    def __init__(self) -> None:
        self.app = None
        self.error = ""

    def __enter__(self) -> "KompasSession":
        info = detect()
        if not info.installed:
            self.error = info.note or "КОМПАС не найден"
            return self
        try:
            import win32com.client as wc
            try:
                self.app = wc.DispatchEx(info.prog_id)          # отдельный процесс
            except Exception:                                  # noqa: BLE001
                self.app = wc.Dispatch(info.prog_id)
            for attr, val in (("Visible", False), ("HideMessage", 1)):
                try:
                    setattr(self.app, attr, val)
                except Exception:                              # noqa: BLE001
                    pass
        except Exception as e:                                 # noqa: BLE001
            self.app = None
            self.error = f"КОМПАС не запустился: {str(e)[:100]}"
        return self

    def __exit__(self, *exc) -> None:
        if self.app is not None:
            try:
                self.app.Quit()
            except Exception:                                  # noqa: BLE001
                pass
            self.app = None

    @property
    def ok(self) -> bool:
        return self.app is not None

    def body(self, path: str) -> BodyResult:
        if self.app is None:
            return BodyResult(error=self.error or "КОМПАС недоступен")
        return measure(self.app, os.path.abspath(path))


def log_result(path: str, r: BodyResult) -> None:
    name = os.path.basename(path)
    if r.dims:
        logger.info(f"КОМПАС(фон): {name}: габарит {r.dims}")
    else:
        logger.warn(f"КОМПАС(фон): {name}: {r.error}; попытки: {'; '.join(r.attempts)}")
