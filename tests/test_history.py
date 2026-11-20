"""Ana sayfadaki "Son çeviriler" listesi ve Ayarlar'daki "Altyazı akışı" bölümü (ekran gerektirmez;
Qt 'offscreen').

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır
(bkz. test_profiles.py). Açılan her pencere öğesi testten sonra kapatılıp Qt tarafında silinir: açık kalırsa
Python onu süreç kapanırken QApplication'dan sonra yok eder ve süreç çöker (bkz. test_style_profiles.py)."""

import os
import time
from types import SimpleNamespace

import pytest

from altyazitr.config import FlowCfg, Settings


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


_OBJS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if not _OBJS:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget

    for o in reversed(_OBJS):
        if isinstance(o, QWidget):
            o.close()
        o.deleteLater()
    _OBJS.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _keep(o):
    _OBJS.append(o)
    return o


def _ev(text, original="", speaker="", line=None, **kw):
    ev = {"text": text, "original": original, "speaker": speaker, "cached": False, "engine": "Yerel",
          "ocr_ms": 6.0, "tr_ms": 24.0, "latency_ms": 300.0}
    if line is not None:
        ev["line"] = line
    ev.update(kw)
    return ev


def _panel(qapp, toasts=None):
    from altyazitr.gui.history import HistoryPanel

    return _keep(HistoryPanel(toast=(lambda m, k="info": toasts.append(m)) if toasts is not None else None))


def _clipboard():
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.clipboard().text()


# --- aynı satır yerinde güncellenir ---------------------------------------------------------
def test_same_line_updates_in_place(qapp):
    from altyazitr.gui.history import HistoryModel

    m = _keep(HistoryModel())
    t0 = time.mktime((2026, 11, 17, 21, 14, 2, 0, 0, -1))
    assert m.add(_ev("Arabaya bin.", "Get in the car.", "LUCIA", line=1), now=t0) == (0, True)
    assert m.add(_ev("Acele et!", "Hurry up!", line=2), now=t0 + 3) == (0, True)
    # hibrit: aynı satırın daha iyi çevirisi, sonraki satır gösterildikten sonra gelir
    assert m.add(_ev("Arabaya atla.", "Get in the car.", "LUCIA", line=1), now=t0 + 9) == (1, False)
    assert m.rowCount() == 2
    old = m.entry(1)
    assert (old.text, old.original, old.speaker, old.line) == ("Arabaya atla.", "Get in the car.", "LUCIA", 1)
    assert old.time == time.strftime("%H:%M:%S", time.localtime(t0))  # ilk göründüğü an kalır
    assert m.entry(0).text == "Acele et!"  # en yenisi üstte
    # daktilo uzaması: aynı kimlik, uzayan metin
    m.add(_ev("Acele et! Polis geliyor.", "Hurry up! Cops are coming.", line=2))
    assert m.rowCount() == 2 and m.entry(0).original == "Hurry up! Cops are coming."
    # boş metin eklenmez
    assert m.add(_ev("  ", "x", line=3)) == (-1, False) and m.rowCount() == 2


def test_missing_line_new_entry_when_text_changes(qapp):
    from altyazitr.gui.history import HistoryModel

    m = _keep(HistoryModel())
    m.add(_ev("Merhaba.", "Hello."))
    m.add(_ev("Merhaba.", "Hello."))  # aynı metin: aynı satır
    assert m.rowCount() == 1
    m.add(_ev("Selam.", "Hi."))
    assert m.rowCount() == 2 and m.entry(0).text == "Selam."


def test_forget_lines_after_restart(qapp):
    from altyazitr.gui.history import HistoryModel

    m = _keep(HistoryModel())
    m.add(_ev("Bir.", line=1))
    m.forget_lines()  # işlem hattı yeniden başladı: kimlik baştan sayılsa da eski satır değişmez
    m.add(_ev("Başka bir satır.", line=1))
    assert m.rowCount() == 2 and m.entry(1).text == "Bir."


