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
beklemeden hızlı çeviriyle gösterilir. Asıl motor hazırlanamazsa hat hızlı çeviriyle sürer; yerel yapay zekânın
bilinen hatası (Ollama kapalı, model indirilmemiş) uyarı penceresi yerine bildirimle (notice) söylenir. Sonuçlar Qt
sinyalleriyle arayüze iletilir.

Vurgulu kelimeler: OCR'dan sonra satırdaki renkli kelimeler bulunur (okumanın `spans` alanı: İngilizce metindeki
aralıklar). Çevirmene satır [[...]] işaretleriyle gider, çeviri işaretli döner ve önbelleğe işaretli yazılır.
Arayüze giden olayda `text` her zaman düz metindir; vurgular varsa ayrı `spans` alanındadır (Türkçe metindeki
aralıklar). LLM bağlamına işaretsiz metin girer. İki görünümde de vurgu kapalıysa vurgu hiç aranmaz.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage
from rapidfuzz import fuzz

from altyazitr import winutil
from altyazitr.ai_setup import model_title
from altyazitr.capture import CaptureError, RegionCapturer, WindowCapturer, roi_screen_rect
from altyazitr.config import Settings
from altyazitr.detect import ChangeGate, GateParams, TextDetector, TextGate, TextGateParams, text_mask
from altyazitr.english import english_score
from altyazitr.ocr import OcrLine, OcrUnavailable, create_engine
from altyazitr.textproc import (Spans, assemble, clean_marks, highlight_spans, is_valid, mark, match_initial_case,
                                norm_key, subtitle_box, unmark, without_marks)
from altyazitr.paths import DATA_DIR
from altyazitr.subtitle_style import SubtitleFilter
from altyazitr.translate import (QuickMtTranslator, TranslationCache, Translator, TranslatorError, build_glossary,
                                 create_translator, release_ollama_models, restore_end_punctuation)

log = logging.getLogger(__name__)

REMOTE_ENGINES = ("ollama", "gemini", "deepl")
CACHE_FILE = DATA_DIR / "ceviri_onbellegi.json"
OCR_QUEUE_MAX = 8  # yakalama → OCR (kareler; akıllı algılamada yalnız değişince gelir)
# OpenCV'nin iş parçacığı sayısı (süreç geneli). Buradaki küçük görüntülerde çok iş parçacığı süreyi kısaltmıyor,
# işlemci zamanını 3-4 kat artırıyor (ölçüm: tam kare kenar maskesi 1 iş parçacığıyla 14 ms / 15 ms işlemci, 24 iş
# parçacığıyla 14 ms / 57 ms işlemci).
CV_THREADS = 1
TR_QUEUE_MAX = 16  # OCR → çeviri ve çeviri → uzak motor (küçük sözlükler + işaretler)
# Bu kadar saniye hiç satır gelmezse LLM'e giden bağlam (son satırlar) silinir: yeni sahne sayılır. Sahne içindeki
# sürüşler, ara sahneler ve sessiz anlar boyunca konuşmanın bağlamı (kim kime ne dedi, hitap, ton) kalsın diye uzun.
CONTEXT_RESET_S = 90.0

READY_STATUS = "Hazır — altyazı bekleniyor"
PREPARING_STATUS = "Yapay zekâ hazırlanıyor… Hazır olana kadar hızlı çeviri gösterilir."
# Asıl motorun kendi hazırlık iletilerine eklenir ("Gemma 4 12B belleğe yükleniyor… 5 sn"): altyazıların o sırada
# hızlı çeviriyle geldiği görünsün, hat takılmış sanılmasın.
WHILE_PREPARING = " (şimdilik hızlı çeviri)"
STOPPING_STATUS = "Durduruluyor…"
RESTART_PENDING_STATUS = "Önceki çalıştırma kapanıyor; bitince başlayacak…"
RECOVER_STATUS = "Yapay zekâ yanıt vermiyor; yeniden hazırlanıyor. Bu sırada hızlı çeviri gösterilir."
FALLBACK_STATUS = "Yedek olarak yerel hızlı çeviri (quickmt) kullanılıyor."
# Asıl motorun süresi bekleme penceresini bu kattan fazla aşıyorsa satır hiç bekletilmez (bkz. Pipeline._wait_s)
WAIT_GIVE_UP = 1.25
# Asıl çevirinin ne zaman geleceği tahmin edilirken ortanca süreye eklenen pay (süre oynar): tahmin bekleme
# süresinin hemen dışına düşerse hızlı çeviri bir an görünüp asıl çeviriyle değişmesin ("göz kırpması").
ARRIVE_MARGIN = 1.15
ARRIVE_PAD_S = 0.08
# Yerel yapay zekâ hazırlanamadı, hat hızlı çeviriyle sürüyor: notice'in 1. satırı (2. satır kısa neden)
AI_FALLBACK_NOTICE = "{model} hazırlanamadı; hızlı çeviri kullanılıyor."
# Karma mod kapalıyken asıl motor satır başına bundan yavaşsa (son satırların ortancası) kullanıcıya bir kez
# Hibrit mod önerilir: o ayarda satırlar çeviriyi beklerken ekranda hiçbir şey görünmez.
SLOW_HINT_S = 2.0
AI_SLOW_NOTICE = ("Yapay zekâ çevirisi yavaş (satır başına ~{sec} sn): Hibrit modu açın.\n"
                  "Çeviri sayfasında Hibrit mod açıkken satırlar önce hızlı çeviriyle görünür, yapay zekânınki "
                  "gelince yerine geçer.")
