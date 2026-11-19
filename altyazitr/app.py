"""Uygulama giriş noktası ve tüm bileşenleri birbirine bağlayan AppContext.

Açılış sırası (main): tek örnek denetimi → ayarlar → yerel yapay zekâ kurulum servisi (AiSetup) → ilk açılış penceresi
(hızlı çeviri modeli yoksa; uygun ekran kartında yapay zekâyı da kurar) → AppContext → ana pencere.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QObject, QTimer, QTranslator, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from altyazitr import APP_NAME, __version__, winutil
from altyazitr.ai_setup import AiSetup
from altyazitr.config import Settings, load_settings, save_settings
from altyazitr.display import SubtitleDisplay
from altyazitr.hotkeys import HotkeyManager
from altyazitr.logutil import guard_stdio, setup_logging
from altyazitr.overlay import ScreenOverlay
from altyazitr.paths import ensure_dirs
from altyazitr.pipeline import Pipeline
from altyazitr.server import AsyncBridge, OverlayServer
from altyazitr.translate import release_ollama_models
from altyazitr.update_service import UpdateService

log = logging.getLogger("altyazitr")

# Önceki açılışta yarıda kalan yapay zekâ kurulumu açılıştan bu kadar sonra kendiliğinden sürer (açılış ağırlaşmasın)
AI_RESUME_DELAY_MS = 3000


class AppContext(QObject):
    """Uygulamanın bileşenleri (OBS sunucusu, işlem hattı, ekran katmanı, kısayollar, yerel yapay zekâ kurulumu) ve
    aralarındaki bağlar. Arayüz (gui/main_window.py) bunlara buradan ulaşır.

    `ai` (altyazitr.ai_setup.AiSetup) yerel yapay zekâ kurulum servisidir: main() onu ilk açılış penceresinden önce
    oluşturur ve buraya verir. Arayüz ilerlemeyi `ai.changed`, sonucu `ai.finished(başarılı, ileti)` ile alır. Burada
    bağlı olanlar: çeviri çalışırken kurulumun indirmesi duraklar (pipeline.running_changed → ai.on_pipeline_running),
    yarıda kalan kurulum açılıştan biraz sonra kendiliğinden sürer (ai.resume_pending), kapanışta iş durur
    (ai.shutdown; süren Ollama kurulum süreci öldürülmez, yarım iş sonraki açılışta sürer).

    Pencere açmayan bildirimler tek kanaldan geçer: notify() → notified(kısa metin, ayrıntı, düzey). İşlem hattının
    notice'i (yerel yapay zekâ hazırlanamadı, hızlı çeviri kullanılıyor) burada bağlıdır; arayüz notified'ı tepsi
    balonu / durum satırıyla gösterir ve yapay zekâ kurulumunun sonucunu (ai.finished) da notify() ile verir.

    `updater` (altyazitr.update_service.UpdateService) yeni sürümü saatte bir denetler; main() ana pencere açılınca
    başlatır (updater.start). Kullanıcı Güncelle'ye basıp kurulum başlarsa (updater.launched) main() kapanıştan sonra
    süreci hemen bitirir: kurulum bu sürecin bitmesini bekler."""

    obs_clients = Signal(int)
    needs_region = Signal()
    notified = Signal(str, str, str)  # (kısa metin, ayrıntı, düzey: "info" | "warn"); bkz. notify()

    def __init__(self, settings: Settings, ai: AiSetup | None = None):
        super().__init__()
        self.s = settings
        # main() verir (ilk açılış penceresi yapay zekâyı aynı servisle kurar); verilmezse (araçlar) burada oluşur
        self.ai = ai if ai is not None else AiSetup(settings, save=self.save_now)
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
        self.pipeline.running_changed.connect(self.ai.on_pipeline_running)  # çeviri çalışırken indirme duraklar
        self.pipeline.notice.connect(self._on_pipeline_notice)

        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkey_errors = self.register_hotkeys()

        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save_now)
        self.updater = UpdateService(settings, save=self.save_later, save_now=self.save_now)
        self._restart_timer = QTimer(self, singleShot=True, interval=800, timeout=self._do_restart)
        self._restart_pending = False
        # Yarıda kalan yapay zekâ kurulumu (hatayla bitmediyse) biraz sonra sürer; --start ile çeviri başladıysa iş
        # duraklamış başlar. Plan arka planda çıkarılır: arayüz donmaz.
        self._ai_resume_timer = QTimer(self, singleShot=True, interval=AI_RESUME_DELAY_MS,
                                       timeout=self.ai.resume_pending)
        self._ai_resume_timer.start()

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

    # --- bildirimler ----------------------------------------------------------
    def notify(self, text: str, level: str = "info") -> None:
        """Kullanıcıya pencere açmadan bildirir (tepsi balonu, durum satırı; uyarı penceresi yok): notified yayılır.
        text: 1. satır kısa metin, sonraki satırlar ayrıntı (modüllerin hata biçimi; "Ayrıntı: " öneki atılır).
        level: "info" | "warn". Arayüz iş parçacığında çağrılır; boş metin yok sayılır."""
        head, _, rest = (text or "").strip().partition("\n")
        head, detail = head.strip(), rest.strip().removeprefix("Ayrıntı:").strip()
        if not head:
            return
        log.info("Bildirim: %s%s", head, f" ({detail})" if detail else "")
        self.notified.emit(head, detail, level)

    @Slot(str)
    def _on_pipeline_notice(self, text: str) -> None:
        """İşlem hattının bildirimi (iş parçacığından kuyrukla gelir): hat sürüyor, yalnız haber verilir."""
        self.notify(text, "warn")

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
        self.updater.shutdown()
        self.save_now()
        self._ai_resume_timer.stop()
        # Yapay zekâ kurulumu durur: indirme isteği kesilir, süren Ollama kurulum süreci öldürülmez, yarım iş sonraki
        # açılışta sürer. Hattan önce: hattın durması duraklamış işi yeniden başlatmasın.
        self.ai.shutdown()
        self.pipeline.stop_and_wait(3.0)
        # Hat dururken model bellekte bırakılır (yeniden Başlat anında hazır olsun); uygulama kapanırken boşaltılır
        release_ollama_models()
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


def _first_setup(settings: Settings, ai: AiSetup) -> bool:
    """Yerel hızlı çeviri modeli (quickmt) zorunludur: yoksa ilk açılış penceresi açılır, model orada iner; uygun
    ekran kartında yerel yapay zekâ da önerilir ve `ai` ile kurulur (iş ana pencere açıldıktan sonra arka planda
    sürer). Model varsa (1.0.x / 1.1.0'dan güncelleyen kullanıcı) pencere açılmaz, hiçbir şey inmez.
    False → kullanıcı kurulumu tamamlamadan çıktı; ana pencere açılmamalı."""
    from altyazitr.translate import quickmt_downloaded

    if quickmt_downloaded():
        return True
    log.info("Çeviri modeli bulunamadı; ilk kurulum penceresi açılıyor.")
    from altyazitr.gui import setup_dialog

    if setup_dialog.run_first_setup(settings, ai):
        log.info("İlk kurulum tamamlandı.")
        return True
    log.info("İlk kurulum tamamlanmadan çıkıldı.")
    ai.shutdown()  # pencerenin başlattığı yapay zekâ işi durur; yarım iş sonraki açılışta sürer
    if setup_dialog.download_running():
        # snapshot_download yarıda durdurulamıyor; indirme iş parçacıkları çıkışı bekletmesin.
        # Yarım dosyalar bir sonraki açılışta kaldığı yerden tamamlanır.
        logging.shutdown()
        os._exit(0)
    return False


def _open_context() -> AppContext | None:
    """Ayarlar → yerel yapay zekâ kurulum servisi → ilk açılış penceresi → AppContext. Servis ilk açılış
    penceresinden önce oluşur (pencere yapay zekâyı onunla kurar) ve AppContext'e geçer (arayüz ilerlemeyi ve sonucu
    ondan okur). None: kullanıcı ilk kurulumu tamamlamadan çıktı; ana pencere açılmamalı."""
    settings = load_settings()
    ai = AiSetup(settings, save=lambda: save_settings(settings))
    if not _first_setup(settings, ai):
        return None
    return AppContext(settings, ai)


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

    # Mutex adı kurulum programındaki AppMutex ile aynı olmalı (installer/RobzVision.iss).
    if not winutil.acquire_single_instance("Local\\RobzVision_TekOrnek"):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} zaten çalışıyor. Sistem tepsisindeki simgeye bakın.")
        return 0

    ctx = _open_context()
    if ctx is None:
        return 0

    from altyazitr.gui.main_window import MainWindow

    win = MainWindow(ctx)
    win.show()
    ctx.updater.start()  # ilk denetim 10 sn sonra, sonra saatte bir (arka planda)
    if "--start" in sys.argv:
        QTimer.singleShot(500, ctx.pipeline.start)
    try:
        return app.exec()
    finally:
        ctx.shutdown()
        if ctx.updater.launched:
            # Kurulum bu sürecin bitmesini bekliyor (RobzVision.iss /BEKLE): ayarlar kaydedildi, hat durdu, model
            # boşaltıldı; kalan iş parçacıkları çıkışı geciktirmesin.
            log.info("Güncelleme için çıkılıyor")
            logging.shutdown()
            os._exit(0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
