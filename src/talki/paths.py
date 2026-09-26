import os
from pathlib import Path

from platformdirs import user_config_path, user_data_path

APP_ID = "dev.talki.Talki"

CONFIG_DIR = user_config_path("talki")
DATA_DIR = user_data_path("talki")
AUDIO_DIR = DATA_DIR / "audio"
SPOOL_DIR = DATA_DIR / "spool"
DB_PATH = DATA_DIR / "talki.db"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = DATA_DIR / "talki.log"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/talki-{os.getuid()}"
    Path(base).mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(base)


def socket_path() -> Path:
    return runtime_dir() / "talki.sock"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, AUDIO_DIR, SPOOL_DIR):
        d.mkdir(parents=True, exist_ok=True)