def test_keeps_last_200(qapp):
    from altyazitr.gui.history import HISTORY_MAX

    assert HISTORY_MAX == 200
    p = _panel(qapp)
    for i in range(205):
        p.add(_ev(f"Satır {i}", f"Line {i}", line=i))
    assert p.count() == 200
    items = p.model.entries()
    assert items[0].text == "Satır 204" and items[-1].text == "Satır 5"
    p.add(_ev("Satır 4 düzeltildi", line=4))  # listeden düşmüş satırın güncellemesi: yeni satır olur
    assert p.count() == 200 and p.model.entry(0).text == "Satır 4 düzeltildi"


def test_clear(qapp):
    p = _panel(qapp)
    assert p.empty.isVisibleTo(p) and not p.view.isVisibleTo(p) and not p.bar.isVisibleTo(p)
    p.add(_ev("Bir.", "One.", line=1))
    p.add(_ev("İki.", "Two.", line=2))
    assert p.count() == 2 and not p.empty.isVisibleTo(p) and p.view.isVisibleTo(p) and p.bar.isVisibleTo(p)
    p.clear_btn.click()
    assert p.count() == 0 and p.empty.isVisibleTo(p) and not p.view.isVisibleTo(p)
    assert p.empty.text() == "Henüz çeviri yok"
    p.add(_ev("Üç.", line=3))
    assert p.count() == 1 and p.view.isVisibleTo(p)


# --- kopyalama ---------------------------------------------------------------------------------
def test_copy_text_format(qapp):
    from altyazitr.gui.history import HistoryEntry, entry_text, transcript

    a = HistoryEntry("21:14:02", "Arabaya bin.", "Get in the car.", "LUCIA")
    b = HistoryEntry("21:14:05", "Acele et!", "Hurry up!")
    c = HistoryEntry("21:14:07", "Tamam.")
    assert entry_text(a) == "LUCIA: Arabaya bin."
    assert entry_text(a, original=True) == "LUCIA: Get in the car.\nLUCIA: Arabaya bin."
    assert entry_text(b, original=True) == "Hurry up!\nAcele et!"
    assert entry_text(c, original=True) == "Tamam."  # orijinal yoksa yalnız Türkçe
    assert transcript([a, b]) == ("21:14:02\nLUCIA: Get in the car.\nLUCIA: Arabaya bin.\n\n"
                                  "21:14:05\nHurry up!\nAcele et!")

    toasts: list[str] = []
    p = _panel(qapp, toasts)
    p.add(_ev("Arabaya bin.", "Get in the car.", "LUCIA", line=1), now=1_800_000_000)
    p.add(_ev("Acele et!", "Hurry up!", line=2), now=1_800_000_003)
    p.copy_row(1)
    assert _clipboard() == "LUCIA: Arabaya bin."
    p.copy_row(0, original=True)
    assert _clipboard() == "Hurry up!\nAcele et!"
    p.copy_all_btn.click()
    t1, t2 = (time.strftime("%H:%M:%S", time.localtime(t)) for t in (1_800_000_000, 1_800_000_003))
    assert _clipboard() == f"{t1}\nLUCIA: Get in the car.\nLUCIA: Arabaya bin.\n\n{t2}\nHurry up!\nAcele et!"
    assert toasts == ["Çeviri kopyalandı", "İngilizce ve Türkçe kopyalandı", "2 satır kopyalandı"]


