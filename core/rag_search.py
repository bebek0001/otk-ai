# ===========================
# RAG_SEARCH.PY — семантический поиск похожих эталонов
# ===========================
#
# Идея:
#   Каждый эталон из drawing_db.json превращается в вектор (embedding)
#   через OpenAI text-embedding-3-small. Векторы кэшируются в файл
#   embeddings_cache.json, чтобы не пересчитывать каждый раз.
#
#   Для нового чертежа считаем его вектор и находим топ-N ближайших
#   эталонов по косинусной близости. Эти эталоны идут в промпт GPT
#   как примеры "реши по аналогии".
#
# Поток:
#   1. build_index()      — один раз: считает векторы всех эталонов
#   2. find_similar(text) — для каждого нового чертежа: топ-N похожих
#
# Встраивается в engine.py ПОСЛЕ поиска по хешу и ДО запроса к GPT.

import os
import re
import json
import math
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from openai import OpenAI

from core import logger
from core import drawing_db


from core.paths import EMBEDDINGS_CACHE_PATH as USER_CACHE_PATH

EMBEDDINGS_CACHE_PATH = str(USER_CACHE_PATH)


EMBED_MODEL = "text-embedding-3-small"   # дёшево, поддерживает русский
EMBED_DIM = 1536
TOP_N_DEFAULT = 5


# ============================================================
# КЛИЕНТ OpenAI
# ============================================================

def _client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ============================================================
# ТЕКСТ ЭТАЛОНА ДЛЯ ВЕКТОРИЗАЦИИ
# ============================================================

def _etalon_to_text(rec: Dict[str, Any]) -> str:
    """
    Собирает компактное смысловое описание эталона для эмбеддинга.
    Не весь сырой PDF (там много мусора), а ключевые поля —
    так вектор точнее отражает суть детали.
    """
    parts = [
        f"Деталь: {rec.get('part_name', '')}",
        f"Тип заготовки: {rec.get('stock_type', '')}",
        f"Сортамент: {rec.get('gost_stock', '')}",
        f"Материал: {rec.get('material_mark', '')} {rec.get('material_gost', '')}",
        f"Размер заготовки: {rec.get('d_blank_std_mm', '')}",
        f"Длина заготовки: {rec.get('l_blank_mm', '')}",
        f"Решение: {rec.get('result_line', '').replace(chr(10), ' ')}",
    ]
    return " | ".join(p for p in parts if p.strip().endswith(":") is False)


def _query_to_text(pdf_text: str, part_name: str = "") -> str:
    """
    Готовит текст НОВОГО чертежа для эмбеддинга.
    Чистим мусор, оставляем смысловую часть.
    """
    t = re.sub(r"\s+", " ", (pdf_text or "")).strip()
    # Берём первые ~1500 символов — там штамп и основные размеры
    t = t[:1500]
    prefix = f"Деталь: {part_name}. " if part_name else ""
    return prefix + t


# ============================================================
# ЭМБЕДДИНГИ
# ============================================================

def _embed_one(text: str) -> List[float]:
    """Считает вектор для одного текста."""
    resp = _client().embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


def _embed_batch(texts: List[str]) -> List[List[float]]:
    """Считает векторы для пачки текстов одним запросом (дешевле)."""
    if not texts:
        return []
    resp = _client().embeddings.create(model=EMBED_MODEL, input=texts)
    # порядок сохраняется
    return [d.embedding for d in resp.data]


# ============================================================
# КОСИНУСНАЯ БЛИЗОСТЬ
# ============================================================

