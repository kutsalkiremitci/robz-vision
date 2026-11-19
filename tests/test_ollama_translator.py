"""Yerel yapay zekâ (Ollama) çevirisinin hız ve kararlılık davranışı: yalnız sona eklenen bağlam penceresi, adres,
çıktı sınırı, hazırlığın (modelin belleğe yüklenip ısınması) durdurulabilmesi ve modelin bellekte kalması.

Ağ yok: istekler sahte oturumlarla yakalanır; modelin bellekte olup olmadığı testten ayarlanır."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from altyazitr import translate as tr_mod
from altyazitr.config import TranslateCfg
from altyazitr.translate import (OLLAMA_NUM_CTX, ContextWindow, Glossary, OllamaTranslator, Translator,
                                 TranslatorError, ollama_base, predict_limit)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Her isteğe aynı yanıtı veren oturum; gelen istekler `calls`ta: (adres, gövde)."""

    def __init__(self, payload: dict | None = None):
        self.payload = {"message": {"content": "Selam."}} if payload is None else payload
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        return _FakeResponse(self.payload)

    def close(self) -> None:
        pass


class _SlowSession(_FakeSession):
    """İlk isteği (modelin belleğe yüklenmesi) testin izniyle biten oturum."""

    def __init__(self) -> None:
        super().__init__()
        self.loading = threading.Event()  # ilk istek geldi
        self.loaded = threading.Event()  # yükleme bitsin

    def post(self, url, json=None, timeout=None, headers=None):
        first = not self.calls
        self.calls.append((url, json))
        if first:
            self.loading.set()
            self.loaded.wait(5)
        return _FakeResponse(self.payload)


class _Recorder:
    """_ollama_session yerine: gönderilen istekleri kaydeden oturum (with ile de kullanılır)."""

    def __init__(self, sent: list[tuple[str, dict]]):
        self.sent = sent

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url, json=None, timeout=None, headers=None):
        self.sent.append((url, json))
        return _FakeResponse({})

    def get(self, url, timeout=None):
        self.sent.append((url, {}))
        return _FakeResponse({"models": [{"name": "gemma4:12b"}]})

    def close(self) -> None:
        pass


@pytest.fixture
def local(monkeypatch) -> SimpleNamespace:
    """Ollama açık, modeller indirilmiş. Modelin bellekte olup olmadığı testten ayarlanır (`in_memory`); bellekten
    boşaltma istekleri `sent`te toplanır."""
    state = SimpleNamespace(in_memory=False, sent=[])
    monkeypatch.setattr(tr_mod, "ollama_list", lambda url: ["gemma4:e4b", "gemma4:12b"])
    monkeypatch.setattr(tr_mod, "ollama_loaded", lambda url, model: state.in_memory)
    monkeypatch.setattr(tr_mod, "_ollama_session", lambda: _Recorder(state.sent))
    return state


def _ollama(model: str = "gemma4:12b", session: _FakeSession | None = None, **kw) -> OllamaTranslator:
    t = OllamaTranslator("http://x", model, Glossary(), **kw)
    t._s = session or _FakeSession()
    return t


def _until(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "beklenen olay gelmedi"
        time.sleep(0.005)


def _talk(n: int) -> list[tuple[str, str]]:
    return [(f"Line {i}.", f"Satır {i}.") for i in range(n)]


# --- yalnız sona eklenen bağlam penceresi -------------------------------------------------------------------
def test_context_window_only_appends_between_trims():
    """Hattın kayan penceresi (son 8 satır) her yeni satırda istemin başını değiştirir; ContextWindow aynı satırları
    yalnız sona ekleyerek verir. Kırpılmadıkça her bağlam bir öncekinin devamıdır (Ollama istemin başını önbellekten
    kullanır); pencere sınırı aşınca son satırlara iner. En yeni satır hep bağlamdadır, arada satır atlanmaz."""
    lines = _talk(40)
    win = ContextWindow(8)
    assert (win.keep, win.limit) == (4, 12)
    prev: list[tuple[str, str]] = []
    sizes: list[int] = []
    trims: list[int] = []
    for i in range(1, len(lines) + 1):
        got = win.update(lines[max(0, i - 8):i])  # hattın verdiği: son 8 satır
        assert got == lines[i - len(got):i]  # en yeni satırla biter, ardışıktır
        if got[:len(prev)] != prev:  # önceki bağlamın devamı değil: kırpıldı
            trims.append(i)
            assert len(got) == win.keep
        sizes.append(len(got))
        prev = got
    assert sizes[:13] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 4]
    assert trims == [13, 22, 31, 40]  # 9 satırda bir; kayan pencere 9. satırdan sonra HER satırda değişirdi
    assert min(sizes[12:]) == 4 and max(sizes) == 12


