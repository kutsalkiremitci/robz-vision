"""Geliştiricinin Kick bağlantısı (altyazitr.gui.developer): kenar çubuğunun altındaki satır ve Ayarlar → Uygulama →
Geliştirici doğru adresi açar (QDesktopServices.openUrl sahtelenir, tarayıcı açılmaz), klavyeyle de çalışır ve
bildirim göstermez; Kick logosu yüklenir ve pip ile PyInstaller paketlerine girer; yayın çıktısına (OBS sayfası,
sunucu, ekrandaki altyazı kutusu) bağlantı ya da logo girmez.

Ekran gerekmez: Qt 'offscreen', pencereler WA_DontShowOnScreen ile masaüstünde açılmaz. Ağ yok (yalnız yerel, rastgele
portta OBS sunucusu)."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from altyazitr.config import Settings
from altyazitr.gui import developer
from altyazitr.paths import resource_dir

ROOT = Path(__file__).resolve().parents[1]
KICK = "https://kick.com/robzsogood"
YOUTUBE = "https://www.youtube.com/@robzsogood"
FOLLOW_TIP = "Robz Vision'ı geliştiren Robz so good'u Kick'te takip et"


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


@pytest.fixture
def opened(monkeypatch):
    """QDesktopServices.openUrl yerine: açılması istenen adresleri toplar (tarayıcı açılmaz)."""
    from PySide6.QtGui import QDesktopServices

    urls: list[str] = []

    def fake(url) -> bool:
        urls.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", fake)
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


def _window(qapp, width: int = 1100, height: int = 760):
    """Ana pencere; sayfalar boş yer tutucular, bağlam sahte (bkz. test_main_window_ai.py). Tur görülmüş."""
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
        running = False
        preview_enabled = True

    class FakeCtx(QObject):
        obs_clients = Signal(int)
        needs_region = Signal()
        notified = Signal(str, str, str)

        def __init__(self):
            super().__init__()
            self.s = Settings()
            self.s.first_run, self.s.tour_seen = False, True
            self.ai, self.pipeline = FakeAi(), FakePipeline()
            self.display = SimpleNamespace(toggle_visible=lambda: None, push_style=lambda: None)
            self.server_error = None
            self.notes: list[tuple] = []
            self.notified.connect(lambda *a: self.notes.append(a))

        def notify(self, text: str, level: str = "info") -> None:
            self.notified.emit(text, "", level)

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
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(width, height)
    win.show()
    _pump(qapp)
    return win


def _center_x(w, ref) -> int:
    return w.mapTo(ref, w.rect().center()).x()


# --- kenar çubuğu ------------------------------------------------------------------------------------------------
def test_sidebar_link_sits_at_the_bottom_and_is_quiet(qapp):
    """En altta, sürümün altında: Kick logosu + "Robz so good"; ipucu ve ekran okuyucu adı; logosu durum ikonlarıyla
    aynı eksende. Klavyeyle odaklanır (Tab)."""
    from PySide6.QtCore import Qt

    win = _window(qapp)
    row, side = win._kick_row, win.side
    assert row.isVisibleTo(win) and row.tx.isVisibleTo(win)
    assert row.tx.text() == "Robz so good" and not row.ic.pixmap().isNull()
    assert row.toolTip() == FOLLOW_TIP
    assert row.accessibleName() == "Robz so good'u Kick'te takip et"
    assert "kick.com/robzsogood" in row.accessibleDescription()
    assert row.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert row.cursor().shape() == Qt.CursorShape.PointingHandCursor
    top = row.mapTo(side, row.rect().topLeft()).y()
    assert top > win._ver.mapTo(side, win._ver.rect().bottomLeft()).y()  # sürümün altında
    assert top > win.nav.mapTo(side, win.nav.rect().bottomLeft()).y()
    assert side.height() - (top + row.height()) <= 16  # pencerenin alt kenarına yakın
    assert _center_x(row.ic, side) == _center_x(win._obs.ic, side)  # durum ikonlarıyla aynı eksen
    assert _center_x(row.tx, side) > _center_x(row.ic, side)


def test_sidebar_link_compact_keeps_logo_and_tooltip(qapp):
    """Dar pencerede yalnız logo kalır (yazı ipucunda); menü öğeleri sıkışmaz (tur hedefleri bozulmaz)."""
    win = _window(qapp, 820, 560)
    row, side = win._kick_row, win.side
    assert win._compact is True
    assert row.isVisibleTo(win) and not row.tx.isVisibleTo(win)
    assert row.toolTip() == FOLLOW_TIP
    assert _center_x(row.ic, side) == _center_x(win._obs.ic, side)
    nav = win.nav
    for i in range(nav.count()):  # menü öğeleri dikeyde tam sığar
        r = nav.visualItemRect(nav.item(i))
        assert not r.isEmpty() and r.top() >= 0 and r.bottom() < nav.viewport().height(), i


def test_sidebar_link_opens_kick_by_mouse_and_keyboard(qapp, opened):
    """Tıklama, Enter ve Boşluk kanalı varsayılan tarayıcıda açar; bildirim ya da pencere çıkmaz."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    win = _window(qapp)
    row = win._kick_row
    before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert opened == [KICK]
    QTest.keyClick(row, Qt.Key.Key_Return)
    QTest.keyClick(row, Qt.Key.Key_Enter)
    QTest.keyClick(row, Qt.Key.Key_Space)
    _pump(qapp)
    assert opened == [KICK] * 4
    assert win.ctx.notes == [] and not win._toasts._toasts  # bildirim yok
    after = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    assert after <= before  # açılır pencere yok
    assert win.stack.currentWidget() is win.pages["home"]  # sayfa değişmez


