"""Kaydırırken yanlışlıkla değer değişmesin (gui/wheel_guard.py): açılır liste tekerlekle hiç değişmez; sayı
kutusu ve kaydırıcı yalnız odaktayken değişir; değişmeyince sayfa kayar. Qt 'offscreen'."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    from altyazitr.gui import wheel_guard

    wheel_guard.install(app)
    return app


def _wheel(app, widget, dy: int = -120) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, ev)
    app.processEvents()


@pytest.fixture
def page(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget

    sa = QScrollArea()
    inner = QWidget()
    lay = QVBoxLayout(inner)
    combo = QComboBox()
    combo.addItems(["Bir", "İki", "Üç"])
    spin = QSpinBox()
    spin.setRange(0, 10)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(50)
    for w in (combo, spin, slider):
        lay.addWidget(w)
    lay.addSpacing(3000)  # sayfa kaydırılabilsin
    sa.setWidget(inner)
    sa.setWidgetResizable(True)
    sa.resize(400, 300)
    sa.show()
    qapp.processEvents()
    yield sa, combo, spin, slider
    sa.close()
    sa.deleteLater()


def test_odakta_degilken_deger_degismez_sayfa_kayar(qapp, page):
    from PySide6.QtCore import Qt

    sa, combo, spin, slider = page
    for w in (combo, spin, slider):
        assert w.focusPolicy() != Qt.FocusPolicy.WheelFocus  # tekerlek odak vermez
    sa.setFocus()  # açılışta ilk denetime kendiliğinden verilen odak kalksın (sayfada gezinen kullanıcı gibi)
    qapp.processEvents()
    assert not combo.hasFocus() and not spin.hasFocus() and not slider.hasFocus()
    bar = sa.verticalScrollBar()
    start = bar.value()
    _wheel(qapp, combo)
    _wheel(qapp, spin)
    _wheel(qapp, slider)
    assert combo.currentIndex() == 0 and spin.value() == 0 and slider.value() == 50
    assert bar.value() > start  # tekerlek sayfayı kaydırdı
    assert not combo.hasFocus() and not spin.hasFocus()


def test_odaktayken_tekerlek_degeri_degistirir(qapp, page):
    from PySide6.QtCore import Qt

    sa, combo, spin, _slider = page
    sa.activateWindow()
    qapp.processEvents()
    spin.setFocus(Qt.FocusReason.MouseFocusReason)
    qapp.processEvents()
    if not spin.hasFocus():
        pytest.skip("Bu ortamda pencere etkinleştirilemedi (odak verilemiyor)")
    _wheel(qapp, spin, dy=120)
    assert spin.value() == 1
    combo.setFocus(Qt.FocusReason.MouseFocusReason)  # seçimden sonra odakta kalan liste de değişmez
    qapp.processEvents()
    _wheel(qapp, combo)
    assert combo.currentIndex() == 0


def test_son_ceviriler_ucuna_gelince_tekerlegi_sayfaya_birakir(qapp):
    """Son çeviriler listesi taşmıyorsa (ya da ucundaysa) tekerlek olayını üstlenmez: olay sayfanın kaydırma alanına
    geçer (Canlı sayfasının geniş düzeni). Qt elle gönderilen tekerlek olayını üst öğeye iletmediği için burada olayın
    kabul edilmediği ve listenin kaymadığı denetlenir; gerçek fare olayında Qt üst öğeye iletir."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from altyazitr.gui.history import HistoryModel, HistoryView

    view = HistoryView(HistoryModel())
    view.resize(300, 200)
    view.show()
    qapp.processEvents()
    for dy in (-120, 120):
        pos = QPointF(50, 50)
        ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        qapp.sendEvent(view.viewport(), ev)
        assert not ev.isAccepted() and view.verticalScrollBar().value() == 0
    view.close()
    view.deleteLater()
