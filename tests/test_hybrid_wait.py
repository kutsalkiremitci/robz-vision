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


def test_hybrid_off_waits_for_main_and_keeps_fallback_out_of_context(make_pipe):
    """(f) Karma kapalı: satır asıl çeviriyi bekler (bekleme penceresi dolsa da hızlı çeviri görünmez); hızlı
    çeviri yalnız asıl motor hata verince gösterilir ve bağlama girmez."""
    h = Hybrid(make_pipe(hybrid=False), main=FakeTr("asıl", fail=("Who are those guys?",)))
    a = h.read("Get in the car, now.")
    h.wait(3.0)  # karma açıkken pencere (1,2 sn) çoktan dolmuştu
    assert h.out == []
    h.remote()
    assert h.out == [(MAIN, a["text"], a["line"])]
    b = h.read("Who are those guys?")
    h.remote()  # asıl motor hata verdi: hızlı çeviri beklemeden
    assert h.out[-1] == (QUICK, b["text"], b["line"])
    c = h.read("Hold on tight!")
    h.remote()
    assert [o[0] for o in h.out] == [MAIN, QUICK, MAIN] and h.out[-1][1:] == (c["text"], c["line"])
    assert h.main.contexts["Hold on tight!"] == [("Get in the car, now.", "asıl: Get in the car, now."),
                                                 ("Who are those guys?", "")]


