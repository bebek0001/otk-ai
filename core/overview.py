# -*- coding: utf-8 -*-
"""
Данные для вкладки «Обзор»: что программа видит в файле, что лежит в базе
и по каким правилам принимаются решения. Только логика и данные, без окна,
чтобы можно было проверить тестами и показать одинаково в окне и в отчёте.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import trace

Field = Tuple[str, str]


# --------------------------------------------------------------------------- #
#  1. Что программа прочитала из файла
# --------------------------------------------------------------------------- #

def _f(label: str, value: Any) -> Field:
    return (label, "" if value in (None, "") else str(value))


def read_file(path: str) -> Dict[str, Any]:
    """Поля, прочитанные из CDW, M3D/A3D или PDF. Ничего не меняет в базе."""
    ext = Path(path).suffix.lower()
    out: Dict[str, Any] = {"kind": "", "fields": [], "geometry": [], "error": ""}
    try:
        if ext == ".cdw":
            out["kind"] = "CDW (чертёж КОМПАС)"
            _read_cdw(path, out)
        elif ext in (".m3d", ".a3d"):
            out["kind"] = "M3D/A3D (модель КОМПАС)"
            _read_m3d(path, out)
        elif ext == ".pdf":
            out["kind"] = "PDF (чертёж)"
            _read_pdf(path, out)
        else:
            out["error"] = f"Формат {ext or 'без расширения'} не поддерживается"
    except Exception as e:                                       # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _read_cdw(path: str, out: Dict[str, Any]) -> None:
    from core import cdw_contour, cdw_dims
    from core.cdw_reader import read_cdw
    c = read_cdw(path)
    if not c.get("ok"):
        out["error"] = c.get("error") or "Файл не прочитан"
    out["fields"] = [
        _f("Обозначение", c.get("marking")),
        _f("Наименование", c.get("name")),
        _f("Материал (штамп)", c.get("material")),
        _f("Сортамент из библиотеки", "; ".join(c.get("library_refs") or [])),
        _f("Масса чистая, кг", c.get("mass")),
        _f("Плотность, кг/м3", c.get("density")),
        _f("Неуказанная шероховатость", c.get("roughness")),
        _f("Формат / листов", f"{c.get('format') or '-'} / {c.get('sheets') or '-'}"),
        _f("Литера", c.get("revision")),
        _f("Разработал", c.get("author")),
        _f("Версия КОМПАС", c.get("kompas")),
        _f("Технические требования", " | ".join((c.get("tech_demands") or [])[:3])),
    ]
    geo: List[str] = []
    dims = cdw_dims.dimension_values(path)
    geo.append(f"Размерных значений найдено: {len(dims)}" +
               (f"; наибольшие: {', '.join(f'{v:g}' for v in dims[-8:])}" if dims else ""))
    mat0 = (c.get("material") or "").split(";")[0].strip()
    try:
        geo += cdw_contour.describe(path, mat0, c.get("mass"), c.get("density"))
    except Exception as e:                                       # noqa: BLE001
        geo.append(f"Контур не разобран: {e}")
    out["geometry"] = geo
    out["card"] = c


def _read_m3d(path: str, out: Dict[str, Any]) -> None:
    from core.m3d_reader import read_m3d
    m = read_m3d(path)
    if m is None:
        out["error"] = "Свойства модели не прочитаны"
        return
    out["fields"] = [
        _f("Обозначение", m.drawing_no),
        _f("Наименование", m.part_name),
        _f("Материал", m.material),
        _f("Марка / ГОСТ материала", f"{m.material_mark} / {m.material_gost}"),
        _f("Плотность, кг/м3", m.density),
        _f("Масса, кг", m.mass_kg),
        _f("Литера", m.revision_letter),
        _f("Раздел спецификации", m.spec_section),
        _f("Разработал", m.author),
        _f("Организация", m.organization),
        _f("Версия КОМПАС", m.kompas_version),
        _f("Изменён", m.modified),
    ]
    out["geometry"] = ["Геометрия модели закрыта форматом КОМПАС: длина и диаметр из файла недоступны, "
                       "размеры заготовки определяются только по базе"]


def _read_pdf(path: str, out: Dict[str, Any]) -> None:
    from core.engine import extract_from_pdf
    ex = extract_from_pdf(path, max_pages=3)
    text = getattr(ex, "pdf_text", "") or ""
    out["fields"] = [
        _f("Наименование", getattr(ex, "part_name", "")),
        _f("Марка материала", getattr(ex, "material_mark", "")),
        _f("ГОСТ материала", getattr(ex, "material_gost", "")),
        _f("Длина детали, мм", getattr(ex, "length_mm", "")),
        _f("Символов текста распознано", len(text)),
    ]
    out["geometry"] = ["Начало распознанного текста: " + " ".join(text.split())[:300]]


# --------------------------------------------------------------------------- #
#  2. Как принято решение
# --------------------------------------------------------------------------- #

def run_trace(path: str, runner: Callable[[str], Dict[str, Any]]) -> Dict[str, Any]:
    """Обработать файл и вернуть этапы решения. runner(path) -> результат строки."""
    trace.collect_start()
    try:
        result = runner(path)
    finally:
        steps = trace.collect_end()
    return {"steps": steps, "result": result}


def result_lines(result: Dict[str, Any]) -> List[Field]:
    return [
        _f("Статус", result.get("status")),
        _f("Источник ответа", result.get("source")),
        _f("Сортамент", result.get("new_sortament")),
        _f("Размер заготовки", result.get("stock_size")),
        _f("Масса заготовки, кг", result.get("stock_mass_kg")),
        _f("Масса детали, кг", result.get("clean_mass_kg")),
        _f("Уровень доверия агента", f"{result.get('agent_level', '')} ({result.get('agent_rule', '') or 'нет правила'})"),
        _f("Примечание", result.get("error") or result.get("note")),
    ]


# --------------------------------------------------------------------------- #
#  3. База эталонов
# --------------------------------------------------------------------------- #

def _origin(rec: Dict[str, Any]) -> str:
    h = str(rec.get("pdf_text_hash", ""))
    return "таблица технолога" if h.startswith("excel:") else "PDF"


def base_rows(db: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if db is None:
        from core import drawing_db
        db = drawing_db.load_db()
    rows = []
    for e in db:
        lines = [x.strip().rstrip(";") for x in str(e.get("result_line", "")).splitlines() if x.strip()]
        rows.append({
            "drawing_no": e.get("drawing_no", ""),
            "part_name": e.get("part_name", ""),
            "stock_type": e.get("stock_type", ""),
            "sortament": lines[0] if lines else "",
            "size": lines[1] if len(lines) > 1 else "",
            "mass_blank": e.get("mass_kg"),
            "mass_clean": e.get("mass_clean_kg"),
            "origin": _origin(e),
            "saved_at": str(e.get("saved_at", ""))[:10],
        })
    rows.sort(key=lambda r: r["drawing_no"])
    return rows


def base_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    by_origin: Dict[str, int] = {}
    for r in rows:
        by_type[r["stock_type"] or "не указан"] = by_type.get(r["stock_type"] or "не указан", 0) + 1
        by_origin[r["origin"]] = by_origin.get(r["origin"], 0) + 1
    return {"total": len(rows), "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "by_origin": by_origin,
            "with_clean_mass": sum(1 for r in rows if r["mass_clean"])}


def filter_rows(rows: List[Dict[str, Any]], text: str = "", stock_type: str = "") -> List[Dict[str, Any]]:
    t = (text or "").strip().lower()
    out = []
    for r in rows:
        if stock_type and stock_type != "Все" and r["stock_type"] != stock_type:
            continue
        if t and t not in " ".join(str(v) for v in r.values()).lower():
            continue
        out.append(r)
    return out


BASE_COLUMNS = (("drawing_no", "Обозначение"), ("part_name", "Наименование"), ("stock_type", "Тип"),
                ("sortament", "Сортамент"), ("size", "Размер заготовки"), ("mass_blank", "Масса заг., кг"),
                ("mass_clean", "Масса дет., кг"), ("origin", "Источник"), ("saved_at", "Дата"))


def export_base_xlsx(path: str, rows: List[Dict[str, Any]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "База эталонов"
    ws.append([title for _, title in BASE_COLUMNS])
    for r in rows:
        ws.append([r.get(k, "") for k, _ in BASE_COLUMNS])
    for col, w in zip("ABCDEFGHI", (22, 28, 14, 44, 28, 14, 14, 18, 12)):
        ws.column_dimensions[col].width = w
    wb.save(path)


# --------------------------------------------------------------------------- #
#  4. Алгоритмы и накопленная статистика
# --------------------------------------------------------------------------- #

def pipeline_text() -> List[str]:
    """Порядок решения с действующими параметрами (берутся из кода, а не из текста)."""
    from core import agent, allowance, analogs, cdw_contour, cdw_dims, crosscheck
    return [
        "Порядок решения для каждого файла (первый сработавший шаг даёт ответ):",
        "1. Чтение файла. CDW: свойства документа и штамп КОМПАС читаются напрямую, КОМПАС не нужен. "
        "Если обозначение или наименование в свойствах пусты, берутся из имени файла.",
        "2. Сборочные чертежи пропускаются.",
        f"3. База эталонов: ответ технолога по обозначению чертежа. Штамп сверяется с записью "
        f"(вид проката, масса с допуском {crosscheck.MASS_TOL:.0%}); расхождение помечается.",
        f"4. Формулы по ГОСТ: если длина детали известна, заготовка считается по сортаменту и "
        f"стандартным размерам.",
        f"5. Аналог из базы: то же наименование, тот же сортамент, чистая масса в пределах "
        f"{analogs.MASS_TOL:.0%}; при совпадении размеров у нескольких аналогов уверенность выше.",
        f"6. Контур листа: габарит из геометрии CDW, проверка по массе (прямоугольник "
        f"{cdw_contour.RECT_MASS_RANGE[0]}-{cdw_contour.RECT_MASS_RANGE[1]} к чистой, "
        f"габарит точек {cdw_contour.BBOX_MASS_RANGE[0]}-{cdw_contour.BBOX_MASS_RANGE[1]}). "
        f"Шероховатость: Ra 50 и грубее без припуска, Ra {cdw_contour.RA_ALLOWANCE_MAX:g} и точнее "
        f"+{cdw_contour.SIDE_ALLOWANCE_MM:g} мм на сторону.",
        f"7. Длина проката по размерам чертежа: кандидаты проверяются по массе (заготовка не легче "
        f"детали и не тяжелее {cdw_dims.MAX_BLANK_TO_CLEAN:g} раз), припуск по длине берётся из "
        f"исправлений технолога, затем из базы, затем из настроек ({cdw_dims.DEFAULT_ALLOWANCE_MM:g} мм).",
        f"8. Агент: уровень «авто» правило получает при точности (нижняя граница Уилсона) не ниже "
        f"{agent.MIN_LOWER_BOUND:.0%} на {agent.MIN_N} и более проверенных ответах; предположения "
        f"(аналог, контур, длина по размерам, ИИ) всегда идут на проверку.",
        f"9. Обучение: исправленная технологом таблица пополняет базу; припуск выучивается при "
        f"{allowance.MIN_N}+ согласованных наблюдениях (разброс до {allowance.MAX_MAD:g} мм).",
    ]


def allowance_table() -> List[Dict[str, Any]]:
    from core import allowance, drawing_db
    import statistics
    tech = allowance._load()
    base = allowance.from_db(drawing_db.load_db())
    rows = []
    for kind in sorted(set(tech) | set(base)):
        def med(v):
            return f"{statistics.median(v):g}" if v else "-"
        val, n, learned = allowance.get(kind, 10.0)
        src = "исправления технолога" if allowance._consistent(tech.get(kind, [])) is not None else (
            "база эталонов" if allowance._consistent(base.get(kind, [])) is not None else "настройки")
        rows.append({"kind": kind, "tech_n": len(tech.get(kind, [])), "tech_med": med(tech.get(kind, [])),
                     "base_n": len(base.get(kind, [])), "base_med": med(base.get(kind, [])),
                     "value": f"{val:g}", "source": src})
    return rows


def agent_table() -> List[Dict[str, Any]]:
    from core import agent
    stats = agent.load_stats()
    rows = []
    for rule in sorted(set(stats) | set(agent.RULES.values())):
        s = stats.get(rule, {})
        n, c = int(s.get("n", 0)), int(s.get("correct", 0))
        rows.append({"rule": rule, "n": n, "correct": c,
                     "lower": f"{agent.wilson_lower(c, n):.2f}" if n else "-",
                     "level": agent.autonomy(rule, stats)})
    return rows


def report_text(file_info: Dict[str, Any], trace_info: Optional[Dict[str, Any]], name: str = "") -> str:
    """Один текстовый отчёт по файлу: что прочитано, как решено, что в итоге."""
    L: List[str] = [f"Файл: {name}", f"Формат: {file_info.get('kind', '')}", ""]
    if file_info.get("error"):
        L += [f"Ошибка чтения: {file_info['error']}", ""]
    L.append("1. Что программа прочитала")
    L += [f"  {k}: {v or '-'}" for k, v in file_info.get("fields", [])]
    if file_info.get("geometry"):
        L += ["", "   Геометрия:"] + [f"  {g}" for g in file_info["geometry"]]
    if trace_info:
        L += ["", "2. Как принято решение"]
        L += [f"  {i}. {st}: {msg}" for i, (st, msg) in enumerate(trace_info["steps"], 1)]
        L += ["", "3. Итог"] + [f"  {k}: {v or '-'}" for k, v in result_lines(trace_info["result"])]
    return "\n".join(L) + "\n"
