"""Hızlı konuşma benzetimi: altyazı satırları iş parçacıkları arasındaki kuyruklarda kayboluyor mu?

Pencere, gerçek OCR ya da çeviri modeli yok. Sanal saatle çalışan ayrık olay benzetimi: yakalama, değişim
kapısının koyacağı anlarda OCR kuyruğuna kare koyar; OCR, çeviri ve uzak çeviri "işçileri" işlem hattının
gerçek adımlarını (Pipeline._ocr_item / _translate_item / _remote_item / _apply) çalıştırır, sahte
motorların harcadığı süre kadar meşgul kalır ve sonuçlarını o süre dolunca uygular. Gerçek zaman ve iş
parçacığı olmadığı için sonuç her çalıştırmada aynıdır.

Aynı diyalog eski davranışla da çalıştırılır: tek yuvalı LatestSlot kutuları ve eski çeviri döngüsünün
`seq` denetimleri (run_old, eski _translate_loop'un birebir aktarımı). Kaybolan satırlar karşılaştırılır:
    python -m pytest tests/test_flow_queue.py -q -s -k old_vs_new
"""

from __future__ import annotations

import heapq
import logging
import sys
import threading
import time
from dataclasses import dataclass

import pytest
from PySide6.QtCore import Qt

from altyazitr import pipeline as pl
from altyazitr.config import Settings
from altyazitr.ocr import OcrLine
from altyazitr.pipeline import FifoQueue, LatestSlot, Pipeline, TrContext, Tracker
from altyazitr.textproc import assemble
from altyazitr.translate import TranslationCache, Translator

OCR_S = 0.150  # RapidOCR, bir kare
QUICK_S = 0.050  # quickmt, bir satır
GATE_S = 0.07  # yazı belirdikten sonra kapının okumaya göndermesi (stable_frames=2, 30 fps)
GONE_S = 0.10  # yazı kaybolduktan sonra 'gone' (gone_frames=3)
START = 0.5
QUICK_LABEL, MAIN_REMOTE_LABEL = "sahte hizli", "sahte uzak"


def _canon(raw: str) -> str:
    """OCR'ın okuduğu ham metin → olaydaki 'original' (konuşmacı adı ayrılmış)."""
    return assemble([OcrLine(raw, 0.95, 40, 300, 600, 30)])[0]


class Dialogue:
    """Satırlar: (ekranda kalma s, ardından boşluk s, [(belirdikten kaç s sonra okundu, OCR'ın okuduğu metin)])."""

    def __init__(self, lines: list):
        self.lines = lines
        self.n = len(lines)
        self.line_of = {_canon(raw): i for i, (_, _, reads) in enumerate(lines) for _, raw in reads}

    def schedule(self) -> list[tuple[float, tuple]]:
        """Değişim kapısının OCR kuyruğuna koyacağı öğeler: (zaman, öğe)."""
        t, out = START, []
        for dur, gap, reads in self.lines:
            for off, raw in reads:
                out.append((t + off, ("text", t + off, Frame(raw))))
            if gap >= GONE_S:
                out.append((t + dur + GONE_S, ("gone", t + dur + GONE_S, None)))
            t += dur + gap
        return out

    def first_reads(self) -> list[float]:
        t, out = START, []
        for dur, gap, reads in self.lines:
            out.append(t + reads[0][0])
            t += dur + gap
        return out

    def gap_lines(self) -> list[int]:
        return [i for i, (_, gap, _) in enumerate(self.lines) if gap >= GONE_S]

    def final_text(self, i: int) -> str:
        return _canon(self.lines[i][2][-1][1])