def test_context_window_follows_rereads_and_resets():
    win = ContextWindow(8)
    a, b, c, d = ("A.", "a."), ("B.", "b."), ("C.", "c."), ("D.", "d.")
    assert win.update([a, b]) == [a, b]
    assert win.update([a, b]) == [a, b]  # yeni satır yok: aynı bağlam
    assert win.update([a, b, c]) == [a, b, c]
    c2 = ("C, now.", "c, şimdi.")  # aynı satırın daha temiz okuması: hat eskisini çıkarıp yenisini sona koyar
    assert win.update([a, b, c2]) == [a, b, c2]
    assert win.update([a, c2, d]) == [a, c2, d]  # eski bir satır bağlamdan çıktı: pencere hattı izler
    assert win.update([]) == []  # uzun sessizlik (yeni sahne): hat bağlamı boşaltır, pencere de boşalır
    assert win.update([d]) == [d]
    x, y = ("X.", "x."), ("Y.", "y.")
    assert win.update([x, y]) == [x, y]  # hizalanamayan bağlam pencerenin yerini alır
    assert win.update([("X.", ""), y, a]) == [x, y, a]  # satırlar İngilizcesiyle eşleşir: istemin başı değişmez


@pytest.mark.parametrize(("lines", "keep", "limit"), [(3, 2, 5), (8, 4, 12), (12, 6, 18)])
def test_context_window_size_follows_the_setting(lines, keep, limit):
    """N satırlık ayarda pencere N/2 ile N + N/2 satır arasında gezer."""
    win = ContextWindow(lines)
    assert (win.keep, win.limit) == (keep, limit)
    talk = _talk(60)
    sizes = [len(win.update(talk[max(0, i - lines):i])) for i in range(1, 61)]
    assert max(sizes) == limit and min(sizes[limit:]) == keep


def test_context_window_with_no_or_one_context_line():
    """Bağlam kapalıysa (0) LLM'e önceki satır gitmez. Tek satırlık bağlamda art arda gelen bağlamlar örtüşmez:
    pencere büyüyemez, hattın verdiği satır olduğu gibi gider."""
    talk = _talk(10)
    off = ContextWindow(0)
    assert [off.update(talk[i - 1:i]) for i in range(1, 10)] == [[]] * 9
    one = ContextWindow(1)
    assert [one.update(talk[i - 1:i]) for i in range(1, 10)] == [talk[i - 1:i] for i in range(1, 10)]


def test_prompt_prefix_stays_the_same_between_lines():
    """Art arda satırlarda kullanıcı isteminin başı (bağlam) değişmez, yalnız sonuna satır eklenir. Hattın kayan
    penceresi olduğu gibi gitseydi 9. satırdan sonra her istekte istemin başı değişirdi."""
    t = _ollama("gemma4:e4b", _FakeSession({"message": {"content": "TR: tamam"}}), context_lines=8)
    lines = _talk(13)
    for i in range(1, 13):
        t.translate(lines[i][0], lines[max(0, i - 8):i])
    prompts = [body["messages"][1]["content"] for _url, body in t._s.calls]
    heads = [p.rsplit("Line to translate:", 1)[0] for p in prompts]  # istemin bağlam kısmı
    assert all(later.startswith(earlier) for earlier, later in zip(heads, heads[1:], strict=False))
    assert "EN: Line 0.\nTR: Satır 0." in prompts[-1] and prompts[-1].endswith("EN: Line 12.\nTR:")
    assert all(body["messages"][0]["content"] == t.system for _url, body in t._s.calls)


def test_marked_lines_use_the_window_too():
    """Vurgulu satırın isteği de aynı pencereyi kullanır (iki yol ayrı pencere tutsaydı istemin başı değişirdi)."""
    t = _ollama(session=_FakeSession({"message": {"content": "[[Radar istasyonuna]] sür."}}), context_lines=8)
    lines = _talk(3)
    t.translate(lines[1][0], lines[:1])
    assert t.translate_marked("Drive to the [[radar station]].", lines[:2]) == "[[Radar istasyonuna]] sür."
    first, second = (body["messages"][1]["content"] for _url, body in t._s.calls)
    assert second.startswith(first.rsplit("Line to translate:", 1)[0])


