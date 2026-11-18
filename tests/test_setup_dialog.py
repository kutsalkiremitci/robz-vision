"""İlk açılış penceresi (gui/setup_dialog.py) ve yapay zekâ kurulumunun arayüz parçaları (gui/ai_setup_view.py).

Ekran gerektirmez (Qt 'offscreen'), ağ yok. Hızlı çeviri modeli indirmesi (translate.download_quickmt) sahte bir
işlevle değiştirilir: gerçek QThread'de çalışır ve test "kapıyı" açana kadar bekler. Yapay zekâ servisi yerine aynı
sinyalleri ve alanları taşıyan sahte bir QObject (FakeAi) kullanılır; değerleri test verir. Plan ve öneri nesneleri
SimpleNamespace'tir (ai_setup.Plan ve hardware.Advice'ın alanlarıyla). Hiçbir test süre ölçmez: olay döngüsü beklenen
durum gelene kadar döndürülür; hız ve geçen süre için panelin saati sahtedir.

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır (bkz.
test_profiles.py). Açılan pencereler her testten sonra kapatılıp Qt tarafında silinir; süren iş parçacıkları önce
bitirilir (bkz. test_translate_page.py)."""

from __future__ import annotations

import os
import threading
import time
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

import altyazitr.translate as T

Step = Enum("Step", "DOWNLOAD INSTALL START CHECK PULL TEST")
D, I, S, C, P = Step.DOWNLOAD, Step.INSTALL, Step.START, Step.CHECK, Step.PULL
GB = 1_000_000_000
SETUP = 1_857_663_944
M12, M4 = 8_000_000_000, 6_600_000_000
SIG_ERROR = "Ollama dosyasının imzası doğrulanamadı; kurulmadı.\nAyrıntı: İmzalayan: Evil Inc. (beklenen: Ollama Inc.)."
BLOCKED_ERROR = "Ollama kurulumu engellendi.\nAyrıntı: Antivirüs programınız kurulum dosyasını engelledi."
NET_ERROR = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.\nAyrıntı: ConnectError: getaddrinfo"
GPU_ERROR = ("Ollama ekran kartınızı kullanamıyor.\n"
             "Model indirilmedi. Ekran kartı sürücüsünü güncelleyip yeniden deneyin.")


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


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    """Arka plan işlerinin sinyalleri arayüz iş parçacığında işlensin diye olay döngüsünü döndürür."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


# --- sahte hızlı çeviri modeli (quickmt) ----------------------------------------------------------------------------
class FakeQuick:
    """translate.download_quickmt / quickmt_downloaded yerine. İndirme `gate` açılana dek sürer; `error` verilirse
    TranslatorError fırlatır. `ready`: model dosyaları tamam mı."""

    def __init__(self):
        self.ready = False
        self.gate = threading.Event()
        self.frac = 0.5
        self.error = ""
        self.calls = 0

    def downloaded(self) -> bool:
        return self.ready

    def download(self, status, progress=None):
        self.calls += 1
        if progress is not None:
            progress(self.frac)
        self.gate.wait(10)
        if self.error:
            raise T.TranslatorError(self.error)
        self.ready = True
        if progress is not None:
            progress(1.0)


_DIALOGS: list = []
_WIDGETS: list = []


@pytest.fixture
def quick(monkeypatch):
    fake = FakeQuick()
    monkeypatch.setattr(T, "download_quickmt", fake.download)
    monkeypatch.setattr(T, "quickmt_downloaded", fake.downloaded)
    yield fake
    fake.gate.set()  # süren sahte indirme bitsin


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from altyazitr.gui import ai_setup_view as v
    from altyazitr.gui import setup_dialog as sd

    for dlg in _DIALOGS:
        th = dlg._thread
        if th is not None:
            th.wait(5000)
    for th in sd._abandoned:
        th.wait(5000)
    sd._abandoned.clear()
    end = time.monotonic() + 5
    while v.surveys_running() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    for w in _DIALOGS + _WIDGETS:
        w.close()
        w.deleteLater()
    _DIALOGS.clear()
    _WIDGETS.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


# --- sahte yapay zekâ servisi -------------------------------------------------------------------------------------
def _fake_ai_class():
    from PySide6.QtCore import QObject, Signal

    from altyazitr.ai_setup import MANUAL_KINDS, NO_RETRY_KINDS

    class FakeAi(QObject):
        """AiSetup'ın sinyalleri ve alanları; işi yürütmez (değerleri test verir). advice() `advice_gate` açılana
        dek bekler (donanım okuması sürerken ilk ekranı denemek için). Hatada "Tekrar dene" ve "Elle kur" kararı
        servisteki gibi hatanın türünden (error_kind) çıkar."""

        changed = Signal()
        finished = Signal(bool, str)

        def __init__(self, advice=None, plans=None):
            super().__init__()
            self._advice = advice
            self._plans = dict(plans or {})
            self.advice_gate = threading.Event()
            self.advice_gate.set()
            self.state, self.step, self.substep = "idle", None, ""
            self.done_bytes = self.total_bytes = self.all_done_bytes = self.all_total_bytes = 0
            self.eta_s = None
            self.message = self.error = self.error_kind = self.model = self.note = ""
            self.speed = None
            self.active_plan = None
            self.can_cancel = True
            self.started: list = []
            self.cancels = self.retries = self.resumes = 0

        @property
        def can_retry(self) -> bool:
            return self.state == "failed" and self.error_kind not in NO_RETRY_KINDS

        @property
        def manual_install(self) -> bool:
            if self.state != "failed":
                return False
            return self.error_kind in MANUAL_KINDS or (not self.error_kind and self.step in (D, I))

        def advice(self):
            self.advice_gate.wait(10)
            return self._advice

        def plan(self, model=None, apply=True):
            p = self._plans[model]
            if isinstance(p, Exception):
                raise p
            return p

        def start(self, plan):
            self.started.append(plan)
            self.active_plan, self.model = plan, plan.model
            self.state, self.step, self.substep, self.message = "running", None, "", "Hazırlanıyor…"
            self.error = self.error_kind = ""
            self.done_bytes = self.total_bytes = 0
            self.changed.emit()

        def retry(self):
            self.retries += 1
            self.start(self.active_plan)

        def cancel(self):
            self.cancels += 1
            if self.state in ("running", "paused"):
                self.state, self.step, self.message = "idle", None, "İptal edildi."
                self.finished.emit(False, "İptal edildi.")
            else:
                self.state, self.error, self.error_kind, self.message = "idle", "", "", ""
            self.changed.emit()

        def force_resume(self):
            self.resumes += 1

        def emit(self, **fields):
            for k, val in fields.items():
                setattr(self, k, val)
            self.changed.emit()

        def fail(self, step, error, kind=""):
            """Servisin hatası: kind hatanın türü (ai_setup: signature, blocked, network, no_gpu…); substep korunur."""
            self.state, self.step, self.error, self.message = "failed", step, error, error.splitlines()[0]
            self.error_kind = kind
            self.finished.emit(False, error)
            self.changed.emit()

        def succeed(self, speed=None):
            self.state, self.step, self.message, self.speed = "done", None, "Yapay zekâ çevirisi hazır", speed
            self.finished.emit(True, self.message)
            self.changed.emit()

    return FakeAi


def advice(tier: str, model: str | None = "gemma4:12b", line: str = "NVIDIA GeForce RTX 4070 12 GB", warn: str = "",
           tip: str = ""):
    """hardware.Advice'ın alanları; sunma ve önceden seçme kademeden (tasarım belgesindeki tablo)."""
    offer = tier not in ("none", "weak")
    preselect = tier in ("mid", "good", "strong")
    return SimpleNamespace(tier=tier, offer=offer, preselect=preselect, model=model if offer else None,
                           gpu=None, line=line, warn=warn, tip=tip)