# 12 ayrı satır, çoğu 0,6-1,0 sn, bazıları 0,4 sn; daktilo, bozuk→temiz okuma ve titreme içerir
DIALOGUE = Dialogue([
    (0.8, 0.0, [(GATE_S, "LUCIA: Get in the car, now.")]),
    (0.6, 0.0, [(GATE_S, "JASON: Where are we going?")]),
    (0.4, 0.0, [(GATE_S, "LUCIA: Just drive.")]),
    (0.7, 0.2, [(GATE_S, "They're watching us."),  # daktilo efekti: metin uzar, aynı satır
                (0.25, "They're watching us. We need"),
                (0.45, "They're watching us. We need to move, now!")]),
    (0.9, 0.0, [(GATE_S, "Get in theccar. We're going to Vice City."),  # bozuk okuma → temiz okuma, aynı satır
                (0.35, "Get in the car. We're going to Vice City.")]),
    (0.4, 0.0, [(GATE_S, "Who are those guys?")]),
    (0.6, 0.0, [(GATE_S, "Cops. Keep your head down."), (0.40, "Cops. Keep your head down.")]),  # titreme
    (0.4, 0.0, [(GATE_S, "Hold on tight!")]),
    (1.0, 0.3, [(GATE_S, "We need to lose them before the bridge.")]),
    (0.5, 0.0, [(GATE_S, "Take the next exit.")]),
    (0.4, 0.0, [(GATE_S, "I see them behind us!")]),
    (0.8, 0.5, [(GATE_S, "Okay, we lost them. Breathe.")]),
])

# Çatışma anı: kısa bağırışlar art arda (0,3 sn), OCR bunlardan yavaşken (ör. işlemci yükü altında)
BURST = Dialogue([
    (0.3, 0.0, [(GATE_S, "Go, go, go!")]),
    (0.3, 0.0, [(GATE_S, "Get down!")]),
    (0.3, 0.0, [(GATE_S, "They're shooting at us!")]),
    (0.3, 0.0, [(GATE_S, "Drive, Jason, drive!")]),
    (0.3, 0.0, [(GATE_S, "Left, take the left!")]),
    (0.3, 0.0, [(GATE_S, "Watch out for that truck!")]),
    (0.8, 0.4, [(GATE_S, "Hold on to something.")]),
])


@dataclass(frozen=True)
class Scenario:
    name: str
    main_s: float  # asıl çeviri motorunun süresi
    remote: bool  # asıl motor uzak mı (Ollama/Gemini); öyleyse quickmt hızlı/yedek çeviri olarak da hazır
    hybrid: bool = True  # "önce hızlı yerel çeviriyi göster"
    ocr_s: float = OCR_S
    flow_mode: str = "flow"
    dialogue: Dialogue = DIALOGUE

    @property
    def main_label(self) -> str:
        return MAIN_REMOTE_LABEL if self.remote else QUICK_LABEL


QUICKMT = Scenario("quickmt (50 ms)", QUICK_S, remote=False)
HYBRID_600 = Scenario("karma: quickmt + uzak 600 ms", 0.6, remote=True)
HYBRID_1000 = Scenario("karma: quickmt + uzak 1000 ms", 1.0, remote=True)
REMOTE_600 = Scenario("uzak 600 ms, karma kapalı", 0.6, remote=True, hybrid=False)
SLOW_OCR = Scenario("quickmt, yavaş OCR 400 ms", QUICK_S, remote=False, ocr_s=0.4)
HYBRID_600_SINGLE = Scenario("karma 600 ms, tek satır kipi", 0.6, remote=True, flow_mode="single")
BURST_SLOW_OCR = Scenario("çatışma: 0,3 sn satırlar, OCR 450 ms", QUICK_S, remote=False, ocr_s=0.45,
                          dialogue=BURST)
BURST_HYBRID = Scenario("çatışma: 0,3 sn satırlar, karma 600 ms", 0.6, remote=True, dialogue=BURST)
SCENARIOS = [QUICKMT, HYBRID_600, HYBRID_1000, REMOTE_600, SLOW_OCR, HYBRID_600_SINGLE, BURST_SLOW_OCR,
             BURST_HYBRID]
HYBRIDS = [sc for sc in SCENARIOS if sc.remote and sc.hybrid]


# --- benzetim altyapısı ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Frame:
    text: str


class Sim:
    """Ayrık olay benzetimi: sanal saat + işçiler. Sahte motorlar harcadıkları süreyi `cost`a ekler."""

    def __init__(self):
        self.t = 0.0
        self.cost = 0.0
        self._heap: list = []
        self._n = 0
        self.workers: list[Worker] = []

    def at(self, t: float, fn) -> None:
        heapq.heappush(self._heap, (t, self._n, fn))
        self._n += 1

    def worker(self, take, job) -> None:
        self.workers.append(Worker(self, take, job))

    def run(self) -> None:
        while True:
            while any(w.poll() for w in self.workers):  # boştaki işçiler sıradaki öğeyi alır
                pass
            if not self._heap:
                return
            self.t, _, fn = heapq.heappop(self._heap)
            fn()


