""""Oyun altyazısının üzerine" açıkken üst üste yazılar: bölgede aynı anda birden çok ayrı yazı (altta konuşma
altyazısı, üstünde daha küçük görev yazısı) ayrı okunur, ayrı çevrilir ve her birinin üstüne ayrı kapak konur.

İşlem hattı (öbek → yuva, ayrı takipçi, ortak satır kimliği sayacı, yuva başına 'gone' / 'same', LLM bağlamı), örtme
kapalıyken eski davranışın aynen sürmesi, gösterim (kapak başına süreler, yalnız kaybolan yazının kapağının kalkması),
sunucu iletisi, OBS sayfası (birden çok kutu, eski ileti biçimi) ve ekrandaki kutu (tek saydam pencerede birden çok
kapak).

Ekran gerektirmez: Qt 'offscreen' QApplication'dan hemen önce, fikstürün içinde ayarlanır (bkz. test_cover)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import numpy as np
import pytest

from altyazitr.config import Settings
from altyazitr.ocr import OcrLine
from altyazitr.paths import resource_dir
from altyazitr.server import OverlayServer
from altyazitr.textproc import MAX_SLOTS, subtitle_blocks, subtitle_rows
from altyazitr.translate import Translator

ROI = (1536, 216)  # bölge (genişlik, yükseklik)
TALK = "We need to get out of here before the cops show up."
TALK2 = "Fine, but you are driving this time."
TASK = "Chase Arnold."


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


def _ln(text, x, y, w, h, conf=0.95):
    return OcrLine(text, conf, x, y, w, h)


def talk(text=TALK):
    """Altta konuşma altyazısı (satır boyu 40)."""
    return _ln(text, 300, 150, 900, 40)


def task(text=TASK):
    """Konuşmanın üstünde daha küçük görev yazısı (satır boyu 26)."""
    return _ln(text, 640, 70, 250, 26)


def _img():
    return np.zeros((ROI[1], ROI[0], 3), np.uint8)


def _engine(*lines):
    return SimpleNamespace(recognize=lambda _img: list(lines))


@pytest.fixture
def make_pipe(tmp_path, monkeypatch):
    from altyazitr import pipeline as pl

    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")  # kullanıcının önbelleğine dokunma

    def make(cover: bool = True):
        s = Settings()
        s.ocr.subtitle_only = False  # sahte kare: biçim süzgeci ayrı testte (test_subtitle_style)
        s.style.highlight = s.screen_style.highlight = False
        s.screen_style.cover_original = cover
        return pl.Pipeline(s)

    return make


def _read(pipe, t, *lines, kind="text"):
    return pipe._ocr_item(_engine(*lines), (kind, t, _img() if kind == "text" else None))


def _subs(out):
    return [ev for kind, _t, ev in out if kind == "sub"]


# --- öbekler ve takipçiler -------------------------------------------------------------------------------------------
def test_obekler_ve_eski_satir_secimi_ayni():
    lines = [task(), talk()]
    blocks = subtitle_blocks(lines)
    assert [[x.text for r in b for x in r] for b in blocks] == [[TASK], [TALK]]  # üstten alta
    assert [x.text for r in subtitle_rows(lines) for x in r] == [TALK]  # keep_lowest_block: en alttaki öbek
    assert [x.text for r in subtitle_rows(lines, False) for x in r] == [TASK, TALK]
    assert subtitle_blocks([]) == []


def test_yuvalar_ortak_kimlik_sayacindan():
    from altyazitr.pipeline import LineIds, Tracker

    assert Tracker(5).on_text("Hello there, Jason!", None, 0.0)["line"] == 6  # tek takipçi: eskisi gibi
    ids = LineIds(5)
    a, b = Tracker(ids=ids), Tracker(ids=ids)
    assert a.on_text("Get in the car.", None, 0.0)["line"] == 6
    assert b.on_text("Chase Arnold.", None, 0.0)["line"] == 7
    assert a.on_text("We are going to Vice City.", None, 1.0)["line"] == 8
    assert b.on_text("Chase Arnold.", None, 1.0) is None  # aynı yazı: titreme
    assert ids.last == 8


# --- işlem hattı: örtme açık ----------------------------------------------------------------------------------------
def test_iki_yazi_iki_yuva_iki_olay_iki_kutu(make_pipe):
    pipe = make_pipe()
    out = _read(pipe, 1000.0, task(), talk())
    assert [k for k, _t, _v in out] == ["sub", "sub"]
    a, b = _subs(out)  # önce karedeki asıl yazı (konuşma), sonra görev yazısı
    assert (a["text"], b["text"]) == (TALK, TASK)
    assert a["slot"] != b["slot"] and a["line"] != b["line"]  # ayrı yuva, çakışmayan kimlik
    assert a["in_context"] is True and b["in_context"] is False  # LLM bağlamına yalnız konuşma girer
    w, h = ROI
    assert a["box"]["rect"] == pytest.approx([300 / w, 150 / h, 900 / w, 40 / h], abs=1e-5)
    assert b["box"]["rect"] == pytest.approx([640 / w, 70 / h, 250 / w, 26 / h], abs=1e-5)
    assert a["box"]["rows"] == b["box"]["rows"] == 1

    # aynı kare: iki yazı da duruyor (her yuvanın kendi 'same' işareti)
    assert _read(pipe, 1000.3, task(), talk()) == [("same", 1000.3, a["slot"]), ("same", 1000.3, b["slot"])]

    # konuşma kayboldu, görev yazısı duruyor: yalnız konuşmanın satırı biter
    assert _read(pipe, 1001.0, task()) == [("gone", 1001.0, a["slot"]), ("same", 1001.0, b["slot"])]
    assert _read(pipe, 1001.3, task()) == [("same", 1001.3, b["slot"])]  # bitiş bir kez söylenir

    # yeni konuşma aynı yerde: aynı yuva, yeni satır (kimlik hep artar)
    out = _read(pipe, 1003.0, task(), talk(TALK2))
    c = _subs(out)[0]
    assert c["slot"] == a["slot"] and c["text"] == TALK2 and c["line"] > b["line"]
    assert ("same", 1003.0, b["slot"]) in out

    # bölgede yazı kalmadı: iki yuvanın da satırı biter
    assert sorted(_read(pipe, 1004.0, kind="gone")) == sorted([("gone", 1004.0, a["slot"]),
                                                                ("gone", 1004.0, b["slot"])])


def test_yazi_kisa_sure_kaybolup_donerse_ayni_satir(make_pipe):
    pipe = make_pipe()
    a, b = _subs(_read(pipe, 1000.0, task(), talk()))
    assert _read(pipe, 1000.5, talk()) == [("gone", 1000.5, b["slot"]), ("same", 1000.5, a["slot"])]
    assert _read(pipe, 1001.0, task(), talk()) == [("same", 1001.0, a["slot"]), ("same", 1001.0, b["slot"])]


def test_en_cok_uc_yuva_en_alttakiler(make_pipe):
    assert MAX_SLOTS == 3
    pipe = make_pipe()
    texts = ["Top banner text here.", "Find the second clue.", "Chase Arnold now.", "We have to move, now."]
    lines = [_ln(t, 500, 4 + 60 * i, 400, 24) for i, t in enumerate(texts)]
    subs = _subs(_read(pipe, 1000.0, *lines))
    assert sorted(ev["text"] for ev in subs) == sorted(texts[1:])  # en üstteki atılır
    assert len({ev["slot"] for ev in subs}) == 3 and len(pipe._slots) == 3


def test_ortme_kapali_eski_davranis_aynen(make_pipe):
    """Örtme kapalıyken: tek takipçi, en alttaki öbek, olayda yuva alanı yok, işaretlerde değer None."""
    pipe = make_pipe(cover=False)
    [(kind, t, ev)] = _read(pipe, 1000.0, task(), talk())
    assert (kind, t, ev["text"], ev["line"]) == ("sub", 1000.0, TALK, 1)
    assert set(ev) == {"seq", "line", "text", "speaker", "key", "replace", "t", "ocr_ms", "spans", "box"}
    assert ev["box"]["rect"][1] == pytest.approx(150 / ROI[1], abs=1e-5)  # kutu yalnız konuşmanın
    assert _read(pipe, 1000.3, task(), talk()) == [("same", 1000.3, None)]
    # konuşma gidince en alttaki öbek görev yazısıdır: aynı tek satır dizisinde yeni satır (bugünkü gibi)
    [(kind, _t, ev2)] = _read(pipe, 1001.0, task())
    assert (kind, ev2["text"], ev2["line"]) == ("sub", TASK, 2) and "slot" not in ev2
    assert _read(pipe, 1002.0, kind="gone") == [("gone", 1002.0, None)]
    assert pipe._slots == [] and pipe._multi_was is False
    out = pipe._event(ev, "Kaçmamız lazım.", time.perf_counter(), False, "sahte")
    assert "slot" not in out


def test_ortme_calisirken_acilip_kapanir(make_pipe):
    pipe = make_pipe(cover=False)
    [(_k, _t, ev)] = _read(pipe, 1000.0, talk())
    pipe.s.screen_style.cover_original = True  # açıldı: tek takipçinin satırı biter, yazı yuvada yeni satır
    out = _read(pipe, 1000.5, talk())
    assert out[0] == ("gone", 1000.5, None)
    [a] = _subs(out)
    assert a["text"] == TALK and a["line"] == ev["line"] + 1 and a["slot"] is not None
    pipe.s.screen_style.cover_original = False  # kapandı: yuvaların satırları biter, tek takipçi yeniden okur
    out = _read(pipe, 1001.0, talk())
    assert out[0] == ("gone", 1001.0, a["slot"]) and _subs(out)[0]["line"] == a["line"] + 1
    assert pipe._slots == []


def test_ayni_yuvanin_ayni_isaretleri_birlesir(make_pipe):
    pipe = make_pipe()
    for item in [("same", 1.0, 1), ("same", 1.1, 1), ("same", 1.2, 2), ("same", 1.3, 2), ("same", 1.4, None)]:
        pipe._post_tr(item)
    assert pipe._tr_q.snapshot() == [("same", 1.1, 1), ("same", 1.3, 2), ("same", 1.4, None)]


def test_yeni_okuma_sirada_ise_eskisi_atlanir(make_pipe):
    pipe = make_pipe()
    ev = {"line": 5, "slot": 1}
    pipe._tr_q.put(("same", 1.0, 2))
    pipe._tr_q.put(("sub", 1.0, {"line": 5, "slot": 1}))
    assert pipe._newer_queued(ev)  # araya öbür yazının işareti girse de
    pipe._tr_q.clear()
    pipe._tr_q.put(("gone", 1.0, 1))
    pipe._tr_q.put(("sub", 1.0, {"line": 5, "slot": 1}))
    assert not pipe._newer_queued(ev)  # arada kendi 'gone' işareti var: bu okuma da gösterilir
    assert not pipe._newer_queued({"line": 5})  # tek yazı: yalnız hemen sıradaki öğeye bakılır (bugünkü gibi)


# --- çeviri ve gösterim, uçtan uca ---------------------------------------------------------------------------------
class FakeTr(Translator):
    def __init__(self, key: str):
        self.key, self.label, self.model = key, f"sahte {key}", ""
        self.contexts: dict[str, list] = {}

    def prepare(self, status) -> None:
        pass

    def translate(self, text, context=()):
        self.contexts[text] = list(context)
        return f"{self.key}: {text}"

    def close(self) -> None:
        pass


class _Out:
    """Sunucu ve köprü yerine: OBS'e giden iletileri sırayla kaydeder."""

    def __init__(self):
        self.lines, self.masks, self.covers, self.styles = [], [], [], []

    def submit(self, coro):
        asyncio.run(coro)

    async def set_lines(self, lines):
        self.lines.append(lines)

    async def set_mask(self, rect):
        self.masks.append(rect)

    async def set_cover(self, cover):
        self.covers.append(cover)

    async def set_style(self, vars_):
        self.styles.append(vars_)