def test_hybrid_off_shows_quick_when_main_never_answers(make_pipe):
    """Karma kapalıyken de satır kaybolmaz: asıl çeviri istek zaman aşımına kadar gelmezse hızlı çeviri gösterilir."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe)
    a = h.read("Keep your head down.")
    h.wait(pipe.s.translate.timeout_s - 0.1)
    assert h.out == []
    h.wait(0.2)
    assert h.out == [(QUICK, a["text"], a["line"])]


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


# --- uyarlamalı bekleme: asıl motor süreye yetişemiyorsa satır boşuna bekletilmez --------------------------
def _speed(cx: TrContext, seconds: float) -> None:
    """Asıl motorun ölçülen hızını ayarlar: son satırların hepsi bu kadar sürmüş gibi."""
    cx._service.clear()
    for _ in range(pl.SERVICE_SAMPLES):
        cx.note_service(seconds)


def _arrive(service: float, queued: int = 0, lag: float = 0.0) -> float:
    """Asıl çevirinin okunduğu kareden kaç sn sonra gelmesi beklenir (payıyla; bkz. Pipeline._wait_s)."""
    return lag + service * (queued + 1) * pl.ARRIVE_MARGIN + pl.ARRIVE_PAD_S


def test_wait_adapts_to_main_engine_speed(make_pipe):
    """Asıl çevirinin geleceği an (payıyla) pencereye sığıyorsa pencere kadar beklenir; az farkla aşıyorsa o ana
    kadar beklenir (hızlı çeviri görünüp hemen değişmesin); belirgin aşıyorsa hiç beklenmez (hızlı çeviri hemen)."""
    pipe = make_pipe()
    h = Hybrid(pipe)  # pencere 1,2 sn
    assert pipe._wait_s(h.cx) == pytest.approx(1.2)  # henüz ölçüm yok
    _speed(h.cx, 0.4)
    assert pipe._wait_s(h.cx) == pytest.approx(1.2)
    _speed(h.cx, 1.2)
    assert 1.2 < _arrive(1.2) <= 1.2 * pl.WAIT_GIVE_UP
    assert pipe._wait_s(h.cx) == pytest.approx(_arrive(1.2))
    _speed(h.cx, 1.35)
    assert pipe._wait_s(h.cx) == 0.0  # payıyla 1,5 sn'yi aşar
    _speed(h.cx, 2.5)
    assert pipe._wait_s(h.cx) == 0.0
    # sırada bekleyen işler de sayılır: 0,4 sn'lik motor, önünde iki iş varken pencereye az farkla yetişemez
    _speed(h.cx, 0.4)
    pipe._remote_q.put({"ev": {"line": 901}, "quick": None, "done": None})
    pipe._remote_q.put({"ev": {"line": 902}, "quick": None, "done": None})
    assert pipe._wait_s(h.cx) == pytest.approx(_arrive(0.4, queued=2))
    pipe._remote_q.put({"ev": {"line": 903}, "quick": None, "done": None})
    assert pipe._wait_s(h.cx) == 0.0
    pipe._remote_q.clear()


def test_wait_counts_the_time_already_spent(make_pipe):
    """Süre satırın okunduğu kareden sayılır: OCR ve hızlı çeviride geçen süre (lag) de hesaba girer. 1 sn'lik
    motor boş satırda pencereye sığar; okuma 0,3 sn geç geldiyse sığmaz, satır beklemez."""
    pipe = make_pipe()
    h = Hybrid(pipe)
    _speed(h.cx, 1.0)
    assert pipe._wait_s(h.cx) == pytest.approx(_arrive(1.0))
    assert _arrive(1.0, lag=0.3) > 1.2 * pl.WAIT_GIVE_UP
    assert pipe._wait_s(h.cx, 0.3) == 0.0


def test_translation_in_flight_counts_towards_the_wait(make_pipe):
    """Asıl motor o an başka bir satırı çeviriyorsa onun kalan süresi de hesaba girer: 1 sn'lik motor yeni
    başladığı bir çevirinin ortasındayken gelen satır pencereye (1,2 sn) yetişemez, bekletilmez."""
    pipe = make_pipe()
    h = Hybrid(pipe)
    _speed(h.cx, 0.8)
    assert pipe._wait_s(h.cx) == pytest.approx(1.2)  # motor boşta: 0,8 sn pencereye sığar
    h.cx.job_started()
    assert pipe._wait_s(h.cx) == 0.0  # kalan ~0,8 sn + kendi 0,8 sn
    h.cx.note_service(0.8)  # süren iş bitti
    assert pipe._wait_s(h.cx) == pytest.approx(1.2)


def test_slow_main_engine_shows_quick_at_once_then_upgrades(make_pipe):
    """Zayıf makine: asıl motor satır başına ~2,5 sn sürüyorsa satır pencere kadar boşuna bekletilmez; hızlı
    çeviri hemen görünür, asıl çeviri gelince aynı satırı günceller. Satır kaybolmaz."""
    h = Hybrid(make_pipe())
    _speed(h.cx, 2.5)
    a = h.read("They're right behind us!")
    assert h.out == [(QUICK, a["text"], a["line"])]
    h.wait(2.5)
    h.remote()
    assert h.out == [(QUICK, a["text"], a["line"]), (MAIN, a["text"], a["line"])]


def test_hybrid_off_does_not_wait_for_a_translation_that_cannot_make_it(make_pipe):
    """Karma kapalı: satır asıl çeviriyi bekler. Asıl motorun yetişemeyeceği belliyse (ağır model: satır başına
    istek zaman aşımından uzun, ya da sırada çok iş var) boş ekranda zaman aşımı kadar beklenmez: hızlı çeviri
    hemen görünür, asıl çeviri gelince üstüne yazılır."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe)
    limit = pipe.s.translate.timeout_s
    assert pipe._wait_s(h.cx) == pytest.approx(limit)  # henüz ölçüm yok: beklenir
    _speed(h.cx, 3.5)
    assert pipe._wait_s(h.cx) == pytest.approx(limit)  # yavaş ama yetişir: karma kapalıyken beklenir
    _speed(h.cx, limit + 1.0)
    assert pipe._wait_s(h.cx) == 0.0
    a = h.read("Nobody moves until I say so.")
    assert h.out == [(QUICK, a["text"], a["line"])]
    h.remote()
    assert h.out == [(QUICK, a["text"], a["line"]), (MAIN, a["text"], a["line"])]