def _cosine(a: List[float], b: List[float]) -> float:
    """Косинусная близость двух векторов. 1.0 = идентичны."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ============================================================
# КЭШ ВЕКТОРОВ
# ============================================================

def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(EMBEDDINGS_CACHE_PATH):
        return {"model": EMBED_MODEL, "items": {}}
    try:
        with open(EMBEDDINGS_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("model") != EMBED_MODEL:
            logger.warn(f"Кэш эмбеддингов от другой модели ({data.get('model')}), пересчёт")
            return {"model": EMBED_MODEL, "items": {}}
        return data
    except Exception as e:
        logger.warn(f"Ошибка загрузки кэша эмбеддингов: {e}")
        return {"model": EMBED_MODEL, "items": {}}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(EMBEDDINGS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        logger.info(f"Кэш эмбеддингов сохранён: {len(cache.get('items', {}))} векторов")
    except Exception as e:
        logger.error(f"Ошибка сохранения кэша эмбеддингов: {e}")


# ============================================================
# ПОСТРОЕНИЕ ИНДЕКСА
# ============================================================

def build_index(force: bool = False) -> int:
    """
    Считает векторы для всех эталонов базы и кэширует их.
    Ключ кэша — pdf_text_hash эталона (стабилен, не меняется).
    Пересчитывает только новые/отсутствующие эталоны.

    Возвращает число векторов в индексе.
    """
    records = drawing_db.load_db()
    cache = _load_cache() if not force else {"model": EMBED_MODEL, "items": {}}
    items = cache["items"]

    # Какие эталоны ещё не векторизованы
    to_embed: List[Tuple[str, str]] = []  # (hash, text)
    for r in records:
        h = r.get("pdf_text_hash")
        if not h:
            continue
        if h in items and not force:
            continue
        to_embed.append((h, _etalon_to_text(r)))

    if to_embed:
        logger.info(f"RAG: считаю эмбеддинги для {len(to_embed)} эталонов...")
        # Батчами по 100 (лимит API щедрый, но перестрахуемся)
        BATCH = 100
        for i in range(0, len(to_embed), BATCH):
            chunk = to_embed[i:i + BATCH]
            vecs = _embed_batch([t for _, t in chunk])
            for (h, _), v in zip(chunk, vecs):
                items[h] = v
        _save_cache(cache)
    else:
        logger.info("RAG: индекс актуален, пересчёт не нужен")

    return len(items)


# ============================================================
# ПОИСК ПОХОЖИХ
# ============================================================

def find_similar(
    pdf_text: str,
    part_name: str = "",
    top_n: int = TOP_N_DEFAULT,
    min_score: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Находит top_n самых похожих эталонов на новый чертёж.

    Возвращает список записей эталонов с добавленным полем '_score'
    (косинусная близость, 0..1), отсортированный по убыванию.
    """
    records = drawing_db.load_db()
    if not records:
        return []

    cache = _load_cache()
    items = cache["items"]

    # Если индекс пуст или неполон — достраиваем
    missing = [r for r in records if r.get("pdf_text_hash") and r["pdf_text_hash"] not in items]
    if missing:
        logger.info(f"RAG: индекс неполон ({len(missing)} новых), достраиваю")
        build_index()
        cache = _load_cache()
        items = cache["items"]

    # Вектор запроса
    query_text = _query_to_text(pdf_text, part_name)
    try:
        q_vec = _embed_one(query_text)
    except Exception as e:
        logger.error("RAG: ошибка эмбеддинга запроса", e)
        return []

    # Считаем близость ко всем эталонам
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for r in records:
        h = r.get("pdf_text_hash")
        if not h or h not in items:
            continue
        score = _cosine(q_vec, items[h])
        if score >= min_score:
            scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)

    out: List[Dict[str, Any]] = []
    for score, r in scored[:top_n]:
        rec = dict(r)
        rec["_score"] = round(score, 4)
        out.append(rec)

    if out:
        logger.info(
            f"RAG: найдено {len(out)} похожих, лучший — "
            f"'{out[0].get('part_name')}' (score={out[0]['_score']})"
        )
    return out


# ============================================================
# БЛОК ПРИМЕРОВ ДЛЯ ПРОМПТА GPT
# ============================================================

