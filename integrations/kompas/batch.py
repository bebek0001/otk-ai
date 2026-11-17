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
    exact: bool = False          # габарит только по телам (STEP); GetGabarit захватывает и плоскости
    # исполнения модели: [{"index", "marking", "mass_g", "dims", "exact"}], по одному на каждое
    embs: List[dict] = field(default_factory=list)


@dataclass
class KompasInfo:
    installed: bool
    prog_id: str = ""
    api: str = ""
    running: bool = False
    note: str = ""


def is_available() -> bool:
    return sys.platform == "win32"


def _resolve_progid(prog: str) -> Tuple[str, str]:
    """(CLSID, '') если ProgID зарегистрирован, иначе ('', причина).
    Сначала так же, как это делает GetActiveObject (pywintypes.IID), затем по реестру."""
    err = ""
    try:
        import pywintypes
        return str(pywintypes.IID(prog)), ""
    except Exception as e:                                     # noqa: BLE001
        err = f"IID: {str(e)[:70]}"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + "\\CLSID") as k:
            return str(winreg.QueryValueEx(k, "")[0]), ""
    except Exception as e:                                     # noqa: BLE001
        return "", f"{err}; реестр: {str(e)[:70]}"


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
    reasons: List[str] = []
    for api, prog in PROG_IDS:
        clsid, why = _resolve_progid(prog)
        if not clsid:
            reasons.append(f"{prog}: {why}")
            continue
        running = False
        try:
            wc.GetActiveObject(prog)
            running = True
        except Exception:                                      # noqa: BLE001
            pass
        return KompasInfo(True, prog, api, running)
    return KompasInfo(False, note="КОМПАС-3D не найден на этом компьютере"
                      + (" (" + "; ".join(reasons) + ")" if reasons else ""))


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


# Проверено на КОМПАС-3D v25 Home: первый параметр GetGabarit выбирает, что считать.
#   False -> только тела детали (точный габарит: Палец 94x36x36, Платик 80x8x80);
#   True  -> вместе со вспомогательной геометрией (плоскости, оси), лишние до 50 мм: не годится.
_GABARIT_CALLS: List[Tuple[str, Callable, bool]] = [
    ("GetGabarit(False, 0 x6)", lambda p: p.GetGabarit(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), True),
    ("GetGabarit(True, 0 x6)", lambda p: p.GetGabarit(True, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), False),
    ("GetGabarit(False)", lambda p: p.GetGabarit(False), True),
    ("GetGabarit(True)", lambda p: p.GetGabarit(True), False),
    ("GetGabarit()", lambda p: p.GetGabarit(), False),
]


def read_body(part, attempts: Optional[List[str]] = None) -> Tuple[Optional[Tuple[float, float, float]], bool]:
    """(габарит, точный ли). Точный только если КОМПАС посчитал одни тела детали."""
    attempts = attempts if attempts is not None else []
    for name, call, exact in _GABARIT_CALLS:
        try:
            res = call(part)
        except Exception as e:                                 # noqa: BLE001
            attempts.append(f"{name} -> ошибка: {str(e)[:80]}")
            continue
        dims = dims_from_gabarit(res)
        if dims:
            attempts.append(f"{name} -> успех, координаты {[round(v, 3) for v in _numbers(res)]}")
            return dims, exact
        attempts.append(f"{name} -> нет размеров в ответе")
    return None, False


def read_body_dims(part, attempts: Optional[List[str]] = None) -> Optional[Tuple[float, float, float]]:
    """Габарит детали (без признака точности)."""
    return read_body(part, attempts)[0]


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
                r = ks.body(path, explore=True)
                lines.append(f"Габарит тела: {r.dims} (только тела)" if r.exact
                             else f"Габарит тела: {r.dims} (НЕ точный: GetGabarit)")
                lines.append(f"Ошибка: {r.error}")
                lines.extend(f"  {a}" for a in r.attempts)
    text = "\n".join(lines)
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    return text


