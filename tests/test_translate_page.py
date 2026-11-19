"""Çeviri sayfası: oyun profili, bağlam, karma mod, yapay zekâ daveti ve "Önerilen kurulum" (ekran gerektirmez; Qt
'offscreen').

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır
(bkz. test_profiles.py). Açılan sayfalar her testten sonra kapatılıp Qt tarafında silinir (bkz.
test_style_profiles.py). Ağ yok: yapay zekâ kurulum servisi yerine aynı sinyalleri ve alanları taşıyan sahte bir
QObject (FakeAi; değerleri test verir) kullanılır, onay penceresi (confirm_ai_setup) sahtesiyle değiştirilir. Uzak
Ollama sunucusu yolu için ollama_list ve ollama_pull sahtedir. Donanım önerisi, servisin planı ve sahte indirme gerçek
arka plan iş parçacıklarında çalışır, sonuçları sayfaya sinyalle döner."""

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from altyazitr.ai_setup import Step
from altyazitr.ai_setup import model_title as catalog_title
from altyazitr.config import Settings
from altyazitr.translate import RECOMMENDED_OLLAMA_MODEL, TranslatorError

LIGHT = "gemma4:e4b"
LOCAL = "http://127.0.0.1:11434"
REMOTE = "http://192.168.1.20:11434"
GB = 1_000_000_000
SETUP = 1_857_663_944
NET_ERROR = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.\nAyrıntı: ConnectError: getaddrinfo"


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


# Açık kalan sayfa, süreç kapanırken QApplication'dan sonra silinir ve süreci çökertir (bkz.
# test_style_profiles.py). Süren sahte indirme ve donanım okuması da sayfayla birlikte silinmeden önce bitirilir.
_PAGES: list = []


@pytest.fixture(autouse=True)
def _close_pages():
    yield
    if not _PAGES:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from altyazitr.gui import ai_setup_view as v

    for pg in _PAGES:
        pg.ai.gate.set()
    end = time.monotonic() + 5
    while v.surveys_running() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    for pg in _PAGES:
        for w in pg._workers:
            w.cancel.set()
            w.wait(5000)
        pg.close()
        pg.deleteLater()
    _PAGES.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


class FakeOllama:
    """ollama_list / ollama_pull yerine geçer. names: yüklü modeller (None: Ollama'ya ulaşılamıyor).
    `gate` kapatılırsa indirme, test izin verene ya da kullanıcı iptal edene kadar sürer."""

    def __init__(self, names):
        self.names = None if names is None else list(names)
        self.lists: list[str] = []
        self.pulls: list[tuple[str, str]] = []
        self.gate = threading.Event()
        self.gate.set()

    def list(self, url):
        self.lists.append(url)
        return None if self.names is None else list(self.names)

    def pull(self, url, model, progress, cancel):
        self.pulls.append((url, model))
        progress("pulling manifest", -1.0)
        progress("pulling 6a0746a1ec1a — 4.00 / 8.00 GB", 0.5)
        while not self.gate.wait(0.01):
            if cancel.is_set():
                raise TranslatorError("İndirme iptal edildi.")
        progress("success", 1.0)
        self.names.append(model)


# --- sahte yapay zekâ servisi ---------------------------------------------------------------------
def advice(tier: str = "good", model: str | None = RECOMMENDED_OLLAMA_MODEL, warn: str = "",
           tip: str = "Gemma 4 12B bu karta sığar."):
    """hardware.Advice'ın alanları; sunma kademeden (tasarım belgesindeki tablo: none/weak sunulmaz)."""
    offer = tier not in ("none", "weak")
    return SimpleNamespace(tier=tier, offer=offer, preselect=tier in ("mid", "good", "strong"),
                           model=model if offer else None, gpu=None, line="NVIDIA GeForce RTX 4070 12 GB", warn=warn,
                           tip=tip)


GOOD = advice()


def plan(model: str = RECOMMENDED_OLLAMA_MODEL, apply: bool = True, steps=None):
    """ai_setup.Plan'ın alanları. Ayar uygulayan iş Ollama'yı da kurar; yalnız model indiren iş yalnız çeker."""
    if steps is None:
        steps = [Step.DOWNLOAD, Step.INSTALL, Step.START, Step.CHECK, Step.PULL] if apply else [Step.PULL]
    size = {RECOMMENDED_OLLAMA_MODEL: 8 * GB, LIGHT: 6_600_000_000}.get(model, 5 * GB)
    download = (SETUP if Step.DOWNLOAD in steps else 0) + (size if Step.PULL in steps else 0)
    return SimpleNamespace(model=model, steps=list(steps), download_bytes=download, disk_bytes=download + GB,
                           disk_path=Path("C:/Users/oyuncu/.ollama/models"), blocker="", blocker_kind="", apply=apply,
                           url=LOCAL, disk_free=200 * GB, ollama_version="", updating=False)


