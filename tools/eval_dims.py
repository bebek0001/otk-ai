# -*- coding: utf-8 -*-
"""Проверка предложения длины на папке с CDW и таблицей ответов технолога (.docx).
Запуск: python tools/eval_dims.py <папка>"""
import glob, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docx
from core import cdw_reader, cdw_dims

def main(folder):
    t = docx.Document(glob.glob(os.path.join(folder, "*.docx"))[0]).tables[0]
    truth = {}
    for r in t.rows[1:]:
        c = [x.text.strip() for x in r.cells]
        truth[c[1][-3:]] = (c[3], c[4], float(c[5].replace(",", ".")))
    good = tot = 0
    for f in sorted(glob.glob(os.path.join(folder, "*.cdw"))):
        card = cdw_reader.read_cdw(f)
        k = card["marking"][-3:]
        if k not in truth:
            continue
        sort, size, bm = truth[k]
        sec = cdw_dims.parse_section(sort)
        m = re.search(r"L=(\d+)", size)
        L = float(m.group(1)) if m else None
        dens = card.get("density") or 7850.0
        pr = cdw_dims.propose_length(sec, cdw_dims.dimension_values(f), card["mass"], dens) if sec else None
        tot += 1
        hit = bool(pr and L and pr.blank_length_mm == L)
        good += hit
        print(k, card["name"], sort[:28], "| ответ L", L, "| предложено",
              pr and pr.blank_length_mm, pr and pr.confidence, "|", "OK" if hit else "--",
              "|", pr and pr.note)
    print(f"совпало {good} из {tot}")

main(sys.argv[1])
