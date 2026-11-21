r"""Güncelleme servisi (Qt): zamanlama, arka plan işleri ve arayüzün okuduğu durum. Ağ, dosya ve kurulum işleri
altyazitr.updater'dadır; burada iş parçacıkları başlatılır ve sonuçları arayüz iş parçacığında uygulanır.

Zamanlama tek bir QTimer'dır: açılıştan FIRST_CHECK_MS (3 sn) sonra ilk denetim, sonra uygulama açık kaldıkça saatte
bir (CHECK_EVERY_MS). Oran sınırında (403/429) bir sonraki deneme sunucunun söylediği kadar ertelenir (en çok 24 saat).
Bilgisayar uykudan dönünce ya da internet yeni gelince bekleyen denetim zamanlayıcının sırasında yapılır. Denetim
kapalıyken (settings.update.auto_check) zamanlayıcı durur; "Şimdi denetle" (check_now(manual=True)) yine çalışır.
Denetim arayüzü bekletmez (iş parçacığı) ve hatada sessizdir (günlük + Ayarlar'daki durum satırı).

Durum (arayüz `changed` ile okur):
* release: kullanılan sürümden yeni son sürüm (atlanmış olsa da) ya da None; available: atlanmamışsa o.
* phase: "idle" | "downloading" (done/total bayt) | "ready" (doğrulanmış kurulum dosyası) | "installing" (kurulum
  başladı, uygulama kapanıyor) | "failed" (failure: UpdateError).
* kind (updater.install_kind): yalnız "user" kendini günceller; taşınabilir, tüm kullanıcılar için kurulu ve kaynaktan
  çalışan kopyada arayüz sürüm sayfasını tarayıcıda açar, kurulum hiç başlatılmaz.

`found(release, manual)`: atlanmamış yeni sürüm bu oturumda ilk kez bulundu (ya da kullanıcı Şimdi denetle'ye bastı).
Bildirimi arayüz verir (çeviri çalışırken değil; bkz. gui/main_window.py). `post_note`: güncellemeden sonraki ilk
açılışta bir kezlik sonuç ("güncellendi" ya da "kurulamadı"; settings.update.updating_from'dan).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from altyazitr import __version__, updater
from altyazitr.config import Settings
from altyazitr.updater import GITHUB, CheckResult, Release, Source, UpdateChecker, UpdateError

log = logging.getLogger(__name__)

FIRST_CHECK_MS = 3_000  # açılıştan sonra ilk denetim (pencere çizilsin, sonra; denetim iş parçacığında)
CHECK_EVERY_MS = 3_600_000  # sonra saatte bir
CLEANUP_DELAY_MS = 30_000  # önceki güncellemenin kurulum dosyası bu kadar sonra silinir (kurulum çoktan bitmiştir)


class UpdateService(QObject):
    changed = Signal()
    found = Signal(object, bool)  # (Release, manual)
    _checked = Signal(object)  # iş parçacığından: CheckResult
    _progress = Signal(int, int)  # iş parçacığından: (inen, toplam) bayt
    _downloaded = Signal(object)  # iş parçacığından: (Release, Path | UpdateError, sha256)

    def __init__(self, settings: Settings, save: Callable[[], None] | None = None, *,
                 save_now: Callable[[], None] | None = None, source: Source = GITHUB, kind: str | None = None,
                 version: str = __version__, download_dir: Path | None = None,
                 launcher: Callable[..., None] | None = None):
        super().__init__()
        self.s = settings
        self._save = save or (lambda: None)
        self._save_now = save_now or self._save
        self.source = source
        self.version = version
        self.download_dir = Path(download_dir) if download_dir is not None else updater.UPDATE_DIR
        self._launcher = launcher or updater.launch_setup
        self.checker = UpdateChecker(source)
        self.kind = updater.install_kind() if kind is None else kind
        self.checking = False
        self.status = ""  # son denetim: "" | "current" | "available" | "error" | "limited"
        self.error = ""  # son denetim hatasının ayrıntısı (günlük ve ipucu)
        self.release: Release | None = None
        self.target: Release | None = None  # indirilen / kurulmaya hazır sürüm
        self.phase = "idle"
        self.done = self.total = 0
        self.failure: UpdateError | None = None
        self.ready_path: Path | None = None
        self._ready_sha = ""
        self.launched = False
        self._manual = False
        self._notified = ""  # bu oturumda found ile bildirilen sürüm
        self._started = self._closed = False
        self._cancel = threading.Event()
        self._timer = QTimer(self, singleShot=True, timeout=self._on_timer)
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._downloaded.connect(self._on_downloaded)
        self.post_note: tuple[str, str] | None = self._take_post_note()

    # --- durum -----------------------------------------------------------------------------------------------
    @property
    def can_self_update(self) -> bool:
        """Bu kopya kendini güncelleyebilir mi (bu kullanıcıya kurulu, derlenmiş sürüm)?"""
        return self.kind == "user"

    @property
    def available(self) -> Release | None:
        """Kullanıcıya önerilecek yeni sürüm: "Bu sürümü atla" denmemişse release."""
        rel = self.release
        if rel is None or rel.version == self.s.update.skipped_version:
            return None
        return rel

    @property
    def shown(self) -> Release | None:
        """Güncelleme penceresinin gösterdiği sürüm: indirilen/hazır olan, yoksa son yeni sürüm."""
        return self.target if self.phase in ("downloading", "ready", "installing", "failed") and self.target \
            else self.release

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total > 0 else 0.0

    def _take_post_note(self) -> tuple[str, str] | None:
        """Güncellemeden sonraki ilk açılış: sonuç bir kez (metin, düzey); alan boşaltılır."""
        prev = self.s.update.updating_from.strip()
        if not prev:
            return None
        self.s.update.updating_from = ""
        self._save()
        if prev != self.version:
            log.info("Güncelleme tamamlandı: %s → %s", prev, self.version)
            return f"Robz Vision {self.version} sürümüne güncellendi", "info"
        log.warning("Güncelleme kurulamadı: sürüm hâlâ %s (kurulum günlüğü: %s)", self.version, updater.SETUP_LOG)
        return (f"Güncelleme kurulamadı; Robz Vision {self.version} sürümünde\n"
                f"Ayrıntı: Kurulum günlüğü: {updater.SETUP_LOG}", "warn")

    # --- zamanlama ve denetim ----------------------------------------------------------------------------------
    def start(self, first_delay_ms: int = FIRST_CHECK_MS) -> None:
        """Ana pencere açıldıktan sonra (app.main): ilk denetim ve eski indirmelerin temizliği zamanlanır."""
        if self._started or self._closed:
            return
        self._started = True
        if self.s.update.auto_check:
            self._timer.start(max(0, int(first_delay_ms)))
        QTimer.singleShot(CLEANUP_DELAY_MS, self, self._cleanup)

    def _cleanup(self) -> None:
        if self._closed or self.phase != "idle":
            return
        threading.Thread(target=updater.cleanup_downloads, args=(self.download_dir,), name="guncelleme-temizlik",
                         daemon=True).start()

    def _on_timer(self) -> None:
        self.check_now(manual=False)

    def check_now(self, manual: bool = True) -> None:
        """Arka planda tek denetim. manual: kullanıcı "Şimdi denetle"ye bastı (atlanmış sürüm yine gösterilir)."""
        if self._closed:
            return
        self._manual = self._manual or manual
        if self.checking:
            return
        self.checking = True
        self.changed.emit()
        threading.Thread(target=self._check_worker, name="guncelleme-denetim", daemon=True).start()

    def _check_worker(self) -> None:
        try:
            res = self.checker.check()
        except Exception as e:  # check hata fırlatmaz; yine de iş parçacığı sessizce ölmesin
            log.warning("Güncelleme denetimi beklenmedik biçimde bitti", exc_info=True)
            res = CheckResult("error", error=f"{type(e).__name__}: {e}")
        self._checked.emit(res)

    def _on_checked(self, res: CheckResult) -> None:
        manual, self._manual = self._manual, False
        self.checking = False
        if self._closed:
            return
        next_ms = CHECK_EVERY_MS
        if res.status in ("ok", "same", "none"):
            self.s.update.last_check = time.time()
            self.error = ""
            rel = res.release if res.release is not None and updater.is_newer(res.release.version, self.version) \
                else None
            if self.phase in ("idle", "failed"):  # süren indirmenin sürümü değişmez
                self.release = rel
            self.status = "available" if rel is not None else "current"
            log.info("Güncelleme denetimi: %s", f"yeni sürüm {rel.version}" if rel else "güncel")
            self._save()
        elif res.status == "limited":
            self.status, self.error = "limited", res.error
            if res.retry_after:
                next_ms = max(next_ms, int(res.retry_after * 1000))
            log.info("Güncelleme denetimi oran sınırına takıldı (%s); sonraki deneme %d dk sonra", res.error,
                     next_ms // 60000)
        else:
            self.status, self.error = "error", res.error
            log.info("Güncelleme denetlenemedi: %s", res.error)
        if manual and self.release is not None and self.s.update.skipped_version == self.release.version:
            self.s.update.skipped_version = ""  # kullanıcı açıkça sordu: atlanan sürüm yine gösterilir
            self._save()
        if self.s.update.auto_check:
            self._timer.start(min(next_ms, int(updater.MAX_RETRY_S * 1000)))
        self.changed.emit()
        avail = self.available
        if avail is not None and (manual or avail.version != self._notified):
            self._notified = avail.version
            self.found.emit(avail, manual)

    def set_auto(self, on: bool) -> None:
        """Ayarlar → Güncellemeleri denetle. Açılınca ilk denetim kısa süre sonra, kapanınca zamanlayıcı durur."""
        self.s.update.auto_check = bool(on)
        self._save()
        if on and self._started and not self._closed:
            if not self._timer.isActive():
                self._timer.start(FIRST_CHECK_MS)
        else:
            self._timer.stop()
        self.changed.emit()

    def skip(self) -> None:
        """Bu sürümü atla: bu sürüm bir daha bildirilmez; daha yenisi çıkınca yine bildirilir."""
        rel = self.release
        if rel is None or self.phase not in ("idle", "failed"):
            return
        self.s.update.skipped_version = rel.version
        self.phase, self.failure = "idle", None
        self._save()
        log.info("Güncelleme atlandı: %s", rel.version)
        self.changed.emit()

    # --- indirme -------------------------------------------------------------------------------------------------
    def download(self) -> bool:
        """Kurulum dosyasını arka planda indirir ve doğrular (phase: downloading → ready ya da failed). Yalnız kendini
        güncelleyebilen kopyada ve sürümde kurulum dosyası varsa. True: indirme başladı."""
        rel = self.release
        if self._closed or rel is None or self.phase in ("downloading", "installing"):
            return False
        if not self.can_self_update or not rel.installable or rel.setup is None:
            return False
        self.target, self.phase, self.failure = rel, "downloading", None
        self.done, self.total = 0, rel.setup.size
        self.ready_path, self._ready_sha = None, ""
        self._cancel.clear()
        threading.Thread(target=self._download_worker, args=(rel,), name="guncelleme-indirme", daemon=True).start()
        self.changed.emit()
        return True

    def _download_worker(self, rel: Release) -> None:
        try:
            path, sha = updater.download_setup(rel, self._progress.emit, self._cancel,
                                               folder=self.download_dir / rel.version, source=self.source)
            self._downloaded.emit((rel, path, sha))
        except UpdateError as e:
            self._downloaded.emit((rel, e, ""))
        except Exception as e:
            log.warning("Güncelleme indirilemedi", exc_info=True)
            self._downloaded.emit((rel, UpdateError(f"Güncelleme indirilemedi.\nAyrıntı: {type(e).__name__}: {e}",
                                                    "download"), ""))

    def _on_progress(self, done: int, total: int) -> None:
        if self.phase == "downloading":
            self.done, self.total = int(done), int(total)
            self.changed.emit()

    def _on_downloaded(self, payload) -> None:
        rel, result, sha = payload
        if self._closed or rel is not self.target:
            return
        if isinstance(result, UpdateError):
            if result.kind == "cancel":
                self.phase, self.failure = "idle", None
                log.info("Güncelleme indirmesi iptal edildi")
            else:
                self.phase, self.failure = "failed", result
                log.warning("Güncelleme indirilemedi: %s", str(result).replace("\n", " "))
        else:
            self.phase, self.ready_path, self._ready_sha = "ready", Path(result), sha
            self.done = self.total
        self.changed.emit()

    def cancel(self) -> None:
        """Süren indirmeyi durdurur (yarım dosya silinir; phase iş bitince idle olur)."""
        if self.phase == "downloading":
            self._cancel.set()

    # --- kurulum -------------------------------------------------------------------------------------------------
    def install(self) -> bool:
        """Doğrulanmış kurulum dosyasını başlatır. True: kurulum başladı; çağıran uygulamayı hemen kapatmalı
        (kurulum bu sürecin bitmesini bekler). False: başlatılamadı (failure dolu) ya da hazır değil.

        Sıra: dosya yeniden doğrulanır → ayarlar diske yazılır ve yedeklenir → updating_from kaydedilir → tek örnek
        kilidi bırakılır → kurulum başlatılır. Başlatılamazsa kilit geri alınır, uygulama açık kalır."""
        rel = self.target
        if (self.phase != "ready" or rel is None or rel.setup is None or self.ready_path is None
                or not self.can_self_update):
            return False
        try:
            updater.verify_file(self.ready_path, rel.setup.size, self._ready_sha)
        except UpdateError as e:
            self._fail(e)
            return False
        self._save_now()  # yedek güncel ayarlardan alınsın
        updater.backup_settings(self.version)
        self.s.update.updating_from = self.version
        self._save_now()
        from altyazitr import winutil

        winutil.release_single_instance()  # kurulumun AppMutex denetimi takılmasın (bkz. RobzVision.iss)
        try:
            self._launcher(self.ready_path, os.getpid())
        except UpdateError as e:
            winutil.reacquire_single_instance()
            self.s.update.updating_from = ""
            self._save_now()
            self._fail(e)
            return False
        self.launched, self.phase = True, "installing"
        log.info("Güncelleme kurulumu başladı (%s → %s); uygulama kapanıyor", self.version, rel.version)
        self.changed.emit()
        return True

    def _fail(self, e: UpdateError) -> None:
        self.phase, self.failure = "failed", e
        self.ready_path = None
        log.warning("Güncelleme kurulamadı: %s", str(e).replace("\n", " "))
        self.changed.emit()

    def shutdown(self) -> None:
        """Uygulama kapanırken: zamanlayıcı durur, süren indirme kesilir (iş parçacığı beklenmez)."""
        self._closed = True
        self._timer.stop()
        self._cancel.set()
