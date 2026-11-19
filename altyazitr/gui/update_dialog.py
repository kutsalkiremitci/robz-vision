"""Güncelleme penceresi ve güncelleme durumunun kısa metinleri (kenar çubuğu, Ayarlar). Servis:
altyazitr.update_service.UpdateService (`upd`).

Pencere: "Robz Vision X.Y.Z yayınlandı", kullanılan sürüm, Neler yeni? (sürüm notları, Markdown; HTML işlenmez, resim ve
yerel dosya yüklenmez, bağlantılar yalnız tarayıcıda açılır), Sürüm sayfası bağlantısı ve düğmeler: Bu sürümü atla,
Sonra, Güncelle. Güncelle: kurulum dosyası iner ve doğrulanır (ilerleme çubuğu, İptal); hazır olunca çeviri çalışıyorsa
"kapanıp yeniden açılacak" onayı istenir, sonra on_install() (ana pencere: kurulumu başlatır ve uygulamayı kapatır).
Kendini güncelleyemeyen kopyada (taşınabilir, tüm kullanıcılar için kurulu, kaynaktan) birincil düğme İndir'dir: sürüm
sayfası tarayıcıda açılır, hiçbir şey kurulmaz. İndirme sürerken pencere kapatılırsa indirme iptal edilir.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from altyazitr import APP_NAME
from altyazitr.gui.ai_setup_view import fmt_pair, fmt_percent
from altyazitr.gui.dialogs import AlertLabel, font_css
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F
from altyazitr.gui.translate_page import with_icon

TOAST_TEXT = "Robz Vision {v} yayınlandı: güncellemek için tıklayın"
TOAST_MS = 10000
ROW_TEXT = "Güncelleme var"
CONFIRM_TITLE = "Güncelleme için Robz Vision kapanıp yeniden açılacak"
CONFIRM_TEXT = "Çeviri durur. Yeni sürüm açılınca Başlat'a basarak sürdürebilirsiniz."
NO_NOTES = "Bu sürüm için not yazılmamış."
DIALOG_W = 520

# Kendini güncelleyemeyen kopyalar: neden ve ne yapılacağı (pencerenin alt satırı)
MANUAL_NOTES = {
    "portable": "Taşınabilir sürüm kendini güncelleyemez. Yeni sürümü indirip eski klasörün yerine çıkarın; "
                "ayarlarınız korunur.",
    "all_users": "Robz Vision bu bilgisayarda tüm kullanıcılar için kurulmuş. Yeni sürümü indirip kurun "
                 "(yönetici izni ister); ayarlarınız korunur.",
    "source": "Kaynak koddan çalışıyor: güncelleme kurulmaz. Yeni sürümü git ile alın.",
}
AUTO_NOTE = "Güncelle'ye basınca yeni sürüm iner; Robz Vision kapanır, kurulur ve kendiliğinden açılır."


# --- kısa metinler -----------------------------------------------------------------------------------------------
def sidebar_status(upd: Any) -> tuple[str, str, str] | None:
    """Kenar çubuğundaki satır: (durum, kısa yazı, ipucu); gösterilecek bir şey yoksa None (satır gizli).
    Durum _UpdateRow.LOOK anahtarıdır: available, running, failed."""
    if upd is None:
        return None
    phase = str(getattr(upd, "phase", "idle"))
    rel = getattr(upd, "shown", None)
    v = getattr(rel, "version", "")
    if phase == "downloading":
        tip = f"Robz Vision {v} indiriliyor: {fmt_pair(upd.done, upd.total)}"
        return "running", f"Güncelleme {fmt_percent(upd.fraction)}", tip
    if phase in ("ready", "installing"):
        return "running", "Güncelleme hazır", f"Robz Vision {v} kurulmaya hazır"
    if phase == "failed":
        failure = getattr(upd, "failure", None)
        return "failed", "Güncellenemedi", getattr(failure, "short", "") or "Güncelleme kurulamadı"
    avail = getattr(upd, "available", None)
    if avail is not None:
        return "available", ROW_TEXT, f"Robz Vision {avail.version} yayınlandı. Ayrıntılar ve güncelleme için tıklayın."
    return None


def when_text(ts: float, now: float | None = None) -> str:
    """Son denetimin zamanı: bugünse "14:05", değilse "18.11.2026 14:05"."""
    t, n = time.localtime(ts), time.localtime(time.time() if now is None else now)
    if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
        return time.strftime("%H:%M", t)
    return time.strftime("%d.%m.%Y %H:%M", t)


def settings_status(upd: Any, settings: Any, now: float | None = None) -> tuple[str, str, str]:
    """Ayarlar → Uygulama → Güncellemeler durum satırı: (metin, StatusLine türü, ipucu)."""
    if upd is None:
        return "", "info", ""
    last = float(getattr(settings.update, "last_check", 0.0) or 0.0)
    last_tip = f"Son denetim: {when_text(last, now)}" if last > 0 else ""
    if upd.checking:
        return "Denetleniyor…", "busy", ""
    if upd.phase == "downloading":
        return f"Güncelleme indiriliyor {fmt_percent(upd.fraction)}", "busy", ""
    rel = upd.release
    if rel is not None:
        if upd.available is None:
            return (f"Sürüm {rel.version} atlandı", "info",
                    "Şimdi denetle'ye basınca yine gösterilir. Daha yeni bir sürüm çıkınca kendiliğinden bildirilir.")
        return f"Sürüm {rel.version} yayınlandı", "info", last_tip
    if upd.status == "error":
        return "Denetlenemedi: internet bağlantınızı denetleyin", "warn", upd.error
    if upd.status == "limited":
        return "GitHub yanıt vermiyor; sonra yeniden denenecek", "warn", upd.error
    if upd.status == "current":
        return f"Robz Vision güncel. Son denetim {when_text(last, now)}", "ok", ""
    if last > 0:
        return f"Son denetim {when_text(last, now)}", "info", ""
    return "Henüz denetlenmedi", "info", ""


def _open_web(url: QUrl | str) -> None:
    """Yalnız http(s) bağlantıları, varsayılan tarayıcıda."""
    url = url if isinstance(url, QUrl) else QUrl(url)
    if url.scheme() in ("http", "https"):
        QDesktopServices.openUrl(url)


def confirm_restart(parent: QWidget | None) -> bool:
    """Çeviri çalışırken: uygulama kapanıp yeniden açılacak; sürdürülsün mü?"""
    box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, CONFIRM_TITLE, parent=parent)
    box.setInformativeText(CONFIRM_TEXT)
    go = box.addButton("Kapat ve güncelle", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Vazgeç", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(go)
    box.exec()
    return box.clickedButton() is go


class ReleaseNotes(QTextBrowser):
    """Sürüm notları: Markdown (GitHub biçimi) HTML'siz işlenir; resim ve başka kaynak yüklenmez, bağlantılar yalnız
    tarayıcıda açılır (pencere içinde gezinme yok)."""

    FEATURES = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("releaseNotes")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(_open_web)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setAccessibleName("Sürüm notları")
        self.document().setDefaultStyleSheet(f"a {{ color: {C.CYAN}; }}")
        self.document().setDocumentMargin(SPACE["s"])
        self.setStyleSheet(f"QTextBrowser#releaseNotes {{ background: {C.PANEL}; border: 1px solid {C.LINE};"
                           f" border-radius: {RADIUS['l']}px; padding: {SPACE['xs']}px; color: {C.TEXT};"
                           f" {font_css(F.BODY, F.BODY_SIZE)} }}")

    def set_notes(self, markdown: str) -> None:
        self.document().setMarkdown(markdown.strip() or NO_NOTES, self.FEATURES)

    def loadResource(self, _type, _name):  # noqa: N802 - Qt
        return None


class UpdateDialog(QDialog):
    """Güncelleme penceresi (bkz. modül açıklaması). Görünümü servisin durumundan kurar (upd.changed)."""

    def __init__(self, parent: QWidget | None, upd: Any, running: Callable[[], bool],
                 on_install: Callable[[], None]):
        super().__init__(parent)
        self.upd = upd
        self._running = running
        self._on_install = on_install
        self._requested = False  # kullanıcı Güncelle'ye bastı: hazır olunca kurulum başlar
        self._notes_for = ""
        self.setWindowTitle("Güncelleme")
        self.setFixedWidth(DIALOG_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["l"])
        lay.setSpacing(0)

        self.title = QLabel()
        self.title.setObjectName("section")
        lay.addWidget(self.title)
        lay.addSpacing(SPACE["s"])
        self.current = QLabel()
        self.current.setObjectName("muted")
        self.current.setWordWrap(True)
        lay.addWidget(self.current)
        lay.addSpacing(SPACE["l"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["m"])
        news = QLabel("Neler yeni?")
        news.setStyleSheet(font_css(F.DISPLAY_SEMIBOLD, F.BUTTON, C.TEXT))
        head.addWidget(news)
        head.addStretch(1)
        # dış bağlantı görünümü (translate_page.link_button gibi); adres o an gösterilen sürümden
        self.page_link = with_icon(QPushButton("Sürüm sayfası"), "external", C.CYAN)
        self.page_link.setObjectName("ghost")
        self.page_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.page_link.setStyleSheet(f"QPushButton {{ color: {C.CYAN}; padding-left: {SPACE['xs']}px; "
                                     f"padding-right: {SPACE['s']}px; }}")
        self.page_link.setToolTip("Sürüm sayfasını tarayıcıda açar")
        self.page_link.clicked.connect(self._open_page)
        head.addWidget(self.page_link)
        lay.addLayout(head)
        lay.addSpacing(SPACE["s"])
        self.notes = ReleaseNotes()
        self.notes.setFixedHeight(220)
        lay.addWidget(self.notes)

        lay.addSpacing(SPACE["l"])
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Güncelleme indirmesi")
        self.progress_text = QLabel()
        self.progress_text.setObjectName("muted")
        self.progress_box = QWidget()
        pb = QVBoxLayout(self.progress_box)
        pb.setContentsMargins(0, 0, 0, SPACE["m"])
        pb.setSpacing(SPACE["xs"])
        pb.addWidget(self.progress)
        pb.addWidget(self.progress_text)
        lay.addWidget(self.progress_box)
        self.error = AlertLabel()
        self.error.setContentsMargins(ICON["s"] + SPACE["s"], 0, 0, SPACE["s"])
        lay.addWidget(self.error)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        lay.addSpacing(SPACE["xl"])
        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        self.skip_btn = QPushButton("Bu sürümü atla")
        self.skip_btn.setObjectName("ghost")
        self.skip_btn.setToolTip("Bu sürüm bir daha bildirilmez; daha yeni bir sürüm çıkınca yine bildirilir")
        self.skip_btn.clicked.connect(self._skip)
        self.later_btn = QPushButton("Sonra")
        self.later_btn.clicked.connect(self._later)
        self.main_btn = QPushButton("Güncelle")
        self.main_btn.setObjectName("primary")
        self.main_btn.setDefault(True)
        self.main_btn.clicked.connect(self._main)
        btns.addWidget(self.skip_btn)
        btns.addStretch(1)
        btns.addWidget(self.later_btn)
        btns.addWidget(self.main_btn)
        lay.addLayout(btns)

        upd.changed.connect(self._sync)
        self._sync()

    # --- görünüm ---------------------------------------------------------------------------------------------
    def _sync(self) -> None:
        upd = self.upd
        rel = upd.shown
        if rel is None:  # sürüm kalktı (yeni denetimde artık yeni değil): pencere kapanır
            if upd.phase != "installing":
                self.reject()
            return
        self.title.setText(f"{APP_NAME} {rel.version} yayınlandı")
        self.current.setText(f"Kullandığınız sürüm {upd.version}. Ayarlarınız ve indirdiğiniz modeller korunur.")
        if self._notes_for != rel.version:
            self._notes_for = rel.version
            self.notes.set_notes(rel.notes)
        phase = upd.phase
        auto = bool(upd.can_self_update and rel.installable)
        downloading = phase == "downloading"
        self.progress_box.setVisible(downloading or phase in ("ready", "installing"))
        self.progress.setValue(int(upd.fraction * 1000) if downloading else 1000)
        if downloading:
            self.progress_text.setText(f"İndiriliyor: {fmt_pair(upd.done, upd.total)}")
        elif phase == "ready":
            self.progress_text.setText("İndirildi ve doğrulandı")
        elif phase == "installing":
            self.progress_text.setText("Kurulum başlıyor; Robz Vision kapanıyor…")
        failure = upd.failure if phase == "failed" else None
        self.error.setText(failure.short if failure is not None else "")
        self.error.setToolTip(failure.detail if failure is not None else "")
        self.error.setVisible(failure is not None)
        if auto:
            self.note.setText(AUTO_NOTE)
        elif upd.can_self_update:  # sürümde kurulum dosyası yok
            self.note.setText("Bu sürümün kurulum dosyası bulunamadı. Sürüm sayfasından indirebilirsiniz.")
        else:
            self.note.setText(MANUAL_NOTES.get(upd.kind, MANUAL_NOTES["portable"]))
        self.note.setVisible(phase == "idle" or (phase == "failed" and not auto))  # hatada hata satırı yeter

        self.skip_btn.setVisible(phase in ("idle", "failed"))
        self.later_btn.setText("İptal" if downloading else "Sonra")
        self.later_btn.setEnabled(phase != "installing")
        if not auto:
            self.main_btn.setText("İndir")
            self.main_btn.setToolTip("Sürüm sayfasını tarayıcıda açar")
        elif phase == "failed":
            self.main_btn.setText("Tekrar dene")
        elif phase == "ready":
            self.main_btn.setText("Kur ve yeniden başlat")
        else:
            self.main_btn.setText("Güncelle")
        self.main_btn.setEnabled(phase in ("idle", "failed", "ready"))
        if phase == "ready" and self._requested:
            self._requested = False
            self._install()

    # --- eylemler --------------------------------------------------------------------------------------------
    def _open_page(self) -> None:
        rel = self.upd.shown
        _open_web(rel.page_url if rel is not None else self.upd.source.releases_url)

    def _main(self) -> None:
        upd = self.upd
        rel = upd.shown
        if rel is None:
            return
        if not (upd.can_self_update and rel.installable):
            self._open_page()
            self.accept()
            return
        if upd.phase == "ready":
            self._install()
            return
        self._requested = upd.download()
        self._sync()

    def _install(self) -> None:
        """Kurulum dosyası hazır: çeviri çalışıyorsa onay; sonra ana pencere kurulumu başlatır ve uygulamayı kapatır."""
        if self._running() and not confirm_restart(self):
            return
        self._on_install()

    def _skip(self) -> None:
        self.upd.skip()
        self.accept()

    def _later(self) -> None:
        if self.upd.phase == "downloading":
            self._requested = False
            self.upd.cancel()
            return
        self.reject()

    def reject(self) -> None:
        """Esc ya da pencereyi kapatma: indirme sürüyorsa iptal edilir."""
        if self.upd.phase == "downloading":
            self._requested = False
            self.upd.cancel()
        super().reject()

    def done(self, r: int) -> None:
        try:
            self.upd.changed.disconnect(self._sync)
        except (RuntimeError, TypeError):
            pass
        super().done(r)
