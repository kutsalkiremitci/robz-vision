"""Ucuz altyazı değişim algılama: OCR'ı yalnızca gerektiğinde çalıştırmak için.

Yöntem (LunaTranslator / Translumo / owocr'dan derlenen yaklaşım):
* Bölge gri tona çevrilip ~480 px genişliğe küçültülür.
* "Beyaz top-hat" + parlaklık eşiği ile *metin maskesi* çıkarılır (geniş parlak
  alanlar — gökyüzü, ışık — elenir, ince parlak harf çizgileri kalır).
* Maske doluluk oranı ile metnin var/yok olduğu (histerezisli) belirlenir.
* Maske, en son OCR yapılan maskeden belirgin farklıysa ve birkaç kare boyunca
  sabit kaldıysa OCR tetiklenir (daktilo efekti / yavaş beliren yazılar için).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

# Eliptik çekirdek: büyük parlak alanların (gökyüzü, far, güneş) kavisli kenarlarında
# dikdörtgen çekirdeğin bıraktığı "kıymıkları" bırakmaz (testte boş karede %0 yanlış piksel).
_KT = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
_K3 = np.ones((3, 3), np.uint8)


def text_mask(bgr: np.ndarray, bright: int = 160, width: int = 480) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape[:2]
    if w > width:
        g = cv2.resize(g, (width, max(1, h * width // w)), interpolation=cv2.INTER_AREA)
    th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, _KT)
    m = ((th > 50) & (g > bright)).astype(np.uint8)
    return cv2.dilate(m, _K3).astype(bool)


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