class _Box:
    """Ekrandaki kutunun yerine: set_lines / set_cover çağrılarını kaydeder."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def set_lines(self, lines):
        self.calls.append(("lines", [dict(x) for x in lines]))

    def set_cover(self, cover):
        self.calls.append(("cover", cover))

    def clear(self):
        self.calls.append(("lines", []))

    def update(self):
        pass

    def last(self, kind):
        return next((v for k, v in reversed(self.calls) if k == kind), None)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def wired(qapp, make_pipe, monkeypatch):
    """İşlem hattı (örtme iki görünümde de açık, tek aşamalı sahte çeviri) → gerçek SubtitleDisplay → sahte çıktılar.
    Sinyaller aynı iş parçacığında: doğrudan bağlantı, adımlar sırayla."""
    import altyazitr.display as dmod
    from altyazitr.display import SubtitleDisplay
    from altyazitr.overlay import to_logical
    from altyazitr.pipeline import TrContext

    monkeypatch.setattr(dmod, "region_screen_rect", lambda c: [0, 864, 1536, 216])
    monkeypatch.setattr(dmod, "screen_area", lambda r: to_logical(r, [((0, 0, 1920, 1080), 1.0)]))
    pipe = make_pipe()
    s = pipe.s
    s.style.cover_original = True
    s.overlay.enabled = True
    s.flow.max_show_s, s.flow.min_show_s, s.ocr.hide_delay_ms = 7.0, 3.0, 700
    s.capture.mode, s.capture.region = "window", [0.0, 0.8, 0.8, 0.2]
    out, box = _Out(), _Box()
    d = SubtitleDisplay(s, out, out, box)
    clock = _Clock()
    d.clock = pipe.clock = clock
    main = FakeTr("asıl")
    cx = TrContext(s.translate, main, None)
    assert not cx.two_stage
    pipe.subtitle.connect(d.on_subtitle)
    pipe.gone.connect(d.on_gone)
    pipe.same.connect(d.on_same)

    def frame(*lines, kind="text"):
        for item in _read(pipe, clock.t, *lines, kind=kind):
            pipe._apply(pipe._translate_item(item, cx))

    def advance(sec):
        clock.t += sec
        if d._hide.isActive():
            d._tick()

    try:
        yield SimpleNamespace(pipe=pipe, d=d, out=out, box=box, clock=clock, main=main, cx=cx, frame=frame,
                              advance=advance)
    finally:
        d._hide.stop()


def test_uctan_uca_iki_kapak_ve_yalniz_kaybolanin_kapagi_kalkar(wired):
    w = wired
    w.frame(task(), talk())
    covers = w.out.covers[-1]
    assert isinstance(covers, list) and len(covers) == 2  # OBS: iki kapak aynı anda
    talk_c, task_c = covers  # eskiden yeniye (konuşma önce okundu)
    assert talk_c["line"]["text"] == f"asıl: {TALK}" and task_c["line"]["text"] == f"asıl: {TASK}"
    assert talk_c["slot"] != task_c["slot"]
    assert task_c["rect"][1] + task_c["rect"][3] < talk_c["rect"][1]  # görev yazısının kutusu konuşmanınkinin üstünde
    assert task_c["font"] < talk_c["font"]  # her kutu kendi yazısının boyunda
    screen = w.box.last("cover")
    assert isinstance(screen, list) and [c["slot"] for c in screen] == [talk_c["slot"], task_c["slot"]]
    assert w.out.lines[-1] == [] and w.box.last("lines") is None  # örtme açık görünüm akış göstermez

    # LLM bağlamına görev yazısı girmez: sonraki konuşma yalnız önceki konuşmayı bağlam olarak alır
    assert [ln for ln, _en, _tr in w.cx.hist] == [w.d._slots[talk_c["slot"]]["line"]]

    w.advance(2.0)
    w.frame(task())  # konuşma kayboldu, görev yazısı duruyor
    w.advance(1.0)  # gizleme süresi doldu ama konuşmanın okuma süresi (≈3,5 sn) sürüyor
    assert isinstance(w.out.covers[-1], list)
    w.advance(0.6)
    c = w.out.covers[-1]
    assert isinstance(c, dict) and c["slot"] == task_c["slot"] and c["line"]["text"] == f"asıl: {TASK}"
    assert w.box.last("cover")["slot"] == task_c["slot"]

    w.frame(task(), talk(TALK2))  # yeni konuşma: kendi kapağı, görev yazısınınki yerinde
    covers = w.out.covers[-1]
    assert [x["line"]["text"] for x in covers] == [f"asıl: {TASK}", f"asıl: {TALK2}"]
    assert covers[1]["slot"] == talk_c["slot"]
    assert w.main.contexts[TALK2] == [(TALK, f"asıl: {TALK}")]  # bağlamda yalnız konuşma

    w.frame(kind="gone")  # ikisi de kayboldu: kapaklar kendi sürelerinde kalkar
    w.advance(10.0)
    assert w.out.covers[-1] is None and w.box.last("cover") is None


def test_uctan_uca_ust_sinir_kapak_basina(wired):
    """Ekranda duran görev yazısının kapağı en uzun kalma süresinde kalkar; konuşmanınki kendi süresiyle gelir gider.
    Oyun görev yazısını kaldırmadıkça kapağı geri gelmez."""
    w = wired
    w.frame(task())
    slot = w.out.covers[-1]["slot"]
    for _ in range(14):
        w.advance(0.5)
        w.frame(task())
    w.advance(0.2)
    assert w.out.covers[-1] is None  # 7 sn üst sınır
    w.frame(task(), talk())
    c = w.out.covers[-1]
    assert isinstance(c, dict) and c["line"]["text"] == f"asıl: {TALK}"  # görev yazısınınki dönmedi
    w.frame(talk())
    w.advance(2.0)  # titreme sayılmayacak kadar (Tracker.REAPPEAR_S) sonra
    w.frame(task(), talk())  # görev yazısı kaldırılıp yeniden gösterildi (aynı yazı): çevirisi yine görünür
    assert slot in [x["slot"] for x in w.out.covers[-1]]


def test_iki_asamali_ceviri_yuva_basina(make_pipe):
    """Karma mod (bekleme 0): her yazının hızlı çevirisi hemen, asıl çevirisi gelince aynı satırı günceller."""
    from altyazitr.pipeline import TrContext

    pipe = make_pipe()
    pipe.s.translate.engine, pipe.s.translate.hybrid, pipe.s.translate.hybrid_wait_ms = "ollama", True, 0
    cx = TrContext(pipe.s.translate, FakeTr("asıl"), FakeTr("hızlı"))
    cx.set_ready()
    got: list[tuple] = []
    pipe.subtitle.connect(lambda ev: got.append((ev["engine"], ev["original"], ev.get("slot"))))
    pipe.gone.connect(lambda slot: got.append(("gone", slot)))
    for item in _read(pipe, pipe.clock(), task(), talk()):
        pipe._apply(pipe._translate_item(item, cx))
    a, b = (s for _e, _o, s in got)
    assert got == [("sahte hızlı", TALK, a), ("sahte hızlı", TASK, b)] and a != b
    pipe._apply(pipe._remote_item(pipe._remote_q.get(0), cx))
    pipe._apply(pipe._remote_item(pipe._remote_q.get(0), cx))
    assert got[2:] == [("sahte asıl", TALK, a), ("sahte asıl", TASK, b)]  # ekrandan çıkıyor sayılmadı


# --- sunucu ve OBS sayfası ------------------------------------------------------------------------------------------
def _cv(text, rect, slot=None):
    c = {"rect": list(rect), "rows": 1, "font": 0.024, "min": 0.018, "line": {"line": 1, "text": text,
                                                                               "speaker": None}}
    if slot is not None:
        c["slot"] = slot
    return c


def test_sunucu_kapak_listesi_ve_eski_bicim():
    server = OverlayServer(1)
    asyncio.run(server.set_cover([_cv("Konuş", [0.2, 0.85, 0.6, 0.05], 1), _cv("Görev", [0.4, 0.78, 0.2, 0.03], 2)]))
    m = server.state["cover"]
    assert [c["slot"] for c in m["covers"]] == [1, 2] and m["cover"] == m["covers"][-1]  # eski sayfa en yeniyi çizer
    asyncio.run(server.set_cover(_cv("Tek", [0.2, 0.85, 0.6, 0.05])))
    assert server.state["cover"] == {"type": "cover", "cover": {**_cv("Tek", [0.2, 0.85, 0.6, 0.05]),
                                                                "min": 0.018}}  # tek kapak: eski biçim
    asyncio.run(server.set_cover([]))
    assert server.state["cover"] == {"type": "cover", "cover": None, "covers": []}


_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
class El {
  constructor(tag, cls) {
    this.tagName = tag; this.className = cls || ''; this.kids = []; this.parent = null; this.text = null;
    this.style = { setProperty(k, v) { this[k] = v; } };
    const self = this;
    const set = () => new Set(self.className.split(/\s+/).filter(Boolean));
    this.classList = {
      add(...c) { const s = set(); c.forEach((x) => s.add(x)); self.className = [...s].join(' '); },
      remove(...c) { const s = set(); c.forEach((x) => s.delete(x)); self.className = [...s].join(' '); },
      contains(c) { return set().has(c); },
    };
  }
  add(el) { el.parent = this; this.kids.push(el); return el; }
  appendChild(el) { return this.add(el); }
  append(...ns) {
    for (const n of ns) this.add(typeof n === 'string' ? Object.assign(new El('#text'), { text: n }) : n);
  }
  get textContent() { return this.text !== null ? this.text : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.text = null; if (v !== '') this.append(String(v)); }
  querySelector(sel) {
    for (const k of this.kids) {
      if (k.classList.contains(sel.slice(1))) return k;
      const r = k.querySelector(sel);
      if (r) return r;
    }
    return null;
  }
  getBoundingClientRect() {
    if (!this.classList.contains('tx')) return { top: 0, height: 0 };
    const size = parseFloat(this.parent.style.fontSize), width = parseFloat(this.style.width);
    const perLine = Math.max(1, Math.floor(width / (size * 0.5)));
    return { top: 0, height: Math.ceil(this.textContent.length / perLine) * size * 1.28 };
  }
}
const body = new El('body');
const cover = body.add(new El('div', 'cover'));
const tx = cover.add(new El('div', 'tx'));
tx.add(new El('span', 'sp'));
tx.add(new El('span', 't'));
const els = { stack: new El('div'), mask: new El('div'), status: new El('div'), cover };
const sockets = [];
const ctx = {
  document: { getElementById: (id) => els[id], createElement: (t) => new El(t), documentElement: new El('html'), body },
  window: {}, navigator: {}, location: { protocol: 'http:', host: '127.0.0.1:1', search: '' }, URLSearchParams,
  innerWidth: 1920, innerHeight: 1080,
  matchMedia: () => ({ matches: true }),
  getComputedStyle: () => ({ getPropertyValue: (k) => (k === '--size' ? '4.074vh' : '0'), transform: 'none' }),
  requestAnimationFrame: (f) => f(), setTimeout: () => 0, Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  WebSocket: class { constructor() { sockets.push(this); } },
};
vm.createContext(ctx);
vm.runInContext(script, ctx);
const ws = sockets[0];
ws.onopen();
const out = [];
for (const m of JSON.parse(process.argv[3])) {
  ws.onmessage({ data: JSON.stringify(m) });
  out.push(body.kids.filter((k) => k.classList.contains('cover')).map((el) => ({
    first: el === cover, on: el.classList.contains('on'), text: el.querySelector('.tx').textContent,
    box: ['left', 'top', 'width', 'height'].map((k) => parseFloat(el.style[k])),
  })));
}
process.stdout.write(JSON.stringify(out));
"""