def _fake_ai_class():
    from PySide6.QtCore import QObject, Signal

    class FakeAi(QObject):
        """AiSetup'ın sinyalleri ve alanları (bkz. test_setup_dialog.py); işi yürütmez, durumu test verir. succeed()
        servisin başarıda yaptığını yapar: ayar uygulayan işte motor ve model ayarlara yazılır, ayardaki adres kurulan
        Ollama'nınki olur. advice() `gate` açılana dek bekler (donanım okuması sürerken sayfayı denemek için)."""

        changed = Signal()
        finished = Signal(bool, str)

        def __init__(self, settings, adv):
            super().__init__()
            self.settings = settings
            self._advice = adv
            self.gate = threading.Event()
            self.gate.set()
            self.state, self.step, self.substep = "idle", None, ""
            self.done_bytes = self.total_bytes = self.all_done_bytes = self.all_total_bytes = 0
            self.fraction = 0.0
            self.eta_s = None
            self.message = self.error = self.error_kind = self.model = self.note = ""
            self.speed = None
            self.active_plan = None
            self.can_cancel = True
            self.plans: list[tuple] = []
            self.started: list = []
            self.confirms: list = []
            self.on_confirm = None  # onay penceresinde "Kur": bu planla iş başlar (None: Vazgeç)
            self.plan_error: Exception | None = None  # plan() bunu fırlatır (Ollama yoklanamadı vb.)
            self.cancels = self.retries = self.resumes = self.advice_calls = 0

        @property
        def can_retry(self) -> bool:
            return self.state == "failed" and self.error_kind != "signature"

        @property
        def manual_install(self) -> bool:
            return self.state == "failed" and self.error_kind in ("signature", "blocked")

        def advice(self):
            self.advice_calls += 1
            self.gate.wait(10)
            return self._advice

        def plan(self, model=None, apply=True):
            self.plans.append((model, apply))
            if self.plan_error is not None:
                raise self.plan_error
            return plan(model or RECOMMENDED_OLLAMA_MODEL, apply=apply)

        def start(self, p):
            self.started.append(p)
            self.active_plan, self.model = p, p.model
            self.state, self.step, self.message, self.error, self.error_kind = "running", None, "Hazırlanıyor…", "", ""
            self.changed.emit()

        def retry(self):
            self.retries += 1
            self.state, self.error, self.error_kind, self.message = "running", "", "", "Hazırlanıyor…"
            self.changed.emit()

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

        def fail(self, error, kind=""):
            self.state, self.error, self.error_kind, self.message = "failed", error, kind, error.splitlines()[0]
            self.finished.emit(False, error)
            self.changed.emit()

        def succeed(self):
            p = self.active_plan
            if p is None or p.apply:
                t = self.settings.translate
                t.engine, t.ollama_model = "ollama", self.model
                if t.ollama_url != p.url:
                    t.ollama_url = p.url  # kurulan Ollama bu adreste
                text = "Yapay zekâ çevirisi hazır"
            else:
                text = f"{catalog_title(self.model)} indirildi."
            self.state, self.step, self.message = "done", None, text
            self.finished.emit(True, text)
            self.changed.emit()

    return FakeAi


def _fake_confirm(parent, ai):
    """confirm_ai_setup yerine: çağrıyı kaydeder; ai.on_confirm bir plansa "Kur"a basılmış gibi işi başlatır."""
    ai.confirms.append(parent)
    if ai.on_confirm is None:
        return False
    ai.start(ai.on_confirm)
    return True


