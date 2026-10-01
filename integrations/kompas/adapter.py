# -*- coding: utf-8 -*-
"""
integrations/kompas/adapter.py — чтение материала и массы из активного
документа уже запущенного КОМПАС-3D (без экспорта файлов).

Путь подтверждён диагностикой (integrations/kompas/probe.py) и
документацией АСКОН:
    doc -> IKompasDocument3D -> TopPart (IPart7: Material, Mass, Marking)
         -> IMassInertiaParam7 (Volume, Density, Area) — для деталей расчёта

ВАЖНО — честно о границах этой версии:
  Подключение к живому КОМПАСу подтверждено на реальной машине (API7, v22).
  Само чтение TopPart/Material/Mass через этот путь ПОКА НЕ проверено на
  реальной детали — написано строго по документации АСКОН, но без доступа
  к компьютеру с КОМПАСом нельзя исключить, что какое-то имя свойства
  отличается на практике. Каждый шаг обёрнут в try/except с понятным
  текстом ошибки — если что-то не совпадёт, это будет видно в результате,
  а не приведёт к тихому падению.

  Главное ограничение: 3D-модель (.m3d/.a3d) не даёт готовой классификации
  "это круг/труба/лист" с размерами, как даёт текст 2D-чертежа — это не
  баг, а особенность формата (эта классификация в .cdw/.pdf читается из
  текста штампа, а в 3D-модели её физически нет, только геометрия и
  масса). Поэтому пока эта функция:
    1) пробует найти деталь в БАЗЕ ЭТАЛОНОВ по обозначению (Marking) —
       если нашлась, отдаёт готовый результат, как CDW;
    2) если в базе нет — отдаёт то, что реально прочитала (материал,
       масса), но НЕ пытается угадать сортамент по формуле — для этого
       нужна отдельная классификация геометрии (отдельная будущая задача),
       а не честная замена ей.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core import drawing_db
from core.drawing_db import lookup_by_drawing_no
from core import logger

from .connector import connect, get_active_document, is_available, ConnectionStatus


@dataclass
class KompasReadResult:
    ok: bool
    drawing_no: str = ""
    part_name: str = ""
    material_mark: str = ""
    mass_kg: Optional[float] = None
    volume_mm3: Optional[float] = None
    density_kg_m3: Optional[float] = None
    source: str = ""            # "КОМПАС(live)+база" / "КОМПАС(live)"
    result_line: str = ""       # заполнено, если нашёлся эталон в базе
    status: str = ""            # "ОК" / "⚠ ..." / "❌ ошибка"
    message: str = ""
    raw_debug: str = ""         # что реально вернул COM — для разбора проблем


def _typed_module_from_running_app(app) -> None:
    """Готовит typed-обёртку (gencache) из типобиблиотеки УЖЕ запущенного
    приложения — без запуска нового экземпляра и без угадывания GUID."""
    from win32com.client import gencache
    type_info = app._oleobj_.GetTypeInfo()
    type_lib, _ = type_info.GetContainingTypeLib()
    lib_attr = type_lib.GetLibAttr()
    gencache.EnsureModule(lib_attr[0], lib_attr[1], lib_attr[3], lib_attr[4])


def _clean_part_name(raw: str) -> str:
    """'235610.03.02.001 Основание' -> 'Основание' (убираем номер чертежа)."""
    import re
    if not raw:
        return ""
    m = re.match(r'^[\d.\s_]+([А-ЯЁа-яёA-Za-z].*)', raw)
    return (m.group(1) if m else raw).strip()


def read_active_part() -> KompasReadResult:
    """
    Подключается к уже запущенному КОМПАСу и читает материал/массу
    активной 3D-детали. Не бросает исключений — все ошибки попадают
    в result.status/message, чтобы вызывающий код (UI) мог их просто
    показать.
    """
    if not is_available():
        return KompasReadResult(False, status="❌ ошибка",
                                 message="Доступно только в Windows-сборке")

    status = connect()
    if not status.connected:
        return KompasReadResult(False, status="❌ ошибка", message=status.error)

    app = status.app
    doc = get_active_document(app)
    if doc is None:
        return KompasReadResult(
            False, status="❌ ошибка",
            message="Нет активного документа. Откройте деталь/сборку в КОМПАСе и повторите."
        )

    debug_lines = []

    try:
        doc_name = str(getattr(doc, "Name", "") or "")
    except Exception as e:                                 # noqa: BLE001
        doc_name = ""
        debug_lines.append(f"doc.Name -> ошибка: {e}")

    try:
        from win32com.client import CastTo
        _typed_module_from_running_app(app)
        doc3d = CastTo(doc, "IKompasDocument3D")
    except Exception as e:                                 # noqa: BLE001
        return KompasReadResult(
            False, status="❌ ошибка",
            message=f"Не удалось получить типизированный 3D-документ (IKompasDocument3D): {e}",
            raw_debug="\n".join(debug_lines),
        )

    try:
        top_part = doc3d.TopPart
    except Exception as e:                                 # noqa: BLE001
        return KompasReadResult(
            False, status="❌ ошибка",
            message=f"doc3d.TopPart: {e}",
            raw_debug="\n".join(debug_lines),
        )
    if top_part is None:
        return KompasReadResult(
            False, status="❌ ошибка",
            message="TopPart пуст — модель, похоже, не полностью загружена.",
            raw_debug="\n".join(debug_lines),
        )

    marking = ""
    material_mark = ""
    mass_kg = None
    volume_mm3 = None
    density_kg_m3 = None

    for attr, setter in (
        ("Marking", lambda v: v),
        ("Material", lambda v: v),
        ("Mass", lambda v: v),
    ):
        try:
            value = getattr(top_part, attr)
            if attr == "Marking":
                marking = str(value or "")
            elif attr == "Material":
                material_mark = str(value or "")
            elif attr == "Mass":
                mass_kg = float(value) if value is not None else None
        except Exception as e:                             # noqa: BLE001
            debug_lines.append(f"TopPart.{attr} -> ошибка: {e}")

    try:
        mip = CastTo(top_part, "IMassInertiaParam7")
        for attr in ("Mass", "Volume", "Density", "Material"):
            try:
                value = getattr(mip, attr)
            except Exception as e:                         # noqa: BLE001
                debug_lines.append(f"MassInertiaParam.{attr} -> ошибка: {e}")
                continue
            if attr == "Mass" and mass_kg is None and value is not None:
                mass_kg = float(value)
            elif attr == "Volume" and value is not None:
                volume_mm3 = float(value)
            elif attr == "Density" and value is not None:
                density_kg_m3 = float(value)
            elif attr == "Material" and not material_mark and value:
                material_mark = str(value)
    except Exception as e:                                 # noqa: BLE001
        debug_lines.append(f"CastTo(TopPart, 'IMassInertiaParam7') -> ошибка: {e}")

    part_name = _clean_part_name(doc_name)
    drawing_no = marking or part_name

    # 1) База эталонов — ищем точно так же, как для CDW: по обозначению.
    etalon = None
    if drawing_no:
        try:
            etalon = lookup_by_drawing_no(drawing_no)
        except Exception as e:                             # noqa: BLE001
            logger.warn(f"КОМПАС(live): поиск эталона не удался: {e}")

    result = KompasReadResult(
        ok=True,
        drawing_no=drawing_no,
        part_name=part_name,
        material_mark=material_mark,
        mass_kg=mass_kg,
        volume_mm3=volume_mm3,
        density_kg_m3=density_kg_m3,
        raw_debug="\n".join(debug_lines),
    )

    if etalon:
        result.source = "КОМПАС(live)+база"
        result.result_line = etalon.get("result_line", "")
        result.status = "ОК"
        result.message = ""
        return result

    # 2) В базе нет. Классификации геометрии (круг/труба/лист) для 3D
    # пока не сделано — это честно отдельная задача, не подменяем её.
    result.source = "КОМПАС(live)"
    result.status = "⚠ заготовка не определена"
    result.message = (
        "Материал и масса прочитаны из модели, но автоматический подбор "
        "заготовки для 3D-моделей ещё не реализован (нужна классификация "
        "геометрии — отдельная задача). Это не ошибка чтения."
    )
    return result