class Worker:
    """Bir iş parçacığının benzeri: kuyruğundan öğe alır; iş üreteci `yield süre` ile meşgul kalır."""

    def __init__(self, sim: Sim, take, job):
        self.sim, self.take, self.job = sim, take, job
        self.gen = None

    def poll(self) -> bool:
        if self.gen is not None:
            return False
        item = self.take()
        if item is None:
            return False
        self.gen = self.job(item)
        self._step()
        return True

    def _step(self) -> None:
        try:
            d = next(self.gen)
        except StopIteration:
            self.gen = None
            return
        self.sim.at(self.sim.t + d, self._step)


class FakeOcr:
    name = "sahte OCR"

    def __init__(self, sim: Sim, cost: float):
        self.sim, self.cost = sim, cost

    def recognize(self, frame: Frame):
        self.sim.cost += self.cost
        return [OcrLine(frame.text, 0.95, 40, 300, 600, 30)]

    def close(self) -> None:
        pass


class FakeTr(Translator):
    def __init__(self, sim: Sim, key: str, cost: float):
        self.sim, self.key, self.label, self.cost = sim, key, f"sahte {key}", cost
        self.calls: list[str] = []

    def translate(self, text, context=()):
        self.sim.cost += self.cost
        self.calls.append(text)
        return f"{self.key}: {text}"


def _settings(sc: Scenario) -> Settings:
    s = Settings()
    s.translate.engine = "gemini" if sc.remote else "quickmt"
    s.translate.hybrid = sc.hybrid
    s.flow.mode = sc.flow_mode
    return s


Out = list[tuple[float, str, "dict | None"]]


def run_new(pipe: Pipeline, sc: Scenario) -> Out:
    """Yeni işlem hattı: FIFO kuyruklar + karma modda ayrı uzak çeviri işçisi."""
    sim = Sim()
    out: Out = []
    pipe.cache = TranslationCache()
    pipe.subtitle.connect(lambda ev: out.append((sim.t, "sub", ev)))
    pipe.gone.connect(lambda: out.append((sim.t, "gone", None)))
    pipe.same.connect(lambda: out.append((sim.t, "same", None)))
    engine = FakeOcr(sim, sc.ocr_s)
    main = FakeTr(sim, "uzak" if sc.remote else "hizli", sc.main_s)
    fast = FakeTr(sim, "hizli", QUICK_S) if sc.remote else None  # _prepare_translators ile aynı
    cx = TrContext(pipe.s.translate, main, fast)

    def ocr_job(item):
        sim.cost = 0.0
        res = pipe._ocr_item(engine, item)
        yield sim.cost
        for x in res:
            pipe._post_tr(x)

    def tr_job(item):
        sim.cost = 0.0
        acts = pipe._translate_item(item, cx)
        yield sim.cost
        pipe._apply(acts)

    def remote_job(job):
        sim.cost = 0.0
        acts = pipe._remote_item(job, cx)
        yield sim.cost
        pipe._apply(acts)

    for t, item in sc.dialogue.schedule():
        sim.at(t, lambda item=item: pipe._ocr_q.put(item))
    sim.worker(lambda: pipe._ocr_q.get(0), ocr_job)
    sim.worker(lambda: pipe._tr_q.get(0), tr_job)
    sim.worker(lambda: pipe._remote_q.get(0), remote_job)
    sim.run()
    return out


