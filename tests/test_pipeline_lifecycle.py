"""İşlem hattının açılıp kapanması: Durdur anında bildirilir, kapanırken basılan Başlat kaybolmaz, asıl motorun
süren hazırlığı (yerel LLM'in belleğe yüklenmesi) Durdur'u ve yeniden Başlat'ı bekletmez.

Yakalama ve OCR yok: hattın döngüleri sahteleriyle değiştirilir; çeviri döngüsü gerekirse gerçeğiyle, sahte
motorlarla çalışır. Kuyruğa alınan sinyaller için QCoreApplication'ın olay döngüsü döndürülür; süre ölçülmez,
yalnız olayların sırası denetlenir."""

from __future__ import annotations

import threading
import time
import types
from collections.abc import Callable

import pytest

from altyazitr import pipeline as pl
from altyazitr.config import Settings
from altyazitr.pipeline import Pipeline
from altyazitr.translate import Translator, TranslatorError


@pytest.fixture(scope="module")
def qapp():
    """Pencere yok: QCoreApplication yeter. Kendi kurduğu uygulamayı sonunda kapatır ki sonraki modüller kendi
    uygulamasını kurabilsin; önceden kurulmuş bir uygulama varsa onu kullanır."""
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    created = inst is None
    if created:
        inst = QCoreApplication([])
    yield inst
    if created:
        inst.shutdown()


@pytest.fixture
def make_pipe(tmp_path, monkeypatch) -> Callable[..., Pipeline]:
    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")  # kullanıcının önbelleğine dokunma
    made: list[Pipeline] = []

    def make(hybrid: bool = True) -> Pipeline:
        s = Settings()
        s.translate.engine = "ollama"
        s.translate.hybrid = hybrid
        made.append(Pipeline(s))
        return made[-1]

    yield make
    for pipe in made:  # test yarıda kaldıysa iş parçacığı kalmasın
        pipe.stop_and_wait(3.0)


