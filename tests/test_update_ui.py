"""Güncellemenin arayüzü: kenar çubuğundaki satır, "yeni sürüm" bildirimi (çeviri çalışırken ve pencere gizliyken
beklenir, tepsi balonu yok), güncelleme penceresi (Güncelle → indir → doğrula → onay → kur; taşınabilir sürümde İndir;
Bu sürümü atla; sürüm notları güvenli), güncellemeden sonraki ilk açılışın bildirimi ve Ayarlar → Uygulama →
Güncellemeler satırı.

Servis gerçektir (altyazitr.update_service.UpdateService); ağ yalnız 127.0.0.1'deki sahte GitHub'a (tests/fake_github.py),
kurulum dosyası hiç çalıştırılmaz (başlatıcı sahte), uygulama kapanmaz (quit_app sahte), tarayıcı açılmaz
(QDesktopServices.openUrl sahte). Qt 'offscreen'; pencereler WA_DontShowOnScreen ile masaüstünde açılmaz."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from altyazitr import config, updater
from altyazitr.config import Settings
from altyazitr.updater import CheckResult, parse_release
from tests.fake_github import FakeGitHub

SETUP = os.urandom(200_000)
TOAST = "Robz Vision 1.3.1 yayınlandı: güncellemek için tıklayın"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and (not isinstance(app, QApplication) or app.platformName() != "offscreen"):
        app.shutdown()
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # QApplication'dan hemen önce
        app = QApplication([])
    assert app.platformName() == "offscreen"
    return app


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(updater, "UPDATE_DIR", tmp_path / "guncelleme")
    monkeypatch.setattr(updater, "SETUP_LOG", tmp_path / "gunluk" / "kurulum.log")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "ayarlar" / "ayarlar.json")
    from altyazitr import winutil

    monkeypatch.setattr(winutil, "release_single_instance", lambda: None)
    monkeypatch.setattr(winutil, "reacquire_single_instance", lambda: None)


@pytest.fixture
def gh():
    server = FakeGitHub().start()
    yield server
    server.stop()


@pytest.fixture
def opened(monkeypatch):
    from PySide6.QtGui import QDesktopServices

    urls: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url.toString()) or True)
    return urls


_WIDGETS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if not _WIDGETS:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    for w in _WIDGETS:
        tray = getattr(w, "tray", None)
        if tray is not None:
            tray.hide()
        dlg = getattr(w, "_update_dlg", None)
        if dlg is not None:
            dlg.hide()
        w.hide()
        w.deleteLater()
    _WIDGETS.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _pump(app, sec: float = 0.05) -> None:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return cond()


def _service(gh, tmp_path, kind: str = "user", settings: Settings | None = None, version: str = "1.3.0"):
    from altyazitr.update_service import UpdateService

    launched: list = []
    svc = UpdateService(settings or Settings(), source=gh.source, kind=kind, version=version,
                        download_dir=tmp_path / "guncelleme", launcher=lambda path, pid: launched.append((path, pid)))
    svc.launched_calls = launched  # type: ignore[attr-defined]
    return svc


def _announce(svc, gh, manual: bool = False, version: str = "1.3.1", **kw) -> None:
    """Servise "denetim bitti, yeni sürüm var" sonucunu verir (ağ yok: sonuç doğrudan)."""
    rel = parse_release(gh.publish(version, SETUP, **kw), gh.source)
    svc._manual = manual
    svc._on_checked(CheckResult("ok", rel))


def _window(qapp, svc, running: bool = False):
    """Ana pencere; sayfalar boş yer tutucular, bağlam sahte (bkz. test_developer_link.py), güncelleme servisi gerçek."""
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QWidget

    import altyazitr.gui.main_window as mw

    class Blank(QWidget):
        def __init__(self, _win):
            super().__init__()

    class BlankTranslate(Blank):
        advice_ready = Signal(object)
        ai_progress = Signal()
        ai_fraction = None

    class FakeAi(QObject):
        changed = Signal()
        finished = Signal(bool, str)
        state = "idle"

    class FakePipeline(QObject):
        error = Signal(str)
        notice = Signal(str)
        running_changed = Signal(bool)
        preview_enabled = True

        def __init__(self):
            super().__init__()
            self.running = running

    class FakeCtx(QObject):
        obs_clients = Signal(int)
        needs_region = Signal()
        notified = Signal(str, str, str)

        def __init__(self):
            super().__init__()
            self.s = svc.s
            self.s.first_run, self.s.tour_seen = False, True
            self.ai, self.pipeline, self.updater = FakeAi(), FakePipeline(), svc
            self.display = SimpleNamespace(toggle_visible=lambda: None, push_style=lambda: None)
            self.server_error = None
            self.notes: list[tuple] = []
            self.notified.connect(lambda *a: self.notes.append(a))

        def notify(self, text: str, level: str = "info") -> None:
            head, _, rest = text.partition("\n")
            self.notified.emit(head, rest.removeprefix("Ayrıntı:").strip(), level)

        def save_later(self):
            pass

        def toggle_running(self):
            pass

    pages = [("home", "Ana sayfa", Blank), ("style", "Görünüm", Blank), ("translate", "Çeviri", BlankTranslate),
             ("settings", "Ayarlar", Blank)]
    old = mw.PAGES
    mw.PAGES = pages
    try:
        win = mw.MainWindow(FakeCtx())
    finally:
        mw.PAGES = old
    _WIDGETS.append(win)
    win.tray.hide()
    balloons: list = []
    win.tray.showMessage = lambda *a, **k: balloons.append(a)  # tepsi balonu yayına düşer: hiç çıkmamalı
    win.balloons = balloons
    quits: list = []
    win.quit_app = lambda: quits.append(1)  # uygulama kapanmasın
    win.quits = quits
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1100, 760)
    win.show()
    assert _wait(qapp, lambda: win._startup_done)  # açılıştaki tur kararı (700 ms)
    return win


def _toasts(win) -> list[str]:
    return [t.text for t in win._toasts._toasts if not t._closing]


# --- kenar çubuğu ve bildirim ------------------------------------------------------------------------------------
def test_sidebar_row_appears_only_with_a_new_version(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    assert not win._update_box.isVisibleTo(win)
    _announce(svc, gh)
    _pump(qapp)
    row = win._update_row
    assert win._update_box.isVisibleTo(win) and row.tx.text() == "Sürümü güncelle"
    assert "1.3.1" in row.toolTip() and row.accessibleDescription() == "Güncelleme penceresini açar"
    assert _toasts(win) == [TOAST] and win.balloons == []
    win._apply_compact(True)  # dar kenar çubuğu: yalnız ikon, ikon diğer durum ikonlarıyla aynı eksende
    assert not row.tx.isVisibleTo(win)
    side = win.side
    assert row.ic.mapTo(side, row.ic.rect().center()).x() == win._obs.ic.mapTo(side, win._obs.ic.rect().center()).x()


def test_notice_waits_while_translating_and_never_uses_the_tray(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc, running=True)
    _announce(svc, gh)
    _pump(qapp)
    assert _toasts(win) == [] and win.balloons == []  # yayın sırasında dikkat dağıtmaz
    assert win._update_box.isVisibleTo(win)  # yalnız kenar çubuğunda işaret
    win.ctx.pipeline.running = False
    win.ctx.pipeline.running_changed.emit(False)
    _pump(qapp)
    assert _toasts(win) == [TOAST] and win.balloons == []
    win.ctx.pipeline.running_changed.emit(False)  # bir kez
    assert _toasts(win) == [TOAST]


def test_notice_waits_while_window_is_hidden(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    win.hide()
    _announce(svc, gh)
    _pump(qapp)
    assert _toasts(win) == [] and win.balloons == []
    win.show()
    _pump(qapp)
    assert _toasts(win) == [TOAST]


def test_clicking_the_notice_opens_the_dialog(qapp, gh, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    _announce(svc, gh)
    _pump(qapp)
    toast = [t for t in win._toasts._toasts if t.text == TOAST][0]
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    _pump(qapp)
    dlg = win._update_dlg
    assert dlg is not None and dlg.isVisible() and dlg.title.text() == "Robz Vision 1.3.1 yayınlandı"
    assert "Kullandığınız sürüm 1.3.0" in dlg.current.text() and "korunur" in dlg.current.text()
    assert dlg.main_btn.text() == "Güncelle" and dlg.main_btn.objectName() == "primary"


def test_manual_check_opens_dialog_without_notice(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    _announce(svc, gh, manual=True)
    _pump(qapp)
    assert win._update_dlg is not None and win._update_dlg.isVisible() and _toasts(win) == []


def test_skip_hides_everything(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    _announce(svc, gh)
    win.open_update_dialog()
    dlg = win._update_dlg
    dlg.skip_btn.click()
    _pump(qapp)
    assert svc.s.update.skipped_version == "1.3.1" and not win._update_box.isVisibleTo(win)
    assert win._update_dlg is None or not win._update_dlg.isVisible()


def test_post_update_note_once(qapp, gh, tmp_path):
    s = Settings()
    s.update.updating_from = "1.2.1"
    svc = _service(gh, tmp_path, settings=s, version="1.3.0")
    win = _window(qapp, svc)
    assert ("Robz Vision 1.3.0 sürümüne güncellendi", "", "info") in win.ctx.notes
    assert s.update.updating_from == "" and svc.post_note is None


# --- güncelleme penceresi ------------------------------------------------------------------------------------------
def test_update_downloads_verifies_and_installs(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    rel = parse_release(gh.publish("1.3.1", SETUP), gh.source)
    svc.release = rel
    win.open_update_dialog()
    dlg = win._update_dlg
    dlg.main_btn.click()
    assert svc.phase == "downloading" and dlg.later_btn.text() == "İptal" and not dlg.main_btn.isEnabled()
    assert _wait(qapp, lambda: win.quits)  # indi, doğrulandı, kurulum başladı, uygulama kapanıyor
    assert svc.launched_calls == [(svc.ready_path, os.getpid())] and svc.phase == "installing"
    assert svc.ready_path.read_bytes() == SETUP
    assert win.balloons == []


def test_running_translation_asks_before_restart(qapp, gh, tmp_path, monkeypatch):
    from altyazitr.gui import update_dialog as ud

    answers = [False, True]
    asked: list = []
    monkeypatch.setattr(ud, "confirm_restart", lambda parent: asked.append(1) or answers.pop(0))
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc, running=True)
    svc.release = parse_release(gh.publish("1.3.1", SETUP), gh.source)
    win.open_update_dialog()
    dlg = win._update_dlg
    dlg.main_btn.click()
    assert _wait(qapp, lambda: svc.phase == "ready" and asked)
    _pump(qapp)
    assert win.quits == [] and svc.launched_calls == []  # Vazgeç: uygulama açık, dosya hazır
    assert dlg.main_btn.text() == "Kur ve yeniden başlat"
    dlg.main_btn.click()
    assert asked == [1, 1] and win.quits == [1] and len(svc.launched_calls) == 1


def test_cancel_and_close_during_download(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    svc.release = parse_release(gh.publish("1.3.1", SETUP * 20), gh.source)
    win.open_update_dialog()
    dlg = win._update_dlg
    svc.download()
    dlg.reject()  # pencereyi kapatmak indirmeyi durdurur
    assert _wait(qapp, lambda: svc.phase != "downloading")
    assert svc.phase in ("idle", "ready") and svc.failure is None and win.quits == []


@pytest.mark.parametrize("kind", ["portable", "all_users", "source"])
def test_manual_kinds_open_release_page(qapp, gh, tmp_path, opened, kind):
    from altyazitr.gui.update_dialog import MANUAL_NOTES

    svc = _service(gh, tmp_path, kind=kind)
    win = _window(qapp, svc)
    _announce(svc, gh)
    win.open_update_dialog()
    dlg = win._update_dlg
    assert dlg.main_btn.text() == "İndir" and dlg.note.text() == MANUAL_NOTES[kind]
    before = len(gh.requests)
    dlg.main_btn.click()
    _pump(qapp)
    assert opened == [f"{gh.base}/releases/tag/v1.3.1"]
    assert svc.phase == "idle" and svc.launched_calls == [] and len(gh.requests) == before  # hiçbir şey inmez
    assert win.quits == []


def test_failed_download_shows_error_and_retry(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    svc.release = parse_release(gh.publish("1.3.1", SETUP, size=len(SETUP) + 1), gh.source)
    win.open_update_dialog()
    dlg = win._update_dlg
    dlg.main_btn.click()
    assert _wait(qapp, lambda: svc.phase == "failed")
    _pump(qapp)
    assert dlg.error.isVisibleTo(dlg) and "doğrulanamadı" in dlg.error.text()
    assert dlg.main_btn.text() == "Tekrar dene" and win._update_row.tx.text() == "Güncellenemedi"
    assert svc.launched_calls == [] and win.quits == []


def test_release_notes_are_safe(qapp, opened):
    from PySide6.QtCore import QUrl

    from altyazitr.gui.update_dialog import ReleaseNotes, _open_web

    notes = ReleaseNotes()
    _WIDGETS.append(notes)
    notes.set_notes("- **Yeni** <b>kalın</b> <script>alert(1)</script> ![r](file:///C:/Windows/win.ini) "
                    "[bağlantı](https://github.com/kutsalkiremitci/robz-vision)")
    text = notes.toPlainText()
    assert "<b>kalın</b>" in text and "<script>" in text  # HTML işlenmez, düz yazı kalır
    assert notes.openLinks() is False and notes.openExternalLinks() is False
    assert notes.loadResource(0, QUrl("file:///C:/Windows/win.ini")) is None
    _open_web(QUrl("file:///C:/Windows/System32/calc.exe"))
    _open_web(QUrl("javascript:alert(1)"))
    _open_web(QUrl("https://github.com/kutsalkiremitci/robz-vision"))
    assert opened == ["https://github.com/kutsalkiremitci/robz-vision"]
    notes.set_notes("")
    assert notes.toPlainText() == "Bu sürüm için not yazılmamış."


def test_long_release_notes_open_at_top(qapp):
    """Pencere notları gösterilmeden önce doldurur: uzun notlar ilk gösterimde sona kaydırılmış açılmamalı."""
    from PySide6.QtCore import Qt

    from altyazitr.gui.update_dialog import ReleaseNotes

    notes = ReleaseNotes()
    _WIDGETS.append(notes)
    notes.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    notes.resize(470, 200)
    notes.set_notes("\n".join(f"- Madde {i}: uzun bir sürüm notu satırı" for i in range(60)))
    notes.show()
    qapp.processEvents()
    bar = notes.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == 0
    assert notes.textCursor().position() == 0
    notes.set_notes("- Yeni sürüm\n" * 60)  # ikinci sürümün notları da baştan
    qapp.processEvents()
    assert bar.value() == 0


# --- Ayarlar → Uygulama → Güncellemeler ------------------------------------------------------------------------------
def _settings_page(qapp, svc=None, win=None):
    from PySide6.QtCore import Qt

    from altyazitr.gui.settings_page import SettingsPage

    s = svc.s if svc is not None else Settings()
    ctx = SimpleNamespace(s=s, save_later=lambda: None, request_restart=lambda: None,
                          display=SimpleNamespace(push_style=lambda: None), hotkey_errors=[], server_error="",
                          register_hotkeys=lambda: [], server=SimpleNamespace(url="http://127.0.0.1:1"))
    if svc is not None:
        ctx.updater = svc
    page = SettingsPage(win or SimpleNamespace(ctx=ctx))
    _WIDGETS.append(page)
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.resize(900, 700)
    page.show()
    _pump(qapp)
    return page


def test_settings_row(qapp, gh, tmp_path):
    svc = _service(gh, tmp_path)
    svc.start(first_delay_ms=60_000)
    page = _settings_page(qapp, svc)
    assert page.update_check.text() == "Yeni sürümleri denetle" and page.update_check.isChecked()
    assert "GitHub" in page.update_check.toolTip() and "kişisel bilgi" in page.update_check.toolTip()
    assert page.update_line.text.text() == "Henüz denetlenmedi" and not page.update_open.isVisibleTo(page)
    page.update_check.click()  # kapat: zamanlayıcı durur
    assert svc.s.update.auto_check is False and not svc._timer.isActive()
    page.update_check.click()
    assert svc.s.update.auto_check is True and svc._timer.isActive()
    gh.publish("1.3.0", SETUP)  # depodaki son sürüm kullanılanla aynı
    page.update_now.click()
    assert _wait(qapp, lambda: not svc.checking)
    _pump(qapp)
    assert page.update_line.text.text().startswith("Robz Vision güncel. Son denetim ")
    assert page.update_now.isEnabled()
    _announce(svc, gh)
    _pump(qapp)
    assert page.update_line.text.text() == "Sürüm 1.3.1 yayınlandı" and page.update_open.isVisibleTo(page)
    svc.skip()
    assert page.update_line.text.text() == "Sürüm 1.3.1 atlandı" and not page.update_open.isVisibleTo(page)
    gh.api_status = 500
    svc.check_now(manual=False)
    assert _wait(qapp, lambda: not svc.checking)
    svc.s.update.skipped_version, svc.release = "", None
    svc.changed.emit()
    assert page.update_line.text.text().startswith("Denetlenemedi")
    svc.shutdown()


def test_settings_row_without_service(qapp):
    page = _settings_page(qapp)
    assert page.update_check.isChecked() and not page.update_now.isEnabled()
    assert page.update_line.text.text() == "" and not page.update_open.isVisibleTo(page)


def test_when_text():
    from altyazitr.gui.update_dialog import when_text

    now = time.mktime((2026, 11, 19, 15, 0, 0, 0, 0, -1))
    assert when_text(now - 3600, now) == "14:00"
    assert when_text(now - 2 * 86400, now) == "17.11.2026 15:00"


# --- kenar çubuğu → Güncellemeleri denetle ----------------------------------------------------------------------------
def test_check_row_reports_current_version(qapp, gh, tmp_path):
    """Güncelleme satırı yokken sürümün altında "Güncellemeleri denetle" durur; tıklanınca denetim sürerken
    "Denetleniyor…" yazar ve tıklanmaz, güncelse kısa "güncel" bildirimi çıkar."""
    import altyazitr.gui.main_window as mw

    gh.publish("1.3.0", SETUP)  # depodaki son sürüm kullanılanla aynı
    gh.api_delay = 0.4
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    row = win._check_row
    assert win._check_box.isVisibleTo(win) and row.tx.text() == mw.CHECK_TEXT and row.isEnabled()
    assert row.toolTip() == mw.CHECK_TIP and not win._update_box.isVisibleTo(win)
    side = win.side  # sürümün hemen altında, ikonu durum ikonlarıyla aynı eksende
    assert row.mapTo(side, row.rect().topLeft()).y() > win._ver.mapTo(side, win._ver.rect().bottomLeft()).y()
    assert row.ic.mapTo(side, row.ic.rect().center()).x() == win._obs.ic.mapTo(side, win._obs.ic.rect().center()).x()

    row.click()
    assert _wait(qapp, lambda: row.tx.text() == mw.CHECKING_TEXT and not row.isEnabled())
    assert _wait(qapp, lambda: not svc.checking)
    _pump(qapp)
    assert row.tx.text() == mw.CHECK_TEXT and row.isEnabled() and svc.status == "current"
    assert _toasts(win) == [mw.CHECK_CURRENT.format(v="1.3.0")] and win._update_dlg is None

    win._apply_compact(True)  # dar kenar çubuğu: yalnız ikon, yazı ipucunda
    assert not row.tx.isVisibleTo(win) and row.toolTip().startswith(mw.CHECK_TEXT)


def test_check_row_opens_dialog_for_new_version(qapp, gh, tmp_path):
    """Yeni sürüm varsa güncelleme penceresi açılır; denetle satırı gizlenir, yerini "Sürümü güncelle" alır. Atlanmış
    sürüm de elle denetimde yine gösterilir."""
    gh.publish("1.3.1", SETUP)
    settings = Settings()
    settings.update.skipped_version = "1.3.1"
    svc = _service(gh, tmp_path, settings=settings)
    win = _window(qapp, svc)
    win._check_row.click()
    assert _wait(qapp, lambda: win._update_dlg is not None)
    assert not win._check_box.isVisibleTo(win) and win._update_box.isVisibleTo(win)
    assert win._update_row.tx.text() == "Sürümü güncelle" and settings.update.skipped_version == ""
    assert not [t for t in _toasts(win) if "güncel (" in t]  # "güncel" bildirimi çıkmaz


@pytest.mark.parametrize("status, text", [(500, "CHECK_FAILED"), (429, "CHECK_LIMITED")])
def test_check_row_reports_failure(qapp, gh, tmp_path, status, text):
    import altyazitr.gui.main_window as mw

    gh.publish("1.3.1", SETUP)
    gh.api_status = status
    gh.api_headers = {"Retry-After": "60"} if status == 429 else {}
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    win._check_row.click()
    assert _wait(qapp, lambda: not svc.checking and _toasts(win))
    assert _toasts(win) == [getattr(mw, text)] and win._check_box.isVisibleTo(win) and win._update_dlg is None


def test_automatic_check_reports_nothing(qapp, gh, tmp_path):
    """Kendiliğinden denetim güncelse bildirim çıkarmaz (yalnız elle denetim sonucu bildirir)."""
    gh.publish("1.3.0", SETUP)
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    svc.check_now(manual=False)
    assert _wait(qapp, lambda: not svc.checking)
    _pump(qapp)
    assert svc.status == "current" and _toasts(win) == []


def test_default_window_size():
    from PySide6.QtCore import QRect

    from altyazitr.gui.main_window import WINDOW_DEFAULT, default_window_size

    assert WINDOW_DEFAULT == (1280, 800)
    assert default_window_size(QRect(0, 0, 1920, 1032)) == (1280, 800)  # 1080p (görev çubuğu hariç)
    assert default_window_size(QRect(0, 0, 2560, 1400)) == (1280, 800)
    assert default_window_size(QRect(0, 0, 1366, 728)) == (1256, 760)  # küçük ekran: %92, en az 980×760
    assert default_window_size(None) == (980, 760)


def test_tam_ekran_gecisi_ve_pencere_dugmeleri(qapp, gh, tmp_path):
    """Varsayılan açılış pencerede; F11 / sağ üstteki düğme tam ekran ile pencere arasında geçer, seçim ayara
    yazılır; pencere düğmeleri yalnız tam ekranda görünür."""
    assert Settings().open_fullscreen is False
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    ctl = win._win_controls
    assert not win.isFullScreen() and not ctl.isVisibleTo(win)
    win.toggle_fullscreen()
    _pump(qapp)
    assert win.isFullScreen() and win.ctx.s.open_fullscreen is True and ctl.isVisibleTo(win)
    assert set(ctl.buttons) == {"window", "minimize", "close"}
    ctl.buttons["window"].click()  # pencerede göster
    _pump(qapp)
    assert not win.isFullScreen() and win.ctx.s.open_fullscreen is False and not ctl.isVisibleTo(win)


def test_kurulum_sihirbazi(qapp, gh, tmp_path):
    """Başla → adım: kurulum sihirbazı adımın sayfasını açar, balonun üstünde "Adım 1 / 4 · Oyunu göster" yazar.
    Adımın son parçasından ileri gidilince adım onaylanır (ayara yazılır) ve sıradakine geçilir; Geri önceki parçaya
    döner; Daha sonra kapatır (onaylananlar kalır); Bitir Başla'da biter. Tanıtım turu ilk açılışta kendiliğinden
    açılmaz: yeni kullanıcıyı Başla karşılar."""
    from altyazitr.gui import tour
    from altyazitr.gui.main_window import PAGES

    keys = [k for k, _t, _c in PAGES]
    svc = _service(gh, tmp_path)
    win = _window(qapp, svc)
    win.ctx.s.guide_done = []
    win.ctx.s.tour_seen = False
    win._maybe_tour()
    assert tour.active_tour(win) is None

    win.start_guide("source")
    _pump(qapp)
    wiz = tour.active_tour(win)
    assert wiz is not None and keys[win.nav.currentRow()] == "home"
    b = wiz.bubble
    assert b.kicker.text() == "Adım 1 / 4 · Oyunu göster" and b.title.text() == "Oyun nerede görünüyor?"
    assert b.count.text() == "1/2" and b.next_btn.text() == "İleri" and not b.back_btn.isVisibleTo(b)
    b.next_btn.click()
    _pump(qapp)
    assert b.count.text() == "2/2" and b.next_btn.text() == "Sonraki adım" and win.ctx.s.guide_done == []
    b.next_btn.click()
    _pump(qapp)
    assert win.ctx.s.guide_done == ["source"] and b.kicker.text().startswith("Adım 2 / 4")
    b.back_btn.click()
    _pump(qapp)
    assert b.kicker.text().startswith("Adım 1 / 4") and b.count.text() == "2/2"
    b.later_btn.click()  # Daha sonra: kapanır, onaylanan adım kalır
    _pump(qapp)
    assert tour.active_tour(win) is None and win.ctx.s.guide_done == ["source"]

    win.start_guide("translate")
    _pump(qapp)
    wiz = tour.active_tour(win)
    b = wiz.bubble
    assert keys[win.nav.currentRow()] == "translate" and b.kicker.text() == "Adım 3 / 4 · Çeviriyi seç"
    for _ in range(4):  # çeviri (1 parça) → görünüm (3 parça) → hazırsınız
        b.next_btn.click()
        _pump(qapp)
    assert b.title.text() == "Hazırsınız" and b.next_btn.text() == "Bitir" and keys[win.nav.currentRow()] == "start"
    assert win.ctx.s.guide_done == ["source", "translate", "style"]
    b.next_btn.click()
    _pump(qapp)
    assert tour.active_tour(win) is None and keys[win.nav.currentRow()] == "start"
    assert win.ctx.s.tour_seen is False  # sihirbazı bitirmek tanıtım turunu görülmüş saymaz