def run_old(pipe: Pipeline, sc: Scenario) -> Out:
    """Eski davranış (2245179 ve öncesi): yakalama → LatestSlot → OCR → LatestSlot → tek çeviri iş
    parçacığı. Çeviri adımı eski _translate_loop'un birebir aktarımıdır (önbellek, karma modda önce hızlı
    çeviri, `seq` denetimleri). OCR mantığı değişmediği için _ocr_item kullanılır; sonucu OCR bitince
    uygulanır, eski kodda olduğu gibi gone/same OCR iş parçacığından hemen yayınlanır."""
    sim = Sim()
    out: Out = []
    ocr_slot, tr_slot = LatestSlot(), LatestSlot()
    cache = TranslationCache()
    engine = FakeOcr(sim, 0.0)  # süre aşağıda sc.ocr_s olarak sayılır
    main_key = "uzak" if sc.remote else "hizli"
    two = sc.remote and sc.hybrid  # eski kod: fast is not None and tc.hybrid

    def emit(text: str, ev: dict, engine_key: str) -> None:
        out.append((sim.t, "sub", {"text": text, "original": ev["text"], "engine": f"sahte {engine_key}",
                                   "line": ev["line"]}))

    def ocr_job(item):
        if item[0] == "text":
            yield sc.ocr_s
        for kind, _t, ev in pipe._ocr_item(engine, item):
            if kind == "sub":
                tr_slot.put(ev)
            else:
                out.append((sim.t, kind, None))

    def tr_job(ev):
        ck = f"{main_key}:|{ev['key']}"
        res = cache.get(ck)
        if res is None:
            if two:
                yield QUICK_S
                if ev["seq"] == pipe.tracker.seq:
                    emit(f"hizli: {ev['text']}", ev, "hizli")
            yield sc.main_s
            res = f"{main_key}: {ev['text']}"
            cache.put(ck, res)
        if ev["seq"] != pipe.tracker.seq:
            return  # "bu sırada yeni altyazı geldi"
        emit(res, ev, main_key)

    for t, item in sc.dialogue.schedule():
        sim.at(t, lambda item=item: ocr_slot.put(item))
    sim.worker(lambda: ocr_slot.get(0), ocr_job)
    sim.worker(lambda: tr_slot.get(0), tr_job)
    sim.run()
    return out


def _subs(out: Out, dlg: Dialogue) -> list[tuple[float, int, dict]]:
    """(zaman, diyalog satırı, olay) — yalnız altyazı olayları."""
    return [(t, dlg.line_of[ev["original"]], ev) for t, kind, ev in out if kind == "sub"]


def lost_lines(out: Out, dlg: Dialogue) -> int:
    """Hiç gösterilmeyen diyalog satırı sayısı."""
    return dlg.n - len({i for _, i, _ in _subs(out, dlg)})


def lines_without(out: Out, dlg: Dialogue, engine_label: str) -> int:
    """Verilen motorun çevirisi hiç gösterilmeyen satır sayısı (karma modda: LLM çevirisi gelmeyenler)."""
    return dlg.n - len({i for _, i, ev in _subs(out, dlg) if ev["engine"] == engine_label})


def first_show_delays(out: Out, dlg: Dialogue) -> list[float]:
    """Her satırın ilk okunmasından ekranda ilk görünmesine kadar geçen süre (gösterilmeyenler hariç)."""
    first: dict[int, float] = {}
    for t, i, _ in _subs(out, dlg):
        first.setdefault(i, t)
    reads = dlg.first_reads()
    return [first[i] - reads[i] for i in sorted(first)]


def compare(make, sc: Scenario) -> dict:
    """Aynı diyaloğu eski ve yeni işlem hattıyla çalıştırır; karşılaştırma sayıları."""
    dlg = sc.dialogue
    old, new = run_old(make(sc), sc), run_new(make(sc), sc)
    return {
        "lost_old": lost_lines(old, dlg), "lost_new": lost_lines(new, dlg),
        "no_main_old": lines_without(old, dlg, sc.main_label), "no_main_new": lines_without(new, dlg, sc.main_label),
        "delay_old": max(first_show_delays(old, dlg), default=0.0), "delay_new": max(first_show_delays(new, dlg)),
    }


@pytest.fixture
def make_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")  # kullanıcının önbelleğine dokunma
    return lambda sc: Pipeline(_settings(sc))


