# ===========================
# ENGINE.PY — с логами + RAG-слой (поиск ответа по базе эталонов)
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
from pathlib import Path
from core import logger

# --- RAG: семантический поиск ответа по базе эталонов ---
# Импорт безопасный: если модуля/интернета нет, движок работает как раньше (на формулах).
try:
    from core import rag_search
    _RAG_AVAILABLE = True
except Exception as _e:
    rag_search = None
    _RAG_AVAILABLE = False
    logger.warn(f"RAG не загружен, работаю на формулах: {_e}")

# Включатель RAG-слоя. Если True — для нового чертежа сначала пробуем
# найти ответ по похожим эталонам из базы, и только при неудаче считаем формулами.
USE_RAG = True
# Порог уверенности: насколько похожим должен быть найденный образец,
# чтобы доверять ответу RAG (0..1). Ниже порога — откат на формулы.
RAG_MIN_SCORE = 0.35
# Включатель Vision: GPT-4o СМОТРИТ на картинку чертежа + образцы из базы.
# Это самый точный режим для новых чертежей, но дороже (картинка в запросе).
# Применяется только если найден точный путь к PDF.
USE_VISION = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOST_DIR = str(PROJECT_ROOT / "gosts")

try:
    import fitz  # PyMuPDF
    logger.info(f"PyMuPDF загружен успешно")
except Exception as e:
    fitz = None
    logger.warn(f"PyMuPDF не загружен: {e}")


# ============================================================
# НАСТРОЙКИ / БАЗА
# ============================================================

DEFAULT_STEEL_DENSITY_KG_M3 = 7850.0
ADD_LENGTH_MM = 20.0
MASS_MODE = "stamp_first"

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
    55, 60, 65, 70, 75, 80, 85, 90, 95, 100,
    105, 110, 115, 120, 125, 130, 140, 150,
    160, 170, 180, 190, 200, 210, 220, 240, 250,
    260, 270, 280, 290, 300, 320, 340, 360, 380, 400,
]

MATERIAL_NORMALIZE_TO_BASE = True

# Погонный вес швеллеров по ГОСТ 8240-97 (кг за 1 метр профиля).
# Ключ — номер швеллера (высота/10). Значения одинаковы для серий У и П
# (масса 1 м зависит от номера, не от типа полок в пределах точности).
SHVELLER_KG_PER_M = {
    5: 4.84, 6.5: 5.90, 8: 7.05, 10: 8.59, 12: 10.4, 14: 12.3,
    16: 14.2, 18: 16.3, 20: 18.4, 22: 21.0, 24: 24.0, 27: 27.7,
    30: 31.8, 33: 36.5, 36: 41.9, 40: 48.3,
}


def shveller_mass_kg(prof_no: int, l_blank_mm: float) -> Optional[float]:
    """
    Точная масса швеллера-заготовки по ГОСТ 8240:
    погонный вес (кг/м) * длина заготовки (м).
    prof_no — номер швеллера (напр. 16), l_blank_mm — длина заготовки в мм.
    Возвращает массу в кг или None, если номер не из сортамента.
    """
    w = SHVELLER_KG_PER_M.get(prof_no)
    if w is None:
        # ближайший стандартный номер (на случай 16а→16 и т.п.)
        nums = sorted(SHVELLER_KG_PER_M.keys())
        cand = [n for n in nums if abs(n - prof_no) < 1.5]
        if cand:
            w = SHVELLER_KG_PER_M[min(cand, key=lambda n: abs(n - prof_no))]
    if w is None:
        return None
    return round(w * (l_blank_mm / 1000.0), 2)


# Стандартные толщины листа по ГОСТ 19903 (горячекатаный лист)
STANDARD_SHEET_THICKNESSES_MM = [
    4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 25,
    28, 30, 32, 35, 36, 38, 40, 45, 50, 55, 60, 65, 70, 75,
    80, 85, 90, 95, 100, 105, 110, 120, 125, 130, 140, 150,
    160, 170, 180, 190, 200,
]


def pick_standard_sheet_thickness(required_mm: float) -> int:
    """Выбирает ближайшую стандартную толщину листа >= required_mm."""
    for t in STANDARD_SHEET_THICKNESSES_MM:
        if t >= required_mm:
            return t
    return STANDARD_SHEET_THICKNESSES_MM[-1]


def find_sheet_thickness_from_drawing(pdf_text: str, diameters: list) -> Optional[float]:
    """
    Определяет толщину листовой заготовки из чертежа.
    Стратегия: ищем наименьший значимый размер детали (не диаметр, не Ra).
    Это и есть толщина/высота плоской детали.
    """
    if not pdf_text:
        return None

    dia_set = {round(d, 1) for d in (diameters or [])}

    nums = _extract_all_numbers_clean(pdf_text)
    # Кандидаты на толщину: маленькие числа, не диаметры, не Ra
    ra_vals_set = set()
    for m in re.finditer(r"Ra\s*(\d+(?:[.,]\d+)?)", pdf_text, re.IGNORECASE):
        v = safe_float(m.group(1))
        if v: ra_vals_set.add(round(v, 1))

    candidates = []
    for v in nums:
        if v < 5 or v > 250:
            continue
        if round(v, 1) in dia_set:
            continue
        if round(v, 1) in ra_vals_set:
            continue
        # Ещё исключаем явные угловые размеры (45, 90 и т.п. с буквой)
        candidates.append(v)

    if not candidates:
        return None

    # Берём наименьший — это скорее всего толщина
    thickness = min(candidates)
    # Округляем до стандартной толщины листа
    return float(pick_standard_sheet_thickness(thickness))



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

    square_features_mm: List[float]
    max_square_mm: Optional[float]

    ra_values: List[float]
    length_mm: Optional[float]

    pdf_path: Optional[str] = None  # путь к исходному PDF (для Vision)


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
    logger.info(f"Запрос к OpenAI (модель={model}), длина промпта: {len(prompt)} символов")
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=360,
        )
        result = resp.choices[0].message.content.strip()
        logger.info(f"Ответ OpenAI получен, длина: {len(result)} символов")
        return result
    except Exception as e:
        logger.error("Ошибка запроса к OpenAI", e)
        raise


# ============================================================
# УТИЛИТЫ + РАБОТА С ГОСТ PDF
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
    logger.info(f"Открываю файл: {path}")
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", path], check=False)
    elif os.name == "nt":
        os.startfile(path)  # type: ignore
    else:
        subprocess.run(["xdg-open", path], check=False)