def _wait(qapp, cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Kuyruğa alınmış sinyaller işlensin diye olay döngüsünü döndürerek bekler."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    qapp.processEvents()
    return cond()


def _until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    """Olay döngüsünü DÖNDÜRMEDEN bekler: kuyruktaki sinyaller işlenmeden kalır."""
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "beklenen olay gelmedi"
        time.sleep(0.002)


def _settle(qapp) -> None:
    for _ in range(5):
        qapp.processEvents()
        time.sleep(0.01)


def _watch(pipe: Pipeline) -> types.SimpleNamespace:
    out = types.SimpleNamespace(states=[], statuses=[], errors=[], notices=[])
    pipe.running_changed.connect(out.states.append)
    pipe.status.connect(out.statuses.append)
    pipe.error.connect(out.errors.append)
    pipe.notice.connect(out.notices.append)
    return out


def _idle(pipe: Pipeline) -> Callable[[], None]:
    def loop() -> None:
        while pipe._run.is_set():
            time.sleep(0.002)

    return loop


def _slow_closing_loops(pipe: Pipeline, gate: threading.Event) -> list[str]:
    """Hattın üç döngüsünü sahteleriyle değiştirir: hat durdurulana kadar çalışır, sonra `gate` açılınca kapanırlar
    (yavaş kapanan iş parçacığı: süren bir OCR, yanıtı beklenen bir çeviri isteği). Başlayanların adları döner."""
    started: list[str] = []

    def loop() -> None:
        started.append(threading.current_thread().name)
        while pipe._run.is_set():
            time.sleep(0.002)
        gate.wait(5)

    pipe._capture_loop = pipe._ocr_loop = pipe._translate_loop = loop
    return started


# --- Başlat / Durdur ---------------------------------------------------------------------------------------
def test_stop_is_reported_at_once_and_start_while_closing_is_not_lost(qapp, make_pipe):
    """Durdur'a basılınca arayüz hemen "durdu" görür; iş parçacıkları arkada kapanır. O sırada basılan Başlat
    kaybolmaz: eski iş parçacıkları kapanınca hat kendiliğinden başlar. (Eskiden o Başlat sessizce yok sayılırdı.)"""
    pipe = make_pipe()
    gate = threading.Event()
    started = _slow_closing_loops(pipe, gate)
    w = _watch(pipe)
    pipe.start()
    assert _wait(qapp, lambda: len(started) == 3) and w.states == [True]
    pipe.stop()
    assert w.states == [True, False] and w.statuses[-1] == pl.STOPPING_STATUS
    assert pipe._alive == 3 and not pipe.running  # iş parçacıkları hâlâ kapanıyor
    pipe.start()  # kapanırken Başlat
    assert not pipe.running and len(started) == 3 and w.statuses[-1] == pl.RESTART_PENDING_STATUS
    gate.set()  # eski iş parçacıkları kapandı
    assert _wait(qapp, lambda: pipe.running and len(started) == 6)
    assert w.states == [True, False, True]
    pipe.stop_and_wait(3.0)
    assert _wait(qapp, lambda: pipe._alive == 0)
    assert w.states == [True, False, True, False] and w.errors == []


def test_stop_cancels_a_start_that_was_waiting(qapp, make_pipe):
    """Kapanırken basılan Başlat'tan sonra yeniden Durdur'a basılırsa hat başlamaz; iş parçacıkları kapanıp
    "başlat" işareti kuyruğa girdikten sonra basılsa bile."""
    pipe = make_pipe()
    gate = threading.Event()
    started = _slow_closing_loops(pipe, gate)
    w = _watch(pipe)
    pipe.start()
    assert _wait(qapp, lambda: len(started) == 3)
    pipe.stop()
    pipe.start()  # bekliyor
    gate.set()
    _until(lambda: pipe._alive == 0)  # iş parçacıkları kapandı; başlatma işareti kuyrukta, henüz işlenmedi
    pipe.stop()  # kullanıcı vazgeçti
    _settle(qapp)
    assert not pipe.running and len(started) == 3 and w.states == [True, False]


def test_second_start_while_running_does_nothing(qapp, make_pipe):
    pipe = make_pipe()
    gate = threading.Event()
    gate.set()
    started = _slow_closing_loops(pipe, gate)
    w = _watch(pipe)
    pipe.start()
    pipe.start()
    assert _wait(qapp, lambda: len(started) == 3)
    _settle(qapp)
    assert len(started) == 3 and w.states == [True]


def test_pipeline_that_stops_itself_reports_stopped_once(qapp, make_pipe):
    """Bir iş parçacığı beklenmeyen hatayla düşerse hat kendiliğinden durur; arayüz bunu bir kez öğrenir."""
    pipe = make_pipe()
    w = _watch(pipe)

    def broken() -> None:
        raise RuntimeError("yakalama koptu")

    pipe._capture_loop = broken
    pipe._ocr_loop = pipe._translate_loop = _idle(pipe)
    pipe.start()
    assert _wait(qapp, lambda: pipe._alive == 0 and w.states == [True, False] and w.statuses[-1:] == ["Durduruldu"])
    assert w.errors == ["Beklenmeyen hata: yakalama koptu"]
    pipe.stop()  # zaten durdu: ikinci kez bildirilmez
    _settle(qapp)
    assert w.states == [True, False]
    pipe._capture_loop = _idle(pipe)
    pipe.start()  # yeniden başlatılabilir
    assert _wait(qapp, lambda: pipe.running and pipe._alive == 3)
    assert w.states == [True, False, True]


# --- asıl motorun hazırlığı Durdur'u bekletmez ---------------------------------------------------------------
class LoadingEngine(Translator):
    """Yerel LLM gibi: prepare() model belleğe yüklenene (testin izni) ya da abort() çağrılana kadar sürer."""

    key, label, model = "ollama", "sahte yapay zekâ", "gemma4:12b"

    def __init__(self) -> None:
        self.loading = threading.Event()
        self.loaded = threading.Event()
        self._abort = threading.Event()
        self.aborted = self.closed = False

    def prepare(self, status) -> None:
        status("Gemma 4 12B belleğe yükleniyor…")
        self.loading.set()
        while not self.loaded.wait(0.01):
            if self._abort.is_set():
                raise TranslatorError("Yapay zekâ hazırlığı durduruldu.")

    def abort(self) -> None:
        self.aborted = True
        self._abort.set()

    def translate(self, text, context=()):
        return f"asıl: {text}"

    def close(self) -> None:
        self.closed = True


class FakeQuick(Translator):
    key, label = "quickmt", "sahte hızlı"

    def prepare(self, status) -> None:
        pass

    def translate(self, text, context=()):
        return f"hızlı: {text}"


@pytest.fixture
def engines(monkeypatch) -> list[LoadingEngine]:
    """Hattın kurduğu asıl motorlar (her çalıştırmada bir tane), sırayla."""
    made: list[LoadingEngine] = []

    def make_engine(tc, glossary) -> LoadingEngine:
        made.append(LoadingEngine())
        return made[-1]

    monkeypatch.setattr(pl, "create_translator", make_engine)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda glossary, device="cpu": FakeQuick())
    yield made
    for e in made:
        e.loaded.set()