def test_link_works_without_logo(qapp, opened, monkeypatch, tmp_path):
    """Logo dosyası okunamazsa satır yine kurulur ve çalışır (yalnız yazı); uygulama çökmez."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import altyazitr.gui.main_window as mw

    monkeypatch.setattr(developer, "kick_logo_path", lambda: tmp_path / "yok.svg")
    monkeypatch.setattr(developer, "_PIXMAPS", {})
    assert developer.kick_pixmap(16).isNull() and developer.kick_icon().isNull()
    row = mw._KickRow()
    _WIDGETS.append(row)
    assert row.tx.text() == "Robz so good"
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert opened == [KICK]


# --- Ayarlar → Uygulama → Geliştirici --------------------------------------------------------------------------
def _settings_page(qapp):
    from PySide6.QtCore import Qt

    from altyazitr.gui.settings_page import SettingsPage

    ctx = SimpleNamespace(s=Settings(), save_later=lambda: None, request_restart=lambda: None,
                          display=SimpleNamespace(push_style=lambda: None), hotkey_errors=[], server_error="",
                          register_hotkeys=lambda: [], server=SimpleNamespace(url="http://127.0.0.1:1"))
    page = SettingsPage(SimpleNamespace(ctx=ctx))
    _WIDGETS.append(page)
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.resize(900, 700)
    page.show()
    _pump(qapp)
    return page


def test_settings_developer_row(qapp, opened):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel

    page = _settings_page(qapp)
    labels = {w.text() for w in page.findChildren(QLabel)}
    assert {"Geliştirici", "Robz so good", "Sürüm"} <= labels
    kick, yt = page.kick_link, page.youtube_link
    assert kick.text() == "Kick'te takip et" and kick.toolTip() == FOLLOW_TIP and not kick.icon().isNull()
    assert kick.accessibleName() == "Robz so good'u Kick'te takip et"
    assert yt.text() == "YouTube" and yt.accessibleName()
    assert kick.focusPolicy() & Qt.FocusPolicy.TabFocus
    QTest.mouseClick(kick, Qt.MouseButton.LeftButton)
    QTest.mouseClick(yt, Qt.MouseButton.LeftButton)
    QTest.keyClick(kick, Qt.Key.Key_Space)
    assert opened == [KICK, YOUTUBE, KICK]


def test_version_tooltip_still_says_not_affiliated(qapp):
    from PySide6.QtWidgets import QLabel

    page = _settings_page(qapp)
    tips = [w.toolTip() for w in page.findChildren(QLabel) if "bağlantılı değildir" in w.toolTip()]
    assert tips and "Kick" in tips[0]


# --- logo ve paket --------------------------------------------------------------------------------------------
def test_kick_logo_file_is_the_unmodified_green_mark():
    """Kick'in yeşil "K" işareti: tek renk (#53FC18), kare görüntü alanı; arayüzde Kick yeşili yalnız bu dosyada."""
    path = developer.kick_logo_path()
    assert path == ROOT / "altyazitr" / "data" / "kick.svg" and path.exists()
    svg = path.read_text(encoding="utf-8")
    assert 'viewBox="0 0 24 24"' in svg and "<title>Kick</title>" in svg
    assert set(re.findall(r"#[0-9A-Fa-f]{6}", svg)) == {developer.KICK_GREEN} == {"#53FC18"}
    assert svg.count("<path") == 1 and "transform" not in svg
    for py in (ROOT / "altyazitr").rglob("*.py"):  # Kick yeşili yalnız logoda (developer.py'deki sabit hariç)
        if py.name != "developer.py":
            assert "53FC18" not in py.read_text(encoding="utf-8").upper(), py


