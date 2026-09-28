# ===========================
# M3D_READER.PY — чтение свойств моделей КОМПАС (.m3d / .a3d)
# ===========================
#
# ВАЖНАЯ НАХОДКА: формат КОМПАС .m3d — это ZIP-архив, внутри которого
# лежит файл MetaProductInfo — обычный XML в кодировке UTF-16 BE.
# Оттуда читаются ВСЕ свойства детали, включая МАТЕРИАЛ,
# которого нет в экспорте STEP.
#
# КОМПАС для этого НЕ НУЖЕН — работает на любой ОС (macOS/Linux/Windows).
#
# Что достаётся:
#   • Обозначение (135517.03.00.051)
#   • Наименование (Платик)
#   • Материал (Сталь 45  ГОСТ 1050-2013)
#   • Плотность (7810)
#   • Масса (1.667126)
#   • Литера (И), Раздел спецификации, Класс точности
#   • Автор, организация, путь к файлу, версия КОМПАС
#
# Геометрия (габариты/объём) в .m3d лежит в закрытом бинарном формате C3D
# и НЕ читается — для неё нужен экспорт в STEP (см. core/step_reader.py).

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from core import logger


@dataclass
class M3DInfo:
    """Свойства детали, прочитанные из .m3d"""
    file_path: str = ""
    drawing_no: str = ""        # Обозначение
    part_name: str = ""         # Наименование
    material: str = ""          # Материал целиком: "Сталь 45  ГОСТ 1050-2013"
    material_mark: str = ""     # Марка: "Сталь 45"
    material_gost: str = ""     # ГОСТ материала: "ГОСТ1050-2013"
    density: Optional[float] = None      # кг/м³
    mass_kg: Optional[float] = None      # масса из КОМПАС
    revision_letter: str = ""   # Литера (И)
    spec_section: str = ""      # Раздел спецификации (Детали)
    accuracy_class: str = ""    # Класс точности
    author: str = ""
    organization: str = ""
    comment: str = ""
    full_file_name: str = ""
    kompas_version: str = ""
    created: str = ""
    modified: str = ""
    all_props: Dict[str, Any] = field(default_factory=dict)
    protocol: str = ""


# ============================================================
# РАЗБОР МАТЕРИАЛА
# ============================================================

def split_material(s: str) -> tuple:
    """
    'Сталь 45  ГОСТ 1050-2013' → ('Сталь 45', 'ГОСТ1050-2013')
    'Ст3сп ГОСТ 535-2005'      → ('Ст3сп', 'ГОСТ535-2005')
    """
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if not s:
        return "", ""
    m = re.search(r"\bГОСТ\s*([\d\-.]+)", s, flags=re.IGNORECASE)
    if m:
        mark = s[:m.start()].strip()
        gost = "ГОСТ" + m.group(1).strip()
        return mark, gost
    return s, ""


# ============================================================
# ЧТЕНИЕ XML ИЗ .m3d
# ============================================================