@pytest.mark.parametrize("hybrid", [True, False], ids=["karma", "karma kapalı"])
def test_subtitles_flow_while_the_model_loads(qapp, make_pipe, engines, hybrid):
    """Başlat'a basılınca model belleğe yüklenirken hat takılmaz: satırlar hızlı çeviriyle gelir, durum satırı bunu
    söyler; model hazır olunca asıl çeviriye geçilir. Karma mod kapalıyken de böyledir (eskiden çeviri döngüsü
    modelin yüklenmesini beklerdi: dakikayı bulan "ısınıyor" takılması)."""
    pipe = make_pipe(hybrid)
    pipe._capture_loop = pipe._ocr_loop = _idle(pipe)
    w = _watch(pipe)
    subs: list[dict] = []
    pipe.subtitle.connect(subs.append)

    def say(text: str) -> None:
        t = pipe.clock()
        pipe._tr_q.put(("sub", t, pipe.tracker.on_text(text, None, t)))

    pipe.start()
    assert _wait(qapp, lambda: len(engines) == 1 and engines[0].loading.is_set())
    assert _wait(qapp, lambda: "Gemma 4 12B belleğe yükleniyor…" + pl.WHILE_PREPARING in w.statuses)
    say("Get in the car, now.")
    assert _wait(qapp, lambda: len(subs) == 1)
    assert (subs[0]["engine"], subs[0]["text"]) == (FakeQuick.label, "hızlı: Get in the car, now.")
    engines[0].loaded.set()  # model yüklendi
    assert _wait(qapp, lambda: pl.READY_STATUS in w.statuses)
    say("Where are we going?")
    assert _wait(qapp, lambda: len(subs) == 2)
    assert (subs[1]["engine"], subs[1]["text"]) == (LoadingEngine.label, "asıl: Where are we going?")
    assert w.errors == w.notices == []


def test_model_is_kept_warm_during_long_silence(qapp, make_pipe, engines, monkeypatch):
    """Hat çalışırken uzun süre satır gelmezse (oyun duraklatıldı) asıl motorun modeli sıcak tutulur: bellekte
    kalma süresi dolup model boşalmasın, sonraki satır yeniden yüklenmesini beklemesin."""
    monkeypatch.setattr(pl, "KEEP_WARM_S", 0.05)
    warmed: list[float] = []
    monkeypatch.setattr(LoadingEngine, "keep_warm", lambda self: warmed.append(time.monotonic()), raising=False)
    pipe = make_pipe()
    pipe._capture_loop = pipe._ocr_loop = _idle(pipe)
    pipe.start()
    assert _wait(qapp, lambda: len(engines) == 1 and engines[0].loading.is_set())
    time.sleep(0.2)
    assert warmed == []  # model yüklenirken değil: hazır olduktan sonra
    engines[0].loaded.set()
    assert _wait(qapp, lambda: len(warmed) >= 2)
    pipe.stop_and_wait(3.0)


