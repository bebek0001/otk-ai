# -*- coding: utf-8 -*-
"""
cdw_reader.py — чтение чертежей КОМПАС-3D (.cdw / .frw / .spw) БЕЗ запуска КОМПАСа
и БЕЗ визуального отображения чертежа: только цифровая структура файла.

Формат КОМПАС v18+ (проверено на v22, build 1513): ZIP-контейнер.
Ключевой поток — MetaProductInfo: XML в UTF-16 со всеми свойствами документа.

Зависимости: только стандартная библиотека Python.

Использование:
    from cdw_reader import read_cdw, is_cdw_supported
    data = read_cdw("290426.00.00.002 Штырь направляющий.cdw")
"""

from __future__ import annotations

import re
import zipfile
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

# Потоки внутри контейнера
STREAM_META = "MetaProductInfo"     # свойства документа (XML UTF-16)
STREAM_FILEINFO = "FileInfo"        # версия КОМПАСа (INI UTF-16)
STREAM_CONTENTS = "Contents"        # графика (KF + zlib-чанки)
STREAM_RESOURCES = "resourcesInfo"  # карта ресурсов: где штамп, где техтребования


# --------------------------------------------------------------------------- #
#  Низкий уровень
# --------------------------------------------------------------------------- #

def _decode_u16(data: bytes) -> str:
    """КОМПАС пишет UTF-16 то BE, то LE — BOM разбирает это сам."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-16-le", "utf-16-be", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _unpack_kf(data: bytes) -> bytes:
    """
    Распаковать поток формата 'KF': сигнатура + последовательность zlib-чанков.
    Возвращает конкатенацию распакованных чанков.
    """
    if data[:2] == b"KF":
        data = data[2:]
    out = bytearray()
    pos = 0
    n = len(data)
    while pos < n - 1:
        if data[pos] == 0x78 and data[pos + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            obj = zlib.decompressobj()
            try:
                chunk = obj.decompress(bytes(data[pos:]))
            except zlib.error:
                pos += 1
                continue
            if chunk:
                out += chunk
                pos = n - len(obj.unused_data)
                continue
        pos += 1
    return bytes(out)


def is_cdw_supported(path: str | Path) -> bool:
    """True — файл читается структурно (КОМПАС v18+, ZIP-контейнер)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


# --------------------------------------------------------------------------- #
#  Разбор свойств
# --------------------------------------------------------------------------- #

def _flatten(node: ET.Element, prefix: str = "") -> dict[str, str]:
    """Свойства КОМПАСа вложенные (material -> name/density). Разворачиваем в плоский dict."""
    res: dict[str, str] = {}
    for prop in node.findall("property"):
        pid = prop.get("id", "")
        if not pid:
            continue
        key = f"{prefix}{pid}"
        val = prop.get("value")
        if val is not None:
            res[key] = val
        res.update(_flatten(prop, prefix=f"{key}."))
    return res


def _assemble_marking(flat: dict[str, str]) -> str:
    """Обозначение собирается из base + исполнение + доп.номер."""
    base = flat.get("marking.base", "").strip()
    if not base:
        return ""
    parts = [base]
    emb = flat.get("marking.embodimentNumber", "").strip()
    if emb:
        parts.append(flat.get("marking.embodimentDelimiter", "-") + emb)
    add = flat.get("marking.additionalNumber", "").strip()
    if add:
        parts.append(flat.get("marking.additionalDelimiter", ".") + add)
    doc = flat.get("marking.documentNumber", "").strip()
    if doc:
        parts.append(flat.get("marking.documentDelimiter", " ") + doc)
    return "".join(parts)


def _kompas_version(z: zipfile.ZipFile) -> str:
    try:
        txt = _decode_u16(z.read(STREAM_FILEINFO))
    except KeyError:
        return ""
    m = re.search(r"AppVersion=([^\r\n]+)", txt)
    return m.group(1).strip() if m else ""


def _library_refs(meta_xml: str) -> list[str]:
    """
    Ссылки на библиотечные макро (.frw/.kle). Для деталей из проката
    здесь прямо лежит сортамент: 'Уголок неравнопол. ГОСТ 8510.frw'.
    """
    refs = []
    for val in re.findall(r'id="fullFileName"\s+value="([^"]*)"', meta_xml):
        if val.lower().endswith((".frw", ".kle")):
            refs.append(val.replace("\\", "/").rsplit("/", 1)[-1])
    return list(dict.fromkeys(refs))


# Допустимые символы русского техтекста. Всё, что за пределами, — хвост
# двоичных данных, случайно попавший в декодированную строку.
_ALLOWED = re.compile(r"[А-Яа-яЁёA-Za-z0-9 .,;:!?()\[\]/\\+\-–—×хx°Ø∅±=%№\"'*]")