def _page(qapp, monkeypatch, s: Settings | None = None, names=(), adv=GOOD, wait_advice: bool = True,
          ai_state: dict | None = None):
    """Sayfa ve sahteleri. adv: donanım önerisi (None: okunamadı). wait_advice=False: öneri kapısı kapalı başlar
    (test açar). ai_state: sayfa kurulmadan önce servisin durumu (ör. önceki açılıştan kalan hata)."""
    import altyazitr.gui.translate_page as tp

    s = s or Settings()
    fake = FakeOllama(names)
    monkeypatch.setattr(tp, "ollama_list", fake.list)
    monkeypatch.setattr(tp, "ollama_pull", fake.pull)
    monkeypatch.setattr(tp, "quickmt_downloaded", lambda: True)
    monkeypatch.setattr(tp, "confirm_ai_setup", _fake_confirm)
    ai = _fake_ai_class()(s, adv)
    if not wait_advice:
        ai.gate.clear()
    for k, val in (ai_state or {}).items():
        setattr(ai, k, val)
    calls = {"save": 0, "restart": 0, "clear": 0}

    def bump(key):
        calls[key] += 1

    ctx = SimpleNamespace(s=s, ai=ai, save_later=lambda: bump("save"), request_restart=lambda: bump("restart"),
                          pipeline=SimpleNamespace(cache=SimpleNamespace(clear=lambda: bump("clear"))))
    page = tp.TranslatePage(SimpleNamespace(ctx=ctx))
    _PAGES.append(page)
    if wait_advice:
        assert _wait(qapp, lambda: page.advice_known)
    return page, s.translate, calls, fake


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    """Arka plan işinin sinyalleri arayüz iş parçacığında işlensin diye olay döngüsünü döndürür."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


def _settings(engine: str = "ollama") -> Settings:
    """Önerilen kurulumdan önceki durum: başka model."""
    s = Settings()
    t = s.translate
    t.engine, t.ollama_model = engine, "translategemma:12b"
    return s


def _vis(page, w) -> bool:
    return w.isVisibleTo(page)


# --- sayfa ------------------------------------------------------------------------------------
def test_page_builds_without_game_profile(qapp, monkeypatch):
    """Oyun profili seçimi ve "Ek bağlam" yok: çeviri her oyunda aynı genel kurallarla yapılır."""
    from PySide6.QtWidgets import QLabel

    page, t, calls, fake = _page(qapp, monkeypatch)
    assert not hasattr(page, "profile") and not hasattr(page, "extra")
    labels = {lb.text() for lb in page.findChildren(QLabel)}
    assert {"Motor", "Hibrit mod", "Bağlam"} <= labels
    assert not labels & {"Oyun profili", "Ek bağlam", "Oyun bağlamı"}
    assert t.profile == "genel"
    assert calls == {"save": 0, "restart": 0, "clear": 0}  # açılırken hiçbir şey kaydedilmez
    assert fake.lists == [] and fake.pulls == []  # açılırken Ollama'ya sorulmaz
    ai = page.ai
    assert ai.plans == [] and ai.started == [] and ai.confirms == []  # kurulum yoklanmaz, başlamaz
    assert ai.advice_calls == 1  # donanım önerisi bir kez, arka planda okunur


def test_context_and_hybrid(qapp, monkeypatch):
    s = Settings()
    s.translate.engine = "ollama"
    page, t, calls, _ = _page(qapp, monkeypatch, s)
    assert (page.context.minimum(), page.context.maximum()) == (0, 12)
    assert page.context.value() == t.context_lines == 8
    page.context.setValue(12)
    assert t.context_lines == 12 and calls == {"save": 1, "restart": 1, "clear": 0}
    assert page.hybrid.text() == "Yapay zekâ gecikirse hızlı çeviriyi göster" and page.hybrid.isChecked()
    assert "~1,2 sn" in page.hybrid.toolTip()
    page.hybrid.setChecked(False)
    assert t.hybrid is False and calls["restart"] == 2


def test_rows_follow_engine(qapp, monkeypatch):
    page, t, _, _ = _page(qapp, monkeypatch)

    def engine(key):
        page.engine.setCurrentIndex(page.engine.findData(key))
        assert t.engine == key

    # yerel hızlı çeviri: yapay zekâya özgü satırlar görünmez
    assert not _vis(page, page.context) and not _vis(page, page.hybrid)
    engine("deepl")  # bağlam satırları var
    assert _vis(page, page.context)
    engine("ollama")
    assert all(_vis(page, w) for w in (page.hybrid, page.context, page.ol_setup, page.ol_pull))
    assert not any(_vis(page, w) for w in (page.ol_bar, page.ol_cancel, page.ol_line, page.ol_progress))
    engine("none")
    assert not _vis(page, page.context)


# --- yapay zekâ daveti ------------------------------------------------------------------------------
def test_invite_visible_until_ollama(qapp, monkeypatch):
    from altyazitr.gui.translate_page import model_title

    page, t, calls, _ = _page(qapp, monkeypatch)  # yeni kullanıcı: yerel hızlı çeviri, 12 GB ekran kartı
    hint = page.hint_box
    assert t.engine == "quickmt" and _vis(page, hint) and _vis(page, page.hint_setup)
    assert not _vis(page, page.hint_progress)  # iş yok: ilerleme paneli gizli
    assert not _vis(page, page.card_ol)  # Ollama bölümü gizliyken de düğmeye ulaşılır
    text = page.hint_text.text.text()
    assert text == (f"Daha iyi çeviri: yerel yapay zekâ (Ollama + {model_title(RECOMMENDED_OLLAMA_MODEL)}), "
                    "ücretsiz ve internetsiz")
    # ayrıntı ipucunda: ne kurulacağı ve karta özgü not; iki düğmede aynı
    tip = page.hint_setup.toolTip()
    assert tip == page.ol_setup.toolTip() == page.hint_text.text.toolTip()
    assert "Ollama'yı" in tip and "Gemma 4 12B modelini indirip kurar" in tip and "Gemma 4 12B bu karta sığar." in tip
    page.engine.setCurrentIndex(page.engine.findData("ollama"))
    page.ol_model.setCurrentText(LIGHT)
    # Ollama seçili: kartı küçük olduğu için bilerek başka model seçene davet çıkmaz; düğme Ollama bölümünde
    assert not _vis(page, hint) and _vis(page, page.ol_setup)
    page.ol_model.setCurrentText(RECOMMENDED_OLLAMA_MODEL)
    assert not _vis(page, hint) and _vis(page, page.ol_setup)
    page.engine.setCurrentIndex(page.engine.findData("gemini"))
    assert _vis(page, hint)


def test_invite_hidden_for_saved_ollama(qapp, monkeypatch):
    s = Settings()
    s.translate.engine, s.translate.ollama_model = "ollama", RECOMMENDED_OLLAMA_MODEL
    page, _, _, _ = _page(qapp, monkeypatch, s)
    assert not _vis(page, page.hint_box) and _vis(page, page.ol_setup)


@pytest.mark.parametrize("tier,model,shown", [
    ("none", None, False),
    ("weak", None, False),
    ("edge", LIGHT, True),
    ("mid", LIGHT, True),
    ("good", RECOMMENDED_OLLAMA_MODEL, True),
    ("strong", RECOMMENDED_OLLAMA_MODEL, True),
    ("unknown", LIGHT, True),
])
def test_invite_follows_hardware_tier(qapp, monkeypatch, tier, model, shown):
    """Davet kademeye göre: uygun ekran kartı yoksa (none/weak) gizli; metindeki model ve ipucundaki uyarı öneriden.
    Ollama bölümündeki "Önerilen kurulum" kademeden bağımsız durur (motoru bilerek Ollama yapan kullanıcı için)."""
    from altyazitr.gui.translate_page import model_title

    warn = "6 GB ekran kartı: yayın açıkken gecikebilir" if tier == "edge" else ""
    page, t, _, _ = _page(qapp, monkeypatch, adv=advice(tier, model, warn=warn, tip=""))
    assert t.engine == "quickmt" and _vis(page, page.hint_box) is shown
    if shown:
        assert f"(Ollama + {model_title(model)})" in page.hint_text.text.text()
        assert f"{model_title(model)} modelini indirip kurar" in page.hint_setup.toolTip()
        assert (warn in page.hint_setup.toolTip()) if warn else "gecikebilir" not in page.hint_setup.toolTip()
    page.engine.setCurrentIndex(page.engine.findData("ollama"))
    assert _vis(page, page.ol_setup) and page.ol_setup.isEnabled()


def test_invite_waits_for_hardware_advice(qapp, monkeypatch):
    """Ekran kartı arka planda okunur (arayüz donmaz); okunana dek davet gizli. Okunamazsa (None) davet görünür:
    onay penceresi ve kurulumdaki GPU kapısı yine korur."""
    page, _, _, _ = _page(qapp, monkeypatch, adv=None, wait_advice=False)
    got: list = []
    page.advice_ready.connect(got.append)
    assert not page.advice_known and not _vis(page, page.hint_box)
    page.ai.gate.set()
    assert _wait(qapp, lambda: page.advice_known)
    assert got == [None] and page.advice is None and _vis(page, page.hint_box)
    assert "Gemma 4 12B" in page.hint_text.text.text()  # öneri yok: önerilen model


# --- önerilen kurulum: onay penceresi ve servisin işi ----------------------------------------------------
BUTTONS = ("hint_setup", "ol_setup", "ol_pull")


def _enabled(page) -> list[bool]:
    return [getattr(page, b).isEnabled() for b in BUTTONS]


@pytest.mark.parametrize("where", ["hint", "ollama"])
def test_recommended_setup_opens_confirm_window(qapp, monkeypatch, where):
    """İki "Önerilen kurulum" da onay penceresini açar: confirm_ai_setup(sayfa, servis). Vazgeçilirse hiçbir şey inmez,
    ayar değişmez. Ollama'ya doğrudan sorulmaz, siteye yönlendirme yok: Ollama'yı servis kurar."""
    from PySide6.QtWidgets import QPushButton

    s = _settings(engine="quickmt" if where == "hint" else "ollama")
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=None)  # Ollama kurulu değil
    before = (t.engine, t.ollama_model, t.profile)
    button = page.hint_setup if where == "hint" else page.ol_setup
    assert _vis(page, button) and button.isEnabled()
    assert _wait(qapp, lambda: not page._ol_reading)  # Ollama bölümü yüklü modelleri arka planda okur
    listed = len(fake.lists)
    button.click()
    ai = page.ai
    assert ai.confirms == [page] and ai.started == [] and ai.plans == []
    assert len(fake.lists) == listed and fake.pulls == [] and page._workers == []  # Ollama'ya doğrudan sorulmaz
    assert (t.engine, t.ollama_model, t.profile) == before and calls == {"save": 0, "restart": 0, "clear": 0}
    assert not _vis(page, page.hint_progress) and not _vis(page, page.ol_progress) and not _vis(page, page.ol_line)
    assert all(_enabled(page))
    links = [b for b in page.findChildren(QPushButton) if "ollama.com" in b.accessibleDescription()]
    assert not any(_vis(page, b) for b in links)


def test_running_job_shows_compact_panel_in_invite(qapp, monkeypatch):
    """"Kur" denince iş arka planda başlar: davet alanında kısa ilerleme paneli (İptal), davet metni ve düğmesi
    gizli; iş sürerken iki "Önerilen kurulum" ve "Modeli indir" kapalı. Duraklatılınca "Yine de sürdür". İptal
    edilince davet geri gelir."""
    page, t, calls, _ = _page(qapp, monkeypatch)
    ai = page.ai
    ai.on_confirm = plan()
    page.hint_setup.click()
    assert ai.started == [ai.on_confirm]
    panel = page.hint_progress
    assert _vis(page, page.hint_box) and _vis(page, panel) and not _vis(page, page.hint_invite)
    assert not _vis(page, page.ol_progress)
    assert _enabled(page) == [False, False, False]
    ai.emit(step=Step.PULL, message="Gemma 4 12B indiriliyor", done_bytes=int(2.7 * GB), total_bytes=8 * GB)
    assert panel.line_text.text() == "Gemma 4 12B indiriliyor" and panel.line_detail.text() == "2,7 / 8,0 GB"
    assert _vis(page, panel.bar) and _vis(page, panel.cancel_btn) and panel.cancel_btn.isEnabled()
    assert not _vis(page, panel.retry_btn) and not _vis(page, panel.resume_btn)
    ai.emit(state="paused", message="Duraklatıldı: çeviri çalışıyor")
    assert _vis(page, panel) and _vis(page, panel.resume_btn) and _enabled(page) == [False, False, False]
    panel.resume_btn.click()
    assert ai.resumes == 1
    ai.emit(state="running")
    panel.cancel_btn.click()
    assert ai.cancels == 1 and ai.state == "idle"
    assert not _vis(page, panel) and _vis(page, page.hint_invite) and _vis(page, page.hint_setup)
    assert all(_enabled(page)) and not _vis(page, page.ol_line)  # davetten başlayan işin iptali davette biter
    assert (t.engine, calls) == ("quickmt", {"save": 0, "restart": 0, "clear": 0})


def test_job_progress_stays_where_it_started(qapp, monkeypatch):
    """İlerleme düğmesine basılan yerde: Ollama bölümünden başlayan iş orada görünür (hatada "Hızlı çeviride kal"
    yerine "Vazgeç": motor zaten Ollama). Ollama bölümü gizlenirse (motor değişti) ilerleme davete geçer."""
    page, t, _, _ = _page(qapp, monkeypatch, _settings(engine="ollama"))
    ai = page.ai
    ai.on_confirm = plan()
    page.ol_setup.click()
    assert _vis(page, page.ol_progress) and not _vis(page, page.hint_box)
    assert page.ol_progress.stay_btn.text() == "Vazgeç"
    page.engine.setCurrentIndex(page.engine.findData("quickmt"))
    assert _vis(page, page.hint_progress) and not _vis(page, page.card_ol)
    page.engine.setCurrentIndex(page.engine.findData("ollama"))
    assert _vis(page, page.ol_progress) and not _vis(page, page.hint_box)
    ai.fail(NET_ERROR, "network")
    panel = page.ol_progress
    assert _vis(page, panel) and panel.line_text.text() == NET_ERROR.splitlines()[0]
    assert _vis(page, panel.retry_btn) and _vis(page, panel.stay_btn) and not _vis(page, panel.link_btn)
    assert all(_enabled(page))  # hata: yeni bir iş başlatılabilir
    panel.retry_btn.click()  # "Tekrar dene" yerini korur
    assert ai.retries == 1 and _vis(page, page.ol_progress) and not _vis(page, page.hint_box)
    panel.cancel_btn.click()
    assert ai.cancels == 1 and not _vis(page, panel)
    assert page.ol_line.text.text() == "İptal edildi" and _vis(page, page.ol_line)


def test_failed_job_shows_retry_and_stay_returns_invite(qapp, monkeypatch):
    """Hata davette kalır: kısa metin (ayrıntı ipucunda), "Tekrar dene" ve "Hızlı çeviride kal". İmza hatasında
    yeniden denemek düzeltmez: "Tekrar dene" yok, "Ollama'yı elle kur" var. "Hızlı çeviride kal" hatayı kapatır,
    davet geri gelir."""
    page, _, _, _ = _page(qapp, monkeypatch)
    ai = page.ai
    ai.on_confirm = plan()
    page.hint_setup.click()
    ai.fail(NET_ERROR, "network")
    panel = page.hint_progress
    assert _vis(page, panel) and not _vis(page, page.hint_invite)
    assert panel.line_text.text() == NET_ERROR.splitlines()[0] and panel.line_text.toolTip() == NET_ERROR
    assert _vis(page, panel.retry_btn) and _vis(page, panel.stay_btn) and not _vis(page, panel.link_btn)
    assert panel.stay_btn.text() == "Hızlı çeviride kal"
    sig = "Ollama dosyasının imzası doğrulanamadı; kurulmadı.\nAyrıntı: İmzalayan: Evil Inc."
    ai.fail(sig, "signature")
    assert not _vis(page, panel.retry_btn) and _vis(page, panel.stay_btn) and _vis(page, panel.link_btn)
    panel.stay_btn.click()
    assert ai.cancels == 1 and ai.state == "idle"
    assert not _vis(page, panel) and _vis(page, page.hint_invite) and all(_enabled(page))


def test_failure_from_previous_run_shows_in_invite(qapp, monkeypatch):
    """Önceki açılışta hatayla biten iş (servis "failed" başlar): sayfa açılınca davette, motor Ollama olsa da."""
    s = Settings()
    s.translate.engine = "ollama"
    page, _, _, _ = _page(qapp, monkeypatch, s, ai_state={"state": "failed", "error": NET_ERROR,
                                                         "error_kind": "network", "message": "İnternet"})
    assert _vis(page, page.hint_box) and _vis(page, page.hint_progress) and not _vis(page, page.ol_progress)
    assert _vis(page, page.hint_progress.retry_btn)


# --- kurulum bitti: seçimler ayarlarla eşlenir -------------------------------------------------------------
def test_finished_job_syncs_selections_without_signals(qapp, monkeypatch):
    """Kurulum bitince servis ayarları yazdı (motor, model; eski uzak adres kurulan Ollama'nınki oldu): seçimler
    sinyal göndermeden eşlenir, sayfa ayar kaydı ve yeniden başlatma istemez (çeviri çalışıyorsa yapay zekâ bir
    sonraki Başlat'ta devreye girer). Profil korunur. Sonuç Ollama bölümünde yazar."""
    from altyazitr.gui.translate_page import model_title

    s = _settings(engine="quickmt")
    s.translate.ollama_url = REMOTE
    profile = s.translate.profile
    page, t, calls, fake = _page(qapp, monkeypatch, s, adv=advice("mid", LIGHT))
    ai = page.ai
    ai.on_confirm = plan(LIGHT)
    page.hint_setup.click()
    ai.succeed()
    assert (t.engine, t.ollama_model, t.ollama_url, t.profile) == ("ollama", LIGHT, LOCAL, profile)
    assert page.engine.currentData() == "ollama" and page.ol_model.currentText() == LIGHT
    assert page.ol_url.text() == LOCAL
    assert calls == {"save": 0, "restart": 0, "clear": 0}
    # davet gizlenir, Ollama bölümü açılır ve sonuç orada
    assert not _vis(page, page.hint_box) and _vis(page, page.card_ol) and not _vis(page, page.card_qm)
    assert page.ol_line.text.text() == f"Hazır: {model_title(LIGHT)} yerel çeviri" and _vis(page, page.ol_line)
    assert not _vis(page, page.hint_progress) and not _vis(page, page.ol_progress) and all(_enabled(page))
    assert fake.pulls == []  # model servisle indi; yüklü modeller yalnız "Modeli indir" düğmesi için okunur
    assert _wait(qapp, lambda: not page._ol_reading) and page.ol_line.text.text().startswith("Hazır: ")
    # sonra kullanıcı modeli değiştirirse her zamanki gibi kaydedilir ve hat yeniden başlar
    page.ol_model.setCurrentText(RECOMMENDED_OLLAMA_MODEL)
    assert t.ollama_model == RECOMMENDED_OLLAMA_MODEL and calls == {"save": 1, "restart": 1, "clear": 0}


# --- "Modeli indir": yerel adreste servis, uzak sunucuda doğrudan -------------------------------------------
def test_manual_model_download_goes_to_service_on_local_address(qapp, monkeypatch):
    """Yerel Ollama'da "Modeli indir" kurulum servisine gider: ai.start(ai.plan(model, apply=False)); plan arka
    planda çıkarılır. Yalnız model iner, ayar değişmez; ilerleme Ollama bölümünde."""
    s = Settings()
    s.translate.engine = "ollama"
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=["llama3.2:3b"])
    assert _wait(qapp, lambda: page._ol_installed is not None)  # yüklü modeller arka planda okundu
    listed = len(fake.lists)
    page.ol_model.setCurrentText("qwen3:8b")
    assert t.ollama_model == "qwen3:8b" and calls["restart"] == 1 and _vis(page, page.ol_pull)  # yüklü değil
    ai = page.ai
    page.ol_pull.click()
    assert _enabled(page) == [False, False, False]  # plan hazırlanırken de tek indirme
    assert _wait(qapp, lambda: ai.started)
    assert ai.plans == [("qwen3:8b", False)] and [p.apply for p in ai.started] == [False]
    assert len(fake.lists) == listed and fake.pulls == []  # Ollama'ya doğrudan gidilmez
    assert _vis(page, page.ol_progress) and not _vis(page, page.hint_box) and not _vis(page, page.ol_line)
    assert _enabled(page) == [False, False, False]
    ai.succeed()
    assert (t.engine, t.ollama_model) == ("ollama", "qwen3:8b")  # ayar değişmez
    assert page.ol_line.text.text() == "qwen3:8b indirildi" and page.ol_model.findData("qwen3:8b") >= 0
    assert calls == {"save": 1, "restart": 1, "clear": 0} and not _vis(page, page.ol_progress)
    assert all(_enabled(page))


def test_manual_model_download_reports_plan_error(qapp, monkeypatch):
    s = Settings()
    s.translate.engine, s.translate.ollama_url = "ollama", ""  # boş adres: servis varsayılan yerel adresi kullanır
    page, _, _, fake = _page(qapp, monkeypatch, s)
    page.ai.plan_error = RuntimeError("yoklama başarısız")
    page.ol_pull.click()
    assert _wait(qapp, lambda: page._planning is None)
    assert page.ai.plans and page.ai.started == [] and fake.pulls == []
    assert page.ol_line.text.text() == "İndirme başlatılamadı" and "yoklama başarısız" in page.ol_line.text.toolTip()
    assert all(_enabled(page))


def test_manual_model_download_on_remote_server_uses_ollama_directly(qapp, monkeypatch):
    """Uzak Ollama sunucusunda (servis 127.0.0.1 dışına gitmez) model eskisi gibi doğrudan oraya iner; bitince
    model listesi yenilenir ve hat yeniden başlar."""
    s = Settings()
    s.translate.engine, s.translate.ollama_url = "ollama", REMOTE
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=["llama3.2:3b"])
    page.ol_model.setCurrentText("qwen3:8b")
    page.ol_pull.click()
    assert _wait(qapp, lambda: page._pull is None)
    assert fake.pulls == [(REMOTE, "qwen3:8b")] and page.ai.plans == [] and page.ai.started == []
    # yüklü modeller arka planda yeniden okunup listeye eklenir; inen model artık yüklü, "Modeli indir" gizlenir
    assert _wait(qapp, lambda: page.ol_model.findData("qwen3:8b") >= 0)
    assert page.ol_line.text.text() == "qwen3:8b indirildi" and not _vis(page, page.ol_pull)
    assert t.engine == "ollama" and t.ollama_model == "qwen3:8b" and t.profile == "genel"
    assert calls["restart"] == 2 and all(_enabled(page))


