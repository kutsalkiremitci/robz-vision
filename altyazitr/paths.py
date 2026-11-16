"""Uygulamanın kullandığı klasörler (%APPDATA% / %LOCALAPPDATA%) ve eski klasörlerden taşıma."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

from altyazitr import APP_ID, LEGACY_APP_ID

log = logging.getLogger(__name__)

CONFIG_DIR = Path(user_config_dir(APP_ID, appauthor=False, roaming=True))
DATA_DIR = Path(user_data_dir(APP_ID, appauthor=False))
MODEL_DIR = DATA_DIR / "modeller"
LOG_DIR = Path(user_log_dir(APP_ID, appauthor=False))
SETTINGS_FILE = CONFIG_DIR / "ayarlar.json"

LEGACY_DIRS: list[tuple[Path, Path]] = [
    (Path(user_config_dir(LEGACY_APP_ID, appauthor=False, roaming=True)), CONFIG_DIR),
    (Path(user_data_dir(LEGACY_APP_ID, appauthor=False)), DATA_DIR),
]


def resource_dir() -> Path:
    """Paket içi kaynakların (web/, data/) bulunduğu klasör.

    PyInstaller ile paketlendiğinde dosyalar sys._MEIPASS altına açılır."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "altyazitr"
    return Path(__file__).resolve().parent


def _same_tree(a: Path, b: Path) -> bool:
    """İki klasör aynı göreli dosya yollarına ve boyutlara sahipse True (ör. iki kez indirilmiş model)."""
    def listing(root: Path) -> dict[str, int]:
        # .cache: indirme aracının (huggingface) küçük kayıt dosyaları; iki indirme arasında farklı olabilir
        return {str(p.relative_to(root)).lower(): p.stat().st_size for p in root.rglob("*")
                if p.is_file() and ".cache" not in p.relative_to(root).parts}

    try:
        la, lb = listing(a), listing(b)
    except OSError:
        return False
    return bool(la) and la == lb


def migrate_legacy_dirs(pairs: Iterable[tuple[Path, Path]] | None = None) -> list[str]:
    """Eski adla (AltyaziTR) oluşturulmuş ayarları ve indirilmiş modeli yeni klasöre taşır.

    Öğe öğe birleştirir: hedefte zaten olan bir şeyin üzerine yazmaz. Taşıma (aynı sürücüde anlık)
    olmazsa kopyalar. Boşalan eski klasörü siler. Taşınan öğelerin adlarını döndürür."""
    moved: list[str] = []
    for old, new in pairs if pairs is not None else LEGACY_DIRS:
        try:
            if not old.is_dir() or old.resolve() == new.resolve():
                continue
            new.mkdir(parents=True, exist_ok=True)
            for child in list(old.iterdir()):
                dst = new / child.name
                if dst.exists():
                    # açılışta ensure_dirs() boş "modeller" vb. klasörleri önceden oluşturur: boşsa yok say
                    if dst.is_dir() and child.is_dir() and not any(dst.iterdir()):
                        dst.rmdir()
                    elif dst.is_dir() and child.is_dir() and child.name != "Logs" and _same_tree(child, dst):
                        # aynı içerik iki yerde (ör. model iki kez inmiş): eski kopyayı sil, yer kaplamasın
                        shutil.rmtree(child, ignore_errors=True)
                        moved.append(f"{child.name} (yinelenen kopya silindi)")
                        continue
                    else:
                        continue
                try:
                    os.replace(child, dst)
                except OSError:
                    try:
                        if child.is_dir():
                            shutil.copytree(child, dst)
                        else:
                            shutil.copy2(child, dst)
                    except OSError:
                        log.warning("Eski klasörden taşınamadı: %s", child, exc_info=True)
                        continue
                moved.append(child.name)
            try:
                old.rmdir()  # yalnızca boşsa
            except OSError:
                pass
        except OSError:
            log.warning("Eski klasör taşıma hatası: %s → %s", old, new, exc_info=True)
    if moved:
        log.info("Eski 'AltyaziTR' klasörlerinden taşındı: %s", ", ".join(moved))
    return moved


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, MODEL_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
