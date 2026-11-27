"""Ana sayfanın canlı kaynak önizlemesi (gui/source_view.py) ve önizlemede bölge düzenleme.

Gerçek ekran ya da pencere yakalanmaz: kareler bellekte üretilir, yakalayıcının kaynağı sahte bir işlevdir."""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from altyazitr.gui import source_view as sv
from tests.test_history import qapp  # noqa: F401 - pytest fikstürü (offscreen QApplication)

_KEEP: list = []


# --- saf hesaplar -------------------------------------------------------------------------------------
def test_ekran_pikseli_ve_oran_gidip_gelir():
    mon = (1920, 0, 2560, 1440)  # ikinci ekran
    rect = [1920 + 320, 1100, 1920, 220]
    frac = sv.rect_to_frac(rect, mon)
    assert frac == pytest.approx((0.125, 1100 / 1440, 0.75, 220 / 1440))
    assert sv.frac_to_rect(frac, mon) == rect
    assert sv.rect_to_frac(rect, (0, 0, 1920, 1080)) is None  # ortası başka ekranda
    assert sv.monitor_index_for(rect, [(0, 0, 1920, 1080), mon]) == 1
    assert sv.monitor_index_for([5000, 5000, 10, 10], [(0, 0, 1920, 1080), mon]) == 0


def test_tasima_ve_boyutlandirma_sinirlari():
    r = (0.1, 0.7, 0.8, 0.2)
    assert sv.drag_region("move", r, 0.5, 0.5) == pytest.approx((0.2, 0.8, 0.8, 0.2))  # kenara dayanır
    assert sv.drag_region("e", r, 0.05, 0) == pytest.approx((0.1, 0.7, 0.85, 0.2))
    assert sv.drag_region("nw", r, -0.2, -0.1) == pytest.approx((0.0, 0.6, 0.9, 0.3))
    w = sv.drag_region("w", r, 2.0, 0)  # karşı kenarı geçemez, en küçük boy kalır
    assert w[2] == pytest.approx(sv.MIN_FRAC) and w[0] + w[2] == pytest.approx(0.9)
    assert sv.span_region((0.9, 0.2), (0.3, 1.4)) == pytest.approx((0.3, 0.2, 0.6, 0.8))
    assert sv.clamp_frac((-0.2, 0.95, 0.5, 0.3)) == pytest.approx((0.0, 0.7, 0.5, 0.3))


def test_kare_kucultulur_ve_kopyalanir():
    frame = np.zeros((1080, 1920, 4), np.uint8)
    frame[:, :, 2] = 200  # BGRA: kırmızı
    img = sv.downscale_bgr(frame, 640)
    assert (img.width(), img.height()) == (640, 360)
    assert img.pixelColor(10, 10).red() == 200 and img.pixelColor(10, 10).blue() == 0


# --- görünüm ----------------------------------------------------------------------------------------
@pytest.fixture
def view(qapp):
    v = sv.SourceView()
    _KEEP.append(v)
    v.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    v.resize(960, 540)
    img = QImage(1920, 1080, QImage.Format.Format_RGB888)
    img.fill(0x223344)
    v.set_frame(img, QSize(1920, 1080))
    v.show()
    qapp.processEvents()
    got: list = []
    v.committed.connect(lambda r: got.append(tuple(r)))
    yield v, got
    v.close()


def _pt(v, fx, fy) -> QPoint:
    ir = v._img_rect()
    return QPoint(round(ir.x() + fx * ir.width()), round(ir.y() + fy * ir.height()))


def _drag(v, a: QPoint, b: QPoint) -> None:
    QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for k in (0.33, 0.66, 1.0):
        QTest.mouseMove(v, QPoint(round(a.x() + (b.x() - a.x()) * k), round(a.y() + (b.y() - a.y()) * k)))
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)


def test_onizlemede_bolge_cizilir_tasinir_boyutlandirilir(view):
    v, got = view
    assert v.region is None and v.hit(_pt(v, 0.5, 0.5).toPointF()) == "new"
    _drag(v, _pt(v, 0.1, 0.7), _pt(v, 0.9, 0.92))  # yeni bölge
    assert got[-1] == pytest.approx((0.1, 0.7, 0.8, 0.22), abs=0.003)
    assert v.hit(_pt(v, 0.5, 0.8).toPointF()) == "move"
    assert v.hit(_pt(v, 0.9, 0.92).toPointF()) == "se" and v.hit(_pt(v, 0.1, 0.8).toPointF()) == "w"
    _drag(v, _pt(v, 0.5, 0.8), _pt(v, 0.5, 0.7))  # yukarı taşı
    assert got[-1] == pytest.approx((0.1, 0.6, 0.8, 0.22), abs=0.003)
    _drag(v, _pt(v, 0.9, 0.82), _pt(v, 0.95, 0.82))  # sağ kenarı uzat
    assert got[-1][2] == pytest.approx(0.85, abs=0.003)
    n = len(got)
    QTest.mouseClick(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(v, 0.5, 0.2))
    assert len(got) == n and v.region == pytest.approx(got[-1])  # tıklama bölgeyi silmez