def _read_meta_xml(path: str) -> Optional[str]:
    """Достаёт MetaProductInfo из .m3d (это ZIP) и декодирует UTF-16 BE."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            target = None
            for n in ("MetaProductInfo", "MetaInfo"):
                if n in names:
                    target = n
                    break
            if not target:
                logger.warn(f"m3d: MetaProductInfo не найден в {os.path.basename(path)}")
                return None
            raw = z.read(target)
    except zipfile.BadZipFile:
        logger.error(f"m3d: файл не является ZIP: {path}")
        return None
    except Exception as e:
        logger.error(f"m3d: ошибка открытия {path}: {e}")
        return None

    for enc in ("utf-16-be", "utf-16-le", "utf-8"):
        try:
            txt = raw.decode(enc)
            if "<document" in txt or "<?xml" in txt:
                return txt.lstrip("\ufeff")
        except Exception:
            continue
    return None


def _read_file_info(path: str) -> Dict[str, str]:
    """Читает FileInfo (версия КОМПАС, даты)."""
    out: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as z:
            if "FileInfo" not in z.namelist():
                return out
            raw = z.read("FileInfo")
        txt = raw.decode("utf-16-be", errors="ignore").lstrip("\ufeff")
        for line in txt.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _props_of(elem) -> Dict[str, Any]:
    """
    Собирает свойства элемента в словарь, разворачивая вложенность:
      <property id="material"><property id="name" value="Сталь 45"/></property>
      → {"material": {"name": "Сталь 45", ...}}
    """
    out: Dict[str, Any] = {}
    for ch in elem:
        if ch.tag != "property":
            continue
        pid = ch.attrib.get("id", "")
        if not pid:
            continue
        kids = [c for c in ch if c.tag == "property"]
        if kids:
            out[pid] = _props_of(ch)
        else:
            out[pid] = ch.attrib.get("value", "")
    return out


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def read_m3d(path: str) -> Optional[M3DInfo]:
    """
    Читает свойства детали из .m3d (или .a3d) БЕЗ КОМПАСа.
    Возвращает M3DInfo или None.
    """
    if not path or not os.path.exists(path):
        logger.warn(f"m3d: файл не найден: {path}")
        return None

    xml_txt = _read_meta_xml(path)
    if not xml_txt:
        return None

    try:
        root = ET.fromstring(xml_txt)
    except Exception as e:
        logger.error(f"m3d: XML не разобрался: {e}")
        return None

    info = M3DInfo(file_path=path)

    # Ищем объект детали: у него есть и наименование, и материал,
    # а имя не вида "Тело N"
    best: Dict[str, Any] = {}
    doc_props: Dict[str, Any] = {}

    for elem in root.iter():
        tag = elem.tag
        if tag in ("infObject", "groupInfObject"):
            p = _props_of(elem)
            name = str(p.get("name", ""))
            has_mat = isinstance(p.get("material"), dict)
            if has_mat and name and not re.match(r"^Тело\s*\d*$", name.strip()):
                best = p
            elif has_mat and not best:
                best = p
        elif tag == "document":
            p = _props_of(elem)
            if p:
                doc_props.update(p)

    if not best:
        logger.warn(f"m3d: свойства детали не найдены в {os.path.basename(path)}")

    # Наименование
    info.part_name = str(best.get("name", "")).strip()

    # Обозначение (marking.base)
    marking = best.get("marking")
    if isinstance(marking, dict):
        info.drawing_no = str(marking.get("base", "")).strip()

    # Материал
    mat = best.get("material")
    if isinstance(mat, dict):
        info.material = re.sub(r"\s+", " ", str(mat.get("name", ""))).strip()
        info.material_mark, info.material_gost = split_material(info.material)
        try:
            info.density = float(mat.get("density")) if mat.get("density") else None
        except (TypeError, ValueError):
            info.density = None

    # Масса
    try:
        info.mass_kg = round(float(best.get("mass")), 4) if best.get("mass") else None
    except (TypeError, ValueError):
        info.mass_kg = None

    info.revision_letter = str(best.get("revisionLetter", "")).strip()
    info.accuracy_class = str(best.get("accuracyClass", "")).strip()

    sec = best.get("SPCSection")
    if isinstance(sec, dict):
        info.spec_section = str(sec.get("sectionName", "")).strip()
    elif sec:
        info.spec_section = str(sec).strip()
    if not info.spec_section:
        # иногда лежит отдельно
        for elem in root.iter("property"):
            if elem.attrib.get("id") == "sectionName" and elem.attrib.get("value"):
                info.spec_section = elem.attrib["value"].strip()
                break

    info.author = str(doc_props.get("author", "")).strip()
    info.organization = str(doc_props.get("organization", "")).strip()
    info.comment = str(doc_props.get("comment", "")).strip()
    info.full_file_name = str(doc_props.get("fullFileName", "")).strip()

    fi = _read_file_info(path)
    info.kompas_version = fi.get("AppName", "")
    info.created = fi.get("CreateData", "")
    info.modified = fi.get("ModifyData", "")

    info.all_props = best

    prot = [
        "Протокол чтения .m3d (свойства КОМПАС)",
        f"- Файл: {os.path.basename(path)}",
        f"- Обозначение: {info.drawing_no or '—'}",
        f"- Наименование: {info.part_name or '—'}",
        f"- Материал: {info.material or '—'}",
        f"-   марка: {info.material_mark or '—'} | ГОСТ: {info.material_gost or '—'}",
        f"- Плотность: {info.density if info.density else '—'} кг/м³",
        f"- Масса (из КОМПАС): {info.mass_kg if info.mass_kg else '—'} кг",
        f"- Литера: {info.revision_letter or '—'}",
        f"- Раздел спецификации: {info.spec_section or '—'}",
        f"- Класс точности: {info.accuracy_class or '—'}",
        f"- Автор: {info.author or '—'}",
        f"- Версия КОМПАС: {info.kompas_version or '—'}",
        f"- Изменён: {info.modified or '—'}",
        "- Геометрия в .m3d закрыта (формат C3D) — габариты берутся из STEP",
    ]
    info.protocol = "\n".join(prot)

    logger.info(f"m3d: {info.part_name} / {info.drawing_no} → материал: {info.material}")
    return info


def find_m3d_for(step_path: str) -> Optional[str]:
    """
    Ищет .m3d рядом со STEP-файлом (по совпадению имени).
    Например: 'Платик.stp' → 'Платик.m3d'
    """
    if not step_path:
        return None
    base = os.path.splitext(step_path)[0]
    for ext in (".m3d", ".M3D"):
        p = base + ext
        if os.path.exists(p):
            return p
    return None