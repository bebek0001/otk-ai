# -*- coding: utf-8 -*-
"""
MAKE_DB_RECORDS.PY — записи для drawing_db.json из эталонной таблицы заказчика.

Схема взята из твоей базы (73 записи, первая — 23.14.02.004 Тяга):
    drawing_no, part_name, stock_type, gost_stock, d_blank_std_mm,
    l_part_mm, l_blank_mm, material_mark, material_gost, mass_kg,
    result_line, pdf_text_hash, saved_at, mass_clean_kg

Запуск из корня проекта:

    # все 10 записей
    python3 tools/make_db_records.py -o ~/Desktop/db_290426_all.json

    # 8 в базу, .107 и .141 оставить на проверку
    python3 tools/make_db_records.py -o ~/Desktop/db_290426_train8.json \
        --exclude 290426.00.00.107 290426.00.00.141

    # сразу дописать в базу (делает резервную копию)
    python3 tools/make_db_records.py --append drawing_db.json \
        --exclude 290426.00.00.107 290426.00.00.141

Поле pdf_text_hash оставлено пустым: посчитать его для .cdw невозможно,
там нет текста PDF. Если lookup_etalon() ищет по хешу, эти записи не
найдутся — тогда нужно править поиск, а не базу.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

# ============================================================
# Эталонная таблица заказчика от 03.08.2026
# ------------------------------------------------------------
# stock_type / gost_stock — как их называет движок.
# d_blank_std_mm — первый (главный) размер профиля:
#     шестигранник — размер под ключ, полоса — толщина,
#     труба — наружный диаметр, лист — толщина,
#     квадрат — сторона, уголок — большая полка, круг — диаметр.
# size_line — вторая строка result_line: «L=150» для погонного проката,
#     «30х30» для листовой заготовки.
# stock_desc — первая строка result_line. Записана СЛОВО В СЛОВО как
#     в таблице заказчика, включая «КП205» и пробелы: по ней идёт сверка.
# ============================================================

ROWS = [
    dict(no="290426.00.00.002", name="Штырь направляющий",
         stock_type="Шестигранник", gost="ГОСТ2879-2006", d=24,
         stock_desc="Шестигранник 24 ГОСТ2879-2006", size_line="L=150",
         l_blank=150.0, mark="Ст2пс", mat_gost="ГОСТ535-2005",
         m_blank=0.6, m_clean=0.33),

    dict(no="290426.00.00.022", name="Планка",
         stock_type="Полоса", gost="ГОСТ103-2006", d=12,
         stock_desc="Полоса 12х45 ГОСТ103-2006", size_line="L=110",
         l_blank=110.0, mark="Ст3сп", mat_gost="ГОСТ535-2005",
         m_blank=0.47, m_clean=0.16),

    dict(no="290426.00.00.032", name="Труба",
         stock_type="ТрубаПроф", gost="ГОСТ32931-2015", d=159,
         stock_desc="Труба К-159х8-КП205-ГОСТ32931-2015", size_line="L=165",
         l_blank=165.0, mark="Ст3сп", mat_gost="ГОСТ380-2005",
         m_blank=4.9, m_clean=2.3),

    dict(no="290426.00.00.051", name="Шайба",
         stock_type="ЛистХК", gost="ГОСТ19904-90", d=5,
         stock_desc="Лист 5 ГОСТ19904-90", size_line="30х30",
         l_blank=None, mark="12Х18Н10Т", mat_gost="ГОСТ5582-75",
         m_blank=0.036, m_clean=0.005),

    dict(no="290426.00.00.062", name="Планка",
         stock_type="ЛистАл", gost="ГОСТ21631-2023", d=10,
         stock_desc="Лист АМг5 10 ГОСТ21631-2023", size_line="60х60",
         l_blank=None, mark="АМг5", mat_gost="ГОСТ4784-2019",
         m_blank=0.1, m_clean=0.02),

    dict(no="290426.00.00.072", name="Насадка",
         stock_type="Квадрат", gost="ГОСТ2591-2006", d=60,
         stock_desc="Квадрат 60 ГОСТ2591-2006", size_line="L=80",
         l_blank=80.0, mark="45", mat_gost="ГОСТ1050-2013",
         m_blank=2.3, m_clean=0.97),

    dict(no="290426.00.00.092", name="Планка",
         stock_type="ЛистГК", gost="ГОСТ19903-2015", d=16,
         stock_desc="Лист 16 ГОСТ19903-2015", size_line="95х32",
         l_blank=None, mark="45", mat_gost="ГОСТ1577-2022",
         m_blank=0.4, m_clean=0.07),

    dict(no="290426.00.00.107", name="Направляющая",
         stock_type="УголокНеравн", gost="ГОСТ8510-86", d=100,
         stock_desc="Уголок 100х63х10 ГОСТ8510-86", size_line="L=170",
         l_blank=170.0, mark="Ст3пс", mat_gost="ГОСТ380-2005",
         m_blank=2.1, m_clean=1.0),

    dict(no="290426.00.00.121", name="Вставка",
         stock_type="Труба", gost="ГОСТ8732-2025", d=108,
         stock_desc="Труба 108x20 ГОСТ8732-2025", size_line="L=110",
         l_blank=110.0, mark="В20", mat_gost="ГОСТ8731-2025",
         m_blank=4.8, m_clean=1.9),

    dict(no="290426.00.00.141", name="Ось правая",
         stock_type="Круг", gost="ГОСТ2590-2006", d=32,
         stock_desc="Круг 32 ГОСТ2590-2006", size_line="L=103",
         l_blank=103.0, mark="10Х17Н13М2Т", mat_gost="ГОСТ5632-2014",
         m_blank=0.7, m_clean=0.43),
]

# Надбавка по длине — как в engine.ADD_LENGTH_MM
ADD_LENGTH_MM = 20.0


def fmt_mass(v: float) -> str:
    """0.036 → '0,036', 4.9 → '4,90'. Мелкие массы не округляем до нуля."""
    s = f"{v:.2f}" if abs(v) >= 0.1 else f"{v:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def build(row: dict) -> dict:
    l_blank = row["l_blank"]
    l_part = (l_blank - ADD_LENGTH_MM) if l_blank else None
    return {
        "drawing_no":     row["no"],
        "part_name":      row["name"],
        "stock_type":     row["stock_type"],
        "gost_stock":     row["gost"],
        "d_blank_std_mm": row["d"],
        "l_part_mm":      l_part,
        "l_blank_mm":     l_blank,
        "material_mark":  row["mark"],
        "material_gost":  row["mat_gost"],
        "mass_kg":        row["m_blank"],
        "result_line": (
            f"{row['stock_desc']};\n"
            f"{row['size_line']};\n"
            f"Масса заготовки, кг: {fmt_mass(row['m_blank'])}"
        ),
        # Хеш текста PDF для .cdw посчитать нельзя — текста PDF не существует.
        "pdf_text_hash":  None,
        "saved_at":       datetime.datetime.now().isoformat(timespec="seconds"),
        "mass_clean_kg":  row["m_clean"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Записи для drawing_db.json из эталонной таблицы 03.08.2026")
    ap.add_argument("-o", "--out", help="куда записать JSON")
    ap.add_argument("--append", metavar="DB",
                    help="дописать прямо в базу (сделает резервную копию)")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="номера чертежей, которые НЕ включать (для проверки)")
    a = ap.parse_args()

    excl = set(a.exclude)
    recs = [build(r) for r in ROWS if r["no"] not in excl]

    print(f"Подготовлено записей: {len(recs)}")
    if excl:
        print(f"Исключено (оставлено на проверку): {', '.join(sorted(excl))}")
    for r in recs:
        print(f"   {r['drawing_no']}  {r['stock_type']:14s} "
              f"{r['result_line'].splitlines()[0]}")

    if a.append:
        if not os.path.isfile(a.append):
            print(f"Файл базы не найден: {a.append}")
            return 2
        with open(a.append, encoding="utf-8") as fh:
            db = json.load(fh)
        if not isinstance(db, list):
            print("Ожидался список записей — база другой структуры, "
                  "дописывать не буду.")
            return 2

        backup = a.append + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(a.append, backup)
        print(f"\nРезервная копия: {backup}")

        have = {r.get("drawing_no") for r in db}
        added = [r for r in recs if r["drawing_no"] not in have]
        skipped = len(recs) - len(added)
        db.extend(added)
        with open(a.append, "w", encoding="utf-8") as fh:
            json.dump(db, fh, ensure_ascii=False, indent=2)
        print(f"Добавлено: {len(added)}"
              + (f", пропущено как уже существующие: {skipped}" if skipped else ""))
        print(f"Всего в базе: {len(db)}")
        return 0

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False, indent=2)
        print(f"\nЗаписано → {a.out}")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())