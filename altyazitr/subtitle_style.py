"""Altyazı biçimi süzgeci ("Yalnız altyazıları oku"): OCR'ın okuduğu satırlardan altyazıya benzemeyenleri atar.

Bölgede altyazıdan başka yazı da olur: araç plakası, tabela, duvar yazısı, grafiti, HUD (para, saat, yer adı), tuş
ipucu. Bunlar satır birleştirmeye (textproc.assemble) girerse çeviriye karışır, hatta en alttaki öbek seçildiği için
altyazının yerine geçer. Süzgeç her satırı biçimine ve yerine göre değerlendirir:

* Biçim (bkz. line_style): harflerin dolgusu, iki yanı daha koyu olan ince parlak çizgilerdir. *Koyu kenar*,
  dolgu piksellerinin iki yanının da koyu olduğu orandır (dış çizgi, gölge ya da koyu zemin). *Beyaz*, dolgunun
  renksiz (beyaz / açık gri) oranıdır; zeminden sızan renk (4:2:0 sıkıştırma) renk sayılmaz (bkz. detect.own_color).
  *Eğim*, harflerin dikey ortasının satır boyunca kayması. *Koyu yazı*: açık zeminle çevrili koyu harfler var, koyu
  kenarla çevrili açık dolgu yok (plaka, tabela; ama açık zeminde gri dolgulu, kenarlı altyazı da böyle görünebilir).
* Yer: altyazı yatay ortalıdır, tek ya da iki satırdır, satırların ortası ortak ve boyu aynıdır. Altyazının ortası
  bölgenin ortası olmayabilir (bölge simetrik seçilmemiş): süzgeç kabul ettiği altyazı satırlarından ortayı, satır
  boyunu ve altyazının dikey yerini öğrenir (farklı metinlerin ortancası; aynı yazının tekrar tekrar okunması tek
  sayılır, duran bir tabela öğretemez). Öğrenene kadar bölgenin ortası kullanılır. RapidOCR bir satırı birkaç kutuya
  bölebildiği (vurgulu kelimeler çoğu kez ayrı kutudadır) için aynı hizadaki parçalar birlikte de değerlendirilir.
* Metin: altyazı küçük harfli cümledir; tabela, plaka, duvar yazısı çoğunlukla büyük harfli tek iki kelimedir.

Karar (bkz. _verdict): eğik satır atılır. Biçimi belirgin satır (koyu kenarlı, renksiz) ortalıysa ya da ortanın
çevresindeki şeritte ve boyu altyazınınkine uyuyorsa geçer; şeridin dışındaki (bölgenin kenarında: HUD, tuş ipucu)
atılır. Biçimi zayıf satır (açık zeminde kenarı seçilmeyen yazı) ve koyu yazı ancak küçük harfliyse ve ortalıysa
(boyu da uyuyorsa) ya da şeritte bir cümleyse geçer; orta öğrenilmediyse şeritteki küçük harfli kelimeler yeter.
Renkli satır (tamamı vurgulu altyazı satırı olabilir) ve plakaya benzeyen metin ancak tam altyazı yerindeyse geçer.
Kabul edilen bir altyazı satırının hemen altındaki / üstündeki, ortası ve boyu ortak satır (iki satırlı altyazının
öbür satırı) da geçer.

Ölçüm: tools/altyazi_bicimi_testi.py. Satır başına ~1 ms.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
from rapidfuzz import fuzz

from altyazitr.detect import own_color, ridge_sides, vmax
from altyazitr.english import english_score

if TYPE_CHECKING:
    from altyazitr.ocr import OcrLine

log = logging.getLogger(__name__)

LINE_PX = 28  # satır boyu bundan büyükse ölçüm bu boya küçültülmüş kesitte yapılır (süre sınırlı kalsın)
RIDGE = 40  # dolgu pikseli: iki yanındaki en koyu değerden en az bu kadar parlak (V, 0-255)
DARK_SIDE = 100  # dolgunun iki yanı bundan koyuysa koyu kenar sayılır
STRONG_EDGE = 0.5  # dolgunun en az bu kadarının iki yanı koyuysa biçim belirgin
COLOR_CHROMA = 18.0  # dolgu pikselinin kendi rengi (zeminden sızan çıkarılınca, Cr-Cb) bundan güçlüyse renkli
WHITE_MIN = 0.35  # dolgunun renksiz oranı bundan azsa satır renkli (tabela, neon, grafiti; vurgu kelimeler hariç)
DARK_HOLES = 0.08  # koyu yazı: açıkla çevrili koyu parçalar kutunun en az bu kadarı...
LIGHT_HOLES = 0.04  # ...koyuyla çevrili açık parçalar en çok bu kadarı
# Altyazı yataydır (derece). Kenarı seçilmeyen yazıda harf pikselleri seyrek ve gürültülü: eğim kabası ölçülür.
MAX_TILT = 3.0
MAX_TILT_WEAK = 6.0
# Biçimi belirgin satırın ortası altyazının ortasından en çok bölge genişliğinin bu katı uzak olabilir (ya da satır
# ortanın üstünden geçmeli). Orta henüz öğrenilmediyse geniş: altyazının ortası bölgenin ortası olmayabilir.
BAND = 0.15
BAND_COLD = 0.3
CENTER_TOL = 0.025  # ortalı: ortalar arası en çok bölge genişliğinin bu katı + satır boyunun yarısı
HEIGHT_TOL = 1.5  # boy uyumu: öğrenilen satır boyunun en çok bu katı (ya da 1 / bu kat)
HISTORY = 16  # öğrenmede tutulan son farklı altyazı satırı
TRUST_MIN = 3  # en az bu kadar satır aynı ortada buluşunca öğrenilen orta kullanılır
# Üst üste yazılar (keep(stacked=True); "Oyun altyazısının üzerine" açıkken bölgedeki her öbek ayrı okunur): konuşmanın
# üstündeki görev yazısı daha küçük ve öğrenilen altyazı satırından yukarıdadır. Boy uyumu bu kata kadar gevşer, satırın
# öğrenilen altyazı satırında olması aranmaz; ortalılık ve biçim kuralları aynen geçerlidir.
STACK_HEIGHT_TOL = 2.5


@dataclass
class LineStyle:
    """Bir OCR satırının biçimi (bkz. modül açıklaması)."""

    edge: float  # dolgunun iki yanı koyu olan oranı
    white: float  # dolgunun renksiz oranı
    tilt: float  # derece
    dark: bool  # koyu yazı (açık zeminde koyu harf)

    @property
    def colored(self) -> bool:
        return self.white < WHITE_MIN

    @property
    def tilted(self) -> bool:
        return abs(self.tilt) > (MAX_TILT if self.edge >= STRONG_EDGE else MAX_TILT_WEAK)

    @property
    def strong(self) -> bool:
        return self.edge >= STRONG_EDGE and not self.colored and not self.dark


def _crop(img: np.ndarray, line: OcrLine) -> tuple[np.ndarray, tuple[int, int, int, int], float] | None:
    """Satırın çevresiyle kesiti (gerekirse LINE_PX boyuna küçültülmüş), kesitteki kutusu ve satır boyu."""
    H, W = img.shape[:2]
    x, y, w, h = float(line.x), float(line.y), float(line.w), float(line.h)
    if w < 2 or h < 2:
        return None
    p = int(round(0.35 * h)) + 2
    X0, Y0 = max(0, int(x) - p), max(0, int(y) - p)
    X1, Y1 = min(W, math.ceil(x + w) + p), min(H, math.ceil(y + h) + p)
    if X1 - X0 < 3 or Y1 - Y0 < 3:
        return None
    crop = img[Y0:Y1, X0:X1]
    s = min(1.0, LINE_PX / h)
    if s < 1.0:
        crop = cv2.resize(crop, (max(3, round(crop.shape[1] * s)), max(3, round(crop.shape[0] * s))),
                          interpolation=cv2.INTER_AREA)
    ch, cw = crop.shape[:2]
    box = (int((x - X0) * s), int((y - Y0) * s), min(cw, math.ceil((x + w - X0) * s)),
           min(ch, math.ceil((y + h - Y0) * s)))
    if box[2] - box[0] < 3 or box[3] - box[1] < 3:
        return None
    return crop, box, h * s


def _holes(mask: np.ndarray, box: np.ndarray, hh: float) -> float:
    """Maskeyle tümüyle çevrili (kesitin kenarına değmeyen), harf boyunda boşlukların kutudaki oranı."""
    n, lab, st, _ = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    hgt = st[:, cv2.CC_STAT_HEIGHT]
    ok = (hgt >= 0.15 * hh) & (hgt <= 1.2 * hh)
    ok[np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))] = False
    ok[0] = False  # 0: maskenin kendisi
    return float((ok[lab] & box).sum() / max(1, box.sum()))


def _tilt(fill: np.ndarray, hh: float) -> float:
    """Eğim (derece): harf piksellerinin dikey ortası, yarım harf boyu genişliğindeki dilimlerde; birbirinden en az
    üç harf boyu uzak dilim çiftlerinin eğimlerinin ortancası (alt / üst uzantılı harfler sonucu bozmasın). Harf boyu
    dilimlerdeki dikey yayılımın ortancasıdır: eğik satırın kutusu harflerden çok daha yüksektir. Sekiz harf boyundan
    kısa satırda ölçülemez ("With Chop?" gibi kısa altyazıda harf biçimleri eğim gibi görünür): 0."""
    ys, xs = np.nonzero(fill)
    if xs.size < 10:
        return 0.0
    x0 = int(xs.min())
    b = (xs - x0) // max(2, int(round(hh / 4)))
    lo, hi = np.full(b.max() + 1, 1 << 30), np.full(b.max() + 1, -1)
    np.minimum.at(lo, b, ys)
    np.maximum.at(hi, b, ys)
    used = hi >= 0
    text_h = max(2.0, float(np.median(hi[used] - lo[used] + 1)))
    if xs.max() - x0 < 8 * text_h:
        return 0.0
    bw = max(2, int(round(text_h / 2)))
    b = (xs - x0) // bw
    cnt = np.bincount(b)
    # dilimin dikey ortası: piksellerinin ortancası (uzantılar ve noktalama kaydırmasın); seyrek dilim (yalnız nokta,
    # virgül) sayılmaz
    order = np.lexsort((ys, b))
    mid = np.cumsum(cnt) - cnt + cnt // 2
    keep = cnt >= max(3.0, 0.3 * float(np.median(cnt[cnt > 0])))
    cy = ys[order][np.minimum(mid, len(ys) - 1)][keep].astype(np.float64)
    cx = (np.flatnonzero(keep) + 0.5) * bw
    i, j = np.triu_indices(len(cx), k=1)
    far = cx[j] - cx[i] >= 3 * text_h
    if not far.any():
        return 0.0
    return float(np.degrees(np.arctan(np.median((cy[j] - cy[i])[far] / (cx[j] - cx[i])[far]))))


def line_style(img: np.ndarray, line: OcrLine) -> LineStyle | None:
    """Satırın biçimi; ölçülemiyorsa (kutu çok küçük) None."""
    cut = _crop(img, line)
    if cut is None:
        return None
    crop, (bx0, by0, bx1, by1), hh = cut
    v = vmax(crop)
    r = max(2, int(round(0.18 * hh)))  # yarım pencere: harf çizgisinin yarısı + kenar
    side = ridge_sides(v, r)
    box = np.zeros(v.shape, bool)
    box[by0:by1, bx0:bx1] = True
    fill = box & (v.astype(np.int16) - side.astype(np.int16) >= RIDGE)
    t, _ = cv2.threshold(v[box].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark_mask = v <= t
    dark = _holes(~dark_mask, box, hh) >= DARK_HOLES and _holes(dark_mask, box, hh) <= LIGHT_HOLES
    if int(fill.sum()) < 10:  # ince parlak çizgi yok (açık zeminde kenarsız yazı ya da koyu yazı): rengi bilinmez
        return LineStyle(0.0, 1.0, 0.0, dark)
    edge = float(np.mean(side[fill] <= DARK_SIDE))
    own = own_color(crop, fill, r)[0]
    white = float(np.mean(np.hypot(own[:, 0], own[:, 1]) < COLOR_CHROMA))
    return LineStyle(edge, white, _tilt(fill, hh), dark)


def _words(text: str) -> int:
    return sum(1 for w in text.split() if sum(c.isalpha() for c in w) >= 2)


def lower_words(text: str) -> bool:
    """Küçük harfli yazı: en az iki kelime ve küçük harf (tabela, plaka, duvar yazısı çoğunlukla büyük harflidir)."""
    return _words(text) >= 2 and any(c.islower() for c in text)


def sentence_like(text: str) -> bool:
    """Cümleye benzer: küçük harfli, en az üç kelime, çoğu bilinen İngilizce kelime."""
    return _words(text) >= 3 and lower_words(text) and english_score(text) >= 0.7


def plate_like(text: str) -> bool:
    """Plakaya / koda benzer: küçük harfsiz, hem harf hem rakam içeren kısa metin ("6ABC123", "LND 4821")."""
    t = "".join(text.split())
    return (0 < len(t) <= 9 and not any(c.islower() for c in t) and any(c.isdigit() for c in t)
            and any(c.isalpha() for c in t))


@dataclass
class _Box:
    """Satırın ya da aynı hizadaki parçalarının kapladığı alan."""

    x0: float
    x1: float
    y0: float
    y1: float
    h: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @staticmethod
    def of(line: OcrLine) -> _Box:
        return _Box(line.x, line.x + line.w, line.y, line.y + line.h, line.h)

    def join(self, other: _Box) -> _Box:
        return _Box(min(self.x0, other.x0), max(self.x1, other.x1), min(self.y0, other.y0), max(self.y1, other.y1),
                    (self.h + other.h) / 2)


def _rows(lines: Sequence[OcrLine], styles: Sequence[LineStyle | None]) -> tuple[list[_Box], list[_Box]]:
    """Her satır için (komşu parçalarıyla öbeği, hizasındaki parçalarla birleşimi). Parçalar aynı hizadadır (dikey
    ortaları satır boyunun %30'undan yakın). Öbekte boylar en çok %35 farklı (kutu boyu içeriğe göre oynar:
    uzantısız kelimeler kısa) ve aradaki boşluk satır boyunun iki katından az; vurgulu (renkli) parça da girer
    (RapidOCR renkli kelimeleri çoğu kez ayrı kutuda okur). Birleşimde boşluk sınırı yok (okunamayan kelimeler
    yüzünden aradaki parça eksik olabilir) ama parçaların biçimi aynı olmalı: ikisi de renksiz, plakaya benzemeyen ve
    kenarı ikisinde de belirgin ya da ikisinde de zayıf. Başka boydaki yazı (tabela, HUD) ikisine de girmez; iki
    yandaki farklı yazılar birleşip ortalı görünmesin."""
    n = len(lines)
    boxes = [_Box.of(ln) for ln in lines]
    near, union = list(boxes), list(boxes)
    order = sorted(range(n), key=lambda i: lines[i].x)
    kind = [None if st is None or st.colored or plate_like(ln.text) else st.edge >= STRONG_EDGE
            for ln, st in zip(lines, styles)]
    for k, a in enumerate(order):
        for b in order[k + 1:]:
            la, lb = lines[a], lines[b]
            hi, lo = max(la.h, lb.h), min(la.h, lb.h)
            if abs((la.y + la.h / 2) - (lb.y + lb.h / 2)) >= 0.3 * hi or hi > HEIGHT_TOL * lo:
                continue
            for boxes_of, ok in ((union, kind[a] is not None and kind[a] == kind[b]),
                                 (near, lb.x - (la.x + la.w) <= 2 * hi and hi <= 1.35 * lo)):
                if ok and boxes_of[a] is not boxes_of[b]:
                    old_a, old_b = boxes_of[a], boxes_of[b]
                    merged = old_a.join(old_b)
                    for i in range(n):
                        if boxes_of[i] is old_a or boxes_of[i] is old_b:
                            boxes_of[i] = merged
    return near, union


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


@dataclass
class _Seen:
    """Kabul edilen, biçimi belirgin bir altyazı satırı (öğrenme için)."""

    key: str
    cx: float
    h: float
    bottom: float


@dataclass
class _Learned:
    cx: float  # altyazının yatay ortası
    h: float  # satır boyu
    bottom: float  # altyazı satırlarının alt kenarı (ortanca)


class SubtitleFilter:
    """Yalnız altyazı biçimindeki satırları geçirir; kabul ettiği altyazılardan ortayı, satır boyunu ve altyazının
    dikey yerini öğrenir. Tek iş parçacığından (OCR) kullanılır. Kare boyu değişince (başka bölge) unutur."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._shape: tuple[int, int] | None = None
        self._seen: deque[_Seen] = deque(maxlen=HISTORY)

    def learned(self) -> _Learned | None:
        """Öğrenilen altyazı yeri; yeterince altyazı görülmediyse ya da ortalar dağınıksa None."""
        if len(self._seen) < TRUST_MIN or self._shape is None:
            return None
        cx = statistics.median(s.cx for s in self._seen)
        h = statistics.median(s.h for s in self._seen)
        near = [s for s in self._seen if abs(s.cx - cx) <= max(0.015 * self._shape[1], 0.5 * h)]
        if len(near) < max(TRUST_MIN, 0.6 * len(self._seen)):
            return None
        return _Learned(float(statistics.median(s.cx for s in near)), float(statistics.median(s.h for s in near)),
                        float(statistics.median(s.bottom for s in near)))

    def keep(self, img, lines: Sequence[OcrLine], stacked: bool = False) -> list[OcrLine]:
        """Altyazı biçimindeki satırlar (sırası korunur). Görüntü yoksa (testlerde sahte kare) hepsi geçer; ölçüm
        hata verirse de (en iyi çaba: süzgeç yüzünden altyazı kaybolmasın). stacked: bölgedeki ayrı yazılar (öbekler)
        ayrı okunuyor; konuşmanın üstündeki daha küçük görev yazısı öğrenilen satır boyu ve yeri yüzünden atılmaz (bkz.
        STACK_HEIGHT_TOL)."""
        if not isinstance(img, np.ndarray) or img.ndim != 3 or not lines:
            return list(lines)
        try:
            return self._keep(img, lines, stacked)
        except Exception:
            log.warning("Altyazı biçimi ölçülemedi; bu karede bütün satırlar okunacak", exc_info=True)
            return list(lines)

    def _keep(self, img: np.ndarray, lines: Sequence[OcrLine], stacked: bool = False) -> list[OcrLine]:
        H, W = img.shape[:2]
        if self._shape != (H, W):
            self.reset()
            self._shape = (H, W)
        styles = [line_style(img, ln) for ln in lines]
        near, union = _rows(lines, styles)
        learned = self.learned()
        verdict = [_verdict(ln, st, (_Box.of(ln), a, b), W, learned, stacked)
                   for ln, st, a, b in zip(lines, styles, near, union)]
        _attach(lines, styles, near, verdict, W)
        for ln, st, box, ok in zip(lines, styles, near, verdict):
            # öğrenme yalnız küçük harfli altyazıdan: büyük harfli tabela ortayı yanlış öğretmesin
            if ok and st is not None and ((st.strong and lower_words(ln.text)) or sentence_like(ln.text)):
                key = _key(ln.text)
                if key and all(fuzz.ratio(s.key, key) < 80 for s in self._seen):  # aynı yazının farklı okuması
                    self._seen.append(_Seen(key, box.cx, box.h, ln.y + ln.h))
        return [ln for ln, ok in zip(lines, verdict) if ok]


