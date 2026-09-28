
import os
import sys
import json

from core.paths import USER_DATA_DIR, SETTINGS_PATH

import customtkinter as ctk


# ── Пути приложения ──────────────────────────────────────────

# Корневая директория проекта
ROOT = os.path.dirname(os.path.abspath(__file__))

# Определяем, запущено ли приложение как .exe / .app
IS_FROZEN = getattr(sys, "frozen", False)

# Путь к файлу с API-ключами
# В режиме разработки — корень проекта
# В собранном приложении — пользовательская директория
ENV_PATH = os.path.join(
    str(USER_DATA_DIR) if IS_FROZEN else ROOT,
    ".env"
)

# Добавляем корень проекта в sys.path
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── Загрузка .env ────────────────────────────────────────────

try:
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)

except ImportError:

    # Резервный способ загрузки без python-dotenv
    if os.path.exists(ENV_PATH):

        with open(ENV_PATH, "r", encoding="utf-8") as f:

            for line in f:

                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if "=" in line:

                    key, _, val = line.partition("=")

                    key = key.strip()
                    val = val.strip().strip('"').strip("'")

                    if key and key not in os.environ:
                        os.environ[key] = val


# ── Загрузка пользовательской темы ──────────────────────────

ctk.set_appearance_mode("dark")

try:

    if os.path.exists(SETTINGS_PATH):

        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)

        theme_map = {
            "Тёмная": "dark",
            "Светлая": "light",
            "Системная": "system"
        }

        saved_theme = theme_map.get(
            settings.get("ui_theme", ""),
            "dark"
        )

        ctk.set_appearance_mode(saved_theme)

except Exception as e:

    print(f"Не удалось загрузить настройки темы: {e}")


# ── Проверка API-ключа ───────────────────────────────────────

api_key = os.environ.get("OPENAI_API_KEY", "")

if api_key:

    print("✅ OpenAI API-ключ успешно загружен")

else:

    print("⚠️ OpenAI API-ключ не найден")
    print(f"Ожидается файл: {ENV_PATH}")
    print("Содержимое: OPENAI_API_KEY=sk-...")


# ── Запуск приложения ────────────────────────────────────────

from ui.app import App


if __name__ == "__main__":

    App().mainloop()