def build_rag_examples_block(
    pdf_text: str,
    part_name: str = "",
    top_n: int = 3,
    min_score: float = 0.3,
) -> str:
    """
    Главная функция для engine.py.
    Находит похожие эталоны и формирует текстовый блок примеров
    для подстановки в промпт GPT.

    Заменяет/дополняет build_etalon_examples_block из drawing_db.py,
    но подбирает примеры по СМЫСЛУ, а не просто по типу заготовки.
    """
    similar = find_similar(pdf_text, part_name, top_n=top_n, min_score=min_score)
    if not similar:
        return ""

    lines = [
        "ПОХОЖИЕ ПРОВЕРЕННЫЕ ЧЕРТЕЖИ ИЗ БАЗЫ (решай новый по аналогии с ними):"
    ]
    for i, r in enumerate(similar, 1):
        lines.append(
            f"\n[Похожий пример {i}, релевантность {r['_score']}]"
            f"\n  Деталь: {r.get('part_name', '—')} (чертёж {r.get('drawing_no', '—')})"
            f"\n  Тип заготовки: {r.get('stock_type', '—')}"
            f"\n  Материал: {r.get('material_mark', '—')} {r.get('material_gost', '—')}"
            f"\n  ПРАВИЛЬНЫЙ ОТВЕТ: {r.get('result_line', '—').replace(chr(10), ' | ')}"
        )
    lines.append("")
    return "\n".join(lines)


# ============================================================
# ГЛАВНОЕ: ОТВЕТ НА НОВЫЙ ЧЕРТЁЖ ПО ОБРАЗЦАМ ИЗ БАЗЫ
# ============================================================

def solve_via_rag(
    pdf_text: str,
    part_name: str = "",
    model: str = "gpt-4o-mini",
    top_n: int = 5,
    min_score: float = 0.3,
) -> Optional[Dict[str, Any]]:
    """
    ГЛАВНАЯ ФУНКЦИЯ под задачу:
      Пользователь присылает НОВЫЙ чертёж без ответа.
      Система находит похожие ПРОВЕРЕННЫЕ чертежи из базы (с ответами)
      и через GPT формирует ответ для нового по их образцу.

    Возвращает dict:
      {
        "result_line": "...",        # готовый ответ в формате базы
        "based_on": [...],           # на каких эталонах основан (для протокола)
        "best_score": 0.87,          # близость лучшего образца
        "source": "rag"
      }
    или None, если похожих не нашлось / нет интернета.
    """
    similar = find_similar(pdf_text, part_name, top_n=top_n, min_score=min_score)
    if not similar:
        logger.info("RAG: похожих эталонов не найдено — ответ по базе невозможен")
        return None

    # Блок образцов из базы
    examples_lines = []
    for i, r in enumerate(similar, 1):
        examples_lines.append(
            f"[Образец {i}] (похожесть {r['_score']})\n"
            f"  Деталь: {r.get('part_name', '')} (чертёж {r.get('drawing_no', '')})\n"
            f"  Тип заготовки: {r.get('stock_type', '')}\n"
            f"  Материал: {r.get('material_mark', '')} {r.get('material_gost', '')}\n"
            f"  ПРАВИЛЬНЫЙ ОТВЕТ:\n  {r.get('result_line', '').replace(chr(10), chr(10) + '  ')}"
        )
    examples_block = "\n\n".join(examples_lines)

    # Текст нового чертежа (компактно)
    new_text = re.sub(r"\s+", " ", (pdf_text or "")).strip()[:2000]

    prompt = f"""Ты — технолог ОТК машиностроительного завода. Определяешь НЕДОСТАЮЩУЮ спецификацию заготовки на чертеже по ГОСТ.

Ниже — ПРОВЕРЕННЫЕ образцы из базы предприятия (похожие детали с правильными ответами). Используй их как эталон формата и логики подбора.

=== ПОХОЖИЕ ПРОВЕРЕННЫЕ ЧЕРТЕЖИ ИЗ БАЗЫ ===
{examples_block}

=== НОВЫЙ ЧЕРТЁЖ (нужно определить заготовку) ===
Деталь: {part_name}
Текст чертежа:
{new_text}

ЗАДАЧА:
Определи заготовку для НОВОГО чертежа ТОЧНО в том же формате, что в образцах выше.
Ответь СТРОГО в формате (3 строки, как в образцах), без пояснений:

<сортамент с ГОСТами>;
<размер заготовки>;
Масса заготовки, кг: <число>"""

    logger.info(f"RAG: запрос к GPT по {len(similar)} образцам из базы (модель={model})")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("RAG: ошибка запроса к GPT", e)
        return None

    # Чистим возможные ```-обёртки
    answer = _clean_vision_answer(answer)

    logger.info(f"RAG: ответ получен на основе базы (лучший образец score={similar[0]['_score']})")
    return {
        "result_line": answer,
        "based_on": [
            {
                "drawing_no": r.get("drawing_no"),
                "part_name": r.get("part_name"),
                "score": r["_score"],
                "result_line": r.get("result_line"),
            }
            for r in similar
        ],
        "best_score": similar[0]["_score"],
        "source": "rag",
    }