def _verdict(line: OcrLine, st: LineStyle | None, boxes: tuple[_Box, _Box, _Box], W: int,
             learned: _Learned | None, stacked: bool = False) -> bool:
    """Tek satırın kararı (bkz. modül açıklaması). boxes: satırın kendisi, komşu parçalarıyla öbeği, hizasındaki
    bütün parçalarla birleşimi. Ortalılık üçünden birinde sağlanırsa yeter: bölünmüş satırın parçası, eksik parçası
    okunamamış olsa da birleşimle ortalıdır. Şerit için birleşime bakılmaz (aynı hizadaki HUD ortadan geçmiş
    sayılmasın). stacked: üst üste ayrı yazılar okunuyor (bkz. SubtitleFilter.keep)."""
    if st is None or st.tilted:
        return False
    own = boxes[0]
    center = W / 2 if learned is None else learned.cx
    centered = any(abs(b.cx - center) <= CENTER_TOL * W + 0.5 * b.h for b in boxes)
    tol = STACK_HEIGHT_TOL if stacked else HEIGHT_TOL
    fits = learned is None or 1 / tol <= own.h / learned.h <= tol
    # altyazının satırı: öğrenilen alt kenardan iki buçuk satır yukarıya kadar (iki satırlı altyazı); üst üste
    # yazılarda her öbek kendi yerindedir
    on_row = stacked or learned is None or (learned.bottom - 2.8 * learned.h <= (own.y0 + own.y1) / 2
                                            <= learned.bottom + learned.h)
    # şerit: öğrenilen ortanın da bölgenin ortasının da çevresi (yanlış öğrenilmiş orta altyazıyı attırmasın). Yalnız
    # satırın kendi kutusuyla: yanındaki altyazıyla birleşen HUD ortadan geçmiş sayılmasın.
    band = BAND_COLD if learned is None else BAND
    in_band = on_row and any(abs(own.cx - c) <= band * W or own.x0 <= c <= own.x1 for c in {center, W / 2})
    if st.colored:  # tamamı vurgulu satır olabilir: ancak tam altyazı yerindeyse (orta öğrenilmediyse kenarlı ya da
        # cümle olmalı)
        return centered and fits and on_row and (learned is not None or st.edge >= STRONG_EDGE
                                                 or sentence_like(line.text))
    if plate_like(line.text):  # plaka koyu ya da renkli yazıdır; kenarlı beyaz "plaka" ancak altyazı yerindeyse
        return st.strong and centered and fits
    if st.strong:  # bölgenin tam ortasındaki de geçer: yanlış öğrenilmiş orta (ör. tabela) altyazıyı attırmasın
        return centered or (in_band and fits) or any(abs(b.cx - W / 2) <= CENTER_TOL * W + 0.5 * b.h for b in boxes)
    # Biçimi zayıf (ya da koyu yazı): küçük harf de istenir; tabela, araç ve duvar yazısı çoğunlukla büyük harflidir
    lower = any(c.islower() for c in line.text)
    if learned is None:  # orta öğrenilmedi (bölge simetrik olmayabilir): şeritteki küçük harfli kelimeler de yeter
        return (centered and lower) or (in_band and lower_words(line.text))
    return (centered and fits and lower) or (in_band and sentence_like(line.text))


