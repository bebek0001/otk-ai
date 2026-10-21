# -*- coding: utf-8 -*-
"""
Сверка папки чертежей CDW с базой эталонов (PDF и таблицы технолога).

    python tools/check_base_vs_cdw.py <папка_с_CDW> [отчёт.xlsx]

Для каждого CDW ищется запись базы по номеру чертежа и сравниваются сортамент
и масса из штампа. Показывает, какая часть базы подтверждается чертежами и
где данные расходятся.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import crosscheck, drawing_db
from core.cdw_reader import read_cdw


def main(folder, out=None):
    rows = []
    for p in sorted(Path(folder).rglob("*.cdw")):
        try:
            card = read_cdw(p)
        except Exception as e:                                   # noqa: BLE001
            rows.append((p.name, "", "ошибка чтения", str(e)[:80]))
            continue
        no = card.get("marking", "")
        et = drawing_db.lookup_by_drawing_no(no) if no else None
        if not et:
            rows.append((p.name, no, "нет в базе", ""))
            continue
        notes = crosscheck.compare(card, et)
        rows.append((p.name, no, "расходится" if notes else "совпало", "; ".join(notes)))
    total = len(rows)
    for st in ("совпало", "расходится", "нет в базе", "ошибка чтения"):
        print(f"{st}: {sum(1 for r in rows if r[2] == st)} из {total}")
    for r in rows:
        if r[2] in ("расходится", "ошибка чтения"):
            print(" ", r[1] or r[0], "|", r[3])
    if out:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Файл", "Обозначение", "Результат", "Расхождение"])
        for r in rows:
            ws.append(list(r))
        wb.save(out)
        print("Отчёт:", out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