# ============================================================
# VISION + RAG: МОДЕЛЬ СМОТРИТ НА ЧЕРТЁЖ + ОБРАЗЦЫ ИЗ БАЗЫ
# ============================================================

import base64

# Какой провайдер использовать для Vision по умолчанию:
#   "claude" — Anthropic Claude
#   "openai" — GPT-4o
# ВАЖНО: реальный выбор берётся из настроек (settings.json, поле "vision_engine"),
# который переключается в интерфейсе. Эта строка — только запасное значение,
# если настройка не задана.
VISION_PROVIDER = "openai"

VISION_MODEL = "gpt-4o"                      # модель OpenAI для Vision
CLAUDE_VISION_MODEL = "claude-sonnet-4-6"    # модель Claude для Vision


def _get_vision_engine() -> str:
    """
    Возвращает выбранный движок Vision из настроек ('openai' или 'claude').
    Читает settings.json (поле 'vision_engine'). Если нет — VISION_PROVIDER.
    """
    try:
        from core.settings import load_settings
        s = load_settings()
        eng = (s.get("vision_engine", "") or "").strip().lower()
        if eng in ("openai", "claude"):
            return eng
    except Exception:
        pass
    return VISION_PROVIDER


def _claude_client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _is_refusal(text: str) -> bool:
    """Распознаёт отказ модели (чтобы не принять его за валидный ответ)."""
    if not text:
        return True
    t = text.lower()
    markers = [
        "i'm sorry", "i am sorry", "can't assist", "cannot assist",
        "can't help", "cannot help", "unable to assist", "не могу помочь",
        "не могу с этим", "извините", "к сожалению, я не",
    ]
    return any(m in t for m in markers)


def _clean_vision_answer(text: str) -> str:
    """
    Убирает вводные фразы модели ("Анализирую чертёж...", "Вот ответ:" и т.п.)
    и оставляет только 3 содержательные строки результата:
      <сортамент>; / <размер>; / Масса заготовки, кг: <...>
    """
    if not text:
        return ""
    # снять markdown-обёртки
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip()).strip()
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    # выкидываем строки-вступления (заканчиваются двоеточием и не содержат ГОСТ/массу,
    # либо начинаются с типичных вводных слов)
    intro_pat = re.compile(
        r"^(анализ|вот |ответ|определ|рассмотр|смотр|итак|заготовк[а:]|результат)",
        re.IGNORECASE,
    )
    cleaned = []
    for ln in lines:
        low = ln.lower()
        is_intro = (
            (ln.endswith(":") and "гост" not in low and "масса" not in low)
            or bool(intro_pat.match(ln))
        )
        if is_intro:
            continue
        cleaned.append(ln)

    # Ищем «якорь» — строку с массой; результат это она и 2 строки перед ней
    for i, ln in enumerate(cleaned):
        if "масса" in ln.lower():
            start = max(0, i - 2)
            return "\n".join(cleaned[start:i + 1])

    # если массу не нашли — вернём первые 3 содержательные строки
    return "\n".join(cleaned[:3]) if cleaned else text


