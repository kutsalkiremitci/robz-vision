"""Ana işlem hattı: yakalama → değişim algılama → OCR → takip/tekilleştirme → çeviri.

İş parçacıkları arasında *sırayı koruyan* kısa kuyruklar (FifoQueue) vardır: karakterler hızlı
konuştuğunda OCR ya da çeviri meşgulken gelen satır bir öncekinin üzerine yazılmaz, her satır sırayla
okunur ve çevrilir. 'Kayboldu' ve 'aynı' işaretleri de metinlerle aynı sırada ilerler.

Her ayrı altyazı satırına artan bir kimlik verilir (olaydaki `line`). Aynı satırın güncellemeleri
(daha temiz okuma, uzayan daktilo metni, hızlı çevirinin yerine gelen LLM çevirisi) aynı kimlikle
gelir; gerçekten yeni bir altyazı bir sonraki kimliği alır.

Karma modda (hybrid) çeviri iki aşamalıdır: hızlı yerel çeviri yeni satırı hemen gösterir, yavaş uzak
motor (Ollama/Gemini/DeepL) kendi iş parçacığında sırayla çalışır ve yanıtı aynı satırı günceller.
Böylece yavaş motor yeni satırları asla bekletmez. Sonuçlar Qt sinyalleriyle arayüze iletilir.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from rapidfuzz import fuzz

from altyazitr import winutil
from altyazitr.capture import CaptureError, RegionCapturer, WindowCapturer
from altyazitr.config import Settings
from altyazitr.detect import ChangeGate, GateParams, TextDetector, text_mask
from altyazitr.english import english_score
from altyazitr.ocr import OcrUnavailable, create_engine
from altyazitr.textproc import assemble, is_valid, norm_key
from altyazitr.paths import DATA_DIR
from altyazitr.translate import (Glossary, QuickMtTranslator, TranslationCache, Translator,
                                 TranslatorError, create_translator, restore_end_punctuation)

log = logging.getLogger(__name__)

REMOTE_ENGINES = ("ollama", "gemini", "deepl")
CACHE_FILE = DATA_DIR / "ceviri_onbellegi.json"
OCR_QUEUE_MAX = 8  # yakalama → OCR (kareler; akıllı algılamada yalnız değişince gelir)
TR_QUEUE_MAX = 16  # OCR → çeviri ve çeviri → uzak motor (küçük sözlükler + işaretler)


class FifoQueue:
    """Sırayı koruyan sınırlı kuyruk: put() sona ekler, get() bekler ve baştan alır.

    Dolarsa en eski öğe atılır ve uyarı yazılır; pratikte olmamalı (yakalama yalnız değişimde öğe
    koyar). `merge` verilirse ve sondaki öğe için doğruysa yeni öğe onun yerine geçer (art arda iki
    'aynı' işareti ya da sabit aralık kipinde henüz okunmamış eski kare gibi)."""

    def __init__(self, maxlen: int = 8, name: str = "kuyruk"):
        self.maxlen = maxlen
        self.name = name
        self.dropped = 0
        self._q: deque = deque()
        self._cv = threading.Condition()

    def put(self, v, merge: Callable[[object], bool] | None = None) -> None:
        with self._cv:
            if merge is not None and self._q and merge(self._q[-1]):
                self._q[-1] = v
            else:
                if len(self._q) >= self.maxlen:
                    self._q.popleft()
                    self.dropped += 1
                    log.warning("%s kuyruğu doldu (%d öğe); en eski öğe atıldı", self.name, self.maxlen)
                self._q.append(v)
            self._cv.notify()

    def get(self, timeout: float = 0.5):
        with self._cv:
            if not self._q:
                self._cv.wait(timeout)
            return self._q.popleft() if self._q else None

    def peek(self):
        """Sıradaki öğe (çıkarmadan) ya da None."""
        with self._cv:
            return self._q[0] if self._q else None

    def has(self, pred: Callable[[object], bool]) -> bool:
        with self._cv:
            return any(pred(v) for v in self._q)

    def __len__(self) -> int:
        with self._cv:
            return len(self._q)

    def clear(self) -> None:
        with self._cv:
            self._q.clear()
            self._cv.notify_all()


def _is_frame(item) -> bool:
    return item[0] == "text"


def _is_same(item) -> bool:
    return item[0] == "same"


class LatestSlot:
    """Tek yuvalı kutu: put() eski değerin üzerine yazar, get() bekler ve boşaltır.
    Artık işlem hattında kullanılmıyor (hızlı konuşmada satır kaybettiriyordu); eski davranışla
    karşılaştıran testler için duruyor."""

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
    aynı altyazının *daha iyi* bir okuması gelirse (kelime puanı yüksek) çeviriyi düzeltir.

    Her ayrı satıra artan bir kimlik verir (`line`): aynı satırın daha iyi okuması ya da uzaması
    kimliği korur (`replace` True), gerçekten yeni bir altyazı bir sonraki kimliği alır. Kimlikler
    `line_base`ten devam eder; işlem hattı yeniden başlatılınca da hep artar."""

    UPGRADE_MARGIN = 0.12
    REAPPEAR_S = 1.5  # bundan kısa süre kaybolup dönen satır aynı satır sayılır

    def __init__(self, line_base: int = 0):
        self.key: str | None = None
        self.score = 0.0
        self.seq = 0
        self.line = line_base
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
            recent = self.gone_t is None or t - self.gone_t < self.REAPPEAR_S
            r = fuzz.ratio(k, self.key)
            thr = 90 if min(len(k), len(self.key)) < 20 else 88
            if r >= thr and recent:
                self.gone_t = None
                if k == self.key or score < self.score + self.UPGRADE_MARGIN:
                    return None  # aynı satır (OCR titremesi)
                replace = True  # aynı satırın daha temiz okuması
            elif recent and len(k) >= len(self.key) + 4 and fuzz.partial_ratio(self.key, k) >= 95:
                replace = True  # metin uzadı (daktilo efekti / ikinci satır)
            elif r >= 70 and recent and score >= self.score + self.UPGRADE_MARGIN:
                replace = True  # bozuk okunmuş satırın düzgün hali
        if not replace:
            self.line += 1  # yeni satır
        self.key = k
        self.score = score
        self.gone_t = None
        self.seq += 1
        return {"seq": self.seq, "line": self.line, "text": text, "speaker": speaker, "key": k,
                "replace": replace, "t": t}