def test_service_time_is_the_median_of_recent_lines(make_pipe):
    """Asıl motorun süresi her uzak çeviride (hata dahil) ölçülür; son satırların ortancası alınır: tek bir zaman
    aşımı sonraki satırların beklemesini bozmaz, makine gerçekten yavaşladıysa birkaç satırda izlenir."""
    pipe = make_pipe()
    h = Hybrid(pipe, main=FakeTr("asıl", fail=("Just drive.",)))
    assert h.cx.service_s is None and h.cx.steady_service_s() is None
    h.read("Get in the car, now.")
    h.remote()
    assert h.cx.service_s is not None and h.cx.service_s >= 0
    h.read("Just drive.")
    h.remote()  # hata: yine ölçülür
    assert len(h.cx._service) == 2 and h.cx.steady_service_s() is None  # kullanıcıya gösterilecek karar için az

    _speed(h.cx, 0.4)
    h.cx.note_service(6.0)  # tek bir zaman aşımı
    assert h.cx.service_s == pytest.approx(0.4) and pipe._wait_s(h.cx) == pytest.approx(1.2)
    h.cx.note_service(6.0)
    assert h.cx.service_s == pytest.approx(0.4)
    h.cx.note_service(6.0)  # son beş satırın üçü yavaş: makine gerçekten yavaşladı
    assert h.cx.service_s == pytest.approx(6.0) and pipe._wait_s(h.cx) == 0.0
    for _ in range(3):
        h.cx.note_service(0.4)  # toparlandı
    assert h.cx.service_s == pytest.approx(0.4) and h.cx.steady_service_s() == pytest.approx(0.4)


def test_slow_engine_with_hybrid_off_suggests_hybrid_once(make_pipe):
    """Karma kapalıyken asıl motor yavaşsa (ağır model, zayıf makine) kullanıcıya pencere açmayan bir bildirimle
    Hibrit mod önerilir: oturumda bir kez, yeterince satır ölçüldükten sonra. Karma açıkken (satırlar zaten hızlı
    çeviriyle görünür) ya da motor hızlıyken bildirim çıkmaz."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe)
    notices: list[str] = []
    pipe.notice.connect(notices.append)
    h.cx.note_service(3.0)
    pipe._hint_if_slow(h.cx)
    assert notices == []  # tek ölçüm yetmez
    _speed(h.cx, 0.4)
    pipe._hint_if_slow(h.cx)
    assert notices == []  # hızlı motor
    _speed(h.cx, 3.0)
    pipe._hint_if_slow(h.cx)
    assert notices == [pl.AI_SLOW_NOTICE.format(sec="3,0")] and "Hibrit" in notices[0]
    pipe._hint_if_slow(h.cx)
    assert len(notices) == 1  # oturumda bir kez

    on = make_pipe(hybrid=True)
    h2 = Hybrid(on)
    got: list[str] = []
    on.notice.connect(got.append)
    _speed(h2.cx, 3.0)
    on._hint_if_slow(h2.cx)
    assert got == []


def test_every_remote_translation_checks_for_a_slow_engine(make_pipe):
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe)
    seen: list[TrContext] = []
    pipe._hint_if_slow = seen.append
    h.read("Get in the car, now.")
    h.remote()
    assert seen == [h.cx]


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
    # motorun kendi durum satırı "şimdilik hızlı çeviri" ekiyle gelir: kullanıcı altyazının sürdüğünü görür
    assert statuses.index(pl.PREPARING_STATUS) < statuses.index(
        "sahte model yükleniyor…" + pl.WHILE_PREPARING) < statuses.index(pl.READY_STATUS)


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


def test_main_request_starts_before_quick_translation(make_pipe):
    """Asıl istek hızlı çeviriyi beklemeden kuyruğa girer (karma kapalıyken her satırın yolu budur): uzak iş
    parçacığı hızlı çeviri hesaplanırken çevirmeye başlayabilir. Asıl çeviri hızlı çeviriden önce gelirse yalnız o
    gösterilir; hızlı çeviri sonradan gelince bir şey değişmez."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe)
    order: list[str] = []
    real_quick = h.fast.translate

    def quick(text, context=()):
        order.append(f"hızlı başladı, uzak kuyrukta {len(pipe._remote_q)} iş")
        h.remote()  # asıl çeviri bu arada geldi
        return real_quick(text, context)

    h.fast.translate = quick
    a = h.read("Get in the car, now.")
    assert order == ["hızlı başladı, uzak kuyrukta 1 iş"]
    assert h.out == [(MAIN, a["text"], a["line"])]
    h.wait(10.0)
    assert h.out == [(MAIN, a["text"], a["line"])]