def _vision_call(instruction: str, img_b64: str, max_tokens: int = 200) -> Optional[str]:
    """
    Единый вызов Vision: шлёт текст + картинку ВЫБРАННОМУ в настройках движку.
    Работает только выбранный движок (без авто-запасного).
    Возвращает текст ответа или None (при ошибке/отказе).
    """
    providers = [_get_vision_engine()]  # только один активный движок
    last_err = None
    for prov in providers:
        try:
            if prov == "claude":
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    logger.warn("Vision: ANTHROPIC_API_KEY не задан, пропускаю Claude")
                    continue
                client = _claude_client()
                msg = client.messages.create(
                    model=CLAUDE_VISION_MODEL,
                    max_tokens=max_tokens,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": "image/png", "data": img_b64}},
                            {"type": "text", "text": instruction},
                        ],
                    }],
                )
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
                if text and not _is_refusal(text):
                    logger.info(f"Vision: ответ от Claude ({CLAUDE_VISION_MODEL})")
                    return text
                if text:
                    logger.warn("Vision: Claude вернул отказ, пробую следующего провайдера")
            else:  # openai
                if not os.environ.get("OPENAI_API_KEY"):
                    continue
                client = _client()
                resp = client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        ],
                    }],
                    temperature=0.1,
                    max_tokens=max_tokens,
                )
                text = resp.choices[0].message.content.strip()
                if text and not _is_refusal(text):
                    logger.info(f"Vision: ответ от OpenAI ({VISION_MODEL})")
                    return text
                if text:
                    logger.warn("Vision: OpenAI вернул отказ, пробую следующего провайдера")
        except Exception as e:
            last_err = e
            logger.warn(f"Vision: провайдер {prov} не сработал: {e}")
            continue
    if last_err:
        logger.error(f"Vision: все провайдеры не сработали: {last_err}")
    return None