SERVICE_SAMPLES = 5  # asıl motorun hızı son bu kadar satırın ortancasıdır
# Asıl motor art arda bu kadar satırda hata verdiyse satırlar onu beklemez (hızlı çeviri hemen); motor gerçekten
# çalışamıyorsa (yerel LLM'in modeli bellekte değil: Ollama kapandı ya da yeniden başladı) yeniden hazırlanır.
FAIL_LIMIT = 2
# Hat çalışırken bu kadar süre çeviri istenmediyse asıl motorun modeli sıcak tutulur (Translator.keep_warm):
# Ollama'nın bellekte tutma süresinden (30 dk) kısa olmalı.
KEEP_WARM_S = 600.0


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
            if recent and len(k) >= len(self.key) + 4 and fuzz.partial_ratio(self.key, k) >= 95:
                # metin uzadı (daktilo efekti / ikinci satır). Titreme denetiminden önce: uzun satırın sonuna birkaç
                # harf eklenince benzerlik titreme eşiğini geçer, uzayan hal yutulmasın
                replace = True
            elif r >= thr and recent:
                self.gone_t = None
                if k == self.key or score < self.score + self.UPGRADE_MARGIN:
                    return None  # aynı satır (OCR titremesi)
                replace = True  # aynı satırın daha temiz okuması
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
    quick_due: bool = False  # hızlı çeviri henüz hesaplanıyor (asıl istek ondan önce başladı; bkz. set_quick)

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

    def hold(self, ev: dict, quick: dict | None, deadline: float, quick_due: bool = False) -> None:
        """Asıl çeviriyi `deadline` anına kadar bekleyecek okuma; `quick` vaktinde gelmezse gösterilecek olan.
        quick_due: hızlı çeviri sonradan gelecek (set_quick); gelene kadar okuma ona da bekler."""
        deadline = min([deadline, *self._supersede(ev)])
        self._items.append(_Out("subtitle", ev, None, quick, deadline, quick_due))

    def set_quick(self, ev: dict, quick: dict | None) -> None:
        """Bekleyen okumanın hızlı çevirisi hazır (None: hızlı çeviri yapılamadı). Okuma asıl çeviriyle çoktan
        gösterildiyse bir şey yapmaz; beklemeyi bıraktıysa (süre doldu ya da asıl çeviri hata verdi) hemen
        hızlı çeviriyle gösterilir."""
        for it in self._items:
            if it.kind == "subtitle" and it.is_reading(ev):
                it.quick_due = False
                if it.waiting:
                    it.quick = quick
                    if it.deadline == math.inf:  # bekleme bitmiş, yalnız hızlı çeviri bekleniyordu
                        self._give_up(it)
                return

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
        elif it.quick_due:  # hızlı çeviri birazdan gelir (set_quick): satır kaybolmasın, ona kadar bekler
            it.deadline = math.inf
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
    """İki aşamalı çeviri: asıl motor uzak/LLM ve hızlı yerel çeviri hazır. Asıl motor kendi iş parçacığında
    hazırlanır ve çevirir; hazırlanırken (yerel LLM'in belleğe yüklenmesi) ve hata verdiğinde satırlar hızlı
    çeviriyle gösterilir, hat hiç beklemez. Karma mod ayarı (tc.hybrid) yalnız asıl çevirinin ne kadar
    bekleneceğini belirler (bkz. Pipeline._wait_s): kapalıysa satır asıl çeviri gelene ya da hata verene dek bekler."""
    return fast is not None


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
        system = getattr(main, "system", "")
        if isinstance(system, str) and system:
            # Önbellek anahtarı istemi de içerir: kurallar (sözlük, istem) değişince eski çeviriler kullanılmaz
            self.model_tag += ":" + hashlib.sha1(system.encode("utf-8")).hexdigest()[:8]
        # (line, en, tr); tr "" ise satırın güvenilir Türkçesi yok, LLM'e yalnız İngilizcesi gider
        self.hist: deque[tuple[int, str, str]] = deque(maxlen=max(0, tc.context_lines) or 1)
        self.last_t = 0.0
        self.stop = threading.Event()
        # Karma modda asıl motor hazır olana kadar (ya da hazırlanamazsa) satırlar asıl motor hata vermiş gibi
        # işlenir ve LLM bağlamını çeviri döngüsü yazar; hazır olunca bağlamı yalnız uzak iş parçacığı yazar
        # (satır sırasıyla). Kilit bu el değiştirmeyi korur.
        self.lock = threading.Lock()
        self.ready = not self.two_stage
        self._service: deque[float] = deque(maxlen=SERVICE_SAMPLES)  # asıl motorun son satır süreleri (sn)
        self._busy_t: float | None = None  # asıl motorun süren çevirisi ne zaman başladı (perf_counter); boştaysa None
        self.fails = 0  # asıl motorun art arda hata verdiği satır sayısı (uzak iş parçacığı yazar)
        # LLM bağlamını (hist) çoğunlukla tek iş parçacığı yazar (bkz. lock); motor yeniden hazırlanırken ikisi
        # birden yazabilir: bu kilit bağlamın kendisini korur
        self.hist_lock = threading.Lock()
        self.last_call = time.monotonic()  # asıl motor en son ne zaman çalıştı (sıcak tutma için)

    def set_ready(self) -> None:
        with self.lock:
            self.ready = True
        self.fails = 0
        self.last_call = time.monotonic()

    def set_not_ready(self) -> None:
        """Asıl motor yeniden hazırlanacak: o sırada satırlar hata vermiş gibi hemen hızlı çeviriyle gösterilir."""
        with self.lock:
            self.ready = False

    @property
    def failing(self) -> bool:
        return self.fails >= FAIL_LIMIT

    @property
    def service_s(self) -> float | None:
        """Asıl motorun satır başına süresi: son satırların ortancası; henüz ölçülmediyse None. Ortanca olduğu için
        tek bir zaman aşımı ya da takılma sonraki satırların kararını bozmaz; makine gerçekten yavaşsa (ya da
        hızlandıysa) birkaç satırda izler."""
        with self.lock:
            return statistics.median(self._service) if self._service else None

    def steady_service_s(self) -> float | None:
        """service_s, ama yalnız yeterince satır ölçüldükten sonra (kullanıcıya gösterilecek karar için)."""
        with self.lock:
            return statistics.median(self._service) if len(self._service) == self._service.maxlen else None

    def job_started(self) -> None:
        """Asıl motor bir satırı çevirmeye başladı (uzak iş parçacığı)."""
        with self.lock:
            self._busy_t = time.perf_counter()
        self.last_call = time.monotonic()

    def note_service(self, seconds: float) -> None:
        """Asıl motorun bir satırı çevirme süresini (hata ve zaman aşımı dahil) kaydeder; süren iş bitti."""
        with self.lock:
            self._service.append(max(0.0, seconds))
            self._busy_t = None

    def expected_s(self, queued: int) -> float | None:
        """Şimdi gelen satırın asıl çevirisinin kaç saniye sonra gelmesi beklenir: süren çevirinin kalanı + sıradaki
        işler + kendisi. Henüz ölçüm yoksa None."""
        with self.lock:
            if not self._service:
                return None
            service = statistics.median(self._service)
            left = 0.0 if self._busy_t is None else max(0.0, service - (time.perf_counter() - self._busy_t))
            return left + service * (queued + 1)

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
    error = Signal(str)  # ölümcül ya da kullanıcının düzeltmesi gereken hata (arayüz uyarı penceresiyle gösterir)
    # Pencere açmayan bildirim (1. satır kısa metin, 2. satır kısa neden): hat sürüyor, yalnız haber verilir. Örnek:
    # yerel yapay zekâ hazırlanamadı, hızlı çeviri kullanılıyor. İş parçacığından yayılır (arayüze kuyrukla gider).
    notice = Signal(str)
    running_changed = Signal(bool)
    preview = Signal(QImage, float)
    subtitle = Signal(dict)
    same = Signal()
    gone = Signal()
    # Önceki çalıştırma kapanırken basılan Başlat: son iş parçacığı bitince ana iş parçacığında başlatır
    _resume = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._run = threading.Event()
        self._threads: list[threading.Thread] = []
        self._alive = 0
        # Çalıştırmanın açılışı ve kapanışı bu kilitle sıraya girer: son iş parçacığı kapanışını bitirmeden yeni
        # çalıştırma başlamaz (yeniden girilebilir: sinyaller kilit altında yayılır, alıcı hattı yeniden çağırabilir)
        self._alive_lock = threading.RLock()
        self._pending_start = False  # hat kapanırken Başlat'a basıldı: kapanınca başlayacak
        self._stop_announced = False  # bu çalıştırma için running_changed(False) yayıldı mı
        self._main_tr: Translator | None = None  # hazırlanan/çalışan asıl motor: stop() hazırlığını bırakabilsin
        self._slow_hinted = False  # "yapay zekâ yavaş, Hibrit modu açın" bildirimi bu oturumda verildi mi
        self._resume.connect(self._start_pending, Qt.ConnectionType.QueuedConnection)
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
        """Hattı başlatır. Önceki çalıştırmanın iş parçacıkları hâlâ kapanıyorsa istek kaybolmaz: kapanınca başlar."""
        with self._alive_lock:
            if self._run.is_set():
                return
            waiting = self._pending_start = self._alive > 0
            if not waiting:
                self._stop_announced = False
                self._run.set()
                self._clear_queues()
                self._line_seq.clear()
                self.tracker = Tracker(self.tracker.line)  # satır kimlikleri yeniden başlatmada da artmaya devam eder
                self._threads = [
                    threading.Thread(target=self._wrap, args=(self._capture_loop,), name="yakalama", daemon=True),
                    threading.Thread(target=self._wrap, args=(self._ocr_loop,), name="ocr", daemon=True),
                    threading.Thread(target=self._wrap, args=(self._translate_loop,), name="ceviri", daemon=True),
                ]
                self._alive = len(self._threads)
        if waiting:
            self.status.emit(RESTART_PENDING_STATUS)
            return
        for t in self._threads:
            t.start()
        self.running_changed.emit(True)
        log.info("İşlem hattı başlatıldı")

    def _start_pending(self) -> None:
        """Önceki çalıştırma kapandı (_resume): o sırada basılan Başlat hâlâ geçerliyse hattı başlatır."""
        if self._pending_start:
            self.start()

    def stop(self) -> None:
        """Hattı durdurur. Arayüz hemen "durdu" görür (running_changed(False)); iş parçacıkları arkada kapanır.
        Asıl motorun süren hazırlığı (yerel LLM'in belleğe yüklenmesi dakikayı bulabilir) bırakılır: Durdur ve
        yeniden Başlat onu beklemez."""
        was_running = self._run.is_set()
        self._pending_start = False
        self._run.clear()
        self._clear_queues()
        self._abort_main()
        if was_running and self._announce_stop():
            self.status.emit(STOPPING_STATUS)

    def _fail_stop(self) -> None:
        """Hat kendiliğinden duruyor (beklenmeyen hata; OCR ya da çeviri motoru açılamadı). stop() gibi: asıl motorun
        süren hazırlığı bırakılır ve arayüz hemen "durdu" görür (Başlat düğmesi yeniden başlatır); iş parçacıkları
        arkada kapanır."""
        self._run.clear()
        self._abort_main()
        self._announce_stop()

    def _abort_main(self) -> None:
        """Asıl motorun hazırlığını bırakır. _run temizlendikten SONRA çağrılır: motoru kuran iş parçacığı onu
        _main_tr'ye yazdıktan sonra _run'a bakar, böylece ikisinden biri ötekini mutlaka görür."""
        tr = self._main_tr
        if tr is not None:
            try:
                tr.abort()
            except Exception:
                log.debug("Çeviri motorunun hazırlığı bırakılamadı", exc_info=True)

    def _announce_stop(self) -> bool:
        """running_changed(False)'ı çalıştırma başına bir kez yayar; yaydıysa True."""
        with self._alive_lock:
            if self._stop_announced:
                return False
            self._stop_announced = True
        self.running_changed.emit(False)
        return True

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
            self._fail_stop()
        finally:
            with self._alive_lock:
                self._alive -= 1
                if self._alive == 0:  # son iş parçacığı: kapanışın tamamı kilit altında, start() araya giremez
                    self.capturer = None
                    self._main_tr = None
                    self._announce_stop()  # kendiliğinden durduysa (hata) arayüz şimdi öğrenir
                    self.status.emit("Durduruldu")
                    log.info("İşlem hattı durdu")
                    if self._pending_start:  # kapanırken Başlat'a basılmıştı
                        self._resume.emit()

    def grab_full_frame(self, timeout: float = 2.0) -> np.ndarray | None:
        """Pencere modunda çalışan yakalamadan tam kare (bölge seçimi için)."""
        cap = self.capturer
        return cap.grab_full(timeout) if isinstance(cap, WindowCapturer) else None

    def peek_full_frame(self) -> np.ndarray | None:
        """Pencere modunda çalışan yakalamanın son tam karesi, beklemeden (Ana sayfanın canlı önizlemesi; ikinci bir
        yakalama açılmasın). Ekran modunda ya da yakalama yokken None."""
        cap = self.capturer
        return cap.peek_full() if isinstance(cap, WindowCapturer) else None

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

    def _make_gate(self) -> TextGate | ChangeGate | None:
        """Algılama geçidi. Yapay zekâ metin bulucu açıksa TextGate: sabit aralık kipinde de çalışır (yazı belirince
        ya da değişince aralığı beklemeden okunur, aralık sigortadır). Bulucu kapalıysa ya da açılamazsa akıllı kipte
        kurallı ChangeGate; sabit aralık kipinde geçit yoktur."""
        o = self.s.ocr
        if o.text_detector:
            try:
                return TextGate(TextDetector(), TextGateParams(stable_frames=o.stable_frames,
                                                               lowest_only=o.keep_lowest_block))
            except Exception:
                log.warning("Yapay zekâ metin bulucu açılamadı; yalnız hızlı algılama kullanılacak", exc_info=True)
        return self._rule_gate()

    def _rule_gate(self) -> ChangeGate | None:
        """Metin bulucusuz geçit: akıllı kipte kurallı kenar maskesi, sabit aralık kipinde yok."""
        o = self.s.ocr
        return None if o.detect_mode == "interval" else ChangeGate(
            GateParams(stable_frames=o.stable_frames, bright=o.bright_threshold))

    def _capture_loop(self) -> None:
        c, o = self.s.capture, self.s.ocr
        cv2.setNumThreads(CV_THREADS)
        gate = self._make_gate()
        interval = o.detect_mode == "interval"
        period = 1.0 / max(1, c.fps)
        last_find = -10.0
        last_preview = 0.0
        last_read = 0.0  # sabit aralık kipinde son okuma isteği
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
                        if gate is not None:
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
                        if gate is not None:
                            gate.reset()
                else:
                    cap.set_region(tuple(c.region))
                # Pencere yakalamada kare geldiği anda işlenir (bekleme yok); ekran bölgesi tarama hızında yoklanır
                wait = getattr(cap, "wait_frame", None)
                fresh = wait is None or wait(max(0.25, 2 * period))
                t0 = time.monotonic()
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
                if not fresh:
                    continue  # pencerenin görüntüsü değişmedi (Windows yeni kare vermedi): yeniden işlemeye gerek yok

                if gate is not None:
                    gate.p.stable_frames = o.stable_frames
                    if isinstance(gate, ChangeGate):
                        gate.p.bright = o.bright_threshold
                    try:
                        action = gate.update(roi, t0)
                    except Exception:  # metin bulucu çalışmadı: kurallı algılamayla sürer
                        log.warning("Metin bulucu çalışmadı, yalnız hızlı algılamayla devam", exc_info=True)
                        gate, action = self._rule_gate(), None
                    if action is not None and self._on_gate(action, t0, roi):
                        last_read = t0
                if interval and t0 - last_read >= o.interval_ms / 1000.0:
                    last_read = t0
                    # sabit aralık örneklemedir: OCR yetişemezse bekleyen eski kare en yenisiyle değişir
                    self._ocr_q.put(("text", t0, roi), merge=_is_frame)

                if self.preview_enabled and t0 - last_preview > 0.25:
                    last_preview = t0
                    if isinstance(gate, TextGate):
                        mask, ink = gate.mask(), gate.ink
                    elif gate is not None:
                        mask, ink = gate.prev, gate.ink
                    else:
                        mask = text_mask(roi, o.bright_threshold)
                        ink = float(mask.mean())
                    try:
                        self.preview.emit(_to_qimage(roi, mask), ink)
                    except Exception:
                        log.debug("Önizleme üretilemedi", exc_info=True)

                dt = time.monotonic() - t0
                if dt < period:
                    time.sleep(period - dt)
        finally:
            if cap is not None:
                cap.stop()

    def _on_gate(self, action, t: float, roi: np.ndarray) -> bool:
        """Geçidin kararını OCR kuyruğuna koyar; kare okunacaksa True. Değişimle gelen her kare sırayla okunur,
        hiçbiri atlanmaz. Erken okuma ("spec") yalnız OCR'ın önünde iş yokken istenir: yetişemeyen makinede boşuna
        yük olmasın (doğrulandığında sonucu yoksa kare yine okunur, bkz. _ocr_item)."""
        kind = action if isinstance(action, str) else action.kind  # ChangeGate dize, TextGate GateAction döner
        if kind == "gone":
            self._ocr_q.put(("gone", t, None))
            return False
        if kind == "spec":
            if not len(self._ocr_q):
                self._ocr_q.put(("spec", t, (roi, action.spec)))
            return False
        spec = getattr(action, "spec", None)
        self._ocr_q.put(("text", t, roi) if spec is None else ("confirm", t, (roi, spec)))
        return True

    # --- OCR ------------------------------------------------------------
    def _ocr_loop(self) -> None:
        o = self.s.ocr
        try:
            engine = create_engine(o.engine, o.upscale, o.preprocess)
        except OcrUnavailable as e:
            self.error.emit(str(e))
            self._fail_stop()
            return
        except Exception as e:
            log.exception("OCR motoru başlatılamadı")
            self.error.emit(f"OCR motoru başlatılamadı: {e}")
            self._fail_stop()
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

    # Erken okumanın sonucu (kimlik, t, kare, satırlar, ms): geçit yazının değişmediğini doğrulayana kadar tutulur
    _held: tuple | None = None
    # "Yalnız altyazıları oku" süzgeci (ilk okumada kurulur): kabul ettiği altyazılardan ortayı ve boyu öğrenir
    _subtitles: SubtitleFilter | None = None

    def _ocr_item(self, engine, item: tuple) -> list[tuple]:
        """Kuyruktan gelen tek öğeyi işler; çeviri kuyruğuna sırayla gidecek öğeleri döndürür:
        ("sub", t, olay) yeni/güncellenen satır, ("gone", t, None) altyazı kayboldu, ("same", t, None)
        aynı satır hâlâ ekranda. İşaretler de kuyruktan geçer ki arayüze metinlerle aynı sırada ulaşsın.

        Kuyruk öğeleri: ("text", t, kare) oku; ("gone", t, None); ("spec", t, (kare, kimlik)) yazının belirdiği kare:
        okunur, sonucu tutulur, hiçbir şey gönderilmez; ("confirm", t, (kare, kimlik)) sonraki kare aynı yazıyı
        gösteriyor: tutulan sonuç (yoksa bu kare) kullanılır. Böylece OCR, yazının durulmasını beklerken çalışır;
        yazı o arada değiştiyse (beliriyor, uzuyor) erken okuma atılır, yarım satır gönderilmez."""
        o = self.s.ocr
        kind, t, payload = item
        if kind == "spec":
            img, sid = payload
            lines, ms = self._recognize(engine, img)
            self._held = (sid, t, img, lines, ms) if lines is not None else None
            return []
        held, self._held = self._held, None
        if kind == "gone":
            return self._gone_marker(t)
        if kind == "confirm" and held is not None and held[0] == payload[1]:
            _sid, t, img, lines, ms = held  # erken okunan kare: satır o anda okunmuş sayılır
        else:
            img = payload[0] if kind == "confirm" else payload
            lines, ms = self._recognize(engine, img)
        if lines is None:
            return []
        if o.subtitle_only:  # plaka, tabela, HUD gibi altyazı biçiminde olmayan satırlar atılır (bkz. subtitle_style)
            self._subtitles = self._subtitles or SubtitleFilter()
            lines = self._subtitles.keep(img, lines)
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
        # altyazının görüntüdeki ve bölgenin ekrandaki yeri: çeviri oyunun altyazısının üstüne yazılabilsin (display)
        ev["box"] = subtitle_box(lines, getattr(img, "shape", (0, 0))[1::-1], o.keep_lowest_block)
        if ev["box"] is not None:
            ev["box"]["screen"] = roi_screen_rect(self.capturer)
        return [("sub", t, ev)]

    @staticmethod
    def _recognize(engine, img) -> tuple[list[OcrLine] | None, float]:
        """(satırlar, süre ms); OCR hata verirse satırlar None."""
        t1 = time.perf_counter()
        try:
            lines = engine.recognize(img)
        except Exception:
            log.exception("OCR hatası")
            return None, 0.0
        return lines, (time.perf_counter() - t1) * 1000

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
        çeviri çalışır; karma mod ayarından bağımsızdır (bkz. _two_stage). Hızlı çevirisi olmayan motorlar (quickmt'nin
        kendisi) ve hızlı çeviri hazırlanamadıysa asıl motor burada hazırlanır."""
        tc = self.s.translate
        if tc.engine != "ollama":  # yerel yapay zekâdan başka motora geçildi: model ekran kartını tutmasın
            release_ollama_models()
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
        main: Translator | None = None
        try:
            main = create_translator(tc, glossary)
            self._main_tr = main  # stop() süren hazırlığı bırakabilsin (önce yaz, sonra _run'a bak: _abort_main)
            if not self._run.is_set():  # hızlı çeviri yüklenirken Durdur'a basıldı: asıl motor hiç hazırlanmaz
                _close_quietly(main)
                _close_quietly(fast)
                return None, None
            if not _two_stage(tc, fast):
                main.prepare(self.status.emit)
            return main, fast
        except Exception as e:
            if not self._run.is_set():  # durdurulurken yarıda kesilen hazırlık: hata değil, yedeğe de geçilmez
                _close_quietly(main)
                _close_quietly(fast)
                return None, None
            self._report_prepare_error(e, main, quick=fast is not None)  # hızlı çeviri hazırsa hat onunla sürer
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

    def _report_prepare_error(self, e: Exception, main: Translator | None = None, *, quick: bool = False) -> None:
        """Asıl çeviri motoru hazırlanamadı (except bloğunda çağrılır). quick: hat hızlı çeviriyle sürüyor.

        Yerel yapay zekânın bilinen hatası (TranslatorError: Ollama kapalı ve başlatılamadı, model indirilmemiş) hat
        hızlı çeviriyle sürerken notice ile söylenir: her Başlat'ta uyarı penceresi açılmaz. Beklenmeyen hatalar ve
        çevrimiçi motorların hataları (ör. API anahtarı girilmemiş: kullanıcı düzeltmeli) error ile bildirilir."""
        if quick and isinstance(e, TranslatorError) and getattr(main, "key", "") == "ollama":
            text = str(e).strip()
            name = model_title(str(getattr(main, "model", "") or "")) or "Yerel yapay zekâ"
            log.warning("%s hazırlanamadı, hızlı çeviri kullanılıyor: %s", name, text.replace("\n", " | "))
            head = AI_FALLBACK_NOTICE.format(model=name)
            reason = text.splitlines()[0] if text else ""  # kısa neden; ayrıntı satırı günlükte
            self.notice.emit(f"{head}\n{reason}" if reason else head)
        elif isinstance(e, TranslatorError):
            self.error.emit(str(e))
        else:
            log.exception("Çeviri motoru hazırlanamadı")
            self.error.emit(f"Çeviri motoru hazırlanamadı: {e}")

    def _translate_loop(self) -> None:
        main, fast = self._prepare_translators()
        if main is None:
            self._fail_stop()  # durdurulduysa zaten bildirildi; motor hiç kurulamadıysa arayüz şimdi öğrenir
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
        """İki aşamalı çevirinin ikinci aşaması (kendi iş parçacığında): önce asıl motoru hazırlar, sonra uzak çeviri
        kuyruğundaki satırları sırayla çevirir. Asıl motoru bu iş parçacığı kapatır. Hat durdurulunca stop() süren
        hazırlığı bırakır (Translator.abort): iş parçacığı modelin yüklenmesini beklemeden kapanır."""
        try:
            if not self._prepare_main(cx):
                return
            while self._run.is_set() and not cx.stop.is_set():
                job = self._remote_q.get(0.3)
                if job is not None:
                    self._apply(self._remote_item(job, cx))
                    if cx.failing and not self._main_alive(cx) and not self._recover_main(cx):
                        return
                elif time.monotonic() - cx.last_call >= KEEP_WARM_S:  # uzun sessizlik: model bellekten düşmesin
                    cx.last_call = time.monotonic()
                    try:
                        cx.main.keep_warm()
                    except Exception:
                        log.debug("Asıl çeviri motoru sıcak tutulamadı", exc_info=True)
        finally:
            _close_quietly(cx.main)

    @staticmethod
    def _main_alive(cx: TrContext) -> bool:
        try:
            return cx.main.healthy()
        except Exception:
            log.debug("Asıl çeviri motorunun durumu okunamadı", exc_info=True)
            return True  # bilinmiyorsa yeniden hazırlanmaz: hatalar yalnız satırları beklemekten kurtarır

    def _recover_main(self, cx: TrContext) -> bool:
        """Asıl motor art arda hata veriyor ve çalışamıyor (yerel LLM: Ollama kapandı ya da yeniden başladı, model
        bellekten düştü). Satırlar o sırada hemen hızlı çeviriyle gösterilir; sıradaki işler bekletilmeden hızlı
        çeviriye bırakılır, motor yeniden hazırlanır (gerekirse Ollama yeniden başlatılır, model belleğe yüklenir).
        Hazırlanamazsa bildirilir, hat hızlı çeviriyle sürer (False)."""
        log.warning("Asıl çeviri motoru art arda %d satırda hata verdi ve çalışmıyor; yeniden hazırlanıyor", cx.fails)
        cx.set_not_ready()
        acts: list[tuple[str, object]] = []
        while (job := self._remote_q.get(0)) is not None:
            if job["done"] is None:
                acts.append(("fallback", job["ev"]))
            self._remember(cx, job["ev"], job["done"] or "")
        self._apply(acts)
        self.status.emit(RECOVER_STATUS)
        return self._prepare_main(cx)

    def _prepare_main(self, cx: TrContext) -> bool:
        """Asıl motoru hazırlar. Bu sürede çeviri döngüsü satırları asıl motor hata vermiş gibi beklemeden hızlı
        çeviriyle gösterir; motorun kendi hazırlık iletilerine bunu söyleyen bir ek konur (hat takılmış sanılmasın).
        Hazırlanamazsa kullanıcıya bildirilir (yerel yapay zekânın bilinen hatası notice ile, bkz.
        _report_prepare_error), hat hızlı çeviriyle sürer."""
        if not self._run.is_set() or cx.stop.is_set():  # hazırlık başlamadan durduruldu
            return False
        if cx.fails == 0:  # yeniden hazırlıkta durum satırı zaten söyledi (RECOVER_STATUS)
            self.status.emit(PREPARING_STATUS)
        t0 = time.monotonic()
        try:
            cx.main.prepare(lambda msg: self.status.emit(msg + WHILE_PREPARING))
            log.info("Asıl çeviri motoru hazır: %s, %.1f sn", cx.model_tag, time.monotonic() - t0)
        except Exception as e:
            if self._run.is_set() and not cx.stop.is_set():  # durdurulurken çıkan hata kullanıcıya gösterilmez
                self._report_prepare_error(e, cx.main, quick=True)
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
                elif kind == "quick":  # bekleyen okumanın hızlı çevirisi (asıl istek ondan önce başladı)
                    self._show.set_quick(*val)
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
        try:  # tek aşama: hızlı çevirisi olmayan motor (quickmt'nin kendisi ya da hızlı çeviri hazırlanamadı)
            out = self._main_translate(cx, ev, ck)
        except Exception as e:
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            self._remember(cx, ev, "")  # çevrilemeyen satır bağlama yalnız İngilizcesiyle girer
            self.status.emit(f"Çeviri hatası: {e}")
            return []
        self._remember(cx, ev, out)
        return [("subtitle", (ev, self._event(ev, out, t1, False, cx.main.label)))]

    def _fast_stage(self, cx: TrContext, ev: dict, t1: float) -> list[tuple[str, object]]:
        """İki aşamalı çevirinin ilk aşaması: hızlı çeviri. Asıl motor hazırsa satır, okunduğu andan itibaren
        bekleme süresi kadar (bkz. _wait_s) asıl çeviriyi bekler ve uzak motorun kuyruğuna bırakılır; süre 0 ise
        hızlı çeviri hemen gösterilir. Asıl motor hazırlanıyorsa ya da hazırlanamadıysa hata vermiş gibi: hızlı
        çeviri beklemeden."""
        with cx.lock:
            ready = cx.ready
        job = None
        if ready and (wait := self._wait_s(cx, self.clock() - ev["t"])) > 0:
            # Asıl istek hızlı çeviriyi beklemeden başlasın (karma kapalıyken her satırın yolu budur): önce bekleme
            # kaydı ve uzak iş, sonra hızlı çeviri. Okuma, hızlı çevirisi gelene kadar onu da bekler (set_quick).
            job = {"ev": ev, "quick": None, "done": None}
            self._apply([("hold", (ev, None, ev["t"] + wait, True)), ("remote", job)])
        quick = self._safe_fast(cx.fast, ev)
        qsub = self._event(ev, quick, t1, False, cx.fast.label) if quick else None
        if job is not None:
            job["quick"] = quick
            return [("quick", (ev, qsub))]
        show_now = [("subtitle", (ev, qsub))] if qsub else []
        with cx.lock:
            if not cx.ready:
                self._remember(cx, ev, "")
                return show_now
        return [*show_now, ("remote", {"ev": ev, "quick": quick, "done": None})]

    def _wait_s(self, cx: TrContext, lag: float = 0.0) -> float:
        """Satırın asıl çeviriyi en çok kaç saniye bekleyeceği.

        Karma mod kapalı: asıl çeviri beklenir (istek zaman aşımına kadar); hızlı çeviri yalnız hata ya da zaman
        aşımında görünür. Karma mod açık: bekleme penceresi (hybrid_wait_ms). Asıl motorun son süreleri ve sıradaki
        işler, bu satırın çevirisinin süreye yetişemeyeceğini gösteriyorsa (zayıf makine, ağır model, hızlı konuşma)
        satır hiç bekletilmez: hızlı çeviri hemen gösterilir, asıl çeviri gelince üstüne yazılır (iki modda da:
        yetişmeyeceği belli olan çeviri için boş ekranda beklenmez). Pencereyi az farkla aşacaksa pencere o kadar
        uzatılır (önce hızlı çevirinin görünüp hemen değişmesi yerine).

        Süreler satırın okunduğu karenin anından sayılır; lag: o andan bu yana geçen süre (OCR, hızlı çeviri). Asıl
        çevirinin geleceği an bir payla tahmin edilir (ARRIVE_MARGIN): tahmin süreye ancak sığıyorsa da beklenmez,
        yoksa asıl çeviri sürenin hemen dışına düşer, hızlı çeviri bir an görünüp değişir."""
        tc = cx.tc
        if cx.failing:  # asıl motor art arda hata veriyor (ör. Ollama kapandı): boş ekranda beklenmez
            return 0.0
        expected = cx.expected_s(len(self._remote_q))
        arrive = None if expected is None else lag + expected * ARRIVE_MARGIN + ARRIVE_PAD_S
        if not tc.hybrid:
            limit = max(1.0, float(tc.timeout_s))
            return 0.0 if arrive is not None and arrive > limit else limit
        window = max(0, tc.hybrid_wait_ms) / 1000.0
        if window <= 0:
            return 0.0
        if arrive is None or arrive <= window:
            return window
        if arrive <= window * WAIT_GIVE_UP:
            return arrive
        return 0.0

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
        if quick and len(self._remote_q) and self.clock() - ev["t"] + (cx.service_s or 0.0) > self._stale_s():
            # Yavaş makinede iş birikti: çevirisi geldiğinde satır ekrandan çoktan kalkmış olur, arkasında yeni satırlar
            # bekliyor. Motor onlara kalsın (bu satır hızlı çevirisiyle gösterildi ya da şimdi gösterilir). Motor
            # boştaysa geç de olsa çevrilir: bağlama Türkçesiyle girer, kimseyi bekletmez.
            self._remember(cx, ev, "")
            return [("fallback", ev)]
        cx.job_started()
        t1 = time.perf_counter()
        try:
            out = self._main_translate(cx, ev, cx.cache_key(ev))
        except Exception as e:
            cx.note_service(time.perf_counter() - t1)  # zaman aşımı da sayılır: sonraki satırlar boşuna beklemesin
            cx.fails += 1
            log.warning("Çeviri hatası (%s): %s", cx.main.key, e)
            self._remember(cx, ev, "")
            self.status.emit(f"Yedek yerel çeviri kullanıldı ({e})" if quick else f"Çeviri hatası: {e}")
            return [("fallback", ev)]
        cx.note_service(time.perf_counter() - t1)
        cx.fails = 0
        self._hint_if_slow(cx)
        self._remember(cx, ev, out)
        return [("upgrade", (ev, self._event(ev, out, t1, False, cx.main.label)))]

    def _hint_if_slow(self, cx: TrContext) -> None:
        """Karma mod kapalıyken asıl motor yavaşsa (ağır model, zayıf makine) kullanıcıya oturumda bir kez Hibrit
        mod önerilir: bu ayarda her satır çevirisini beklerken ekran boş kalır."""
        if self._slow_hinted or cx.tc.hybrid:
            return
        service = cx.steady_service_s()
        if service is not None and service > SLOW_HINT_S:
            self._slow_hinted = True
            log.info("Asıl çeviri motoru yavaş (satır başına %.1f sn); Hibrit mod önerildi", service)
            self.notice.emit(AI_SLOW_NOTICE.format(sec=f"{service:.1f}".replace(".", ",")))

    def _stale_s(self) -> float:
        """Satırın okunmasından bu kadar sonra gelecek asıl çeviri artık gösterilmez (satır ekrandan kalkmıştır):
        en uzun kalma süresi (kapalıysa 10 sn)."""
        fl = getattr(self.s, "flow", None)
        limit = float(getattr(fl, "max_show_s", 0) or 0)
        return limit if limit > 0 else 10.0

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
        with cx.hist_lock:
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
        with cx.hist_lock:
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
        metindir; satırda vurgu varsa Türkçe metindeki aralıkları `spans` alanındadır ([[başlangıç, bitiş], …]).
        Okumanın altyazı kutusu biliniyorsa `box` alanındadır (bkz. textproc.subtitle_box, roi_screen_rect)."""
        spans: Spans = []
        if ev.get("spans"):
            text, spans = unmark(text)
        out = {
            "text": text, "original": ev["text"], "speaker": ev["speaker"], "cached": cached, "engine": engine,
            "ocr_ms": ev.get("ocr_ms", 0.0), "tr_ms": (time.perf_counter() - t1) * 1000, "line": ev["line"],
        }
        if spans:
            out["spans"] = [[s, e] for s, e in spans]
        if ev.get("box"):
            out["box"] = ev["box"]
        return out