def plan(model: str = "gemma4:12b", steps=(D, I, S, C, P), download: int | None = None, free: int = 200 * GB,
         need: int = 15 * GB, blocker: str = ""):
    """ai_setup.Plan'ın alanları. download verilmezse adımlardan hesaplanır."""
    steps = list(steps)
    if download is None:
        download = (SETUP if D in steps else 0) + ({"gemma4:12b": M12, "gemma4:e4b": M4}[model] if P in steps else 0)
    return SimpleNamespace(model=model, steps=steps, download_bytes=download, disk_bytes=need if download else 0,
                           disk_path=Path("C:/Users/oyuncu/.ollama/models"), blocker=blocker, apply=True,
                           disk_free=free, ollama_version="", updating=False)


def _plans(**kw):
    return {"gemma4:12b": plan("gemma4:12b", **kw), "gemma4:e4b": plan("gemma4:e4b", **kw)}


def _dialog(qapp, ai=None, settings=None, wait_survey: bool = True):
    from altyazitr.gui.setup_dialog import FirstRunDialog

    dlg = FirstRunDialog(settings, ai)
    _DIALOGS.append(dlg)
    dlg.show()
    if ai is not None and wait_survey:
        assert _wait(qapp, lambda: dlg.confirm.has_plan("gemma4:12b") and dlg.confirm.has_plan("gemma4:e4b"))
    return dlg


def _ai(**kw):
    return _fake_ai_class()(**kw)


def _vis(parent, w) -> bool:
    return w.isVisibleTo(parent)


def _to_progress(qapp, dlg, quick):
    """Kart A → Devam → Kur; hızlı çeviri hazırsa iş hemen başlar."""
    dlg.card_ai.click()
    dlg.next_btn.click()
    assert dlg.stack.currentWidget() is dlg.confirm and dlg.confirm.ready()
    dlg.confirm.install_btn.click()
    assert dlg.stack.currentWidget() is dlg.progress


# --- Ekran 1: kademeye göre seçim -------------------------------------------------------------------------------------
@pytest.mark.parametrize("tier", ["mid", "good", "strong"])
def test_suitable_card_preselects_ai(qapp, quick, tier):
    from altyazitr.gui.ai_setup_view import CARD_AI_TIP, RECOMMENDED

    ai = _ai(advice=advice(tier), plans=_plans())
    dlg = _dialog(qapp, ai)
    assert dlg.card_ai.isEnabled() and dlg.card_ai.isChecked() and not dlg.card_quick.isChecked()
    assert dlg.card_ai.badge.text() == RECOMMENDED and _vis(dlg.card_ai, dlg.card_ai.badge)
    assert dlg.card_ai.info.text.text() == "NVIDIA GeForce RTX 4070 12 GB" and dlg.card_ai.info.kind == "ok"
    assert dlg.card_ai.toolTip().startswith(CARD_AI_TIP)
    assert dlg.card_ai.size_label.text() == "İndirme: yaklaşık 10 GB"  # Ekran 2'deki toplamla aynı
    assert dlg.next_btn.isEnabled()
    assert ai.started == []  # "Kur"a basılmadan tek bayt inmez


def test_edge_card_preselects_quick_with_warning(qapp, quick):
    ai = _ai(advice=advice("edge", "gemma4:e4b", "NVIDIA GeForce RTX 3060 6 GB",
                           warn="6 GB ekran kartı: yayın açıkken gecikebilir"), plans=_plans())
    dlg = _dialog(qapp, ai)
    assert dlg.card_quick.isChecked() and dlg.card_ai.isEnabled() and not dlg.card_ai.isChecked()
    assert _vis(dlg.card_ai, dlg.card_ai.warn) and dlg.card_ai.warn.kind == "warn"
    assert dlg.card_ai.warn.text.text() == "6 GB ekran kartı: yayın açıkken gecikebilir"
    assert not _vis(dlg.card_ai, dlg.card_ai.badge)  # önerilmeyen kartta "Önerilen" yok
    assert dlg.confirm.model == "gemma4:e4b"  # Ekran 2'de kademenin modeli seçili


@pytest.mark.parametrize("tier,line", [("none", "Uygun ekran kartı yok: Intel Iris Xe Graphics"),
                                       ("weak", "Uygun ekran kartı yok: NVIDIA GeForce GTX 970 4 GB")],
                         ids=["none", "weak"])
