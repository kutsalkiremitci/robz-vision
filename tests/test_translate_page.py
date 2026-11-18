"""Çeviri sayfası: oyun profili, bağlam, karma mod ve "Önerilen kurulum" (ekran gerektirmez; Qt 'offscreen').

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır
(bkz. test_profiles.py). Açılan sayfalar her testten sonra kapatılıp Qt tarafında silinir (bkz.
test_style_profiles.py). Ağ yok: ollama_list ve ollama_pull sahteleriyle değiştirilir; sahte indirme gerçek
arka plan iş parçacığında (Worker) çalışır, sonucu sayfaya sinyalle döner."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from altyazitr.config import Settings
from altyazitr.game_profiles import DEFAULT_PROFILE_ID, get_profile, list_profiles
from altyazitr.translate import RECOMMENDED_OLLAMA_MODEL, TranslatorError


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
# test_style_profiles.py). Süren sahte indirme de sayfayla birlikte silinmeden önce bitirilir.
_PAGES: list = []


@pytest.fixture(autouse=True)
def _close_pages():
    yield
    if not _PAGES:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

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


def _page(qapp, monkeypatch, s: Settings | None = None, names=()):
    import altyazitr.gui.translate_page as tp

    s = s or Settings()
    fake = FakeOllama(names)
    monkeypatch.setattr(tp, "ollama_list", fake.list)
    monkeypatch.setattr(tp, "ollama_pull", fake.pull)
    monkeypatch.setattr(tp, "quickmt_downloaded", lambda: True)
    monkeypatch.setattr(tp, "confirm_download", lambda parent, model: True)  # boyut onayı: "İndir"
    calls = {"save": 0, "restart": 0, "clear": 0}

    def bump(key):
        calls[key] += 1

    ctx = SimpleNamespace(s=s, save_later=lambda: bump("save"), request_restart=lambda: bump("restart"),
                          pipeline=SimpleNamespace(cache=SimpleNamespace(clear=lambda: bump("clear"))))
    page = tp.TranslatePage(SimpleNamespace(ctx=ctx))
    _PAGES.append(page)
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


def _other_profile() -> str:
    """Varsayılan olmayan bir profil (genel profil her zaman listededir)."""
    other = next((p.id for p in list_profiles() if p.id != DEFAULT_PROFILE_ID), None)
    assert other is not None
    return other


def _settings(engine: str = "ollama") -> Settings:
    """Önerilen kurulumdan önceki durum: başka model, varsayılan olmayan profil."""
    s = Settings()
    t = s.translate
    t.engine, t.ollama_model, t.profile = engine, "translategemma:12b", _other_profile()
    return s


def _vis(page, w) -> bool:
    return w.isVisibleTo(page)


# --- sayfa ve oyun profili -----------------------------------------------------------------------
def test_page_builds_and_lists_profiles(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    page, t, calls, fake = _page(qapp, monkeypatch)
    profiles = list_profiles()
    assert profiles and DEFAULT_PROFILE_ID in {p.id for p in profiles}
    cb = page.profile
    assert [cb.itemText(i) for i in range(cb.count())] == [p.name for p in profiles]
    assert [cb.itemData(i) for i in range(cb.count())] == [p.id for p in profiles]
    assert [cb.itemData(i, Qt.ItemDataRole.ToolTipRole) for i in range(cb.count())] == \
        [p.description for p in profiles]
    assert cb.currentData() == t.profile == DEFAULT_PROFILE_ID
    assert cb.toolTip() == get_profile(DEFAULT_PROFILE_ID).description
    labels = {lb.text() for lb in page.findChildren(QLabel)}
    assert {"Motor", "Oyun profili", "Ek bağlam", "Hibrit mod", "Bağlam"} <= labels
    assert "Oyun bağlamı" not in labels
    assert calls == {"save": 0, "restart": 0, "clear": 0}  # açılırken hiçbir şey kaydedilmez
    assert fake.lists == [] and fake.pulls == []  # açılırken Ollama'ya sorulmaz


def test_profile_selection_writes_setting(qapp, monkeypatch):
    other = _other_profile()
    page, t, calls, _ = _page(qapp, monkeypatch)
    page.profile.setCurrentIndex(page.profile.findData(other))
    assert t.profile == other
    assert page.profile.toolTip() == get_profile(other).description
    # eski profille yapılmış çeviriler önbellekten silinir; ayar kaydedilir, işlem hattı yeniden başlar
    assert calls == {"save": 1, "restart": 1, "clear": 1}
    page.profile.setCurrentIndex(page.profile.findData(DEFAULT_PROFILE_ID))
    assert t.profile == DEFAULT_PROFILE_ID and calls["restart"] == 2


def test_unknown_saved_profile_shows_default(qapp, monkeypatch):
    s = Settings()
    s.translate.profile = "yok-boyle-bir-oyun"
    page, t, calls, _ = _page(qapp, monkeypatch, s)
    assert page.profile.currentData() == DEFAULT_PROFILE_ID
    # çeviri de ekrandaki profille yapılsın (get_profile bilinmeyen kimlikte genel profile düşerdi)
    assert t.profile == DEFAULT_PROFILE_ID
    assert calls == {"save": 0, "restart": 0, "clear": 0}


def test_saved_profile_matches_case_insensitively(qapp, monkeypatch):
    other = _other_profile()
    s = Settings()
    s.translate.profile = other.upper()
    page, t, calls, _ = _page(qapp, monkeypatch, s)
    assert page.profile.currentData() == other and t.profile == other.upper()  # get_profile de aynısını bulur
    assert calls == {"save": 0, "restart": 0, "clear": 0}


def test_context_hybrid_and_extra_context(qapp, monkeypatch):
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
    assert page.extra.placeholderText() == "Ör: Lucia ile soygun planı"
    assert page.extra.toolTip().startswith("Profilin üstüne eklenen kendi notun")
    page.extra.setText("Lucia ile soygun planı")
    page.extra.editingFinished.emit()
    assert t.game_context == "Lucia ile soygun planı" and calls["restart"] == 3


def test_rows_follow_engine(qapp, monkeypatch):
    page, t, _, _ = _page(qapp, monkeypatch)

    def engine(key):
        page.engine.setCurrentIndex(page.engine.findData(key))
        assert t.engine == key

    # yerel hızlı çeviri: profil (adları korur) görünür; yapay zekâya özgü satırlar görünmez
    assert _vis(page, page.profile) and not _vis(page, page.extra) and not _vis(page, page.context)
    engine("deepl")  # DeepL istem kullanmaz: ek bağlam yok, bağlam satırları var
    assert _vis(page, page.profile) and _vis(page, page.context) and not _vis(page, page.extra)
    engine("ollama")
    assert all(_vis(page, w) for w in (page.profile, page.extra, page.hybrid, page.context, page.ol_setup))
    assert not _vis(page, page.ol_bar) and not _vis(page, page.ol_cancel) and not _vis(page, page.ol_line)
    engine("none")
    assert not _vis(page, page.profile) and not _vis(page, page.extra)


# --- önerilen kurulum: davet ---------------------------------------------------------------------
def test_setup_hint_visible_until_recommended_ollama(qapp, monkeypatch):
    from altyazitr.gui.translate_page import model_title

    page, t, calls, _ = _page(qapp, monkeypatch)  # yeni kullanıcı: yerel hızlı çeviri
    hint = page.hint_box
    assert t.engine == "quickmt" and _vis(page, hint) and _vis(page, page.hint_setup)
    assert not _vis(page, page.card_ol)  # Ollama bölümü gizliyken de düğmeye ulaşılır
    text = page.hint_text.text.text()
    assert text == (f"Daha iyi çeviri: yerel yapay zekâ (Ollama + {model_title(RECOMMENDED_OLLAMA_MODEL)}), "
                    "ücretsiz ve internetsiz")
    assert page.hint_setup.toolTip() == page.ol_setup.toolTip()
    page.engine.setCurrentIndex(page.engine.findData("ollama"))
    page.ol_model.setCurrentText("gemma4:e4b")
    # Ollama seçili: kartı küçük olduğu için bilerek başka model seçene davet çıkmaz; düğme Ollama bölümünde
    assert not _vis(page, hint) and _vis(page, page.ol_setup)
    page.ol_model.setCurrentText(RECOMMENDED_OLLAMA_MODEL)
    assert not _vis(page, hint) and _vis(page, page.ol_setup)
    page.engine.setCurrentIndex(page.engine.findData("gemini"))
    assert _vis(page, hint)


def test_setup_hint_hidden_for_saved_recommended_ollama(qapp, monkeypatch):
    s = Settings()
    s.translate.engine, s.translate.ollama_model = "ollama", RECOMMENDED_OLLAMA_MODEL
    page, _, _, _ = _page(qapp, monkeypatch, s)
    assert not _vis(page, page.hint_box) and _vis(page, page.ol_setup)


# --- önerilen kurulum: üç durum (Ollama yok, model eksik, model var) -------------------------------
def test_setup_without_ollama_points_to_download(qapp, monkeypatch):
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(engine="quickmt"), names=None)
    before = (t.engine, t.ollama_model, t.profile)
    hs = page.hint_status
    assert not _vis(page, hs.link) and not _vis(page, hs.line)  # bağlantı ve ileti yalnız gerekince
    page.hint_setup.click()
    assert fake.lists == [t.ollama_url] and fake.pulls == [] and page._workers == []
    assert hs.line.text.text() == "Önce Ollama'yı kurun" and _vis(page, hs.line)
    assert "yeniden basın" in hs.line.text.toolTip()
    assert _vis(page, hs.link) and hs.link.accessibleDescription() == "https://ollama.com/download"
    assert page.ol_line.text.text() == ""  # ileti düğmesine basılan yerde
    assert (t.engine, t.ollama_model, t.profile) == before
    assert calls == {"save": 0, "restart": 0, "clear": 0}
    assert page.hint_setup.isEnabled() and not _vis(page, hs.bar)


def test_setup_without_ollama_from_ollama_section(qapp, monkeypatch):
    s = Settings()
    s.translate.engine, s.translate.ollama_model = "ollama", RECOMMENDED_OLLAMA_MODEL
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=None)
    page.ol_setup.click()
    assert page.ol_line.text.text() == "Önce Ollama'yı kurun" and _vis(page, page.ol_line)
    assert _vis(page, page.ol_get) and page.ol_get.accessibleDescription() == "https://ollama.com/download"
    assert not _vis(page, page.hint_box) and fake.pulls == []
    assert calls == {"save": 0, "restart": 0, "clear": 0}


def test_setup_downloads_missing_model_then_applies(qapp, monkeypatch):
    s = _settings(engine="quickmt")
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=["llama3.2:3b"])
    main = threading.current_thread()
    applied_in = []
    apply = page._apply_recommended

    def spy():
        applied_in.append(threading.current_thread())
        apply()

    page._apply_recommended = spy
    hs = page.hint_status
    fake.gate.clear()  # indirme, test izin verene kadar sürsün
    page.hint_setup.click()
    assert _wait(qapp, lambda: "4.00 / 8.00 GB" in hs.line.text.text())
    assert fake.pulls == [(t.ollama_url, RECOMMENDED_OLLAMA_MODEL)]
    # indirilirken: davette ilerleme çubuğu ve İptal görünür, düğmeler kapalı, ayarlar henüz değişmez
    assert _vis(page, hs.bar) and hs.bar.maximum() == 1000 and hs.bar.value() == 500
    assert _vis(page, hs.cancel) and hs.cancel.isEnabled()
    assert not any(b.isEnabled() for b in (page.hint_setup, page.ol_setup, page.ol_pull))
    assert t.engine == "quickmt" and calls == {"save": 0, "restart": 0, "clear": 0}
    page.hint_setup.click()  # kapalı düğme: ikinci indirme başlamaz
    fake.gate.set()
    assert _wait(qapp, lambda: page._pull is None)
    assert applied_in == [main]  # bitiş sinyali arayüz iş parçacığında işlenir
    assert len(fake.pulls) == 1
    assert (t.engine, t.ollama_model, t.profile) == ("ollama", RECOMMENDED_OLLAMA_MODEL, DEFAULT_PROFILE_ID)
    assert page.engine.currentData() == "ollama" and page.ol_model.currentText() == RECOMMENDED_OLLAMA_MODEL
    assert page.profile.currentData() == DEFAULT_PROFILE_ID
    # davet gizlenir; Ollama bölümü açılır ve onay orada
    assert not _vis(page, page.hint_box) and hs.line.text.text() == ""
    assert _vis(page, page.card_ol) and not _vis(page, page.card_qm) and _vis(page, page.extra)
    assert page.ol_line.text.text().startswith("Hazır: ") and _vis(page, page.ol_line)
    assert not _vis(page, page.ol_bar) and not _vis(page, page.ol_cancel)
    assert all(b.isEnabled() for b in (page.hint_setup, page.ol_setup, page.ol_pull))
    assert calls == {"save": 1, "restart": 1, "clear": 1}  # tek kayıt, tek yeniden başlatma


def test_setup_with_installed_model_applies_at_once(qapp, monkeypatch):
    from altyazitr.gui.translate_page import model_title

    page, t, calls, fake = _page(qapp, monkeypatch, _settings(), names=["llama3.2:3b", RECOMMENDED_OLLAMA_MODEL])
    assert not _vis(page, page.hint_box)  # Ollama (başka modelle) seçili: davet yok
    page.ol_setup.click()  # Ollama bölümündeki düğme: davetteki ile aynı iş
    assert fake.pulls == [] and page._workers == []
    assert (t.engine, t.ollama_model, t.profile) == ("ollama", RECOMMENDED_OLLAMA_MODEL, DEFAULT_PROFILE_ID)
    assert page.ol_model.currentText() == RECOMMENDED_OLLAMA_MODEL
    assert page.profile.currentData() == DEFAULT_PROFILE_ID
    assert page.ol_line.text.text() == f"Hazır: {model_title(RECOMMENDED_OLLAMA_MODEL)} yerel çeviri"
    assert not _vis(page, page.hint_box)
    assert calls == {"save": 1, "restart": 1, "clear": 1}
    # ikinci basış: zaten hazır; profil değişmediği için önbellek silinmez
    page.ol_setup.click()
    assert fake.pulls == [] and calls == {"save": 2, "restart": 2, "clear": 1}


def test_setup_asks_before_big_download(qapp, monkeypatch):
    """Model yüklü değilse önerilen kurulum boyutu söyleyip sorar; "Vazgeç" denirse hiçbir şey inmez, ayar değişmez."""
    import altyazitr.gui.translate_page as tp

    page, t, calls, fake = _page(qapp, monkeypatch, _settings(engine="quickmt"), names=[])
    asked: list[str] = []
    monkeypatch.setattr(tp, "confirm_download", lambda parent, model: asked.append(model) or False)
    before = (t.engine, t.ollama_model, t.profile)
    page.hint_setup.click()
    assert asked == [RECOMMENDED_OLLAMA_MODEL]
    assert fake.pulls == [] and page._workers == [] and (t.engine, t.ollama_model, t.profile) == before
    assert calls == {"save": 0, "restart": 0, "clear": 0}
    assert RECOMMENDED_OLLAMA_MODEL in tp.MODEL_DOWNLOAD_SIZE  # önerilen modelin boyutu onayda yazılı


def test_setup_from_hint_with_installed_model(qapp, monkeypatch):
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(engine="gemini"), names=[RECOMMENDED_OLLAMA_MODEL])
    page.hint_setup.click()
    assert fake.pulls == [] and (t.engine, t.ollama_model) == ("ollama", RECOMMENDED_OLLAMA_MODEL)
    assert not _vis(page, page.hint_box) and _vis(page, page.card_ol) and not _vis(page, page.card_gm)
    assert page.ol_line.text.text().startswith("Hazır: ") and calls["restart"] == 1


def test_setup_download_can_be_cancelled(qapp, monkeypatch):
    page, t, calls, fake = _page(qapp, monkeypatch, _settings(engine="quickmt"), names=[])
    before = (t.engine, t.ollama_model, t.profile)
    hs = page.hint_status
    fake.gate.clear()
    page.hint_setup.click()
    assert _wait(qapp, lambda: hs.bar.value() == 500)
    hs.cancel.click()
    assert not hs.cancel.isEnabled() and hs.line.text.text() == "İptal ediliyor…"
    assert _wait(qapp, lambda: page._pull is None)
    assert hs.line.text.text() == "İndirme iptal edildi" and _vis(page, page.hint_box)
    assert (t.engine, t.ollama_model, t.profile) == before
    assert calls == {"save": 0, "restart": 0, "clear": 0}
    assert page.hint_setup.isEnabled() and not _vis(page, hs.bar) and not _vis(page, hs.cancel)


def test_quit_stops_running_download(qapp, monkeypatch):
    """Uygulama kapanırken (aboutToQuit) süren indirme durdurulur: iş parçacığı çalışırken yok edilmesin."""
    page, t, _, fake = _page(qapp, monkeypatch, _settings(), names=[])
    fake.gate.clear()
    page.ol_setup.click()
    assert _wait(qapp, lambda: page.ol_bar.value() == 500)
    w = page._workers[0]
    assert w.isRunning()
    page._stop_workers()
    assert w.isFinished() and w.cancel.is_set()
    assert t.engine == "ollama" and t.ollama_model == "translategemma:12b"  # yarım kurulum uygulanmaz


def test_manual_model_download_still_works(qapp, monkeypatch):
    s = Settings()
    s.translate.engine = "ollama"
    page, t, calls, fake = _page(qapp, monkeypatch, s, names=["llama3.2:3b"])
    page.ol_model.setCurrentText("qwen3:8b")
    assert t.ollama_model == "qwen3:8b" and calls["restart"] == 1
    page.ol_pull.click()
    assert _wait(qapp, lambda: page._pull is None)
    assert fake.pulls == [(t.ollama_url, "qwen3:8b")]
    assert page.ol_model.findData("qwen3:8b") >= 0  # yüklü modeller listeye eklenir
    assert page.ol_line.text.text() == "2 model yüklü"
    assert t.engine == "ollama" and t.ollama_model == "qwen3:8b" and t.profile == DEFAULT_PROFILE_ID
    assert calls["restart"] == 2


# --- küçük yardımcılar ---------------------------------------------------------------------------
def test_helpers():
    from altyazitr.gui.translate_page import hybrid_tip, model_installed, model_title

    assert "~1,2 sn" in hybrid_tip(1200) and "~2 sn" in hybrid_tip(2000) and "~0,5 sn" in hybrid_tip(500)
    assert "~" not in hybrid_tip(0)
    presets = [("gemma4:12b", "Gemma 4 12B — önerilen: en iyi bağlam"), ("x:1b", "X (deneme) — hızlı")]
    assert model_title("gemma4:12b", presets) == "Gemma 4 12B"
    assert model_title("x:1b", presets) == "X"
    assert model_title("bilinmeyen:7b", presets) == "bilinmeyen:7b"
    assert model_installed("gemma4:12b", ["gemma4:12b"]) and model_installed("llama3", ["llama3:latest"])
    assert not model_installed("gemma4:12b", ["gemma4:e4b", "gemma4:12b-q8"])
