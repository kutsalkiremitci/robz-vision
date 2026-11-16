"""Tanıtım turu: adım tanımları ve başlatma/kapatma davranışı (ekran gerektirmez; Qt 'offscreen' çalışır)."""

import os
import re
import time
from types import SimpleNamespace

import pytest

from altyazitr.gui import tour

PAGES = {"home", "style", "translate", "settings"}
SIDES = {"below", "above", "right", "left"}


def _settings(seen=False, hotkey="Ctrl+Alt+T"):
    return SimpleNamespace(tour_seen=seen, hotkeys=SimpleNamespace(toggle=hotkey))


# --- adım tanımları ----------------------------------------------------------------
def test_steps_are_valid():
    win = SimpleNamespace(ctx=SimpleNamespace(s=_settings()))
    steps = tour.STEPS
    assert 6 <= len(steps) <= 12
    assert len({s.key for s in steps}) == len(steps)
    assert not steps[0].targets and not steps[-1].targets  # karşılama ve kapanış ortada
    for s in steps:
        assert s.page is None or s.page in PAGES, s.key
        assert set(s.prefer) <= SIDES, s.key
        assert all(callable(t) for t in s.targets), s.key
        # başlık: cümle düzeni, kısa, noktasız
        assert s.title and s.title[0].isupper() and not s.title.isupper(), s.key
        assert len(s.title) <= 40 and not s.title.endswith("."), s.key
        body = s.text(win)
        assert body, s.key
        # en fazla 2 kısa cümle; ünlem, orta nokta, emoji yok
        sentences = [x for x in re.split(r"(?<=[.?!])\s+", body.strip()) if x]
        assert len(sentences) <= 2, (s.key, sentences)
        assert "!" not in body and "·" not in body, s.key
        assert all(ch.isalnum() or ch in " .,;:'+?-()" for ch in body), (s.key, body)
        assert len(body) <= 170, s.key
    middle = steps[1:-1]
    assert all(s.targets for s in middle)


def test_start_step_uses_configured_hotkey():
    start = next(s for s in tour.STEPS if s.key == "start")
    custom = SimpleNamespace(ctx=SimpleNamespace(s=_settings(hotkey="Ctrl+Shift+Y")))
    assert "Ctrl+Shift+Y" in start.text(custom)
    none = SimpleNamespace(ctx=SimpleNamespace(s=_settings(hotkey="")))
    assert "Ctrl" not in start.text(none) and start.text(none)
    broken = SimpleNamespace()  # ayarlar okunamasa da metin üretilir
    assert start.text(broken)


# --- davranış ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        # başka bir test yalnız QGuiApplication açmış olabilir; QWidget için QApplication gerekir
        app.shutdown()
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


def _pump(app, sec=0.1):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        time.sleep(0.005)


def _fake_win(seen):
    """Sayfası ve menüsü olmayan sahte pencere: hedefler bulunamaz, tur yine de çalışmalı (ortada)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    class FakeWin(QWidget):
        def __init__(self):
            super().__init__()
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self.saves = 0
            self.pages_visited = []
            self.pages = {}
            self.ctx = SimpleNamespace(s=_settings(seen), save_later=self._save)
            self.resize(900, 640)

        def _save(self):
            self.saves += 1

        def goto(self, key):
            self.pages_visited.append(key)

    w = FakeWin()
    w.show()
    return w


def test_maybe_start_respects_tour_seen(qapp):
    seen = _fake_win(seen=True)
    assert tour.maybe_start_tour(seen) is False
    assert tour.active_tour(seen) is None

    win = _fake_win(seen=False)
    assert tour.maybe_start_tour(win) is True
    _pump(qapp)
    t = tour.active_tour(win)
    assert t is not None and t.index == 0
    assert t._hole is None  # hedef yok → ortada, deliksiz
    win.close()
    seen.close()


def test_later_keeps_unseen_and_finish_marks_seen(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = _fake_win(seen=False)
    tour.start_tour(win)
    _pump(qapp)
    t = tour.active_tour(win)
    QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Right)
    QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Right)
    QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Left)
    assert t.index == 1
    QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Escape)  # Daha sonra
    _pump(qapp)
    assert tour.active_tour(win) is None
    assert win.ctx.s.tour_seen is False and win.saves == 0

    tour.start_tour(win)
    _pump(qapp)
    t = tour.active_tour(win)
    t.go(4)
    tour.start_tour(win)  # açık turda yeniden başlatma: 1. adıma döner, tek katman kalır
    _pump(qapp)
    t = tour.active_tour(win)
    assert t.index == 0 and len(win.findChildren(type(t))) == 1
    for _ in range(t.count - 1):
        QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Return)
    assert t.index == t.count - 1 and t.bubble.next_btn.text() == "Bitir"
    QTest.keyClick(t.bubble.next_btn, Qt.Key.Key_Return)  # Bitir
    _pump(qapp)
    assert tour.active_tour(win) is None
    assert win.ctx.s.tour_seen is True and win.saves == 1
    assert "home" in win.pages_visited
    assert tour.maybe_start_tour(win) is False
    win.close()