def _typelib_members(obj, pattern: str, limit: int = 60) -> List[str]:
    """Ищет в типобиблиотеке КОМПАС все интерфейсы, у которых есть члены по шаблону."""
    import re
    import sys as _sys
    out: List[str] = []
    mod = _sys.modules.get(type(obj).__module__)
    if mod is None:
        return ["модуль типобиблиотеки не найден"]
    pat = re.compile(pattern, re.I)
    for cname in sorted(dir(mod)):
        cls = getattr(mod, cname, None)
        if not isinstance(cls, type):
            continue
        if pat.search(cname):
            out.append(f"класс {cname}")
        names = set(k for k in vars(cls) if not str(k).startswith("_"))
        for attr in ("_prop_map_get_", "_prop_map_put_", "_dispid_to_func_"):
            m = getattr(cls, attr, None)
            if isinstance(m, dict):
                names.update(str(k) for k in m)
        hit = sorted(n for n in names if pat.search(n))
        if hit:
            out.append(f"{cname}: {', '.join(hit)}")
    return out[:limit]


def _tlb_returns(app, target: str, extra_pat: str = r"embodiment") -> tuple:
    """Читает настоящую типобиблиотеку КОМПАС: кто возвращает интерфейс target.
    Возвращает (список (интерфейс, член), справочные строки про члены по шаблону)."""
    import re
    ti0 = app._oleobj_.GetTypeInfo()
    tlb, _ = ti0.GetContainingTypeLib()
    pat = re.compile(extra_pat, re.I)
    hits, info = [], []
    dump_ifaces = {"IKompasDocument", "IKompasDocument3D", "IKompasDocument3D1", "IPart7",
                   "IApplication", "IDocuments", "IProductDataManager", "IModelContainer",
                   "IDocument3DManager"}
    dumped: dict = {}

    def ref_name(ti, t):
        # t = (VT_PTR, (VT_USERDEFINED, href)) либо (VT_USERDEFINED, href)
        try:
            while isinstance(t, tuple) and t and t[0] == 26:
                t = t[1]
            if isinstance(t, tuple) and t and t[0] == 29:
                return ti.GetRefTypeInfo(t[1]).GetDocumentation(-1)[0]
        except Exception:                                      # noqa: BLE001
            pass
        return ""

    for i in range(tlb.GetTypeInfoCount()):
        try:
            ti = tlb.GetTypeInfo(i)
            iname = ti.GetDocumentation(-1)[0]
            attr = ti.GetTypeAttr()
            for j in range(attr.cFuncs):
                fd = ti.GetFuncDesc(j)
                names = ti.GetNames(fd.memid)
                if not names:
                    continue
                member = names[0]
                rets = [ref_name(ti, fd.rettype[0] if isinstance(fd.rettype, tuple) else fd.rettype)]
                for ad in (fd.args or ()):
                    rets.append(ref_name(ti, ad[0]))
                if target in rets:
                    hits.append((iname, member))
                if pat.search(member) and len(info) < 80:
                    info.append(f"{iname}.{member} -> {rets[0] or '?'}")
                if iname in dump_ifaces:
                    dumped.setdefault(iname, set()).add(f"{member}:{rets[0] or '?'}")
                elif "manager" in member.lower() and len(info) < 120:
                    info.append(f"{iname}.{member} -> {rets[0] or '?'}")
        except Exception:                                      # noqa: BLE001
            continue
    for iname, ms in sorted(dumped.items()):
        info.append(f"[{iname}] " + ", ".join(sorted(ms)))
    return sorted(set(hits)), info


