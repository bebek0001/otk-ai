
import os
import sys
from pathlib import Path


# Папка с ресурсами приложения
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_user_data_dir() -> Path:
    """
    Папка для хранения пользовательских данных.
    """

    if getattr(sys, "frozen", False):

        if sys.platform == "win32":
            base = Path(
                os.environ.get(
                    "APPDATA",
                    str(Path.home() / "AppData" / "Roaming")
                )
            )

            data_dir = base / "OTK AI"

        else:
            data_dir = (
                Path.home()
                / "Library"
                / "Application Support"
                / "OTK AI"
            )

    else:
        # При разработке сохраняем старое поведение
        data_dir = PROJECT_ROOT

    data_dir.mkdir(parents=True, exist_ok=True)

    return data_dir


USER_DATA_DIR = get_user_data_dir()

SETTINGS_PATH = USER_DATA_DIR / "settings.json"

DB_PATH = USER_DATA_DIR / "drawing_db.json"

# Эталонная база, которая едет вместе с установщиком/исходниками.
# При первом запуске, если у пользователя ещё нет своей базы, она
# копируется в DB_PATH — см. core.drawing_db._install_seed().
SEED_DB_PATH = PROJECT_ROOT / "seed" / "drawing_db.json"

EMBEDDINGS_CACHE_PATH = (
    USER_DATA_DIR / "embeddings_cache.json"
)

# Журнал обработки: по одному файлу в день, в каждом — построчно JSON
# с тем, какой чертёж обрабатывался, что из него было прочитано и чем
# закончилась обработка. См. core.activity_log.
PROCESSING_LOG_DIR = USER_DATA_DIR / "processing_log"

GOSTS_DIR = PROJECT_ROOT / "gosts"
