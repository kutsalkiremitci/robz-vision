"""Karma mod: asıl çeviri için bekleme penceresi (hybrid_wait_ms), asıl motorun arka planda hazırlanması ve LLM
bağlamı.

Çoğu test tek iş parçacığında ve sahte saatle çalışır: çeviri döngüsünün adımları (yeni okuma, işaret, süre dolumu
için uyanma) ve uzak çeviri iş parçacığının adımları elle, istenen sırayla çalıştırılır. Böylece "asıl çeviri pencere
içinde geldi / gecikti / hata verdi" durumları zamanlamaya bağlı olmadan denenir. Arka planda hazırlık gerçek iş
parçacıklarıyla denenir; orada da süre ölçülmez, yalnız olayların sırası denetlenir.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt

from altyazitr import pipeline as pl
from altyazitr.config import Settings
from altyazitr.pipeline import CONTEXT_RESET_S, Pipeline, TrContext
from altyazitr.textproc import norm_key
from altyazitr.translate import Translator, TranslatorError

MAIN, QUICK = "sahte asıl", "sahte hızlı"


class FakeTr(Translator):
    """Sahte çeviri motoru: '<anahtar>: <metin>' döndürür ve her metin için aldığı bağlamı kaydeder. `fail`
    içindeki metinlerde hata verir; `prepare_error` verilirse prepare() onu fırlatır."""

    def __init__(self, key: str, fail: tuple[str, ...] = (), prepare_error: Exception | None = None):
        self.key, self.label = key, f"sahte {key}"
        self.fail = set(fail)
        self.prepare_error = prepare_error
        self.contexts: dict[str, list[tuple[str, str]]] = {}
        self.prepared = self.closed = False

    def prepare(self, status) -> None:
        self.prepared = True
        if self.prepare_error is not None:
            raise self.prepare_error

    def translate(self, text, context=()):
        self.contexts[text] = list(context)
        if text in self.fail:
            raise TranslatorError("bağlantı koptu")
        return f"{self.key}: {text}"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def make_pipe(tmp_path, monkeypatch) -> Callable[..., Pipeline]:
    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")  # kullanıcının önbelleğine dokunma

    def make(hybrid: bool = True) -> Pipeline:
        s = Settings()
        s.translate.engine = "ollama"
        s.translate.hybrid = hybrid
        return Pipeline(s)

    return make


class Hybrid:
    """Karma mod, tek iş parçacığında ve sahte saatle. read / marker / wait çeviri döngüsünün, remote uzak çeviri
    iş parçacığının bir adımıdır. Arayüze gidenler `out`ta: (motor, orijinal, satır) ya da ("gone",) / ("same",)."""

    def __init__(self, pipe: Pipeline, main: FakeTr | None = None, wait_ms: int = 1200):
        self.pipe = pipe
        self.now = 1000.0
        pipe.clock = lambda: self.now
        pipe.s.translate.hybrid_wait_ms = wait_ms
        self.main = main or FakeTr("asıl")
        self.fast = FakeTr("hızlı")
        self.cx = TrContext(pipe.s.translate, self.main, self.fast)
        assert self.cx.two_stage and not self.cx.ready
        self.cx.set_ready()  # asıl motor hazır (hazırlık sırasındaki davranış ayrı testlerde)
        self.out: list[tuple] = []
        self.subs: list[dict] = []
        pipe.subtitle.connect(self._on_sub)
        pipe.gone.connect(lambda: self.out.append(("gone",)))
        pipe.same.connect(lambda: self.out.append(("same",)))

    def _on_sub(self, ev: dict) -> None:
        self.subs.append(ev)
        self.out.append((ev["engine"], ev["original"], ev["line"]))

    def read(self, text: str, score: float = 1.0) -> dict:
        """Oyunda okunan satır (OCR'ın kelime puanıyla) çeviri döngüsüne gelir."""
        ev = self.pipe.tracker.on_text(text, None, self.now, score)
        assert ev is not None, "takipçi bu okumayı aynı satırın titremesi saydı"
        self.pipe._apply(self.pipe._translate_item(("sub", self.now, ev), self.cx))
        return ev

    def marker(self, kind: str) -> None:
        self.pipe._apply(self.pipe._translate_item((kind, self.now, None), self.cx))

    def remote(self) -> None:
        """Uzak çeviri iş parçacığı kuyruğundaki ilk işi bitirir."""
        self.pipe._apply(self.pipe._remote_item(self.pipe._remote_q.get(0), self.cx))

    def wait(self, s: float) -> None:
        """Saat ilerler, çeviri döngüsü uyanır: süresi dolan satırlar gösterilir."""
        self.now += s
        self.pipe._apply([])

    def cache(self, text: str, tr: str) -> None:
        """Asıl motorun önceki bir çevirisi önbellekte."""
        self.pipe.cache.put(self.cx.cache_key({"key": norm_key(text)}), tr)


# --- bekleme penceresi ------------------------------------------------------------------------------
def test_main_in_time_only_main_is_shown(make_pipe):
    """(a) Asıl çeviri pencere içinde gelirse yalnız o, normal bir yeni satır olarak gösterilir; hızlı çeviri hiç
    gösterilmez (süre sonra dolsa da)."""
    h = Hybrid(make_pipe())
    a = h.read("Get in the car, now.")
    h.wait(0.6)
    assert h.out == []
    h.remote()
    h.wait(2.0)
    assert h.out == [(MAIN, a["text"], a["line"])]
    assert not h.subs[0]["cached"] and h.subs[0]["latency_ms"] == pytest.approx(600)


def test_late_main_quick_at_deadline_then_upgrade(make_pipe):
    """(b) Asıl çeviri gecikirse süre dolunca hızlı çeviri gösterilir; asıl çeviri gelince aynı satırı günceller."""
    h = Hybrid(make_pipe())
    a = h.read("Where are we going?")
    h.wait(1.19)
    assert h.out == []
    h.wait(0.02)
    assert h.out == [(QUICK, a["text"], a["line"])]
    assert h.subs[0]["latency_ms"] == pytest.approx(1210)
    h.remote()
    assert h.out == [(QUICK, a["text"], a["line"]), (MAIN, a["text"], a["line"])]


def test_main_error_shows_quick_at_once(make_pipe):
    """(c) Asıl motor hata verirse hızlı çeviri sürenin dolmasını beklemeden gösterilir; satır kaybolmaz."""
    h = Hybrid(make_pipe(), main=FakeTr("asıl", fail=("Just drive.",)))
    statuses: list[str] = []
    h.pipe.status.connect(statuses.append)
    a = h.read("Just drive.")
    h.wait(0.3)
    h.remote()
    assert h.out == [(QUICK, a["text"], a["line"])]
    assert statuses == ["Yedek yerel çeviri kullanıldı (bağlantı koptu)"]
    h.wait(2.0)
    assert len(h.out) == 1


def test_zero_wait_is_previous_behavior(make_pipe):
    """(d) hybrid_wait_ms = 0: hızlı çeviri hemen gösterilir, asıl çeviri gelince aynı satırı günceller."""
    h = Hybrid(make_pipe(), wait_ms=0)
    a = h.read("Hold on tight!")
    assert h.out == [(QUICK, a["text"], a["line"])]
    h.remote()
    assert h.out == [(QUICK, a["text"], a["line"]), (MAIN, a["text"], a["line"])]


def test_gone_and_same_do_not_overtake_waiting_line(make_pipe):
    """(e) 'gone' ve 'same' bekleyen satırın altyazısını sollamaz: oyun altyazıyı kaldırsa da çeviri önce gösterilir
    (asıl çeviri vaktinde geldiyse o, gelmediyse süre dolunca hızlı çeviri), işaretler ardından gelir."""
    h = Hybrid(make_pipe())
    a = h.read("Take the next exit.")
    h.marker("same")
    h.wait(0.2)
    h.marker("gone")
    assert h.out == []
    h.remote()
    assert h.out == [(MAIN, a["text"], a["line"]), ("same",), ("gone",)]

    b = h.read("I see them behind us!")
    h.marker("gone")
    h.wait(1.3)
    assert h.out[3:] == [(QUICK, b["text"], b["line"]), ("gone",)]
    h.remote()  # aynı okumanın daha iyi çevirisi: oyunda yok ama ekrandaki satırı günceller
    assert h.out[5:] == [(MAIN, b["text"], b["line"])]

    h.marker("same")  # bekleyen satır yokken işaretler hemen geçer
    assert h.out[6:] == [("same",)]


def test_lines_and_cached_lines_keep_order_behind_waiting_line(make_pipe):
    """Önünde bekleyen yoksa önbellekteki satır anında gösterilir. Bekleyen satırdan sonra gelen satır, önbellekten
    gelse de onu sollamaz: satırlar ekrana sırayla çıkar."""
    h = Hybrid(make_pipe())
    h.cache("Okay, we lost them.", "Tamam, onları kaybettik.")
    h.cache("Breathe.", "Nefes al.")
    x = h.read("Okay, we lost them.")
    assert h.out == [(MAIN, x["text"], x["line"])] and h.subs[0]["cached"]
    h.remote()  # önbellekten gelen satır yalnız LLM bağlamına eklenir
    a = h.read("Who are those guys?")
    h.wait(0.3)
    b = h.read("Keep your head down.")
    h.wait(0.1)
    y = h.read("Breathe.")
    h.wait(0.2)
    h.remote()
    assert h.out[1:] == [(MAIN, a["text"], a["line"])]
    h.wait(0.4)
    h.remote()
    assert h.out[2:] == [(MAIN, b["text"], b["line"]), (MAIN, y["text"], y["line"])]
    assert h.subs[-1]["cached"]


def test_newer_reading_replaces_waiting_one(make_pipe):
    """Aynı satırın uzamış / daha temiz okuması gelince henüz gösterilmemiş eski okuma sıradan çıkar: satır yeni
    okumayla, en geç ilk okumasının süresi dolunca görünür. Eski okuma hiç çevrilmez."""
    h = Hybrid(make_pipe())
    s1 = h.read("They're watching us.")
    h.wait(0.4)
    s2 = h.read("They're watching us. We need to move, now!")
    assert s2["line"] == s1["line"]
    h.remote()  # s1'in işi: aynı satırın yenisi sırada, atlanır
    h.wait(0.79)
    assert h.out == []
    h.wait(0.02)  # ilk okumanın süresi doldu (yeni okumanınki dolmadı)
    assert h.out == [(QUICK, s2["text"], s2["line"])]
    h.remote()
    assert h.out[1:] == [(MAIN, s2["text"], s2["line"])]
    assert s1["text"] not in h.main.contexts


def test_late_answer_for_replaced_reading_is_dropped(make_pipe):
    """Eski okuma çevrilirken yeni okuması gelirse eski okumanın çevirisi gösterilmez; satır yeni okumanın
    çevirisiyle görünür."""
    h = Hybrid(make_pipe())
    s1 = h.read("Get in the car.")
    late = h.pipe._remote_item(h.pipe._remote_q.get(0), h.cx)  # uzak motor s1'i çeviriyor...
    h.wait(0.3)
    s2 = h.read("Get in the car. We're going to Vice City.")  # ...bu sırada metin uzadı (daktilo)
    assert s2["line"] == s1["line"]
    h.pipe._apply(late)
    assert h.out == []
    h.wait(0.3)
    h.remote()
    assert h.out == [(MAIN, s2["text"], s2["line"])]


def test_remote_skip_shows_quick_at_once(make_pipe):
    """Uzak motor geride kalınca ekrandan çıkacak satırları atlar; atlanan satır sürenin dolmasını beklemeden hızlı
    çevirisiyle gösterilir, sonrakiler sırayla gelir. Hiçbir satır kaybolmaz."""
    h = Hybrid(make_pipe())  # akış: ekranda 3 satır
    evs = []
    for text in ("Go, go, go!", "Get down!", "They're shooting at us!", "Drive, Jason, drive!"):
        evs.append(h.read(text))
        h.wait(0.1)
    h.remote()  # ilk satırdan sonra 3 yeni satır geldi: atlanır
    assert h.out == [(QUICK, evs[0]["text"], evs[0]["line"])]
    for _ in range(3):
        h.remote()
    assert h.out[1:] == [(MAIN, ev["text"], ev["line"]) for ev in evs[1:]]


def test_shortened_window_keeps_order(make_pipe):
    """Pencere çalışırken kısaltılırsa süresi önce dolan yeni satır, öndeki satırı da beklemekten kurtarır; satırlar
    yine sırayla ve en geç kendi süreleri dolunca görünür."""
    h = Hybrid(make_pipe())
    a = h.read("Get in the car, now.")
    h.pipe.s.translate.hybrid_wait_ms = 200
    h.wait(0.1)
    b = h.read("Where are we going?")
    h.wait(0.19)
    assert h.out == []
    h.wait(0.02)
    assert h.out == [(QUICK, a["text"], a["line"]), (QUICK, b["text"], b["line"])]


def test_translate_loop_wakes_at_deadline(make_pipe):
    """Çeviri döngüsü kuyrukta en çok bir sonraki süre dolumuna kadar bekler (ayrı zamanlayıcı yok)."""
    h = Hybrid(make_pipe())
    assert h.pipe._until_due() == 0.3
    h.read("Hold on tight!")
    assert h.pipe._until_due() == 0.3
    h.now += 1.1
    assert h.pipe._until_due() == pytest.approx(0.1)
    h.now += 0.2
    assert h.pipe._until_due() == 0.0


# --- LLM bağlamı -------------------------------------------------------------------------------------
def test_context_has_blank_turkish_for_quick_fallback(make_pipe):
    """(f) Karma mod: LLM bağlamına asıl motorun çevirisi (önbellekten gelen dahil) girer. Asıl çevirisi olmayan
    satır (hata, ekrandan çıktığı için atlandı) bağlamda yalnız İngilizcesiyle durur: tr="", hızlı çeviri girmez."""
    h = Hybrid(make_pipe(), main=FakeTr("asıl", fail=("Who are those guys?",)))
    h.cache("Cops. Keep your head down.", "Polis. Başını eğ.")
    for text in ("Get in the car, now.", "Who are those guys?", "Cops. Keep your head down."):
        h.read(text)
        h.remote()
    for text in ("Hold on tight!", "Take the next exit.", "I see them behind us!", "Okay, we lost them."):
        h.read(text)
    h.remote()  # "Hold on tight!": 3 yeni satır geldi, atlanır
    h.remote()
    assert h.main.contexts["Take the next exit."] == [
        ("Get in the car, now.", "asıl: Get in the car, now."),
        ("Who are those guys?", ""),
        ("Cops. Keep your head down.", "Polis. Başını eğ."),
        ("Hold on tight!", ""),
    ]


def test_context_blank_for_fallback_when_hybrid_off(make_pipe):
    """(f) Karma kapalı: asıl motor hata verince gösterilen yedek hızlı çeviri de bağlama girmez."""
    pipe = make_pipe(hybrid=False)
    main = FakeTr("asıl", fail=("Who are those guys?",))
    cx = TrContext(pipe.s.translate, main, FakeTr("hızlı"))
    assert not cx.two_stage and cx.ready
    got: list[dict] = []
    pipe.subtitle.connect(got.append)
    for k, text in enumerate(("Get in the car, now.", "Who are those guys?", "Hold on tight!")):
        ev = pipe.tracker.on_text(text, None, 1000.0 + k)
        pipe._apply(pipe._translate_item(("sub", ev["t"], ev), cx))
    assert [g["engine"] for g in got] == [MAIN, QUICK, MAIN]
    assert main.contexts["Hold on tight!"] == [("Get in the car, now.", "asıl: Get in the car, now."),
                                               ("Who are those guys?", "")]


def test_context_survives_quiet_scene_then_resets(make_pipe):
    """(g) Bağlam 90 sn'lik sessizlikte silinmez, daha uzununda silinir (yeni sahne). Varsayılan bağlam 8 satır."""
    pipe = make_pipe(hybrid=False)
    main = FakeTr("asıl")
    cx = TrContext(pipe.s.translate, main, None)

    def say(text: str, t: float) -> list[tuple[str, str]]:
        ev = pipe.tracker.on_text(text, None, t)
        pipe._apply(pipe._translate_item(("sub", t, ev), cx))
        return main.contexts[text]

    say("Get in the car, now.", 1000.0)
    assert say("Where are we going?", 1089.0) == [("Get in the car, now.", "asıl: Get in the car, now.")]
    assert say("Just drive.", 1089.0 + 91.0) == []
    assert CONTEXT_RESET_S == 90.0

    lines = ["Who are those guys?", "Cops. Keep your head down.", "Hold on tight!", "Take the next exit.",
             "I see them behind us!", "We need to lose them.", "Turn left at the bridge.", "Okay, we lost them.",
             "Breathe, Jason.", "Where do we go now?"]
    for k, text in enumerate(lines):
        say(text, 1200.0 + k)
    ctx = say("Head south to Leonida Keys.", 1211.0)
    assert len(ctx) == pipe.s.translate.context_lines == 8
    assert [en for en, _ in ctx] == lines[2:]


# --- asıl motorun arka planda hazırlanması --------------------------------------------------------------
class SlowLlm(FakeTr):
    """Hazırlığı (model yükleme + ısınma) testin izniyle biten, `slow` içindeki metinleri de izinle çeviren LLM."""

    def __init__(self):
        super().__init__("llm")
        self.prepared_evt = threading.Event()
        self.release = threading.Event()
        self.slow: set[str] = set()
        self.prepare_thread = ""

    def prepare(self, status) -> None:
        self.prepare_thread = threading.current_thread().name
        status("sahte model yükleniyor…")
        if not self.prepared_evt.wait(5):
            raise TranslatorError("hazırlık zaman aşımı")
        self.prepared = True

    def translate(self, text, context=()):
        if text in self.slow and not self.release.wait(5):
            raise TranslatorError("zaman aşımı")
        return super().translate(text, context)


def _wait_for(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "beklenen olay gelmedi"
        time.sleep(0.005)


def test_main_prepares_in_background_quick_meanwhile(make_pipe, monkeypatch):
    """Karma modda asıl motorun hazırlığı (yerel LLM'de dakikayı bulabilir) uzak iş parçacığında yapılır. Hazır olana
    kadar satırlar pencereyi beklemeden hızlı çeviriyle gösterilir; hazır olunca asıl çeviri pencere içinde
    gelirse yalnız o gösterilir, gecikirse süre dolunca hızlı çeviri ve ardından güncelleme gelir."""
    llm, fast = SlowLlm(), FakeTr("hızlı")
    monkeypatch.setattr(pl, "create_translator", lambda tc, glossary: llm)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda glossary, device="cpu": fast)
    pipe = make_pipe()
    pipe.s.translate.hybrid_wait_ms = 60_000  # hazırlık sırasında beklenseydi satır gelmezdi
    got: list[tuple[float, dict]] = []
    statuses: list[str] = []
    pipe.subtitle.connect(lambda ev: got.append((time.monotonic(), ev)), Qt.ConnectionType.DirectConnection)
    pipe.status.connect(statuses.append, Qt.ConnectionType.DirectConnection)

    def say(text: str) -> float:
        t = time.monotonic()
        pipe._tr_q.put(("sub", t, pipe.tracker.on_text(text, None, t)))
        return t

    pipe._run.set()
    loop = threading.Thread(target=pipe._translate_loop, daemon=True)
    loop.start()
    try:
        say("Get in the car, now.")
        _wait_for(lambda: len(got) == 1)
        assert got[0][1]["engine"] == fast.label and not llm.prepared
        llm.prepared_evt.set()
        _wait_for(lambda: pl.READY_STATUS in statuses)
        say("Where are we going?")
        _wait_for(lambda: len(got) == 2)
        assert (got[1][1]["engine"], got[1][1]["original"]) == (llm.label, "Where are we going?")

        pipe.s.translate.hybrid_wait_ms = 150
        llm.slow.add("Just drive.")
        t = say("Just drive.")
        _wait_for(lambda: len(got) == 3)
        assert got[2][1]["engine"] == fast.label and got[2][0] >= t + 0.15
        llm.release.set()
        _wait_for(lambda: len(got) == 4)
        assert got[3][1]["engine"] == llm.label and got[3][1]["line"] == got[2][1]["line"]
    finally:
        pipe.stop()
        llm.prepared_evt.set()
        llm.release.set()
        loop.join(5)
    assert not loop.is_alive()
    assert not any(th.name == "ceviri-uzak" and th.is_alive() for th in threading.enumerate())
    assert llm.prepare_thread == "ceviri-uzak" and llm.closed and fast.closed
    assert statuses.index(pl.PREPARING_STATUS) < statuses.index("sahte model yükleniyor…") < statuses.index(
        pl.READY_STATUS)


@pytest.mark.parametrize(("exc", "message"), [
    (TranslatorError("Ollama'ya ulaşılamadı."), "Ollama'ya ulaşılamadı."),
    (RuntimeError("bozuk model"), "Çeviri motoru hazırlanamadı: bozuk model"),
], ids=["bilinen hata", "beklenmeyen hata"])
def test_prepare_failure_reports_error_and_keeps_quick(make_pipe, exc, message):
    """Asıl motor hazırlanamazsa kullanıcıya hata bildirilir; hat durmaz, satırlar beklemeden hızlı çeviriyle
    gösterilir, uzak motora iş gitmez."""
    pipe = make_pipe()
    main, fast = FakeTr("asıl", prepare_error=exc), FakeTr("hızlı")
    cx = TrContext(pipe.s.translate, main, fast)
    errors: list[str] = []
    statuses: list[str] = []
    got: list[dict] = []
    pipe.error.connect(errors.append)
    pipe.status.connect(statuses.append)
    pipe.subtitle.connect(got.append)
    pipe._run.set()
    pipe._remote_loop(cx)  # uzak iş parçacığının gövdesi: hazırlık hata verir, biter
    assert errors == [message] and statuses == [pl.PREPARING_STATUS, pl.FALLBACK_STATUS]
    assert pipe._run.is_set() and not cx.ready and main.closed

    pipe.s.translate.hybrid_wait_ms = 60_000
    ev = pipe.tracker.on_text("Get in the car, now.", None, pipe.clock())
    pipe._apply(pipe._translate_item(("sub", ev["t"], ev), cx))
    assert [(g["engine"], g["original"]) for g in got] == [(QUICK, ev["text"])]
    assert len(pipe._remote_q) == 0


def test_prepare_failure_while_stopping_is_silent(make_pipe):
    """Durdurulurken yarıda kalan hazırlığın hatası kullanıcıya gösterilmez."""
    pipe = make_pipe()
    main = FakeTr("asıl", prepare_error=TranslatorError("bağlantı kapandı"))
    cx = TrContext(pipe.s.translate, main, FakeTr("hızlı"))
    errors: list[str] = []
    pipe.error.connect(errors.append)
    pipe._remote_loop(cx)  # _run kurulu değil: hat durduruluyor
    assert errors == [] and main.closed


@pytest.mark.parametrize("hybrid", [True, False], ids=["karma", "karma kapalı"])
def test_prepare_translators(make_pipe, monkeypatch, hybrid):
    """Karma modda asıl motor çeviri döngüsünü bekletmesin diye burada hazırlanmaz (uzak iş parçacığı hazırlar);
    karma kapalıysa davranış aynı: önceden hazırlanır. İki motor da oyun profilinin adlarını içeren sözlüğü alır."""
    glossary = object()
    main, fast = FakeTr("asıl"), FakeTr("hızlı")
    seen: list[object] = []
    monkeypatch.setattr(pl, "build_glossary", lambda tc: glossary)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda g, device="cpu": seen.append(g) or fast)
    monkeypatch.setattr(pl, "create_translator", lambda tc, g: seen.append(g) or main)
    pipe = make_pipe(hybrid)
    assert pipe._prepare_translators() == (main, fast)
    assert seen == [glossary, glossary]
    assert fast.prepared and main.prepared is not hybrid