def _explore_embodiments(doc, top, attempts: List[str], n_emb: int = 1) -> None:
    """Диагностика: где в этой версии КОМПАС переключаются исполнения модели."""
    doc3d = None
    try:
        from win32com.client import CastTo
        doc3d = CastTo(doc, "IKompasDocument3D")
    except Exception as e:                                     # noqa: BLE001
        attempts.append(f"исполнения, doc3d -> ошибка: {str(e)[:80]}")
    # 1) что вообще есть в типобиблиотеке про исполнения
    try:
        for line in _typelib_members(top, r"embod|dummy|execution"):
            attempts.append("тип: " + line[:300])
    except Exception as e:                                     # noqa: BLE001
        attempts.append(f"тип: ошибка {str(e)[:80]}")
    # 2) соседние версии интерфейса документа
    if doc3d is not None:
        from win32com.client import CastTo
        for iface in ("IKompasDocument3D1", "IKompasDocument3D2", "IKompasDocument3D3"):
            try:
                d = CastTo(doc, iface)
            except Exception as e:                             # noqa: BLE001
                attempts.append(f"{iface} -> {str(e)[:50]}")
                continue
            for prop in ("EmbodimentsManager", "Embodiments", "CurrentEmbodiment"):
                try:
                    attempts.append(f"{iface}.{prop} = {getattr(d, prop)!r}"[:200])
                except Exception as e:                         # noqa: BLE001
                    attempts.append(f"{iface}.{prop} -> {str(e)[:50]}")
    # 3) менеджер исполнений: ищем, какой член возвращает IEmbodimentsManager
    mgr = None
    try:
        hits, info = _tlb_returns(doc.Application, "IEmbodimentsManager")
        for line in info:
            attempts.append("tlb: " + line)
    except Exception as e:                                     # noqa: BLE001
        hits = []
        attempts.append(f"типобиблиотека -> ошибка: {str(e)[:100]}")
    attempts.append("IEmbodimentsManager возвращают: " + ", ".join(f"{c}.{m}" for c, m in hits)[:400])
    probes = [("doc3d", doc3d), ("top", top), ("doc", doc)]
    try:
        probes.append(("app", doc.Application))
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from win32com.client import CastTo
        probes.append(("doc3d1", CastTo(doc, "IKompasDocument3D1")))
    except Exception:                                          # noqa: BLE001
        pass
    for label, obj in probes:
        if obj is None or mgr is not None:
            continue
        mro = {c.__name__ for c in type(obj).__mro__}
        for cls, member in hits:
            if cls not in mro or mgr is not None:
                continue
            try:
                v = getattr(obj, member)
                v = v() if callable(v) else v
                attempts.append(f"{label}.{member} ({cls}) -> {type(v).__name__}")
                if v is not None:
                    mgr = v
            except Exception as e:                             # noqa: BLE001
                attempts.append(f"{label}.{member} ({cls}) -> {str(e)[:60]}")
    if mgr is None:
        # менеджер исполнений реализован самим документом: достаём через QueryInterface
        try:
            from win32com.client import CastTo as _Cast
        except Exception:                                      # noqa: BLE001
            _Cast = None
        for label, obj in probes:
            if obj is None or mgr is not None or _Cast is None:
                continue
            try:
                cand = _Cast(obj, "IEmbodimentsManager")
                attempts.append(f"QueryInterface({label}) -> IEmbodimentsManager: число исполнений={cand.EmbodimentCount}")
                mgr = cand
            except Exception as e:                             # noqa: BLE001
                attempts.append(f"QueryInterface({label}) -> {str(e)[:60]}")
    if mgr is None:
        try:
            from win32com.client import CastTo
        except Exception:                                      # noqa: BLE001
            CastTo = None
        for label, obj in probes:
            if obj is None or mgr is not None:
                continue
            from .step_export import api_names
            for nm in api_names(obj, r"manager"):
                try:
                    v = getattr(obj, nm)
                    v = v() if callable(v) else v
                except Exception as e:                         # noqa: BLE001
                    attempts.append(f"{label}.{nm} -> {str(e)[:40]}")
                    continue
                attempts.append(f"{label}.{nm} -> {type(v).__name__}: "
                                + ", ".join(api_names(v, r"embod|current|variant"))[:200])
                if v is None:
                    continue
                for cand in (v, CastTo(v, "IEmbodimentsManager") if CastTo else None):
                    try:
                        if cand is not None and cand.EmbodimentCount is not None:
                            mgr = cand
                            attempts.append(f"  менеджер исполнений найден через {label}.{nm}")
                            break
                    except Exception:                          # noqa: BLE001
                        continue
                if mgr is not None:
                    break
    if mgr is None:
        attempts.append("менеджер исполнений не получен")
        return
    try:
        cnt = mgr.EmbodimentCount
        attempts.append(f"менеджер: EmbodimentCount={cnt}, CurrentEmbodimentIndex={mgr.CurrentEmbodimentIndex}")
    except Exception as e:                                     # noqa: BLE001
        attempts.append(f"менеджер: счётчик -> {str(e)[:60]}")
        cnt = n_emb
    start = None
    try:
        start = mgr.CurrentEmbodimentIndex
    except Exception:                                          # noqa: BLE001
        pass
    def snap(part) -> str:
        out = []
        for nm in ("Marking", "Mass"):
            try:
                out.append(f"{nm}={getattr(part, nm)!r}")
            except Exception as e:                             # noqa: BLE001
                out.append(f"{nm}->{str(e)[:15]}")
        try:
            out.append(f"габарит={read_body(part, [])[0]}")
        except Exception as e:                                 # noqa: BLE001
            out.append(f"габарит->{str(e)[:25]}")
        return " ".join(out)

    from .step_export import api_names
    for i in range(0, int(cnt or n_emb)):
        attempts.append(f"исполнение {i}:")
        try:
            mgr.SetCurrentEmbodiment(i)
            attempts.append(f"   индекс={mgr.CurrentEmbodimentIndex}")
        except Exception as e:                                 # noqa: BLE001
            attempts.append(f"   Set -> {str(e)[:60]}")
            continue
        try:
            emb = mgr.Embodiment(i)
            line = "   Embodiment(i): " + ", ".join(api_names(emb, r"."))[:150]
            for nm in ("Marking", "Name", "Index", "AdditionalNumber"):
                try:
                    line += f" | {nm}={getattr(emb, nm)!r}"
                except Exception:                              # noqa: BLE001
                    pass
            attempts.append(line)
        except Exception as e:                                 # noqa: BLE001
            attempts.append(f"   Embodiment(i) -> {str(e)[:60]}")
        attempts.append("   top (тот же объект): " + snap(top))
        try:
            top2 = doc3d.TopPart
            attempts.append("   doc3d.TopPart заново: " + snap(top2))
        except Exception as e:                                 # noqa: BLE001
            top2 = None
            attempts.append(f"   TopPart заново -> {str(e)[:50]}")
        for label, fn in (("top.Update()", lambda: top.Update()),
                          ("doc3d.RebuildDocument()", lambda: doc3d.RebuildDocument()),
                          ("top.RebuildModel()", lambda: top.RebuildModel())):
            try:
                r = fn()
                t3 = doc3d.TopPart
                attempts.append(f"   после {label}={r!r}: " + snap(t3))
            except Exception as e:                             # noqa: BLE001
                attempts.append(f"   {label} -> {str(e)[:50]}")
    if start is not None:
        try:
            mgr.SetCurrentEmbodiment(start)
        except Exception:                                      # noqa: BLE001
            try:
                mgr.CurrentEmbodimentIndex = start
            except Exception:                                  # noqa: BLE001
                pass
    return
    try:
        top.SetDummyEmbodiment(-1)
    except Exception:                                          # noqa: BLE001
        pass


