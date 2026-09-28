"""
GOST_READER.PY — чтение таблиц из локальных PDF ГОСТов
Извлекает нужные страницы и передаёт ИИ как контекст.
"""

import os
import re
from pathlib import Path
from typing import Optional, Dict

try:
    import fitz
except ImportError:
    fitz = None

from core import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOSTS_DIR = str(PROJECT_ROOT / "gosts")

# Карта: тип заготовки / ключевое слово → файл ГОСТа
GOST_MAP: Dict[str, str] = {
    # Круг
    "круг":          "GOST_2590-2006.pdf",
    "2590":          "GOST_2590-2006.pdf",
    # Квадрат
    "квадрат":       "GOST_2591-2006.pdf",
    "2591":          "GOST_2591-2006.pdf",
    # Шестигранник
    "шестигранник":  "GOST_2879-2006.pdf",
    "2879":          "GOST_2879-2006.pdf",
    # Лист горячекатаный
    "лист":          "GOST_19903-2015.pdf",
    "19903":         "GOST_19903-2015.pdf",
    # Лист холоднокатаный
    "19904":         "GOST_19904-90.pdf",
    # Труба бесшовная горячедеформированная
    "труба":         "GOST_53383-2009.pdf",
    "53383":         "GOST_53383-2009.pdf",
    # Профильная труба
    "32931":         "GOST_32931-2015.pdf",
    "профильная":    "GOST_32931-2015.pdf",
    # Двутавр
    "двутавр":       "GOST_35087-2024.pdf",
    "35087":         "GOST_35087-2024.pdf",
    # Шероховатость
    "шероховатость": "GOST_ 2.309-73.pdf",
    "2.309":         "GOST_ 2.309-73.pdf",
}


def list_available_gosts() -> list:
    """Возвращает список доступных файлов ГОСТов."""
    if not os.path.exists(GOSTS_DIR):
        return []
    return [f for f in os.listdir(GOSTS_DIR) if f.endswith('.pdf')]


def find_gost_file(query: str) -> Optional[str]:
    """
    Находит файл ГОСТа по ключевому слову или номеру.
    Например: 'круг', '2590', 'лист', 'труба'
    """
    q = query.lower().strip()
    
    # Прямое совпадение в карте
    for key, fname in GOST_MAP.items():
        if key in q or q in key:
            fpath = os.path.join(GOSTS_DIR, fname)
            if os.path.exists(fpath):
                return fpath
    
    # Поиск по имени файла
    if os.path.exists(GOSTS_DIR):
        for fname in os.listdir(GOSTS_DIR):
            if q.replace(' ', '').replace('-', '') in fname.lower().replace('-', '').replace('_', ''):
                return os.path.join(GOSTS_DIR, fname)
    
    return None


def extract_table_pages(pdf_path: str, min_numbers: int = 40) -> str:
    """
    Извлекает страницы с таблицами размеров из PDF ГОСТа.
    Страница считается табличной если содержит много чисел.
    """
    if fitz is None:
        return "[PyMuPDF не установлен]"
    
    if not os.path.exists(pdf_path):
        return f"[Файл не найден: {pdf_path}]"
    
    try:
        doc = fitz.open(pdf_path)
        table_pages = []
        
        for i, page in enumerate(doc):
            text = page.get_text()
            numbers = re.findall(r'\b\d+[,.]?\d*\b', text)
            if len(numbers) >= min_numbers:
                table_pages.append((i + 1, text))
        
        if not table_pages:
            # Если таблиц нет — берём первые 3 страницы
            result = ""
            for i, page in enumerate(doc[:3]):
                result += page.get_text()
            return result[:3000]
        
        # Берём не более 3 табличных страниц
        result_parts = []
        for page_no, text in table_pages[:3]:
            # Оставляем только строки с числами (убираем мусор)
            clean_lines = []
            for ln in text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                # Строки с числами или заголовки таблиц
                has_num = bool(re.search(r'\d', s))
                is_header = any(kw in s.lower() for kw in [
                    'диаметр', 'толщин', 'размер', 'масса', 'таблица',
                    'сторон', 'номинал', 'предельн', 'отклонен', 'мм', 'кг'
                ])
                if has_num or is_header:
                    clean_lines.append(s)
            
            result_parts.append(f"[Страница {page_no}]\n" + "\n".join(clean_lines[:80]))
        
        return "\n\n".join(result_parts)
    
    except Exception as e:
        logger.error(f"Ошибка чтения ГОСТ PDF {pdf_path}: {e}")
        return f"[Ошибка чтения: {e}]"


def get_gost_context_for_ai(stock_type: str) -> str:
    """
    Главная функция: возвращает текст ГОСТа для подстановки в промпт ИИ.
    stock_type: 'Круг', 'Лист', 'Труба', 'Квадрат' и т.д.
    """
    gost_file = find_gost_file(stock_type)
    
    if not gost_file:
        available = list_available_gosts()
        logger.warn(f"ГОСТ для '{stock_type}' не найден. Доступны: {available}")
        return ""
    
    fname = os.path.basename(gost_file)
    logger.info(f"Читаю ГОСТ: {fname} для типа '{stock_type}'")
    
    tables = extract_table_pages(gost_file)
    
    if not tables:
        return ""
    
    return (
        f"\nТАБЛИЦЫ РАЗМЕРОВ ИЗ {fname}:\n"
        f"{'─' * 50}\n"
        f"{tables}\n"
        f"{'─' * 50}\n"
    )


def get_gost_context_for_query(user_query: str) -> str:
    """
    Определяет какой ГОСТ нужен из текста запроса и возвращает его таблицы.
    """
    query_lower = user_query.lower()
    
    # Определяем тип по запросу
    stock_type = None
    if any(w in query_lower for w in ['круг', 'пруток', '2590', 'вал', 'ось', 'болт']):
        stock_type = 'круг'
    elif any(w in query_lower for w in ['лист', '19903', 'диск', 'фланец', 'крышка', 'шайба']):
        stock_type = 'лист'
    elif any(w in query_lower for w in ['труба', '53383', 'втулка', 'гильза']):
        stock_type = 'труба'
    elif any(w in query_lower for w in ['квадрат', '2591', 'брус']):
        stock_type = 'квадрат'
    elif any(w in query_lower for w in ['шестигранник', '2879']):
        stock_type = 'шестигранник'
    elif any(w in query_lower for w in ['двутавр', 'балка', '35087']):
        stock_type = 'двутавр'
    
    if stock_type:
        return get_gost_context_for_ai(stock_type)
    
    return ""


def get_all_gosts_summary() -> str:
    """Краткая сводка всех доступных ГОСТов."""
    available = list_available_gosts()
    if not available:
        return "Папка gosts не найдена или пуста."
    
    lines = [f"Доступные ГОСТы в папке ({len(available)} файлов):"]
    descriptions = {
        "2590": "Круглый прокат (прутки круглые)",
        "2591": "Квадратный прокат",
        "2879": "Шестигранный прокат",
        "19903": "Листовой прокат горячекатаный",
        "19904": "Листовой прокат холоднокатаный",
        "53383": "Трубы бесшовные горячедеформированные",
        "32931": "Трубы профильные",
        "35087": "Двутавры горячекатаные",
        "2.309": "Шероховатость поверхностей",
    }
    
    for fname in sorted(available):
        desc = ""
        for key, d in descriptions.items():
            if key in fname:
                desc = f" — {d}"
                break
        lines.append(f"  {fname}{desc}")
    
    return "\n".join(lines)