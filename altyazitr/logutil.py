"""Dosyaya dönen günlük (log) kaydı ve yakalanmamış hataların kaydı."""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from altyazitr.paths import LOG_DIR

log = logging.getLogger("altyazitr")


def guard_stdio() -> None:
    """pythonw.exe ve PyInstaller --windowed altında sys.stdout/sys.stderr None olur; doğrudan
    .write() çağıran kütüphaneler (tqdm ilerleme çubukları vb.) "'NoneType' object has no attribute
    'write'" hatası verir. Eksik akışları boş bir dosyaya (os.devnull) yönlendirir."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def has_console(stream) -> bool:
    """Akış gerçek bir çıktıya mı gidiyor (None veya os.devnull değil)?"""
    return stream is not None and getattr(stream, "name", None) != os.devnull


def setup_logging(debug: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    fh = RotatingFileHandler(LOG_DIR / "robzvision.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # pythonw / --windowed: konsol yok (stdout None ya da guard_stdio ile os.devnull); ekleme
    if has_console(sys.stdout):
        # Çıktı bir boruya/dosyaya yönlendirildiğinde Windows ANSI kod sayfası (ör. cp1252) Türkçe harfleri
        # yazamaz ve her satırda "Logging error" basılır; UTF-8 kullan, yazılamayanı kaçışla göster.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # gürültülü kütüphaneleri kıs
    for noisy in ("aiohttp.access", "urllib3", "huggingface_hub", "httpx", "httpx2", "httpcore", "RapidOCR"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        log.critical("Yakalanmamış hata", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        log.critical("İş parçacığında yakalanmamış hata (%s)", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