def test_unsuitable_card_is_disabled(qapp, quick, tier, line):
    from altyazitr.gui.ai_setup_view import NO_GPU_TIP

    ai = _ai(advice=advice(tier, line=line), plans=_plans())
    dlg = _dialog(qapp, ai)
    card = dlg.card_ai
    assert not card.isEnabled() and not card.isChecked() and dlg.card_quick.isChecked()
    assert card.info.text.text() == line and card.info.kind == "info"
    assert card.toolTip() == NO_GPU_TIP and not _vis(card, card.size_label)
    card.click()  # seçilemez
    assert not card.isChecked() and dlg.card_quick.isChecked()


def test_unknown_card_offered_but_not_preselected(qapp, quick):
    ai = _ai(advice=advice("unknown", "gemma4:e4b", line="Ekran kartı belirlenemedi"), plans=_plans())
    dlg = _dialog(qapp, ai)
    assert dlg.card_ai.isEnabled() and not dlg.card_ai.isChecked() and dlg.card_quick.isChecked()
    assert dlg.card_ai.info.kind == "info"


def test_inspecting_until_advice_and_user_pick_kept(qapp, quick):
    """Donanım okunurken "Ekran kartı inceleniyor…" yazar; kullanıcı o arada bir karta tıkladıysa seçimi ezilmez."""
    from altyazitr.gui.ai_setup_view import INSPECTING

    ai = _ai(advice=advice("good"), plans=_plans())
    ai.advice_gate.clear()
    dlg = _dialog(qapp, ai, wait_survey=False)
    assert dlg.card_ai.info.text.text() == INSPECTING
    assert not dlg.card_ai.isChecked() and not dlg.card_quick.isChecked() and not dlg.next_btn.isEnabled()
    dlg.card_quick.click()
    assert dlg.next_btn.isEnabled()
    ai.advice_gate.set()
    assert _wait(qapp, lambda: dlg.card_ai.info.text.text() != INSPECTING)
    assert dlg.card_quick.isChecked() and not dlg.card_ai.isChecked()  # öneri "Yapay zekâ" dese de


def test_pick_of_unavailable_card_is_overridden(qapp, quick):
    ai = _ai(advice=advice("none", line="Uygun ekran kartı yok"), plans=_plans())
    ai.advice_gate.clear()
    dlg = _dialog(qapp, ai, wait_survey=False)
    dlg.card_ai.click()
    assert dlg.card_ai.isChecked()
    ai.advice_gate.set()
    assert _wait(qapp, lambda: not dlg.card_ai.isEnabled())
    assert dlg.card_quick.isChecked() and not dlg.card_ai.isChecked()


def test_cards_are_accessible_radio_buttons(qapp, quick):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAccessible
    from PySide6.QtTest import QTest

    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    a, b = dlg.card_ai, dlg.card_quick
    assert a.accessibleName() == "Yapay zekâ ile" and b.accessibleName() == "Yalnız hızlı çeviri"
    assert "Konuşmayı takip eder" in a.accessibleDescription() and "İndirme" in a.accessibleDescription()
    iface = QAccessible.queryAccessibleInterface(a)
    assert iface is not None and iface.role() == QAccessible.Role.RadioButton
    assert a.focusPolicy() & Qt.FocusPolicy.TabFocus
    # oklar seçimi taşır (radyo düğmesi gibi), boşluk seçer
    dlg.activateWindow()
    a.setFocus()
    if not _wait(qapp, a.hasFocus, 1.0):
        pytest.skip("offscreen pencere odak alamadı")
    QTest.keyClick(a, Qt.Key.Key_Down)
    assert b.isChecked() and b.hasFocus()
    QTest.keyClick(b, Qt.Key.Key_Up)
    assert a.isChecked() and a.hasFocus()
    b.setFocus()
    QTest.keyClick(b, Qt.Key.Key_Space)
    assert b.isChecked()


def test_choice_is_recorded_in_settings(qapp, quick):
    settings = SimpleNamespace(ai=SimpleNamespace(choice=""), translate=SimpleNamespace(engine="quickmt"))
    quick.ready = True
    dlg = _dialog(qapp, _ai(advice=advice("good"), plans=_plans()), settings=settings)
    dlg.card_quick.click()
    dlg.next_btn.click()
    assert dlg.choice == "quickmt" and settings.ai.choice == "quickmt"


# --- Ekran 1 → kapanış ("Yalnız hızlı çeviri") ve Çıkış ------------------------------------------------------------
def test_quick_choice_closes_when_model_downloaded(qapp, quick):
    from PySide6.QtWidgets import QDialog

    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    assert quick.calls == 1  # hızlı çeviri modeli pencere açılır açılmaz iner
    dlg.card_quick.click()
    dlg.next_btn.click()
    # model inmedi: Ekran 3 yalnız hızlı çeviri satırıyla, "Şimdi başla" kapalı
    assert dlg.stack.currentWidget() is dlg.progress and dlg.isVisible()
    assert [r.d.key for r in dlg.progress.rows()] == ["QUICK"]
    assert not dlg.progress.start_btn.isEnabled() and _vis(dlg.progress, dlg.progress.wait_label)
    assert _vis(dlg.progress, dlg.progress.back_btn) and not _vis(dlg.progress, dlg.progress.cancel_btn)
    quick.gate.set()
    assert _wait(qapp, lambda: not dlg.isVisible())
    assert dlg.result() == QDialog.DialogCode.Accepted and ai.started == []


def test_quick_choice_closes_at_once_if_model_ready(qapp, quick):
    from PySide6.QtWidgets import QDialog

    dlg = _dialog(qapp, _ai(advice=advice("edge"), plans=_plans()))
    quick.gate.set()
    assert _wait(qapp, lambda: dlg.quick_footer.state == "done")
    assert dlg.quick_footer.text.text() == "Hızlı çeviri modeli hazır" and dlg.isVisible()
    dlg.next_btn.click()  # kademe "edge": hızlı çeviri seçili
    assert not dlg.isVisible() and dlg.result() == QDialog.DialogCode.Accepted


