"""Hızlı OCR yolu: doğrudan onnxruntime ile PP-OCRv6 (PpOcr), uyarlamalı tek geçiş, erken okuma (spec/confirm),
yakalamanın kare olayı ve uzayan satırın takibi."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from altyazitr import ocr as ocr_mod
from altyazitr.config import Settings
from altyazitr.ocr import OcrLine
from altyazitr.pipeline import Pipeline, Tracker


def _subtitle(lines: list[str], w: int = 1213, h: int = 179, seed: int = 0) -> np.ndarray:
    """Koyu, dokulu zeminde kenarlı beyaz altyazı (bir ya da iki satır, ortalı, alta yakın)."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur((rng.random((h, w, 3)) * 110).astype(np.uint8), (0, 0), 3)
    y = h - 30 - 42 * (len(lines) - 1)
    for ln in lines:
        (tw, _th), _ = cv2.getTextSize(ln, cv2.FONT_HERSHEY_DUPLEX, 1.1, 2)
        org = ((w - tw) // 2, y)
        cv2.putText(img, ln, org, cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 7, cv2.LINE_AA)
        cv2.putText(img, ln, org, cv2.FONT_HERSHEY_DUPLEX, 1.1, (245, 245, 245), 2, cv2.LINE_AA)
        y += 42
    return img


# --- PpOcr: RapidOCR'ın kendi çağrısıyla aynı sonuç ---------------------------------------------------------------
@pytest.fixture(scope="module")
def engines():
    pytest.importorskip("rapidocr")
    return ocr_mod.PpOcr(2), ocr_mod._rapidocr(2)


@pytest.mark.parametrize("lines,size", [
    (["Get in the car, Jason!"], (1213, 179)),
    (["Hey bro, you good? That cop's been tailing us", "for like ten blocks."], (1213, 179)),
    (["We need to lose them before the bridge."], (1500, 120)),  # çok yassı: dikey dolgu
])
def test_ppocr_matches_rapidocr(engines, monkeypatch, lines, size):
    """Aynı modeller, aynı ön ve son işleme: RapidOCR'ın tanıma partileriyle metin, güven, satır ve kelime kutuları
    onunkiyle aynı (bkz. test_rec_batches: partiler yalnız çok farklı genişlikte satırlarda ayrılır)."""
    monkeypatch.setattr(ocr_mod, "REC_SPLIT", float("inf"))
    pp, rapid = engines
    img = _subtitle(lines, *size)
    mine = pp.ocr(img)
    ref = rapid(img, use_cls=False, return_word_box=True)
    assert [t for _b, t, _s, _w in mine] == list(ref.txts)
    assert [round(s, 3) for _b, _t, s, _w in mine] == [round(float(s), 3) for s in ref.scores]
    np.testing.assert_allclose(np.array([b for b, _t, _s, _w in mine]), np.asarray(ref.boxes, np.float32), atol=1)
    for (_b, _t, _s, words), ref_words in zip(mine, ref.word_results):
        assert [w[0] for w in words] == [w[0] for w in ref_words]
        np.testing.assert_allclose(np.array([w[2] for w in words], np.float32),
                                   np.array([w[2] for w in ref_words], np.float32), atol=1)


def test_rec_batches():
    """Tanıma partileri: oranına göre sıralı, en çok 6 satır; kısa kırpıntılar (tabela, HUD) altyazı satırının
    genişliğine doldurulmasın diye oranı partinin ilkinin 2 katını geçen satır yeni partiye geçer."""
    assert ocr_mod._rec_batches([18.0]) == [[0]]
    assert ocr_mod._rec_batches([18.0, 2.5, 3.0, 1.2]) == [[3, 1, 2], [0]]  # tabela kelimeleri ve altyazı ayrı
    assert ocr_mod._rec_batches([16.0, 12.0]) == [[1, 0]]  # iki satırlık altyazı birlikte
    assert ocr_mod._rec_batches([1.0] * 8) == [[0, 1, 2, 3, 4, 5], [6, 7]]


def test_ppocr_sessions_limited_threads_no_spinning(engines):
    """İş parçacığı sayısı sınırlı, çağrı aralarında dönme (spinning) kapalı: kareler arasında çekirdek yakmaz."""
    pp, _ = engines
    for sess in (pp._det, pp._rec):
        so = sess.get_session_options()
        assert so.intra_op_num_threads == 2 and so.inter_op_num_threads == 1
        assert so.get_session_config_entry("session.intra_op.allow_spinning") == "0"


@pytest.mark.parametrize("cpus,threads", [(1, 1), (4, 1), (8, 2), (12, 3), (16, 4), (24, 6), (64, 6), (None, 1)])
def test_ort_threads_by_cpu_count(monkeypatch, cpus, threads):
    monkeypatch.setattr(ocr_mod.os, "cpu_count", lambda: cpus)
    assert ocr_mod.ort_threads() == threads


def test_engine_reads_one_line_fast(engines):
    """Tek satırlık okuma (bulma + tanıma, kelime kutularıyla) bu boyutta onlarca ms sürer, saniye değil."""
    eng = ocr_mod.RapidOcrEngine("1", "auto", threads=2)
    img = _subtitle(["They're watching us. We need to move, now!"])
    eng.recognize(img)
    t = time.perf_counter()
    lines = eng.recognize(img)
    assert time.perf_counter() - t < 0.5
    assert [ln.text for ln in lines] == ["They're watching us. We need to move, now!"] and lines[0].words


# --- uyarlamalı ön işleme --------------------------------------------------------------------------------------
def _line(text: str, conf: float = 0.98, y: float = 100) -> OcrLine:
    return OcrLine(text, conf, 100, y, 600, 30)


class _Passes(ocr_mod.RapidOcrEngine):
    """Modelsiz: her ön işleme kipinin okuması verilir, hangi kiplerin okunduğu kaydedilir."""

    def __init__(self, reads: dict[str, list[OcrLine]]):
        self.mode, self.reads, self.calls = "auto", reads, []

    def _recognize(self, bgr, mode):
        self.calls.append(mode)
        return self.reads[mode]


def test_auto_skips_second_pass_when_raw_reading_is_strong():
    eng = _Passes({"none": [_line("Get in the car, Jason!")], "outline_bright": [_line("Get in the car Jason")]})
    assert [ln.text for ln in eng.recognize(None)] == ["Get in the car, Jason!"]
    assert eng.calls == ["none"]


@pytest.mark.parametrize("raw,masked,picked", [
    pytest.param([_line("Get in the car, Jason!", 0.7)], [_line("Get in the car Jason", 0.95)], "raw",
                 id="zayıf ama ikincisi belirgin üstün değil: ham kalır (noktalama)"),
    pytest.param([_line("PAWN TACOS Get in the", 0.8)], [_line("Get in the car now, we have to leave", 0.95)],
                 "masked", id="ikincisi belirgin daha çok kelime: o seçilir"),
    pytest.param([], [_line("We need to move, now!")], "masked", id="ham okuma boş"),
    pytest.param([_line("x7#q zz", 0.6)], [], "raw", id="ikisi de geçersiz"),
])
def test_auto_second_pass_when_weak(monkeypatch, raw, masked, picked):
    monkeypatch.setattr(ocr_mod, "text_mask", lambda img: np.ones((2, 2), bool))  # kenar maskesi yazı görüyor
    eng = _Passes({"none": raw, "outline_bright": masked})
    out = eng.recognize(None)
    assert eng.calls == ["none", "outline_bright"]
    assert out is (raw if picked == "raw" else masked)


def test_auto_skips_second_pass_on_empty_frame():
    """Ham okuma boş ve hızlı kenar maskesi de yazı görmüyor (yazısız kare): ikinci geçiş yapılmaz."""
    eng = _Passes({"none": [], "outline_bright": [_line("Go, go, go!")]})
    assert eng.recognize(np.full((120, 900, 3), 40, np.uint8)) == [] and eng.calls == ["none"]
    img = _subtitle(["Go, go, go!"], 900, 120)  # kenarlı yazı var ama ham okuma kaçırdı: ikinci geçiş denenir
    assert [ln.text for ln in eng.recognize(img)] == ["Go, go, go!"] and eng.calls[-1] == "outline_bright"


# --- erken okuma: spec / confirm ---------------------------------------------------------------------------------
class _CountingOcr:
    def __init__(self):
        self.calls = 0

    def recognize(self, img):
        self.calls += 1
        return [_line(img)]


@pytest.fixture
def pipe():
    s = Settings()
    s.style.highlight = s.screen_style.highlight = False
    return Pipeline(s)


def test_confirmed_early_read_is_used_without_reading_again(pipe):
    eng = _CountingOcr()
    assert pipe._ocr_item(eng, ("spec", 1.0, ("Get in the car, Jason!", 7))) == []  # okunur ama gönderilmez
    [(kind, t, ev)] = pipe._ocr_item(eng, ("confirm", 1.05, ("Get in the car, Jason!", 7)))
    assert kind == "sub" and ev["text"] == "Get in the car, Jason!" and t == 1.0  # erken okunan karenin anı
    assert eng.calls == 1


def test_unconfirmed_early_read_is_dropped(pipe):
    """Yazı erken okumadan sonra değiştiyse (beliriyordu, uzuyordu) o okuma hiç gönderilmez; doğrulanan kare okunur."""
    eng = _CountingOcr()
    pipe._ocr_item(eng, ("spec", 1.0, ("Get in the c", 3)))
    [(_, _, ev)] = pipe._ocr_item(eng, ("confirm", 1.3, ("Get in the car, Jason!", 4)))  # başka erken okumanın kimliği
    assert ev["text"] == "Get in the car, Jason!" and eng.calls == 2
    pipe._ocr_item(eng, ("spec", 2.0, ("Where are we going?", 5)))
    pipe._ocr_item(eng, ("gone", 2.1, None))  # yazı kayboldu: tutulan okuma atılır
    [(_, _, ev)] = pipe._ocr_item(eng, ("confirm", 2.5, ("Who are those guys?", 5)))
    assert ev["text"] == "Who are those guys?"


def test_gate_actions_to_ocr_queue(pipe):
    """Erken okuma yalnız OCR'ın önünde iş yokken istenir; okuma istekleri sırayla, hiçbiri atlanmadan kuyruğa girer."""
    from altyazitr.detect import GateAction

    roi = np.zeros((10, 10, 3), np.uint8)
    assert not pipe._on_gate(GateAction("spec", 1), 1.0, roi)
    assert pipe._on_gate(GateAction("ocr", 1), 1.05, roi)
    assert not pipe._on_gate(GateAction("spec", 2), 1.5, roi)  # önünde okuma var: erken okuma istenmez
    assert pipe._on_gate(GateAction("ocr", None, "max_wait"), 1.6, roi)
    assert pipe._on_gate("ocr", 1.7, roi) and not pipe._on_gate("gone", 1.8, None)  # kurallı geçidin dizeleri
    kinds = []
    while (item := pipe._ocr_q.get(0)) is not None:
        kinds.append((item[0], item[2][1] if item[0] in ("spec", "confirm") else None))
    assert kinds == [("spec", 1), ("confirm", 1), ("text", None), ("text", None), ("gone", None)]


# --- uzayan satır ---------------------------------------------------------------------------------------------
def test_tracker_long_line_extension_is_not_swallowed():
    """Uzun satırın sonuna birkaç harf eklendi (daktilo efektinin son harfleri): titreme sayılıp yutulmaz, aynı
    satırın güncellemesi olarak gelir."""
    tr = Tracker()
    a = tr.on_text("Wait... Is that him? No way. He said he lef", None, 0.0)
    b = tr.on_text("Wait... Is that him? No way. He said he left town!", None, 0.4)
    assert b is not None and b["replace"] and b["line"] == a["line"]
    assert tr.on_text("Wait... Is that him? No way. He said he left town!", None, 0.8) is None


# --- yakalama: kare geldiği anda ------------------------------------------------------------------------------
def test_window_capturer_signals_new_frames(monkeypatch):
    from altyazitr import capture

    monkeypatch.setattr(capture.winutil, "window_bounds", lambda hwnd: None)
    monkeypatch.setattr(capture.winutil, "client_rect_screen", lambda hwnd: None)
    cap = capture.WindowCapturer(0, (0.0, 0.5, 1.0, 0.5), fps=20)
    assert not cap.wait_frame(0.01)
    cap._on_frame(np.zeros((100, 200, 4), np.uint8))
    assert cap.wait_frame(0.01)
    assert cap.grab_roi().shape == (50, 200, 3)
    assert not cap.wait_frame(0.01)  # aynı kare yeniden işlenmez
    assert capture.frame_interval_ms(20) == 50 and capture.frame_interval_ms(100) == 16


def test_capture_loop_reads_new_subtitle_within_frames(pipe):
    """Gerçek yakalama döngüsü, sahte pencere ve sahte metin bulucuyla: yazının belirdiği kare erken okunur, bir
    sonraki karede doğrulanır; kare gelmezse aynı kare yeniden işlenmez."""
    from altyazitr.detect import TextGate, TextGateParams

    frames = [_subtitle([]), _subtitle([], seed=1), _subtitle(["Get in the car, Jason!"], seed=2),
              _subtitle(["Get in the car, Jason!"], seed=3)]
    text_box = [np.array([[400, 120], [820, 120], [820, 160], [400, 160]], np.float32) * (640 / 1213)]

    class _Cap:
        alive, i = True, -1

        def wait_frame(self, timeout):
            if self.i + 1 >= len(frames):
                pipe._run.clear()
                return False
            return True

        def grab_roi(self):
            self.i = min(self.i + 1, len(frames) - 1)
            return frames[self.i]

        def set_region(self, region):
            pass

        def stop(self):
            pass

    cap = _Cap()

    class _Det:
        def boxes(self, small):
            return text_box if cap.i >= 2 else []

    pipe._open_capturer = lambda: cap
    pipe._make_gate = lambda: TextGate(_Det(), TextGateParams(det_budget=1.0))
    pipe.preview_enabled = False
    pipe.s.capture.fps = 1000
    pipe._run.set()
    pipe._capture_loop()
    items = []
    while (item := pipe._ocr_q.get(0)) is not None:
        items.append(item[0])
    assert items == ["spec", "confirm"]