def test_remote_server_unreachable(qapp, monkeypatch):
    s = Settings()
    s.translate.engine, s.translate.ollama_url = "ollama", REMOTE
    page, _, calls, fake = _page(qapp, monkeypatch, s, names=None)
    page.ol_pull.click()
    assert page.ol_line.text.text() == "Ollama çalışmıyor" and fake.pulls == [] and page._workers == []
    assert calls["restart"] == 0


def test_remote_download_can_be_cancelled(qapp, monkeypatch):
    s = _settings(engine="ollama")
    s.translate.ollama_url = REMOTE
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=[])
    before = (t.engine, t.ollama_model, t.profile)
    fake.gate.clear()
    page.ol_pull.click()
    assert _wait(qapp, lambda: page.ol_bar.value() == 500)
    assert "4.00 / 8.00 GB" in page.ol_line.text.text() and page.ol_bar.maximum() == 1000
    assert _vis(page, page.ol_bar) and _vis(page, page.ol_cancel) and page.ol_cancel.isEnabled()
    assert _enabled(page) == [False, False, False]
    page.ol_cancel.click()
    assert not page.ol_cancel.isEnabled() and page.ol_line.text.text() == "İptal ediliyor…"
    assert _wait(qapp, lambda: page._pull is None)
    assert page.ol_line.text.text() == "İndirme iptal edildi"
    assert (t.engine, t.ollama_model, t.profile) == before
    assert calls == {"save": 0, "restart": 0, "clear": 0}
    assert all(_enabled(page)) and not _vis(page, page.ol_bar) and not _vis(page, page.ol_cancel)