def test_create_translator_passes_context_lines_and_fast_address():
    cfg = TranslateCfg(engine="ollama", ollama_url="http://localhost:11434/", ollama_model="gemma4:12b",
                       context_lines=4)
    t = tr_mod.create_translator(cfg, Glossary())
    try:
        assert isinstance(t, OllamaTranslator)
        assert (t._window.keep, t._window.limit) == (2, 6) and t.url == "http://127.0.0.1:11434"
    finally:
        t.close()


# --- adres, çıktı sınırı ---------------------------------------------------------------------------------
@pytest.mark.parametrize(("url", "base"), [
    ("http://localhost:11434/", "http://127.0.0.1:11434"),
    ("http://LOCALHOST:11434", "http://127.0.0.1:11434"),
    (" http://localhost ", "http://127.0.0.1"),
    ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
    ("http://192.168.1.20:11434", "http://192.168.1.20:11434"),
    ("http://localhost.evim:11434", "http://localhost.evim:11434"),
    ("https://localhost:11434", "https://localhost:11434"),  # sertifika localhost adına olabilir
])
def test_ollama_base_avoids_the_slow_localhost_lookup(url, base):
    """Windows `localhost`u önce IPv6'ya çözer, Ollama yalnız IPv4'te dinler: her yeni bağlantı ~2 sn bekler.
    Adres 127.0.0.1'e çevrilir; başka adreslere dokunulmaz."""
    assert ollama_base(url) == base


