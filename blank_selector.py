# ===========================
# ЧАСТЬ 1/5
# ===========================

import os
import re
import sys
import math
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Tuple
import threading

from openai import OpenAI

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None


# ============================================================
# НАСТРОЙКИ / БАЗА
# ============================================================

GOST_DIR = "gosts"
DEFAULT_STEEL_DENSITY_KG_M3 = 7850.0

# У вас ожидаемо: 50 -> 54
ADD_LENGTH_MM = 4.0

# Масса: приоритет штампа (как "факт"), если найдена
MASS_MODE = "stamp_first"  # stamp_first / physics_only

# Типы заготовок -> ГОСТ сортамента (PDF в gosts/)
STOCKTYPE_TO_GOST = {
    "Круг": "ГОСТ2590-2006",
    "Квадрат": "ГОСТ2591-2006",
    "ЛистГК": "ГОСТ19903-2015",
    "ЛистХК": "ГОСТ19904-90",
    "Швеллер": "ГОСТ8240-97",
    "Двутавр": "ГОСТ35087-2024",
    "Труба": "ГОСТ53383-2009",
    "ТрубаПроф": "ГОСТ32931-2015",
}

ROUND_DIAMETERS_MM = [
    5, 6, 7, 8, 9, 10, 11, 12,
    13, 14, 15, 16, 17, 18, 19, 20,
    21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
    32, 34, 36, 38, 40, 42, 45, 48, 50,
    55, 60, 65, 70, 75, 80, 85, 90, 95, 100
]

# Нормализация "Ст3пс/сп" -> "Ст3"
MATERIAL_NORMALIZE_TO_BASE = True

# Табличный припуск (если совпало — используем)
ALLOWANCE_TABLE = [
    (20.0, "g6", 3.2, 1.10),
]


# ============================================================
# МОДЕЛИ
# ============================================================

@dataclass
class DiameterFeature:
    d_mm: float
    tol: Optional[str]


@dataclass
class Extracted:
    pdf_text: str
    part_name: str
    looks_rotational: bool

    material_mark: Optional[str]
    material_gost: Optional[str]

    stamp_mass_kg: Optional[float]

    diameter_features: List[DiameterFeature]
    max_d_mm: Optional[float]
    max_d_tol: Optional[str]

    square_features_mm: List[float]     # размеры со звёздочкой (20* и т.п.)
    max_square_mm: Optional[float]

    ra_values: List[float]
    length_mm: Optional[float]          # длина детали (если нашли)


@dataclass
class CalcResult:
    stock_type: str
    gost_stock: str
    gost_pdf_path: str

    material_out: str
    material_gost: str

    d_part_mm: Optional[float]
    tol: Optional[str]
    ra: Optional[float]

    tube_od_mm: Optional[float]
    tube_wall_mm: Optional[float]

    square_mm: Optional[float]

    allowance_side_mm: float
    d_blank_calc_mm: float
    d_blank_std_mm: int

    l_part_mm: float
    l_blank_mm: float

    density_kg_m3: float
    mass_kg: float
    mass_physics_kg: float
    mass_stamp_kg: Optional[float]

    result_line: str
    protocol: str


# ============================================================
# OpenAI
# ============================================================

def ai_ask(prompt: str, model: str = "gpt-4o-mini") -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=360,
    )
    return resp.choices[0].message.content.strip()

# ===========================
# ЧАСТЬ 2/5
# ===========================

# ============================================================
# УТИЛИТЫ + РАБОТА С ГОСТ PDF (локально)
# ============================================================

def normalize_mark(s: str) -> str:
    s = re.sub(r"\s+", "", (s or ""))
    s = s.replace("ст", "Ст").replace("СТ", "Ст")
    return s


def normalize_gost(s: str) -> str:
    s = (s or "").strip().replace(" ", "")
    s = s.replace("гост", "ГОСТ").replace("Гост", "ГОСТ")
    return s


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def open_with_default_app(path: str) -> None:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path)

    if sys.platform.startswith("darwin"):
        subprocess.run(["open", path], check=False)
    elif os.name == "nt":
        os.startfile(path)  # type: ignore
    else:
        subprocess.run(["xdg-open", path], check=False)


def find_gost_pdf(gost_code: str, gost_dir: str = GOST_DIR) -> str:
    base_dir = os.path.join(os.path.dirname(__file__), gost_dir)
    if not os.path.isdir(base_dir):
        return ""

    key = gost_code.replace("ГОСТ", "").strip().lower()

    for fn in os.listdir(base_dir):
        if not fn.lower().endswith(".pdf"):
            continue
        if key in fn.lower():
            p = os.path.join(base_dir, fn)
            if os.path.exists(p):
                return p
    return ""


