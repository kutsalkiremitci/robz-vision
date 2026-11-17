"""Altyazının var olup olmadığını ve değişip değişmediğini ucuzca bulur: OCR yalnızca gerektiğinde çalışır.

Yöntem:
* Bölge ~640 px genişliğe küçültülür.
* *Metin maskesi*: rengi ne olursa olsun, çevresinden belirgin koyu bir kenarla (dış çizgi, gölge ya da koyu
  zemin) ayrılan parlak parçalar (bkz. outlined_text_mask). Asfalt kumlanması, yol çizgisi, gökyüzü, far gibi
  çevresi orta tonlu ya da büyük parlak şeyler elenir; beyaz, sarı, mavi, kırmızı… her renk yazı kalır.
* Maske doluluk oranı ile metnin var/yok olduğu (histerezisli) belirlenir.
* Maske, en son OCR yapılan maskeden belirgin farklıysa ve birkaç kare boyunca
  sabit kaldıysa OCR tetiklenir (daktilo efekti / yavaş beliren yazılar için).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

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


def text_mask(bgr: np.ndarray, bright: int = 150, width: int = 640) -> np.ndarray:
    """Algılama maskesi (True = yazı): rengi ne olursa olsun koyu kenarlı parlak yazı; önizlemedeki yeşil alan."""
    h, w = bgr.shape[:2]
    if w > width:
        bgr = cv2.resize(bgr, (width, max(1, h * width // w)), interpolation=cv2.INTER_AREA)
    # Küçültülmüş karede dış çizgi incelir: 1 px'lik halkaya bakılır (2 px'lik halka çizginin dışına, zemine
    # taşar ve beyazdan sönük renkler kenarsız sayılır). 4 px'ten kısa parçalar harf değil: asfalt/kum taneleri
    # "yazı var" sanılmasın. Çevre farkı koşulu (OCR'da harflere doku yapışmasın diye) algılamada gerekmez.
    m = outlined_text_mask(bgr, min_v=bright, min_height=4, local=0, ring_px=1)
    return cv2.dilate(m, _K3) > 0


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


class ChangeGate:
    """Her kare için update() çağrılır; 'ocr', 'gone' veya None döner."""

    def __init__(self, params: GateParams | None = None):
        self.p = params or GateParams()
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

    def update(self, bgr: np.ndarray, now: float | None = None) -> str | None:
        p = self.p
        now = time.monotonic() if now is None else now
        m = text_mask(bgr, p.bright)
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