# --- yeni işlem hattı: hiçbir satır kaybolmaz ---------------------------------------------------------
@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda sc: sc.name)
def test_fast_dialogue_every_line_once_in_order(make_pipe, sc):
    pipe = make_pipe(sc)
    out = run_new(pipe, sc)
    dlg = sc.dialogue
    subs = _subs(out, dlg)
    assert lost_lines(out, dlg) == 0
    assert pipe._ocr_q.dropped == pipe._tr_q.dropped == pipe._remote_q.dropped == 0

    # her diyalog satırının bütün olayları tek bir kimlik taşır; kimlikler satır sırasıyla birer artar
    ids: dict[int, set[int]] = {}
    for _, i, ev in subs:
        ids.setdefault(i, set()).add(ev["line"])
    assert all(len(v) == 1 for v in ids.values()), ids
    line_id = [ids[i].pop() for i in range(dlg.n)]
    assert line_id == list(range(line_id[0], line_id[0] + dlg.n))

    # her kimlik tam bir kez "yeni" olarak, sırayla ortaya çıkar; sonrakiler aynı satırın güncellemesidir
    firsts: list[int] = []
    for _, _, ev in subs:
        if ev["line"] not in firsts:
            firsts.append(ev["line"])
    assert firsts == line_id

    # her satırda en son gösterilen, satırın son (en iyi / en uzun) okumasıdır; eski okumanın geç gelen
    # çevirisi yenisini ezmez
    last = {i: ev for _, i, ev in subs}
    for i in range(dlg.n):
        assert last[i]["original"] == dlg.final_text(i)
    for i in range(dlg.n):
        texts = [ev["original"] for _, j, ev in subs if j == i]
        assert texts == sorted(texts, key=lambda s: [_canon(r) for _, r in dlg.lines[i][2]].index(s))

    # 'gone' işaretleri kapattıkları satırdan sonra, bir sonraki satırdan önce gelir
    order = [(kind, dlg.line_of[ev["original"]] if ev else None) for _, kind, ev in out]
    first_pos = {i: order.index(("sub", i)) for i in range(dlg.n)}
    gone_pos = [k for k, (kind, _) in enumerate(order) if kind == "gone"]
    assert len(gone_pos) == len(dlg.gap_lines())
    for pos, i in zip(gone_pos, dlg.gap_lines()):
        assert first_pos[i] < pos < first_pos.get(i + 1, len(order))


@pytest.mark.parametrize("sc", HYBRIDS, ids=lambda sc: sc.name)
def test_slow_remote_never_delays_new_lines(make_pipe, sc):
    """Karma mod: yeni satır uzak motoru beklemeden hızlı çeviriyle hemen gelir; uzak motorun yanıtı geç de
    gelse yalnız kendi satırını (aynı kimlik) günceller, yeni kimlik açmaz, yeni satırı ezmez."""
    pipe = make_pipe(sc)
    out = run_new(pipe, sc)
    dlg = sc.dialogue
    subs = _subs(out, dlg)

    newest = 0
    for _, kind, ev in out:
        if kind != "sub":
            continue
        if ev["engine"] == MAIN_REMOTE_LABEL and not ev["cached"]:
            assert ev["line"] <= newest  # güncelleme: satırı zaten gösterilmiş bir kimliğe gider
        else:
            assert ev["line"] >= newest  # yeni satır ya da en yeni satırın yeni okuması, hızlı çeviriyle
            newest = ev["line"]
    assert max(first_show_delays(out, dlg)) <= sc.ocr_s + QUICK_S + 0.12  # uzak motorun süresi eklenmez

    ups = [ev["line"] for _, _, ev in subs if ev["engine"] == MAIN_REMOTE_LABEL]
    assert ups and ups == sorted(ups)  # güncellemeler satır sırasıyla
    last = subs[-1][2]
    assert last["engine"] == MAIN_REMOTE_LABEL and dlg.line_of[last["original"]] == dlg.n - 1  # en son satır da
    if sc.flow_mode == "flow" and sc.main_s <= 0.6 and dlg is DIALOGUE:
        assert lines_without(out, dlg, MAIN_REMOTE_LABEL) == 0  # uzak motor yetişiyorsa her satır LLM çevirisi alır


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda sc: sc.name)
def test_old_vs_new_lost_lines(make_pipe, sc):
    """Eski tek yuvalı kutularla aynı diyalog: kaç satır hiç gösterilmiyordu, şimdi kaç (-s ile yazdırılır)."""
    r = compare(make_pipe, sc)
    n = sc.dialogue.n
    msg = (f"\n[{sc.name}] kaybolan satır: eski {r['lost_old']}/{n}, yeni {r['lost_new']}/{n}; asıl motorun "
           f"çevirisi gelmeyen: eski {r['no_main_old']}, yeni {r['no_main_new']}; ilk görünme gecikmesi en çok: "
           f"eski {r['delay_old']:.2f} sn, yeni {r['delay_new']:.2f} sn")
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(msg.encode(enc, "replace").decode(enc))  # Windows konsolu (cp1252) Türkçe harfte çökmesin
    assert r["lost_new"] == 0 and r["lost_old"] >= r["lost_new"]
    # eski davranışın kayıpları (kullanıcının bildirdiği hata): yavaş motorda / OCR'dan hızlı konuşmada satırlar
    # hiç görünmüyordu, karma modda satırların çoğu LLM çevirisini hiç almıyordu
    if sc in (HYBRID_1000, REMOTE_600, BURST_SLOW_OCR, BURST_HYBRID):
        assert r["lost_old"] > 0
    if sc.remote:
        assert r["no_main_old"] > r["no_main_new"]


