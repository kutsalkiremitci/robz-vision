"""Yerel yapay zekânın uygulamaya bağlanması: açılış sırası (ayarlar → kurulum servisi → ilk açılış penceresi →
AppContext), AppContext'teki bağlar (çeviri çalışırken indirme duraklar, yarım iş sürer, kapanışta durur), pencere
açmayan bildirimler (Pipeline.notice → AppContext.notified), yerel yapay zekânın hazırlığı (kapalı Ollama'yı başlatma,
hata metinleri, sistemin vekil sunucusunu yok sayma) ve model kataloğunun tek kaynağı.

Ağ yok (yalnız testin 127.0.0.1'deki küçük HTTP sunucusu), pencere açılmaz, gerçek Ollama başlatılmaz, ayar dosyasına
yazılmaz: AppContext'in ağır parçaları (OBS sunucusu, ekran katmanı, genel kısayollar, işlem hattı) sahteleriyle,
ilk açılış penceresinin modülü sahte bir modülle değiştirilir. Olay döngüsü için QCoreApplication yeter (bkz.
test_ai_setup.py)."""

from __future__ import annotations

import concurrent.futures
import json
import socket
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from PySide6.QtCore import QObject, Signal

from altyazitr import ai_setup, app, ollama_runtime
from altyazitr import pipeline as pl
from altyazitr import translate as T
from altyazitr.config import Settings
from altyazitr.pipeline import Pipeline, TrContext
from altyazitr.translate import Translator, TranslatorError

LOCAL = "http://127.0.0.1:11434"


@pytest.fixture(scope="module")
def qapp():
    """Pencere yok: QCoreApplication yeter. Kendi kurduğu uygulamayı sonunda kapatır ki sonraki modüller (test_core)
    kendi QGuiApplication'ını kurabilsin; önceden kurulmuş bir uygulama varsa onu kullanır."""
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    created = inst is None
    if created:
        inst = QCoreApplication([])
    yield inst
    if created:
        inst.shutdown()


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    """Kuyruğa alınmış sinyaller ve zamanlayıcılar işlensin diye olay döngüsünü döndürür."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    qapp.processEvents()
    return cond()


@pytest.fixture
def no_start(monkeypatch) -> list[str]:
    """Gerçek Ollama hiçbir testte başlatılmasın: ensure_running sahte (başlatamaz), çağrılar listede."""
    calls: list[str] = []
    monkeypatch.setattr(ollama_runtime, "ensure_running", lambda url, timeout=25.0: calls.append(url) or False)
    return calls


# --- yerel yapay zekânın hazırlığı (OllamaTranslator.prepare) ----------------------------------------------------
class _Reply:
    status_code, text = 200, ""

    def __init__(self, data: dict) -> None:
        self._data = data

    def json(self) -> dict:
        return self._data


class _Session:
    """requests.Session yerine: /api/chat'e sabit yanıt verir, istekleri kaydeder."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(url)
        return _Reply({"message": {"content": "Selam."}})

    def close(self) -> None:
        pass


def test_prepare_starts_closed_ollama(monkeypatch, no_start):
    """Ollama kurulu ama kapalıysa (Windows açılışında başlamadı, kapatıldı) hazırlık onu penceresiz başlatır ve
    model listesini yeniden alır; sonra model ısınır."""
    lists = iter([None, ["gemma4:12b"]])
    monkeypatch.setattr(T, "ollama_list", lambda url: next(lists))
    monkeypatch.setattr(ollama_runtime, "ensure_running", lambda url, timeout=25.0: no_start.append(url) or True)
    tr = T.OllamaTranslator(LOCAL, "gemma4:12b", T.Glossary())
    tr._s = _Session()
    statuses: list[str] = []
    tr.prepare(statuses.append)
    assert no_start == [LOCAL] and "Ollama başlatılıyor…" in statuses
    assert len(tr._s.calls) == len(T.WARMUP_LINES)  # başladıktan sonra ısındı