class TrContext:
    """Bir çalıştırmanın çeviri durumu: motorlar, LLM'e giden son satırlar, iki aşamalı mı."""

    def __init__(self, tc, main: Translator, fast: Translator | None):
        self.tc = tc
        self.main = main
        self.fast = fast
        # karma mod: hızlı çeviri satırı hemen gösterir, uzak motor ayrı iş parçacığında aynı satırı günceller
        self.two_stage = fast is not None and bool(tc.hybrid)
        self.model_tag = f"{main.key}:{getattr(main, 'model', '')}"
        self.hist: deque[tuple[int, str, str]] = deque(maxlen=max(0, tc.context_lines) or 1)  # (line, en, tr)
        self.last_t = 0.0
        self.stop = threading.Event()

    def cache_key(self, ev: dict) -> str:
        return f"{self.model_tag}|{ev['key']}"


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
        self._ocr_q = FifoQueue(OCR_QUEUE_MAX, "OCR")
        self._tr_q = FifoQueue(TR_QUEUE_MAX, "Çeviri")
        self._remote_q = FifoQueue(TR_QUEUE_MAX, "Uzak çeviri")
        # yalnız çeviri iş parçacığı yazar, uzak çeviri iş parçacığı okur:
        self._shown_line = 0  # hızlı aşamadan geçen en yeni satır kimliği
        self._line_seq: dict[int, int] = {}  # satır kimliği → hızlı aşamadan geçen en yeni okumasının seq'i
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
        self._clear_queues()
        self._line_seq.clear()
        self.tracker = Tracker(self.tracker.line)  # satır kimlikleri yeniden başlatmada da artmaya devam eder
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
        self._clear_queues()

    def _clear_queues(self) -> None:
        for q in (self._ocr_q, self._tr_q, self._remote_q):
            q.clear()

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
        detector = None
        if o.detect_mode != "interval" and o.text_detector:
            try:  # açık zeminde beyaz/kenarsız altyazıları da bulan yapay zekâ katmanı
                detector = TextDetector()
            except Exception:
                log.warning("Yapay zekâ metin bulucu açılamadı; yalnız hızlı algılama kullanılacak", exc_info=True)
        gate = ChangeGate(GateParams(stable_frames=o.stable_frames, bright=o.bright_threshold), detector)
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
                        self._ocr_q.put(("gone", t0, None))
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
                        # sabit aralık örneklemedir: OCR yetişemezse bekleyen eski kare en yenisiyle değişir
                        self._ocr_q.put(("text", t0, roi), merge=_is_frame)
                    mask = None
                    if self.preview_enabled and t0 - last_preview > 0.25:
                        mask = text_mask(roi, o.bright_threshold)
                        gate.ink = float(mask.mean())
                else:
                    gate.p.bright = o.bright_threshold
                    gate.p.stable_frames = o.stable_frames
                    action = gate.update(roi, t0)
                    if action == "ocr":  # değişimle gelen her kare sırayla okunur, hiçbiri atlanmaz
                        self._ocr_q.put(("text", t0, roi))
                    elif action == "gone":
                        self._ocr_q.put(("gone", t0, None))
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
                item = self._ocr_q.get(0.3)
                if item is None:
                    continue
                for out in self._ocr_item(engine, item):
                    self._post_tr(out)
        finally:
            engine.close()

    def _ocr_item(self, engine, item: tuple) -> list[tuple]:
        """Kuyruktan gelen tek öğeyi işler; çeviri kuyruğuna sırayla gidecek öğeleri döndürür:
        ("sub", t, olay) yeni/güncellenen satır, ("gone", t, None) altyazı kayboldu, ("same", t, None)
        aynı satır hâlâ ekranda. İşaretler de kuyruktan geçer ki arayüze metinlerle aynı sırada ulaşsın."""
        o = self.s.ocr
        kind, t, img = item
        if kind == "gone":
            return self._gone_marker(t)
        t1 = time.perf_counter()
        try:
            lines = engine.recognize(img)
        except Exception:
            log.exception("OCR hatası")
            return []
        ms = (time.perf_counter() - t1) * 1000
        text, speaker, confs = assemble(lines, o.keep_lowest_block, o.detect_speaker)
        if not text.strip():
            return self._gone_marker(t)
        score = english_score(text)
        if not is_valid(text, confs, o.min_letters, score):
            return []
        ev = self.tracker.on_text(text, speaker, t, score)
        if ev is None:
            return [("same", t, None)]
        ev["ocr_ms"] = ms
        return [("sub", t, ev)]

    def _gone_marker(self, t: float) -> list[tuple]:
        if self.tracker.key is not None and self.tracker.gone_t is None:
            self.tracker.on_gone(t)
            return [("gone", t, None)]
        return []

    def _post_tr(self, item: tuple) -> None:
        # art arda 'aynı' işaretleri tek işarete iner (sabit aralık kipinde kuyruğu doldurmasın)
        self._tr_q.put(item, merge=_is_same if item[0] == "same" else None)

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
        main, fast = self._prepare_translators()
        if main is None:
            self._run.clear()
            return
        cx = TrContext(self.s.translate, main, fast)
        remote = None
        if cx.two_stage:  # yavaş uzak motor kendi iş parçacığında: yeni satırların hızlı çevirisini bekletmez
            remote = threading.Thread(target=self._remote_loop, args=(cx,), name="ceviri-uzak", daemon=True)
            remote.start()
        self.status.emit("Hazır — altyazı bekleniyor")
        try:
            while self._run.is_set():
                item = self._tr_q.get(0.3)
                if item is None:
                    continue
                self._apply(self._translate_item(item, cx))
        finally:
            cx.stop.set()
            if remote is not None:
                remote.join(cx.tc.timeout_s + 2.0)  # uzak motor bir çevirinin ortasındaysa bitirsin
            for tr in (main, fast):
                if tr is not None:
                    try:
                        tr.close()
                    except Exception:
                        pass
            self.cache.save(CACHE_FILE)

    def _remote_loop(self, cx: TrContext) -> None:
        try:
            while self._run.is_set() and not cx.stop.is_set():
                job = self._remote_q.get(0.3)
                if job is None:
                    continue
                self._apply(self._remote_item(job, cx))
        except Exception as e:
            log.exception("Uzak çeviri iş parçacığı hatası")
            self.error.emit(f"Beklenmeyen hata: {e}")
            self._run.clear()

    def _apply(self, acts: list[tuple[str, object]]) -> None:
        """Çeviri adımlarının sonuçlarını sırayla uygular (sinyaller / uzak motor kuyruğu)."""
        for kind, val in acts:
            if kind == "subtitle":
                self.subtitle.emit(val)
            elif kind == "upgrade":  # uzak motorun yanıtı (seq, olay): yayın anında denetlenir
                seq, sub = val
                if self._line_seq.get(sub["line"], seq) <= seq:
                    self.subtitle.emit(sub)
                # değilse çeviri sürerken aynı satırın daha yeni okuması gösterildi; eski okumanın çevirisi
                # onu ezmesin (yeni okumanın kendi çevirisi sırada)
            elif kind == "gone":
                self.gone.emit()
            elif kind == "same":
                self.same.emit()
            elif kind == "remote":
                self._remote_q.put(val)

    def _translate_item(self, item: tuple, cx: TrContext) -> list[tuple[str, object]]:
        """Çeviri kuyruğundan gelen tek öğe → uygulanacak sonuçlar. Karma modda yalnız hızlı çeviri
        yapılır ve satır uzak motorun kuyruğuna bırakılır; diğer modlarda asıl motor burada çalışır."""
        kind, _t, ev = item
        if kind != "sub":
            return [(kind, None)]  # 'gone' / 'same': metinlerle aynı sırada arayüze
        line = ev["line"]
        nxt = self._tr_q.peek()
        if nxt is not None and nxt[0] == "sub" and nxt[2]["line"] == line:
            return []  # aynı satırın daha yeni okuması sırada (daktilo / daha temiz okuma): onu çeviririz
        self._shown_line = max(self._shown_line, line)
        self._line_seq[line] = ev["seq"]
        if len(self._line_seq) > 64:
            for k in [k for k in self._line_seq if k < line - 32]:
                del self._line_seq[k]
        ck = cx.cache_key(ev)
        t1 = time.perf_counter()
        out = self.cache.get(ck)
        if out is not None:
            acts: list[tuple[str, object]] = [("subtitle", self._event(ev, out, t1, True, cx.main.label))]
            if cx.two_stage:  # LLM bağlamı sırayı korusun diye uzak iş parçacığından geçer
                acts.append(("remote", {"ev": ev, "quick": None, "done": out}))
            else:
                self._remember(cx, ev, out)
            return acts
        if cx.two_stage:
            quick = self._safe_fast(cx.fast, ev["text"])
            acts = [("subtitle", self._event(ev, quick, t1, False, cx.fast.label))] if quick else []
            acts.append(("remote", {"ev": ev, "quick": quick, "done": None}))
            return acts
        try:
            out = self._main_translate(cx, ev, ck)
            engine = cx.main.label
        except Exception as e:
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            out = self._safe_fast(cx.fast, ev["text"]) if cx.fast is not None else None
            if not out:
                self.status.emit(f"Çeviri hatası: {e}")
                return []
            engine = cx.fast.label
            self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})")
        self._remember(cx, ev, out)
        return [("subtitle", self._event(ev, out, t1, False, engine))]

    def _remote_item(self, job: dict, cx: TrContext) -> list[tuple[str, object]]:
        """Karma modun ikinci aşaması: uzak motorun çevirisi, hızlı çevirisi gösterilmiş satırı aynı
        kimlikle günceller. Geç kalan yanıt da yalnız kendi satırını günceller, yenisini ezmez."""
        ev, quick, done = job["ev"], job["quick"], job["done"]
        line = ev["line"]
        if done is not None:  # önbellekten gösterildi; yalnız LLM bağlamına eklenir
            self._remember(cx, ev, done)
            return []
        if self._remote_q.has(lambda j: j["ev"]["line"] == line):
            return []  # aynı satırın daha yeni hali sırada; onu çeviririz
        if quick and line <= self._shown_line - self._lines_on_screen():
            self._remember(cx, ev, quick)
            return []  # satır ekrandan çıktı (yerine yenileri geldi): güncellemenin gösterileceği yer yok
        t1 = time.perf_counter()
        try:
            out = self._main_translate(cx, ev, cx.cache_key(ev))
        except Exception as e:
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            if quick:
                self._remember(cx, ev, quick)
                self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})")
            else:
                self.status.emit(f"Çeviri hatası: {e}")
            return []
        self._remember(cx, ev, out)
        return [("upgrade", (ev["seq"], self._event(ev, out, t1, False, cx.main.label)))]

    def _lines_on_screen(self) -> int:
        """Ekranda aynı anda kalabilen satır sayısı: akışta ayarlanan kadar, tek satır kipinde 1."""
        fl = getattr(self.s, "flow", None)
        if fl is None or fl.mode != "flow":
            return 1
        return max(1, int(fl.max_lines))

    def _main_translate(self, cx: TrContext, ev: dict, ck: str) -> str:
        """Asıl motorla çevirir (LLM'e son satırlar bağlam olarak gider) ve önbelleğe yazar; hata fırlatabilir."""
        if ev["t"] - cx.last_t > 20:
            cx.hist.clear()
        ctx = [(en, tr) for ln, en, tr in cx.hist if ln != ev["line"]] if cx.tc.context_lines > 0 else []
        out = cx.main.translate(ev["text"], ctx)
        if not out:
            raise TranslatorError("Boş çeviri döndü")
        out = restore_end_punctuation(ev["text"], out)
        self.cache.put(ck, out)
        return out

    @staticmethod
    def _remember(cx: TrContext, ev: dict, out: str) -> None:
        """LLM bağlamına ekler; aynı satırın eski hali (daktilo / bozuk okuma) bağlamdan çıkar."""
        cx.last_t = ev["t"]
        if cx.tc.context_lines <= 0:
            return
        kept = [h for h in cx.hist if h[0] != ev["line"]]
        cx.hist.clear()
        cx.hist.extend(kept)
        cx.hist.append((ev["line"], ev["text"], out))

    @staticmethod
    def _safe_fast(fast: Translator, text: str) -> str | None:
        try:
            out = fast.translate(text)
            return restore_end_punctuation(text, out) if out else None
        except Exception:
            log.debug("Hızlı çeviri hatası", exc_info=True)
            return None

    @staticmethod
    def _event(ev: dict, text: str, t1: float, cached: bool, engine: str) -> dict:
        return {
            "text": text, "original": ev["text"], "speaker": ev["speaker"], "cached": cached, "engine": engine,
            "ocr_ms": ev.get("ocr_ms", 0.0), "tr_ms": (time.perf_counter() - t1) * 1000,
            "latency_ms": (time.monotonic() - ev["t"]) * 1000, "line": ev["line"],
        }
