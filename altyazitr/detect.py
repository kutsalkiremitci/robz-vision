"""Altyazının var olup olmadığını ve değişip değişmediğini ucuzca bulur: OCR yalnızca gerektiğinde çalışır.

İki geçit vardır:
* TextGate (yapay zekâ metin bulucu açıkken, varsayılan): yazıyı PP-OCRv6 det modeli bulur; yazı okunduktan sonra
  her karede yalnız kutuların içindeki ucuz bir "harf imzasına" bakılır. Yazı belirdiği karede erken okuma ister,
  bir sonraki karede yazı aynıysa o okuma kullanılır (bkz. TextGate).
* ChangeGate (metin bulucu kapalıyken): kurallı kenar maskesi, aşağıdaki yöntemle.

ChangeGate yöntemi:
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


def vmax(bgr: np.ndarray) -> np.ndarray:
    """En parlak renk kanalı (= HSV'deki V). bgr.max(axis=2)'den ~40 kat hızlı."""
    b, g, r = cv2.split(bgr)
    return cv2.max(cv2.max(b, g), r)


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
    v = vmax(bgr)
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
    """Yapay zekâ metin bulucu: PP-OCRv6 "det" modeli (rapidocr paketiyle programla birlikte gelir).

    Kuralla değil, harf şeklini tanıyarak yazının yerini bulur: açık zeminde beyaz yazı, gölgesiz yazı, her
    renk. Yalnızca kutuları döndürür, okumaz. Tek bir iş parçacığında (yakalama) oluşturulup kullanılmalıdır.
    Her karede çalışabildiği için tek iş parçacığıyla çalışır (640 px'lik şeritte ~15 ms; işlemci zamanı en az)."""

    def __init__(self, limit: int = 640, threads: int = 1):
        from altyazitr.ocr import PpOcr

        self.limit = limit
        try:
            self._pp = PpOcr(threads, rec=False)
            self._det = None
        except Exception:  # paketin iç yapısı değiştiyse: RapidOCR'ın kendi çağrısı
            log.warning("Metin bulucu modeli doğrudan açılamadı; RapidOCR'ın kendi çağrısı kullanılacak", exc_info=True)
            from altyazitr.ocr import _rapidocr

            self._pp = None
            self._det = _rapidocr(threads, det_only=True)

    def boxes(self, bgr: np.ndarray) -> list[np.ndarray]:
        if self._pp is not None:
            return list(self._pp.detect(bgr, self.limit))
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
    v = vmax(small)
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
# Ölçüt renkten bağımsızdır ve harflerin *özüne* bakar: iki yanı daha koyu olan ince parlak çizgiler (kenar çizgisi,
# gölge ya da koyu zemin arasındaki dolgu; bkz. ridge_sides). Renk, YCrCb'nin renk düzlemindeki (Cr, Cb) büyüklüktür:
# beyaz ve gri yazıda ~0. Yayın görüntüsünde renk yarım çözünürlüktedir (4:2:0) ve sıkıştırma bloklarıyla yayılır:
# renkli zeminin (neon, kum, deniz) rengi ince beyaz harfe bulaşır. Bulaşan renk, harfin biraz ötesindeki zeminin
# rengiyle aynı yöndedir ve ondan güçlü olamaz; o pay çıkarılır (bkz. own_color). Kelime ancak özünün çoğu tek bir
# tonda renkliyse ve çevresindeki zemin aynı tonda değilse renklidir. Ölçüm: tools/altyazi_bicimi_testi.py.
HL_STROKE_DELTA = 25  # kelime aralıkları için harf pikseli: çevresinin ortalamasından en az bu kadar parlak (V)
HL_MIN_V = 90  # bundan koyu piksel harf sayılmaz
HL_RIDGE = 40  # harf özü: iki yanındaki en koyu değerden en az bu kadar parlak (V, 0-255)
HL_CORE_PCT = 40  # öz piksellerinin parlaklıkça alttaki bu yüzdesi (zeminle karışan kenarlar) atılır
HL_CHROMA = 16.0  # öz pikselinin kendi rengi (bulaşan çıkarılınca, Cr-Cb büyüklüğü) bundan güçlüyse renkli
HL_COLOR_FRAC = 0.5  # öz piksellerinin en az bu kadarı renkliyse...
HL_HUE_SHARE = 0.7  # ...ve renkli piksellerin en az bu kadarı ortalama tondan en çok HL_HUE_TOL derece uzaksa
HL_HUE_TOL = 40.0  # (tek bir ton) kelime renkli
HL_PLAIN_FRAC = 0.2  # en çok bu kadarı renkliyse kelime beyaz/gri; arası belirsiz
HL_BG_SAME = 0.4  # çevredeki zemin aynı tonda ve gücü kelimeninkinin en az bu katıysa renk zeminden olabilir: belirsiz
HL_MIN_PIXELS = 12  # bundan az öz pikseli olan kelime belirsiz
HL_LINE_PX = 40  # satır boyu bundan büyükse renk bu boya küçültülmüş kesitte ölçülür (4K'da da ~1 ms)


def ridge_sides(v: np.ndarray, r: int) -> np.ndarray:
    """Her pikselin iki yanındaki (r piksel içinde) en koyu değerler: sol-sağ ve üst-alt yönlerinde ikisinden
    parlağı, sonra iki yönden koyusu (uint8). v - ridge_sides(v, r) büyükse piksel ince parlak bir çizginin (harf
    dolgusunun) üstündedir: en az bir yönde iki yanı da koyu. Geniş parlak alan (gökyüzü, tabela) çizgi değildir."""
    kh, kv = np.ones((1, r), np.uint8), np.ones((r, 1), np.uint8)
    return cv2.min(cv2.max(cv2.erode(v, kh, anchor=(r - 1, 0)), cv2.erode(v, kh, anchor=(0, 0))),
                   cv2.max(cv2.erode(v, kv, anchor=(0, r - 1)), cv2.erode(v, kv, anchor=(0, 0))))


def own_color(bgr: np.ndarray, ink: np.ndarray, r: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Yazı piksellerinin (ink'in pikselleri, np.nonzero sırasıyla) rengi (Cr, Cb), her biri (N, 2) float32:
    (kendi rengi, ölçülen rengi, çevredeki zeminin rengi). r: harf çizgisinin yarı kalınlığı (piksel).

    Zeminin rengi, yazının ve hemen bitişiğindeki halkanın (kenar çizgisi: yazının kendi rengi ona da bulaşır)
    dışındaki piksellerin yakın çevredeki ortalamasıdır. Bulaşan renk zeminin yönündedir ve ondan güçlü olamaz:
    ölçülen rengin o yöndeki payı (en çok zeminin rengi kadar) çıkarılınca kendi rengi kalır. Zeminin rengi yarım
    çözünürlükte hesaplanır (yayın görüntüsünün rengi zaten yarım çözünürlüklü)."""
    c = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[..., 1:].astype(np.float32) - 128.0
    k = 2 * max(2, r // 2) + 1
    bg = (cv2.dilate(ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) == 0)
    bg = bg.astype(np.float32)
    h2, w2 = max(1, bg.shape[0] // 2), max(1, bg.shape[1] // 2)
    win = (2 * r + 1, 2 * r + 1)
    csum = cv2.blur(cv2.resize(c * bg[..., None], (w2, h2), interpolation=cv2.INTER_AREA), win)
    wsum = cv2.blur(cv2.resize(bg, (w2, h2), interpolation=cv2.INTER_AREA), win)
    ys, xs = np.nonzero(ink)
    yi, xi = np.minimum(ys // 2, h2 - 1), np.minimum(xs // 2, w2 - 1)
    near = csum[yi, xi] / np.maximum(wsum[yi, xi], 1e-3)[:, None]
    seen = c[ys, xs]
    t = np.clip((seen * near).sum(axis=1) / np.maximum((near * near).sum(axis=1), 1e-3), 0.0, 1.0)
    return seen - near * t[:, None], seen, near


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
    pad = int(round(0.35 * h)) + 2  # çevrenin ortalaması ve zeminin rengi için satırın etrafından biraz zemin
    X0, X1 = max(0, int(x) - pad), min(W, math.ceil(x + w) + pad)
    Y0, Y1 = max(0, int(y) - pad), min(H, math.ceil(y + h) + pad)
    if X1 - X0 < 3 or Y1 - Y0 < 3:
        return [None] * n
    crop = bgr[Y0:Y1, X0:X1]
    s = min(1.0, HL_LINE_PX / h)  # büyük yazı küçültülmüş kesitte ölçülür: süre yazı boyundan bağımsız
    if s < 1.0:
        crop = cv2.resize(crop, (max(3, round(crop.shape[1] * s)), max(3, round(crop.shape[0] * s))),
                          interpolation=cv2.INTER_AREA)
    v = vmax(crop)
    vi = v.astype(np.int16)
    rows = np.zeros(v.shape, bool)  # yalnız satırın kendi yüksekliği: üst/alt satırın harfleri karışmasın
    rows[max(0, int((y - Y0) * s)):max(0, math.ceil((y + h - Y0) * s))] = True
    if ranges is None or len(ranges) != n:
        k = int(np.clip(h * s * 0.9, 9, 61)) | 1
        stroke = (vi >= cv2.blur(v, (k, k)).astype(np.int16) + HL_STROKE_DELTA) & (v >= HL_MIN_V) & rows
        ranges = _word_ranges(stroke, counts, 0)  # kesitin koordinatında
    else:
        ranges = [((a - X0) * s, (b - X0) * s) for a, b in ranges]
    r = max(2, int(round(0.18 * h * s)))
    core_all = rows & (vi - ridge_sides(v, r).astype(np.int16) >= HL_RIDGE) & (v >= HL_MIN_V)
    if core_all.sum() < HL_MIN_PIXELS:
        return [None] * n
    own, seen, near = own_color(crop, core_all, r)
    ys, xs = np.nonzero(core_all)
    vv = v[ys, xs]
    out: list[bool | None] = []
    for a, b in ranges:
        sel = np.flatnonzero((xs >= math.floor(a)) & (xs < math.ceil(b)))
        if sel.size < HL_MIN_PIXELS:
            out.append(None)
            continue
        vs = vv[sel]
        core = sel[vs >= np.partition(vs, sel.size * HL_CORE_PCT // 100)[sel.size * HL_CORE_PCT // 100]]
        out.append(_word_color(own[core], seen[core], near[core]))
    return out


def _word_color(own: np.ndarray, seen: np.ndarray, near: np.ndarray) -> bool | None:
    """Bir kelimenin öz piksellerinden rengi (bkz. word_colors): kendi rengi tek tonda ve zeminden gelmiyorsa True."""
    colored = np.hypot(own[:, 0], own[:, 1]) >= HL_CHROMA
    frac = float(colored.mean())
    if frac <= HL_PLAIN_FRAC:
        return False
    if frac < HL_COLOR_FRAC:
        return None
    ang = np.arctan2(own[colored, 1], own[colored, 0])
    mean = math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean()))
    if float(np.mean(np.abs((np.degrees(ang - mean) + 180.0) % 360.0 - 180.0) <= HL_HUE_TOL)) < HL_HUE_SHARE:
        return None  # karışık tonlar: renkli zeminin parçaları
    s, z = seen.mean(axis=0), near.mean(axis=0)
    ns, nz = float(np.hypot(*s)), float(np.hypot(*z))
    if nz >= HL_BG_SAME * ns and float(s @ z) > 0.85 * ns * nz:
        return None  # çevredeki zemin de aynı tonda: renk ondan bulaşmış olabilir
    return True


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


# --- metin bulucuya dayalı geçit -----------------------------------------------------------------------------------
@dataclass
class TextGateParams:
    width: int = 640  # bulucunun çalıştığı genişlik
    stable_frames: int = 1  # yazı belirdikten / değiştikten sonra bu kadar kare aynı kalınca okunur
    lowest_only: bool = True  # yalnız en alttaki yazı öbeği (assemble'ın keep_lowest_block'u gibi)
    same: float = 0.70  # ardışık iki karede harf imzalarının benzerliği >= bu ise yazı aynı
    drift: float = 0.50  # okunan kareye göre benzerlik < bu ise yazı değişti (yavaş geçişler birikmesin)
    grow: float = 0.45  # kutu kenarı satır boyunun bu katından fazla dışa taştıysa yazı uzuyor (okuma bekler)
    grow_shown: float = 0.6  # okunduktan sonra kutu bu kadar uzadıysa yeniden okunur
    fade: float = 1.12  # ardışık karede imza enerjisi bu kattan fazla arttıysa yazı hâlâ beliriyor (okuma bekler)
    brighter: float = 1.25  # okunduktan sonra enerji bu kata çıktıysa (belirme sürmüş) bir kez daha okunur
    max_wait: float = 1.5  # yazı hiç durulmazsa (uzun daktilo satırı) en geç bu aralıkla ara okuma
    refresh: float = 2.5  # yazı dururken sigorta amaçlı yeniden okuma aralığı
    gone_s: float = 0.25  # yazı bu kadar süre bulunamazsa kayboldu
    det_interval: float = 0.25  # okunan yazı dururken bulucu yine de en az bu aralıkla çalışır
    # Yazının uzadığı (daktilo efekti: eski harfler yerinde ve parlaklığı aynı, kutu büyüyor) görülünce bu kadar süre
    # temkinli olunur (satırın içindeki duraklamalarda yarım okunmasın):
    typing_s: float = 5.0
    typing_wait: float = 0.25  # ...yazı bu kadar süre uzamadan durunca okunur, erken okuma yapılmaz,
    typing_max_wait: float = 4.0  # ...yarım satır gösterilmesin diye ara okuma (max_wait) bu kadar beklenir
    typing_grow: float = 0.2
    # Bulucunun bir kare işleme payı: son çağrısı d sürdüyse sonraki en erken d * (1 / pay - 1) sonra. Hızlı
    # işlemcide her kare (~15 ms), zayıf işlemcide (oyun ve OBS de çalışırken) bir çekirdeğin üçte birinden azı.
    det_budget: float = 0.35


@dataclass
class GateAction:
    kind: str  # "ocr" (oku) | "spec" (erken oku, sonucu tut) | "gone" (yazı kayboldu)
    # "spec": erken okumanın kimliği. "ocr": bu kare o erken okumanın yazısıyla aynı (sonucu kullanılabilir); None ise
    # kare okunmalı.
    spec: int | None = None
    why: str = ""  # "appear" | "stable" | "max_wait" | "fade" | "refresh" (günlük ve ölçüm için)


@dataclass
class _Sig:
    """Bir karedeki yazı: kutular (küçük karede), kutuların dikdörtgenleri, hepsini içeren dikdörtgen ve içindeki ince
    parlak çizgilerin haritası (harf imzası)."""

    boxes: np.ndarray
    rects: np.ndarray
    rect: tuple[int, int, int, int]
    k: int
    hat: np.ndarray
    norm: float


class TextGate:
    """Metin bulucuya dayalı geçit. Her kare için update(); GateAction ya da None döner.

    Durumlar: yok → bekliyor (yazı belirdi ya da değişti) → okundu. Yazı yokken ve beklerken her karede metin bulucu
    çalışır (bkz. TextGateParams.det_budget). Yazı okunduktan sonra her karede yalnız kutuların içindeki harf imzasına
    bakılır (~0,3 ms): ince parlak çizgilerin (top-hat) haritası; akan zeminde aynı yazıda benzerlik ~0,8-1, başka
    yazıda ve yazı kaybolunca ~0,6'nın altı (ölçüm: gerçek klip + sentetik sahneler). Bulucu o sırada da
    `det_interval` aralıkla sigorta olarak çalışır (yanına yeni satır geldi mi, yazı uzadı mı).

    Erken okuma: yazı belirdiği karede "spec" döner (o kare okunabilir); sonraki kare aynıysa "ocr" o okumanın
    kimliğiyle gelir ve onun sonucu kullanılır: OCR süresi, yazının durulmasını beklerken geçer. Yazı hâlâ
    beliriyorsa (enerjisi artıyor) ya da uzuyorsa (kutu büyüyor) erken okuma doğrulanmaz, yarım satır gösterilmez.
    Daktilo efekti görülünce bir süre erken okuma yapılmaz ve yazının uzaması durunca okunur."""

    def __init__(self, detector, params: TextGateParams | None = None):
        self.p = params or TextGateParams()
        self.detector = detector
        self.det_calls = 0
        self.reset()

    def reset(self) -> None:
        self.state = "absent"  # "absent" | "pending" | "shown"
        self.ref: _Sig | None = None  # okunan yazı
        self.last: tuple[np.ndarray, float] | None = None  # önceki karede ref dikdörtgenindeki harita
        self.cand: _Sig | None = None  # beklenen (henüz okunmamış) yazı
        self.stable = 0
        self.t_change = 0.0
        self.t_unstable = float("-inf")
        self.t_read = 0.0
        self.t_det = float("-inf")
        self.det_free_at = 0.0  # bulucunun yeniden çalışabileceği an (perf_counter; bkz. det_budget)
        self.typing_until = float("-inf")
        self.absent_since: float | None = None
        self.announced_gone = True
        self.spec_id = 0
        self.spec_live = False
        self.faded = False
        self.present = False
        self.ink = 0.0  # kutuların kapladığı alan oranı (önizlemedeki "Metin var")
        self.boxes = np.zeros((0, 4, 2), np.float32)
        self.small_shape: tuple[int, int] = (0, 0)

    def update(self, bgr: np.ndarray, now: float | None = None) -> GateAction | None:
        p = self.p
        now = time.monotonic() if now is None else now
        small = shrink(bgr, p.width)
        self.small_shape = small.shape[:2]
        s_prev = s_ref = 0.0
        if self.state == "shown":
            ref = self.ref
            hat, norm = _stroke_map(small, ref.rect, ref.k)
            s_prev, s_ref = _similarity(hat, norm, *self.last), _similarity(hat, norm, ref.hat, ref.norm)
            self.last = (hat, norm)
            if s_prev >= p.same and s_ref >= p.drift and now - self.t_det < p.det_interval:
                if norm > ref.norm * p.brighter and not self.faded:  # okunduktan sonra belirgin parlaklaştı
                    self.faded = True
                    self.ref = _Sig(ref.boxes, ref.rects, ref.rect, ref.k, hat, norm)
                    return self._reread(now, "fade")
                return self._reread(now, "refresh") if now - self.t_read > p.refresh else None
        if time.perf_counter() < self.det_free_at:
            return None  # bulucunun payı doldu (zayıf işlemci): bu kare atlanır
        boxes = self._detect(small, now)
        if len(boxes) == 0:
            return self._no_text(now)
        self.absent_since = None
        cand = _make_sig(small, boxes)
        if self.state == "absent":
            return self._start(cand, now)
        if self.state == "shown":
            steady = s_prev >= p.same and s_ref >= p.drift
            how = _compare(cand.rects, self.ref.rects, p.grow_shown, p.lowest_only)
            if steady and how == "same":
                return self._reread(now, "refresh") if now - self.t_read > p.refresh else None
            if steady and how == "grown" and norm <= self.ref.norm * p.fade:
                self.typing_until = now + p.typing_s  # okunan yazı yerinde, kutu büyüdü: daktilo efekti
            return self._start(cand, now)
        prev = self.cand  # bekliyor: önceki kareyle aynı mı (onun dikdörtgeninde, aynı çekirdekle)
        hat, norm = _stroke_map(small, prev.rect, prev.k)
        same = _similarity(hat, norm, prev.hat, prev.norm) >= p.same
        how = _compare(cand.rects, prev.rects, p.typing_grow if now < self.typing_until else p.grow, p.lowest_only)
        if same and how == "grown" and norm <= prev.norm * p.fade:  # belirirken de kutu büyür: o daktilo değil
            self.typing_until = now + p.typing_s
        if same and how == "same" and norm <= prev.norm * p.fade:
            self.stable += 1
        else:
            self.stable = 0
            self.spec_live = False
            self.t_unstable = now
        self.cand = cand
        return self._maybe_read(now)

    def mask(self) -> np.ndarray:
        """Önizleme için bulunan kutular: küçük kare boyutunda bool maske."""
        m = np.zeros(self.small_shape, np.uint8)
        if len(self.boxes):
            cv2.fillPoly(m, [np.round(b).astype(np.int32) for b in self.boxes], 1)
        return m > 0

    def _detect(self, small: np.ndarray, now: float) -> np.ndarray:
        t = time.perf_counter()
        b = self.detector.boxes(small)
        d = time.perf_counter() - t
        self.det_free_at = t + d + d * (1.0 / max(0.05, self.p.det_budget) - 1.0)
        self.t_det = now
        self.det_calls += 1
        b = np.asarray(b, np.float32).reshape(-1, 4, 2) if len(b) else np.zeros((0, 4, 2), np.float32)
        if self.p.lowest_only:
            b = _lowest_block(b)
        self.boxes = b
        self.present = len(b) > 0
        r = _box_rects(b)
        area = float(((r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])).sum())
        self.ink = min(1.0, area / max(1, small.shape[0] * small.shape[1]))
        return b

    def _no_text(self, now: float) -> GateAction | None:
        if self.absent_since is None:
            self.absent_since = now
        if self.state == "pending":
            self.stable = 0
            self.spec_live = False
        if self.state != "absent" and now - self.absent_since >= self.p.gone_s:
            self.state, self.ref, self.cand, self.last = "absent", None, None, None
            if not self.announced_gone:
                self.announced_gone = True
                return GateAction("gone")
        return None

    def _start(self, cand: _Sig, now: float) -> GateAction | None:
        """Yeni ya da değişen yazı: durulması beklenir; ilk karesi erken okumaya gider."""
        self.state, self.cand, self.stable, self.t_change, self.t_unstable = "pending", cand, 0, now, now
        self.spec_id += 1
        self.spec_live = now >= self.typing_until  # yazı uzuyorsa ilk karesi yarımdır: erken okumaya değmez
        if self.p.stable_frames <= 0:
            return self._maybe_read(now)
        return GateAction("spec", self.spec_id, "appear") if self.spec_live else None

    def _maybe_read(self, now: float) -> GateAction | None:
        p = self.p
        typing = now < self.typing_until and now - self.t_unstable < p.typing_wait
        settled = self.stable >= p.stable_frames and not typing
        if not settled and now - self.t_change <= (p.typing_max_wait if now < self.typing_until else p.max_wait):
            return None
        self.t_read = now
        self.announced_gone = False
        if not settled:  # hâlâ değişiyor (çok hareketli zemin, çok uzun daktilo satırı): ara okuma; durulunca yine
            self.t_change = now
            self.spec_live = False
            return GateAction("ocr", None, "max_wait")
        spec = self.spec_id if self.spec_live else None
        self.state, self.ref, self.last, self.cand = "shown", self.cand, (self.cand.hat, self.cand.norm), None
        self.spec_live = False
        self.faded = False
        return GateAction("ocr", spec, "stable")

    def _reread(self, now: float, why: str) -> GateAction:
        self.t_read = now
        return GateAction("ocr", None, why)


def _box_rects(boxes: np.ndarray) -> np.ndarray:
    """Kutuların eksen hizalı dikdörtgenleri (N, 4): x0, y0, x1, y1."""
    b = np.asarray(boxes, np.float32).reshape(-1, 4, 2)
    return np.concatenate([b.min(axis=1), b.max(axis=1)], axis=1)


def _lowest_block(boxes: np.ndarray) -> np.ndarray:
    """En alttaki yazı öbeği: satırlar arasındaki boşluk ortanca satır boyunun 1,2 katını geçince öbek ayrılır (bkz.
    textproc.assemble). Arkadan geçen tabela, HUD yazısı altyazının değiştiğini sandırmasın."""
    if len(boxes) < 2:
        return boxes
    r = _box_rects(boxes)
    order = np.argsort((r[:, 1] + r[:, 3]) / 2, kind="stable")
    hm = float(np.median(np.maximum(1.0, r[:, 3] - r[:, 1])))
    start, bottom = 0, float(r[order[0], 3])
    for n, i in enumerate(order[1:], 1):
        if r[i, 1] - bottom > 1.2 * hm:  # yeni öbek
            start, bottom = n, float(r[i, 3])
        else:
            bottom = max(bottom, float(r[i, 3]))
    return boxes[np.sort(order[start:])]


def _stroke_map(small: np.ndarray, rect: tuple[int, int, int, int], k: int) -> tuple[np.ndarray, float]:
    """Dikdörtgendeki ince parlak çizgilerin haritası (top-hat) ve normu: harfler kalır, geniş parlak alanlar ve
    yavaş değişen zemin gider."""
    x0, y0, x1, y1 = rect
    h, w = small.shape[:2]
    X0, Y0, X1, Y1 = max(0, x0 - k), max(0, y0 - k), min(w, x1 + k), min(h, y1 + k)
    hat = cv2.morphologyEx(vmax(small[Y0:Y1, X0:X1]), cv2.MORPH_TOPHAT,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    hat = hat[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0].astype(np.float32)
    return hat, float(np.sqrt(float((hat * hat).sum())))


def _make_sig(small: np.ndarray, boxes: np.ndarray) -> _Sig:
    rects = _box_rects(boxes)
    h, w = small.shape[:2]
    x0, y0 = int(max(0, np.floor(rects[:, 0].min()))), int(max(0, np.floor(rects[:, 1].min())))
    x1, y1 = int(min(w, np.ceil(rects[:, 2].max()))), int(min(h, np.ceil(rects[:, 3].max())))
    k = int(np.clip(round(float(np.median(rects[:, 3] - rects[:, 1])) * 0.25), 5, 15)) | 1  # ~harf çizgisi kalınlığı
    rect = (x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))
    hat, norm = _stroke_map(small, rect, k)
    return _Sig(np.asarray(boxes, np.float32), rects, rect, k, hat, norm)


def _similarity(a: np.ndarray, na: float, b: np.ndarray, nb: float) -> float:
    """Kosinüs benzerliği (0..1)."""
    if a.shape != b.shape or na < 1e-3 or nb < 1e-3:
        return 0.0
    return float((a * b).sum()) / (na * nb)


def _compare(new: np.ndarray, old: np.ndarray, frac: float, above_ok: bool) -> str:
    """Yeni kutular eskilerine göre: "same" (yerinde), "grown" (yazı uzadı: satır yana doğru büyüdü ya da alta yeni
    satır eklendi) ya da "changed".

    Bulucunun kutu kenarı kareden kareye birkaç piksel oynar: içe oynama ve satır boyunun `frac` katına (en az 4 px)
    kadar dışa taşma sayılmaz. above_ok: eski kutuların hepsi yerindeyken ÜSTTE beliren ayrı kutular değişiklik
    sayılmaz (yalnız en alttaki öbek okunuyor)."""
    if len(new) < len(old):
        return "changed"
    used: set[int] = set()
    wider = False
    for o in old:
        h = o[3] - o[1]
        tol = max(4.0, frac * h)
        best, best_i = 0.0, -1
        for i, n in enumerate(new):
            ov = min(o[2], n[2]) - max(o[0], n[0])
            if i not in used and ov > best and min(o[3], n[3]) - max(o[1], n[1]) > 0.5 * h:
                best, best_i = ov, i
        if best_i < 0:
            return "changed"
        used.add(best_i)
        n = new[best_i]
        if max(o[1] - n[1], n[3] - o[3]) > tol:
            return "changed"
        wider = wider or max(o[0] - n[0], n[2] - o[2]) > tol
    extra = [n for i, n in enumerate(new) if i not in used]
    if extra:
        top, bottom = float(old[:, 1].min()), float(old[:, 3].max())
        if all(n[1] >= bottom - 0.3 * (n[3] - n[1]) for n in extra):
            return "grown"  # alta yeni satır
        if not (above_ok and all(n[3] <= top + 0.3 * (n[3] - n[1]) for n in extra)):
            return "changed"
    return "grown" if wider else "same"