def test_stop_while_the_quick_model_loads_skips_the_ai(qapp, make_pipe, engines, monkeypatch):
    """Başlat'tan hemen sonra (hızlı çeviri modeli yüklenirken) Durdur'a basılırsa asıl motor hiç hazırlanmaz:
    model boşuna belleğe yüklenmez, ardından basılan Başlat da onu beklemez. (Eskiden bu Durdur kaybolurdu: durmuş
    çalıştırma modeli baştan sona yükler, yeni Başlat o bitene kadar beklerdi.)"""
    quick_loading, quick_loaded = threading.Event(), threading.Event()

    class SlowQuick(FakeQuick):
        def prepare(self, status) -> None:
            quick_loading.set()
            quick_loaded.wait(5)

    quicks: list[SlowQuick] = []
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda glossary, device="cpu": quicks.append(SlowQuick()) or quicks[-1])
    pipe = make_pipe()
    pipe._capture_loop = pipe._ocr_loop = _idle(pipe)
    w = _watch(pipe)
    pipe.start()
    assert _wait(qapp, quick_loading.is_set)
    pipe.stop()
    pipe.start()  # bekliyor
    quick_loaded.set()  # hızlı çeviri modeli yüklendi
    assert _wait(qapp, lambda: len(engines) == 2 and engines[1].loading.is_set())
    assert not engines[0].loading.is_set() and engines[0].closed  # durmuş çalıştırmanın motoru hazırlanmadı
    assert w.errors == w.notices == [] and w.states == [True, False, True]


def test_pipeline_that_fails_while_the_model_loads_stops_at_once(qapp, make_pipe, engines):
    """Model belleğe yüklenirken hat kendiliğinden durursa (ör. OCR motoru açılamadı) yükleme beklenmez: arayüz
    hemen "durdu" görür, iş parçacıkları kapanır."""
    pipe = make_pipe()
    fail = threading.Event()

    def ocr() -> None:
        fail.wait(5)
        raise RuntimeError("OCR motoru açılamadı")

    pipe._capture_loop = _idle(pipe)
    pipe._ocr_loop = ocr
    w = _watch(pipe)
    pipe.start()
    assert _wait(qapp, lambda: len(engines) == 1 and engines[0].loading.is_set())
    fail.set()
    assert _wait(qapp, lambda: w.states == [True, False] and pipe._alive == 0)
    assert engines[0].aborted and not engines[0].loaded.is_set()
    assert w.errors == ["Beklenmeyen hata: OCR motoru açılamadı"]


def test_stop_and_restart_do_not_wait_for_the_model_to_load(qapp, make_pipe, engines):
    """Model belleğe yüklenirken Durdur'a basılır: hazırlık bırakılır, iş parçacıkları hemen kapanır, hata ya da
    bildirim çıkmaz. Ardından basılan Başlat da yüklemeyi beklemeden yeni çalıştırmayı açar."""
    pipe = make_pipe()
    pipe._capture_loop = pipe._ocr_loop = _idle(pipe)
    w = _watch(pipe)
    pipe.start()
    assert _wait(qapp, lambda: len(engines) == 1 and engines[0].loading.is_set())
    pipe.stop()
    pipe.start()  # hemen yeniden Başlat
    assert _wait(qapp, lambda: len(engines) == 2 and engines[1].loading.is_set())
    assert engines[0].aborted and engines[0].closed and not engines[0].loaded.is_set()  # ilk yükleme beklenmedi
    assert w.errors == w.notices == [] and w.states == [True, False, True]
    engines[1].loaded.set()
    assert _wait(qapp, lambda: pl.READY_STATUS in w.statuses)
    pipe.stop_and_wait(3.0)
    assert _wait(qapp, lambda: pipe._alive == 0)
    assert engines[1].closed and w.errors == w.notices == []
