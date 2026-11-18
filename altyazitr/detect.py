"""Altyazının var olup olmadığını ve değişip değişmediğini ucuzca bulur: OCR yalnızca gerektiğinde çalışır.

Yöntem:
* Bölge ~640 px genişliğe küçültülür.
* *Metin maskesi*: rengi ne olursa olsun, çevresinden belirgin koyu bir kenarla (dış çizgi, gölge ya da koyu
  zemin) ayrılan parlak parçalar (bkz. outlined_text_mask). Asfalt kumlanması, yol çizgisi, gökyüzü, far gibi
  çevresi orta tonlu ya da büyük parlak şeyler elenir; beyaz, sarı, mavi, kırmızı… her renk yazı kalır.
* Maske doluluk oranı ile metnin var/yok olduğu (histerezisli) belirlenir.
* Maske, en son OCR yapılan maskeden belirgin farklıysa ve birkaç kare boyunca
  sabit kaldıysa OCR tetiklenir (daktilo efekti / yavaş beliren yazılar için).

Okunan satırdaki *vurgulu* kelimeler (görev yazısında renkli yazılan yer, kişi ve eşya adları) de burada,
OCR'ın satır ve kelime kutularıyla bulunur (bkz. word_colors).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)

_RINGS = {1: np.ones((3, 3), np.uint8), 2: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))}
_K3 = np.ones((3, 3), np.uint8)


def outlined_text_mask(bgr: np.ndarray, contrast: int = 80, ratio: float = 0.4, min_area: int = 3,
                       min_v: int = 150, min_height: int = 0, local: int = 25, solid: float = 0.12,
                       ring_px: int = 2) -> np.ndarray:
    """Rengi ne olursa olsun, çevresinden belirgin koyu bir kenarla ayrılan parlak yazıyı bulur. 255 = yazı.

    Parlaklık en parlak renk kanalıdır (HSV'deki V): mavi, kırmızı, yeşil yazı da beyaz kadar "parlak" sayılır.
    Aday piksel hem `min_v`'den parlak hem de çevresinin ortalamasından `local` birim parlak olmalı (orta tonlu
    asfalt gibi düz dokular harflere yapışmasın). Her aday parçanın çevresindeki `ring_px` (1-2) piksellik halkaya bakılır:
    halkanın en az `ratio` kadarı parçadan `contrast` birim daha koyuysa (yazının dış çizgisi, gölgesi ya da
    koyu zemin) parça yazıdır. Asfalt kumlanması, yol çizgisi, far gibi parlak şeylerin çevresi orta tonlu
    olduğu için elenir. Yazı ince çizgilerden oluşur: ortalama kalınlığı boyunun `solid` katından fazla olan
    dolu parçalar (lamba, tabela, ışık lekesi) ve ekranın yarısından uzun parçalar atılır. `min_height`ten kısa
    parçalar atılır (algılamada tek tük taneler için). Binlerce parçada da hızlı olsun diye döngüsüz hesaplanır."""
    v = bgr.max(axis=2)  # = HSV V, dönüşümden hızlı
    bright = ((v >= min_v) * 255).astype(np.uint8)
    # Dolu parlak lekeler (lamba, tabela, ışık): ortalama kalınlık boyunun `solid` katından fazla. Çevre farkı
    # koşulu lekenin içini boşaltıp kenarını ince bir çizgiye çevireceği için bu, o koşuldan ÖNCE elenir.
    nb, labb, statsb, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    hb = statsb[:, cv2.CC_STAT_HEIGHT]
    dtb = cv2.distanceTransform(bright, cv2.DIST_L2, 3)
    thickb = np.bincount(labb.ravel(), weights=dtb.ravel().astype(np.float64), minlength=nb) / np.maximum(
        statsb[:, cv2.CC_STAT_AREA], 1)
    solid_b = (hb >= 8) & (thickb > solid * hb)
    solid_b[0] = False
    k = int(np.clip(bgr.shape[0] * 0.25, 9, 61)) | 1
    around = cv2.blur(v, (k, k)).astype(np.int16)
    cand = (((bright > 0) & ~solid_b[labb] & (v.astype(np.int16) >= around + local)) * 255).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    if n <= 1:
        return np.zeros_like(cand)
    area = stats[:, cv2.CC_STAT_AREA]
    vmean = np.bincount(lab.ravel(), weights=v.ravel().astype(np.float64), minlength=n) / np.maximum(area, 1)
    # halka pikseli: parçaya ait değil ama 2 px içinde bir parça var (komşu parçanın etiketi alınır)
    near = cv2.dilate(lab.astype(np.float32), _RINGS[ring_px]).astype(np.int32)
    ring = (cand == 0) & (near > 0)
    rl = near[ring]
    dark = v[ring].astype(np.float64) < vmean[rl] - contrast
    frac = np.bincount(rl, weights=dark, minlength=n) / np.maximum(np.bincount(rl, minlength=n), 1)
    height = stats[:, cv2.CC_STAT_HEIGHT]
    # ortalama kalınlık: parçanın içindeki piksellerin kenara ortalama uzaklığı (harfte ~çizgi/4, dolu lekede ~boy/6)
    dt = cv2.distanceTransform(cand, cv2.DIST_L2, 3)
    thick = np.bincount(lab.ravel(), weights=dt.ravel().astype(np.float64), minlength=n) / np.maximum(area, 1)
    blob = (height >= 8) & (thick > solid * height)
    keep = ((frac >= ratio) & (area >= min_area) & (height <= bgr.shape[0] * 0.6) & (height >= min_height)
            & ~blob)
    keep[0] = False
    if keep.any():
        # Harf boyunu alana göre ağırlıklı medyanla bul (tek tek taneler sayıca çok, alanca az). Küçük parçalar
        # (i noktası, virgül, nokta) yalnızca harflere yakınsa kalır; yazıdan uzak tek tük taneler gider.
        hs, ws = height[keep], area[keep]
        order = np.argsort(hs)
        cum = np.cumsum(ws[order])
        h_text = float(hs[order][np.searchsorted(cum, cum[-1] / 2)])
        big = keep & (height >= 0.4 * h_text)
        r = int(max(2, 0.8 * h_text))
        zone = cv2.dilate(big[lab].astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8))  # dikdörtgen: hızlı
        near = np.bincount(lab.ravel(), weights=zone.ravel(), minlength=n) > 0
        keep &= big | near
    return (keep[lab] * 255).astype(np.uint8)


def shrink(bgr: np.ndarray, width: int = 640) -> np.ndarray:
    """Algılamanın çalıştığı boyut: en fazla `width` piksel genişlik."""
    h, w = bgr.shape[:2]
    if w > width:
        return cv2.resize(bgr, (width, max(1, h * width // w)), interpolation=cv2.INTER_AREA)
    return bgr


def text_mask(bgr: np.ndarray, bright: int = 150, width: int = 640) -> np.ndarray:
    """Hızlı algılama maskesi (True = yazı): rengi ne olursa olsun koyu kenarlı parlak yazı."""
    bgr = shrink(bgr, width)
    # Küçültülmüş karede dış çizgi incelir: 1 px'lik halkaya bakılır (2 px'lik halka çizginin dışına, zemine
    # taşar ve beyazdan sönük renkler kenarsız sayılır). 4 px'ten kısa parçalar harf değil: asfalt/kum taneleri
    # "yazı var" sanılmasın. Çevre farkı koşulu (OCR'da harflere doku yapışmasın diye) algılamada gerekmez.
    m = outlined_text_mask(bgr, min_v=bright, min_height=4, local=0, ring_px=1)
    return cv2.dilate(m, _K3) > 0


class TextDetector:
    """Yapay zekâ metin bulucu: PP-OCRv6 "det" modeli (RapidOCR ile programla birlikte gelir).

    Kuralla değil, harf şeklini tanıyarak yazının yerini bulur: açık zeminde beyaz yazı, gölgesiz yazı, her
    renk. Yalnızca kutuları döndürür, okumaz. Tek bir iş parçacığında (yakalama) oluşturulup kullanılmalıdır."""

    def __init__(self, limit: int = 640):
        from rapidocr import ModelType, OCRVersion, RapidOCR

        from altyazitr.ocr import _rapidocr_model_dir

        self._det = RapidOCR(params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(_rapidocr_model_dir()),
            "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
            "Det.limit_type": "max", "Det.limit_side_len": limit,
        })

    def boxes(self, bgr: np.ndarray) -> list[np.ndarray]:
        r = self._det(bgr, use_det=True, use_cls=False, use_rec=False)
        if r is None or getattr(r, "boxes", None) is None:
            return []
        return [np.asarray(b, np.float32) for b in r.boxes]


def boxed_text_mask(small: np.ndarray, boxes: list[np.ndarray], delta: int = 25) -> np.ndarray:
    """Metin bulucunun kutuları içindeki harf pikselleri (True): çevresinden belirgin parlak ya da koyu olanlar.

    Kutu dışındaki her şey (akan asfalt, gökyüzü, araba) yok sayılır; kutu içinde harfler değişince maske de
    değişir, böylece aynı satır yeni bir altyazıyla değiştiğinde OCR yeniden tetiklenir."""
    h, w = small.shape[:2]
    if not boxes:
        return np.zeros((h, w), bool)
    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [np.round(b).astype(np.int32) for b in boxes], 1)
    v = small.max(axis=2)
    k = int(np.clip(h * 0.25, 9, 61)) | 1
    diff = np.abs(v.astype(np.int16) - cv2.blur(v, (k, k)).astype(np.int16))
    strokes = ((diff >= delta) & (inside > 0)).astype(np.uint8)
    if strokes.sum() < 0.03 * inside.sum():
        # Harfler zeminden ayrılamayacak kadar sönük (çok açık zeminde gölgesiz beyaz yazı): bulucu "burada yazı
        # var" dediyse kutuların kendisi yazı sayılır. Değişim, kutuların yeri/boyu değişince anlaşılır.
        return inside > 0
    return cv2.dilate(strokes, _K3) > 0


# --- vurgulu (renkli) kelimeler ------------------------------------------------------------------------------------
# Oyunlar görev yazılarında yer, kişi ve eşya adlarını renkli yazar (GTA 6'da pembe; eskilerde mavi, sarı, yeşil).
# Ölçüt renkten bağımsızdır: harf piksellerinin doygunluğu (HSV'deki S = (V - en küçük kanal) / V). Beyaz ve gri
# gövde metninde S ~0'dır. Harf pikseli, çevresinin ortalamasından belirgin parlak olandır: kenar çizgisi ve gölge
# koyu olduğu için sayılmaz, geniş parlak zemin (gökyüzü) de çevresinden parlak olmadığı için sayılmaz. Video
# sıkıştırması (4:2:0) zeminin rengini harf kenarlarına taşır; bu yüzden yalnız harflerin parlak özüne bakılır.
HL_STROKE_DELTA = 25  # harf pikseli: çevresinin ortalamasından en az bu kadar parlak (V, 0-255)
HL_MIN_V = 90  # bundan koyu piksel harf sayılmaz
HL_CORE_PCT = 40  # harf piksellerinin parlaklıkça alttaki bu yüzdesi (zeminle karışan kenarlar) atılır
HL_SAT = 0.25  # öz pikseli bu doygunluktan yüksekse renkli (beyaz/gri yazı sıkıştırmayla bile ~0,1'i geçmez)
HL_COLOR_FRAC = 0.6  # öz piksellerinin en az bu kadarı renkliyse kelime renkli (kelimenin büyük çoğunluğu)
HL_PLAIN_FRAC = 0.3  # en çok bu kadarı renkliyse kelime beyaz/gri; arası belirsiz
HL_MIN_PIXELS = 12  # bundan az harf pikseli olan kelime belirsiz


def word_colors(bgr: np.ndarray, box: Sequence[float], ranges: Sequence[tuple[float, float]] | None,
                counts: Sequence[int]) -> list[bool | None]:
    """Satırdaki kelimelerin rengi: True renkli, False beyaz/gri, None belirsiz (harf pikseli az ya da karışık).

    box: satırın kutusu (x, y, w, h), `bgr` görüntüsünün koordinatında. ranges: kelimelerin yatay aralıkları
    [(x0, x1)], soldan sağa; None ise harflerin sütun izdüşümündeki boşluklardan bulunur. counts: kelimelerin harf
    sayıları (aralıklar yokken kullanılır; dönen listenin uzunluğu da budur). Bir satır ~1 ms sürer."""
    n = len(counts)
    x, y, w, h = (float(v) for v in box)
    H, W = bgr.shape[:2]
    if n == 0 or w <= 0 or h <= 0:
        return [None] * n
    pad = int(round(0.35 * h)) + 2  # çevrenin ortalaması için satırın etrafından biraz zemin
    X0, X1 = max(0, int(x) - pad), min(W, math.ceil(x + w) + pad)
    Y0, Y1 = max(0, int(y) - pad), min(H, math.ceil(y + h) + pad)
    if X1 - X0 < 3 or Y1 - Y0 < 3:
        return [None] * n
    crop = bgr[Y0:Y1, X0:X1]
    v = crop.max(axis=2)
    k = int(np.clip(h * 0.9, 9, 61)) | 1
    stroke = (v.astype(np.int16) >= cv2.blur(v, (k, k)).astype(np.int16) + HL_STROKE_DELTA) & (v >= HL_MIN_V)
    stroke[:max(0, int(y) - Y0)] = False  # yalnız satırın kendi yüksekliği: üst/alt satırın harfleri karışmasın
    stroke[max(0, math.ceil(y + h) - Y0):] = False
    if ranges is None or len(ranges) != n:
        ranges = _word_ranges(stroke, counts, X0)
    low = crop.min(axis=2)
    out: list[bool | None] = []
    for a, b in ranges:
        c0 = int(np.clip(math.floor(a) - X0, 0, X1 - X0))
        c1 = int(np.clip(math.ceil(b) - X0, 0, X1 - X0))
        st = stroke[:, c0:c1]
        vw = v[:, c0:c1]
        ink = vw[st]
        if ink.size < HL_MIN_PIXELS:
            out.append(None)
            continue
        core = st & (vw >= np.percentile(ink, HL_CORE_PCT))
        vc = vw[core].astype(np.float32)
        colored = float(np.mean(vc - low[:, c0:c1][core] >= HL_SAT * vc))
        out.append(True if colored >= HL_COLOR_FRAC else False if colored <= HL_PLAIN_FRAC else None)
    return out


def _word_ranges(stroke: np.ndarray, counts: Sequence[int], x0: int) -> list[tuple[float, float]]:
    """Kelime kutusu vermeyen motor için kelimelerin yatay aralıkları (görüntü koordinatında). Harf olmayan sütun
    dizilerinden en genişleri kelime araları sayılır; bunlar harf aralarından belirgin geniş değilse (sıkışık yazı)
    satır, kelimelerin harf sayısına göre bölünür."""
    n = len(counts)
    cols = stroke.any(axis=0)
    ink = np.flatnonzero(cols)
    if ink.size == 0:
        return [(x0, x0)] * n
    left, right = int(ink[0]), int(ink[-1]) + 1
    if n > 1:
        blank = np.concatenate(([0], (~cols[left:right]).astype(np.int8), [0]))
        d = np.diff(blank)
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        widths = ends - starts
        if len(widths) >= n - 1:
            order = np.argsort(widths, kind="stable")
            gaps, rest = np.sort(order[-(n - 1):]), order[:-(n - 1)]
            line_h = max(1, int(stroke.any(axis=1).sum()))
            if widths[gaps].min() >= max(2.0, 0.1 * line_h) and (
                    rest.size == 0 or widths[gaps].min() >= 1.5 * widths[rest].max()):
                edges = [left] + [v for g in gaps for v in (left + starts[g], left + ends[g])] + [right]
                return [(x0 + edges[2 * i], x0 + edges[2 * i + 1]) for i in range(n)]
    unit = (right - left) / max(1, sum(counts) + n - 1)  # kelime arası boşluk bir harf sayılır
    out, pos = [], float(left)
    for c in counts:
        out.append((x0 + pos, x0 + pos + c * unit))
        pos += (c + 1) * unit
    return out


def jaccard_distance(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 1.0
    union = np.count_nonzero(a | b)
    if union == 0:
        return 0.0
    return 1.0 - np.count_nonzero(a & b) / union


@dataclass
class GateParams:
    appear: float = 0.003  # maske doluluğu >= bu ise metin VAR
    vanish: float = 0.0015  # maske doluluğu < bu ise metin YOK (histerezis)
    change: float = 0.20  # son OCR'a göre Jaccard uzaklığı > bu ise değişti
    still: float = 0.15  # ardışık kareler arası < bu ise sabit (hareketli arka plan titremesine tolerans)
    stable_frames: int = 2
    max_wait: float = 1.5  # sürekli değişimde en geç bu kadar sonra yine de oku
    refresh: float = 2.5  # metin dururken sigorta amaçlı yeniden okuma aralığı
    gone_frames: int = 3
    bright: int = 160
    det_interval: float = 0.2  # yapay zekâ metin bulucu en fazla bu aralıkla çalışır (saniyede 5 kez)


class ChangeGate:
    """Her kare için update() çağrılır; 'ocr', 'gone' veya None döner.

    Maske iki katmandır: hızlı kenar maskesi (her kare) + varsa yapay zekâ metin bulucunun kutularındaki harf
    pikselleri (bulucu `det_interval` aralıkla çalışır, aradaki karelerde son kutular kullanılır). Biri yazı
    görürse yazı vardır: kenarı olmayan, açık zemindeki beyaz altyazılar da kaçmaz."""

    def __init__(self, params: GateParams | None = None, detector: TextDetector | None = None):
        self.p = params or GateParams()
        self.detector = detector
        self.reset()

    def reset(self) -> None:
        self.prev: np.ndarray | None = None
        self.sent: np.ndarray | None = None
        self.present = False
        self.still = 0
        self.absent = 0
        self.t_change: float | None = None
        self.t_sent = 0.0
        self.ink = 0.0
        self.announced_gone = True
        self.boxes: list[np.ndarray] = []
        self._t_det = float("-inf")

    def update(self, bgr: np.ndarray, now: float | None = None) -> str | None:
        p = self.p
        now = time.monotonic() if now is None else now
        small = shrink(bgr)
        m = text_mask(small, p.bright)
        if self.detector is not None:
            if now - self._t_det >= p.det_interval:
                self._t_det = now
                try:
                    self.boxes = self.detector.boxes(small)
                except Exception:
                    log.warning("Metin bulucu çalışmadı, yalnız hızlı algılamayla devam", exc_info=True)
                    self.detector, self.boxes = None, []
            if self.boxes:
                m = m | boxed_text_mask(small, self.boxes)
        self.ink = float(m.mean())
        self.present = self.ink >= (p.vanish if self.present else p.appear)
        action: str | None = None

        if not self.present:
            self.absent += 1
            self.still = 0
            self.t_change = None
            if self.absent >= p.gone_frames and not self.announced_gone:
                self.announced_gone = True
                self.sent = None
                action = "gone"
        else:
            self.absent = 0
            if self.prev is not None and jaccard_distance(m, self.prev) < p.still:
                self.still += 1
            else:
                self.still = 0
            changed = self.sent is None or jaccard_distance(m, self.sent) > p.change
            if changed or now - self.t_sent > p.refresh:
                if self.t_change is None:
                    self.t_change = now
                if self.still >= p.stable_frames or now - self.t_change > p.max_wait or not changed:
                    self.sent = m
                    self.t_sent = now
                    self.t_change = None
                    self.announced_gone = False
                    action = "ocr"
        self.prev = m
        return action
