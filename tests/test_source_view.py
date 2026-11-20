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
    monkeypatch.setattr("altyazitr.gui.home_page.winutil.find_window", lambda exe, title: None)
    home.refresh()
    assert not home.source._editable and "açık değil" in home.source._message
    home._region_committed((0.1, 0.8, 0.8, 0.15))
    assert c.region == [0.1, 0.8, 0.8, 0.15] and edited == [1, 1]
    assert not home.read_box.isVisibleTo(home)  # okunan bölge yalnız çeviri sürerken
