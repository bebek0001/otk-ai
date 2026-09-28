import os
import sys

from core.paths import USER_DATA_DIR, SETTINGS_PATH


# гарантируем, что корень проекта в sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Загружаем .env файл ──────────────────────────────────────
# Сначала пробуем через python-dotenv (если установлен)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    # Fallback: читаем .env вручную
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
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

# ── Применяем сохранённую тему ──────────────────────────────
try:
    import json
    _sf = os.path.join(ROOT, "settings.json")
    if os.path.exists(_sf):
        with open(_sf, "r", encoding="utf-8") as _f:
            _s = json.load(_f)
        _theme_map = {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}
        _saved = _theme_map.get(_s.get("ui_theme", ""), "dark")
        import customtkinter as _ctk
        _ctk.set_appearance_mode(_saved)
except Exception:
    pass

# ── Проверяем API ключ ───────────────────────────────────────
api_key = os.environ.get("OPENAI_API_KEY", "")
if api_key:
    print(f"✅ OpenAI API ключ загружен ({api_key[:8]}...)")
else:
    print("⚠️  OpenAI API ключ не найден. Проверь файл .env")
    print(f"   Ожидается файл: {os.path.join(ROOT, '.env')}")
    print("   Содержимое: OPENAI_API_KEY=sk-...")

import customtkinter as ctk
from ui.app import App

ctk.set_appearance_mode("dark")

if __name__ == "__main__":
    App().mainloop()