@pytest.mark.parametrize(("started", "lists"), [
    pytest.param(False, [None], id="kurulu değil ya da açılmadı"),
    pytest.param(True, [None, None], id="açıldı dedi ama yanıt yok"),
    pytest.param(OSError("beklenmeyen"), [None], id="başlatıcı hata verdi"),
])
def test_prepare_reports_ollama_down(monkeypatch, started, lists):
    """Ollama'ya yine ulaşılamazsa bilinen hata (TranslatorError): ilk satır kısa ve ne yapılacağını söyler."""
    answers = iter(lists)
    monkeypatch.setattr(T, "ollama_list", lambda url: next(answers))

    def ensure(url, timeout=25.0):
        if isinstance(started, Exception):
            raise started
        return started

    monkeypatch.setattr(ollama_runtime, "ensure_running", ensure)
    tr = T.OllamaTranslator(LOCAL, "gemma4:12b", T.Glossary())
    with pytest.raises(TranslatorError) as ei:
        tr.prepare(lambda _msg: None)
    assert str(ei.value) == f"{T.TEXT_OLLAMA_DOWN}\nAyrıntı: {LOCAL} yanıt vermiyor."
    assert T.TEXT_OLLAMA_DOWN == "Ollama çalışmıyor ve başlatılamadı. Çeviri sayfasındaki Önerilen kurulum'a basın."


@pytest.mark.parametrize(("model", "head"), [
    ("gemma4:12b", "Gemma 4 12B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın."),
    ("gemma4:e4b", "Gemma 4 E4B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın."),
    ("gemma4:26b", "Gemma 4 26B indirilmedi; Çeviri sayfasındaki Modeli indir'e basın."),
    ("qwen3:8b", "'qwen3:8b' modeli indirilmedi; Çeviri sayfasındaki Modeli indir'e basın."),
])
def test_prepare_reports_missing_model(monkeypatch, no_start, model, head):
    """Model yoksa: adı kataloğdan; Önerilen kurulumun kurduğu modellerde o düğme, ötekilerde "Modeli indir"."""
    monkeypatch.setattr(T, "ollama_list", lambda url: ["llama3.2:3b"])
    tr = T.OllamaTranslator(LOCAL, model, T.Glossary())
    with pytest.raises(TranslatorError) as ei:
        tr.prepare(lambda _msg: None)
    assert str(ei.value) == f"{head}\nAyrıntı: '{model}' modeli Ollama'da yüklü değil."
    assert no_start == []  # Ollama açık: başlatılmaya çalışılmaz


def test_model_catalogue_has_one_source():
    """Model adı, başlığı ve boyutu tek kaynakta (ai_setup.MODEL_INFO); Çeviri sayfasının listesi oradan üretilir ve
    görünen metinler değişmedi."""
    assert T.RECOMMENDED_OLLAMA_MODEL == ai_setup.RECOMMENDED_MODEL == "gemma4:12b"
    assert T.LIGHT_OLLAMA_MODEL == ai_setup.LIGHT_MODEL == "gemma4:e4b"
    assert T.MODEL_INFO is ai_setup.MODEL_INFO
    for name, desc in T.OLLAMA_PRESETS:
        info = ai_setup.MODEL_INFO.get(name)
        if info is not None:
            assert desc.startswith(f"{info.title} — ") and f"~{T._size_text(info.size)}," in desc
    assert T.OLLAMA_PRESETS == [
        ("gemma4:12b", "Gemma 4 12B — önerilen: en iyi bağlam ve Türkçe, ~8 GB, 12 GB+ ekran kartı"),
        ("gemma4:e4b", "Gemma 4 E4B — en hızlı, ~6,6 GB, 8 GB ekran kartı ya da yayın + oyun aynı kartta"),
        ("gemma4:26b", "Gemma 4 26B — en yüksek kalite, ~17 GB, 24 GB ekran kartı"),
        ("translategemma:12b", "TranslateGemma 12B — yalnız çeviri modeli; bağlam ve profil kullanmaz"),
    ]