def test_obs_sayfasi_iki_kutu_ve_eski_ileti(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js yok: OBS sayfasının betiği çalıştırılamadı")
    talk_c = _cv("Buradan çıkmalıyız.", [0.2, 0.85, 0.6, 0.05], 1)  # 1152 × 54 piksel (yazı sığar: kutu büyümez)
    task_c = _cv("Arnold'u kovala.", [0.4, 0.75, 0.2, 0.04], 2)  # 384 × 43,2 piksel
    msgs = [
        {"type": "cover", "cover": task_c, "covers": [talk_c, task_c]},  # iki yazı
        {"type": "cover", "cover": task_c, "covers": [task_c]},  # konuşma kalktı
        {"type": "cover", "cover": _cv("Eski uygulama", [0.3, 0.8, 0.4, 0.05])},  # eski uygulamanın tek kapağı
        {"type": "cover", "cover": None},
    ]
    harness = tmp_path / "kapaklar.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(harness), str(resource_dir() / "web" / "overlay.html"), json.dumps(msgs)],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    two, one, old, none = json.loads(r.stdout)

    assert [(e["first"], e["on"], e["text"]) for e in two] == [(True, True, "Buradan çıkmalıyız."),
                                                              (False, True, "Arnold'u kovala.")]
    assert two[0]["box"] == pytest.approx([384, 918, 1152, 54])  # her kutu kendi yazısının yerinde
    assert two[1]["box"] == pytest.approx([768, 810, 384, 43.2])

    assert [(e["on"], e["text"]) for e in one] == [(False, "Buradan çıkmalıyız."), (True, "Arnold'u kovala.")]
    assert one[1]["box"] == two[1]["box"]  # kalan yazının kutusu yerinde

    assert [(e["on"], e["text"]) for e in old] == [(True, "Eski uygulama"), (False, "Arnold'u kovala.")]
    assert old[0]["box"] == pytest.approx([576, 864, 768, 54])  # eski ileti: tek kutu, eskisi gibi
    assert not any(e["on"] for e in none)


# --- ekrandaki kutu ---------------------------------------------------------------------------------------------------
@pytest.fixture
def overlay(qapp):
    from altyazitr.overlay import ScreenOverlay

    s = Settings()
    s.overlay.enabled = False  # pencere gösterilmez; çizim render() ile
    s.overlay.box = [50, 300, 600, 160]
    sc = s.screen_style
    sc.text_color, sc.mask_color, sc.shadow, sc.outline_width = "#FF0000", "#000000", False, 0.0
    ov = ScreenOverlay(s)
    ov._apply_win32 = lambda: None
    try:
        yield ov
    finally:
        ov._anim.stop()
        ov._shift.stop()
        ov._cover_fade.stop()
        ov.close()
        ov.deleteLater()


def _scover(text, rect, slot, font=26.0, low=19.5):
    return {"rect": list(rect), "bounds": [0, 0, 1920, 1080], "rows": 1, "font": font, "min": low, "slot": slot,
            "line": {"line": slot, "text": text, "speaker": None}}


def _render(ov) -> np.ndarray:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    img = QImage(ov.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    ov.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def test_ekrandaki_kutu_iki_kapak_tek_pencerede(overlay):
    from PySide6.QtCore import QRect

    talk_c = _scover("Buradan çıkmalıyız.", (400, 900, 700, 44), 1)
    task_c = _scover("Arnold'u kovala.", (600, 820, 300, 30), 2, font=18.0, low=16.0)
    overlay.set_cover([talk_c, task_c])
    a, b = (it["rect"] for it in overlay._cover["items"])
    g = overlay.geometry()
    assert g == a.united(b) and a.contains(QRect(400, 900, 700, 44)) and b.contains(QRect(600, 820, 300, 30))
    assert overlay._cover["line"]["text"] == "Arnold'u kovala."  # en yeni kapak
    overlay._anim.stop()
    overlay._opacity = 1.0
    px = _render(overlay)
    rel = lambda r: (r.y() - g.y() + r.height() // 2, r.x() - g.x() + r.width() // 2)  # noqa: E731
    assert px[rel(a)][3] == 255 and px[rel(b)][3] == 255  # iki kapak da opak
    gap_y = b.bottom() + 1 + (a.top() - b.bottom() - 1) // 2 - g.y()
    assert px[gap_y, a.left() - g.x() + 5][3] == 0  # aradaki alan saydam: oyun görünür
    assert overlay.s.overlay.box == [50, 300, 600, 160]  # kaydedilen yer değişmedi

    from altyazitr.overlay import WDA_EXCLUDEFROMCAPTURE

    overlay.s.overlay.visible_in_stream, overlay.s.capture.mode = True, "screen"
    assert overlay._affinity() == WDA_EXCLUDEFROMCAPTURE  # bütün kapaklar OCR'a girmez (tek pencere)

    overlay.set_cover(task_c)  # konuşma kalktı: kapağı solar, sonra pencere küçülür
    assert [it["key"] for it in overlay._cover["leaving"]] == [("s", 1)] and overlay._cover_mix == 0.0
    assert overlay.geometry() == g
    overlay._cover_fade.stop()
    overlay._cover_fade_done()
    assert overlay.geometry() == b and overlay._cover["leaving"] == []

    overlay.set_cover([task_c, _scover("Yeni konuşma.", (400, 900, 700, 44), 1)])  # yeni yazı belirir
    assert overlay._cover["entering"] == {("s", 1)} and overlay.geometry() == g
    overlay.set_cover([task_c, _scover("Sonraki satır.", (400, 900, 700, 44), 1)])  # aynı yazının yeni satırı
    assert overlay._cover["entering"] == set() and overlay._cover["leaving"] == []  # yerinde değişir

    overlay.set_cover(None)
    overlay._anim.stop()
    overlay._opacity = 0.0
    overlay._on_anim_done()
    assert overlay._cover is None and overlay.geometry() == QRect(50, 300, 600, 160)


def test_ortmesiz_gorunumun_akisina_yalniz_konusma_girer(wired):
    """Örtme yalnız Yayınımda açıkken Ekranımda'nın akış kutusu eskisi gibi yalnız konuşmayı gösterir: görev yazısı
    (karedeki asıl yazı olmayan öbek) akışa karışmaz; Yayınımda iki kapak yine aynı anda."""
    w = wired
    w.pipe.s.screen_style.cover_original = False  # Ekranımda örtme kapalı, Yayınımda açık (fixture)
    w.frame(task(), talk())
    assert isinstance(w.out.covers[-1], list) and len(w.out.covers[-1]) == 2  # OBS: iki kapak
    flow = w.box.last("lines")
    assert flow is not None and [x["text"] for x in flow] == [f"asıl: {TALK}"]  # görev yazısı yok
    assert [x["original"] for x in w.d.lines] == [TALK]


def test_yakin_duran_konusma_ve_gorev_yazisi_ayrilir():
    """Kullanıcının GTA VI görüntüsündeki ölçüler (RapidOCR): iki satırlık konuşma (47 ve 40 px, aralarında 6 px) ve
    29 px altında daha küçük görev yazısı (30 px). Eski kural (1,2 × ortanca satır boyu = 48 px) bunları tek öbek
    sayıyordu; örtme açıkken (tight) boşluk küçük satırın STACK_GAP katını geçtiği için ayrı yazıdır. Örtme kapalıyken
    tek öbek kalır (tek altyazı yolu değişmez)."""
    from altyazitr.textproc import STACK_GAP, subtitle_blocks

    lines = [_ln("Arnold: Just- just take the scraps that are left, just don't kill me,", 36, 76, 1030, 47),
             _ln("man.", 520, 129, 70, 40),
             _ln("Choose what to do with Arnold", 354, 198, 403, 30)]
    assert len(subtitle_blocks(lines)) == 1
    blocks = subtitle_blocks(lines, tight=True)
    assert [len(b) for b in blocks] == [2, 1]  # konuşmanın iki satırı birlikte, görev yazısı ayrı
    assert 6 < STACK_GAP * 40 < 29


def test_dusen_vurgu_isareti_ceviride_geri_konur():
    """Model kısa cümlede [[...]] işaretini düşürürse çeviride aynen geçen vurgulu isim ekiyle vurgulanır (kullanıcının
    GTA VI görüntüsü: "Choose what to do with Arnold", Arnold pembe). Çeviride geçmeyen vurgu ve zaten işaretli
    çıktı olduğu gibi kalır."""
    from altyazitr.pipeline import _translate_line
    from altyazitr.textproc import restore_marks, unmark

    src, spans = "Choose what to do with Arnold", [(23, 29)]
    out = restore_marks(src, spans, "Arnold'a ne yapacağına karar ver.")
    assert out == "[[Arnold'a]] ne yapacağına karar ver."
    assert unmark(out) == ("Arnold'a ne yapacağına karar ver.", [(0, 8)])
    assert restore_marks("Watch the cops", [(10, 14)], "Polislere dikkat et.") == "Polislere dikkat et."
    assert restore_marks(src, spans, "[[Arnold]] ile ne yapacağına karar ver.") == "[[Arnold]] ile ne yapacağına karar ver."
    assert restore_marks(src, [], "Arnold'a ne yapacağına karar ver.") == "Arnold'a ne yapacağına karar ver."

    class Dropper:  # işaretleri düşüren çevirmen
        def translate_marked(self, text, context=()):
            return "arnold'a ne yapacağına karar ver."

    got = _translate_line(Dropper(), {"text": src, "spans": spans})
    assert got == "[[Arnold'a]] ne yapacağına karar ver."  # cümle başı da büyük harfe döndü


def test_sonradan_beliren_vurgu_satiri_yeniden_cevirtir():
    """Görev yazısı solarak gelir: ilk okumada renk henüz yoksa satır vurgusuz okunur. Aynı satırın sonraki okumasında
    vurgu belirince yeni okuma sayılır (vurguyla yeniden çevrilir); aynı vurgu tekrar gelince 'aynı satır'."""
    from altyazitr.pipeline import Tracker

    tr = Tracker()
    first = tr.on_text("Choose what to do with Arnold", None, 0.0, 0.9)
    assert first is not None and not first["replace"]
    up = tr.on_text("Choose what to do with Arnold", None, 0.2, 0.9, frozenset({"arnold"}))
    assert up is not None and up["replace"] and up["line"] == first["line"]
    assert tr.on_text("Choose what to do with Arnold", None, 0.4, 0.9, frozenset({"arnold"})) is None
    assert tr.on_text("Choose what to do with Arnold", None, 0.6, 0.9) is None  # vurgu kısa süre kaçtı: aynı satır