def test_ollama_functions_use_the_fast_address(monkeypatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(tr_mod, "_ollama_session", lambda: _Recorder(sent))
    assert tr_mod.ollama_list("http://localhost:11434/") == ["gemma4:12b"]
    assert tr_mod.ollama_loaded("http://localhost:11434", "gemma4:12b")
    assert [url for url, _body in sent] == ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/ps"]
    tr_mod._note_loaded("http://localhost:11434/", "gemma4:12b")
    assert tr_mod._is_loaded_by_us("http://127.0.0.1:11434", "gemma4:12b")


@pytest.mark.parametrize(("text", "limit"), [
    ("Hey.", 36),
    ("Get in the car, we're late.", 56),
    (" ".join(["word"] * 60), tr_mod.OLLAMA_MAX_PREDICT),
])
def test_predict_limit_follows_line_length(text, limit):
    """Çıktı sınırı satırın uzunluğuna göre konur: model sapıtıp uzun yazarsa zayıf makinede saniyeler gitmez."""
    assert predict_limit(text) == limit
    assert _ollama()._body(text, ())["options"]["num_predict"] == limit


def test_translation_cut_at_the_limit_is_requested_again_with_penalty():
    """Çıktı sınırına takılan çeviri (model tekrara girdi) gösterilmez: tekrar cezasıyla ve sınırlı uzunlukla bir
    kez daha istenir; yine takılırsa hata (hat hızlı çeviriyi gösterir)."""

    class Cut(_FakeSession):
        def __init__(self, cuts: int):
            super().__init__()
            self.cuts = cuts

        def post(self, url, json=None, timeout=None, headers=None):
            self.calls.append((url, json["options"]))
            if self.cuts:
                self.cuts -= 1
                return _FakeResponse({"message": {"content": "Arabaya bin bin bin bin"}, "done_reason": "length"})
            return _FakeResponse({"message": {"content": "Arabaya bin, geç kaldık."}, "done_reason": "stop"})

    t = _ollama(session=Cut(1))
    assert t.translate("Get in the car, we're late.") == "Arabaya bin, geç kaldık."
    first, second = (opts for _url, opts in t._s.calls)
    assert first["num_predict"] == 56 and "repeat_penalty" not in first
    assert second["num_predict"] == 112 and second["repeat_penalty"] == tr_mod.OLLAMA_REPEAT_RETRY["repeat_penalty"]
    assert second["num_ctx"] == OLLAMA_NUM_CTX
    t = _ollama(session=Cut(2))
    with pytest.raises(TranslatorError, match="sınırını aştı"):
        t.translate("Get in the car, we're late.")
    assert len(t._s.calls) == 2


def test_repeat_limit_error_is_retried_once_with_penalty():
    """Model aynı belirteci üst üste üretip Ollama isteği keserse (500, "token repeat limit") satır tekrar cezasıyla
    bir kez daha istenir; ikinci deneme de olmazsa hata (hat hızlı çeviriyle sürer)."""

    class Repeat(_FakeSession):
        def __init__(self, fails: int):
            super().__init__()
            self.fails = fails

        def post(self, url, json=None, timeout=None, headers=None):
            self.calls.append((url, json))
            if self.fails:
                self.fails -= 1
                r = _FakeResponse({}, status=500)
                r.text = '{"error":"prediction aborted, token repeat limit reached"}'
                return r
            return _FakeResponse({"message": {"content": "Kaç!"}})

    t = _ollama(session=Repeat(1))
    assert t.translate("Run!") == "Kaç!"
    first, second = (body["options"] for _url, body in t._s.calls)
    assert "repeat_penalty" not in first and second["repeat_penalty"] == tr_mod.OLLAMA_REPEAT_RETRY["repeat_penalty"]
    assert second["num_ctx"] == OLLAMA_NUM_CTX  # model yeniden yüklenmesin
    t = _ollama(session=Repeat(2))
    with pytest.raises(TranslatorError, match="repeat limit"):
        t.translate("Run!")
    assert len(t._s.calls) == 2


def test_translation_that_fits_is_not_requested_twice():
    t = _ollama(session=_FakeSession({"message": {"content": "Selam."}, "done_reason": "stop"}))
    assert t.translate("Hey.") == "Selam."
    assert len(t._s.calls) == 1


# --- hazırlık (ısınma), durdurma, modelin bellekte kalması ----------------------------------------------------
def test_prepare_stops_at_once_when_aborted(local):
    """Model belleğe yüklenirken (dakikayı bulabilir) hat durdurulursa hazırlık hemen biter: Durdur ve yeniden
    Başlat yüklemeyi beklemez. Yükleme Ollama'da sürer; bitince model bu uygulamanın yüklediği olarak kaydedilir
    (kapanışta boşaltılsın), yarım kalan ısınmanın ikinci isteği gönderilmez."""
    s = _SlowSession()
    t = _ollama("gemma4:e4b", s)
    statuses: list[str] = []
    result: list[BaseException | None] = []

    def run() -> None:
        try:
            t.prepare(statuses.append)
            result.append(None)
        except Exception as e:
            result.append(e)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    assert s.loading.wait(3)
    t.abort()
    th.join(3)
    assert not th.is_alive()  # yükleme bitmedi (s.loaded kurulmadı) ama hazırlık döndü
    assert isinstance(result[0], TranslatorError) and "durduruldu" in str(result[0])
    assert statuses == ["Ollama'ya bağlanılıyor…", "Gemma 4 E4B belleğe yükleniyor…"]
    assert not tr_mod._is_loaded_by_us("http://x", "gemma4:e4b")
    s.loaded.set()  # yükleme arkada bitti
    _until(lambda: tr_mod._is_loaded_by_us("http://x", "gemma4:e4b"))
    time.sleep(0.05)
    assert len(s.calls) == 1


def test_prepare_stops_at_once_while_ollama_is_starting(monkeypatch):
    """Ollama kapalıysa açılması ~25 sn sürebilir; o sırada da Durdur beklemez. Bırakılan hazırlık, Ollama arkada
    açılınca modeli belleğe yükletmez ve arayüze artık yazmaz."""
    from altyazitr import ollama_runtime

    starting, opened = threading.Event(), threading.Event()

    def slow_start(url: str) -> bool:
        starting.set()
        opened.wait(5)
        return True

    names = iter([None, ["gemma4:12b"]])  # önce kapalı; başlatılınca model listesi gelir
    monkeypatch.setattr(tr_mod, "ollama_list", lambda url: next(names))
    monkeypatch.setattr(ollama_runtime, "ensure_running", slow_start)
    monkeypatch.setattr(tr_mod, "ollama_loaded", lambda url, model: False)
    t = _ollama()
    statuses: list[str] = []
    result: list[BaseException | None] = []

    def run() -> None:
        try:
            t.prepare(statuses.append)
            result.append(None)
        except Exception as e:
            result.append(e)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    assert starting.wait(3)
    t.abort()
    th.join(3)
    assert not th.is_alive() and isinstance(result[0], TranslatorError)
    assert statuses == ["Ollama'ya bağlanılıyor…", "Ollama başlatılıyor…"]
    opened.set()  # Ollama arkada açıldı
    time.sleep(0.1)
    assert t._s.calls == [] and len(statuses) == 2


def test_abort_before_prepare_is_not_lost(local):
    """Durdur, hazırlık başlamadan gelirse (hızlı çeviri modeli yüklenirken basıldı) kaybolmaz: hazırlık hiç
    başlamaz, Ollama'ya istek gitmez, model boşuna belleğe yüklenmez."""
    t = _ollama()
    t.abort()
    with pytest.raises(TranslatorError, match="durduruldu"):
        t.prepare(lambda _msg: None)
    assert t._s.calls == [] and local.sent == []


def test_abandoned_load_of_an_unwanted_model_is_released(local):
    """Model belleğe yüklenirken hat durdurulup başka modele geçildi: yarıda bırakılan yükleme Ollama'da bittiğinde
    o model hemen boşaltılır (iki model ekran kartı belleğini birlikte tutmasın)."""
    s = _SlowSession()
    old = _ollama("gemma4:12b", s)
    th = threading.Thread(target=lambda: pytest.raises(TranslatorError, old.prepare, lambda _m: None), daemon=True)
    th.start()
    assert s.loading.wait(3)
    old.abort()
    th.join(3)
    new = _ollama("gemma4:e4b")
    new.prepare(lambda _msg: None)  # yeni model istendi
    assert local.sent == []
    s.loaded.set()  # eski yükleme arkada bitti
    _until(lambda: local.sent)
    assert local.sent == [("http://x/api/generate", {"model": "gemma4:12b", "keep_alive": 0})]
    assert tr_mod._is_loaded_by_us("http://x", "gemma4:e4b") and not tr_mod._is_loaded_by_us("http://x", "gemma4:12b")


def test_abandoned_load_of_the_wanted_model_is_kept(local):
    """Durdurulan yükleme bittiğinde model hâlâ isteniyorsa (yeniden Başlat aynı modelle) bellekte kalır."""
    s = _SlowSession()
    t = _ollama("gemma4:12b", s)
    th = threading.Thread(target=lambda: pytest.raises(TranslatorError, t.prepare, lambda _m: None), daemon=True)
    th.start()
    assert s.loading.wait(3)
    t.abort()
    th.join(3)
    s.loaded.set()
    _until(lambda: tr_mod._is_loaded_by_us("http://x", "gemma4:12b"))
    time.sleep(0.05)
    assert local.sent == []


def test_switching_to_another_engine_frees_the_model(local, tmp_path, monkeypatch):
    """Yerel yapay zekâdan başka bir motora geçilince model bellekten çıkarılır."""
    from altyazitr import pipeline as pl
    from altyazitr.config import Settings

    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")
    t = _ollama()
    t.prepare(lambda _msg: None)
    t.close()
    s = Settings()
    s.translate.engine = "quickmt"
    pipe = pl.Pipeline(s)
    monkeypatch.setattr(pl, "create_translator", lambda tc, glossary: Translator())
    pipe._prepare_translators()
    assert ("http://x/api/generate", {"model": "gemma4:12b", "keep_alive": 0}) in local.sent


def test_healthy_means_the_model_is_in_memory(local):
    t = _ollama()
    assert not t.healthy()
    local.in_memory = True
    assert t.healthy()


def test_translation_marks_the_model_as_used(local):
    """Isınma başarısız olsa da sonraki çeviri yanıt verdiyse model kayda girer: kapanışta boşaltılsın."""
    t = _ollama()
    assert t.translate("Hey.") == "Selam."
    assert tr_mod._is_loaded_by_us("http://x", "gemma4:12b")


def test_prepare_says_warming_up_when_the_model_is_in_memory(local):
    """Model önceki çalıştırmadan bellekteyse "belleğe yükleniyor" değil "ısınıyor" denir (~1 sn sürer)."""
    local.in_memory = True
    t = _ollama()
    statuses: list[str] = []
    t.prepare(statuses.append)
    assert statuses == ["Ollama'ya bağlanılıyor…", "Gemma 4 12B ısınıyor…"]
    assert tr_mod._is_loaded_by_us("http://x", "gemma4:12b")


def test_prepare_reports_elapsed_seconds_while_loading(local, monkeypatch):
    """Uzun yüklemede durum satırı geçen süreyle yenilenir: kullanıcı hattın takılmadığını görür."""
    ticks = iter(range(100_000))
    monkeypatch.setattr(tr_mod, "time", SimpleNamespace(monotonic=lambda: float(next(ticks))))  # her okuma +1 sn
    s = _SlowSession()
    t = _ollama("gemma4:12b", s)
    statuses: list[str] = []
    th = threading.Thread(target=t.prepare, args=(statuses.append,), daemon=True)
    th.start()
    _until(lambda: len(statuses) >= 4)
    s.loaded.set()
    th.join(3)
    assert not th.is_alive()
    assert statuses[:2] == ["Ollama'ya bağlanılıyor…", "Gemma 4 12B belleğe yükleniyor…"]
    assert statuses[2].startswith("Gemma 4 12B belleğe yükleniyor… ") and statuses[2].endswith(" sn")
    assert len(set(statuses)) == len(statuses)  # aynı saniye iki kez yazılmaz


def test_warm_up_failure_does_not_fail_prepare(local):
    """Isınma isteği hata verirse hazırlık yine biter: çeviri denenir, hata orada görünür (hat hızlı çeviriye düşer)."""

    class Broken(_FakeSession):
        def post(self, url, json=None, timeout=None, headers=None):
            self.calls.append((url, json))
            return _FakeResponse({}, status=500)

    t = _ollama(session=Broken())
    t.prepare(lambda _msg: None)
    assert len(t._s.calls) == 1 and not tr_mod._is_loaded_by_us("http://x", "gemma4:12b")


def test_close_keeps_the_model_in_memory(local):
    """Hat durunca model bellekten atılmaz (yeniden Başlat ve ayar değişikliği onu baştan yüklemesin; 12B'de
    5-100 sn): yalnız bellekte kalma süresi kısalır. Uygulama kapanırken release_ollama_models boşaltır."""
    t = _ollama()
    s = t._s
    t.prepare(lambda _msg: None)
    local.in_memory = True
    t.close()
    assert s.calls[-1] == ("http://x/api/generate", {
        "model": "gemma4:12b", "keep_alive": tr_mod.OLLAMA_IDLE_KEEP_ALIVE, "options": {"num_ctx": OLLAMA_NUM_CTX}})
    assert all(body.get("keep_alive") != 0 for _url, body in s.calls) and local.sent == []
    tr_mod.release_ollama_models()  # uygulama kapanıyor
    assert local.sent == [("http://x/api/generate", {"model": "gemma4:12b", "keep_alive": 0})]
    tr_mod.release_ollama_models()
    assert len(local.sent) == 1  # bir kez boşaltılır


def test_keep_warm_renews_the_time_in_memory(local):
    """Uzun sessizlikte hat modelin bellekte kalma süresini yeniler (çeviri istekleriyle aynı süre ve num_ctx:
    model yeniden yüklenmez). Model bu uygulamaca yüklenmediyse istek gitmez."""
    t = _ollama()
    t.keep_warm()
    assert t._s.calls == []
    t.prepare(lambda _msg: None)
    n = len(t._s.calls)
    t.keep_warm()
    assert t._s.calls[n:] == [("http://x/api/generate", {
        "model": "gemma4:12b", "keep_alive": tr_mod.OLLAMA_KEEP_ALIVE, "options": {"num_ctx": OLLAMA_NUM_CTX}})]


def test_close_after_the_model_expired_sends_nothing(local):
    """Model bu arada bellekten düştüyse (süresi doldu, Ollama yeniden başladı) kapanışta süre isteği gitmez: istek
    modeli boşuna yeniden yükletirdi."""
    t = _ollama()
    t.prepare(lambda _msg: None)
    n = len(t._s.calls)
    local.in_memory = False
    t.close()
    assert len(t._s.calls) == n


def test_close_without_a_loaded_model_sends_nothing(local):
    """Model bu uygulamaca yüklenmediyse (hazırlık yapılmadı ya da hata verdi) kapanışta Ollama'ya istek gitmez:
    süre isteği, yüklü olmayan modeli boşuna yükletirdi."""
    t = _ollama()
    t.close()
    assert t._s.calls == [] and local.sent == []


def test_switching_models_frees_the_previous_one(local):
    """Başka modele geçilince önceki model bellekten çıkarılır (ekran kartı belleğini ikisi birden tutmasın);
    aynı modelle yeniden başlamak hiçbir şeyi boşaltmaz."""
    a = _ollama("gemma4:12b")
    a.prepare(lambda _msg: None)
    a.close()
    again = _ollama("gemma4:12b")
    again.prepare(lambda _msg: None)
    again.close()
    assert local.sent == []
    b = _ollama("gemma4:e4b")
    b.prepare(lambda _msg: None)
    assert local.sent == [("http://x/api/generate", {"model": "gemma4:12b", "keep_alive": 0})]
    assert tr_mod._is_loaded_by_us("http://x", "gemma4:e4b") and not tr_mod._is_loaded_by_us("http://x", "gemma4:12b")
