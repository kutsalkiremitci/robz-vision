"""Ana pencere ve yerel yapay zekâ kurulumu: kenar çubuğundaki durum satırı, bildirimler ve güncelleyen kullanıcıya
bir kezlik davet (ekran gerektirmez; Qt 'offscreen', pencere masaüstünde açılmaz).

Kurulum servisi yerine aynı sinyalleri ve alanları taşıyan sahte bir QObject (FakeAi; değerleri test verir), uygulama
bağlamı yerine bildirim kanalı (notify → notified) AppContext'teki gibi çalışan sahte bir bağlam kullanılır. Çeviri
sayfası gerçektir (donanım önerisini o okur, arka planda); öteki sayfalar boş yer tutuculardır. Ağ yok.

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır (bkz.
test_profiles.py). Pencereler her testten sonra gizlenip Qt tarafında silinir; süren donanım okuması önce biter."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from altyazitr.ai_setup import Step
from altyazitr.ai_setup import model_title as catalog_title
from altyazitr.config import Settings

GB = 1_000_000_000
NET_ERROR = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.\nAyrıntı: ConnectError: getaddrinfo"
READY = "Yapay zekâ çevirisi hazır"
READY_LATER = "Yapay zekâ hazır; durdurup başlatınca devreye girer"
FAILED = "Yapay zekâ kurulamadı; ayrıntı Çeviri sayfasında"
INVITE = "Yeni: yapay zekâ çevirisi. Çeviri sayfasından kurabilirsiniz."


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


_WINDOWS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if not _WINDOWS:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from altyazitr.gui import ai_setup_view as v

    for win in _WINDOWS:
        win.ctx.ai.gate.set()
    end = time.monotonic() + 5
    while v.surveys_running() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    for win in _WINDOWS:
        win.tray.hide()
        win.hide()  # close() uygulamadan çıkardı (tepsi yok): yalnız gizlenir
        win.deleteLater()
    _WINDOWS.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


def advice(tier: str = "good", model: str | None = "gemma4:12b"):
    offer = tier not in ("none", "weak")
    return SimpleNamespace(tier=tier, offer=offer, preselect=offer, model=model if offer else None, gpu=None,
                           line="NVIDIA GeForce RTX 4070 12 GB", warn="", tip="")


def plan(model: str = "gemma4:12b", apply: bool = True):
    steps = [Step.DOWNLOAD, Step.INSTALL, Step.START, Step.CHECK, Step.PULL] if apply else [Step.PULL]
    return SimpleNamespace(model=model, steps=steps, download_bytes=10 * GB, disk_bytes=15 * GB, disk_path=None,
                           blocker="", blocker_kind="", apply=apply, url="http://127.0.0.1:11434", disk_free=None,
                           ollama_version="", updating=False)


def _fake_ai(settings, adv):
    from PySide6.QtCore import QObject, Signal

    class FakeAi(QObject):
        """AiSetup'ın sinyalleri ve alanları; işi yürütmez (bkz. test_translate_page.py). succeed() başarıda servisin
        yaptığını yapar (ayar uygulayan işte motor yapay zekâ olur); iptalden sonra durum "idle", hatadan sonra
        "failed"."""

        changed = Signal()
        finished = Signal(bool, str)

        def __init__(self):
            super().__init__()
            self.settings = settings
            self.gate = threading.Event()
            self.gate.set()
            self.state, self.step, self.substep = "idle", None, ""
            self.done_bytes = self.total_bytes = self.all_done_bytes = self.all_total_bytes = 0
            self.fraction = 0.0
            self.eta_s = None
            self.message = self.error = self.error_kind = self.model = self.note = ""
            self.speed = None
            self.active_plan = None
            self.can_cancel = self.can_retry = True
            self.manual_install = False

        def advice(self):
            self.gate.wait(10)
            return adv

        def plan(self, model=None, apply=True):
            return plan(model or "gemma4:12b", apply)

        def start(self, p):
            self.active_plan, self.model = p, p.model
            self.state, self.step, self.message, self.error = "running", None, "Hazırlanıyor…", ""
            self.changed.emit()

        def retry(self):
            self.start(self.active_plan)

        def cancel(self):
            if self.state in ("running", "paused"):
                self.state, self.step, self.message = "idle", None, "İptal edildi."
                self.finished.emit(False, "İptal edildi.")
            else:
                self.state, self.error, self.message = "idle", "", ""
            self.changed.emit()

        def force_resume(self):
            pass

        def emit(self, **fields):
            for k, val in fields.items():
                setattr(self, k, val)
            self.changed.emit()

        def fail(self, error):
            self.state, self.error, self.message = "failed", error, error.splitlines()[0]
            self.finished.emit(False, error)
            self.changed.emit()

        def succeed(self, running: bool = False):
            if self.active_plan.apply:
                t = self.settings.translate
                t.engine, t.ollama_model = "ollama", self.model
                text = READY_LATER if running else READY
            else:
                text = f"{catalog_title(self.model)} indirildi."
            self.state, self.step, self.message = "done", None, text
            self.finished.emit(True, text)
            self.changed.emit()

    return FakeAi()


def _ctx(settings, ai):
    from PySide6.QtCore import QObject, Signal

    class FakePipeline(QObject):
        error = Signal(str)
        notice = Signal(str)
        running_changed = Signal(bool)

        def __init__(self):
            super().__init__()
            self.running = False
            self.preview_enabled = True
            self.cache = SimpleNamespace(clear=lambda: None)

    class FakeCtx(QObject):
        """AppContext'in ana pencereye görünen yüzü. Bildirim kanalı AppContext'teki gibi: notify(metin, düzey) →
        notified(kısa metin, ayrıntı, düzey); işlem hattının notice'i de buraya bağlı."""

        obs_clients = Signal(int)
        needs_region = Signal()
        notified = Signal(str, str, str)

        def __init__(self):
            super().__init__()
            self.s, self.ai = settings, ai
            self.pipeline = FakePipeline()
            self.pipeline.notice.connect(lambda text: self.notify(text, "warn"))
            self.display = SimpleNamespace(toggle_visible=lambda: None, push_style=lambda: None)
            self.server_error = None
            self.saves = self.restarts = 0
            self.notes: list[tuple[str, str, str]] = []
            self.notified.connect(lambda *a: self.notes.append(a))

        def notify(self, text: str, level: str = "info") -> None:
            head, _, rest = (text or "").strip().partition("\n")
            head, detail = head.strip(), rest.strip().removeprefix("Ayrıntı:").strip()
            if head:
                self.notified.emit(head, detail, level)

        def save_later(self):
            self.saves += 1

        def request_restart(self):
            self.restarts += 1

        def toggle_running(self):
            pass

    return FakeCtx()