def test_quit_rejects_and_keeps_download_thread(qapp, quick):
    from PySide6.QtWidgets import QDialog

    from altyazitr.gui import setup_dialog as sd

    dlg = _dialog(qapp, _ai(advice=advice("good"), plans=_plans()))
    assert dlg._thread is not None and dlg._thread.isRunning()
    dlg.quit_btn.click()
    assert not dlg.isVisible() and dlg.result() == QDialog.DialogCode.Rejected
    assert sd.download_running()  # app.py bunu görüp süreci os._exit ile kapatır
    quick.gate.set()
    assert _wait(qapp, lambda: not sd.download_running())


def test_quickmt_failure_in_footer_and_retry(qapp, quick):
    quick.error = "Çeviri modeli indirilemedi. İnternet bağlantınızı kontrol edin.\nAyrıntı: ConnectError"
    dlg = _dialog(qapp, _ai(advice=advice("good"), plans=_plans()))
    quick.gate.set()
    foot = dlg.quick_footer
    assert _wait(qapp, lambda: foot.state == "failed")
    assert foot.text.text() == "Hızlı çeviri modeli inemedi" and "ConnectError" in foot.toolTip()
    assert _vis(foot, foot.retry_btn) and not _vis(foot, foot.bar)
    quick.error = ""
    foot.retry_btn.click()
    assert _wait(qapp, lambda: foot.state == "done") and quick.calls == 2


# --- Ekran 2: onay -----------------------------------------------------------------------------------------------
def test_confirm_shows_plan_sizes(qapp, quick):
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    dlg.next_btn.click()
    c = dlg.confirm
    assert dlg.stack.currentWidget() is c
    assert c.ollama_row["size"].text() == "1,9 GB" and c.ollama_row["desc"].text().startswith("Yapay zekâyı")
    assert c.model_row["title"].text() == "Gemma 4 12B" and c.model_row["size"].text() == "8,0 GB"
    assert c.total.text() == "~10 GB"
    assert c.disk.text.text() == "C: sürücüsünde 200 GB boş (~15 GB gerekli)" and c.disk.kind == "ok"
    assert _vis(c, c.info) and not _vis(c, c.blocker)
    assert c.ready() and c.install_btn.text() == "Kur"
    # model segmenti: boyutlar o modelin planından
    c.models.group.button(1).click()
    assert c.model == "gemma4:e4b" and c.model_row["title"].text() == "Gemma 4 E4B"
    assert c.model_row["size"].text() == "6,6 GB" and c.total.text() == "~8 GB"
    assert "6,6 GB" in c.model_row["size"].toolTip() or "hafif" in c.model_row["size"].toolTip()
    assert ai.started == []


def test_confirm_disk_short_disables_install(qapp, quick):
    blocker = "C: sürücüsünde yer yetmiyor: 9 GB boş, ~15 GB gerekli"
    ai = _ai(advice=advice("good"), plans=_plans(free=9 * GB, blocker=blocker))
    dlg = _dialog(qapp, ai)
    dlg.next_btn.click()
    c = dlg.confirm
    assert not c.ready() and not c.install_btn.isEnabled()
    assert c.disk.kind == "warn" and c.disk.text.text() == blocker
    assert not _vis(c, c.blocker)  # aynı engel iki kez yazılmaz
    c.install_btn.click()
    assert ai.started == []


def test_confirm_blocker_disables_install(qapp, quick):
    ai = _ai(advice=advice("good"), plans=_plans(blocker="11434 portunu başka bir program kullanıyor."))
    dlg = _dialog(qapp, ai)
    dlg.next_btn.click()
    c = dlg.confirm
    assert _vis(c, c.blocker) and c.blocker.text.text() == "11434 portunu başka bir program kullanıyor."
    assert not c.install_btn.isEnabled()


def test_confirm_installed_ollama_only_model(qapp, quick):
    ai = _ai(advice=advice("good"), plans=_plans(steps=(C, P)))
    dlg = _dialog(qapp, ai)
    assert dlg.card_ai.size_label.text() == "İndirme: 8 GB (Ollama kurulu)"
    dlg.next_btn.click()
    c = dlg.confirm
    assert c.ollama_row["size"].text() == "Kurulu" and not _vis(c, c.info) and c.total.text() == "~8 GB"


def test_nothing_to_download_skips_confirm(qapp, quick):
    quick.ready = True
    installed = {"gemma4:12b": plan("gemma4:12b", steps=(S,), download=0),
                 "gemma4:e4b": plan("gemma4:e4b", steps=(S,), download=0)}
    ai = _ai(advice=advice("good"), plans=installed)
    dlg = _dialog(qapp, ai)
    assert dlg.card_ai.size_label.text() == "Kurulu, indirme yok"
    dlg.next_btn.click()
    assert dlg.stack.currentWidget() is dlg.progress  # Ekran 2 atlandı
    assert ai.started == [installed["gemma4:12b"]] and dlg.choice == "ai"


def test_esc_on_confirm_goes_back(qapp, quick):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dlg = _dialog(qapp, _ai(advice=advice("good"), plans=_plans()))
    dlg.next_btn.click()
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.isVisible() and dlg.stack.currentWidget() is dlg.choice_page


# --- Ekran 3: ilerleme ---------------------------------------------------------------------------------------------
def test_start_now_enabled_after_quickmt(qapp, quick):
    """Kur'a hızlı çeviri inerken basıldı: yapay zekâ bekler (bant bölünmesin), "Şimdi başla" model inince açılır."""
    from PySide6.QtWidgets import QDialog

    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    assert pr.queued and ai.started == [] and pr.phase() == "queued"
    assert not pr.start_btn.isEnabled() and _vis(pr, pr.wait_label)
    assert all(r.state == "pending" for r in pr.rows() if r.d.key != "QUICK")
    assert pr.rows()[0].state == "active" and pr.rows()[0].detail == "202 / 404 MB"
    quick.gate.set()
    assert _wait(qapp, lambda: pr.start_btn.isEnabled())
    assert ai.started == [dlg.confirm.plan] and not pr.queued
    assert pr.start_btn.text() == "Şimdi başla" and not _vis(pr, pr.wait_label)
    pr.start_btn.click()
    assert not dlg.isVisible() and dlg.result() == QDialog.DialogCode.Accepted
    assert ai.state == "running"  # yapay zekâ arka planda sürer


