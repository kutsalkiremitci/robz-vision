"""Uygulama giriş noktası ve tüm bileşenleri birbirine bağlayan AppContext."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QObject, Qt, QTimer, QTranslator, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from altyazitr import APP_NAME, __version__, winutil
from altyazitr.config import Settings, load_settings, save_settings
from altyazitr.display import SubtitleDisplay
from altyazitr.hotkeys import HotkeyManager
from altyazitr.logutil import guard_stdio, setup_logging
from altyazitr.overlay import ScreenOverlay
from altyazitr.paths import ensure_dirs, migrate_legacy_dirs
from altyazitr.pipeline import Pipeline
from altyazitr.server import AsyncBridge, OverlayServer

log = logging.getLogger("altyazitr")


class AppContext(QObject):
    obs_clients = Signal(int)
    hotkeys_changed = Signal(list)
    needs_region = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.bridge = AsyncBridge()
        self.bridge.start()
        self.server = OverlayServer(settings.server.port)
        self.server.on_clients_changed = self.obs_clients.emit  # asyncio iş parçacığından; sinyal kuyruğa alınır
        self.server_error: str | None = None
        try:
            self.bridge.submit(self.server.start()).result(5)
        except Exception as e:
            log.exception("OBS sunucusu başlatılamadı")
            self.server_error = (f"OBS altyazı sunucusu {settings.server.port} portunda başlatılamadı ({e}).\n"
                                 "Başka bir program bu portu kullanıyor olabilir. 'OBS Kurulumu' sayfasından "
                                 "farklı bir port seçip uygulamayı yeniden başlatın.")

        self.pipeline = Pipeline(settings)
        self.overlay = ScreenOverlay(settings)
        self.overlay.geometry_saved.connect(self.save_later)
        self.display = SubtitleDisplay(settings, self.bridge, self.server, self.overlay)
        self.pipeline.subtitle.connect(self.display.on_subtitle)
        self.pipeline.gone.connect(self.display.on_gone)
        self.pipeline.same.connect(self.display.on_same)
        self.pipeline.running_changed.connect(self._on_running)

        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkey_errors = self.register_hotkeys()

        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save_now)
        self._restart_timer = QTimer(self, singleShot=True, interval=800, timeout=self._do_restart)
        self._restart_pending = False

        self.overlay.start()
        self.display.push_style()

    # --- ayarlar ------------------------------------------------------------
    def save_later(self) -> None:
        self._save_timer.start()

    def save_now(self) -> None:
        try:
            save_settings(self.s)
        except Exception:
            log.exception("Ayarlar kaydedilemedi")

    # --- işlem hattı ----------------------------------------------------------
    def toggle_running(self) -> None:
        if self.pipeline.running:
            self._restart_pending = False
            self._restart_timer.stop()
            self.pipeline.stop()
        elif not self.s.capture.is_ready():
            self.pipeline.status.emit("Önce çevrilecek bölgeyi seçin.")
            self.needs_region.emit()
        else:
            self.pipeline.start()

    def request_restart(self) -> None:
        """Yakalama/OCR/çeviri ayarı değişince çalışan hattı yeni ayarlarla yeniden başlatır.
        Art arda gelen değişiklikler (yazarken, sayı kutusunu çevirirken) tek seferde uygulanır."""
        if self.pipeline.running:
            self._restart_timer.start()

    def _do_restart(self) -> None:
        if self.pipeline.running:
            self._restart_pending = True
            self.pipeline.stop()

    def _on_running(self, running: bool) -> None:
        if not running and self._restart_pending:
            self._restart_pending = False
            QTimer.singleShot(150, self.pipeline.start)

    # --- kısayollar -------------------------------------------------------------
    def register_hotkeys(self) -> list[str]:
        self.hotkeys.unregister_all()
        hk = self.s.hotkeys
        errors = []
        for name, seq in (("toggle", hk.toggle), ("visibility", hk.visibility), ("clear", hk.clear)):
            if seq.strip():
                err = self.hotkeys.register(name, seq)
                if err:
                    errors.append(err)
        self.hotkeys_changed.emit(errors)
        return errors

    def _on_hotkey(self, name: str) -> None:
        if name == "toggle":
            self.toggle_running()
        elif name == "visibility":
            self.display.toggle_visible()
        elif name == "clear":
            self.display.clear()

    # --- kapanış ---------------------------------------------------------------
    def shutdown(self) -> None:
        log.info("Kapatılıyor…")
        self.save_now()
        self.pipeline.stop_and_wait(3.0)
        self.overlay.stop()
        self.hotkeys.unregister_all()
        try:
            self.bridge.submit(self.server.stop()).result(2)
        except Exception:
            pass
        self.bridge.stop()


def _install_translations(app: QApplication) -> None:
    QLocale.setDefault(QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey))
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in ("qtbase_tr", "qt_tr"):
        tr = QTranslator(app)
        if tr.load(name, path):
            app.installTranslator(tr)
            app._tr_refs = getattr(app, "_tr_refs", []) + [tr]  # type: ignore[attr-defined]


def self_test() -> int:
    """Arayüz açmadan temel bileşenleri dener (derlenmiş sürüm ve CI için). 0 = başarılı."""
    import asyncio

    import cv2
    import numpy as np

    from altyazitr.ocr import create_engine
    from altyazitr.textproc import assemble
    from altyazitr.translate import Glossary, QuickMtTranslator, quickmt_downloaded

    guard_stdio()  # doğrudan çağrıldığında da pythonw altında güvenli olsun
    ok = True
    img = np.full((120, 700, 3), 25, np.uint8)
    cv2.putText(img, "Get in the car, Jason!", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
    for key in ("windows", "rapidocr"):
        try:
            eng = create_engine(key, "auto", "auto")
            text, _, _ = assemble(eng.recognize(img))
            log.info("ÖZ TEST OCR %s: %r", key, text)
            ok &= "car" in text.lower()
        except Exception:
            log.exception("ÖZ TEST OCR %s başarısız", key)
            ok = False
    if quickmt_downloaded():
        try:
            tr = QuickMtTranslator(Glossary([["Jason", ""]]))
            tr.prepare(lambda m: None)
            log.info("ÖZ TEST çeviri: %r", tr.translate("Get in the car, Jason!"))
        except Exception:
            log.exception("ÖZ TEST çeviri başarısız")
            ok = False
    else:
        log.info("ÖZ TEST çeviri: model indirilmemiş, atlandı")
    try:
        from altyazitr.server import OverlayServer

        async def srv():
            s = OverlayServer(18765)
            await s.start()
            await s.stop()

        asyncio.run(srv())
        log.info("ÖZ TEST sunucu: tamam")
    except Exception:
        log.exception("ÖZ TEST sunucu başarısız")
        ok = False
    log.info("ÖZ TEST SONUCU: %s", "BAŞARILI" if ok else "HATALI")
    return 0 if ok else 1


def _require_translation_model() -> bool:
    """Yerel çeviri modeli (quickmt) zorunludur: yoksa ilk kurulum penceresinde indirilir.
    False → kullanıcı kurulumu tamamlamadan çıktı; ana pencere açılmamalı."""
    from altyazitr.translate import quickmt_downloaded

    if quickmt_downloaded():
        return True
    log.info("Çeviri modeli bulunamadı; ilk kurulum penceresi açılıyor.")
    from altyazitr.gui import setup_dialog

    if setup_dialog.ensure_translation_model():
        log.info("İlk kurulum tamamlandı.")
        return True
    log.info("İlk kurulum tamamlanmadan çıkıldı.")
    if setup_dialog.download_running():
        # snapshot_download yarıda durdurulamıyor; indirme iş parçacıkları çıkışı bekletmesin.
        # Yarım dosyalar bir sonraki açılışta kaldığı yerden tamamlanır.
        logging.shutdown()
        os._exit(0)
    return False


def main() -> int:
    guard_stdio()  # pythonw/--windowed: stdout/stderr None → os.devnull (her şeyden önce)
    ensure_dirs()
    setup_logging(debug="--debug" in sys.argv)
    log.info("%s %s başlıyor (Python %s)", APP_NAME, __version__, sys.version.split()[0])
    if "--self-test" in sys.argv:
        return self_test()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    from altyazitr.gui.theme import app_icon, apply_theme

    app.setWindowIcon(app_icon())
    _install_translations(app)
    apply_theme(app)

    # Mutex adı eski sürümle aynı kalır: eski ve yeni sürüm aynı anda çalışıp port/kısayol çakışmasın.
    if not winutil.acquire_single_instance("Local\\AltyaziTR_TekOrnek"):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} zaten çalışıyor. Sistem tepsisindeki simgeye bakın.")
        return 0

    # Eski "AltyaziTR" klasörlerindeki ayarlar ve indirilmiş model yeni klasöre taşınır (bir kez).
    migrate_legacy_dirs()

    if not _require_translation_model():
        return 0

    settings = load_settings()
    ctx = AppContext(settings)

    from altyazitr.gui.main_window import MainWindow

    win = MainWindow(ctx)
    win.show()
    if "--start" in sys.argv:
        QTimer.singleShot(500, ctx.pipeline.start)
    try:
        return app.exec()
    finally:
        ctx.shutdown()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