def test_kick_logo_renders(qapp):
    from PySide6.QtGui import QColor

    pm = developer.kick_pixmap(16, 2.0)
    assert not pm.isNull() and pm.width() == pm.height() == 32 and pm.devicePixelRatio() == 2.0
    img = pm.toImage()
    green = QColor(developer.KICK_GREEN)
    for x, y in ((8, 16), (16, 16), (8, 4)):  # sol gövde ve orta: dolu, Kick yeşili
        c = img.pixelColor(x, y)
        assert c.alpha() > 240, (x, y)
        assert abs(c.red() - green.red()) < 8 and abs(c.green() - green.green()) < 8, (x, y, c.name())
        assert abs(c.blue() - green.blue()) < 8, (x, y, c.name())
    assert img.pixelColor(0, 31).alpha() == 0  # köşe boş
    assert developer.kick_pixmap(16, 2.0) is pm  # önbellekten
    small = developer.kick_pixmap(16, 1.0, developer.ICON_SCALE).toImage()
    assert small.pixelColor(8, 0).alpha() == 0  # küçültülmüş: üst kenarda boşluk var
    assert not developer.kick_icon().isNull()


def test_kick_logo_is_packaged(monkeypatch, tmp_path):
    """pip paketi ("data/*") ve PyInstaller paketi ("altyazitr/data" klasörü) logoyu içerir; paket içinde de bulunur."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "data/*" in cfg["tool"]["setuptools"]["package-data"]["altyazitr"]
    spec = (ROOT / "RobzVision.spec").read_text(encoding="utf-8")
    assert '("altyazitr/data", "altyazitr/data")' in spec
    assert "QtSvg" not in spec  # logoyu çizen QtSvg paketten çıkarılmamış (PyInstaller içe aktarmadan bulur)
    assert (resource_dir() / "data" / "kick.svg").is_file()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert developer.kick_logo_path() == tmp_path / "altyazitr" / "data" / "kick.svg"


def test_docs_link_the_channel():
    """README (üstte ve Geliştirici bölümünde), kullanım kılavuzu, site, FUNDING ve bildirimler bağlantıyı verir;
    "bağlantılı değildir" uyarısı korunur. Kanal adı her yerde "Robz so good" diye yazılır."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    head = readme.split("## Kimin için?")[0]
    assert KICK in head and 'src="altyazitr/data/kick.svg"' in head
    assert "### Geliştirici" in readme and YOUTUBE in readme
    assert "bağlantılı değildir" in readme and "Kick" in readme.split("bağlantılı değildir")[0][-400:]
    assert KICK in (ROOT / "docs" / "KULLANIM.md").read_text(encoding="utf-8")
    site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert site.count(KICK) >= 2 and YOUTUBE in site
    assert KICK in (ROOT / ".github" / "FUNDING.yml").read_text(encoding="utf-8")
    assert "Kick markası" in (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for path in ("README.md", "docs/KULLANIM.md", "site/index.html", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
                 "altyazitr/gui/developer.py", "altyazitr/gui/main_window.py", "altyazitr/gui/settings_page.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "ROBZSOGOOD" not in text and "RobzSoGood" not in text, path
        assert "Radiant" not in text and "Top 6" not in text, path


# --- yayın çıktısı: bağlantı ya da logo girmez ------------------------------------------------------------------
STREAM_FILES = ("altyazitr/web/overlay.html", "altyazitr/web/sw.js", "altyazitr/server.py", "altyazitr/overlay.py",
                "altyazitr/render.py")
# Not: display.py'deki Test düğmesi örnek satırı (TEST_LINES) kullanıcının bilerek istediği tek istisnadır; gerçek
# altyazı akışı ve OBS sayfasının kendisi buradaki dosyalardan gelir.


@pytest.mark.parametrize("rel", STREAM_FILES)
def test_stream_output_has_no_developer_link(rel):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    for needle in ("kick", "robzsogood", "robz so good", "53fc18", "developer"):
        assert needle not in text, (rel, needle)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_obs_page_served_without_developer_link():
    """OBS'in açtığı sayfa bağlantı içermez; Kick logosu sunucudan verilmez (data/ klasörü sunulmaz)."""
    from altyazitr.server import AsyncBridge, OverlayServer

    bridge = AsyncBridge()
    bridge.start()
    server = OverlayServer(_free_port())
    bridge.submit(server.start()).result(5)

    async def fetch() -> tuple[str, int]:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{server.port}/") as r:
                page = await r.text()
            async with http.get(f"http://127.0.0.1:{server.port}/kick.svg") as r:
                return page, r.status

    try:
        page, status = asyncio.run(fetch())
    finally:
        bridge.submit(server.stop()).result(5)
        bridge.stop()
    low = page.lower()
    assert "<html" in low and "kick" not in low and "robz so good" not in low
    assert status == 404