def test_progress_rows_texts_and_states(qapp, quick):
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    assert ai.started and not pr.queued
    ai.emit(step=D, done_bytes=int(0.8 * GB), total_bytes=SETUP, message="Ollama indiriliyor")
    rows = pr.rows()
    assert [r.title for r in rows] == ["Hızlı çeviri modeli", "Ollama indiriliyor", "İmza ve bütünlük doğrulanıyor",
                                       "Ollama kuruluyor (1–2 dk)", "Ollama başlatılıyor", "Ekran kartı denetleniyor",
                                       "Gemma 4 12B indiriliyor", "Hazır"]
    assert [r.state for r in rows] == ["done", "active"] + ["pending"] * 6
    assert rows[0].detail == "404 MB" and rows[1].detail == "0,8 / 1,9 GB" and rows[6].detail == "8,0 GB"
    w = rows[1].widget
    assert w.label.text() == "Ollama indiriliyor" and w.detail.text() == "0,8 / 1,9 GB" and w.state == "active"
    assert "sürüyor" in w.accessibleName() and "bekliyor" in rows[2].widget.accessibleName()
    # bitince satır geçmiş zamana döner
    ai.emit(step=P, done_bytes=int(2.7 * GB), total_bytes=M12, message="Gemma 4 12B indiriliyor")
    rows = pr.rows()
    assert [r.state for r in rows] == ["done"] * 6 + ["active", "pending"]
    assert rows[1].title == "Ollama indirildi" and rows[3].title == "Ollama kuruldu"
    assert rows[6].detail == "2,7 / 8,0 GB"
    ai.succeed()
    assert all(r.state == "done" for r in pr.rows()) and pr.rows()[-1].title == "Hazır"


def test_progress_speed_eta_and_percent(qapp, quick):
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    clock = [100.0]
    dlg.progress._clock = lambda: clock[0]
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=D, done_bytes=int(0.680 * GB), total_bytes=SETUP)
    clock[0] = 110.0
    ai.emit(done_bytes=int(0.804 * GB))
    assert pr.speed.text() == "12,4 MB/sn" and _vis(pr, pr.speed)
    assert pr.eta.text().startswith("Kalan ~") and pr.eta.text().endswith(" dk")
    assert pr.pct.text().startswith("%") and 0 < pr.bar.value() < 1000
    # ağ koptu: hız gösterilmez, satırın altında bekleme notu
    ai.emit(message="İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.")
    assert pr.speed.text() == "" and not _vis(pr, pr.speed)
    assert pr.rows()[1].widget.note.text().startswith("İnternet bağlantısı yok")


def test_byteless_steps_show_elapsed_time_and_move(qapp, quick):
    """Kurulum, başlatma ve denetim adımında satır donmaz: geçen süre ilerler, toplam çubuk yürür; İptal kapalı."""
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    clock = [500.0]
    dlg.progress._clock = lambda: clock[0]
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=I, message="Ollama kuruluyor (1-2 dk)", can_cancel=False)
    row = next(r for r in pr.rows() if r.d.key == "INSTALL")
    assert row.state == "active" and row.detail == "0:00"
    assert next(r for r in pr.rows() if r.d.key == "VERIFY").state == "done"
    before = pr.bar.value()
    clock[0] += 42
    pr._refresh()  # saniyelik sayaç (QTimer) yerine
    assert row.detail == "0:42" and row.widget.detail.text() == "0:42"
    assert pr.bar.value() > before  # baytsız adımda da çubuk yürür
    assert pr._ticker.isActive()
    assert not pr.cancel_btn.isEnabled() and "iptal edilemez" in pr.cancel_btn.toolTip()
    ai.emit(step=S, message="Ollama başlatılıyor", can_cancel=True)
    start = next(r for r in pr.rows() if r.d.key == "START")
    assert start.state == "active" and start.detail == "0:00" and pr.cancel_btn.isEnabled()
    clock[0] += 7
    pr._refresh()
    assert start.detail == "0:07" and row.state == "done"


def test_verify_row_follows_service_message(qapp, quick):
    """Servis kurulum adımında doğrulamayı "İmza…" iletisiyle bildirirse doğrulama satırı ayrıca ilerler."""
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=I, message="İmza ve bütünlük doğrulanıyor")
    states = {r.d.key: r.state for r in pr.rows()}
    assert states["VERIFY"] == "active" and states["INSTALL"] == "pending" and states["DOWNLOAD"] == "done"
    ai.emit(message="Ollama kuruluyor (1-2 dk)")
    states = {r.d.key: r.state for r in pr.rows()}
    assert states["VERIFY"] == "done" and states["INSTALL"] == "active"


def test_pause_is_visible_and_can_be_overridden(qapp, quick):
    from altyazitr.gui.ai_setup_view import PAUSED

    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=P, done_bytes=int(2.7 * GB), total_bytes=M12)
    assert pr.note.text.text() == "Çeviri çalışırken indirme duraklar." and not _vis(pr, pr.resume_btn)
    ai.emit(state="paused", message=PAUSED)
    assert pr.phase() == "paused" and pr.note.text.text() == PAUSED
    assert _vis(pr, pr.resume_btn) and pr.resume_btn.text() == "Yine de sürdür"
    assert next(r for r in pr.rows() if r.d.key == "PULL").state == "paused" and pr.eta.text() == ""
    pr.resume_btn.click()
    assert ai.resumes == 1