def test_quit_stops_running_download(qapp, monkeypatch):
    """Uygulama kapanırken (aboutToQuit) süren indirme durdurulur: iş parçacığı çalışırken yok edilmesin."""
    s = _settings()
    s.translate.ollama_url = REMOTE
    page, t, _, fake = _page(qapp, monkeypatch, s, names=[])
    fake.gate.clear()
    page.ol_pull.click()
    assert _wait(qapp, lambda: page.ol_bar.value() == 500)
    w = page._workers[0]
    assert w.isRunning()
    page._stop_workers()
    assert w.isFinished() and w.cancel.is_set()
    assert t.engine == "ollama" and t.ollama_model == "translategemma:12b"


# --- küçük yardımcılar ---------------------------------------------------------------------------
def test_helpers():
    from altyazitr.gui.translate_page import hybrid_tip, invite_offered, model_title, setup_tip

    assert "~1,2 sn" in hybrid_tip(1200) and "~2 sn" in hybrid_tip(2000) and "~0,5 sn" in hybrid_tip(500)
    assert "~" not in hybrid_tip(0)
    presets = [("gemma4:12b", "Gemma 4 12B — önerilen: en iyi bağlam"), ("x:1b", "X (deneme) — hızlı")]
    assert model_title("gemma4:12b", presets) == "Gemma 4 12B"
    assert model_title("x:1b", presets) == "X"
    assert model_title("bilinmeyen:7b", presets) == "bilinmeyen:7b"
    assert model_title(LIGHT) == "Gemma 4 E4B"  # hazır listeden (başlıklar model kataloğundan)
    # davet: kart uygun değilse yok; öneri okunamadıysa var
    assert not invite_offered(advice("none", None)) and not invite_offered(advice("weak", None))
    assert invite_offered(advice("edge", LIGHT)) and invite_offered(advice("unknown", LIGHT))
    assert invite_offered(None)
    tip = setup_tip(LIGHT, advice("edge", LIGHT, warn="6 GB ekran kartı: yayın açıkken gecikebilir", tip="Hafif."))
    lines = tip.splitlines()
    assert "Gemma 4 E4B modelini indirip kurar" in lines[0] and lines[1:] == [
        "Hafif.", "6 GB ekran kartı: yayın açıkken gecikebilir"]
    assert setup_tip(RECOMMENDED_OLLAMA_MODEL).count("\n") == 0
    assert "ollama.com" not in tip and "elle" not in tip  # siteye ya da elle kurmaya yönlendirme yok


