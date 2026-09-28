import json
import os


from core.paths import SETTINGS_PATH

SETTINGS_FILE = str(SETTINGS_PATH)


DEFAULT_AI_PROMPT_PDF = (
    "Ты технолог ОТК. Проверь строго по фактам: отсутствует ли на чертеже ИМЕННО эта заготовка, "
    "и соответствует ли выбранная строка ГОСТ сортамента.\n"
    "Отвечай без воды.\n\n"
    "Формат ответа (строго 4 строки):\n"
    "1) Чертеж: ЕСТЬ/НЕТ упоминание этой заготовки (покажи найденные строки или 'не найдено')\n"
    "2) Недостает на чертеже: <скопируй строку заготовки ровно как дана>\n"
    "3) ГОСТ: подтвердить сортамент (ссылка только [ГОСТxxxx-xxxx])\n"
    "4) Масса: подтвердить m (если масса из штампа — 'соответствует штампу', иначе 'соответствует расчету')\n"
)

DEFAULT_AI_PROMPT_3D = (
    "Ты технолог ОТК. У тебя нет прямого доступа к геометрии 3D-модели, только к метаданным/описанию.\n"
    "Дай предварительный список того, чего чаще всего не хватает на 3D-модели для выдачи заготовки по ГОСТ:\n"
    "- материал и ГОСТ материала\n"
    "- масса (если нужна по ТП)\n"
    "- указание типа заготовки (круг/квадрат/лист/труба)\n"
    "- основные размеры заготовки (D/L, a/L, t/b/L, OD/S/L)\n"
    "Отвечай кратко, списком. Если данных недостаточно — прямо напиши, какие данные нужны.\n"
)

DEFAULTS = {
    "ai_prompt_pdf":      DEFAULT_AI_PROMPT_PDF,
    "ai_prompt_3d":       DEFAULT_AI_PROMPT_3D,
    # ИИ модель
    "ai_model":           "gpt-4o-mini",
    # Движок Vision (распознавание чертежа): "openai" или "claude"
    "vision_engine":      "openai",
    # Припуски (мм)
    "allowance_length_mm": "20",
    "allowance_small_mm":  "5",
    # Плотность стали (кг/м³)
    "density_steel":       "7850",
    # Тема интерфейса
    "ui_theme":            "Тёмная",
    # Язык
    "language":            "Русский",
    # Автосохранение эталонов
    "auto_save_etalon":    "0",
    # Максимальное кол-во страниц PDF для чтения
    "pdf_max_pages":       "3",
    # Папка для экспорта Excel по умолчанию
    "export_folder":       "",
}


def _defaults() -> dict:
    return dict(DEFAULTS)


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return _defaults()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _defaults()
        d = _defaults()
        d.update({k: str(v) for k, v in data.items()})
        if not d.get("ai_prompt_pdf", "").strip():
            d["ai_prompt_pdf"] = DEFAULT_AI_PROMPT_PDF
        if not d.get("ai_prompt_3d", "").strip():
            d["ai_prompt_3d"] = DEFAULT_AI_PROMPT_3D
        return d
    except Exception:
        return _defaults()


def save_settings(data: dict) -> None:
    out = _defaults()
    if isinstance(data, dict):
        out.update({k: str(v) for k, v in data.items()})
        if not out.get("ai_prompt_pdf", "").strip():
            out["ai_prompt_pdf"] = DEFAULT_AI_PROMPT_PDF
        if not out.get("ai_prompt_3d", "").strip():
            out["ai_prompt_3d"] = DEFAULT_AI_PROMPT_3D
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)