def _stub_handler(seen: list[tuple[str, str]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        """Ollama'nın /api/tags, /api/chat ve /api/generate yanıtları."""

        def do_GET(self) -> None:  # noqa: N802 - http.server
            seen.append(("GET", self.path))
            if self.path == "/api/tags":
                self._json({"models": [{"name": "gemma4:12b"}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - http.server
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append(("POST", self.path))
            if self.path == "/api/chat":
                self._json({"message": {"content": "Selam."}})
            elif self.path == "/api/generate":
                self._json({})
            else:
                self.send_error(404)

        def _json(self, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    return Handler


@pytest.fixture
def ollama_stub():
    """127.0.0.1'de küçük bir sahte Ollama (rastgele boş port): (adres, gelen istekler)."""
    seen: list[tuple[str, str]] = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _stub_handler(seen))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()
    srv.server_close()
    th.join(5)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ollama_requests_ignore_system_proxy(ollama_stub, monkeypatch, no_start):
    """Vekil sunucu (HTTP_PROXY vb.) tanımlı makinede Ollama istekleri (127.0.0.1) vekile gitmez: model listesi,
    ısınma, çeviri ve modeli bellekten boşaltma doğrudan Ollama'ya gider."""
    url, seen = ollama_stub
    proxy = f"http://127.0.0.1:{_closed_port()}"  # kimsenin dinlemediği vekil: oraya giden istek düşer
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, proxy)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    # denetim: requests'in varsayılanı bu istekleri (127.0.0.1 dahil) vekil sunucuya gönderirdi
    assert requests.utils.get_environ_proxies(f"{url}/api/tags").get("http") == proxy
    assert T.ollama_list(url) == ["gemma4:12b"]
    tr = T.OllamaTranslator(url, "gemma4:12b", T.Glossary())
    assert tr._s.trust_env is False
    tr.prepare(lambda _msg: None)
    assert tr.translate("Hey.") == "Selam."
    tr.close()
    assert ("GET", "/api/tags") in seen and seen.count(("POST", "/api/chat")) == len(T.WARMUP_LINES) + 1
    assert ("POST", "/api/generate") in seen and no_start == []


# --- işlem hattı: hazırlık hatası → notice -----------------------------------------------------------------------
class FakeEngine(Translator):
    """Asıl motor gibi (key: "ollama" yerel yapay zekâ, "gemini" çevrimiçi); prepare() verilen hatayı fırlatır."""

    def __init__(self, error: Exception | None = None, key: str = "ollama", model: str = "gemma4:12b") -> None:
        self.key, self.label, self.model = key, f"sahte {key}", model
        self.error = error
        self.closed = False

    def prepare(self, status) -> None:
        if self.error is not None:
            raise self.error

    def translate(self, text, context=()):
        return f"asıl: {text}"

    def close(self) -> None:
        self.closed = True


class FakeQuick(Translator):
    key, label = "quickmt", "sahte hızlı"

    def __init__(self) -> None:
        self.prepared = self.closed = False

    def prepare(self, status) -> None:
        self.prepared = True

    def translate(self, text, context=()):
        return f"hızlı: {text}"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def make_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")  # kullanıcının önbelleğine dokunma

    def make(hybrid: bool = True) -> Pipeline:
        s = Settings()
        s.translate.engine = "ollama"
        s.translate.hybrid = hybrid
        return Pipeline(s)

    return make


def _watch(pipe: Pipeline) -> types.SimpleNamespace:
    out = types.SimpleNamespace(notices=[], errors=[], statuses=[], subs=[])
    pipe.notice.connect(out.notices.append)
    pipe.error.connect(out.errors.append)
    pipe.status.connect(out.statuses.append)
    pipe.subtitle.connect(out.subs.append)
    return out


@pytest.mark.parametrize(("error", "reason"), [
    pytest.param(TranslatorError(T.missing_model_text("gemma4:12b")),
                 "Gemma 4 12B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın.", id="model indirilmemiş"),
    pytest.param(TranslatorError(f"{T.TEXT_OLLAMA_DOWN}\nAyrıntı: {LOCAL} yanıt vermiyor."), T.TEXT_OLLAMA_DOWN,
                 id="Ollama kapalı ve başlatılamadı"),
])
def test_local_ai_prepare_failure_is_a_notice(make_pipe, error, reason):
    """Karma modda yerel yapay zekâ hazırlanamazsa her Başlat'ta uyarı penceresi açılmaz: pencere açmayan bildirim
    (notice: 1. satır ne oldu, 2. satır kısa neden) gider, hat hızlı çeviriyle sürer, uzak motora iş gitmez."""
    pipe = make_pipe()
    main, fast = FakeEngine(error), FakeQuick()
    cx = TrContext(pipe.s.translate, main, fast)
    w = _watch(pipe)
    pipe._run.set()
    pipe._remote_loop(cx)  # uzak iş parçacığının gövdesi: hazırlık hata verir, biter
    assert w.notices == [f"Gemma 4 12B hazırlanamadı; hızlı çeviri kullanılıyor.\n{reason}"]
    assert w.errors == [] and w.statuses == [pl.PREPARING_STATUS, pl.FALLBACK_STATUS]
    assert pipe._run.is_set() and not cx.ready and main.closed
    ev = pipe.tracker.on_text("Get in the car, now.", None, pipe.clock())
    pipe._apply(pipe._translate_item(("sub", ev["t"], ev), cx))
    assert [(s["engine"], s["text"]) for s in w.subs] == [(fast.label, "hızlı: Get in the car, now.")]
    assert len(pipe._remote_q) == 0


@pytest.mark.parametrize(("main", "message"), [
    pytest.param(FakeEngine(RuntimeError("bozuk model")), "Çeviri motoru hazırlanamadı: bozuk model",
                 id="yerel yapay zekâ, beklenmeyen hata"),
    pytest.param(FakeEngine(TranslatorError("Gemini API anahtarı girilmemiş."), key="gemini"),
                 "Gemini API anahtarı girilmemiş.", id="çevrimiçi motor: kullanıcı düzeltmeli"),
])
def test_other_prepare_failures_stay_errors(make_pipe, main, message):
    """Beklenmeyen hatalar ve çevrimiçi motorların ayar hataları eskisi gibi uyarı penceresiyle (error) bildirilir."""
    pipe = make_pipe()
    cx = TrContext(pipe.s.translate, main, FakeQuick())
    w = _watch(pipe)
    pipe._run.set()
    pipe._remote_loop(cx)
    assert w.errors == [message] and w.notices == []
    assert w.statuses == [pl.PREPARING_STATUS, pl.FALLBACK_STATUS]


def test_prepare_failure_while_stopping_is_silent(make_pipe):
    """Durdurulurken yarıda kalan hazırlığın hatası bildirilmez (ne uyarı ne bildirim)."""
    pipe = make_pipe()
    cx = TrContext(pipe.s.translate, FakeEngine(TranslatorError(T.missing_model_text("gemma4:12b"))), FakeQuick())
    w = _watch(pipe)
    pipe._remote_loop(cx)  # _run kurulu değil: hat durduruluyor
    assert w.notices == w.errors == []


def test_without_hybrid_the_fallback_is_a_notice_too(make_pipe, monkeypatch):
    """Karma kapalıyken asıl motor çeviri başlamadan hazırlanır. Yerel yapay zekâ hazırlanamaz ve hızlı çeviri
    hazırsa hat onunla sürer: yine pencere açmayan bildirim."""
    main, fast = FakeEngine(TranslatorError(T.missing_model_text("gemma4:e4b")), model="gemma4:e4b"), FakeQuick()
    monkeypatch.setattr(pl, "create_translator", lambda tc, glossary: main)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda glossary, device="cpu": fast)
    pipe = make_pipe(hybrid=False)
    w = _watch(pipe)
    assert pipe._prepare_translators() == (fast, None)
    assert w.notices == ["Gemma 4 E4B hazırlanamadı; hızlı çeviri kullanılıyor.\n"
                         "Gemma 4 E4B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın."]
    assert w.errors == [] and pl.FALLBACK_STATUS in w.statuses


def test_without_quick_translation_it_is_fatal(make_pipe, monkeypatch):
    """Hızlı çeviri de hazırlanamadıysa hat çeviremez: ölümcül, uyarı penceresiyle (error) bildirilir."""
    main = FakeEngine(TranslatorError(T.missing_model_text("gemma4:12b")))

    def broken_quick(glossary, device="cpu"):
        raise TranslatorError("Hızlı çeviri modeli bozuk.")

    monkeypatch.setattr(pl, "create_translator", lambda tc, glossary: main)
    monkeypatch.setattr(pl, "QuickMtTranslator", broken_quick)
    pipe = make_pipe()
    w = _watch(pipe)
    assert pipe._prepare_translators() == (None, None)
    assert w.notices == []
    assert w.errors == [T.missing_model_text("gemma4:12b"), "Yedek çeviri de hazırlanamadı: Hızlı çeviri modeli bozuk."]


# --- AppContext: bağlar ve bildirimler -----------------------------------------------------------------------------
_log: list[str] = []  # sahte parçaların çağrıları, sırayla


class FakeBridge:
    def start(self) -> None:
        _log.append("bridge.start")

    def submit(self, coro) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result(None)
        return fut

    def stop(self) -> None:
        _log.append("bridge.stop")


class FakeServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.on_clients_changed = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class FakePipeline(QObject):
    """İşlem hattının AppContext'in kullandığı yüzü (sinyaller ve durdurma)."""

    status = Signal(str)
    error = Signal(str)
    notice = Signal(str)
    running_changed = Signal(bool)
    subtitle = Signal(dict)
    same = Signal()
    gone = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.running = False

    def start(self) -> None:
        _log.append("pipeline.start")

    def stop(self) -> None:
        _log.append("pipeline.stop")

    def stop_and_wait(self, timeout: float = 4.0) -> None:
        _log.append("pipeline.stop_and_wait")


class FakeOverlay(QObject):
    geometry_saved = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()

    def start(self) -> None:
        _log.append("overlay.start")

    def stop(self) -> None:
        _log.append("overlay.stop")


class FakeDisplay:
    def __init__(self, settings, bridge, server, overlay) -> None:
        pass

    def on_subtitle(self, ev) -> None:
        pass

    def on_gone(self) -> None:
        pass

    def on_same(self) -> None:
        pass

    def push_style(self) -> None:
        pass


class FakeHotkeys(QObject):
    triggered = Signal(str)

    def register(self, name: str, seq: str) -> None:
        return None

    def unregister_all(self) -> None:
        _log.append("hotkeys.unregister_all")


class FakeAi(QObject):
    """Kurulum servisinin AppContext'in kullandığı yüzü."""

    changed = Signal()
    finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.running: list[bool] = []
        self.resumed = 0
        self.shutdowns = 0

    def on_pipeline_running(self, running: bool) -> None:
        self.running.append(running)

    def resume_pending(self) -> None:
        self.resumed += 1

    def shutdown(self, wait_ms: int = 3000) -> bool:
        self.shutdowns += 1
        _log.append("ai.shutdown")
        return True


@pytest.fixture
def parts(monkeypatch, tmp_path) -> types.SimpleNamespace:
    """AppContext'in ağır parçaları sahte: OBS sunucusu açılmaz, ekran katmanı ve genel kısayollar kurulmaz; ayarlar
    dosyaya yazılmaz, kayıtlar `saved`ta."""
    from altyazitr import config

    _log.clear()
    saved: list[Settings] = []
    monkeypatch.setattr(app, "AsyncBridge", FakeBridge)
    monkeypatch.setattr(app, "OverlayServer", FakeServer)
    monkeypatch.setattr(app, "Pipeline", FakePipeline)
    monkeypatch.setattr(app, "ScreenOverlay", FakeOverlay)
    monkeypatch.setattr(app, "SubtitleDisplay", FakeDisplay)
    monkeypatch.setattr(app, "HotkeyManager", FakeHotkeys)
    monkeypatch.setattr(app, "save_settings", saved.append)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "ayarlar.json")  # yine de: gerçek dosyaya dokunulmasın
    return types.SimpleNamespace(log=_log, saved=saved)


@pytest.fixture
def make_ctx(qapp, parts):
    made: list[app.AppContext] = []

    def factory(*args) -> app.AppContext:
        ctx = app.AppContext(Settings(), *args)
        made.append(ctx)
        return ctx

    yield factory
    for ctx in made:
        ctx.shutdown()


def test_app_context_wires_ai_setup(qapp, parts, make_ctx):
    """Çeviri başlayınca kurulumun indirmesi duraklar, durunca sürer; yarım kalan kurulum açılıştan 3 sn sonra
    kendiliğinden sürer; kapanışta iş işlem hattından önce durur."""
    ai = FakeAi()
    ctx = make_ctx(ai)
    assert ctx.ai is ai
    ctx.pipeline.running_changed.emit(True)
    ctx.pipeline.running_changed.emit(False)
    assert ai.running == [True, False]
    timer = ctx._ai_resume_timer
    assert app.AI_RESUME_DELAY_MS == 3000
    assert timer.isActive() and timer.isSingleShot() and timer.interval() == app.AI_RESUME_DELAY_MS
    qapp.processEvents()
    assert ai.resumed == 0  # açılışta hemen değil
    timer.setInterval(1)  # süre dolmuş gibi
    assert _wait(qapp, lambda: ai.resumed == 1)
    ctx.shutdown()
    assert ai.shutdowns == 1 and parts.saved == [ctx.s]
    assert parts.log.index("ai.shutdown") < parts.log.index("pipeline.stop_and_wait")


def test_shutdown_cancels_pending_resume(qapp, parts, make_ctx):
    ai = FakeAi()
    ctx = make_ctx(ai)
    ctx.shutdown()
    assert not ctx._ai_resume_timer.isActive() and ai.shutdowns == 1
    qapp.processEvents()
    assert ai.resumed == 0


def test_pipeline_notice_reaches_ui_thread(qapp, parts, make_ctx):
    """İşlem hattının notice'i iş parçacığından yayılır; AppContext onu arayüz iş parçacığında notified(kısa metin,
    ayrıntı, "warn") olarak verir (uyarı penceresi yok)."""
    ctx = make_ctx(FakeAi())
    got: list[tuple] = []
    ctx.notified.connect(lambda head, detail, level: got.append(
        (head, detail, level, threading.current_thread() is threading.main_thread())))
    text = ("Gemma 4 12B hazırlanamadı; hızlı çeviri kullanılıyor.\n"
            "Gemma 4 12B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın.")
    th = threading.Thread(target=ctx.pipeline.notice.emit, args=(text,))
    th.start()
    th.join(5)
    assert _wait(qapp, lambda: got)
    assert got == [("Gemma 4 12B hazırlanamadı; hızlı çeviri kullanılıyor.",
                    "Gemma 4 12B indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın.", "warn", True)]


def test_notify_splits_short_text_and_detail(qapp, parts, make_ctx):
    """notify(): 1. satır kısa metin, sonrası ayrıntı ("Ayrıntı: " öneki atılır); boş metin yok sayılır. Arayüz
    yapay zekâ kurulumunun sonucunu da buradan verir."""
    ctx = make_ctx(FakeAi())
    got: list[tuple[str, str, str]] = []
    ctx.notified.connect(lambda *args: got.append(args))
    ctx.notify("Yapay zekâ çevirisi hazır")
    ctx.notify("Ollama kurulamadı.\nAyrıntı: Kurulum 4 koduyla bitti.", "warn")
    ctx.notify("  \n ")
    assert got == [("Yapay zekâ çevirisi hazır", "", "info"),
                   ("Ollama kurulamadı.", "Kurulum 4 koduyla bitti.", "warn")]


def test_app_context_creates_ai_setup_when_not_given(qapp, parts, make_ctx):
    """Araçlar (tools/arayuz_goruntusu.py) AppContext(ayarlar) der: kurulum servisi burada oluşur, ayarları
    AppContext kaydeder; arayüz ctx.ai'ye her zaman güvenebilir."""
    ctx = make_ctx()
    assert isinstance(ctx.ai, ai_setup.AiSetup) and ctx.ai.settings is ctx.s and ctx.ai.state == "idle"
    assert ctx.ai._save_cb == ctx.save_now


# --- açılış sırası ---------------------------------------------------------------------------------------------
def test_open_context_order(monkeypatch):
    """main(): ayarlar → kurulum servisi (ayarı kaydedebilir) → ilk açılış penceresi (servisle) → AppContext (aynı
    servisle)."""
    settings, order, saved = Settings(), [], []

    class Ai:
        def __init__(self, s, save) -> None:
            order.append("servis")
            self.s, self.save = s, save

    monkeypatch.setattr(app, "load_settings", lambda: order.append("ayarlar") or settings)
    monkeypatch.setattr(app, "save_settings", saved.append)
    monkeypatch.setattr(app, "AiSetup", Ai)
    monkeypatch.setattr(app, "_first_setup", lambda s, ai: order.append(("ilk açılış", s, ai)) or True)
    monkeypatch.setattr(app, "AppContext", lambda s, ai: order.append(("AppContext", s, ai)) or "ctx")
    assert app._open_context() == "ctx"
    ai = order[2][2]
    assert order == ["ayarlar", "servis", ("ilk açılış", settings, ai), ("AppContext", settings, ai)]
    assert ai.s is settings
    ai.save()
    assert saved == [settings]


def test_open_context_stops_when_first_setup_is_left(monkeypatch):
    monkeypatch.setattr(app, "load_settings", Settings)
    monkeypatch.setattr(app, "AiSetup", lambda s, save: object())
    monkeypatch.setattr(app, "_first_setup", lambda s, ai: False)
    monkeypatch.setattr(app, "AppContext", lambda s, ai: pytest.fail("ana pencere açılmamalı"))
    assert app._open_context() is None


class StopAi:
    def __init__(self) -> None:
        self.stopped = 0

    def shutdown(self, wait_ms: int = 3000) -> bool:
        self.stopped += 1
        return True


@pytest.fixture
def dialog(monkeypatch) -> types.ModuleType:
    """İlk açılış penceresinin modülü yerine sahte modül: pencere açılmaz (gerçeği gui/setup_dialog.py)."""
    import altyazitr.gui as gui_pkg

    fake = types.ModuleType("altyazitr.gui.setup_dialog")
    fake.calls, fake.result, fake.running = [], True, False

    def run_first_setup(settings, ai=None, parent=None) -> bool:
        fake.calls.append((settings, ai))
        return fake.result

    fake.run_first_setup = run_first_setup
    fake.download_running = lambda: fake.running
    monkeypatch.setitem(sys.modules, "altyazitr.gui.setup_dialog", fake)
    monkeypatch.setattr(gui_pkg, "setup_dialog", fake, raising=False)
    return fake


def test_first_setup_skips_window_when_quick_model_is_present(dialog, monkeypatch):
    """1.0.x / 1.1.0'dan güncelleyen: hızlı çeviri modeli var, ilk açılış penceresi açılmaz, hiçbir şey inmez."""
    monkeypatch.setattr(T, "quickmt_downloaded", lambda: True)
    ai = StopAi()
    assert app._first_setup(Settings(), ai) is True
    assert dialog.calls == [] and ai.stopped == 0


def test_first_setup_opens_window_with_ai_service(dialog, monkeypatch):
    monkeypatch.setattr(T, "quickmt_downloaded", lambda: False)
    settings, ai = Settings(), StopAi()
    assert app._first_setup(settings, ai) is True
    assert dialog.calls == [(settings, ai)] and ai.stopped == 0  # yapay zekâ arka planda sürer


@pytest.mark.parametrize("downloading", [False, True], ids=["indirme bitmişti", "indirme sürüyor"])
def test_first_setup_left_stops_ai_and_exits(dialog, monkeypatch, downloading):
    """Kullanıcı ilk açılıştan çıktı: kurulum servisi durur; hızlı çeviri modeli hâlâ iniyorsa süreç hemen biter
    (snapshot_download yarıda durdurulamıyor)."""
    monkeypatch.setattr(T, "quickmt_downloaded", lambda: False)
    dialog.result, dialog.running = False, downloading
    exits: list[int] = []
    monkeypatch.setattr(app.os, "_exit", exits.append)
    monkeypatch.setattr(app.logging, "shutdown", lambda: None)
    ai = StopAi()
    assert app._first_setup(Settings(), ai) is False
    assert ai.stopped == 1 and exits == ([0] if downloading else [])