def test_main_error_before_quick_translation_does_not_lose_the_line(make_pipe):
    """Asıl çeviri hızlı çeviri hesaplanırken hata verirse satır kaybolmaz: hızlı çevirisi gelince gösterilir."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe, main=FakeTr("asıl", fail=("Get in the car, now.",)))
    real_quick = h.fast.translate

    def quick(text, context=()):
        h.remote()  # asıl motor hemen hata verdi
        assert h.out == []
        return real_quick(text, context)

    h.fast.translate = quick
    a = h.read("Get in the car, now.")
    assert h.out == [(QUICK, a["text"], a["line"])]


def test_line_waits_for_its_quick_translation_after_deadline(make_pipe):
    """Bekleme süresi hızlı çeviri hesaplanırken dolarsa satır hızlı çevirisini bekler, sonra gösterilir."""
    pipe = make_pipe()
    h = Hybrid(pipe, wait_ms=100)
    real_quick = h.fast.translate

    def quick(text, context=()):
        h.wait(0.5)  # zayıf makine: hızlı çeviri bile yavaş
        assert h.out == []
        return real_quick(text, context)

    h.fast.translate = quick
    a = h.read("Where are we going?")
    assert h.out == [(QUICK, a["text"], a["line"])]
    h.remote()
    assert h.out[-1] == (MAIN, a["text"], a["line"])


def test_stale_job_is_skipped_on_a_slow_machine(make_pipe):
    """Yavaş makinede asıl motorun kuyruğu birikirse, çevirisi geldiğinde ekrandan çoktan kalkmış olacak satır
    çevrilmez: motor yeni satırlara kalır. Satır hızlı çevirisiyle gösterilmiştir; bağlama yalnız İngilizcesi girer."""
    pipe = make_pipe()
    h = Hybrid(pipe)
    _speed(h.cx, 2.5)
    a = h.read("Who are those guys?")
    b = h.read("Hold on tight!")
    assert h.out == [(QUICK, a["text"], a["line"]), (QUICK, b["text"], b["line"])]
    h.wait(pipe.s.flow.max_show_s - 2.0)  # 2,5 sn'lik çeviri gelene kadar satırın ekranda kalma süresi dolar
    h.remote()  # a bayat ve arkasında b bekliyor: atlanır
    assert len(h.out) == 2 and h.main.contexts == {}
    h.remote()  # b de bayat ama arkasında iş yok: motor boşta, geç de olsa çevrilir
    assert h.out[-1] == (MAIN, b["text"], b["line"])
    assert h.main.contexts["Hold on tight!"] == [("Who are those guys?", "")]


def test_failing_engine_is_not_waited_for(make_pipe):
    """Asıl motor art arda hata veriyorsa (ör. Ollama kapandı: her istek ~2 sn'de bağlantı hatası) satırlar onu
    beklemez: hızlı çeviri hemen görünür. Motor yeniden yanıt verince bekleme geri gelir."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe, main=FakeTr("asıl", fail=("Who are those guys?", "Cops. Keep your head down.")))
    h.read("Who are those guys?")
    h.remote()
    assert not h.cx.failing and pipe._wait_s(h.cx) > 0  # tek hata: geçici olabilir
    h.read("Cops. Keep your head down.")
    h.remote()
    assert h.cx.failing and pipe._wait_s(h.cx) == 0.0
    c = h.read("Hold on tight!")
    assert h.out[-1] == (QUICK, c["text"], c["line"])  # beklemeden
    h.remote()  # motor yanıt verdi
    assert not h.cx.failing and h.out[-1] == (MAIN, c["text"], c["line"])
    assert pipe._wait_s(h.cx) > 0


class DeadLlm(FakeTr):
    """Çalışırken ölen yerel LLM: `dead` kuruluyken her çeviri hata verir ve healthy() False döner; prepare()
    (Ollama'yı başlatıp modeli yeniden yükler) onu diriltir."""

    def __init__(self):
        super().__init__("asıl")
        self.dead = False
        self.prepares = 0

    def prepare(self, status) -> None:
        self.prepares += 1
        status("sahte model yükleniyor…")
        self.dead = False

    def healthy(self) -> bool:
        return not self.dead

    def translate(self, text, context=()):
        if self.dead:
            raise TranslatorError("bağlantı reddedildi")
        return super().translate(text, context)


def test_dead_engine_is_prepared_again(make_pipe):
    """Ollama çeviri sırasında kapandı ya da modeli bellekten düştü: asıl motor art arda hata verince ve gerçekten
    çalışamıyorsa uzak iş parçacığı onu yeniden hazırlar (Ollama'yı başlatır, modeli yükler). O sırada sıradaki
    satırlar beklemeden hızlı çeviriyle gösterilir; motor hazır olunca asıl çeviri geri gelir. Hiçbir satır
    kaybolmaz."""
    pipe = make_pipe(hybrid=False)
    llm = DeadLlm()
    h = Hybrid(pipe, main=llm)
    statuses: list[str] = []
    pipe.status.connect(statuses.append)
    a = h.read("Get in the car, now.")
    h.remote()
    assert h.out == [(MAIN, a["text"], a["line"])]
    llm.dead = True
    b = h.read("Where are we going?")
    h.remote()  # 1. hata: satır hızlı çeviriyle
    c = h.read("Just drive.")
    d = h.read("Who are those guys?")  # c'nin işi sırada; d de
    assert len(pipe._remote_q) == 2
    job = pipe._remote_q.get(0)
    pipe._apply(pipe._remote_item(job, h.cx))  # 2. hata: art arda iki hata
    assert h.cx.failing and not pipe._main_alive(h.cx)
    pipe._run.set()
    assert pipe._recover_main(h.cx)
    assert llm.prepares == 1 and h.cx.ready and not h.cx.failing
    assert pl.RECOVER_STATUS in statuses and pl.READY_STATUS in statuses
    assert len(pipe._remote_q) == 0  # sıradaki iş beklemedi: hızlı çeviriye bırakıldı
    shown = {o[2]: o[0] for o in h.out}
    assert shown == {a["line"]: MAIN, b["line"]: QUICK, c["line"]: QUICK, d["line"]: QUICK}
    e = h.read("Take the next exit.")
    h.remote()
    assert h.out[-1] == (MAIN, e["text"], e["line"])


def test_dead_engine_that_cannot_be_prepared_leaves_quick(make_pipe):
    """Yeniden hazırlık da başarısızsa (Ollama başlatılamadı) bildirilir ve hat hızlı çeviriyle sürer."""
    pipe = make_pipe(hybrid=False)
    llm = DeadLlm()
    h = Hybrid(pipe, main=llm)
    errors: list[str] = []
    pipe.error.connect(errors.append)  # sahte motor yerel yapay zekâ değil: bilinen hata da uyarı olarak gelir
    llm.prepare = lambda status: (_ for _ in ()).throw(TranslatorError("Ollama başlatılamadı."))
    llm.dead = True
    for text in ("Where are we going?", "Just drive."):
        h.read(text)
        h.remote()
    pipe._run.set()
    assert not pipe._recover_main(h.cx)
    assert not h.cx.ready and errors == ["Ollama başlatılamadı."]
    f = h.read("Hold on tight!")
    assert h.out[-1] == (QUICK, f["text"], f["line"]) and len(pipe._remote_q) == 0


def test_healthy_engine_is_not_prepared_again(make_pipe):
    """Art arda hata veren ama çalışır durumdaki motor (geçici ağ hatası, kota) yeniden hazırlanmaz."""
    pipe = make_pipe(hybrid=False)
    h = Hybrid(pipe, main=FakeTr("asıl", fail=("Where are we going?", "Just drive.")))
    for text in ("Where are we going?", "Just drive."):
        h.read(text)
        h.remote()
    assert h.cx.failing and pipe._main_alive(h.cx)


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
    """Asıl motor çeviri döngüsünü bekletmesin diye burada hazırlanmaz (uzak iş parçacığı hazırlar); karma ayarı
    kapalıyken de öyle: o ayar yalnız satırın asıl çeviriyi ne kadar bekleyeceğini belirler. İki motor da oyun
    profilinin adlarını içeren sözlüğü alır."""
    glossary = object()
    main, fast = FakeTr("asıl"), FakeTr("hızlı")
    seen: list[object] = []
    monkeypatch.setattr(pl, "build_glossary", lambda tc: glossary)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda g, device="cpu": seen.append(g) or fast)
    monkeypatch.setattr(pl, "create_translator", lambda tc, g: seen.append(g) or main)
    pipe = make_pipe(hybrid)
    pipe._run.set()  # hat çalışıyor
    assert pipe._prepare_translators() == (main, fast)
    assert seen == [glossary, glossary]
    assert fast.prepared and not main.prepared
    assert pipe._main_tr is main  # stop() hazırlığı bunun üzerinden yarıda keser
