"""Ana işlem hattı: yakalama → değişim algılama → OCR → takip/tekilleştirme → çeviri.

Üç iş parçacığı vardır ve aralarında *tek yuvalı, en yenisi kazanır* kutular kullanılır;
böylece gecikme asla birikmez (yavaş bir çeviri sırasında gelen eski altyazılar atlanır).
Sonuçlar Qt sinyalleriyle arayüze iletilir.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from rapidfuzz import fuzz

from altyazitr import winutil
from altyazitr.capture import CaptureError, RegionCapturer, WindowCapturer
from altyazitr.config import Settings
from altyazitr.detect import ChangeGate, GateParams, text_mask
from altyazitr.english import english_score
from altyazitr.ocr import OcrUnavailable, create_engine
from altyazitr.textproc import assemble, is_valid, norm_key
from altyazitr.paths import DATA_DIR
from altyazitr.translate import (Glossary, QuickMtTranslator, TranslationCache, Translator,
                                 TranslatorError, create_translator, restore_end_punctuation)

log = logging.getLogger(__name__)

REMOTE_ENGINES = ("ollama", "gemini", "deepl")
CACHE_FILE = DATA_DIR / "ceviri_onbellegi.json"


class LatestSlot:
    """Tek yuvalı kutu: put() eski değerin üzerine yazar, get() bekler ve boşaltır."""

    def __init__(self):
        self._v = None
        self._cv = threading.Condition()

    def put(self, v) -> None:
        with self._cv:
            self._v = v
            self._cv.notify()

    def get(self, timeout: float = 0.5):
        with self._cv:
            if self._v is None:
                self._cv.wait(timeout)
            v, self._v = self._v, None
            return v

    def clear(self) -> None:
        with self._cv:
            self._v = None
            self._cv.notify_all()


class Tracker:
    """Aynı altyazının OCR titremelerini tekilleştirir, uzayan (daktilo) metni algılar ve
    aynı altyazının *daha iyi* bir okuması gelirse (kelime puanı yüksek) çeviriyi düzeltir."""

    UPGRADE_MARGIN = 0.12

    def __init__(self):
        self.key: str | None = None
        self.score = 0.0
        self.seq = 0
        self.gone_t: float | None = None

    def on_gone(self, t: float) -> None:
        if self.key is not None and self.gone_t is None:
            self.gone_t = t

    def on_text(self, text: str, speaker: str | None, t: float, score: float = 1.0) -> dict | None:
        k = norm_key(text)
        if not k:
            return None
        replace = False
        if self.key:
            recent = self.gone_t is None or t - self.gone_t < 1.5
            r = fuzz.ratio(k, self.key)
            thr = 90 if min(len(k), len(self.key)) < 20 else 88
            if r >= thr and recent:
                self.gone_t = None
                if k == self.key or score < self.score + self.UPGRADE_MARGIN:
                    return None  # aynı satır (OCR titremesi)
                replace = True  # aynı satırın daha temiz okuması
            elif len(k) >= len(self.key) + 4 and fuzz.partial_ratio(self.key, k) >= 95:
                replace = True  # metin uzadı (daktilo efekti / ikinci satır)
            elif r >= 70 and recent and score >= self.score + self.UPGRADE_MARGIN:
                replace = True  # bozuk okunmuş satırın düzgün hali
        self.key = k
        self.score = score
        self.gone_t = None
        self.seq += 1
        return {"seq": self.seq, "text": text, "speaker": speaker, "key": k, "replace": replace, "t": t}


def _to_qimage(bgr: np.ndarray, mask: np.ndarray | None, max_w: int = 720) -> QImage:
    img = bgr
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, max(1, h * max_w // w)), interpolation=cv2.INTER_AREA)
    if mask is not None and mask.any():
        m = cv2.resize(mask.astype(np.uint8), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        img = img.copy()
        img[m > 0] = (0.45 * img[m > 0] + 0.55 * np.array([80, 220, 120])).astype(np.uint8)
    img = np.ascontiguousarray(img)
    hh, ww = img.shape[:2]
    return QImage(img.data, ww, hh, img.strides[0], QImage.Format.Format_BGR888).copy()


class Pipeline(QObject):
    status = Signal(str)
    error = Signal(str)
    running_changed = Signal(bool)
    preview = Signal(QImage, float)
    subtitle = Signal(dict)
    same = Signal()
    gone = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._run = threading.Event()
        self._threads: list[threading.Thread] = []
        self._alive = 0
        self._alive_lock = threading.Lock()
        self._ocr_slot = LatestSlot()
        self._tr_slot = LatestSlot()
        self.tracker = Tracker()
        self.cache = TranslationCache()
        self.cache.load(CACHE_FILE)
        self.capturer: WindowCapturer | RegionCapturer | None = None
        self.preview_enabled = True

    # --- yönetim --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._run.is_set()

    def start(self) -> None:
        if self._run.is_set() or self._alive:
            return
        self._run.set()
        self._ocr_slot.clear()
        self._tr_slot.clear()
        self.tracker = Tracker()
        self._threads = [
            threading.Thread(target=self._wrap, args=(self._capture_loop,), name="yakalama", daemon=True),
            threading.Thread(target=self._wrap, args=(self._ocr_loop,), name="ocr", daemon=True),
            threading.Thread(target=self._wrap, args=(self._translate_loop,), name="ceviri", daemon=True),
        ]
        with self._alive_lock:
            self._alive = len(self._threads)
        for t in self._threads:
            t.start()
        self.running_changed.emit(True)
        log.info("İşlem hattı başlatıldı")

    def stop(self) -> None:
        self._run.clear()
        self._ocr_slot.clear()
        self._tr_slot.clear()

    def stop_and_wait(self, timeout: float = 4.0) -> None:
        self.stop()
        end = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, end - time.monotonic()))

    def _wrap(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            log.exception("İş parçacığı hatası")
            self.error.emit(f"Beklenmeyen hata: {e}")
            self._run.clear()
        finally:
            with self._alive_lock:
                self._alive -= 1
                last = self._alive == 0
            if last:
                self.capturer = None
                self.running_changed.emit(False)
                self.status.emit("Durduruldu")
                log.info("İşlem hattı durdu")

    def grab_full_frame(self, timeout: float = 2.0) -> np.ndarray | None:
        """Pencere modunda çalışan yakalamadan tam kare (bölge seçimi için)."""
        cap = self.capturer
        return cap.grab_full(timeout) if isinstance(cap, WindowCapturer) else None

    # --- yakalama + değişim algılama -----------------------------------
    def _open_capturer(self):
        c = self.s.capture
        if c.mode != "window":
            if not c.is_ready():
                self.status.emit("Önce çevrilecek bölgeyi seçin.")
                return None
            cap = RegionCapturer(c.rect)
            cap.start()
            self.status.emit("Seçilen ekran bölgesi okunuyor")
            return cap
        win = winutil.find_window(c.window_exe, c.window_title)
        if win is None:
            self.status.emit(f"Pencere bulunamadı ({c.window_title or c.window_exe}). Açılması bekleniyor…")
            return None
        cap = WindowCapturer(win.hwnd, tuple(c.region), c.fps)
        try:
            cap.start()
        except CaptureError as e:
            self.status.emit(str(e))
            return None
        except Exception as e:
            log.exception("Pencere yakalama başlatılamadı")
            self.status.emit(f"Pencere yakalanamadı: {e}")
            return None
        self.status.emit(f"Yakalanıyor: {win.title}")
        return cap

    def _capture_loop(self) -> None:
        c, o = self.s.capture, self.s.ocr
        gate = ChangeGate(GateParams(stable_frames=o.stable_frames, bright=o.bright_threshold))
        period = 1.0 / max(1, c.fps)
        last_find = -10.0
        last_preview = 0.0
        last_interval = 0.0
        empty_since: float | None = None
        warned_min = False
        cap = None
        try:
            while self._run.is_set():
                t0 = time.monotonic()
                if cap is None or not cap.alive:
                    if cap is not None:
                        cap.stop()
                        cap = self.capturer = None
                        gate.reset()
                        self._ocr_slot.put(("gone", t0, None))
                        self.status.emit("Pencere kapandı, yeniden aranıyor…")
                    if t0 - last_find >= 2.0:
                        last_find = t0
                        cap = self.capturer = self._open_capturer()
                    if cap is None:
                        time.sleep(0.4)
                        continue
                if isinstance(cap, RegionCapturer):
                    if list(cap.rect) != list(c.rect) and c.is_ready():
                        cap.set_rect(c.rect)
                        gate.reset()
                else:
                    cap.set_region(tuple(c.region))
                roi = cap.grab_roi()
                if roi is None or roi.size == 0:
                    if empty_since is None:
                        empty_since = t0
                    elif t0 - empty_since > 3 and isinstance(cap, WindowCapturer):
                        if winutil.is_minimized(cap.hwnd) and not warned_min:
                            warned_min = True
                            self.status.emit("Pencere simge durumunda — görüntü alınamıyor.")
                    time.sleep(period)
                    continue
                empty_since = None
                if warned_min and isinstance(cap, WindowCapturer) and not winutil.is_minimized(cap.hwnd):
                    warned_min = False
                    self.status.emit("Yakalama devam ediyor.")

                if o.detect_mode == "interval":
                    if t0 - last_interval >= o.interval_ms / 1000.0:
                        last_interval = t0
                        self._ocr_slot.put(("text", t0, roi))
                    mask = None
                    if self.preview_enabled and t0 - last_preview > 0.25:
                        mask = text_mask(roi, o.bright_threshold)
                        gate.ink = float(mask.mean())
                else:
                    gate.p.bright = o.bright_threshold
                    gate.p.stable_frames = o.stable_frames
                    action = gate.update(roi, t0)
                    if action == "ocr":
                        self._ocr_slot.put(("text", t0, roi))
                    elif action == "gone":
                        self._ocr_slot.put(("gone", t0, None))
                    mask = gate.prev

                if self.preview_enabled and t0 - last_preview > 0.25:
                    last_preview = t0
                    try:
                        self.preview.emit(_to_qimage(roi, mask), gate.ink)
                    except Exception:
                        log.debug("Önizleme üretilemedi", exc_info=True)

                dt = time.monotonic() - t0
                if dt < period:
                    time.sleep(period - dt)
        finally:
            if cap is not None:
                cap.stop()

    # --- OCR ------------------------------------------------------------
    def _ocr_loop(self) -> None:
        o = self.s.ocr
        try:
            engine = create_engine(o.engine, o.upscale, o.preprocess)
        except OcrUnavailable as e:
            self.error.emit(str(e))
            self._run.clear()
            return
        except Exception as e:
            log.exception("OCR motoru başlatılamadı")
            self.error.emit(f"OCR motoru başlatılamadı: {e}")
            self._run.clear()
            return
        log.info("OCR motoru: %s", engine.name)
        try:
            while self._run.is_set():
                item = self._ocr_slot.get(0.3)
                if item is None:
                    continue
                kind, t, img = item
                if kind == "gone":
                    self._emit_gone(t)
                    continue
                t1 = time.perf_counter()
                try:
                    lines = engine.recognize(img)
                except Exception:
                    log.exception("OCR hatası")
                    continue
                ms = (time.perf_counter() - t1) * 1000
                text, speaker, confs = assemble(lines, o.keep_lowest_block, o.detect_speaker)
                if not text.strip():
                    self._emit_gone(t)
                    continue
                score = english_score(text)
                if not is_valid(text, confs, o.min_letters, score):
                    continue
                ev = self.tracker.on_text(text, speaker, t, score)
                if ev is None:
                    self.same.emit()
                    continue
                ev["ocr_ms"] = ms
                self._tr_slot.put(ev)
        finally:
            engine.close()

    def _emit_gone(self, t: float) -> None:
        if self.tracker.key is not None and self.tracker.gone_t is None:
            self.tracker.on_gone(t)
            self.gone.emit()

    # --- çeviri ----------------------------------------------------------
    def _prepare_translators(self) -> tuple[Translator | None, Translator | None]:
        tc = self.s.translate
        glossary = Glossary(tc.glossary)
        fast: Translator | None = None
        # LLM/çevrimiçi motorlarda yerel çeviri her zaman yedek olarak hazır (zaman aşımı, kota, bağlantı kopması)
        if tc.engine in REMOTE_ENGINES:
            try:
                fast = QuickMtTranslator(glossary, tc.quickmt_device)
                fast.prepare(self.status.emit)
            except Exception as e:
                log.warning("Hızlı ön çeviri hazırlanamadı: %s", e)
                fast = None
        try:
            main = create_translator(tc, glossary)
            main.prepare(self.status.emit)
            return main, fast
        except TranslatorError as e:
            self.error.emit(str(e))
        except Exception as e:
            log.exception("Çeviri motoru hazırlanamadı")
            self.error.emit(f"Çeviri motoru hazırlanamadı: {e}")
        if tc.engine == "quickmt":
            return None, None
        self.status.emit("Yedek olarak yerel hızlı çeviri (quickmt) kullanılıyor.")
        if fast is not None:
            return fast, None
        try:
            fb = QuickMtTranslator(glossary, tc.quickmt_device)
            fb.prepare(self.status.emit)
            return fb, None
        except Exception as e:
            self.error.emit(f"Yedek çeviri de hazırlanamadı: {e}")
            return None, None

    def _translate_loop(self) -> None:
        tc = self.s.translate
        main, fast = self._prepare_translators()
        if main is None:
            self._run.clear()
            return
        model_tag = f"{main.key}:{getattr(main, 'model', '')}"
        hist: deque[tuple[str, str]] = deque(maxlen=max(0, tc.context_lines) or 1)
        last_t = 0.0
        self.status.emit("Hazır — altyazı bekleniyor")
        try:
            while self._run.is_set():
                ev = self._tr_slot.get(0.3)
                if ev is None:
                    continue
                if ev["t"] - last_t > 20:
                    hist.clear()
                ck = f"{model_tag}|{ev['key']}"
                t1 = time.perf_counter()
                out = self.cache.get(ck)
                cached = out is not None
                if out is None:
                    quick = None
                    if fast is not None and tc.hybrid:
                        quick = self._safe_fast(fast, ev["text"])
                        if quick and ev["seq"] == self.tracker.seq:
                            self._emit_sub(ev, quick, t1, cached=False, engine=fast.label)
                    ctx = list(hist) if tc.context_lines > 0 else []
                    try:
                        out = main.translate(ev["text"], ctx)
                        if not out:
                            raise TranslatorError("Boş çeviri döndü")
                        out = restore_end_punctuation(ev["text"], out)
                        self.cache.put(ck, out)
                    except Exception as e:
                        log.warning("Çeviri hatası (%s): %s", main.key, e)
                        if quick is None and fast is not None:
                            quick = self._safe_fast(fast, ev["text"])
                        if quick:
                            out = quick
                            self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})")
                        else:
                            self.status.emit(f"Çeviri hatası: {e}")
                            continue
                if ev["seq"] != self.tracker.seq:
                    continue  # bu sırada yeni altyazı geldi
                if ev["replace"] and hist:
                    hist.pop()
                if tc.context_lines > 0:
                    hist.append((ev["text"], out))
                last_t = ev["t"]
                self._emit_sub(ev, out, t1, cached=cached, engine=main.label)
        finally:
            for tr in (main, fast):
                if tr is not None:
                    try:
                        tr.close()
                    except Exception:
                        pass
            self.cache.save(CACHE_FILE)

    @staticmethod
    def _safe_fast(fast: Translator, text: str) -> str | None:
        try:
            out = fast.translate(text)
            return restore_end_punctuation(text, out) if out else None
        except Exception:
            log.debug("Hızlı çeviri hatası", exc_info=True)
            return None

    def _emit_sub(self, ev: dict, text: str, t1: float, cached: bool, engine: str) -> None:
        self.subtitle.emit({
            "text": text, "original": ev["text"], "speaker": ev["speaker"], "cached": cached, "engine": engine,
            "ocr_ms": ev.get("ocr_ms", 0.0), "tr_ms": (time.perf_counter() - t1) * 1000,
            "latency_ms": (time.monotonic() - ev["t"]) * 1000,
        })