def find_gost_pdf(gost_code: str, gost_dir: str = GOST_DIR) -> str:
    base_dir = gost_dir
    if not os.path.isdir(base_dir):
        logger.warn(f"Папка ГОСТ не найдена: {base_dir}")
        return ""

    key = gost_code.replace("ГОСТ", "").strip().lower()

    for fn in os.listdir(base_dir):
        if not fn.lower().endswith(".pdf"):
            continue
        if key in fn.lower():
            p = os.path.join(base_dir, fn)
            if os.path.exists(p):
                logger.debug(f"ГОСТ PDF найден: {p}")
                return p

    logger.warn(f"ГОСТ PDF не найден для: {gost_code} (ключ={key}) в {base_dir}")
    return ""


def extract_text_from_any_pdf(pdf_path: str, max_pages: int = 6) -> str:
    if fitz is None:
        return ""
    if not pdf_path or not os.path.exists(pdf_path):
        return ""
    doc = fitz.open(pdf_path)
    texts: List[str] = []
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


def pick_relevant_snippets(
    text: str, keywords: List[str], max_snippets: int = 3, snippet_len: int = 420
) -> List[str]:
    if not text:
        return []
    t = text.replace("\r", "\n")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    if not lines:
        return []
    kws = [_norm(k) for k in keywords if k and k.strip()]
    if not kws:
        return []

    scored: List[Tuple[int, str]] = []
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

    uniq: List[str] = []
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
    sources: List[Tuple[str, List[str]]] = []

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
        elif calc_res.stock_type in {"Швеллер", "Двутавр"}:
            kw += ["профиль", "размеры"]

        snippets = pick_relevant_snippets(txt, kw, max_snippets=3)
        if snippets:
            sources.append((calc_res.gost_stock, snippets))

    mat_gost = calc_res.material_gost or extracted.material_gost or ""
    if mat_gost:
        p = find_gost_pdf(mat_gost, GOST_DIR)
        txt = extract_text_from_any_pdf(p, max_pages=6)
        snippets = pick_relevant_snippets(
            txt, ["сталь", "марка", "химический", "механические", "свойства", "ст3"], max_snippets=3
        )
        if snippets:
            sources.append((mat_gost, snippets))

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
# ЧИСТКА ТЕКСТА
# ============================================================