def test_menu_and_keyboard_copy(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    p = _panel(qapp)
    p.add(_ev("Arabaya bin.", "Get in the car.", "LUCIA", line=1))
    p.add(_ev("Tamam.", line=2))
    menu = _keep(p.view.build_menu(1))
    acts = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in acts] == ["Türkçesini kopyala", "İngilizce + Türkçe kopyala"]
    assert acts[1].isEnabled()
    acts[1].trigger()
    assert _clipboard() == "LUCIA: Get in the car.\nLUCIA: Arabaya bin."
    no_en = _keep(p.view.build_menu(0))
    assert not no_en.actions()[1].isEnabled()  # İngilizcesi olmayan satır
    p.view.setCurrentIndex(p.model.index(1))
    QTest.keyClick(p.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert _clipboard() == "LUCIA: Arabaya bin."


def test_copy_button_click(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    p = _panel(qapp)
    p.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    p.resize(640, 400)
    p.show()
    p.add(_ev("Arabaya bin.", "Get in the car.", "LUCIA", line=1))
    p.add(_ev("Acele et!", "Hurry up!", line=2))
    qapp.processEvents()
    v = p.view
    idx = p.model.index(1)
    btn = v._delegate.button_rect(v.visualRect(idx), v._delegate.layout(p.model.entry(1)))
    assert v.visualRect(idx).contains(btn)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, btn.center())
    assert _clipboard() == "LUCIA: Arabaya bin."
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(btn.left() - 200, btn.center().y()))  # satırın kendisine tıklamak kopyalamaz
    assert _clipboard() == "LUCIA: Arabaya bin."


