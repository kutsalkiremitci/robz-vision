"""İlk kurulum: zorunlu yerel çeviri modelini (quickmt) indirir.

Model yoksa uygulama ana pencereyi açmadan önce bu pencereyi gösterir. İndirme arka planda
(QThread) yapılır; arayüz donmaz. Kullanıcı pencereyi kapatırsa uygulama açılmaz.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from altyazitr import APP_NAME
from altyazitr import translate as T
from altyazitr.gui import icons
from altyazitr.gui.dialogs import AlertLabel, brand_pixmap, font_css
from altyazitr.gui.theme import app_icon
from altyazitr.gui.tokens import ICON, SPACE, C, F

log = logging.getLogger(__name__)

# Kullanıcı indirme sürerken çıktıysa iş parçacığı burada tutulur (çalışırken yok edilmesin).
_abandoned: list[QThread] = []


def download_running() -> bool:
    """Yarıda bırakılmış ve hâlâ süren bir indirme var mı?"""
    _abandoned[:] = [t for t in _abandoned if t.isRunning()]
    return bool(_abandoned)


class _DownloadThread(QThread):
    progressed = Signal(float)
    failed = Signal(str)

    def run(self) -> None:
        try:
            # Modül üzerinden çağrılır: testlerde T.download_quickmt / T.QUICKMT_DIR değiştirilebilir.
            T.download_quickmt(lambda _m: None, progress=self.progressed.emit)
        except Exception as e:  # TranslatorError dahil
            log.warning("İlk kurulum indirmesi başarısız: %s", e)
            self.failed.emit(str(e) or type(e).__name__)


class SetupDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} kurulumu")
        self.setWindowIcon(app_icon())
        self.setModal(True)
        self.setMinimumWidth(460)
        self._thread: _DownloadThread | None = None
        self._failed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["l"] + 2)
        lay.setSpacing(0)

        # başlık: marka işareti + başlık + boyut satırı
        head = QHBoxLayout()
        head.setSpacing(SPACE["l"])
        mark = QLabel()
        mark.setPixmap(brand_pixmap(52))
        mark.setFixedSize(52, 52)
        mark.setAccessibleName(APP_NAME)
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(SPACE["xs"])
        col.setContentsMargins(0, 3, 0, 0)
        self.title = QLabel("Ücretsiz çeviri modeli indiriliyor")
        self.title.setStyleSheet(font_css(F.DISPLAY_SEMIBOLD, 20, C.TEXT))
        col.addWidget(self.title)
        size_line = QLabel("Yaklaşık 400 MB, yalnızca bir kez indirilir")
        size_line.setStyleSheet(font_css(F.BODY, F.BODY_SIZE, C.TEXT_2))
        col.addWidget(size_line)
        head.addLayout(col, 1)
        lay.addLayout(head)
        lay.addSpacing(SPACE["xl"])

        # ilerleme: ince çubuk + Bahnschrift rakamlarla miktar
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setAccessibleName("İndirme ilerlemesi")  # görünüm temadan (QProgressBar)
        lay.addWidget(self.bar)
        lay.addSpacing(SPACE["s"])
        self.amount = QLabel("")
        self.amount.setStyleSheet(font_css(F.DISPLAY, F.BODY_SIZE, C.TEXT_2))
        self.amount.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.amount)

        # hata: uyarı ikonu + kısa ileti (tamamı tooltip'te)
        self.error = AlertLabel("", C.ORANGE)
        self.error.hide()
        lay.addSpacing(SPACE["s"])
        lay.addWidget(self.error)
        lay.addSpacing(SPACE["xl"])

        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        btns.addStretch(1)
        self.quit_btn = QPushButton("Çıkış")
        self.quit_btn.setAutoDefault(False)
        self.quit_btn.clicked.connect(self.reject)
        self.retry_btn = QPushButton("Tekrar dene")
        self.retry_btn.setObjectName("primary")
        self.retry_btn.setIcon(icons.icon("refresh", C.INVERSE))
        self.retry_btn.setIconSize(QSize(ICON["s"], ICON["s"]))
        self.retry_btn.clicked.connect(self.start)
        self.retry_btn.hide()
        btns.addWidget(self.quit_btn)
        btns.addWidget(self.retry_btn)
        lay.addLayout(btns)

        self._set_fraction(0.0)

    # --- indirme --------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._failed = False
        self.title.setText("Ücretsiz çeviri modeli indiriliyor")
        self.error.hide()
        self.error.setToolTip("")
        self.retry_btn.hide()
        th = _DownloadThread()  # ebeveynsiz: pencere kapanınca çalışan iş parçacığı yok edilmesin
        th.progressed.connect(self._set_fraction)
        th.failed.connect(self._on_failed)
        th.finished.connect(self._on_finished)
        self._thread = th
        th.start()

    def _set_fraction(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        self.bar.setValue(round(frac * 1000))
        self.amount.setText(f"{frac * T.QUICKMT_TOTAL_MB:.0f} / {T.QUICKMT_TOTAL_MB:.0f} MB")

    def _on_failed(self, msg: str) -> None:
        self._failed = True
        self._show_error(msg)

    def _on_finished(self) -> None:
        if self._thread is not None:
            self._thread.wait(2000)  # run() döndü; QThread tamamen bitsin (yok edilirken çalışıyor olmasın)
        if self._failed or not self.isVisible():
            return
        if T.quickmt_downloaded():
            self._set_fraction(1.0)
            log.info("Çeviri modeli hazır.")
            self.accept()
        else:
            self._show_error("Model dosyaları eksik indirildi. Lütfen tekrar deneyin.\n"
                             f"Ayrıntı: {T.QUICKMT_DIR}")

    def _show_error(self, msg: str) -> None:
        self.title.setText("Kurulum tamamlanamadı")
        self.error.setText(msg.splitlines()[0] if msg else "Bilinmeyen hata.")
        self.error.setToolTip(msg)
        self.error.show()
        self.retry_btn.show()
        self.retry_btn.setDefault(True)
        self.retry_btn.setFocus()
        self.adjustSize()

    # --- kapatma ----------------------------------------------------------------
    def reject(self) -> None:
        th = self._thread
        if th is not None and th.isRunning():
            log.info("İlk kurulum indirme sürerken kapatıldı.")
            _abandoned.append(th)
        super().reject()

    def showEvent(self, e) -> None:  # noqa: N802 - Qt
        super().showEvent(e)
        if self._thread is None:
            self.start()


def ensure_translation_model(parent: QWidget | None = None) -> bool:
    """Model kuruluysa hemen True. Değilse kurulum penceresini gösterir; indirme başarılı ve
    dosyalar tamamsa True, kullanıcı çıktıysa False."""
    if T.quickmt_downloaded():
        return True
    dlg = SetupDialog(parent)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    return accepted and T.quickmt_downloaded()