def _clean_tail(s: str) -> str:
    """Обрезать строку на первом символе, невозможном в техтребованиях."""
    for i, ch in enumerate(s):
        if not _ALLOWED.match(ch):
            return s[:i].strip()
    return s.strip()


def _tech_demands(z: zipfile.ZipFile) -> list[str]:
    """Технические требования — отдельный ресурс, текст лежит как UTF-16."""
    out: list[str] = []
    pat = re.compile(r"[А-ЯA-Zа-яa-z0-9][^\x00-\x08\x0b\x0c\x0e-\x1f]{5,}")
    for name in z.namelist():
        if not name.endswith(".TechnicalDemand"):
            continue
        raw = z.read(name)
        body = _unpack_kf(raw) if raw[:2] == b"KF" else raw
        if not body:                      # KF-поток без сжатия — читаем как есть
            body = raw
        text = body.decode("utf-16-le", errors="ignore")
        for s in pat.findall(text):
            s = _clean_tail(s)
            if len(s) > 5 and "GOST type" not in s and s not in out:
                out.append(s)
    return out


# Служебный словарь КОМПАСа: эти строки есть в каждом Contents и текстом
# чертежа не являются. Фильтруем, чтобы не засорять выдачу.
SYSTEM_DICT = frozenset("""
Пустой Габарит Обозначение Наименование Литера Материал Формат Предприятие
Масса Позиция Количество Примечание Зона Разрез Шероховатость Техтребования
Код документа Вид документа Класс документа Вид изделия Кол.листов
Ед. изм. массы Раздел спецификации Текст на чертеже Размерные надписи
Линия-выноска Отклонения формы и база Заголовок таблицы Ячейка таблицы
Название таблицы Линия разреза/сечения Линия разреза Стрелка вида
Обозначение изменения Фигурная скобка Номер узла Обозначение узла
Марка координационной оси Выносная надпись Текстовая метка Системный вид
Системный слой Линейный размер Диаметральный размер Радиальный размер
Угловой размер Неуказанная шероховатость Обстановка Автосортировка
Нумерация таблиц Заголовок таблицы отчета Ячейка таблицы отчета
Название таблицы отчета Создавать изделие в PLM Обозначение материала
Дополнительно создавать запись основного изделия
Марка/позиционное обозначение с линией-выноской
Марка/позиционное обозначение на линии
Марка/позиционное обозначение без линии-выноски
Комплект монтажных частей Комплект сменных частей Комплект запасных частей
Комплект инструмента и принадлежностей Комплект укладочных средств
Комплект для гидроиспытаний Простая спецификация ГОСТ 2.106-96.
Осевая Штриховая Штрихпунктирная Штрихпунктирная 2 Основная Тонкая
SYSTEM PROPERTY ADDITIONAL VCRBld БЦО Обозн ссылка Зона Слой 1
ID ФГ ID PartLib Код ОКП ID материала PartNo Doc
""".split("\n")) | frozenset(
    s.strip() for s in """
Пустой|Габарит|Обозначение|Наименование|Код документа|Литера|Вид документа|
Класс документа|Вид изделия|Создавать изделие в PLM|Материал|Формат|Кол.листов|
Предприятие|ссылка|Масса|Ед. изм. массы|Раздел спецификации|Текст на чертеже|
Размерные надписи|Шероховатость|Линия-выноска|Отклонения формы и база|
Заголовок таблицы|Ячейка таблицы|Название таблицы|Линия разреза/сечения|
Стрелка вида|Обозначение изменения|Фигурная скобка|Номер узла|
Марка координационной оси|Выносная надпись|Обозначение узла|Разрез|
Линия разреза|Заголовок таблицы отчета|Ячейка таблицы отчета|
Название таблицы отчета|Техтребования|Неуказанная шероховатость|
Текстовая метка|Системный вид|Линейный размер|Диаметральный размер|
Радиальный размер|Системный слой|Обстановка|Осевая|Штриховая|
Простая спецификация ГОСТ 2.106-96.|БЦО|Обозн|ID ФГ|ID PartLib|Код ОКП|
ID материала|Обозначение материала|Зона|Позиция|Количество|Примечание|
Комплект монтажных частей|Комплект сменных частей|Комплект запасных частей|
Комплект инструмента и принадлежностей|Комплект укладочных средств|
Комплект для гидроиспытаний|SYSTEM|PROPERTY|ADDITIONAL|Автосортировка|
Нумерация таблиц|Название таблицы спецификации|Слой 1|PartNo|Doc|
Дополнительно создавать запись основного изделия|
Марка/позиционное обозначение с линией-выноской|
Марка/позиционное обозначение на линии|
Марка/позиционное обозначение без линии-выноски
""".replace("\n", "").split("|")
)


