"""Ana işlem hattı: yakalama → değişim algılama → OCR → takip/tekilleştirme → çeviri.

İş parçacıkları arasında *sırayı koruyan* kısa kuyruklar (FifoQueue) vardır: karakterler hızlı
konuştuğunda OCR ya da çeviri meşgulken gelen satır bir öncekinin üzerine yazılmaz, her satır sırayla
okunur ve çevrilir. 'Kayboldu' ve 'aynı' işaretleri de metinlerle aynı sırada ilerler.

Her ayrı altyazı satırına artan bir kimlik verilir (olaydaki `line`). Aynı satırın güncellemeleri
(daha temiz okuma, uzayan daktilo metni, hızlı çevirinin yerine gelen LLM çevirisi) aynı kimlikle
gelir; gerçekten yeni bir altyazı bir sonraki kimliği alır.

Karma modda (hybrid) çeviri iki aşamalıdır: hızlı yerel çeviri (quickmt) çeviri iş parçacığında hemen yapılır,
asıl motor (yerel LLM / Gemini / DeepL) kendi iş parçacığında sırayla çalışır. Asıl motorun çevirisi satır
okunduktan sonra bekleme penceresi (hybrid_wait_ms) içinde gelirse yalnız o gösterilir; gelmezse süre dolunca
hızlı çeviri gösterilir ve asıl çeviri gelince aynı satırı günceller. Bekleyen satır kendisinden sonraki satırları
ve işaretleri de bekletir (ShowQueue): satırlar sırayla görünür, hiçbiri kaybolmaz, yavaş motor hiçbir satırı
pencereden uzun bekletmez. Asıl motor ilk açılışta hazırlanırken (yerel LLM'de dakikayı bulabilir) satırlar
beklemeden hızlı çeviriyle gösterilir. Sonuçlar Qt sinyalleriyle arayüze iletilir.

Vurgulu kelimeler: OCR'dan sonra satırdaki renkli kelimeler bulunur (okumanın `spans` alanı: İngilizce metindeki
aralıklar). Çevirmene satır [[...]] işaretleriyle gider, çeviri işaretli döner ve önbelleğe işaretli yazılır.
Arayüze giden olayda `text` her zaman düz metindir; vurgular varsa ayrı `spans` alanındadır (Türkçe metindeki
aralıklar). LLM bağlamına işaretsiz metin girer. İki görünümde de vurgu kapalıysa vurgu hiç aranmaz.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

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
from altyazitr.ocr import OcrLine, OcrUnavailable, create_engine
from altyazitr.textproc import (Spans, assemble, clean_marks, highlight_spans, is_valid, mark, match_initial_case,
                                norm_key, unmark, without_marks)
from altyazitr.paths import DATA_DIR
from altyazitr.translate import (QuickMtTranslator, TranslationCache, Translator, TranslatorError, build_glossary,
                                 create_translator, restore_end_punctuation)

log = logging.getLogger(__name__)

REMOTE_ENGINES = ("ollama", "gemini", "deepl")
CACHE_FILE = DATA_DIR / "ceviri_onbellegi.json"
OCR_QUEUE_MAX = 8  # yakalama → OCR (kareler; akıllı algılamada yalnız değişince gelir)
TR_QUEUE_MAX = 16  # OCR → çeviri ve çeviri → uzak motor (küçük sözlükler + işaretler)
# Bu kadar saniye hiç satır gelmezse LLM'e giden bağlam (son satırlar) silinir: yeni sahne sayılır. Sahne içindeki
# sürüşler, ara sahneler ve sessiz anlar boyunca konuşmanın bağlamı (kim kime ne dedi, hitap, ton) kalsın diye uzun.
CONTEXT_RESET_S = 90.0

READY_STATUS = "Hazır — altyazı bekleniyor"
PREPARING_STATUS = "Yapay zekâ hazırlanıyor… Hazır olana kadar hızlı çeviri gösterilir."
FALLBACK_STATUS = "Yedek olarak yerel hızlı çeviri (quickmt) kullanılıyor."


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


@dataclass(eq=False)
class _Out:
    """Gösterim sırasındaki öğe: bir altyazı okuması ya da 'gone' / 'same' işareti."""

    kind: str  # "subtitle" | "gone" | "same"
    ev: dict | None = None  # satırın okuması (Tracker olayı: line, seq, t); işaretlerde None
    sub: dict | None = None  # arayüze gidecek olay; altyazıda None ise asıl çeviri bekleniyor
    quick: dict | None = None  # asıl çeviri vaktinde gelmezse gösterilecek hızlı çeviri (yoksa None)
    deadline: float = math.inf  # asıl çevirinin en geç bekleneceği an

    @property
    def waiting(self) -> bool:
        return self.kind == "subtitle" and self.sub is None

    def is_reading(self, ev: dict) -> bool:
        return self.ev is not None and self.ev["seq"] == ev["seq"] and self.ev["line"] == ev["line"]


class ShowQueue:
    """Arayüze gidecek olayların (altyazı, 'gone', 'same') sırası.

    Karma modda asıl motorun çevirisini bekleyen okuma, en geç süre dolumuna kadar kendisinden sonra gelenleri de
    bekletir: satırlar ekrana sırayla ilk kez çıkar, 'gone' / 'same' işaretleri bekleyen altyazıyı sollamaz (oyun
    altyazıyı kaldırsa da çevirisi önce gösterilir, izleyici okusun; ekranda kalma süresi gösterildiği andan
    sayılır). Bekleme süreyle sınırlı olduğundan sonrakiler de en çok bekleme penceresi kadar gecikir. Önbellekten
    gelen satır, önünde bekleyen yoksa anında çıkar. Kilit tutmaz; işlem hattı korur."""

    def __init__(self) -> None:
        self._items: deque[_Out] = deque()

    def push(self, kind: str, ev: dict | None = None, sub: dict | None = None) -> None:
        """Hazır öğe: hemen gösterilebilecek altyazı (önbellek, hızlı çeviri, karma kapalı) ya da işaret."""
        if ev is not None:
            self._supersede(ev)
        self._items.append(_Out(kind, ev, sub))

    def hold(self, ev: dict, quick: dict | None, deadline: float) -> None:
        """Asıl çeviriyi `deadline` anına kadar bekleyecek okuma; `quick` vaktinde gelmezse gösterilecek olan."""
        deadline = min([deadline, *self._supersede(ev)])
        self._items.append(_Out("subtitle", ev, None, quick, deadline))

    def _supersede(self, ev: dict) -> list[float]:
        """Aynı satırın hâlâ bekleyen (henüz hiç gösterilmemiş) eski okumaları sıradan çıkar; yerlerini bu yeni
        okuma alır (daha temiz ya da daha uzun metin). Süre dolumları döner: satır en geç ilk okumasının süresi
        dolunca görünür."""
        old = [it for it in self._items if it.waiting and it.ev["line"] == ev["line"]]
        for it in old:
            self._items.remove(it)
        return [it.deadline for it in old]

    def resolve(self, ev: dict, sub: dict) -> bool:
        """Asıl çeviri geldi. Okuma henüz gösterilmediyse onunla gösterilecek (True). Okuma çoktan gösterildiyse
        ya da yerini yeni okuması aldıysa False."""
        for it in self._items:
            if it.kind == "subtitle" and it.is_reading(ev):
                it.sub = sub
                return True
        return False

    def fallback(self, ev: dict) -> None:
        """Okumanın asıl çevirisi gelmeyecek (hata, atlandı): bekliyorsa hızlı çevirisi hemen gösterilir."""
        for it in self._items:
            if it.waiting and it.is_reading(ev):
                self._give_up(it)
                return

    def expire(self, now: float) -> None:
        """Süresi dolan okumalar hızlı çeviriye düşer. Sıra bozulmasın diye süresi dolan son okumadan öncekiler de
        (bekleme ayarı çalışırken kısaltıldıysa) beklemeyi bırakır."""
        due = [i for i, it in enumerate(self._items) if it.waiting and it.deadline <= now]
        if due:
            for it in [it for it in list(self._items)[:due[-1] + 1] if it.waiting]:
                self._give_up(it)

    def _give_up(self, it: _Out) -> None:
        if it.quick is not None:
            it.sub = it.quick
        else:  # gösterilecek hızlı çeviri yok: sıradan çıkar; asıl çeviri gelirse güncelleme olarak gider
            self._items.remove(it)

    def pop_ready(self) -> list[_Out]:
        """Sıranın başındaki hazır öğeler (bekleyen ilk okumaya kadar), sırayla."""
        out = []
        while self._items and not self._items[0].waiting:
            out.append(self._items.popleft())
        return out

    def next_deadline(self) -> float | None:
        return min((it.deadline for it in self._items if it.waiting), default=None)

    def clear(self) -> None:
        self._items.clear()


def _two_stage(tc, fast: Translator | None) -> bool:
    """Karma mod: asıl motor uzak/LLM, hızlı yerel çeviri hazır ve kullanıcı açık bırakmış."""
    return fast is not None and bool(tc.hybrid)


def _close_quietly(tr: Translator | None) -> None:
    if tr is not None:
        try:
            tr.close()
        except Exception:
            log.debug("Çeviri motoru kapatılamadı", exc_info=True)


def _translate_line(tr: Translator, ev: dict, context=()) -> str:
    """Okunan satırı çevirir. Vurgulu satır işaretli gider, çeviri işaretli döner (bkz. Translator.translate_marked);
    çıktı son kez denetlenir: bozuk ya da fazla işaret ekrana gitmez, vurgusuz düz metne düşer."""
    spans = ev.get("spans")
    if not spans:
        return tr.translate(ev["text"], context)
    out = tr.translate_marked(mark(ev["text"], spans), context)
    return match_initial_case(ev["text"], clean_marks(out, len(spans), exact=False))


class TrContext:
    """Bir çalıştırmanın çeviri durumu: motorlar, LLM'e giden son satırlar, iki aşamalı mı, asıl motor hazır mı."""

    def __init__(self, tc, main: Translator, fast: Translator | None):
        self.tc = tc
        self.main = main
        self.fast = fast
        # karma mod: hızlı çeviri çeviri döngüsünde, asıl motor ayrı iş parçacığında hazırlanır ve çevirir
        self.two_stage = _two_stage(tc, fast)
        self.model_tag = f"{main.key}:{getattr(main, 'model', '')}"
        # (line, en, tr); tr "" ise satırın güvenilir Türkçesi yok, LLM'e yalnız İngilizcesi gider
        self.hist: deque[tuple[int, str, str]] = deque(maxlen=max(0, tc.context_lines) or 1)
        self.last_t = 0.0
        self.stop = threading.Event()
        # Karma modda asıl motor hazır olana kadar (ya da hazırlanamazsa) satırlar asıl motor hata vermiş gibi
        # işlenir ve LLM bağlamını çeviri döngüsü yazar; hazır olunca bağlamı yalnız uzak iş parçacığı yazar
        # (satır sırasıyla). Kilit bu el değiştirmeyi korur.
        self.lock = threading.Lock()
        self.ready = not self.two_stage

    def set_ready(self) -> None:
        with self.lock:
            self.ready = True

    def cache_key(self, ev: dict) -> str:
        """Önbellek anahtarı. Vurgulu satırınki vurgulu kelimeleri de içerir: aynı metnin vurgulu ve vurgusuz
        çevirileri ayrı tutulur (bulanık arama da karıştırmaz, bkz. Pipeline._cached)."""
        key = f"{self.model_tag}|{ev['key']}"
        spans = ev.get("spans")
        if spans:
            key += "|vurgu:" + "|".join(norm_key(ev["text"][s:e]) for s, e in spans)
        return key


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
        # arayüze gidecek olayların sırası: iki çeviri iş parçacığı da kilit altında yazar ve sinyalleri kilit altında
        # yayar (sinyallerin sırası, olayların sırasıdır)
        self._show = ShowQueue()
        self._show_lock = threading.Lock()
        # yalnız çeviri iş parçacığı yazar, uzak çeviri iş parçacığı okur:
        self._shown_line = 0  # çeviri aşamasından geçen (gösterilen ya da gösterilmeyi bekleyen) en yeni satır
        self._line_seq: dict[int, int] = {}  # satır kimliği → çeviri aşamasından geçen en yeni okumasının seq'i
        self.tracker = Tracker()
        self.cache = TranslationCache()
        self.cache.load(CACHE_FILE)
        self.capturer: WindowCapturer | RegionCapturer | None = None
        self.preview_enabled = True
        self.clock = time.monotonic  # bekleme penceresinin saati (olaylardaki `t` ile aynı); testlerde sahte saat

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
        with self._show_lock:
            self._show.clear()

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
        ev["spans"] = self._highlights(img, lines, text)
        return [("sub", t, ev)]

    def _highlights(self, img, lines: list[OcrLine], text: str) -> Spans:
        """Satırdaki vurgulu (renkli) kelimelerin aralıkları. İki görünümde de vurgu kapalıysa aranmaz. En iyi çaba:
        bir hata satırı düşürmez, satır vurgusuz gider."""
        if not (self.s.style.highlight or self.s.screen_style.highlight) or not isinstance(img, np.ndarray):
            return []
        try:
            return highlight_spans(img, lines, text)
        except Exception:
            log.warning("Vurgulu kelimeler aranamadı; satır vurgusuz gösterilecek", exc_info=True)
            return []

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
        """(asıl, hızlı) çeviri motorları. Karma modda asıl motor burada yalnız oluşturulur: prepare()'i (yerel
        LLM'in diskten yüklenip ısınması dakikayı bulabilir) uzak çeviri iş parçacığında yapılır, o sırada hızlı
        çeviri çalışır. Karma mod kapalıysa asıl motor burada hazırlanır."""
        tc = self.s.translate
        glossary = build_glossary(tc)  # kullanıcı sözlüğü + oyun profilinin çevrilmeyecek adları
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
            if not _two_stage(tc, fast):
                main.prepare(self.status.emit)
            return main, fast
        except Exception as e:
            self._report_prepare_error(e)
        if tc.engine == "quickmt":
            return None, None
        self.status.emit(FALLBACK_STATUS)
        if fast is not None:
            return fast, None
        try:
            fb = QuickMtTranslator(glossary, tc.quickmt_device)
            fb.prepare(self.status.emit)
            return fb, None
        except Exception as e:
            self.error.emit(f"Yedek çeviri de hazırlanamadı: {e}")
            return None, None

    def _report_prepare_error(self, e: Exception) -> None:
        """Asıl çeviri motoru hazırlanamadı: kullanıcıya hata olarak bildirilir (except bloğunda çağrılır)."""
        if isinstance(e, TranslatorError):
            self.error.emit(str(e))
        else:
            log.exception("Çeviri motoru hazırlanamadı")
            self.error.emit(f"Çeviri motoru hazırlanamadı: {e}")

    def _translate_loop(self) -> None:
        main, fast = self._prepare_translators()
        if main is None:
            self._run.clear()
            return
        cx = TrContext(self.s.translate, main, fast)
        remote = None
        if cx.two_stage:
            # Asıl motor kendi iş parçacığında hazırlanır ve çevirir; hızlı çeviriyi hiç bekletmez. O iş parçacığı da
            # sayılır: hat ancak o da bitince (asıl motoru kapatınca) durmuş sayılır, yeniden başlatma onunla çakışmaz.
            with self._alive_lock:
                self._alive += 1
            remote = threading.Thread(target=self._wrap, args=(partial(self._remote_loop, cx),),
                                      name="ceviri-uzak", daemon=True)
            remote.start()
        else:
            self.status.emit(READY_STATUS)
        try:
            while self._run.is_set():
                item = self._tr_q.get(self._until_due())  # bekleyen satırın süresi dolunca da uyanır
                self._apply(self._translate_item(item, cx) if item is not None else [])
        finally:
            cx.stop.set()
            if remote is not None:
                remote.join(cx.tc.timeout_s + 2.0)  # uzak motor bir çevirinin ortasındaysa bitirsin (önbelleğe girsin)
            _close_quietly(fast)
            if remote is None:  # karma modda asıl motoru uzak iş parçacığı kapatır
                _close_quietly(main)
            self.cache.save(CACHE_FILE)

    def _remote_loop(self, cx: TrContext) -> None:
        """Karma modun ikinci aşaması (kendi iş parçacığında): önce asıl motoru hazırlar, sonra uzak çeviri
        kuyruğundaki satırları sırayla çevirir. Asıl motoru bu iş parçacığı kapatır; hazırlık yarıda kesilemediği
        için durdurulursa hazırlık bitince kapatılır."""
        try:
            if not self._prepare_main(cx):
                return
            while self._run.is_set() and not cx.stop.is_set():
                job = self._remote_q.get(0.3)
                if job is not None:
                    self._apply(self._remote_item(job, cx))
        finally:
            _close_quietly(cx.main)

    def _prepare_main(self, cx: TrContext) -> bool:
        """Karma modda asıl motoru hazırlar. Bu sürede çeviri döngüsü satırları asıl motor hata vermiş gibi
        beklemeden hızlı çeviriyle gösterir. Hazırlanamazsa kullanıcıya bildirilir, hat hızlı çeviriyle sürer."""
        self.status.emit(PREPARING_STATUS)
        try:
            cx.main.prepare(self.status.emit)
        except Exception as e:
            if self._run.is_set() and not cx.stop.is_set():  # durdurulurken çıkan hata kullanıcıya gösterilmez
                self._report_prepare_error(e)
                self.status.emit(FALLBACK_STATUS)
            return False
        if not self._run.is_set() or cx.stop.is_set():
            return False
        cx.set_ready()
        self.status.emit(READY_STATUS)
        return True

    # --- gösterim sırası ---------------------------------------------------
    def _apply(self, acts: list[tuple[str, object]]) -> None:
        """Çeviri adımlarının sonuçlarını sırayla uygular, sonra gösterim sırasının hazır başını arayüze iletir.
        Çeviri döngüsü de uzak iş parçacığı da buradan geçer; sinyaller kilit altında yayılır, sıraları bozulmaz.
        Asıl çeviri ile süre dolumu aynı anda gelirse kilidi ilk alan kazanır: ya yalnız asıl çeviri gösterilir ya
        da hızlı çeviri gösterilir ve asıl çeviri onu günceller; satır iki kez yeni sayılmaz, kaybolmaz."""
        with self._show_lock:
            for kind, val in acts:
                if kind == "subtitle":  # hemen gösterilebilir: önbellek, karma kapalı, bekleme yok
                    self._show.push("subtitle", *val)
                elif kind == "hold":  # karma: asıl çeviriyi en geç süre dolumuna kadar bekler
                    self._show.hold(*val)
                elif kind == "upgrade":  # asıl motorun yanıtı (okuma, olay)
                    ev, sub = val
                    if not self._show.resolve(ev, sub) and self._line_seq.get(ev["line"], ev["seq"]) <= ev["seq"]:
                        self._emit_sub(ev, sub)  # satır hızlı çeviriyle gösterilmişti: aynı kimlikle güncellenir
                    # ikisi de değilse çeviri sürerken aynı satırın daha yeni okuması geldi; eski okumanın çevirisi
                    # onu ezmesin (yeni okumanın kendi çevirisi sırada)
                elif kind == "fallback":  # bu okumanın asıl çevirisi gelmeyecek (hata / atlandı)
                    self._show.fallback(val)
                elif kind == "remote":
                    self._remote_q.put(val)
                else:  # 'gone' / 'same': bekleyen altyazıyı sollamaz
                    self._show.push(kind)
            self._flush()

    def _flush(self) -> None:
        """Süresi dolan bekleyen satırları hızlı çeviriye bırakır, sıranın başındaki hazır öğeleri iletir.
        Kilit tutulurken çağrılır."""
        self._show.expire(self.clock())
        for it in self._show.pop_ready():
            if it.kind == "subtitle":
                self._emit_sub(it.ev, it.sub)
            elif it.kind == "gone":
                self.gone.emit()
            else:
                self.same.emit()

    def _emit_sub(self, ev: dict, sub: dict) -> None:
        sub["latency_ms"] = (self.clock() - ev["t"]) * 1000  # okunduğu andan ekrana çıktığı ana
        self.subtitle.emit(sub)

    def _next_deadline(self) -> float | None:
        """Gösterim sırasında asıl çeviriyi bekleyen satırların en yakın süre dolumu (yoksa None)."""
        with self._show_lock:
            return self._show.next_deadline()

    def _until_due(self, cap: float = 0.3) -> float:
        """Çeviri döngüsünün kuyrukta en çok ne kadar bekleyeceği: bekleyen satırın süresi dolunca uyanır. Ayrı
        zamanlayıcı iş parçacığı gerekmez; bekleyen satırı yalnız bu döngü ekler, süreler sonradan uzamaz."""
        dl = self._next_deadline()
        return cap if dl is None else min(cap, max(0.0, dl - self.clock()))

    # --- çeviri adımları -----------------------------------------------------
    def _translate_item(self, item: tuple, cx: TrContext) -> list[tuple[str, object]]:
        """Çeviri kuyruğundan gelen tek öğe → uygulanacak sonuçlar. Karma modda burada yalnız hızlı çeviri
        yapılır (bkz. _fast_stage); diğer modlarda asıl motor burada çalışır."""
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
        out = self._cached(ck, ev)
        if out is not None:  # asıl motorun önceki çevirisi: beklemeden gösterilir
            return [("subtitle", (ev, self._event(ev, out, t1, True, cx.main.label))), *self._note(cx, ev, out)]
        if cx.two_stage:
            return self._fast_stage(cx, ev, t1)
        try:
            out = self._main_translate(cx, ev, ck)
        except Exception as e:
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            self._remember(cx, ev, "")  # yedek çeviri bağlamı kirletmesin: yalnız İngilizcesi
            out = self._safe_fast(cx.fast, ev) if cx.fast is not None else None
            if not out:
                self.status.emit(f"Çeviri hatası: {e}")
                return []
            self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})")
            return [("subtitle", (ev, self._event(ev, out, t1, False, cx.fast.label)))]
        self._remember(cx, ev, out)
        return [("subtitle", (ev, self._event(ev, out, t1, False, cx.main.label)))]

    def _fast_stage(self, cx: TrContext, ev: dict, t1: float) -> list[tuple[str, object]]:
        """Karma modun ilk aşaması: hızlı çeviri. Asıl motor hazırsa satır, okunduğu andan itibaren bekleme
        penceresi kadar asıl çeviriyi bekler ve uzak motorun kuyruğuna bırakılır (pencere 0 ise hızlı çeviri hemen
        gösterilir). Asıl motor hazırlanıyorsa ya da hazırlanamadıysa hata vermiş gibi: hızlı çeviri beklemeden."""
        quick = self._safe_fast(cx.fast, ev)
        qsub = self._event(ev, quick, t1, False, cx.fast.label) if quick else None
        show_now = [("subtitle", (ev, qsub))] if qsub else []
        with cx.lock:
            if not cx.ready:
                self._remember(cx, ev, "")
                return show_now
        wait = max(0, cx.tc.hybrid_wait_ms) / 1000.0
        acts = [("hold", (ev, qsub, ev["t"] + wait))] if wait > 0 else show_now
        return [*acts, ("remote", {"ev": ev, "quick": quick, "done": None})]

    def _note(self, cx: TrContext, ev: dict, tr: str) -> list[tuple[str, object]]:
        """Satırı LLM bağlamına ekler. Karma modda asıl motor hazırsa bağlamı yalnız uzak iş parçacığı yazar (satır
        sırası korunsun): ekleme ona iş olarak gider."""
        with cx.lock:
            if not (cx.two_stage and cx.ready):
                self._remember(cx, ev, tr)
                return []
        return [("remote", {"ev": ev, "quick": None, "done": tr})]

    def _remote_item(self, job: dict, cx: TrContext) -> list[tuple[str, object]]:
        """Karma modun ikinci aşaması: asıl motorun çevirisi. Satır hâlâ bekliyorsa onunla gösterilir; hızlı
        çevirisiyle gösterildiyse aynı kimlikle günceller. Geç kalan yanıt yalnız kendi satırını günceller, yenisini
        ezmez. Çeviri yapılmazsa (hata, satır atlandı) bekleyen satır hızlı çevirisiyle hemen gösterilir."""
        ev, quick, done = job["ev"], job["quick"], job["done"]
        line = ev["line"]
        if done is not None:  # önbellekten gösterildi; yalnız LLM bağlamına eklenir
            self._remember(cx, ev, done)
            return []
        if self._remote_q.has(lambda j: j["ev"]["line"] == line):
            return []  # aynı satırın daha yeni hali sırada; onu çeviririz (bu okuma artık beklemiyor: yerini o aldı)
        if quick and line <= self._shown_line - self._lines_on_screen():
            self._remember(cx, ev, "")
            return [("fallback", ev)]  # satır ekrandan çıkıyor (yerine yenileri geldi): asıl çeviriye değmez
        t1 = time.perf_counter()
        try:
            out = self._main_translate(cx, ev, cx.cache_key(ev))
        except Exception as e:
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            self._remember(cx, ev, "")
            self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})" if quick else f"Çeviri hatası: {e}")
            return [("fallback", ev)]
        self._remember(cx, ev, out)
        return [("upgrade", (ev, self._event(ev, out, t1, False, cx.main.label)))]

    def _lines_on_screen(self) -> int:
        """Ekranda aynı anda kalabilen satır sayısı: akışta ayarlanan kadar, tek satır kipinde 1."""
        fl = getattr(self.s, "flow", None)
        if fl is None or fl.mode != "flow":
            return 1
        return max(1, int(fl.max_lines))

    def _cached(self, ck: str, ev: dict) -> str | None:
        """Önbellekteki çeviri. Vurgulu satır yalnız birebir anahtarla aranır: bulanık eşleşme aynı metnin vurgusuz
        ya da başka vurgulu çevirisini getirmesin. Vurgusuz satıra bulanık eşleşmeyle gelen işaretli çeviri
        işaretsiz kullanılır."""
        if ev.get("spans"):
            return self.cache.get(ck, fuzzy=False)
        out = self.cache.get(ck)
        return without_marks(out) if out is not None else None

    def _main_translate(self, cx: TrContext, ev: dict, ck: str) -> str:
        """Asıl motorla çevirir (LLM'e son satırlar bağlam olarak gider) ve önbelleğe yazar; hata fırlatabilir.
        Vurgulu satırın çevirisi işaretli döner (önbelleğe de öyle yazılır)."""
        if ev["t"] - cx.last_t > CONTEXT_RESET_S:
            cx.hist.clear()  # uzun sessizlik: yeni sahne, eski konuşma yeni satırları yanıltmasın
        ctx = [(en, tr) for ln, en, tr in cx.hist if ln != ev["line"]] if cx.tc.context_lines > 0 else []
        out = _translate_line(cx.main, ev, ctx)
        if not out:
            raise TranslatorError("Boş çeviri döndü")
        out = restore_end_punctuation(ev["text"], out)
        self.cache.put(ck, out)
        return out

    @staticmethod
    def _remember(cx: TrContext, ev: dict, tr: str) -> None:
        """LLM bağlamına ekler; aynı satırın eski hali (daktilo / bozuk okuma) bağlamdan çıkar. `tr` yalnız asıl
        motorun çevirisidir (önbellekten gelen dahil); bağlama işaretsiz girer. Boşsa satırın güvenilir Türkçesi
        yoktur (asıl motor çeviremedi; gösterilen hızlı yedek çeviri sonraki satırları bozmasın): LLM'e yalnız
        İngilizcesi gider."""
        cx.last_t = ev["t"]
        if cx.tc.context_lines <= 0:
            return
        kept = [h for h in cx.hist if h[0] != ev["line"]]
        cx.hist.clear()
        cx.hist.extend(kept)
        cx.hist.append((ev["line"], ev["text"], without_marks(tr)))

    @staticmethod
    def _safe_fast(fast: Translator, ev: dict) -> str | None:
        try:
            out = _translate_line(fast, ev)
            return restore_end_punctuation(ev["text"], out) if out else None
        except Exception:
            log.debug("Hızlı çeviri hatası", exc_info=True)
            return None

    @staticmethod
    def _event(ev: dict, text: str, t1: float, cached: bool, engine: str) -> dict:
        """Arayüze gidecek altyazı olayı; `latency_ms` gösterildiği anda eklenir (bkz. _emit_sub). `text` düz
        metindir; satırda vurgu varsa Türkçe metindeki aralıkları `spans` alanındadır ([[başlangıç, bitiş], …])."""
        spans: Spans = []
        if ev.get("spans"):
            text, spans = unmark(text)
        out = {
            "text": text, "original": ev["text"], "speaker": ev["speaker"], "cached": cached, "engine": engine,
            "ocr_ms": ev.get("ocr_ms", 0.0), "tr_ms": (time.perf_counter() - t1) * 1000, "line": ev["line"],
        }
        if spans:
            out["spans"] = [[s, e] for s, e in spans]
        return out