def read_embodiments(doc, attempts: List[str]) -> List[dict]:
    """Габарит, обозначение и массу КАЖДОГО исполнения модели.
    Менеджер исполнений достаётся приведением документа (QueryInterface);
    после SetCurrentEmbodiment деталь нужно взять у документа заново (TopPart),
    иначе объект остаётся привязанным к прежнему исполнению. Исходное
    исполнение возвращается на место, файл не сохраняется."""
    out: List[dict] = []
    try:
        from win32com.client import CastTo
        doc3d = CastTo(doc, "IKompasDocument3D")
        mgr = CastTo(doc3d, "IEmbodimentsManager")
        cnt = int(mgr.EmbodimentCount)
    except Exception as e:                                     # noqa: BLE001
        attempts.append(f"исполнения: менеджер недоступен: {str(e)[:70]}")
        return out
    start = None
    try:
        start = mgr.CurrentEmbodimentIndex
    except Exception:                                          # noqa: BLE001
        pass
    for i in range(cnt):
        item = {"index": i, "marking": "", "mass_g": None, "dims": None, "exact": False}
        try:
            mgr.SetCurrentEmbodiment(i)
            part = doc3d.TopPart
            try:
                item["marking"] = str(part.Marking or "")
            except Exception:                                  # noqa: BLE001
                pass
            try:
                item["mass_g"] = float(part.Mass)
            except Exception:                                  # noqa: BLE001
                pass
            dims, exact = read_body(part, [])
            item["dims"], item["exact"] = dims, bool(exact)
        except Exception as e:                                 # noqa: BLE001
            attempts.append(f"исполнение {i}: {str(e)[:70]}")
        out.append(item)
    if start is not None:
        try:
            mgr.SetCurrentEmbodiment(start)
        except Exception:                                      # noqa: BLE001
            pass
    return out