def _strip_gost_and_codes(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    keep: List[str] = []
    for ln in lines:
        ll = ln.lower()
        if "масса" in ll or "масштаб" in ll or "формат" in ll:
            continue
        keep.append(ln)
    t = "\n".join(keep)
    t = re.sub(r"\b\d+\s*:\s*\d+\b", " ", t)
    t = re.sub(r"\bГОСТ\s*\d+\s*[-–]\s*\d+\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{4,6}\s*[-–]\s*\d{2,4}\b", " ", t)
    t = re.sub(r"\b\d{5,}\.\d+\.\d+\.\d+\b", " ", t)
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


# ============================================================
# ДОП. ХЕЛПЕРЫ
# ============================================================

def is_bent_sheet(ex: Extracted) -> bool:
    t = (ex.pdf_text or "").lower()
    n = (ex.part_name or "").lower()
    keys = ["гнут", "гибка", "развертк", "развёртк", "развертка", "развёртка", "линия сгиба", "сгиб", "гиб"]
    return any(k in t for k in keys) or any(k in n for k in keys)


def _closest_to_multiple_of_10(values: List[float]) -> Optional[float]:
    if not values:
        return None

    def score(v: float) -> float:
        nearest = round(v / 10.0) * 10.0
        return abs(v - nearest)

    values2 = sorted(values, key=score)
    return values2[0] if values2 else None


def _pick_profile_length_mm(ex: Extracted) -> Optional[float]:
    l = ex.length_mm
    if l is not None and l >= 80:
        return l
    starred = [v for v in (ex.square_features_mm or []) if v >= 60]
    if starred:
        return float(max(starred))
    nums = _extract_all_numbers_clean(ex.pdf_text)
    cand = [v for v in nums if v >= 60]
    return float(max(cand)) if cand else None


def _pick_bent_sheet_dims(ex: Extracted) -> Tuple[Optional[float], Optional[float]]:
    nums = _extract_all_numbers_clean(ex.pdf_text)
    if not nums:
        return None, None
    length_cand = [v for v in nums if v >= 250]
    l = float(max(length_cand)) if length_cand else None
    width_cand = [v for v in nums if 120 <= v <= 260 and (l is None or abs(v - l) > 1e-6)]
    b = float(max(width_cand)) if width_cand else None
    return b, l


# ============================================================
# PDF: ИЗВЛЕЧЕНИЕ ТЕКСТА ЧЕРТЕЖА
# ============================================================

def extract_text_from_pdf(pdf_path: str, max_pages: int = 2) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF не установлен. Установи: python3 -m pip install pymupdf")
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    logger.info(f"Читаю PDF чертежа: {pdf_path} (макс. страниц: {max_pages})")
    doc = fitz.open(pdf_path)
    texts: List[str] = []
    pages_to_read = min(len(doc), max_pages)
    for i in range(pages_to_read):
        try:
            texts.append(doc[i].get_text("text"))
        except Exception as e:
            logger.warn(f"Ошибка чтения страницы {i}: {e}")
    doc.close()
    result = "\n".join(texts)
    logger.debug(f"Извлечено символов из PDF: {len(result)}")
    return result


# ============================================================
# ПАРСИНГ ЧЕРТЕЖА
# ============================================================

# ГОСТ сортамента — эти ГОСТы НЕ являются ГОСТом на материал
STOCK_GOSTS = {
    "2590", "2591", "19903", "19904", "8240", "35087", "53383", "32931",
    "2879",  # шестигранник
}

# Паттерн марки стали: "Сталь 45", "40Х", "Ст3сп", "В 20", "38ХГН" и т.п.
STEEL_MARK_PAT = re.compile(
    r"\b((?:Сталь\s*[0-9]+[А-ЯA-Za-z]*"   # Сталь 45, Сталь 20Х
    r"|Ст\s*\d+(?:сп|пс|кп)?"              # Ст3сп, Ст45
    r"|В\s*\d+"                             # В 20 (сталь для труб)
    r"|[0-9]{2,3}[А-ЯA-Z][А-ЯA-Za-z0-9]*"  # 40Х, 30ХГСА, 38ХГН
    r"))"
    r"\s+ГОСТ\s*([0-9]+[-–][0-9]+)\b",
    flags=re.IGNORECASE
)


def find_material_mark_and_gost(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Ищет марку материала и ГОСТ материала в тексте чертежа.
    Поддерживает: Ст3сп, 40Х, 30ХГСА, 12Х18Н10Т, 09Г2С и т.д.
    Исключает ГОСТы на сортамент (2590, 2591, 19903 и т.п.)
    """
    if not text:
        return None, None

    for m in STEEL_MARK_PAT.finditer(text):
        raw_mark = m.group(1).strip()
        gost_num = m.group(2).replace("–", "-").strip()
        # Пропускаем ГОСТы сортамента
        base_num = gost_num.split("-")[0].strip()
        if base_num in STOCK_GOSTS:
            continue
        mark = normalize_mark(raw_mark)
        gost = normalize_gost("ГОСТ" + gost_num)
        return mark, gost

    # Fallback: Ст* + ГОСТ
    m = re.search(r"\b(Ст\s*\d+(?:сп|пс)?)\s+ГОСТ\s*([0-9]+[-–][0-9]+)\b", text, flags=re.IGNORECASE)
    if m:
        mark = normalize_mark(m.group(1))
        gost = normalize_gost("ГОСТ" + m.group(2).replace("–", "-"))
        return mark, gost

    return None, None


def find_stamp_mass_kg(text: str) -> Optional[float]:
    """
    Ищет массу в штампе чертежа.
    Стратегия 1: число на строке с 'масс'.
    Стратегия 2: число в блоке после слова 'Масса' (в ГОСТ-штампе масса идёт отдельной строкой).
    """
    if not text:
        return None

    lines = text.splitlines()

    # Стратегия 1: число прямо на строке с "масс"
    for ln in lines:
        if "масс" not in ln.lower():
            continue
        m = re.search(r"(\d+(?:[.,]\d+)?)", ln)
        if not m:
            continue
        v = safe_float(m.group(1))
        if v is not None and 0.001 <= v <= 99999:
            return v

    # Стратегия 2: найти строку "Масса" и взять число из следующих 3 строк
    for i, ln in enumerate(lines):
        if ln.strip().lower() in ("масса", "масса:"):
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.search(r"(\d+(?:[.,]\d+)?)", lines[j])
                if m:
                    v = safe_float(m.group(1))
                    if v is not None and 0.1 <= v <= 99999:
                        return v

    # Стратегия 3: в штампе масса и масштаб идут рядом — ищем блок "Масса Масштаб"
    # Обычно после "МассаМасштаб" идёт строка с числом (масса) и строка с масштабом
    full = text.replace("\n", " ")
    m = re.search(r"Масса.*?Масштаб.*?(\d+(?:[.,]\d+)?)\s*(\d+:\d+)", full)
    if m:
        v = safe_float(m.group(1))
        if v is not None and 0.1 <= v <= 99999:
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


def find_starred_lengths(text: str) -> List[float]:
    """Числа со звёздочкой (N*) — справочные размеры, обычно длина детали."""
    if not text:
        return []
    out: List[float] = []
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*\*", text):
        v = safe_float(m.group(1))
        if v is None or v < 10:
            continue
        out.append(v)
    uniq: List[float] = []
    for v in out:
        if not any(abs(v - u) < 1e-6 for u in uniq):
            uniq.append(v)
    return uniq


def find_square_sizes(text: str) -> List[float]:
    """Ищем квадрат только по явному контексту 'Квадрат NNN'."""
    if not text:
        return []
    out: List[float] = []
    for m in re.finditer(r"(?:квадрат)\s+(\d+(?:[.,]\d+)?)", text, flags=re.IGNORECASE):
        v = safe_float(m.group(1))
        if v is None or v < 5:
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
    logger.info(f"Начинаю извлечение данных из чертежа: {os.path.basename(pdf_path)}")

    pdf_text = extract_text_from_pdf(pdf_path, max_pages=max_pages)
    part_name = os.path.splitext(os.path.basename(pdf_path))[0]

    t = pdf_text.lower()
    n = part_name.lower()
    looks_rot = any(w in n for w in ["ось", "вал", "втулк", "ролик", "фланец"]) or any(
        w in t for w in ["ось", "вал", "втулк", "ролик", "фланец"]
    )

    mark, gost = find_material_mark_and_gost(pdf_text)
    logger.info(f"Материал: {mark}, ГОСТ материала: {gost}")

    stamp_mass = find_stamp_mass_kg(pdf_text)
    logger.info(f"Масса в штампе: {stamp_mass} кг" if stamp_mass else "Масса в штампе: не найдена")

    diam_feats = find_diameters_with_tolerance(pdf_text, looks_rotational=looks_rot)
    logger.debug(f"Диаметры: {[f.d_mm for f in diam_feats]}")

    ra_vals = find_ra_values(pdf_text)
    logger.debug(f"Ra значения: {ra_vals}")

    square_feats = find_square_sizes(pdf_text)
    logger.debug(f"Квадратные размеры: {square_feats}")

    # Сначала ищем справочные размеры (N*) — самое надёжное для длины
    starred = find_starred_lengths(pdf_text)
    logger.debug(f"Справочные размеры (со *): {starred}")

    diam_list = [f.d_mm for f in diam_feats]

    # Приоритет: 1) максимальный справочный размер (звёздочка), 2) обычный поиск
    if starred:
        l = float(max(starred))
        logger.debug(f"Длина из справочного размера (*): {l} мм")
    else:
        l = find_length_mm(pdf_text, diameters=diam_list, squares=square_feats)
        logger.debug(f"Длина детали: {l} мм")

    if diam_feats:
        max_feat = max(diam_feats, key=lambda f: f.d_mm)
        max_d = max_feat.d_mm
        max_tol = max_feat.tol
    else:
        max_d = None
        max_tol = None

    max_sq = max(square_feats) if square_feats else None

    logger.info(f"Извлечение завершено: деталь={part_name}, ротационная={looks_rot}, макс.Ø={max_d}")

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
        pdf_path=pdf_path,
    )


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
    t = (pdf_text or "")
    tl = t.lower()
    variants = [descr.lower(), descr.lower().replace(" ", "")]
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
# ЛОГИКА ПОДБОРА
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


def compute_blank_from_step_model(m, mark: str, mgost: str, dens: float,
                                   allow: float, source_label: str) -> dict:
    """
    Определяет заготовку по уже прочитанной 3D-геометрии (core.step_reader.StepModel)
    — та же логика, что в ui/screens/model3d_tool.py (вкладка «3D инструмент»),
    вынесена сюда, чтобы ею же мог пользоваться живой адаптер КОМПАСа
    (integrations/kompas/adapter.py), не дублируя код.

    m — StepModel (geometry: dx/dy/dz, shape_type, stock_size_hint, volume_mm3,
        blank_volume_mm3, length_mm и т.д.)
    mark/mgost — марка и ГОСТ материала (уже нормализованные)
    dens — плотность, кг/м³
    allow — припуск, мм
    source_label — текст для протокола ("STEP", "КОМПАС(live) — геометрия
        получена экспортом в STEP", и т.п.)

    Возвращает dict: {"ok": bool, "result": str, "protocol": str,
                       "stock_desc": str, "size_str": str,
                       "mass_blank_kg": float, "mass_part_kg": float}
    или {"ok": False, "message": "..."} если тип заготовки не определён.
    """
    st = m.shape_type
    if st == "?" or not st:
        return {
            "ok": False,
            "message": (
                "Тип заготовки не определён по геометрии.\n"
                f"Габариты: {m.dx:.1f} × {m.dy:.1f} × {m.dz:.1f} мм\n"
                f"Подсказка: {m.stock_size_hint}\n\n"
                "Нужно задать тип и размер вручную."
            ),
        }

    st_key = {"Лист": "ЛистГК"}.get(st, st)
    gost_stock = STOCKTYPE_TO_GOST.get(st_key, "")
    L, W, T = m.length_mm, m.width_mm, m.thickness_mm
    prot = ["Определение заготовки по 3D-модели", f"- Тип по геометрии: {st}"]

    if st == "Лист":
        t_std = pick_standard_sheet_thickness(T)
        if abs(t_std - T) > 0.1:
            prot.append(f"- Толщина детали {T:.1f} → стандарт ГОСТ 19903: {t_std} мм")
        b, l = W + allow, L + allow
        gost_stock = STOCKTYPE_TO_GOST["ЛистХК" if t_std <= 3 else "ЛистГК"]
        stock_desc = f"Лист {t_std} {gost_stock}"
        size_str = f"Лист {t_std}, {l:.0f}х{b:.0f}"
        vol_blank = t_std * b * l

    elif st == "Круг":
        D = min(m.dx, m.dy)
        d_std = pick_round_diameter(D + allow)
        Lz = m.length_mm + allow
        stock_desc = f"Круг {d_std} {gost_stock}"
        size_str = f"Круг {d_std}, L={Lz:.0f}"
        vol_blank = math.pi * (d_std ** 2) / 4 * Lz
        if abs(d_std - D) > 0.1:
            prot.append(f"- Ø детали {D:.1f} → сортамент ГОСТ 2590: {d_std} мм")

    elif st == "Квадрат":
        a = min(m.dx, m.dy) + allow
        Lz = m.length_mm + allow
        stock_desc = f"Квадрат {a:.0f} {gost_stock}"
        size_str = f"Квадрат {a:.0f}, L={Lz:.0f}"
        vol_blank = a * a * Lz

    else:
        hint = m.stock_size_hint
        head = hint.split(",")[0].strip()
        size_str = hint.split("(")[0].strip().rstrip(",")
        stock_desc = f"{head} {gost_stock}".strip()
        vol_blank = m.blank_volume_mm3
        if allow and m.length_mm:
            vol_blank += vol_blank / m.length_mm * allow
            size_str = re.sub(r"L=(\d+)",
                              lambda mm: f"L={int(mm.group(1)) + int(allow)}", size_str)
        prot.append(f"- Размер из геометрии: {hint}")

    mass_blank = vol_blank / 1e9 * dens
    mass_part = m.volume_mm3 / 1e9 * dens

    mat_part = f"/ {mark} {mgost}" if (mark and mgost) else (f"/ {mark}" if mark else "")
    line1 = f"{stock_desc}{(' ' + mat_part) if mat_part else ''};"

    result = (
        f"{line1}\n"
        f"{size_str};\n"
        f"Масса заготовки, кг: {mass_str_ru(mass_blank)}\n"
        f"Масса чистовая, кг: {mass_str_ru(mass_part)}"
    )

    prot += [
        f"- Источник данных: {source_label}",
        f"- Габариты детали: {m.dx:.1f} × {m.dy:.1f} × {m.dz:.1f} мм",
        f"- Припуск: +{allow:.0f} мм",
        f"- ГОСТ сортамента: {gost_stock}",
        f"- Плотность: {dens:.0f} кг/м³",
        f"- Объём заготовки: {vol_blank/1000:.2f} см³ → масса {mass_blank:.3f} кг",
        f"- Объём детали: {m.volume_mm3/1000:.2f} см³ → масса чист. {mass_part:.3f} кг",
    ]
    if not mark:
        prot.append("- ⚠ Марка материала не задана")

    return {
        "ok": True,
        "result": result,
        "protocol": "\n".join(prot),
        "stock_desc": stock_desc,
        "size_str": size_str,
        "mass_blank_kg": round(mass_blank, 3),
        "mass_part_kg": round(mass_part, 3),
    }


def build_result_block_bar(stock_desc: str, l_blank: float, mass_kg: float) -> str:
    return (
        f"{stock_desc};\n"
        f"L={int(l_blank)};\n"
        f"Масса заготовки, кг: {mass_str_ru(mass_kg)}"
    )


def build_result_block_sheet(t_mm: float, b_mm: float, l_blank: float, mass_kg: float) -> str:
    return (
        f"Лист {int(round(t_mm))};\n"
        f"{int(round(b_mm))}х{int(round(l_blank))};\n"
        f"Масса заготовки, кг: {mass_str_ru(mass_kg)}"
    )


# Ключевые слова типов заготовок в тексте штампа
STOCK_TYPE_KEYWORDS = {
    "Круг": ["круг"],
    "Квадрат": ["квадрат"],
    "Труба": ["труба", "трубы"],
    "ТрубаПроф": ["труба проф", "профильная труба"],
    "ЛистГК": ["лист гк", "листгк", "лист горячекатан"],
    "ЛистХК": ["лист хк", "листхк", "лист холоднокатан"],
    "Швеллер": ["швеллер"],
    "Двутавр": ["двутавр", "балка"],
}


def detect_stock_type_from_stamp(pdf_text: str) -> Optional[str]:
    """
    Читает тип заготовки прямо из штампа чертежа.
    Учитывает что в PDF текст штампа может быть разбит по строкам.
    Ищет только в контексте рядом с ГОСТ сортамента.
    """
    if not pdf_text:
        return None

    # Нормализуем: убираем лишние переносы вокруг ключевых слов
    # Склеиваем строки для поиска по контексту штампа
    normalized = re.sub(r"\n+", " ", pdf_text)
    t = normalized.lower()

    # Приоритет: ищем точный паттерн "<тип> <число> ГОСТ <номер>" в штампе
    STAMP_PAT = re.compile(
        r"\b(лист|круг|квадрат|труба(?:\s*проф)?|швеллер|двутавр|балка)"
        r"\s+\d+",
        re.IGNORECASE
    )
    m = STAMP_PAT.search(normalized)
    if m:
        kw = m.group(1).lower().strip()
        for stock_type, keywords in STOCK_TYPE_KEYWORDS.items():
            if any(kw == k or kw.startswith(k) for k in keywords):
                return stock_type

    # Fallback: просто есть слово в тексте
    for stock_type, keywords in STOCK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", t):
                return stock_type
    return None


def detect_stock_diameter_from_stamp(pdf_text: str) -> Optional[float]:
    """
    Читает диаметр/толщину/размер заготовки из строки штампа.
    Например:
      'Круг 130 ГОСТ 2590-2006' → 130.0
      'Лист 80 ГОСТ 19903-2015' → 80.0  (толщина листа)
      'Труба 351x25 ГОСТ ...' → 351.0
    Работает и когда текст разбит по строкам.
    """
    if not pdf_text:
        return None
    # Склеиваем переносы строк для поиска
    normalized = re.sub(r"\n+", " ", pdf_text)
    m = re.search(
        r"\b(?:Круг|Квадрат|Труба|Лист)\s+(\d+(?:[.,]\d+)?)",
        normalized, flags=re.IGNORECASE
    )
    if m:
        return safe_float(m.group(1))
    return None


def detect_tube_wall_from_stamp(pdf_text: str) -> Optional[float]:
    """
    Читает толщину стенки трубы из штампа.
    Например: 'Труба 351x25 ГОСТ ...' → 25.0
    """
    if not pdf_text:
        return None
    normalized = re.sub(r"\n+", " ", pdf_text)
    m = re.search(
        r"\bТруба\s+\d+[xхXХ](\d+(?:[.,]\d+)?)",
        normalized, flags=re.IGNORECASE
    )
    if m:
        return safe_float(m.group(1))
    return None


def detect_sheet_size_from_stamp(pdf_text: str) -> Optional[float]:
    """
    Читает габарит листовой заготовки.
    Для круглых листов: диаметр □370 → 370
    Для прямоугольных: 70x400 → max dimension
    Возвращает максимальный габарит.
    """
    if not pdf_text:
        return None
    normalized = re.sub(r"\n+", " ", pdf_text)
    # Ищем прямоугольник NxM или NхM
    m = re.search(
        r"(\d+)\s*[xхXХ]\s*(\d+)",
        normalized
    )
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return max(a, b)
    return None


def detect_stock_type(ex: Extracted) -> str:
    name_l = (ex.part_name or "").lower()
    text_l = (ex.pdf_text or "").lower()

    # Приоритет 1: читаем тип прямо из текста штампа
    stamp_type = detect_stock_type_from_stamp(ex.pdf_text)
    if stamp_type:
        return stamp_type

    if "швеллер" in name_l or "швеллер" in text_l:
        return "Швеллер"
    if "балка" in name_l or "двутавр" in name_l or "балка" in text_l or "двутавр" in text_l:
        return "Двутавр"

    # Труба только если явно упоминается или деталь — втулка
    if ("втулк" in name_l) or ("втулк" in text_l) or ("труба" in text_l):
        return "Труба"

    if is_bent_sheet(ex) or ("кожух" in name_l) or ("кожух" in text_l):
        return "ЛистХК"

    if ("платик" in name_l) or ("пластин" in name_l) or ("платик" in text_l) or ("пластин" in text_l):
        return "ЛистХК"

    if (ex.max_square_mm is not None) and not ex.diameter_features:
        if ex.max_square_mm >= 8:
            return "Квадрат"

    # Приоритет 2: геометрическое определение — плоская деталь?
    # Если есть диаметры И самый маленький размер << максимального → скорее всего лист
    # Признак листа: есть размер который явно "толщина" (<=200мм) и крупный D (>=200мм)
    # и отношение min_size/max_d < 0.3
    dia_vals = sorted({f.d_mm for f in ex.diameter_features})
    if dia_vals:
        max_d = max(dia_vals)
        # Ищем числа из чертежа
        all_nums = _extract_all_numbers_clean(ex.pdf_text)
        small_nums = [v for v in all_nums if 2 <= v <= 200 and v < max_d * 0.4]
        if small_nums and max_d >= 150:
            min_thick = min(small_nums)
            if min_thick <= 120 and max_d / min_thick >= 3.0:
                # Плоская деталь (шайба, диск, крышка, вставка, гайка-кольцо)
                return "ЛистГК"

    return "Круг"


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ РАСЧЁТА
# ============================================================

def calculate_missing_stock_line(ex: Extracted, l_part_mm: float, material_mark: str, material_gost: str) -> CalcResult:
    logger.info(f"Начинаю расчёт заготовки: деталь={ex.part_name}, L={l_part_mm} мм")

    # ========================================================
    # RAG-СЛОЙ: ответ по базе эталонов (похожие чертежи)
    # --------------------------------------------------------
    # Идея: пользователь прислал НОВЫЙ чертёж без ответа.
    # Сначала пытаемся найти похожие ПРОВЕРЕННЫЕ чертежи из базы
    # и сформировать ответ по их образцу. Если не вышло
    # (нет интернета / нет похожих / низкая уверенность) —
    # падаем на обычный формульный расчёт ниже.
    # ========================================================
    if USE_RAG and _RAG_AVAILABLE:
        rag = None

        # --- Сначала пробуем Vision: GPT СМОТРИТ на картинку чертежа ---
        if USE_VISION and getattr(ex, "pdf_path", None):
            try:
                rag = rag_search.solve_via_vision_2step(
                    pdf_path=ex.pdf_path,
                    pdf_text=ex.pdf_text,
                    part_name=ex.part_name,
                )
                if rag and rag.get("result_line"):
                    logger.info("Vision+RAG: получен ответ по изображению чертежа")
            except Exception as e:
                logger.warn(f"Vision+RAG упал, перехожу на текстовый RAG: {e}")
                rag = None

        # --- Если Vision не дал ответа — текстовый RAG по базе ---
        if not (rag and rag.get("result_line")):
            try:
                rag = rag_search.solve_via_rag(
                    pdf_text=ex.pdf_text,
                    part_name=ex.part_name,
                    min_score=RAG_MIN_SCORE,
                )
            except Exception as e:
                logger.warn(f"RAG-слой упал, перехожу на формулы: {e}")
                rag = None

        # Vision-ответ принимаем даже при низком score (он видит чертёж напрямую).
        # Текстовый RAG — только если уверенность выше порога.
        _is_vision = bool(rag and rag.get("source") in ("vision_rag", "vision_2step"))
        _accept = bool(
            rag and rag.get("result_line") and (
                _is_vision or rag.get("best_score", 0) >= RAG_MIN_SCORE
            )
        )

        if _accept:
            logger.info(
                f"{'Vision+RAG' if _is_vision else 'RAG'}: ответ сформирован "
                f"(уверенность {rag.get('best_score')}, образцов {len(rag.get('based_on', []))})"
            )
            # Определяем тип/ГОСТ для заполнения остальных полей CalcResult
            try:
                st = detect_stock_type(ex)
            except Exception:
                st = "Круг"
            gs = STOCKTYPE_TO_GOST.get(st, "")
            gpdf = find_gost_pdf(gs, GOST_DIR) if gs else ""

            mat_out = material_mark or (ex.material_mark or "")
            if MATERIAL_NORMALIZE_TO_BASE and mat_out:
                mat_out = re.sub(r"(Ст\s*\d+)(?:пс|сп)\b", r"\1", mat_out, flags=re.IGNORECASE)
                mat_out = normalize_mark(mat_out)

            # Протокол: на чём основан ответ
            prot_rag: List[str] = []
            prot_rag.append("Протокол: ОТВЕТ " + ("ПО ЧЕРТЕЖУ (Vision) + БАЗА" if _is_vision else "ПО БАЗЕ ЭТАЛОНОВ (RAG)"))
            prot_rag.append(f"- Файл: {ex.part_name}")
            prot_rag.append(f"- Режим: {'GPT-4o смотрит на изображение чертежа + образцы из базы' if _is_vision else 'поиск по похожим эталонам (текст)'}")
            prot_rag.append(f"- Уверенность (похожесть лучшего образца): {rag.get('best_score')}")
            prot_rag.append(f"- Тип заготовки (авто): {st}")
            prot_rag.append("- Ответ сформирован по похожим проверенным чертежам из базы:")
            for b in rag["based_on"]:
                prot_rag.append(
                    f"    • {b.get('drawing_no','')} {b.get('part_name','')} "
                    f"(похожесть {b.get('score')}) → {str(b.get('result_line','')).replace(chr(10),' | ')}"
                )
            prot_rag.append("")
            prot_rag.append("Итоговый ответ (по базе):")
            prot_rag.append(rag["result_line"])

            return CalcResult(
                stock_type=st,
                gost_stock=gs,
                gost_pdf_path=gpdf,
                material_out=mat_out,
                material_gost=material_gost or (ex.material_gost or ""),
                d_part_mm=ex.max_d_mm,
                tol=ex.max_d_tol,
                ra=choose_ra_to_use(ex.ra_values),
                tube_od_mm=None,
                tube_wall_mm=None,
                square_mm=ex.max_square_mm,
                allowance_side_mm=0.0,
                d_blank_calc_mm=0.0,
                d_blank_std_mm=0,
                l_part_mm=float(l_part_mm),
                l_blank_mm=float(l_part_mm + ADD_LENGTH_MM),
                density_kg_m3=DEFAULT_STEEL_DENSITY_KG_M3,
                mass_kg=ex.stamp_mass_kg if ex.stamp_mass_kg is not None else 0.0,
                mass_physics_kg=0.0,
                mass_stamp_kg=ex.stamp_mass_kg,
                result_line=rag["result_line"],
                protocol="\n".join(prot_rag),
            )
        else:
            logger.info("RAG: подходящего ответа по базе нет — считаю формулами")

    # ========================================================
    # ФОРМУЛЬНЫЙ РАСЧЁТ (как раньше) — fallback / основной режим
    # ========================================================
    stock_type = detect_stock_type(ex)
    logger.info(f"Определён тип заготовки: {stock_type}")

    gost_stock = pick_gost_for_stock(stock_type)
    gost_pdf = find_gost_pdf(gost_stock, GOST_DIR)
    logger.info(f"ГОСТ сортамента: {gost_stock}, PDF: {gost_pdf or 'не найден'}")

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
        # Приоритет: диаметр из штампа ("Круг 130 ГОСТ...") как готовый размер заготовки
        stamp_d = detect_stock_diameter_from_stamp(ex.pdf_text)
        if stamp_d is not None:
            # Диаметр уже указан в штампе — используем напрямую как d_blank_std
            d_part = stamp_d
            tol = ex.max_d_tol
            allowance_side = 0.0
            allow_src = "stamp_direct"
            d_blank_calc = stamp_d
            d_blank_std = pick_round_diameter(stamp_d)
            logger.info(f"Круг: диаметр взят из штампа: {stamp_d} мм → стандартный: {d_blank_std} мм")
        elif ex.max_d_mm is None:
            logger.error("Не найден диаметр для типа Круг")
            raise ValueError("Не найден диаметр (Ø) в PDF. Для круга это критично.")
        else:
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
        logger.debug(f"Круг: d_part={d_part}, припуск={allowance_side} ({allow_src}), d_blank_calc={d_blank_calc:.2f}, d_blank_std={d_blank_std}")

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
        # Приоритет 1: читаем OD и стенку из штампа ("Труба 351x25 ГОСТ...")
        stamp_od = detect_stock_diameter_from_stamp(ex.pdf_text)
        stamp_wall = detect_tube_wall_from_stamp(ex.pdf_text)

        if stamp_od is not None and stamp_wall is not None:
            tube_od = float(stamp_od)
            tube_wall = float(stamp_wall)
            tube_id = tube_od - 2.0 * tube_wall
            logger.info(f"Труба из штампа: OD={tube_od}, стенка={tube_wall}")
        else:
            # Fallback: вычисляем из диаметров чертежа
            # Наружный диаметр — справочный (со *), внутренний — с допуском H9
            starred = find_starred_lengths(ex.pdf_text)
            dia_vals2 = sorted({f.d_mm for f in ex.diameter_features})

            if starred:
                tube_od = float(max(starred))
            elif dia_vals2:
                tube_od = float(max(dia_vals2))
            else:
                raise ValueError("Не найден наружный диаметр трубы.")

            # Внутренний диаметр — ближайший меньше OD с допуском H9
            inner_candidates = [d for d in dia_vals2 if d < tube_od - 5]
            if not inner_candidates:
                raise ValueError("Для трубы нужны минимум 2 диаметра.")
            tube_id = float(max(inner_candidates))
            tube_wall = round((tube_od - tube_id) / 2.0, 1)
            if tube_wall <= 0:
                raise ValueError("Толщина стенки трубы <= 0. Проверь диаметры.")
            logger.debug(f"Труба из диаметров: OD={tube_od}, ID={tube_id}, стенка={tube_wall}")

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

        prot.append(f"- Труба: OD={tube_od} мм, стенка={tube_wall} мм, ID={tube_id} мм")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    elif stock_type == "Квадрат":
        if ex.max_square_mm is None:
            logger.error("Не найден размер квадрата")
            raise ValueError("Не найден размер квадрата (например '20*') в PDF. Для квадрата это критично.")
        sq = float(ex.max_square_mm)
        logger.debug(f"Квадрат: сторона={sq} мм")

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
        # --- Шаг 1: определяем толщину заготовки ---
        # Приоритет 1: из штампа ("Лист 80 ГОСТ19903...")
        stamp_t = detect_stock_diameter_from_stamp(ex.pdf_text)
        if stamp_t is not None and 5 <= stamp_t <= 300:
            sheet_t = float(pick_standard_sheet_thickness(stamp_t))
            logger.info(f"Лист: толщина из штампа {stamp_t} → стандарт {sheet_t} мм")
        else:
            # Приоритет 2: максимальная высота детали из чертежа
            # Высота = наибольшее число, которое НЕ является диаметром и < 300
            dia_set = {round(f.d_mm, 1) for f in ex.diameter_features}
            nums_all = _extract_all_numbers_clean(ex.pdf_text)
            # Исключаем диаметры, Ra, угловые
            ra_set = {round(v, 1) for v in find_ra_values(ex.pdf_text)}
            height_cands = [
                v for v in nums_all
                if 10 <= v <= 300
                and round(v, 1) not in dia_set
                and round(v, 1) not in ra_set
            ]
            if height_cands:
                max_height = max(height_cands)
                sheet_t = float(pick_standard_sheet_thickness(max_height))
                logger.info(f"Лист: макс.высота детали={max_height} → стандарт {sheet_t} мм")
            else:
                raise ValueError("Не удалось определить толщину листа.")

        if sheet_t <= 3.0:
            stock_type = "ЛистХК"
        else:
            stock_type = "ЛистГК"
        gost_stock = pick_gost_for_stock(stock_type)
        gost_pdf = find_gost_pdf(gost_stock, GOST_DIR)

        if is_bent_sheet(ex) or ("кожух" in (ex.part_name or "").lower()) or ("кожух" in (ex.pdf_text or "").lower()):
            b_part, l_part = _pick_bent_sheet_dims(ex)
            if b_part is None or l_part is None:
                raise ValueError("Не удалось определить габариты развёртки для гнутой детали.")

            b_blank = float(b_part + 4.0)
            l_blank2 = float(l_part + 2.0 * sheet_t)

            mass_physics = calc_sheet_mass_kg(sheet_t, b_blank, l_blank2, density)

            if MASS_MODE == "stamp_first" and mass_stamp is not None:
                mass_final = float(mass_stamp)
                mass_note = "stamp_first"
            else:
                mass_final = mass_physics
                mass_note = "physics_only"

            result_line = build_result_block_sheet(sheet_t, b_blank, l_blank2, mass_final)

            prot.append(f"- Гнутый лист: t={sheet_t} мм, b(разв)={b_part} мм, L(разв)={l_part} мм")
            prot.append(f"- Габариты заготовки: {int(round(b_blank))}х{int(round(l_blank2))}")
            prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note}, physics={mass_physics:.6f})")

            l_part_mm = float(l_part)
            l_blank = float(l_blank2)

        else:
            # --- Шаг 2: определяем габарит листовой заготовки ---
            #
            # Правило: заготовка = наименьший прямоугольник/круг из листа,
            # из которого можно вырезать деталь с припуском.
            #
            # Круглая деталь (шайба/диск/крышка/гайка-кольцо):
            #   Габарит = максимальный диаметр детали + 10..30мм → округл. вверх до кратного 10
            # Прямоугольная деталь (шпонка/вставка/пластина):
            #   Две стороны = два наибольших НЕ-диаметровых размера + припуск 10мм

            dia_vals = sorted({f.d_mm for f in ex.diameter_features}, reverse=True)
            starred_vals = set(find_starred_lengths(ex.pdf_text))
            # Реальные диаметры детали (не справочные, не мелкие отверстия)
            real_dias = [d for d in dia_vals if d not in starred_vals and d >= 30]
            max_real_d = float(max(real_dias)) if real_dias else None

            nums_all = _extract_all_numbers_clean(ex.pdf_text)
            dia_set2 = {round(d, 1) for d in dia_vals}
            ra_set2 = {round(v, 1) for v in find_ra_values(ex.pdf_text)}

            # Нелинейные (плоские) размеры — стороны детали
            flat_sizes = sorted([
                v for v in nums_all
                if 20 <= v <= 2000
                and round(v, 1) not in dia_set2
                and round(v, 1) not in ra_set2
            ], reverse=True)

            # Определяем форму заготовки
            # Круглая если: есть большие диаметры И нет явного прямоугольника
            # Прямоугольная если: нет больших диаметров ИЛИ есть два сопоставимых плоских размера
            is_round_blank = bool(max_real_d and max_real_d >= 100)

            # Проверяем наличие двух близких плоских размеров (признак прямоугольника)
            if len(flat_sizes) >= 2:
                big_flat = flat_sizes[:3]
                if len(big_flat) >= 2:
                    ratio = big_flat[1] / big_flat[0] if big_flat[0] > 0 else 0
                    # Если второй размер >= 20% от первого — возможно прямоугольник
                    if ratio >= 0.15 and big_flat[1] >= 50:
                        is_round_blank = False

            SHEET_ПРИПУСК = 20  # мм на сторону

            if is_round_blank and max_real_d is not None:
                # Круглая заготовка □D
                blank_d = math.ceil((max_real_d + SHEET_ПРИПУСК) / 10) * 10
                logger.info(f"Лист круглый: max_d={max_real_d} → □{blank_d}")
                mass_physics = density * (math.pi / 4) * (blank_d / 1000) ** 2 * (sheet_t / 1000)
                mass_final = float(mass_stamp) if (MASS_MODE == "stamp_first" and mass_stamp) else mass_physics
                result_line = (
                    "Лист " + str(int(round(sheet_t))) + " " + gost_stock + "\n"
                    + "□" + str(int(blank_d)) + "\n"
                    + "Масса заготовки, кг: " + mass_str_ru(mass_final)
                )
                l_blank = float(blank_d)
                prot.append(f"- Лист круглый: t={sheet_t}, □{int(blank_d)} (из Ø{max_real_d}+{SHEET_ПРИПУСК})")

            elif len(flat_sizes) >= 2:
                # Прямоугольная заготовка BxL
                l_part2 = flat_sizes[0]
                b_part2 = flat_sizes[1]
                b_blank = math.ceil((b_part2 + SHEET_ПРИПУСК) / 10) * 10
                l_blank2 = math.ceil((l_part2 + SHEET_ПРИПУСК) / 10) * 10
                logger.info(f"Лист прямоугольный: {int(b_blank)}х{int(l_blank2)}")
                mass_physics = calc_sheet_mass_kg(sheet_t, b_blank, l_blank2, density)
                mass_final = float(mass_stamp) if (MASS_MODE == "stamp_first" and mass_stamp) else mass_physics
                result_line = (
                    "Лист " + str(int(round(sheet_t))) + " " + gost_stock + "\n"
                    + str(int(b_blank)) + "х" + str(int(l_blank2)) + "\n"
                    + "Масса заготовки, кг: " + mass_str_ru(mass_final)
                )
                l_blank = float(l_blank2)
                prot.append(f"- Лист прямоугольный: t={sheet_t}, {int(b_blank)}х{int(l_blank2)}")

            elif max_real_d is not None:
                # Только диаметры — круглая заготовка
                blank_d = math.ceil((max_real_d + SHEET_ПРИПУСК) / 10) * 10
                mass_physics = density * (math.pi / 4) * (blank_d / 1000) ** 2 * (sheet_t / 1000)
                mass_final = float(mass_stamp) if (MASS_MODE == "stamp_first" and mass_stamp) else mass_physics
                result_line = (
                    "Лист " + str(int(round(sheet_t))) + " " + gost_stock + "\n"
                    + "□" + str(int(blank_d)) + "\n"
                    + "Масса заготовки, кг: " + mass_str_ru(mass_final)
                )
                l_blank = float(blank_d)
                prot.append(f"- Лист круглый (fallback): t={sheet_t}, □{int(blank_d)}")

            else:
                raise ValueError("Не удалось определить габариты для листовой заготовки.")

            mass_note = "stamp_first" if (MASS_MODE == "stamp_first" and mass_stamp) else "physics"
            prot.append(f"- Масса: {mass_final:.3f} кг ({mass_note})")

    elif stock_type == "Швеллер":
        l_part2 = _pick_profile_length_mm(ex)
        if l_part2 is None:
            raise ValueError("Не удалось определить длину детали для швеллера.")
        l_blank = float(l_part2 + ADD_LENGTH_MM)

        nums = _extract_all_numbers_clean(ex.pdf_text)
        cand = [v for v in nums if 30 <= v <= 200 and abs(v - l_part2) > 1e-6]
        h = _closest_to_multiple_of_10(cand)
        if h is None:
            raise ValueError("Не удалось определить номер швеллера (нет характерного размера 50/80/100...).")

        prof_no = int(round(h / 10.0))
        logger.debug(f"Швеллер №{prof_no}, L={l_blank} мм")
        stock_desc = f"Швеллер {prof_no} {gost_stock}"

        # Масса по ГОСТ 8240: погонный вес × длина заготовки (физически точно)
        mass_gost = shveller_mass_kg(prof_no, l_blank)
        mass_physics = mass_gost if mass_gost is not None else 0.0
        if mass_gost is not None:
            mass_final = mass_gost
            mass_note = "gost_8240_linear_weight"
        elif MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = "stamp_first"
        else:
            mass_final = 0.0
            mass_note = "no_physics_for_profile"

        result_line = build_result_block_bar(stock_desc, l_blank, mass_final)

        prot.append(f"- Швеллер: №{prof_no} (по характерному размеру ~{h} мм)")
        prot.append(f"- Длина детали: {l_part2} мм, Lзаг={l_blank} мм (+{ADD_LENGTH_MM})")
        if mass_gost is not None:
            w = SHVELLER_KG_PER_M.get(prof_no, "?")
            prot.append(f"- Масса по ГОСТ 8240: {w} кг/м × {l_blank/1000:.3f} м = {mass_final:.2f} кг")
        else:
            prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    elif stock_type == "Двутавр":
        l_part2 = _pick_profile_length_mm(ex)
        if l_part2 is None:
            raise ValueError("Не удалось определить длину детали для балки/двутавра.")
        l_blank = float(l_part2 + ADD_LENGTH_MM)

        nums = _extract_all_numbers_clean(ex.pdf_text)
        cand = [v for v in nums if 40 <= v <= 400 and abs(v - l_part2) > 1e-6]
        h = _closest_to_multiple_of_10(cand)
        if h is None:
            raise ValueError("Не удалось определить номер балки/двутавра (нет характерного размера 80/100/120...).")

        prof_no = int(round(h / 10.0))
        logger.debug(f"Двутавр №{prof_no}, L={l_blank} мм")
        stock_desc = f"Двутавр {prof_no} {gost_stock}"

        mass_physics = 0.0
        if MASS_MODE == "stamp_first" and mass_stamp is not None:
            mass_final = float(mass_stamp)
            mass_note = "stamp_first"
        else:
            mass_final = 0.0
            mass_note = "no_physics_for_profile"

        result_line = build_result_block_bar(stock_desc, l_blank, mass_final)

        prot.append(f"- Балка/двутавр: №{prof_no} (по характерному размеру ~{h} мм)")
        prot.append(f"- Длина детали: {l_part2} мм, Lзаг={l_blank} мм (+{ADD_LENGTH_MM})")
        prot.append(f"- Масса: {mass_final:.6f} кг ({mass_note})")

    else:
        raise ValueError(f"Тип заготовки пока не поддержан: {stock_type}")

    preview = re.sub(r"\s+", " ", ex.pdf_text).strip()
    preview = preview[:520] + (" ..." if len(preview) > 520 else "")
    prot.append("")
    prot.append("Текст PDF (фрагмент):")
    prot.append(preview)

    logger.info(f"Расчёт завершён: {result_line.replace(chr(10), ' | ')}")

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

        l_part_mm=float(l_part_mm),
        l_blank_mm=float(l_blank),

        density_kg_m3=density,
        mass_kg=mass_final,
        mass_physics_kg=mass_physics,
        mass_stamp_kg=mass_stamp,

        result_line=result_line,
        protocol="\n".join(prot)    )