# --- yüklü model ve oyun profili ipucu ----------------------------------------------------------------
def test_has_model_matches_ollama_names():
    from altyazitr.gui.translate_page import has_model

    names = ["gemma4:12b", "llama3:latest"]
    assert has_model("gemma4:12b", names) and has_model(" Gemma4:12B ", names)
    assert has_model("llama3", names)  # etiketsiz ad Ollama'da ":latest" olarak görünür
    assert not has_model("gemma4:e4b", names) and not has_model("gemma4", names) and not has_model("", names)


def test_pull_button_hidden_for_installed_model(qapp, monkeypatch):
    """Seçili model Ollama'da yüklüyse "Modeli indir" görünmez; yüklü olmayan modelde görünür. Yüklü modeller
    arka planda okunur (arayüz donmaz)."""
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(), names=["translategemma:12b", "llama3:latest"])
    assert _wait(qapp, lambda: page._ol_installed is not None) and fake.lists
    assert t.ollama_model == "translategemma:12b" and not _vis(page, page.ol_pull)
    assert "yüklü" in page.ol_model.toolTip()
    page.ol_model.setCurrentText("gemma4:e4b")  # yüklü değil
    assert _vis(page, page.ol_pull)
    page.ol_model.setCurrentText("llama3")  # Ollama'da "llama3:latest"
    assert not _vis(page, page.ol_pull)
    assert page.ol_model.findData("llama3:latest") >= 0  # yüklü modeller seçime eklendi


def test_pull_button_stays_when_models_unknown(qapp, monkeypatch):
    """Ollama'ya ulaşılamıyorsa hangi modelin yüklü olduğu bilinmez: "Modeli indir" görünür kalır. Yenile düğmesi
    sonucu durum satırına yazar."""
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(), names=None)
    assert _wait(qapp, lambda: fake.lists and not page._ol_reading)
    assert page._ol_installed is None and _vis(page, page.ol_pull)
    page._ollama_refresh()
    assert _wait(qapp, lambda: page.ol_line.text.text() == "Ollama çalışmıyor")
    fake.names = ["translategemma:12b"]
    page._ollama_refresh()
    assert _wait(qapp, lambda: page.ol_line.text.text() == "1 model yüklü")
    assert not _vis(page, page.ol_pull)


def test_models_read_when_switching_to_ollama(qapp, monkeypatch):
    """Motor Ollama değilken yüklü modeller okunmaz; Ollama'ya geçince okunur."""
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(engine="quickmt"), names=["translategemma:12b"])
    qapp.processEvents()
    assert fake.lists == []
    page.engine.setCurrentIndex(page.engine.findData("ollama"))
    assert _wait(qapp, lambda: page._ol_installed is not None)
    assert not _vis(page, page.ol_pull)