# --- parçalar ---------------------------------------------------------------------------------------
def test_fifo_queue_order_merge_overflow(caplog):
    q = FifoQueue(3, "deneme")
    for i in range(3):
        q.put(i)
    assert len(q) == 3 and q.peek() == 0 and q.has(lambda v: v == 2)
    with caplog.at_level(logging.WARNING, logger="altyazitr.pipeline"):
        q.put(3)  # dolu: en eskisi (0) atılır, uyarı yazılır
    assert q.dropped == 1 and "deneme" in caplog.text
    assert [q.get(0) for _ in range(4)] == [1, 2, 3, None]
    q.put(("same", 1, None))
    q.put(("same", 2, None), merge=lambda v: v[0] == "same")  # art arda işaret tek işarete iner
    q.put(("gone", 3, None), merge=lambda v: v[0] == "gone")  # sondaki öğe için tutmazsa sona eklenir
    assert [q.get(0) for _ in range(3)] == [("same", 2, None), ("gone", 3, None), None]
    q.put(1)
    q.clear()
    assert len(q) == 0 and q.get(0) is None


def test_tracker_line_ids():
    tr = Tracker()
    a = tr.on_text("They're watching us.", None, 0.0)
    b = tr.on_text("They're watching us. We need to move, now!", None, 0.4)  # uzadı: aynı satır
    c = tr.on_text("Get in the car.", None, 0.9)  # yeni satır
    tr.on_gone(1.2)
    assert tr.on_text("Get in the car.", None, 1.5) is None  # kısa süre kaybolup döndü
    tr.on_gone(2.0)
    d = tr.on_text("Get in the car.", None, 6.0)  # uzun süre sonra yeniden söylendi: yeni satır
    tr.on_gone(6.5)
    e = tr.on_text("Get in the car. We're going to Vice City.", None, 12.0)  # eskisinin uzantısı ama çok sonra
    assert [x["line"] for x in (a, b, c, d, e)] == [1, 1, 2, 3, 4]
    assert b["replace"] and not e["replace"]
    assert Tracker(line_base=e["line"]).on_text("Hello there, Jason!", None, 0.0)["line"] == 5  # yeniden başlatma


def test_late_remote_answer_does_not_override_newer_reading(make_pipe):
    """Uzak motor bir satırın eski okumasını çevirirken aynı satırın yeni okuması (burada daktilo ile uzayan
    metin) gösterildiyse eski okumanın geç gelen çevirisi yayınlanmaz; yeni okumanın kendi çevirisi gelir."""
    pipe = make_pipe(HYBRID_600)
    sim = Sim()
    main, fast = FakeTr(sim, "uzak", 0.6), FakeTr(sim, "hizli", QUICK_S)
    cx = TrContext(pipe.s.translate, main, fast)
    got = []
    pipe.subtitle.connect(got.append)
    v1 = pipe.tracker.on_text("They're watching us.", None, 1.0)
    pipe._apply(pipe._translate_item(("sub", 1.0, v1), cx))  # hızlı çeviri + uzak iş
    late = pipe._remote_item(pipe._remote_q.get(0), cx)  # uzak motor v1'i çeviriyor...
    v2 = pipe.tracker.on_text("They're watching us. We need to move, now!", None, 1.3)
    assert v2["line"] == v1["line"]
    pipe._apply(pipe._translate_item(("sub", 1.3, v2), cx))  # ...bu sırada v2 hızlı çeviriyle gösterildi
    pipe._apply(late)  # v1'in geç yanıtı: yayınlanmamalı
    pipe._apply(pipe._remote_item(pipe._remote_q.get(0), cx))
    assert [(ev["engine"], ev["original"]) for ev in got] == [
        (QUICK_LABEL, v1["text"]), (QUICK_LABEL, v2["text"]), (MAIN_REMOTE_LABEL, v2["text"])]
    assert {ev["line"] for ev in got} == {v1["line"]}