@pytest.mark.parametrize("step,substep,error,kind,row,retry,manual", [
    (I, "verify", SIG_ERROR, "signature", "VERIFY", False, True),
    (I, "", SIG_ERROR, "signature", "VERIFY", False, True),
    (I, "install", BLOCKED_ERROR, "blocked", "INSTALL", True, True),
    (D, "", NET_ERROR, "network", "DOWNLOAD", True, False),
    (C, "", GPU_ERROR, "no_gpu", "CHECK", True, False),
], ids=["imza", "imza-altadimsiz", "engel", "ag", "ekran-karti"])
def test_failure_row_retry_and_manual_link(qapp, quick, step, substep, error, kind, row, retry, manual):
    """Hatalı satır kırmızı, altında kısa metin (ayrıntı ipucunda). "Hızlı çeviride kal" her hatada; "Tekrar dene"
    yalnız servis yeniden denemeyi anlamlı bulursa (ai.can_retry: imza hatasında değil); "Ollama'yı elle kur" yalnız
    servis elle kurmayı önerirse (ai.manual_install: imza ve engel hatasında). Kurulum adımındaki hatanın satırı
    (doğrulama mı kurulum mu) servisin alt adımından; bildirilmediyse hata metninden."""
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=step, substep=substep)
    ai.fail(step, error, kind)
    assert dlg.stack.currentWidget() is pr and dlg.isVisible()  # hata Ekran 3'te kalır
    failed = [r for r in pr.rows() if r.state == "failed"]
    assert [r.d.key for r in failed] == [row]
    note = failed[0].widget.note
    assert note.isVisibleTo(pr) and note.text().startswith(error.splitlines()[0])
    assert "Ayrıntı" not in note.text() and note.toolTip() == error
    if step is C:  # yönlendirme satırı da görünür
        assert "sürücüsünü güncelleyip" in note.text()
    assert pr.header.title.text() == "Yapay zekâ kurulamadı"
    assert _vis(pr, pr.stay_btn) and not _vis(pr, pr.start_btn) and not _vis(pr, pr.cancel_btn)
    assert _vis(pr, pr.retry_btn) is retry
    assert _vis(pr, pr.link_btn) is manual
    if manual:
        assert pr.link_btn.accessibleDescription() == "https://ollama.com/download"
    if not retry:
        return
    assert pr.retry_btn.text() == "Tekrar dene" and pr.retry_btn.isDefault()
    pr.retry_btn.click()
    assert ai.retries == 1 and ai.state == "running" and pr.phase() == "running"
    assert not any(r.state == "failed" for r in pr.rows()) and not _vis(pr, pr.link_btn)


def test_stay_quick_after_failure_closes(qapp, quick):
    from PySide6.QtWidgets import QDialog

    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    ai.fail(C, GPU_ERROR)
    dlg.progress.stay_btn.click()
    assert ai.cancels == 1  # servis hatalı işi unutur
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.choice == "quickmt"


def test_manual_link_absent_in_normal_flow(qapp, quick):
    """Normal akışta hiçbir yerde ollama.com'a yönlendirme yok."""
    from PySide6.QtWidgets import QPushButton

    def links() -> list:
        return [b for b in dlg.findChildren(QPushButton)
                if "ollama.com" in b.accessibleDescription() and b.isVisibleTo(dlg)]

    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    assert links() == []
    dlg.next_btn.click()
    assert links() == []
    dlg.confirm.install_btn.click()
    for fields in ({"step": D, "done_bytes": GB, "total_bytes": SETUP}, {"step": I}, {"state": "paused"},
                   {"state": "running", "step": P}):
        ai.emit(**fields)
        assert links() == []
    ai.fail(D, NET_ERROR, "network")
    assert links() == []
    ai.fail(I, SIG_ERROR, "signature")
    assert len(links()) == 1


def test_cancel_ai_keeps_quick(qapp, quick):
    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    ai.emit(step=P, done_bytes=GB, total_bytes=M12)
    pr.cancel_btn.click()
    assert ai.cancels == 1 and pr.phase() == "cancelled" and dlg.choice == "quickmt"
    assert dlg.isVisible() and dlg.stack.currentWidget() is pr
    assert not _vis(pr, pr.cancel_btn) and pr.start_btn.text() == "Başla" and pr.start_btn.isEnabled()
    states = {r.d.key: r.state for r in pr.rows()}
    assert states["QUICK"] == "done" and states["PULL"] == "cancelled" and states["DOWNLOAD"] == "done"


def test_cancel_while_queued_never_starts(qapp, quick):
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    pr = dlg.progress
    assert pr.queued
    pr.cancel_btn.click()
    quick.gate.set()
    assert _wait(qapp, lambda: pr.start_btn.isEnabled())
    assert ai.started == [] and ai.cancels == 0 and pr.phase() == "cancelled"


def test_closing_after_ai_started_opens_app(qapp, quick):
    """X/Esc, yapay zekâ kurulumu başladıktan sonra ve hızlı çeviri hazırken: pencere kapanır, ana pencere açılır."""
    from PySide6.QtWidgets import QDialog

    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    dlg.reject()
    assert dlg.result() == QDialog.DialogCode.Accepted and ai.state == "running"


# --- Ekran 4: sonuç ---------------------------------------------------------------------------------------------------
def test_success_shows_result_and_starts(qapp, quick):
    from PySide6.QtWidgets import QDialog

    quick.ready = True
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai)
    _to_progress(qapp, dlg, quick)
    ai.succeed(speed=SimpleNamespace(median_ms=410, p90_ms=510, gpu_share=1.0))
    assert dlg.stack.currentWidget() is dlg.result_page
    assert dlg.result_header.title.text() == "Yapay zekâ hazır"
    assert dlg.result_header.sub.text() == "Satır başına ~0,4 sn. Hızlı çeviri yedekte."
    assert not _vis(dlg.result_page, dlg.res_secondary) and dlg.res_primary.text() == "Başla"
    dlg.res_primary.click()
    assert dlg.result() == QDialog.DialogCode.Accepted


