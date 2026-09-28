# -*- coding: utf-8 -*-
"""
TEST_CDW.PY — проверка работоспособности на эталонной таблице заказчика.

Прогоняет чертежи через тот же конвейер, что и «Пакетная обработка»,
и сам сверяет результат с правильными ответами из таблицы
«Таблица ИИ деталей от 03.08.2026».

Запуск из корня проекта:

    python3 tests/test_cdw.py "/путь/к/папке/с/cdw"
    python3 tests/test_cdw.py "/путь/к/папке" --ai        # с ИИ-добором
    python3 tests/test_cdw.py --make-etalons etalons.json # выгрузить эталоны

Что показывает отчёт:
    ✔ / ✘ по каждому полю  — совпало с ответом заказчика или нет
    столбец «источник»     — откуда взят ответ: CDW / база / расчёт / ИИ

ВАЖНО про честность проверки. Если строка получена из слоя «база»,
это значит, что ответ был заранее записан в drawing_db.json — программа
его вспомнила, а не вывела. Такая строка НЕ доказывает работоспособность.
Настоящую работу показывают только строки со слоем «расчёт» или «ИИ».
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Правильные ответы заказчика (Таблица ИИ деталей от 03.08.2026)
# ============================================================

ETALON = [
    {"no": "290426.00.00.002", "name": "Штырь направляющий",
     "stock": "Шестигранник 24 ГОСТ 2879-2006", "material": "Ст2пс ГОСТ 535-2005",
     "size": "L=150", "m_blank": 0.6,   "m_clean": 0.33},
    {"no": "290426.00.00.022", "name": "Планка",
     "stock": "Полоса 12х45 ГОСТ 103-2006", "material": "Ст3сп ГОСТ 535-2005",
     "size": "L=110", "m_blank": 0.47,  "m_clean": 0.16},
    {"no": "290426.00.00.032", "name": "Труба",
     "stock": "Труба К-159х8-КП205-ГОСТ 32931-2015", "material": "",
     "size": "L=165", "m_blank": 4.9,   "m_clean": 2.3},
    {"no": "290426.00.00.051", "name": "Шайба",
     "stock": "Лист 5 ГОСТ 19904-90", "material": "12Х18Н10Т ГОСТ 5582-75",
     "size": "30х30", "m_blank": 0.036, "m_clean": 0.005},
    {"no": "290426.00.00.062", "name": "Планка",
     "stock": "Лист АМг5 10 ГОСТ 21631-2023", "material": "",
     "size": "60х60", "m_blank": 0.1,   "m_clean": 0.02},
    {"no": "290426.00.00.072", "name": "Насадка",
     "stock": "Квадрат 60 ГОСТ 2591-2006", "material": "45 ГОСТ 1050-2013",
     "size": "L=80",  "m_blank": 2.3,   "m_clean": 0.97},
    {"no": "290426.00.00.092", "name": "Планка",
     "stock": "Лист 16 ГОСТ 19903-2015", "material": "45 ГОСТ 1577-2022",
     "size": "95х32", "m_blank": 0.4,   "m_clean": 0.07},
    {"no": "290426.00.00.107", "name": "Направляющая",
     "stock": "Уголок 100х63х10 ГОСТ 8510-86", "material": "Ст3пс ГОСТ 8510-86",
     "size": "L=170", "m_blank": 2.1,   "m_clean": 1.0},
    {"no": "290426.00.00.121", "name": "Вставка",
     "stock": "Труба 108x20 ГОСТ 8732-2025", "material": "В 20 ГОСТ 8731-2025",
     "size": "L=110", "m_blank": 4.8,   "m_clean": 1.9},
    {"no": "290426.00.00.141", "name": "Ось правая",
     "stock": "Круг 32 ГОСТ 2590-2006", "material": "10Х17Н13М2Т ГОСТ 5632-2014",
     "size": "L=103", "m_blank": 0.7,   "m_clean": 0.43},
]

ETALON_BY_NO = {e["no"]: e for e in ETALON}


# ============================================================
# Нормализация для сравнения
# ============================================================

def norm_stock(s: str) -> str:
    """
    'Круг 32 ГОСТ 2590-2006' и 'Круг32ГОСТ2590' — одно и то же.
    Год ГОСТа игнорируем: сортамент тот же, редакция разная.
    """
    if not s:
        return ""
    s = s.split("/")[0]                       # отбрасываем материал после «/»
    s = s.lower().replace("х", "x").replace("ё", "е")
    s = re.sub(r"гост\s*", "гост", s)
    s = re.sub(r"(гост\d+)\s*[-–]\s*\d{2,4}", r"\1", s)   # ГОСТ2590-2006 → гост2590
    s = re.sub(r"[\s.,;]+", "", s)
    return s


def norm_size(s: str) -> str:
    """
    Приводит размер заготовки к каноническому виду.

    Заказчик пишет размер С ПРЕФИКСОМ сортамента:
        «Квадрат 60, L=80», «Лист  АМг5 10, 60х60»
    Движок может выдать и голый размер: «L=80», «60х60».
    Оба варианта верны, поэтому префикс до последней запятой отбрасываем
    и сравниваем только сам размер.
    """
    if not s:
        return ""
    if "," in s:
        s = s.rsplit(",", 1)[1]
    s = s.lower().replace("\u0445", "x").replace("\u25a1", "d")
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[\s.,;]+", "", s)
    s = re.sub(r"l=", "l", s)
    return s


def to_float(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d]+(?:[.,]\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def mass_close(got, want, rel: float = 0.10) -> bool:
    """Массы сравниваем с допуском 10 % — заказчик округляет."""
    g, w = to_float(got), to_float(want)
    if g is None or w is None:
        return False
    if w == 0:
        return abs(g) < 1e-9
    return abs(g - w) / abs(w) <= rel


def text_eq(got: str, want: str) -> bool:
    n = lambda s: re.sub(r"\s+", " ", (s or "")).strip().lower()
    return n(got) == n(want)


# ============================================================
# Отчёт
# ============================================================

MARK = {True: "✔", False: "✘", None: "·"}


def run(folder: str, use_ai: bool) -> int:
    from ui.screens import batch_tool as bt

    files = bt.scan_folder(folder)
    if not files:
        print(f"В папке нет .cdw и .pdf: {folder}")
        return 2

    print(f"Найдено чертежей: {len(files)}   ИИ: {'вкл' if use_ai else 'выкл'}")
    print("Обработка...\n")

    results = []
    for i, p in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {os.path.basename(p)}", flush=True)
        results.append(bt.process_one_file(p, use_ai=use_ai))
    print()

    score = {k: [0, 0] for k in
             ("№ чертежа", "Наимен.", "Сортамент", "Размер", "Масса заг.", "Масса чист.")}
    by_source = {}
    rows = []

    for r in results:
        e = ETALON_BY_NO.get(r.get("drawing_no", ""))
        src = r.get("source") or r.get("status", "")
        by_source[src] = by_source.get(src, 0) + 1

        if not e:
            rows.append((r.get("drawing_no", "") or r["file"],
                         r.get("part_name", ""), "нет в эталоне", "", "", "", "", src))
            continue

        checks = {
            "№ чертежа":   True,                                       # нашли по номеру
            "Наимен.":     text_eq(r.get("part_name", ""), e["name"]),
            "Сортамент":   norm_stock(r.get("new_sortament", "")) == norm_stock(e["stock"]),
            "Размер":      norm_size(r.get("stock_size", "")) == norm_size(e["size"]),
            "Масса заг.":  mass_close(r.get("stock_mass_kg"), e["m_blank"]),
            "Масса чист.": mass_close(r.get("clean_mass_kg"), e["m_clean"]),
        }
        for k, v in checks.items():
            score[k][1] += 1
            if v:
                score[k][0] += 1

        rows.append((
            e["no"], e["name"],
            MARK[checks["Наимен."]], MARK[checks["Сортамент"]], MARK[checks["Размер"]],
            MARK[checks["Масса заг."]], MARK[checks["Масса чист."]], src,
        ))

    # ---- таблица ----
    print("=" * 100)
    print(f"{'№ чертежа':<20}{'Наименование':<22}{'имя':>5}{'сорт':>6}{'разм':>6}"
          f"{'м.заг':>7}{'м.чист':>8}   источник")
    print("-" * 100)
    for row in rows:
        no, name, c1, c2, c3, c4, c5, src = row
        print(f"{no:<20}{name[:20]:<22}{c1:>5}{c2:>6}{c3:>6}{c4:>7}{c5:>8}   {src}")
    print("=" * 100)

    # ---- сводка ----
    print("\nСОВПАДЕНИЯ С ЭТАЛОНОМ ЗАКАЗЧИКА")
    total_ok = total_all = 0
    for k, (ok, all_) in score.items():
        if all_ == 0:
            continue
        pct = 100.0 * ok / all_
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {k:<14} {ok:>2}/{all_:<3} {bar} {pct:5.1f}%")
        if k != "№ чертежа":
            total_ok += ok
            total_all += all_
    if total_all:
        print(f"  {'ИТОГО':<14} {total_ok:>2}/{total_all:<3} "
              f"{100.0 * total_ok / total_all:5.1f}%")

    print("\nОТКУДА ВЗЯТЫ ОТВЕТЫ")
    for k, v in sorted(by_source.items(), key=lambda x: -x[1]):
        note = ""
        if k.endswith("база") or k == "база":
            note = "  ← ответ был в drawing_db.json, это не проверка, а память"
        print(f"  {k:<16} {v}{note}")

    # ---- расшифровка несовпадений ----
    bad = []
    for r in results:
        e = ETALON_BY_NO.get(r.get("drawing_no", ""))
        if not e:
            continue
        if norm_stock(r.get("new_sortament", "")) != norm_stock(e["stock"]):
            bad.append((e["no"], "сортамент",
                        r.get("new_sortament", "") or f"— ({r.get('error','')})",
                        e["stock"]))
        if norm_size(r.get("stock_size", "")) != norm_size(e["size"]):
            bad.append((e["no"], "размер", r.get("stock_size", "") or "—", e["size"]))
        if not mass_close(r.get("stock_mass_kg"), e["m_blank"]):
            bad.append((e["no"], "масса заг.",
                        r.get("stock_mass_kg", "") or "—", e["m_blank"]))

    if bad:
        print("\nЧТО НЕ СОШЛОСЬ")
        for no, field, got, want in bad:
            print(f"  {no}  {field:<12} получено: {str(got)[:40]:<42} надо: {want}")

    return 0 if total_all and total_ok == total_all else 1


# ============================================================
# Выгрузка эталонов для drawing_db.json
# ============================================================

def fmt_mass(v: float) -> str:
    """0.036 → '0,036', 4.9 → '4,90', 1.0 → '1,00'. Мелкие массы не округляем до нуля."""
    s = f"{v:.2f}" if abs(v) >= 0.1 else f"{v:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def make_etalons(out_path: str) -> int:
    """
    Готовит записи в формате базы эталонов. НЕ пишет в drawing_db.json —
    сначала просмотри файл глазами, потом влей вручную.
    """
    recs = []
    for e in ETALON:
        mat = e["material"]
        m = re.match(r"^(.*?)\s*(ГОСТ\s*[\d\-–]+)$", mat) if mat else None
        recs.append({
            "drawing_no":    e["no"],
            "part_name":     e["name"],
            "material_mark": (m.group(1).strip() if m else mat),
            "material_gost": (m.group(2).replace(" ", "") if m else ""),
            "mass_clean_kg": e["m_clean"],
            "result_line": (
                f"{e['stock']};\n"
                f"{e['size']};\n"
                f"Масса заготовки, кг: {fmt_mass(e['m_blank'])}"
            ),
        })
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(recs, fh, ensure_ascii=False, indent=2)
    print(f"Записано эталонов: {len(recs)} → {out_path}")
    print("Проверь файл глазами и влей в drawing_db.json вручную.")
    return 0


# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка OTK.AI на эталонной таблице")
    ap.add_argument("folder", nargs="?", help="папка с .cdw / .pdf")
    ap.add_argument("--ai", action="store_true", help="включить ИИ-добор")
    ap.add_argument("--make-etalons", metavar="FILE",
                    help="выгрузить эталоны в JSON и выйти")
    a = ap.parse_args()

    if a.make_etalons:
        return make_etalons(a.make_etalons)
    if not a.folder:
        ap.print_help()
        return 2
    if not os.path.isdir(a.folder):
        print(f"Папка не найдена: {a.folder}")
        return 2
    return run(a.folder, a.ai)


if __name__ == "__main__":
    sys.exit(main())