def _render_pdf_to_png_b64(pdf_path: str, page: int = 0, zoom: float = 2.5) -> Optional[str]:
    """
    Рендерит страницу PDF в PNG и возвращает base64.
    zoom — увеличение для читаемости размеров и штампа.
    """
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        logger.error(f"Vision: PyMuPDF недоступен: {e}")
        return None
    if not pdf_path or not os.path.exists(pdf_path):
        logger.warn(f"Vision: PDF не найден: {pdf_path}")
        return None
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return None
        p = doc[min(page, len(doc) - 1)]
        mat = fitz.Matrix(zoom, zoom)
        pix = p.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return base64.b64encode(png_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Vision: ошибка рендера PDF→PNG: {e}")
        return None


def solve_via_vision_rag(
    pdf_path: str,
    pdf_text: str = "",
    part_name: str = "",
    model: str = VISION_MODEL,
    top_n: int = 5,
    min_score: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Vision + RAG: GPT-4o СМОТРИТ на изображение чертежа и решает
    недостающую заготовку, опираясь на похожие проверенные эталоны из базы.

    Нужен путь к PDF (для рендера в картинку). pdf_text используется
    только для поиска похожих эталонов (RAG).

    Возвращает dict с result_line / based_on / best_score / source='vision_rag'
    или None (нет картинки / нет интернета / ошибка).
    """
    img_b64 = _render_pdf_to_png_b64(pdf_path)
    if not img_b64:
        logger.info("Vision: не удалось получить картинку чертежа — пропускаю Vision")
        return None

    # Похожие эталоны из базы (по тексту, если он есть).
    # Если поиск похожих упал (например, проблема с эмбеддингами) —
    # Vision всё равно работает, просто без образцов из базы.
    similar = []
    if pdf_text or part_name:
        try:
            similar = find_similar(pdf_text or part_name, part_name, top_n=top_n, min_score=min_score)
        except Exception as e:
            logger.warn(f"Vision: поиск похожих упал, продолжаю без образцов: {e}")
            similar = []

    examples_block = ""
    dominant_hint = ""
    if similar:
        lines = []
        for i, r in enumerate(similar, 1):
            lines.append(
                f"[Похожая деталь {i}] совпадение={r['_score']}\n"
                f"  Деталь: {r.get('part_name','')} (чертёж {r.get('drawing_no','')})\n"
                f"  Тип заготовки: {r.get('stock_type','')}\n"
                f"  Материал: {r.get('material_mark','')} {r.get('material_gost','')}\n"
                f"  Решение:\n  {r.get('result_line','').replace(chr(10), chr(10)+'  ')}"
            )
        examples_block = "\n\n".join(lines)

        # Доминирующий тип заготовки среди похожих + лучший score
        from collections import Counter
        types = [r.get("stock_type", "") for r in similar if r.get("stock_type")]
        best_score = similar[0].get("_score", 0)
        if types:
            top_type, top_cnt = Counter(types).most_common(1)[0]
            dominant_hint = (
                f"\nПОДСКАЗКА ПО ТИПУ: среди {len(similar)} похожих чертежей "
                f"чаще всего встречается тип заготовки «{top_type}» "
                f"({top_cnt} из {len(similar)}), лучшая похожесть {best_score}."
            )

    if examples_block:
        instruction = f"""Ты — опытный технолог-консультант ОТК. Помогаешь определить, из какой заготовки (сортамента) изготовлена деталь на чертеже, по ГОСТ.

Полезный контекст: на нашем предприятии уже есть справочник похожих деталей с готовыми решениями (ниже). Деталь на чертеже обычно показана уже обработанной, поэтому по её внешнему виду трудно понять исходный прокат — обработанная деталь может выглядеть плоской, хотя заготовкой был квадрат или круг. В таких случаях надёжнее ориентироваться на решения для похожих деталей из справочника.

Как рассуждать:
- Тип заготовки (Круг / Квадрат / Лист / Труба / Швеллер) логичнее определить по похожим деталям из справочника, чем по внешнему виду обработанной детали.
- ГОСТ сортамента и формат записи — такие же, как у похожих деталей того же типа.
- Конкретные размеры (сторону, диаметр, длину L, габариты) бери с самого чертежа — по размерным числам.
- Материал и массу — из основной надписи чертежа.
{dominant_hint}

Справочник похожих деталей с готовыми решениями:
{examples_block}

Определи заготовку для детали на изображении и дай ответ ровно 3 строками в том же формате, что в справочнике:

<сортамент с ГОСТами>;
<размер заготовки>;
Масса заготовки, кг: <число>"""
    else:
        instruction = f"""Ты — опытный технолог-консультант ОТК. Помоги определить заготовку (сортамент) для детали на чертеже по ГОСТ.

Посмотри на чертёж: габаритные размеры, диаметры, длину, основную надпись (материал, масса), тип детали. Похожих образцов в справочнике нет — ориентируйся на чертёж и ГОСТ.

Без вступлений и пояснений. Ответь ТОЛЬКО тремя строками, ничего до и после:

<сортамент с ГОСТами>;
<размер заготовки>;
Масса заготовки, кг: <число>"""

    logger.info(f"Vision+RAG: отправляю чертёж в {model} ({len(similar)} образцов из базы)")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            temperature=0.1,
            max_tokens=200,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Vision+RAG: ошибка запроса к GPT", e)
        return None

    answer = _clean_vision_answer(answer)
    logger.info("Vision+RAG: ответ получен по картинке + базе")

    return {
        "result_line": answer,
        "based_on": [
            {"drawing_no": r.get("drawing_no"), "part_name": r.get("part_name"),
             "score": r["_score"], "result_line": r.get("result_line")}
            for r in similar
        ],
        "best_score": similar[0]["_score"] if similar else 0.0,
        "source": "vision_rag",
    }


# ============================================================
# VISION 2-ЭТАПА: ОПИСАНИЕ → ПОИСК ПОХОЖИХ → РЕШЕНИЕ
# ============================================================
#
# Проблема одноэтапного Vision: похожих ищем по сырому тексту PDF,
# который часто мусорный → подбираются не те эталоны (листы вместо квадратов).
#
# Решение: сначала Vision СМОТРИТ на чертёж и описывает деталь чистыми
# словами (форма, габариты, предполагаемый прокат). По этому ЧИСТОМУ
# описанию ищем похожих в базе — соседи получаются точнее. Затем Vision
# решает заготовку, опираясь уже на правильных похожих.

def _vision_describe(pdf_path: str, part_name: str = "", model: str = VISION_MODEL) -> Optional[str]:
    """
    Этап 1: Vision описывает деталь структурно (для последующего поиска похожих).
    Возвращает короткое текстовое описание или None.
    """
    img_b64 = _render_pdf_to_png_b64(pdf_path)
    if not img_b64:
        return None

    prompt = """Ты — технолог. Посмотри на инженерный чертёж детали и опиши её кратко для поиска похожих деталей в справочнике. Укажи:
- форму детали (тело вращения / плоская / призматическая / трубчатая)
- какой прокат вероятнее всего был заготовкой (круг / квадрат / лист / труба / швеллер) и почему
- основные габариты (диаметр или сторона, длина)
- материал из основной надписи

Ответь 3-4 короткими строками, по-деловому."""

    desc = _vision_call(prompt, img_b64, max_tokens=200)
    if desc:
        logger.info(f"Vision-описание получено: {desc[:80]}...")
    return desc


def solve_via_vision_2step(
    pdf_path: str,
    pdf_text: str = "",
    part_name: str = "",
    model: str = VISION_MODEL,
    top_n: int = 5,
    min_score: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Двухэтапный Vision:
      1) Vision описывает деталь чистыми словами (форма, прокат, габариты)
      2) по этому описанию ищем похожих в базе (точнее, чем по тексту PDF)
      3) Vision решает заготовку, опираясь на правильных похожих
    """
    img_b64 = _render_pdf_to_png_b64(pdf_path)
    if not img_b64:
        return None

    # --- Этап 1: описание детали ---
    desc = _vision_describe(pdf_path, part_name, model=model)
    # Запрос для поиска похожих: описание (если есть) + имя детали.
    # Описание чище, чем сырой текст PDF, поэтому соседи точнее.
    search_query = (desc or "") + " " + (part_name or "")
    if not search_query.strip():
        search_query = pdf_text or part_name

    # --- Этап 2: поиск похожих по чистому описанию ---
    similar = []
    try:
        similar = find_similar(search_query, part_name, top_n=top_n, min_score=min_score)
    except Exception as e:
        logger.warn(f"Vision-2step: поиск похожих упал: {e}")
        similar = []

    examples_block = ""
    dominant_hint = ""
    if similar:
        from collections import Counter
        lines = []
        for i, r in enumerate(similar, 1):
            lines.append(
                f"[Похожая деталь {i}] совпадение={r['_score']}\n"
                f"  Деталь: {r.get('part_name','')} (чертёж {r.get('drawing_no','')})\n"
                f"  Тип заготовки: {r.get('stock_type','')}\n"
                f"  Материал: {r.get('material_mark','')} {r.get('material_gost','')}\n"
                f"  Решение:\n  {r.get('result_line','').replace(chr(10), chr(10)+'  ')}"
            )
        examples_block = "\n\n".join(lines)
        types = [r.get("stock_type", "") for r in similar if r.get("stock_type")]
        if types:
            top_type, top_cnt = Counter(types).most_common(1)[0]
            dominant_hint = (
                f"\nСреди {len(similar)} похожих деталей чаще всего тип заготовки "
                f"«{top_type}» ({top_cnt} из {len(similar)})."
            )

    # --- Этап 3: финальное решение по картинке + правильным похожим ---
    instruction = f"""Помоги инженеру-технологу с расчётом заготовки. На чертеже — деталь, по ней нужно подобрать подходящий прокат (сортамент) по ГОСТ.

Что видно на детали:
{desc or '(нет данных)'}

Для справки — как подбирали прокат для похожих деталей того же предприятия:
{examples_block if examples_block else '(похожих примеров нет)'}
{dominant_hint}

Подбери прокат для этой детали: тип проката — по аналогии с похожими деталями выше, конкретные размеры (сторона/диаметр, длина) — по размерам с чертежа.

ВАЖНО: без вступлений и пояснений. Ответь ТОЛЬКО тремя строками, ничего до и после:

<сортамент с ГОСТами>;
<размер заготовки>;
Масса заготовки, кг: <число>"""

    logger.info(f"Vision-2step: финальное решение ({len(similar)} похожих по описанию)")
    answer = _vision_call(instruction, img_b64, max_tokens=200)
    if not answer:
        logger.error("Vision-2step: финальный запрос не дал ответа")
        return None

    answer = _clean_vision_answer(answer)

    return {
        "result_line": answer,
        "based_on": [
            {"drawing_no": r.get("drawing_no"), "part_name": r.get("part_name"),
             "score": r["_score"], "result_line": r.get("result_line")}
            for r in similar
        ],
        "best_score": similar[0]["_score"] if similar else 0.0,
        "description": desc,
        "source": "vision_2step",
    }


# ============================================================
# СТАТИСТИКА
# ============================================================

def index_stats() -> Dict[str, Any]:
    cache = _load_cache()
    records = drawing_db.load_db()
    return {
        "model": cache.get("model"),
        "vectors_cached": len(cache.get("items", {})),
        "etalons_in_db": len(records),
        "cache_path": EMBEDDINGS_CACHE_PATH,
    }