def test_slow_result_offers_light_model(qapp, quick):
    quick.ready = True
    settings = SimpleNamespace(ai=SimpleNamespace(choice=""), translate=SimpleNamespace(engine="ollama"))
    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = _dialog(qapp, ai, settings=settings)
    _to_progress(qapp, dlg, quick)
    ai.succeed(speed=SimpleNamespace(median_ms=2600, p90_ms=3100, gpu_share=0.6))
    assert dlg.result_header.title.text() == "Yapay zekâ bu bilgisayarda yavaş"
    assert dlg.result_header.sub.text() == "Satır başına ~2,6 sn: ekran kartının belleği yetmiyor."
    assert dlg.res_primary.text() == "Hafif modeli kur (6,6 GB)" and _vis(dlg.result_page, dlg.res_secondary)
    dlg.res_primary.click()  # onay ekranı, hafif model; plan yeniden okunur (Ollama artık kurulu)
    assert dlg.stack.currentWidget() is dlg.confirm and dlg.confirm.model == "gemma4:e4b"
    assert _wait(qapp, lambda: dlg.confirm.ready())
    dlg._show_result()  # aynı sonuç ekranına dönüp öbür yol
    dlg.res_secondary.click()  # "Hızlı çeviride kal": kurulan yavaş yapay zekâ motor olmaz
    assert settings.translate.engine == "quickmt" and dlg.choice == "quickmt"


# --- Çeviri sayfası için kısa biçim ve onay penceresi --------------------------------------------------------------
def _compact(qapp, ai):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from altyazitr.gui.ai_setup_view import AiProgressPanel

    host = QWidget()
    lay = QVBoxLayout(host)
    panel = AiProgressPanel(ai, compact=True)
    lay.addWidget(panel)
    host.resize(600, 160)
    host.show()
    _WIDGETS.append(host)
    return panel


def test_compact_panel_follows_service(qapp):
    from altyazitr.gui.ai_setup_view import PAUSED

    ai = _ai(advice=advice("good"), plans=_plans())
    pnl = _compact(qapp, ai)
    assert pnl.phase() == "idle" and not _vis(pnl, pnl.line_text) and not _vis(pnl, pnl.bar)
    ai.start(plan())  # iş başka yerden başladı (ilk açılış ya da yarım işin sürmesi): satırlar servisin planından
    ai.emit(step=P, done_bytes=int(2.7 * GB), total_bytes=M12)
    assert pnl.line_text.text() == "Gemma 4 12B indiriliyor" and pnl.line_detail.text() == "2,7 / 8,0 GB"
    assert _vis(pnl, pnl.bar) and _vis(pnl, pnl.cancel_btn) and not _vis(pnl, pnl.retry_btn)
    assert [r.d.key for r in pnl.rows()] == ["DOWNLOAD", "VERIFY", "INSTALL", "START", "CHECK", "PULL", "READY"]
    assert not _vis(pnl, pnl.steps_box)
    pnl.steps_btn.setChecked(True)  # adımlar açılır
    assert _vis(pnl, pnl.steps_box) and pnl.rows()[0].widget.isVisibleTo(pnl)
    ai.emit(step=I, done_bytes=0, total_bytes=0, can_cancel=False)
    assert pnl.line_text.text() == "Ollama kuruluyor (1–2 dk)" and pnl.line_detail.text() == "0:00"
    assert not pnl.cancel_btn.isEnabled()
    ai.emit(step=P, state="paused", can_cancel=True, message=PAUSED)
    assert pnl.line_text.text() == PAUSED and _vis(pnl, pnl.resume_btn)
    pnl.resume_btn.click()
    assert ai.resumes == 1
    ai.emit(state="running")
    pnl.cancel_btn.click()
    assert ai.cancels == 1 and pnl.line_text.text() == "İptal edildi."


def test_compact_panel_failure(qapp):
    """Kısa biçimde de: "Hızlı çeviride kal" her hatada, "Tekrar dene" yalnız ai.can_retry, "Ollama'yı elle kur"
    yalnız ai.manual_install ise."""
    ai = _ai(advice=advice("good"), plans=_plans())
    pnl = _compact(qapp, ai)
    ai.start(plan())
    ai.emit(step=I, substep="install")
    ai.fail(I, BLOCKED_ERROR, "blocked")
    assert pnl.line_text.text() == "Ollama kurulumu engellendi." and pnl.line_text.toolTip() == BLOCKED_ERROR
    assert _vis(pnl, pnl.retry_btn) and _vis(pnl, pnl.stay_btn) and _vis(pnl, pnl.link_btn)
    assert not _vis(pnl, pnl.bar) and not _vis(pnl, pnl.cancel_btn)
    pnl.retry_btn.click()
    assert ai.retries == 1 and pnl.phase() == "running" and not _vis(pnl, pnl.link_btn)
    ai.fail(D, NET_ERROR, "network")
    assert _vis(pnl, pnl.retry_btn) and _vis(pnl, pnl.stay_btn) and not _vis(pnl, pnl.link_btn)
    ai.emit(step=I, substep="verify")
    ai.fail(I, SIG_ERROR, "signature")  # imza: yeniden denemek düzeltmez, elle kurmak çözer
    assert not _vis(pnl, pnl.retry_btn) and _vis(pnl, pnl.stay_btn) and _vis(pnl, pnl.link_btn)
    pnl.steps_btn.setChecked(True)
    assert next(r for r in pnl.rows() if r.state == "failed").d.key == "VERIFY"
    pnl.stay_btn.click()  # "Hızlı çeviride kal": hatalı iş unutulur, kısa biçim boşalır
    assert ai.cancels == 1 and pnl.phase() == "idle" and not _vis(pnl, pnl.line_text)
    assert not _vis(pnl, pnl.retry_btn) and not _vis(pnl, pnl.stay_btn) and not _vis(pnl, pnl.link_btn)


def test_confirm_dialog_starts_on_install(qapp):
    from altyazitr.gui.ai_setup_view import AiConfirmDialog, confirm_ai_setup

    ai = _ai(advice=advice("good"), plans=_plans())
    dlg = AiConfirmDialog(ai)
    _WIDGETS.append(dlg)
    dlg.show()
    assert _wait(qapp, lambda: dlg.panel.ready())
    assert dlg.panel.back_btn.text() == "Vazgeç" and ai.started == []
    dlg.panel.install_btn.click()
    assert ai.started == [dlg.panel.plan] and dlg.started is dlg.panel.plan and not dlg.isVisible()
    # iş sürerken ikinci bir onay penceresi açılmaz
    assert confirm_ai_setup(None, ai) is False