def _attach(lines: Sequence[OcrLine], styles: list[LineStyle | None], boxes: list[_Box], verdict: list[bool],
            W: int) -> None:
    """Kabul edilen satırla aynı altyazının parçası: aynı öbekte (yanındaki kutu; satırın okunamayan parçası yüzünden
    öbek ortalı olmayabilir) ya da öbür satırı (hemen üstünde / altında, ortası ortak, boyu aynı). Biçimi zayıf ya da
    tamamı vurgulu (renkli) olsa da geçer. Eğik satır, plakaya benzeyen metin ve biçimi belirgin değilken küçük
    harfsiz yazı geçmez (altyazının hemen altındaki / üstündeki, ortadaki araç plakası ya da tabela)."""
    changed = True
    while changed:
        changed = False
        for i, st in enumerate(styles):
            text = lines[i].text
            if (verdict[i] or st is None or st.tilted or plate_like(text)
                    or not (st.strong or any(c.islower() for c in text))
                    or (st.colored and st.edge < STRONG_EDGE)):  # vurgu da kenarlıdır; neon, renkli tabela değil
                continue
            a = boxes[i]
            for j, ok in enumerate(verdict):
                b = boxes[j]
                if not ok:
                    continue
                if b is a or (max(b.y0 - a.y1, a.y0 - b.y1) <= 1.2 * max(a.h, b.h)
                        and abs(a.cx - b.cx) <= CENTER_TOL * W + 0.5 * max(a.h, b.h)
                        and 1 / HEIGHT_TOL <= a.h / b.h <= HEIGHT_TOL):
                    verdict[i] = changed = True
                    break