def test_ocr_burst_old_slot_loses_lines_fifo_does_not(make_pipe):
    """OCR'dan hızlı gelen satırlar: tek yuvalı kutu bekleyen satırı ezer, FIFO hepsini sırayla okur."""
    r = compare(make_pipe, BURST_SLOW_OCR)
    assert r["lost_old"] >= 2 and r["lost_new"] == 0


# --- gerçek iş parçacıklarıyla kısa uçtan uca deneme ---------------------------------------------------
class _SleepOcr:
    name = "uyuyan OCR"

    def recognize(self, frame: Frame):
        time.sleep(0.06)
        return [OcrLine(frame.text, 0.95, 40, 300, 600, 30)]

    def close(self) -> None:
        pass


class _SleepTr(Translator):
    def __init__(self, key: str, cost: float):
        self.key, self.label, self.cost = key, f"uyuyan {key}", cost

    def translate(self, text, context=()):
        time.sleep(self.cost)
        return f"{self.key}: {text}"


def test_threads_end_to_end_hybrid(make_pipe, monkeypatch):
    """Gerçek iş parçacıkları (_ocr_loop, _translate_loop ve uzak çeviri iş parçacığı), satırlar 150 ms
    arayla: her satır tam bir kez yeni kimlikle ve sırayla gelir, en son satır uzak çeviriyi de alır,
    durdurunca bütün iş parçacıkları kapanır."""
    main, fast = _SleepTr("uzak", 0.25), _SleepTr("hizli", 0.02)
    monkeypatch.setattr(pl, "create_engine", lambda *a, **k: _SleepOcr())
    monkeypatch.setattr(pl, "create_translator", lambda tc, glossary: main)
    monkeypatch.setattr(pl, "QuickMtTranslator", lambda glossary, device="cpu": fast)
    pipe = make_pipe(HYBRID_600)
    got: list[dict] = []
    pipe.subtitle.connect(got.append, Qt.ConnectionType.DirectConnection)  # olay döngüsü yok: iş parçacığında çağır
    texts = ["Get in the car, now.", "Where are we going?", "Just drive, okay?", "Who are those guys?",
             "Keep your head down.", "We need to lose them before the bridge."]
    pipe._run.set()
    threads = [threading.Thread(target=pipe._ocr_loop, daemon=True),
               threading.Thread(target=pipe._translate_loop, daemon=True)]
    for th in threads:
        th.start()
    try:
        for txt in texts:
            pipe._ocr_q.put(("text", time.monotonic(), Frame(txt)))
            time.sleep(0.15)
        end = time.monotonic() + 10
        while time.monotonic() < end and not any(ev["engine"] == main.label and ev["original"] == texts[-1]
                                                 for ev in list(got)):
            time.sleep(0.02)
    finally:
        pipe.stop()
        for th in threads:
            th.join(5)
    assert not any(th.is_alive() for th in threads)
    assert not any(th.name == "ceviri-uzak" and th.is_alive() for th in threading.enumerate())
    quick = [ev for ev in got if ev["engine"] == fast.label]
    assert [ev["original"] for ev in quick] == texts
    first = quick[0]["line"]
    assert [ev["line"] for ev in quick] == list(range(first, first + len(texts)))
    ups = [ev for ev in got if ev["engine"] == main.label]
    assert ups and ups[-1]["original"] == texts[-1] and ups[-1]["line"] == quick[-1]["line"]
    assert [ev["line"] for ev in ups] == sorted(ev["line"] for ev in ups)
    assert all(ev["line"] in {q["line"] for q in quick} for ev in ups)
