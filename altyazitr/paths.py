"""Uygulamanın kullandığı klasörler (%APPDATA% / %LOCALAPPDATA%) ve paket içi kaynaklar."""

from __future__ import annotations

import sys
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

from altyazitr import APP_ID

CONFIG_DIR = Path(user_config_dir(APP_ID, appauthor=False, roaming=True))
DATA_DIR = Path(user_data_dir(APP_ID, appauthor=False))
MODEL_DIR = DATA_DIR / "modeller"
LOG_DIR = Path(user_log_dir(APP_ID, appauthor=False))
SETTINGS_FILE = CONFIG_DIR / "ayarlar.json"


def resource_dir() -> Path:
    """Paket içi kaynakların (web/, data/) bulunduğu klasör.

    PyInstaller ile paketlendiğinde dosyalar sys._MEIPASS altına açılır."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "altyazitr"
    return Path(__file__).resolve().parent


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, MODEL_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