def test_new_line_does_not_move_scrolled_view(qapp):
    """Eski satırları okurken gelen yeni satır görüntüyü kaydırmaz; en üstteyken yeni satır görünür."""
    from PySide6.QtCore import Qt

    p = _panel(qapp)
    p.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    p.resize(640, 600)
    p.show()
    for i in range(30):
        p.add(_ev(f"Satır {i} biraz uzun bir çeviri metni", f"Line {i}", line=i))
    qapp.processEvents()
    v, sb = p.view, p.view.verticalScrollBar()
    assert v.height() == v.MAX_H and sb.maximum() > 0
    sb.setValue(sb.maximum() // 2)
    qapp.processEvents()
    target = p.model.entry(10)
    y = v.visualRect(p.model.index(p.model.entries().index(target))).top()
    p.add(_ev("Yeni satır", "New line", line=100))
    qapp.processEvents()
    assert v.visualRect(p.model.index(p.model.entries().index(target))).top() == y
    sb.setValue(0)
    p.add(_ev("Daha yeni satır", "Newer line", line=101))
    qapp.processEvents()
    assert sb.value() == 0 and v.visualRect(p.model.index(0)).top() == 0


# --- Ana sayfa: işlem hattı olayları ------------------------------------------------------------
def _home(qapp, monkeypatch):
    from PySide6.QtCore import QObject, QRect, Signal

    import altyazitr.gui.home_page as homemod

    monkeypatch.setattr(homemod, "list_monitors", lambda: [(0, 0, 1920, 1080)])  # gerçek ekran okunmaz
    monkeypatch.setattr(homemod.SourceGrabber, "start", lambda self: None)  # gerçek ekran yakalanmaz
    monkeypatch.setattr(homemod.winutil, "list_windows", lambda: [])

    class Pipe(QObject):
        running_changed = Signal(bool)
        status = Signal(str)
        preview = Signal(object, float)
        subtitle = Signal(dict)
        running = False
        preview_enabled = True

    class Disp(QObject):
        visibility_changed = Signal(bool)
        visible = True

        def show_test(self):
            pass

        def toggle_visible(self):
            pass

    class Ov(QObject):
        editing_changed = Signal(bool)
        geometry_saved = Signal()
        editing = False

        def set_editing(self, on):
            pass

        def reset_position(self):
            pass

        def apply_settings(self):
            pass

        def geometry(self):
            return QRect(0, 0, 800, 160)

        def screen(self):
            return None

    class Ctx(QObject):
        obs_clients = Signal(int)

        def __init__(self):
            super().__init__()
            self.s = Settings()
            self.pipeline, self.display, self.overlay = _keep(Pipe()), _keep(Disp()), _keep(Ov())
            self.server = SimpleNamespace(url="http://127.0.0.1:8765/")

        def toggle_running(self):
            pass

        def save_later(self):
            pass

        def request_restart(self):
            pass

    ctx = _keep(Ctx())
    toasts: list[str] = []
    win = SimpleNamespace(ctx=ctx, toast=lambda m, k="info": toasts.append(m), select_region=lambda: None)
    return _keep(homemod.HomePage(win)), ctx, toasts


def test_home_page_history_from_pipeline(qapp, monkeypatch):
    home, ctx, toasts = _home(qapp, monkeypatch)
    h = home.history
    assert h.count() == 0 and not home.metrics.isVisibleTo(home)
    sub = ctx.pipeline.subtitle
    sub.emit(_ev("Arabaya bin.", "Get in the car.", "LUCIA", line=1, ocr_ms=7.0, tr_ms=20.0))
    sub.emit(_ev("Acele et!", "Hurry up!", line=2))
    sub.emit(_ev("Arabaya atla.", "Get in the car.", "LUCIA", line=1, ocr_ms=8.0, tr_ms=410.0))
    assert h.count() == 2
    assert [e.text for e in h.model.entries()] == ["Acele et!", "Arabaya atla."]
    assert home.metrics.isVisibleTo(home)  # en son olayın süreleri başlığın yanında
    assert "410 ms" in home.metrics._items["tr"].text() and "8 ms" in home.metrics._items["ocr"].text()
    # yeniden başlatma: eski kimlikler eşleşmez
    ctx.pipeline.running_changed.emit(True)
    sub.emit(_ev("Yepyeni.", line=1))
    assert h.count() == 3 and h.model.entry(0).text == "Yepyeni."
    h.copy_row(0)
    assert toasts == ["Çeviri kopyalandı"]


# --- Ayarlar: altyazı akışı -------------------------------------------------------------------------
def _flow_card(qapp, flow=None):
    from altyazitr.gui.settings_page import FlowCard

    s = Settings()
    if flow is not None:
        s.flow = flow
    calls: list[str] = []
    ctx = SimpleNamespace(s=s, save_later=lambda: calls.append("save"),
                          display=SimpleNamespace(push_style=lambda: calls.append("push")))
    return _keep(FlowCard(ctx)), s, calls


def test_flow_controls_write_settings(qapp):
    card, s, calls = _flow_card(qapp)
    assert card.mode.group.checkedId() == 0  # Akış
    assert (card.lines.value(), card.min_show.value()) == (3, 3.0)
    assert (card.lines.minimum(), card.lines.maximum()) == (2, 4)
    assert (card.min_show.minimum(), card.min_show.maximum()) == (1.5, 6.0)
    assert card.lines.isEnabled() and card.min_show.isEnabled()

    card.lines.setValue(4)
    assert s.flow.max_lines == 4 and isinstance(s.flow.max_lines, int)
    assert calls == ["save", "push"]  # önce kaydet, sonra iki çıktıya uygula
    card.min_show.setValue(4.5)
    assert s.flow.min_show_s == 4.5 and isinstance(s.flow.min_show_s, float)
    assert calls == ["save", "push"] * 2

    card.mode.group.button(1).click()  # Tek satır
    assert s.flow.mode == "single" and calls == ["save", "push"] * 3
    assert not card.lines.isEnabled() and not card.min_show.isEnabled()  # yalnız akışta işe yarar
    card.mode.group.button(1).click()  # zaten seçili: bir şey olmaz
    assert calls == ["save", "push"] * 3
    card.mode.group.button(0).click()
    assert s.flow.mode == "flow" and card.lines.isEnabled() and len(calls) == 8


def test_flow_card_reads_saved_settings(qapp):
    card, s, calls = _flow_card(qapp, FlowCfg(mode="single", max_lines=2, min_show_s=5.5))
    assert card.mode.group.checkedId() == 1
    assert (card.lines.value(), card.min_show.value()) == (2, 5.5)
    assert not card.lines.isEnabled()
    assert calls == []  # açılırken hiçbir şey kaydedilmez
    tips = [card.mode.group.button(i).toolTip() for i in (0, 1)]
    assert tips[0] == "Son çeviriler alt alta kalır; hızlı konuşmada satır kaybolmaz" and tips[1]