def test_confirm_dialog_skips_when_everything_installed(qapp):
    installed = {"gemma4:12b": plan("gemma4:12b", steps=(), download=0),
                 "gemma4:e4b": plan("gemma4:e4b", steps=(), download=0)}
    from altyazitr.gui.ai_setup_view import AiConfirmDialog

    ai = _ai(advice=advice("good"), plans=installed)
    dlg = AiConfirmDialog(ai)
    _WIDGETS.append(dlg)
    dlg.show()
    assert _wait(qapp, lambda: not dlg.isVisible())
    assert ai.started == [installed["gemma4:12b"]]  # yalnız ayar yapılır


# --- yapay zekâsız (eski) davranış ---------------------------------------------------------------------------------
def test_run_first_setup_returns_at_once_when_model_present(qapp, quick):
    from altyazitr.gui.setup_dialog import ensure_translation_model, run_first_setup

    quick.ready = True
    assert run_first_setup(None) is True and run_first_setup(None, _ai()) is True
    assert ensure_translation_model() is True and quick.calls == 0  # pencere açılmaz, hiçbir şey inmez


def test_without_ai_only_quickmt(qapp, quick):
    """ai=None: bugünkü pencere; model inince kendiliğinden kapanır ve True döner."""
    from PySide6.QtCore import QTimer

    from altyazitr.gui.setup_dialog import run_first_setup

    quick.gate.set()
    guard = QTimer(singleShot=True, interval=8000)  # güvenlik: takılırsa pencereyi kapatır (test yine başarısız)
    guard.timeout.connect(lambda: [w.reject() for w in qapp.topLevelWidgets() if w.isVisible()])
    guard.start()
    try:
        assert run_first_setup(None) is True and quick.calls == 1
    finally:
        guard.stop()


def test_without_ai_failure_retry_and_quit(qapp, quick):
    from PySide6.QtWidgets import QDialog

    from altyazitr.gui import setup_dialog as sd

    quick.error = "Çeviri modeli indirilemedi. İnternet bağlantınızı kontrol edin.\nAyrıntı: ConnectError"
    dlg = _dialog(qapp, None)
    assert not hasattr(dlg, "choice_page") and dlg.header.title.text() == "Ücretsiz çeviri modeli indiriliyor"
    assert dlg.amount.text() == "202 / 404 MB" or _wait(qapp, lambda: dlg.amount.text() == "202 / 404 MB")
    quick.gate.set()
    assert _wait(qapp, lambda: dlg.retry_btn.isVisibleTo(dlg))
    assert dlg.header.title.text() == "Kurulum tamamlanamadı"
    assert dlg.error.text() == "Çeviri modeli indirilemedi. İnternet bağlantınızı kontrol edin."
    assert "ConnectError" in dlg.error.toolTip()
    quick.gate.clear()
    quick.error = ""
    dlg.retry_btn.click()
    assert _wait(qapp, lambda: quick.calls == 2) and not dlg.retry_btn.isVisibleTo(dlg)
    dlg.quit_btn.click()  # indirme sürerken Çıkış: iş parçacığı bırakılır, app.py süreci kapatır
    assert dlg.result() == QDialog.DialogCode.Rejected and sd.download_running()
    quick.gate.set()
    assert _wait(qapp, lambda: not sd.download_running())


# --- biçim yardımcıları ------------------------------------------------------------------------------------------
def test_formatters():
    from altyazitr.gui import ai_setup_view as v

    assert v.fmt_size(SETUP) == "1,9 GB" and v.fmt_size(404e6) == "404 MB" and v.fmt_size(9.86e9, True) == "10 GB"
    assert v.fmt_pair(0.8e9, SETUP) == "0,8 / 1,9 GB" and v.fmt_pair(212e6, 404e6) == "212 / 404 MB"
    assert v.fmt_speed(12.4e6) == "12,4 MB/sn" and v.fmt_speed(850e3) == "850 KB/sn" and v.fmt_speed(None) == ""
    assert v.fmt_eta(9 * 60) == "Kalan ~9 dk" and v.fmt_eta(30) == "Kalan 1 dk'dan az" and v.fmt_eta(None) == ""
    assert v.fmt_eta(3900) == "Kalan ~1 sa 5 dk"
    assert v.fmt_elapsed(42) == "0:42" and v.fmt_elapsed(725) == "12:05" and v.fmt_elapsed(3729) == "1:02:09"
    # "Tekrar dene" ve "Elle kur" kararı servisin (alan yoksa: yeniden denenir, elle kurma önerilmez)
    assert v.can_retry(SimpleNamespace()) and not v.can_retry(SimpleNamespace(can_retry=False))
    assert v.manual_install(SimpleNamespace(manual_install=True)) and not v.manual_install(SimpleNamespace())
    assert v.MODEL_BYTES[v.LIGHT_MODEL] == 6_600_000_000 and v.MODEL_BYTES[v.HEAVY_MODEL] == 8_000_000_000
    assert v.error_note(GPU_ERROR).endswith("güncelleyip yeniden deneyin.")
    assert v.error_note(NET_ERROR) == "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer."
    keys = [d.key for d in v.row_defs(["DOWNLOAD", "INSTALL", "START", "CHECK", "PULL"], "gemma4:12b")]
    assert keys == ["DOWNLOAD", "VERIFY", "INSTALL", "START", "CHECK", "PULL", "READY"]
    assert v.row_defs([]) == []
    out = v.describe_result(False, GPU_ERROR)
    assert out.kind == "failed" and out.title == "Ollama ekran kartınızı kullanamıyor"
    assert v.describe_result(True, "", SimpleNamespace(median_ms=410)).text.startswith("Satır başına ~0,4 sn")
    assert v.step_name(Step.PULL) == "PULL" and v.step_name(None) == "" and v.step_name("install") == "INSTALL"