def measure(app, path: str, explore: bool = False) -> BodyResult:
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
        res.dims, res.exact = read_body(top, res.attempts)
        res.embs = read_embodiments(doc, res.attempts)
        if not res.embs and res.dims:
            # менеджер недоступен: габарит относится к текущему исполнению, оно известно по обозначению
            try:
                res.embs = [{"index": 0, "marking": str(top.Marking or ""), "mass_g": None,
                             "dims": res.dims, "exact": bool(res.exact)}]
            except Exception:                                  # noqa: BLE001
                pass
        if explore:
            try:
                from core.m3d_reader import embodiments
                n_emb = len(embodiments(path)) or 1
            except Exception:                                  # noqa: BLE001
                n_emb = 1
            _explore_embodiments(doc, top, res.attempts, n_emb)
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

    def body(self, path: str, explore: bool = False) -> BodyResult:
        if self.app is None:
            return BodyResult(error=self.error or "КОМПАС недоступен")
        return measure(self.app, os.path.abspath(path), explore)


def _write_log_file(text: str) -> None:
    """Дублирует журнал в файл kompas_batch.log (рядом с программой): его можно
    прислать для разбора, не копируя из панели разработчика."""
    try:
        import datetime
        from core.paths import USER_DATA_DIR
        with open(os.path.join(str(USER_DATA_DIR), "kompas_batch.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except Exception:                                          # noqa: BLE001
        pass


def log_result(path: str, r: BodyResult) -> None:
    name = os.path.basename(path)
    if r.dims:
        msg = f"КОМПАС(фон): {name}: габарит {r.dims}" + (" (только тела)" if r.exact else " (GetGabarit: с плоскостями, не используется)")
        logger.info(msg)
        _write_log_file(msg + (" | " + "; ".join(r.attempts) if r.attempts else ""))
        for e in r.embs:
            _write_log_file(f"   исполнение {e.get('index')}: {e.get('marking')} габарит {e.get('dims')}"
                            f" масса {e.get('mass_g')} г" + ("" if e.get("exact") else " (НЕ точный)"))
    else:
        msg = f"КОМПАС(фон): {name}: {r.error}; попытки: {'; '.join(r.attempts)}"
        logger.warn(msg)
        _write_log_file(msg)
