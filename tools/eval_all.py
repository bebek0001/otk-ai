# -*- coding: utf-8 -*-
"""Сквозная оценка «агент против технолога» на данных data/eval/*.json.
Для каждой детали сравниваются: тип заготовки, сечение, длина заготовки, масса.
Запуск: python tools/eval_all.py [припуск_мм]"""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import cdw_dims
from core.assistant import model, excel_import


def truth_len(size: str):
    m = re.search(r"L\s*=\s*(\d+(?:[.,]\d+)?)", size or "")
    return float(m.group(1).replace(",", ".")) if m else None


def run(allowance: float):
    rows = []
    for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "eval", "*.json"))):
        rows += json.load(open(f, encoding="utf-8"))
    n = len(rows)
    stat = dict(n=n, stamp_has_sort=0, type_ok=0, type_by_stamp=0, type_by_model=0,
                section_ok=0, len_ok=0, len_n=0, row_ok=0, lens_exact_by_conf={"высокая": [0, 0], "низкая": [0, 0]})
    detail = []
    for r in rows:
        tr, st, dims = r["truth"], r["stamp"], r["dims"]
        tsec = cdw_dims.parse_section(tr["blank"])
        ttype = excel_import.stock_type_from_text(tr["blank"])
        sec = cdw_dims.parse_section(st["material"])
        if sec:
            stat["stamp_has_sort"] += 1
            ptype = excel_import.stock_type_from_text(st["material"])
            stat["type_by_stamp"] += 1
        else:
            s = model.suggest(st["name"], st["material"], None)
            ptype = s.candidates[0].stock_type if (s.candidates and not s.abstain) else None
            stat["type_by_model"] += 1
        t_ok = ptype == ttype
        stat["type_ok"] += t_ok
        sec_ok = bool(sec and tsec and sec.kind == tsec.kind and sec.sizes == tsec.sizes)
        stat["section_ok"] += sec_ok
        tl = truth_len(tr["size"])
        l_ok = None
        conf = None
        if sec and tl:
            stat["len_n"] += 1
            p = cdw_dims.propose_length(sec, dims, st["mass"] or 0.0, st["density"] or 7850.0, allowance)
            l_ok = bool(p.blank_length_mm == tl)
            conf = p.confidence if p.blank_length_mm else None
            stat["len_ok"] += l_ok
            if conf:
                stat["lens_exact_by_conf"][conf][1] += 1
                stat["lens_exact_by_conf"][conf][0] += l_ok
        row_ok = bool(t_ok and sec_ok and (l_ok if tl else True))
        stat["row_ok"] += row_ok
        detail.append((tr["drawing_no"][-3:], tr["name"], ttype, ptype, sec_ok, tl, l_ok, conf, row_ok))
    return stat, detail


if __name__ == "__main__":
    allow = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    stat, detail = run(allow)
    for d in detail:
        print(*d, sep=" | ")
    print(json.dumps(stat, ensure_ascii=False))