def _drawing_texts(z: zipfile.ZipFile) -> list[str]:
    """Свободные текстовые надписи с поля чертежа (из Contents)."""
    try:
        raw = _unpack_kf(z.read(STREAM_CONTENTS))
    except KeyError:
        return []
    text = raw.decode("utf-16-le", errors="ignore")
    pat = re.compile(r"[А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9 .,\-–/×хx()°Ø∅=+№]{3,}")
    junk = ("GOST type", "Program Files", "ASCON", "KOMPAS", "Lighting",
            "graphic.lyt", "GRAPHIC.LYT")
    out = []
    for s in pat.findall(text):
        s = s.strip()
        if not s or s in SYSTEM_DICT or s in out:
            continue
        if any(j in s for j in junk):
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- #
#  Публичный API
# --------------------------------------------------------------------------- #

def read_cdw(path: str | Path) -> dict[str, Any]:
    """
    Прочитать чертёж КОМПАС структурно.

    Возвращает dict:
        marking      — обозначение (290426.00.00.002)
        name         — наименование (Штырь направляющий)
        material     — материал из штампа (Ст2пс ГОСТ 380-2005)
        density      — плотность, кг/м3
        mass         — масса чистовая, кг
        format       — формат листа (A4)
        revision     — литера (И)
        sheets       — количество листов
        roughness    — неуказанная шероховатость (Ra 12,5)
        author       — разработал
        source_path  — исходный путь файла у конструктора
        library_refs — библиотечные макро (сортамент для проката)
        tech_demands — технические требования (список строк)
        texts        — свободные надписи на поле чертежа
        kompas       — версия КОМПАСа
        ok / error   — статус разбора
    """
    path = Path(path)
    res: dict[str, Any] = {
        "file": path.name, "ok": False, "error": "",
        "marking": "", "name": "", "material": "", "density": None,
        "mass": None, "format": "", "revision": "", "sheets": None,
        "roughness": "", "author": "", "source_path": "",
        "library_refs": [], "tech_demands": [], "texts": [], "kompas": "",
    }

    if not is_cdw_supported(path):
        res["error"] = ("Не ZIP-контейнер: КОМПАС ниже v18 (формат OLE) "
                        "или файл повреждён")
        return res

    try:
        with zipfile.ZipFile(path) as z:
            res["kompas"] = _kompas_version(z)
            meta_xml = _decode_u16(z.read(STREAM_META))
            res["library_refs"] = _library_refs(meta_xml)
            res["tech_demands"] = _tech_demands(z)
            res["texts"] = _drawing_texts(z)

            root = ET.fromstring(meta_xml)
            # основной документ = <document mainSource="true"> внутри <product>
            doc = None
            for cand in root.iter("document"):
                if cand.get("mainSource") == "true" and cand.get("id"):
                    doc = cand
                    break
            if doc is None:
                res["error"] = "В MetaProductInfo не найден основной документ"
                return res

            flat = _flatten(doc)
            res["marking"] = _assemble_marking(flat)
            res["name"] = flat.get("name", "").strip()
            res["material"] = flat.get("material.name", "").strip()
            res["author"] = flat.get("author", "").strip()
            res["format"] = flat.get("format", "").strip()
            res["revision"] = flat.get("revisionLetter", "").strip()
            res["roughness"] = flat.get("specRoughValue", "").strip()
            res["source_path"] = flat.get("fullFileName", "").strip()

            for key, dst in (("mass", "mass"), ("material.density", "density")):
                try:
                    res[dst] = float(flat[key])
                except (KeyError, ValueError):
                    pass
            try:
                res["sheets"] = int(flat.get("sheetsNumber", ""))
            except ValueError:
                pass

            res["ok"] = bool(res["marking"] or res["name"])
            if not res["ok"]:
                res["error"] = "Обозначение и наименование пусты"
    except zipfile.BadZipFile:
        res["error"] = "Повреждённый ZIP-контейнер"
    except ET.ParseError as exc:
        res["error"] = f"Ошибка XML в MetaProductInfo: {exc}"
    except KeyError as exc:
        res["error"] = f"Нет обязательного потока: {exc}"
    except Exception as exc:                      # noqa: BLE001
        res["error"] = f"{type(exc).__name__}: {exc}"
    return res


def read_folder(folder: str | Path, pattern: str = "*.cdw") -> list[dict[str, Any]]:
    """Прочитать все чертежи в папке. Сбой одного файла не роняет пакет."""
    return [read_cdw(p) for p in sorted(Path(folder).glob(pattern))]


if __name__ == "__main__":
    import sys, json
    for arg in sys.argv[1:]:
        print(json.dumps(read_cdw(arg), ensure_ascii=False, indent=2))