def _window(qapp, monkeypatch, s: Settings | None = None, adv=advice()):
    """Ana pencere: Çeviri sayfası gerçek, ötekiler boş. Tur görülmüş, ilk açılış geçmiş (kayıt sayılmasın)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    import altyazitr.gui.main_window as mw
    import altyazitr.gui.translate_page as tp

    class Blank(QWidget):
        def __init__(self, _win):
            super().__init__()

    monkeypatch.setattr(mw, "PAGES", [("home", "Ana sayfa", Blank), ("style", "Görünüm", Blank),
                                      ("translate", "Çeviri", tp.TranslatePage), ("settings", "Ayarlar", Blank)])
    monkeypatch.setattr(tp, "quickmt_downloaded", lambda: True)
    monkeypatch.setattr(tp, "confirm_ai_setup", lambda parent, ai: False)
    s = s or Settings()
    s.first_run, s.tour_seen = False, True
    ctx = _ctx(s, _fake_ai(s, adv))
    win = mw.MainWindow(ctx)
    _WINDOWS.append(win)
    win.tray.hide()
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    return win, ctx


def _toasts(win) -> list[tuple[str, str]]:
    return [(t.text, t.kind) for t in win._toasts._toasts if not t._closing]


def _vis(win, w) -> bool:
    return w.isVisibleTo(win)


def _downloading(win, ai, **extra) -> None:
    """Model iniyor (Ollama kurulu: planda yalnız model): 2,7 / 8,0 GB, kalan ~9 dk. Panelin saati sabit."""
    win.pages["translate"].hint_progress._clock = lambda: 1000.0
    if ai.active_plan is None:
        ai.start(plan(apply=False))
    fields = dict(state="running", step=Step.PULL, model="gemma4:12b", message="Gemma 4 12B indiriliyor",
                  done_bytes=int(2.7 * GB), total_bytes=8 * GB, eta_s=540)
    fields.update(extra)
    ai.emit(**fields)


def _panel_percent(win) -> str:
    return win.pages["translate"].hint_progress.pct.text()


# --- kenar çubuğundaki durum satırı ---------------------------------------------------------------------------
def test_ai_status_row_texts(qapp, monkeypatch):
    """Yalnız iş sürerken ya da hata varken görünür: "Yapay zekâ %33" (ipucu: adım, miktar, kalan süre),
    "Yapay zekâ duraklatıldı", "Yapay zekâ kurulamadı" (ipucu: hatanın kısa metni)."""
    win, ctx = _window(qapp, monkeypatch)
    ai, row = ctx.ai, win._ai_row
    assert not _vis(win, row)  # iş yok
    _downloading(win, ai)
    assert _vis(win, row) and row.tx.text() == "Yapay zekâ %33" and row.accessibleName() == "Yapay zekâ %33"
    assert _panel_percent(win) == "%33"  # Çeviri sayfasındaki panelle aynı sayı
    assert row.toolTip() == "Gemma 4 12B indiriliyor: 2,7 / 8,0 GB, kalan ~9 dk"
    assert row.accessibleDescription() == "Çeviri sayfasını açar"
    ai.emit(step=Step.START, message="Ollama başlatılıyor", done_bytes=0, total_bytes=0, eta_s=None)
    assert row.toolTip() == "Ollama başlatılıyor"
    _downloading(win, ai, message="İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.", eta_s=None)
    assert row.toolTip() == ("Gemma 4 12B indiriliyor: 2,7 / 8,0 GB\n"
                             "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.")
    ai.emit(state="paused", message="Duraklatıldı: çeviri çalışıyor")
    assert _vis(win, row) and row.tx.text() == "Yapay zekâ duraklatıldı"
    assert row.toolTip().startswith("Duraklatıldı: çeviri çalışıyor.")
    ai.fail(NET_ERROR)
    assert _vis(win, row) and row.tx.text() == "Yapay zekâ kurulamadı"
    assert row.toolTip() == NET_ERROR.splitlines()[0]
    ai.cancel()  # "Hızlı çeviride kal": hata unutulur
    assert not _vis(win, row)
    _downloading(win, ai)
    ai.emit(state="done")
    assert not _vis(win, row)


def test_ai_status_percent_matches_progress_panel(qapp, monkeypatch):
    """Ollama da kurulurken yüzde, sayfadaki panelinkiyle aynıdır: baytsız adımlar (kurulum, başlatma) beklenen
    süreleriyle sayılır (servisin yalnız indirilen baytlardan oranı farklı olurdu)."""
    win, ctx = _window(qapp, monkeypatch)
    ai, row = ctx.ai, win._ai_row
    win.pages["translate"].hint_progress._clock = lambda: 1000.0
    ai.start(plan())
    setup = 1_857_663_944
    for step, done, total in ((Step.DOWNLOAD, int(0.8 * GB), setup), (Step.INSTALL, 0, 0), (Step.START, 0, 0),
                              (Step.PULL, int(2.7 * GB), 8 * GB)):
        ai.emit(step=step, done_bytes=done, total_bytes=total, fraction=0.0)
        assert row.tx.text() == f"Yapay zekâ {_panel_percent(win)}" != "Yapay zekâ %0", step
    assert row.tx.text() == "Yapay zekâ %52"  # servisin oranı (1,9 + 2,7) / 9,9 GB = %46 olurdu


def test_ai_status_row_opens_translate_page(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win, ctx = _window(qapp, monkeypatch)
    _downloading(win, ctx.ai)
    assert win.stack.currentWidget() is win.pages["home"]
    QTest.mouseClick(win._ai_row, Qt.MouseButton.LeftButton)
    assert win.stack.currentWidget() is win.pages["translate"] and win.nav.currentRow() == 2
    # sayfada ilerleme davette (iş sayfa dışından başladı)
    page = win.pages["translate"]
    assert page.hint_progress.isVisibleTo(page)
    win.goto("home")
    QTest.keyClick(win._ai_row, Qt.Key.Key_Return)  # klavyeyle de (Tab ile ulaşılır)
    assert win.stack.currentWidget() is win.pages["translate"]
    assert win._ai_row.focusPolicy() == Qt.FocusPolicy.TabFocus


def test_ai_status_row_in_compact_sidebar_shows_icon_only(qapp, monkeypatch):
    win, ctx = _window(qapp, monkeypatch)
    _downloading(win, ctx.ai)
    row = win._ai_row
    win.resize(800, 640)
    assert _wait(qapp, lambda: win._compact)
    assert _vis(win, row) and _vis(win, row.ic) and not _vis(win, row.tx)
    assert row.toolTip() == "Yapay zekâ %33\nGemma 4 12B indiriliyor: 2,7 / 8,0 GB, kalan ~9 dk"
    # ikon, öteki durum satırlarının ikonlarıyla aynı eksende
    x = row.ic.mapTo(win.side, row.ic.rect().topLeft()).x()
    assert x == win._live.ic.mapTo(win.side, win._live.ic.rect().topLeft()).x()
    win.resize(1000, 760)
    assert _wait(qapp, lambda: not win._compact)
    assert _vis(win, row.tx) and row.toolTip() == "Gemma 4 12B indiriliyor: 2,7 / 8,0 GB, kalan ~9 dk"
    assert row.ic.mapTo(win.side, row.ic.rect().topLeft()).x() == \
        win._live.ic.mapTo(win.side, win._live.ic.rect().topLeft()).x()
    assert row.tx.mapTo(win.side, row.tx.rect().topLeft()).x() == \
        win._live.tx.mapTo(win.side, win._live.tx.rect().topLeft()).x()


# --- bildirimler ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("running,text", [(False, READY), (True, READY_LATER)], ids=["duruyor", "calisiyor"])
def test_ai_ready_notification(qapp, monkeypatch, running, text):
    """Başarı ctx.notify ile ("info"): çeviri çalışıyorsa yeniden başlatılmaz, bir sonraki Başlat'ta devreye girer."""
    win, ctx = _window(qapp, monkeypatch)
    ctx.pipeline.running = running
    ctx.ai.start(plan())
    ctx.ai.succeed(running)
    assert ctx.notes == [(text, "", "info")] and _toasts(win) == [(text, "info")]
    assert ctx.restarts == 0