def extract_text_from_any_pdf(pdf_path: str, max_pages: int = 6) -> str:
    if fitz is None:
        return ""
    if not pdf_path or not os.path.exists(pdf_path):
        return ""
    doc = fitz.open(pdf_path)
    texts = []
    pages_to_read = min(len(doc), max_pages)
    for i in range(pages_to_read):
        try:
            texts.append(doc[i].get_text("text"))
        except Exception:
            pass
    doc.close()
    return "\n".join(texts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def pick_relevant_snippets(text: str, keywords: List[str], max_snippets: int = 3, snippet_len: int = 420) -> List[str]:
    if not text:
        return []
    t = text.replace("\r", "\n")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    if not lines:
        return []
    kws = [_norm(k) for k in keywords if k and k.strip()]
    if not kws:
        return []

    scored = []
    for i, ln in enumerate(lines):
        ln_n = _norm(ln)
        score = 0
        for k in kws:
            if k in ln_n:
                score += 1
        if score > 0:
            start = max(0, i - 1)
            end = min(len(lines), i + 2)
            chunk = " ".join(lines[start:end])
            chunk = re.sub(r"\s+", " ", chunk).strip()
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    uniq = []
    seen = set()
    for score, chunk in scored:
        key = chunk[:180]
        if key in seen:
            continue
        seen.add(key)
        if len(chunk) > snippet_len:
            chunk = chunk[:snippet_len].rstrip() + "…"
        uniq.append(chunk)
        if len(uniq) >= max_snippets:
            break
    return uniq


def build_gost_sources_block(calc_res: CalcResult, extracted: Extracted) -> str:
    sources = []

    # сортамент
    if calc_res.gost_stock:
        p = find_gost_pdf(calc_res.gost_stock, GOST_DIR)
        txt = extract_text_from_any_pdf(p, max_pages=6)

        kw = ["сортамент", "размеры", "предельные", "отклонения", "допуски"]
        if calc_res.stock_type == "Круг":
            kw += ["круг", "диаметр", str(calc_res.d_blank_std_mm)]
        elif calc_res.stock_type == "Квадрат":
            kw += ["квадрат", "сторона"]
        elif calc_res.stock_type == "Труба":
            kw += ["трубы", "наружный", "толщина"]
        elif calc_res.stock_type in {"ЛистХК", "ЛистГК"}:
            kw += ["лист", "толщина"]

        snippets = pick_relevant_snippets(txt, kw, max_snippets=3)
        if snippets:
            sources.append((calc_res.gost_stock, snippets))

    # материал (если PDF есть)
    mat_gost = calc_res.material_gost or extracted.material_gost or ""
    if mat_gost:
        p = find_gost_pdf(mat_gost, GOST_DIR)
        txt = extract_text_from_any_pdf(p, max_pages=6)
        snippets = pick_relevant_snippets(
            txt, ["сталь", "марка", "химический", "механические", "свойства", "ст3"], max_snippets=3
        )
        if snippets:
            sources.append((mat_gost, snippets))

    # шероховатость
    rough_gost = "ГОСТ2.309-73"
    p = find_gost_pdf(rough_gost, GOST_DIR)
    if p:
        txt = extract_text_from_any_pdf(p, max_pages=6)
        snippets = pick_relevant_snippets(txt, ["шероховат", "ra", "обознач", "поверхн"], max_snippets=2)
        if snippets:
            sources.append((rough_gost, snippets))

    if not sources:
        return "ИСТОЧНИКИ: (локальные ГОСТы не найдены/не извлечён текст)\n"

    out = ["ИСТОЧНИКИ (выдержки из локальных PDF ГОСТ):"]
    for gost_name, snippets in sources:
        out.append(f"\n[{gost_name}]")
        for s in snippets:
            out.append(f"- {s}")
        out.append("")
    return "\n".join(out)


# ============================================================
# ЧИСТКА ТЕКСТА ДЛЯ РАЗМЕРОВ (убираем ГОСТ/масштаб/массу/коды)
# ============================================================

def _strip_gost_and_codes(text: str) -> str:
    if not text:
        return ""

    # выкидываем строки с массой/форматом/масштабом
    lines = text.splitlines()
    keep = []
    for ln in lines:
        ll = ln.lower()
        if "масса" in ll or "масштаб" in ll or "формат" in ll:
            continue
        keep.append(ln)
    t = "\n".join(keep)

    # масштаб 1:1, 2:1, 1:2 (часто без слова "масштаб")
    t = re.sub(r"\b\d+\s*:\s*\d+\b", " ", t)

    # ГОСТ 16523-97
    t = re.sub(r"\bГОСТ\s*\d+\s*[-–]\s*\d+\b", " ", t, flags=re.IGNORECASE)

    # 16523-97 без слова ГОСТ
    t = re.sub(r"\b\d{4,6}\s*[-–]\s*\d{2,4}\b", " ", t)

    # обозначения типа 129986.01.00.001
    t = re.sub(r"\b\d{5,}\.\d+\.\d+\.\d+\b", " ", t)

    # длинные номера
    t = re.sub(r"\b\d{6,}\b", " ", t)

    return t


def _extract_all_numbers_clean(text: str) -> List[float]:
    t2 = _strip_gost_and_codes(text or "")
    out: List[float] = []
    for m in re.finditer(r"\b(\d{1,4}(?:[.,]\d+)?)\b", t2):
        v = safe_float(m.group(1))
        if v is None:
            continue
        if 1900 <= v <= 2100:
            continue
        if v <= 0 or v > 3000:
            continue
        out.append(v)
    return out

# ===========================
# ЧАСТЬ 3/5
# ===========================

# ============================================================
# PDF: ИЗВЛЕЧЕНИЕ ТЕКСТА ЧЕРТЕЖА
# ============================================================

def extract_text_from_pdf(pdf_path: str, max_pages: int = 2) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF не установлен. Установи: python3 -m pip install pymupdf")
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    doc = fitz.open(pdf_path)
    texts = []
    pages_to_read = min(len(doc), max_pages)
    for i in range(pages_to_read):
        try:
            texts.append(doc[i].get_text("text"))
        except Exception:
            pass
    doc.close()
    return "\n".join(texts)


# ============================================================
# ПАРСИНГ ЧЕРТЕЖА
# ============================================================

def find_material_mark_and_gost(text: str) -> Tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None

    m = re.search(r"\b(Ст\s*\d+(?:сп|пс)?)\s*(ГОСТ)\s*([0-9]+[-–][0-9]+)\b", text, flags=re.IGNORECASE)
    if m:
        mark = normalize_mark(m.group(1))
        gost = normalize_gost(f"{m.group(2)}{m.group(3)}")
        return mark, gost

    m2 = re.search(r"\b(Ст\s*\d+(?:сп|пс)?)\s*([0-9]+[-–][0-9]+)\b", text, flags=re.IGNORECASE)
    if m2:
        mark = normalize_mark(m2.group(1))
        gost = normalize_gost("ГОСТ" + m2.group(2))
        return mark, gost

    return None, None


def find_stamp_mass_kg(text: str) -> Optional[float]:
    """
    Масса в штампе: ищем по строкам, где встречается "масс".
    Так ловим разные форматы: "Масса 0,02", "Масса, кг 0,02", "Масса=0,02".
    """
    if not text:
        return None

    for ln in text.splitlines():
        if "масс" not in ln.lower():
            continue
        m = re.search(r"(\d+(?:[.,]\d+)?)", ln)
        if not m:
            continue
        v = safe_float(m.group(1))
        if v is None:
            continue
        if 0.001 <= v <= 2000:
            return v

    return None


def find_ra_values(text: str) -> List[float]:
    if not text:
        return []
    out: List[float] = []
    for m in re.finditer(r"\bRa\s*(\d+(?:[.,]\d+)?)", text, flags=re.IGNORECASE):
        v = safe_float(m.group(1))
        if v is not None:
            out.append(v)
    return out


def find_square_sizes(text: str) -> List[float]:
    """
    В вытащенном тексте размеров часто появляется "20*" вместо "20".
    Собираем такие как потенциальные квадрат/толщина/прочее.
    """
    if not text:
        return []
    out: List[float] = []
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*\*", text):
        v = safe_float(m.group(1))
        if v is None:
            continue
        if v < 0.5:
            continue
        out.append(v)

    uniq: List[float] = []
    for v in out:
        if not any(abs(v - u) < 1e-6 for u in uniq):
            uniq.append(v)
    return uniq


def find_diameters_with_tolerance(text: str, looks_rotational: bool) -> List[DiameterFeature]:
    if not text:
        return []

    feats: List[DiameterFeature] = []

    pat_tol = r"(?:⌀|Ø|Ç)\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]\s*\d{1,2})"
    for m in re.finditer(pat_tol, text):
        d = safe_float(m.group(1))
        if d is None:
            continue
        tol = re.sub(r"\s+", "", m.group(2)).lower()
        feats.append(DiameterFeature(d_mm=float(d), tol=tol))

    pat_plain = r"(?:⌀|Ø|Ç)\s*(\d+(?:[.,]\d+)?)"
    for m in re.finditer(pat_plain, text):
        d = safe_float(m.group(1))
        if d is None:
            continue
        if not any(abs(f.d_mm - d) < 1e-9 for f in feats):
            feats.append(DiameterFeature(d_mm=float(d), tol=None))

    for m in re.finditer(r"\bD\s*=\s*(\d+(?:[.,]\d+)?)", text, flags=re.IGNORECASE):
        d = safe_float(m.group(1))
        if d is None:
            continue
        if not any(abs(f.d_mm - d) < 1e-9 for f in feats):
            feats.append(DiameterFeature(d_mm=float(d), tol=None))

    if looks_rotational:
        has_any_diameter_symbol = bool(re.search(r"[⌀ØÇ]", text))
        if has_any_diameter_symbol or re.search(r"\b(ось|вал|втулк)\b", text, flags=re.IGNORECASE):
            for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*([a-zA-Z]\s*\d{1,2})\b", text):
                d = safe_float(m.group(1))
                if d is None:
                    continue
                if 1900 <= d <= 2100:
                    continue
                tol = re.sub(r"\s+", "", m.group(2)).lower()
                if not any(abs(f.d_mm - d) < 1e-9 for f in feats):
                    feats.append(DiameterFeature(d_mm=float(d), tol=tol))

    return feats


def find_length_mm(text: str, diameters: List[float], squares: List[float]) -> Optional[float]:
    """
    1) Ищем L= / Длина ...
    2) Иначе берём максимальный габарит из очищенного текста.
    """
    if not text:
        return None

    patterns = [
        r"\bL\s*=\s*(\d+(?:[.,]\d+)?)\s*мм?\b",
        r"\bДлин[аы]\s*(\d+(?:[.,]\d+)?)\s*мм?\b",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            v = safe_float(m.group(1))
            if v and v > 0:
                return v

    nums = _extract_all_numbers_clean(text)
    if not nums:
        return None

    dia_set = set(round(d, 4) for d in (diameters or []))
    sq_set = set(round(s, 4) for s in (squares or []))

    candidates: List[float] = []
    for v in nums:
        if v < 5:
            continue
        rv = round(v, 4)
        if rv in dia_set:
            continue
        if rv in sq_set:
            continue
        candidates.append(v)

    if not candidates:
        return None

    return max(candidates)


def extract_from_pdf(pdf_path: str, max_pages: int = 2) -> Extracted:
    pdf_text = extract_text_from_pdf(pdf_path, max_pages=max_pages)
    part_name = os.path.splitext(os.path.basename(pdf_path))[0]

    t = pdf_text.lower()
    n = part_name.lower()
    looks_rot = any(w in n for w in ["ось", "вал", "втулк"]) or any(w in t for w in ["ось", "вал", "втулк"])

    mark, gost = find_material_mark_and_gost(pdf_text)
    stamp_mass = find_stamp_mass_kg(pdf_text)

    diam_feats = find_diameters_with_tolerance(pdf_text, looks_rotational=looks_rot)
    ra_vals = find_ra_values(pdf_text)

    square_feats = find_square_sizes(pdf_text)

    diam_list = [f.d_mm for f in diam_feats]
    l = find_length_mm(pdf_text, diameters=diam_list, squares=square_feats)

    if diam_feats:
        max_feat = max(diam_feats, key=lambda f: f.d_mm)
        max_d = max_feat.d_mm
        max_tol = max_feat.tol
    else:
        max_d = None
        max_tol = None

    max_sq = max(square_feats) if square_feats else None

    return Extracted(
        pdf_text=pdf_text,
        part_name=part_name,
        looks_rotational=looks_rot,

        material_mark=mark,
        material_gost=gost,

        stamp_mass_kg=stamp_mass,

        diameter_features=diam_feats,
        max_d_mm=max_d,
        max_d_tol=max_tol,

        square_features_mm=square_feats,
        max_square_mm=max_sq,

        ra_values=ra_vals,
        length_mm=l,
    )

# ===========================
# ЧАСТЬ 4/5
# ===========================

# ============================================================
# ПРОВЕРКА: ЕСТЬ ЛИ ЗАГОТОВКА В ТЕКСТЕ ЧЕРТЕЖА?
# ============================================================

def has_stock_spec_in_drawing(pdf_text: str) -> bool:
    t = (pdf_text or "").lower()

    if any(k in t for k in ["заготов", "прокат", "сортамент", "загот."]):
        return True

    if re.search(r"\b(круг|квадрат|лист|швеллер|двутавр|труба)\b.*\bгост\s*\d", t):
        return True

    if "материал заготов" in t:
        return True

    return False


def find_exact_stock_mentions(pdf_text: str, descr: str) -> List[str]:
    """
    Ищет упоминания именно нашего ОПИСАНИЯ (без привязки к ГОСТ),
    иначе любые ГОСТы в штампе дают ложные совпадения.
    """
    t = (pdf_text or "")
    tl = t.lower()

    variants = [
        descr.lower(),
        descr.lower().replace(" ", ""),
    ]
    if not any(v in tl for v in variants):
        return []

    hits: List[str] = []
    for line in t.splitlines():
        ll = line.lower()
        if any(v in ll for v in variants):
            line_clean = re.sub(r"\s+", " ", line).strip()
            if line_clean and line_clean not in hits:
                hits.append(line_clean)
        if len(hits) >= 6:
            break
    return hits


# ============================================================
# ЛОГИКА ПОДБОРА: выбор типа + расчёты масс/строк
# ============================================================

def pick_gost_for_stock(stock_type: str) -> str:
    gost = STOCKTYPE_TO_GOST.get(stock_type)
    if not gost:
        raise ValueError(f"Нет правила ГОСТа для типа: {stock_type}")
    return normalize_gost(gost)


def choose_ra_to_use(ra_values: List[float]) -> Optional[float]:
    return min(ra_values) if ra_values else None


def allowance_side_from_table(d_part_mm: float, tol: Optional[str], ra: Optional[float]) -> Optional[float]:
    if ra is None:
        return None
    tol_norm = (tol or "").lower()
    for max_d, tol_key, ra_max, allow in ALLOWANCE_TABLE:
        if d_part_mm <= max_d and tol_norm == tol_key and ra <= ra_max:
            return float(allow)
    return None


def allowance_side_base(d_part_mm: float, tol: Optional[str], ra: Optional[float]) -> float:
    if d_part_mm <= 20:
        base = 0.5
    elif d_part_mm <= 50:
        base = 0.8
    elif d_part_mm <= 100:
        base = 1.2
    else:
        base = 1.5

    tol_norm = (tol or "").lower()
    if tol_norm in {"g6", "h6", "js6", "k6", "f6", "e6"}:
        base += 0.2

    if ra is not None:
        if ra <= 1.6:
            base += 0.3
        elif ra <= 3.2:
            base += 0.1

    return round(base, 2)


def pick_round_diameter(required_d_mm: float) -> int:
    for d in ROUND_DIAMETERS_MM:
        if d >= required_d_mm:
            return int(d)
    return int(ROUND_DIAMETERS_MM[-1])


def calc_cylinder_mass_kg(d_mm: float, l_mm: float, density_kg_m3: float) -> float:
    d_m = d_mm / 1000.0
    l_m = l_mm / 1000.0
    volume_m3 = math.pi * (d_m ** 2) / 4.0 * l_m
    return density_kg_m3 * volume_m3


def calc_tube_mass_kg(od_mm: float, wall_mm: float, l_mm: float, density_kg_m3: float) -> float:
    d_mm = od_mm - 2.0 * wall_mm
    if d_mm < 0:
        d_mm = 0
    D_m = od_mm / 1000.0
    d_m = d_mm / 1000.0
    L_m = l_mm / 1000.0
    volume_m3 = (math.pi / 4.0) * (D_m * D_m - d_m * d_m) * L_m
    return density_kg_m3 * volume_m3


def calc_square_bar_mass_kg(a_mm: float, l_mm: float, density_kg_m3: float) -> float:
    a_m = a_mm / 1000.0
    l_m = l_mm / 1000.0
    volume_m3 = a_m * a_m * l_m
    return density_kg_m3 * volume_m3


def calc_sheet_mass_kg(t_mm: float, b_mm: float, l_mm: float, density_kg_m3: float) -> float:
    t_m = t_mm / 1000.0
    b_m = b_mm / 1000.0
    l_m = l_mm / 1000.0
    volume_m3 = t_m * b_m * l_m
    return density_kg_m3 * volume_m3


def mass_str_ru(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def build_result_block_bar(stock_desc: str, l_blank: float, mass_kg: float) -> str:
    # как у вас для оси/втулки/бруса
    return (
        f"{stock_desc};\n"
        f"L={int(l_blank)};\n"
        f"Масса заготовки, кг: {mass_str_ru(mass_kg)}"
    )


def build_result_block_sheet(t_mm: float, b_mm: float, l_blank: float, mass_kg: float) -> str:
    # как у вас для "Платик": "Лист 2; 24х54; Масса ..."
    return (
        f"Лист {int(round(t_mm))};\n"
        f"{int(round(b_mm))}х{int(round(l_blank))};\n"
        f"Масса заготовки, кг: {mass_str_ru(mass_kg)}"
    )


def detect_stock_type(ex: Extracted) -> str:
    """
    Поддержано в расчёте:
      - Круг, Труба, Квадрат, ЛистХК/ЛистГК
    """
    name_l = (ex.part_name or "").lower()
    text_l = (ex.pdf_text or "").lower()

    dia_vals = sorted({round(f.d_mm, 6) for f in ex.diameter_features})
    has_diam = len(dia_vals) > 0
    has_2_diam = len(dia_vals) >= 2

    # втулка/полая/2 диаметра -> труба
    if ("втулк" in name_l) or ("втулк" in text_l) or has_2_diam:
        return "Труба"

    # платик/пластина -> лист (тип уточним по толщине)
    if ("платик" in name_l) or ("пластин" in name_l) or ("платик" in text_l) or ("пластин" in text_l):
        return "ЛистХК"

    # квадрат: есть 20* и Ø нет
    if (ex.max_square_mm is not None) and (not has_diam):
        if ex.max_square_mm >= 8:
            return "Квадрат"

    return "Круг"


def calculate_missing_stock_line(ex: Extracted, l_part_mm: float, material_mark: str, material_gost: str) -> CalcResult:
    stock_type = detect_stock_type(ex)

    gost_stock = pick_gost_for_stock(stock_type)
    gost_pdf = find_gost_pdf(gost_stock, GOST_DIR)

    density = DEFAULT_STEEL_DENSITY_KG_M3
    l_blank = l_part_mm + ADD_LENGTH_MM

    material_out = material_mark
    if MATERIAL_NORMALIZE_TO_BASE:
        material_out = re.sub(r"(Ст\s*\d+)(?:пс|сп)\b", r"\1", material_out, flags=re.IGNORECASE)
        material_out = normalize_mark(material_out)

    ra = choose_ra_to_use(ex.ra_values)

    d_part = ex.max_d_mm
    tol = ex.max_d_tol

    tube_od = None
    tube_wall = None
    sq = None

    allowance_side = 0.0
    d_blank_calc = 0.0
    d_blank_std = 0

    mass_physics = 0.0
    mass_stamp = ex.stamp_mass_kg
    mass_final = 0.0

    result_line = ""

    prot: List[str] = []
    prot.append("Протокол подбора НЕДОСТАЮЩЕЙ заготовки")
    prot.append(f"- Файл: {ex.part_name}")
    prot.append(f"- Признаки заготовки в тексте чертежа: {'ДА' if has_stock_spec_in_drawing(ex.pdf_text) else 'НЕТ'}")
    prot.append(f"- Авто-тип заготовки: {stock_type}")
    prot.append(f"- ГОСТ сортамента: {gost_stock}" + (f" (файл: {gost_pdf})" if gost_pdf else f" (PDF не найден в {GOST_DIR}/)"))
    prot.append(f"- Материал (из штампа/ввода): {material_mark} {material_gost}")
    prot.append(f"- Материал (в выдаче): {material_out}{material_gost} (нормализация={'on' if MATERIAL_NORMALIZE_TO_BASE else 'off'})")
    prot.append(f"- Масса в штампе: {mass_stamp}" if mass_stamp is not None else "- Масса в штампе: не найдена")
    prot.append(f"- Ra (найдено): {ra}" if ra is not None else "- Ra не найдено")
    prot.append(f"- Длина детали L: {l_part_mm} мм")
    prot.append(f"- Надбавка по длине: +{ADD_LENGTH_MM} мм")
    prot.append(f"- Длина заготовки Lзаг: {l_blank} мм")

    if stock_type == "Круг":
        if ex.max_d_mm is None:
            raise ValueError("Не найден диаметр (Ø) в PDF. Для круга это критично.")
        d_part = float(ex.max_d_mm)
        tol = ex.max_d_tol

        tab_allow = allowance_side_from_table(d_part, tol, ra)
        if tab_allow is not None:
            allowance_side = tab_allow
            allow_src = "table"
        else:
            allowance_side = allowance_side_base(d_part, tol, ra)
            allow_src = "base"

        d_blank_calc = d_part + 2.0 * allowance_side
        d_blank_std = pick_round_diameter(d_blank_calc)

        mass_physics = calc_cylinder_mass_kg(d_blank_std, l_blank, density)

        if MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = f"stamp_first (physics={mass_physics:.6f})"
        else:
            mass_final = mass_physics
            mass_note = "physics_only"

        stock_desc = f"Круг {d_blank_std} {gost_stock}"
        result_line = build_result_block_bar(stock_desc, l_blank, mass_final)

        prot.append(f"- Диаметр детали: {d_part} мм" + (f" (допуск: {tol})" if tol else ""))
        prot.append(f"- Припуск на сторону: {allowance_side} мм (source={allow_src})")
        prot.append(f"- Диаметр заготовки расчётный: {d_blank_calc:.3f} мм")
        prot.append(f"- Диаметр заготовки по сортаменту: {d_blank_std} мм")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    elif stock_type == "Труба":
        dia_vals2 = sorted({f.d_mm for f in ex.diameter_features})
        if len(dia_vals2) < 2:
            raise ValueError("Для трубы нужны минимум 2 диаметра (наружный и внутренний).")

        tube_od = float(max(dia_vals2))
        tube_id = float(min(dia_vals2))
        tube_wall = round((tube_od - tube_id) / 2.0, 3)
        if tube_wall <= 0:
            raise ValueError("Толщина стенки трубы получилась <= 0. Проверь диаметры.")

        d_blank_calc = tube_od
        d_blank_std = int(round(tube_od))

        mass_physics = calc_tube_mass_kg(tube_od, tube_wall, l_blank, density)

        if MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = f"stamp_first (physics={mass_physics:.6f})"
        else:
            mass_final = mass_physics
            mass_note = "physics_only"

        stock_desc = f"Труба {int(round(tube_od))}x{int(round(tube_wall))} {gost_stock}"
        result_line = build_result_block_bar(stock_desc, l_blank, mass_final)

        prot.append(f"- Диаметры: наружн={tube_od} мм, внутр={tube_id} мм, стенка={tube_wall} мм")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    elif stock_type == "Квадрат":
        if ex.max_square_mm is None:
            raise ValueError("Не найден размер квадрата (например '20*') в PDF. Для квадрата это критично.")
        sq = float(ex.max_square_mm)

        d_blank_calc = sq
        d_blank_std = int(round(sq))

        mass_physics = calc_square_bar_mass_kg(sq, l_blank, density)

        if MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = f"stamp_first (physics={mass_physics:.6f})"
        else:
            mass_final = mass_physics
            mass_note = "physics_only"

        stock_desc = f"Квадрат {int(round(sq))} {gost_stock}"
        result_line = build_result_block_bar(stock_desc, l_blank, mass_final)

        prot.append(f"- Сторона квадрата: {sq} мм")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    elif stock_type in {"ЛистХК", "ЛистГК"}:
        nums = _extract_all_numbers_clean(ex.pdf_text)

        # Толщина: целые 1..20, но "1" часто масштаб 1:1 -> если есть 2, берём 2
        small_int = sorted({int(round(v)) for v in nums if 1 <= v <= 20 and abs(v - round(v)) < 1e-6})
        if not small_int:
            raise ValueError("Не удалось определить толщину листа из размеров чертежа.")

        if 2 in small_int:
            sheet_t = 2.0
        else:
            ge2 = [x for x in small_int if x >= 2]
            sheet_t = float(min(ge2)) if ge2 else float(min(small_int))

        # уточняем тип листа + ГОСТ
        if sheet_t <= 3.0:
            stock_type = "ЛистХК"
        else:
            stock_type = "ЛистГК"
        gost_stock = pick_gost_for_stock(stock_type)
        gost_pdf = find_gost_pdf(gost_stock, GOST_DIR)

        # Ширина: берём "второй габарит" (для Платика 24), отсекая мусор типа 10
        cand = [v for v in nums if v >= 10 and v <= 300]
        cand = [v for v in cand if abs(v - sheet_t) > 1e-6 and abs(v - l_part_mm) > 1e-6]

        # предпочтение: >=20 и < длины детали (чтобы 24 победило 10)
        prefer = [v for v in cand if v >= 20 and v < l_part_mm]
        if prefer:
            sheet_b = float(max(prefer))
        else:
            # fallback: что осталось
            sheet_b = float(max(cand)) if cand else None

        if sheet_b is None:
            raise ValueError("Не удалось определить ширину листа.")

        mass_physics = calc_sheet_mass_kg(sheet_t, sheet_b, l_blank, density)

        if MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = "stamp_first"
        else:
            mass_final = mass_physics
            mass_note = "physics_only"

        # ВЫВОД КАК У ВАС (без ГОСТ в первой строке)
        result_line = build_result_block_sheet(sheet_t, sheet_b, l_blank, mass_final)

        prot.append(f"- Лист: толщина={sheet_t} мм, ширина={sheet_b} мм")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note}, physics={mass_physics:.6f})")

    else:
        raise ValueError(f"Тип заготовки пока не поддержан: {stock_type}")

    preview = re.sub(r"\s+", " ", ex.pdf_text).strip()
    preview = preview[:520] + (" ..." if len(preview) > 520 else "")
    prot.append("")
    prot.append("Текст PDF (фрагмент):")
    prot.append(preview)

    return CalcResult(
        stock_type=stock_type,
        gost_stock=gost_stock,
        gost_pdf_path=gost_pdf,

        material_out=material_out,
        material_gost=material_gost,

        d_part_mm=d_part,
        tol=tol,
        ra=ra,

        tube_od_mm=tube_od,
        tube_wall_mm=tube_wall,

        square_mm=sq,

        allowance_side_mm=allowance_side,
        d_blank_calc_mm=d_blank_calc,
        d_blank_std_mm=d_blank_std,

        l_part_mm=l_part_mm,
        l_blank_mm=l_blank,

        density_kg_m3=density,
        mass_kg=mass_final,
        mass_physics_kg=mass_physics,
        mass_stamp_kg=mass_stamp,

        result_line=result_line,
        protocol="\n".join(prot),
    )

# ===========================
# ЧАСТЬ 5/5
# ===========================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Определение недостающей заготовки по чертежу")
        self.geometry("1040x680")
        self.minsize(1040, 680)

        self.pdf_path: Optional[str] = None
        self.extracted: Optional[Extracted] = None
        self.last_result: Optional[CalcResult] = None
        self.missing_line: Optional[str] = None

        self._build_ui()

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("aqua")
        except Exception:
            pass

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        self.lbl_pdf = ttk.Label(top, text="PDF не выбран")
        self.lbl_pdf.pack(side="left", fill="x", expand=True)

        ttk.Button(top, text="Выбрать PDF…", command=self.on_pick_pdf).pack(side="right")

        frm = ttk.LabelFrame(self, text="Данные из чертежа (штамп) + ввод", padding=12)
        frm.pack(fill="x", padx=12, pady=(0, 12))

        ttk.Label(frm, text="Марка материала (штамп):").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        self.var_mark = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_mark, width=28).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(frm, text="ГОСТ материала (штамп):").grid(row=0, column=2, sticky="w", padx=(20, 10), pady=6)
        self.var_mat_gost = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_mat_gost, width=20).grid(row=0, column=3, sticky="w", pady=6)

        ttk.Label(frm, text="Размеры из PDF (инфо):").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        self.var_dim_info = tk.StringVar(value="(не считано)")
        ttk.Entry(frm, textvariable=self.var_dim_info, width=78, state="readonly").grid(
            row=1, column=1, columnspan=3, sticky="w", pady=6
        )

        ttk.Label(frm, text="Длина детали L, мм (ввод, можно пусто):").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=6
        )
        self.var_l = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_l, width=28).grid(row=2, column=1, sticky="w", pady=6)

        frm.grid_columnconfigure(4, weight=1)

        btns = ttk.Frame(self, padding=(12, 0, 12, 12))
        btns.pack(fill="x")

        self.btn_read = ttk.Button(btns, text="Считать из PDF", command=self.on_read_pdf, state="disabled")
        self.btn_read.pack(side="left")

        self.btn_calc = ttk.Button(btns, text="Найти недостающую заготовку", command=self.on_calculate, state="disabled")
        self.btn_calc.pack(side="left", padx=8)

        self.btn_ai = ttk.Button(btns, text="ИИ: подтвердить (по ГОСТ)", command=self.on_ai, state="disabled")
        self.btn_ai.pack(side="left", padx=8)

        self.btn_open_gost = ttk.Button(btns, text="Открыть ГОСТ сортамента", command=self.on_open_gost, state="disabled")
        self.btn_open_gost.pack(side="left", padx=8)

        self.btn_copy = ttk.Button(btns, text="Копировать результат", command=self.on_copy, state="disabled")
        self.btn_copy.pack(side="left", padx=8)

        out = ttk.PanedWindow(self, orient="horizontal")
        out.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = ttk.Labelframe(out, text="Что отсутствует на чертеже", padding=8)
        right = ttk.Labelframe(out, text="Протокол", padding=8)
        out.add(left, weight=1)
        out.add(right, weight=2)

        self.txt_result = tk.Text(left, height=10, wrap="word")
        self.txt_result.pack(fill="both", expand=True)

        self.txt_protocol = tk.Text(right, wrap="word")
        self.txt_protocol.pack(fill="both", expand=True)

    def on_pick_pdf(self):
        path = filedialog.askopenfilename(
            title="Выбери PDF чертёж",
            filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return

        self.pdf_path = path
        self.lbl_pdf.config(text=os.path.basename(path))

        self.btn_read.config(state="normal")
        self.btn_calc.config(state="normal")

        self.extracted = None
        self.last_result = None
        self.missing_line = None
        self._clear_output()
        self.var_dim_info.set("(не считано)")

    def on_read_pdf(self):
        if not self.pdf_path:
            return
        if fitz is None:
            messagebox.showerror("Нет PyMuPDF", "Установи: python3 -m pip install pymupdf")
            return

        try:
            self.extracted = extract_from_pdf(self.pdf_path, max_pages=2)
        except Exception as e:
            messagebox.showerror("Ошибка чтения PDF", str(e))
            return

        if self.extracted.material_mark:
            self.var_mark.set(self.extracted.material_mark)
        if self.extracted.material_gost:
            self.var_mat_gost.set(self.extracted.material_gost)

        diams = [f.d_mm for f in self.extracted.diameter_features]
        sqs = self.extracted.square_features_mm
        l = self.extracted.length_mm
        m = self.extracted.stamp_mass_kg

        parts = []
        if diams:
            parts.append("Ø: " + ", ".join(str(int(d)) if abs(d - int(d)) < 1e-6 else str(d) for d in sorted(set(diams))))
        if sqs:
            parts.append("*: " + ", ".join(str(int(s)) if abs(s - int(s)) < 1e-6 else str(s) for s in sorted(set(sqs))))
        if l is not None:
            parts.append(f"L(из PDF)={l}")
        if m is not None:
            parts.append(f"Масса(штамп)={m}")

        self.var_dim_info.set("; ".join(parts) if parts else "(размеры не распознаны)")

        messagebox.showinfo("Готово", "Данные считаны. Если L пустое — возьмём длину из PDF (если нашли).")

    def _build_search_descr(self, res: CalcResult) -> str:
        """
        Описание для поиска упоминаний в тексте чертежа (без ГОСТ).
        """
        if res.stock_type == "Круг":
            return f"Круг {res.d_blank_std_mm}"
        if res.stock_type == "Квадрат":
            return f"Квадрат {int(round(res.square_mm))}" if res.square_mm is not None else "Квадрат"
        if res.stock_type == "Труба":
            if res.tube_od_mm is not None and res.tube_wall_mm is not None:
                return f"Труба {int(round(res.tube_od_mm))}x{int(round(res.tube_wall_mm))}"
            return "Труба"
        if res.stock_type in {"ЛистХК", "ЛистГК"}:
            # ищем "Лист 2" или "Лист2"
            m = re.search(r"Лист\s*(\d+)", res.result_line, flags=re.IGNORECASE)
            if m:
                return f"Лист {m.group(1)}"
            return "Лист"
        return res.stock_type

    def on_calculate(self):
        if not self.pdf_path:
            messagebox.showwarning("Нет файла", "Сначала выбери PDF.")
            return

        if self.extracted is None:
            try:
                self.extracted = extract_from_pdf(self.pdf_path, max_pages=2)
            except Exception as e:
                messagebox.showerror("Ошибка чтения PDF", str(e))
                return

        mark = normalize_mark(self.var_mark.get())
        mat_gost = normalize_gost(self.var_mat_gost.get())

        # L: если ввели — берём ввод; иначе берём из PDF
        l_part = safe_float(self.var_l.get())
        if l_part is None:
            l_part = self.extracted.length_mm

        if not mark:
            messagebox.showwarning("Не хватает данных", "Не найдена/не введена марка материала из штампа.")
            return
        if not mat_gost:
            messagebox.showwarning("Не хватает данных", "Не найден/не введён ГОСТ материала из штампа.")
            return
        if l_part is None or l_part <= 0:
            messagebox.showwarning("Не хватает данных", "Не найдена длина L. Введи L вручную.")
            return

        try:
            res = calculate_missing_stock_line(self.extracted, l_part, mark, mat_gost)
        except Exception as e:
            messagebox.showerror("Ошибка подбора", str(e))
            return

        self.last_result = res

        descr = self._build_search_descr(res)
        mentions = find_exact_stock_mentions(self.extracted.pdf_text, descr)

        if mentions:
            self.missing_line = "ПРОВЕРЬ: на чертеже найдено упоминание похожей заготовки.\n\n" + res.result_line
        else:
            self.missing_line = "НЕТ НА ЧЕРТЕЖЕ:\n\n" + res.result_line

        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("1.0", self.missing_line)

        self.txt_protocol.delete("1.0", "end")
        self.txt_protocol.insert("1.0", res.protocol)

        self.btn_copy.config(state="normal")
        self.btn_open_gost.config(state="normal" if res.gost_pdf_path else "disabled")
        self.btn_ai.config(state="normal")

    def on_ai(self):
        if not self.extracted or not self.last_result:
            return

        res = self.last_result
        sources_block = build_gost_sources_block(res, self.extracted)

        descr = self._build_search_descr(res)
        mentions = find_exact_stock_mentions(self.extracted.pdf_text, descr)
        mentions_block = "\n".join(f"- {m}" for m in mentions) if mentions else "(не найдено совпадений в тексте чертежа)"

        missing_text = self.missing_line or ("НЕТ НА ЧЕРТЕЖЕ:\n\n" + res.result_line)

        mass_str = mass_str_ru(res.mass_kg)
        l_blank = int(res.l_blank_mm)

        ctrl = [f"тип={res.stock_type}", f"L={l_blank}", f"m={mass_str} кг"]
        if res.stock_type == "Круг":
            ctrl.append(f"D={res.d_blank_std_mm} мм")
        if res.stock_type == "Труба" and res.tube_od_mm and res.tube_wall_mm:
            ctrl.append(f"ODxS={int(round(res.tube_od_mm))}x{int(round(res.tube_wall_mm))}")
        if res.stock_type == "Квадрат" and res.square_mm:
            ctrl.append(f"a={int(round(res.square_mm))} мм")
        if res.stock_type in {"ЛистХК", "ЛистГК"}:
            # ИИ пусть подтверждает по ГОСТ листа и по штампу массы
            pass

        prompt = (
            "Ты технолог ОТК. Проверь строго по фактам: отсутствует ли на чертеже ИМЕННО эта заготовка, "
            "и соответствует ли выбранная строка ГОСТ сортамента.\n"
            "Отвечай без воды.\n\n"
            "Формат ответа (строго 4 строки):\n"
            "1) Чертеж: ЕСТЬ/НЕТ упоминание этой заготовки (покажи найденные строки или 'не найдено')\n"
            "2) Недостает на чертеже: <скопируй строку заготовки ровно как дана>\n"
            "3) ГОСТ: подтвердить сортамент (ссылка только [ГОСТxxxx-xxxx])\n"
            "4) Масса: подтвердить m (если масса из штампа — 'соответствует штампу', иначе 'соответствует расчету')\n\n"
            f"Проверяемая выдача приложения:\n{missing_text}\n\n"
            "Найденные совпадения в тексте чертежа:\n"
            f"{mentions_block}\n\n"
            f"Контрольные данные: {', '.join(ctrl)}\n\n"
            f"{sources_block}\n"
        )

        self.btn_ai.config(state="disabled")
        self.txt_protocol.insert("end", "\n\n[ИИ] Формальная проверка...\n")
        self.txt_protocol.see("end")

        def worker():
            try:
                answer = ai_ask(prompt)
            except Exception as e:
                answer = f"[ИИ] Ошибка: {e}"

            def ui_update():
                self.txt_protocol.insert("end", "\n[ИИ] Подтверждение:\n" + answer + "\n")
                self.txt_protocol.see("end")
                self.btn_ai.config(state="normal")

            self.after(0, ui_update)

        threading.Thread(target=worker, daemon=True).start()

    def on_open_gost(self):
        if self.last_result and self.last_result.gost_pdf_path:
            try:
                open_with_default_app(self.last_result.gost_pdf_path)
            except Exception as e:
                messagebox.showerror("Не открылось", str(e))

    def on_copy(self):
        if not self.missing_line:
            return
        self.clipboard_clear()
        self.clipboard_append(self.missing_line)
        messagebox.showinfo("Скопировано", "Результат скопирован в буфер обмена.")

    def _clear_output(self):
        self.txt_result.delete("1.0", "end")
        self.txt_protocol.delete("1.0", "end")
        self.btn_copy.config(state="disabled")
        self.btn_open_gost.config(state="disabled")
        self.btn_ai.config(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

    