def test_ok_tuslari_tasir_shift_boyutlandirir(view):
    v, got = view
    v.set_region((0.2, 0.7, 0.6, 0.2))
    v.setFocus()
    QTest.keyClick(v, Qt.Key.Key_Right)
    assert got[-1] == pytest.approx((0.2 + sv.KEY_STEP, 0.7, 0.6, 0.2))
    QTest.keyClick(v, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert got[-1] == pytest.approx((0.2 + sv.KEY_STEP, 0.7, 0.6, 0.2 + sv.KEY_STEP))


def test_duzenlenemezken_dokunulmaz(view):
    v, got = view
    v.set_region((0.2, 0.7, 0.6, 0.2))
    v.set_editable(False)
    _drag(v, _pt(v, 0.5, 0.8), _pt(v, 0.5, 0.3))
    assert got == [] and v.region == pytest.approx((0.2, 0.7, 0.6, 0.2))


# --- yakalayıcı -------------------------------------------------------------------------------------
def test_yakalayici_islem_hattinin_karesini_paylasir(qapp, monkeypatch):
    from altyazitr import winutil

    monkeypatch.setattr(winutil, "is_window", lambda h: True)
    monkeypatch.setattr(winutil, "is_minimized", lambda h: False)
    frame = np.full((720, 1280, 3), 90, np.uint8)
    calls: list[int] = []

    def borrow():
        calls.append(1)
        return frame

    g = sv.SourceGrabber()
    _KEEP.append(g)
    got: list = []
    g.frame.connect(lambda img, size: got.append((img.width(), size.width(), size.height())))
    g.set_interval(0.05)
    g.set_window(1234)
    g.set_borrow(borrow)  # ikinci yakalama açılmaz: kendi WindowCapturer'ı hiç kurulmaz
    g.start()
    t0 = time.monotonic()
    while len(got) < 2 and time.monotonic() - t0 < 3:
        qapp.processEvents()
        time.sleep(0.01)
    g.stop()
    assert got and got[0] == (1280, 1280, 720) and calls and g._wincap is None
    assert not g.running


# --- Ana sayfa ----------------------------------------------------------------------------------------
def test_ana_sayfada_onizlemeden_bolge_kaydedilir(qapp, monkeypatch):
    from tests.test_history import _home

    home, ctx, _toasts = _home(qapp, monkeypatch)
    edited: list[int] = []
    home.win.region_edited = lambda: edited.append(1)
    c = ctx.s.capture
    c.mode, c.rect = "screen", []
    home.refresh()
    assert home.source.region is None and home.source._hint  # bölge yok: "fareyle çizin"
    home._region_committed((0.12, 0.74, 0.76, 0.22))
    assert c.rect == [230, 799, 1459, 238] and edited == [1]
    home.refresh()
    assert home.source.region == pytest.approx((230 / 1920, 799 / 1080, 1459 / 1920, 238 / 1080))
    assert not home.source._hint

    c.mode, c.window_exe, c.window_title = "window", "chiaki.exe", "chiaki-ng"
    monkeypatch.setattr("altyazitr.gui.home_page.winutil.find_window", lambda exe, title, **_kw: None)
    home.refresh()
    assert not home.source._editable and "açık değil" in home.source._message
    home._region_committed((0.1, 0.8, 0.8, 0.15))
    assert c.region == [0.1, 0.8, 0.8, 0.15] and edited == [1, 1]
    assert not home.read_box.isVisibleTo(home)  # okunan bölge yalnız çeviri sürerken


def test_ana_sayfa_genis_ve_dar_duzen(qapp, monkeypatch):
    """Geniş: solda tam boy çeviriler (liste doldurur), sağda kaynak ve yan yana Ekranımda / OBS. Dar: tek kayan
    sütun, kaynak en üstte. Geçişte kartlar taşınır, kaybolmaz."""
    from tests.test_history import _home

    home, _ctx, _toasts = _home(qapp, monkeypatch)
    home.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    home.resize(1068, 800)
    home.show()
    qapp.processEvents()
    assert home._wide and home._left.isVisibleTo(home) and not home._sa.isVisibleTo(home)
    assert home.hist_card.parentWidget() is home._left and home.history.view._fill
    assert home.ov_card.parentWidget() is home._docks and home.obs_card.parentWidget() is home._docks
    assert home.source.width() > home._left.width()  # kaynak en büyük öğe

    home.resize(780, 800)
    qapp.processEvents()
    assert not home._wide and home._sa.isVisibleTo(home) and not home._left.isVisibleTo(home)
    order = [home._lay.indexOf(c) for c in (home.source_card, home.ov_card, home.obs_card, home.hist_card)]
    assert order == [0, 1, 2, 3] and not home.history.view._fill
    assert all(c.isVisibleTo(home) for c in (home.source_card, home.ov_card, home.obs_card, home.hist_card))
    home.hide()


def test_pencere_listesi_duzenli_ve_acilirken_yenilenir(qapp, monkeypatch):
    """Pencere modunda yeni açılan pencere listeye kendiliğinden gelir (iki saniyede bir ve liste açılırken); seçim
    korunur, liste değişmediyse yeniden kurulmaz; seçili pencere sonradan açılınca önizleme ona geçer."""
    from altyazitr import winutil
    from tests.test_history import _home

    home, ctx, _toasts = _home(qapp, monkeypatch)
    c = ctx.s.capture
    c.mode, c.window_exe, c.window_title = "window", "chiaki.exe", "chiaki-ng"
    open_now: list = [winutil.WindowInfo(11, "Not Defteri", "notepad.exe", 800, 600)]
    monkeypatch.setattr("altyazitr.gui.home_page.winutil.list_windows", lambda: list(open_now))
    monkeypatch.setattr("altyazitr.gui.home_page.winutil.find_window",
                        lambda exe, title, **_kw: next((w for w in open_now if w.exe == exe), None))
    home.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    home.show()
    home.refresh_windows()
    home.refresh()
    combo = home.win_combo
    texts = lambda: [combo.itemText(i) for i in range(combo.count())]  # noqa: E731
    assert texts()[0].startswith("(açık değil)") and combo.currentIndex() == 0 and home._hwnd == 0

    clears: list[int] = []
    real_clear = combo.clear
    monkeypatch.setattr(combo, "clear", lambda: (clears.append(1), real_clear()))
    home._ticks = 0
    home._watchdog()
    home._watchdog()  # iki saniye: liste aynı, yeniden kurulmaz
    assert clears == []

    open_now.append(winutil.WindowInfo(22, "chiaki-ng", "chiaki.exe", 1920, 1080))  # seçili pencere açıldı
    home._watchdog()
    home._watchdog()
    assert clears == [1] and not any(t.startswith("(açık değil)") for t in texts())
    assert combo.itemData(combo.currentIndex()) in (("chiaki.exe", "chiaki-ng"), ["chiaki.exe", "chiaki-ng"])
    assert home._hwnd == 22 and home.source._editable  # önizleme açılan pencereye geçti

    open_now.append(winutil.WindowInfo(33, "OBS 31", "obs64.exe", 1280, 720))  # liste açılırken de tazelenir
    combo.opening.emit()
    assert any("OBS 31" in t for t in texts())
    assert combo.itemData(combo.currentIndex()) in (("chiaki.exe", "chiaki-ng"), ["chiaki.exe", "chiaki-ng"])
    home.hide()


def test_varsayilan_altyazi_bolgesi(qapp):
    """Yeni kullanıcıda (ekran modu, bölge yok) açılışta birincil ekrana varsayılan şerit atanır; Windows ölçeği
    fiziksel piksele çevrilir. Bölgesi olana ve pencere moduna dokunulmaz; pencere modunun varsayılanı aynı şerit."""
    from types import SimpleNamespace

    from PySide6.QtCore import QRect

    from altyazitr.app import apply_default_screen_region
    from altyazitr.config import DEFAULT_REGION, Settings

    def screen(w, h, dpr):
        return SimpleNamespace(geometry=lambda: QRect(0, 0, w, h), devicePixelRatio=lambda: dpr)

    s = Settings()
    assert s.capture.mode == "screen" and not s.capture.is_ready()
    assert s.capture.region == list(DEFAULT_REGION)
    assert apply_default_screen_region(s, screen(1920, 1080, 1.0))
    assert s.capture.rect == [338, 868, 1233, 212] and s.capture.is_ready()
    assert not apply_default_screen_region(s, screen(1920, 1080, 1.0))  # bölgesi var: dokunulmaz
    assert s.capture.rect == [338, 868, 1233, 212]

    s2 = Settings()
    assert apply_default_screen_region(s2, screen(1707, 960, 1.5))  # 2560×1440, %150 ölçek
    assert s2.capture.rect == sv.frac_to_rect(DEFAULT_REGION, (0, 0, 2560, 1440))

    s3 = Settings()
    s3.capture.mode = "window"
    assert not apply_default_screen_region(s3, screen(1920, 1080, 1.0)) and s3.capture.rect == []


def test_buyuk_pencerede_yukseklik_siniri(view):
    """Büyük pencerede önizleme sınırlı yükseklikte kalır; görüntü oranını koruyarak ortalanır, bölge çizimi ve
    yakalanacak karenin genişliği görüntünün kendisine göredir."""
    v, _got = view
    assert v.heightForWidth(1600) == 900
    v.set_height_cap(500)
    assert v.heightForWidth(1600) == 500 and v.heightForWidth(640) == 360  # dar alanda sınır devreye girmez
    v.resize(1600, 500)
    ir = v._img_rect()
    assert round(ir.width()) == v.display_width() == round(500 * 1920 / 1080)
    assert ir.left() == pytest.approx((1600 - ir.width()) / 2)
    v.set_height_cap(0)
    assert v.heightForWidth(1600) == 900