def test_ai_failure_notification_and_none_on_cancel(qapp, monkeypatch):
    win, ctx = _window(qapp, monkeypatch)
    ai = ctx.ai
    ai.start(plan())
    ai.cancel()  # kullanıcı iptal etti: bildirim yok
    assert ctx.notes == [] and _toasts(win) == []
    ai.start(plan())
    ai.fail(NET_ERROR)
    assert ctx.notes == [(FAILED, NET_ERROR.splitlines()[0], "warn")] and _toasts(win) == [(FAILED, "warn")]


def test_model_only_download_notification(qapp, monkeypatch):
    """"Modeli indir" (ayar değişmez): sonuç servisin iletisiyle."""
    win, ctx = _window(qapp, monkeypatch)
    ctx.s.translate.engine = "ollama"
    ctx.ai.start(plan("gemma4:26b", apply=False))
    ctx.ai.succeed()
    assert ctx.notes == [("Gemma 4 26B indirildi.", "", "info")]


def test_pipeline_notice_shown_once(qapp, monkeypatch):
    """İşlem hattının notice'i AppContext'te notify'a bağlıdır; pencere yalnız notified'ı gösterir (iki kez değil)."""
    win, ctx = _window(qapp, monkeypatch)
    ctx.pipeline.notice.emit("Gemma 4 12B hazırlanamadı; hızlı çeviri kullanılıyor.\n"
                             "Gemma 4 12B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın.")
    assert _toasts(win) == [("Gemma 4 12B hazırlanamadı; hızlı çeviri kullanılıyor.", "warn")]


def test_notification_goes_to_tray_when_window_hidden(qapp, monkeypatch):
    win, ctx = _window(qapp, monkeypatch)
    shown: list = []
    monkeypatch.setattr(win.tray, "isVisible", lambda: True)
    monkeypatch.setattr(win.tray, "showMessage", lambda *a: shown.append(a[:2]))
    win.hide()
    ctx.notify("Yapay zekâ kurulamadı; ayrıntı Çeviri sayfasında\nDiskte yer kalmadı (C:).", "warn")
    assert _toasts(win) == []
    assert shown == [("Robz Vision", "Yapay zekâ kurulamadı; ayrıntı Çeviri sayfasında\nDiskte yer kalmadı (C:).")]


# --- güncelleyen kullanıcıya bir kezlik davet -------------------------------------------------------------------
def test_invite_notification_once(qapp, monkeypatch):
    """invite_pending ve kart uygunsa açılış (tur kararı) bittikten sonra bir kez bildirim; işaret kalkar ve
    kaydedilir."""
    s = Settings()
    s.ai.invite_pending = True
    win, ctx = _window(qapp, monkeypatch, s)
    assert _toasts(win) == [] and not win._startup_done  # açılış bitmeden gösterilmez
    assert _wait(qapp, lambda: win._advice_known)
    win._maybe_tour()  # açılıştan 700 ms sonra zamanlayıcı da çağırır
    assert _toasts(win) == [(INVITE, "info")] and ctx.notes == [(INVITE, "", "info")]
    assert s.ai.invite_pending is False and ctx.saves == 1
    win._maybe_tour()
    win.hide()
    win.show()  # tepsiden geri gelince yeniden gösterilmez
    assert len(ctx.notes) == 1 and ctx.saves == 1


@pytest.mark.parametrize("tier", ["none", "weak"])
def test_invite_closed_silently_for_unsuitable_card(qapp, monkeypatch, tier):
    s = Settings()
    s.ai.invite_pending = True
    win, ctx = _window(qapp, monkeypatch, s, adv=advice(tier, None))
    assert _wait(qapp, lambda: win._advice_known)
    win._maybe_tour()
    assert ctx.notes == [] and _toasts(win) == []
    assert s.ai.invite_pending is False and ctx.saves == 1


def test_invite_waits_when_hardware_unknown(qapp, monkeypatch):
    """Ekran kartı okunamadıysa davet sonraki açılışa kalır (bildirim yok, işaret durur)."""
    s = Settings()
    s.ai.invite_pending = True
    win, ctx = _window(qapp, monkeypatch, s, adv=None)
    assert _wait(qapp, lambda: win._advice_known)
    win._maybe_tour()
    assert ctx.notes == [] and s.ai.invite_pending is True and ctx.saves == 0


def test_no_invite_without_pending_flag(qapp, monkeypatch):
    win, ctx = _window(qapp, monkeypatch)
    assert _wait(qapp, lambda: win._advice_known)
    win._maybe_tour()
    assert ctx.notes == [] and ctx.saves == 0
