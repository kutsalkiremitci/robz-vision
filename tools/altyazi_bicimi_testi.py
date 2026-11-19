"""Altyazı biçimi testi: "Yalnız altyazıları oku" süzgecini ve vurgulu kelime algılamasını ölçer.

Sahneler kodla üretilir; telifli görsel yoktur, altyazı cümleleri bu proje için özgün yazılmıştır:

* Altyazı GTA tarzındadır: beyaz ya da açık gri dolgu, ince koyu kenar ve gölge (bir kısmı yalnız gölgeli ya da
  kenarsız), yatay ortalı, tek ya da iki satır; bir kısmında pembe / sarı / yeşil / mavi vurgulu kelime var.
  Altyazının ortası bölgenin ortası olmayabilir (bölge simetrik seçilmemiş).
* Aynı karede dikkat dağıtıcılar: araç plakası, tabela, neon, duvar yazısı, grafiti, araç üstü yazı, HUD (para,
  saat, yer adı), tuş ipucu. Eğik, perspektifli, renkli, farklı yazı tipinde.
* Zeminler: gece, gündüz, açık beton, yeşillik, kum, su, neon, çok açık; --arka-plan ile yerel görseller.
* Kareler yayın görüntüsü gibi sıkıştırılır: JPEG, 4:2:0 renk alt örneklemesi, 720p'den büyütme.

Sahneler oturumlara ayrılır: bir oturumda bölge boyu, altyazının ortası, yazı tipi ve boyu aynıdır; süzgeç oturum
boyunca kabul ettiği satırlardan altyazının ortasını ve boyunu öğrenir. Her kare uygulamanın gerçek OCR motoruyla
(RapidOCR, otomatik ön işleme) okunur; her satır, kutusunun en çok örtüştüğü nesneyle etiketlenir. Hiçbir nesneye
denk gelmeyen okuma "gürültü"dür (zemin dokusu).

Ölçülenler:
* altyazı satırı geri çağırma: süzgecin geçirdiği altyazı satırı oranı (atılan gerçek altyazı 0 olmalı),
* dikkat dağıtıcıların atılma oranı (türe göre),
* vurgu: vurgusuz kelimede vurgu (yanlış alarm) ve vurgulu kelimede vurgu yok (kaçırma), kelime ve kare başına;
  karede vurgulu kelime yokken ekranda vurgu çıkması ayrıca sayılır,
* süre: satır başına süzgeç ve vurgu algılama süresi (ms).

--gorsel ile verilen görsellerde (ör. oyundan altyazı kırpıntıları) okunan her satır altyazı sayılır: süzgecin
hepsini geçirmesi beklenir; satırların kelime renkleri de yazılır. Görseller ayrıca JPEG ve 4:2:0 sıkıştırmasıyla
da denenir.

Kullanım:
  python tools/altyazi_bicimi_testi.py                         # 240 sahne (20 oturum), rapor ekrana
  python tools/altyazi_bicimi_testi.py --sahne 600 --tohum 7 --json sonuc.json
  python tools/altyazi_bicimi_testi.py --suzgec kapali          # süzgeçsiz (her satır geçer): karşılaştırma için
  python tools/altyazi_bicimi_testi.py --arka-plan KLASOR       # zemin olarak yerel görsellerden kesitler
  python tools/altyazi_bicimi_testi.py --kayit ornekler --ornek 12   # ilk 12 sahneyi kutularıyla PNG kaydeder
  python tools/altyazi_bicimi_testi.py --gorsel 5.png --gorsel 6.png --sahne 0

Yazı tipleri Windows'un yazı tipi klasöründen Pillow ile çizilir; Pillow ya da yazı tipi yoksa OpenCV'nin yerleşik
yazı tipleri kullanılır. İlerleme stderr'e, rapor --out dosyasına (verilmezse stdout'a) yazılır.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from altyazitr.ocr import OcrLine  # noqa: E402  (ROOT yola eklendikten sonra)
from altyazitr.textproc import assemble, highlight_spans, line_colors  # noqa: E402

BICIM = 1  # --json çıktısının biçim sürümü
OTURUM_SAHNE = 12  # bir oturumdaki sahne sayısı
DIKKAT_TURLERI = ("plaka", "tabela", "neon", "duvar", "grafiti", "arac", "hud", "ipucu")
TUR_ADI = {"altyazi": "altyazı", "plaka": "plaka", "tabela": "tabela", "neon": "neon", "duvar": "duvar yazısı",
           "grafiti": "grafiti", "arac": "araç yazısı", "hud": "HUD", "ipucu": "tuş ipucu", "gurultu": "gürültü"}
ZEMINLER = ("gece", "gunduz", "beton", "yesil", "kum", "su", "neon", "acik")
SIKISTIRMALAR = ("temiz", "jpeg", "yuv420", "yayin")

# --- özgün metinler -------------------------------------------------------------------------------------------------
# Köşeli ayraç içi vurgulu (oyunun renkli yazdığı yer, kişi, eşya adı). Hepsi bu test için yazıldı.
KONUSMALAR = (
    "We need to get out of here before the cops show up.", "You said this would be easy. This is not easy.",
    "Keep the engine running, I'll be right back.", "Nobody told me the boat would be this small.",
    "If he calls again, tell him we already left town.", "I'm not going back to that place, not after last time.",
    "Turn left at the next light and stay close behind them.", "Relax, nobody is following us. I checked twice.",
    "That's the third time this week your cousin owes me money.", "Give me the keys and stop asking questions.",
    "Look at this traffic. We're never getting there on time.", "You think they'll notice the van is missing?",
    "Stay low and wait for my signal.", "I told you, the guy at the counter knows my face.",
    "Come on, the sun's going down and we still have work to do.", "Are you sure this is the right address?",
    "Don't touch anything. Just grab the bag and walk.", "We split it fifty-fifty, like we agreed.",
    "Hey! Watch the paint, I just got this thing fixed.", "Maybe next time you listen to me first.",
    "Hold on. Something feels wrong about this.", "I can hear sirens. Move, move, move!",
    "Your phone is ringing again. Answer it or throw it.", "We're almost there. Two more blocks.",
    "Who leaves a truck like that unlocked?", "I don't like boats and I don't like any of this.",
    "Okay. Deep breath. We do this clean.", "Tell your brother the deal is off.", "That was way too close.",
    "Fine. But I'm driving.",
)
GOREVLER = (
    "Drive to [the old marina].", "Pick up [Dante] outside [the diner].", "Take the truck to [the car wash].",
    "Lose the cops and return to [the safehouse].", "Meet [Rosa] at [Harbor Street].", "Get back in [the boat].",
    "Go to [the pawn shop] and wait for the call.", "Follow [the white van] without being seen.",
    "Deliver the package to [the motel] on [Bay Road].", "Park the car in [the garage].",
    "Steal [the speedboat] from [the north dock].", "Find a way into [the warehouse].",
    "Wait for [Marco] near [the gas station].", "Bring [the cash] back to [the apartment].",
    "Head to [the airstrip] before sunset.", "Escape [the swamp] in [the airboat].",
    "Drop off [Nina] at [the club].", "Collect [the keys] from [the front desk].",
)
DIKKAT_METINLERI = {
    "tabela": ("LIQUOR", "MOTEL", "OPEN 24 HRS", "CAR WASH", "PAWN SHOP", "DINER", "TACOS", "AUTO PARTS",
               "NO PARKING", "VACANCY", "BAIL BONDS", "PHARMACY", "SURF SHOP", "SALE", "Fresh Seafood"),
    "neon": ("OPEN", "BAR", "MOTEL", "LIVE MUSIC", "COCKTAILS", "VACANCY", "CLUB", "Dance"),
    "duvar": ("KEEP OUT", "STAFF ONLY", "LOADING ZONE", "DANGER", "PRIVATE PROPERTY", "EXIT", "DELIVERIES"),
    "grafiti": ("RAW", "KINGZ", "ZEKO", "FLOW", "DREAM", "SOUTHSIDE", "NOISE"),
    "arac": ("ALVINO V1", "TURBO", "SPORT", "RACING", "GT 500", "SEAWAVE", "CITY TAXI"),
    "hud": ("$12,450", "$980", "11:42 PM", "08:15", "North Beach", "Harbor Point", "+$250", "Wanted"),
    "ipucu": ("Press E to enter", "Hold X to sprint", "Press Q to call", "Press F to open", "Hold R to reload"),
}
# RGB
BEYAZLAR = ((240, 240, 240), (252, 252, 252), (228, 228, 230), (205, 205, 205), (192, 194, 196))
VURGU_RENKLERI = ((216, 128, 155), (236, 141, 192), (240, 200, 80), (110, 200, 110), (93, 182, 229))
VURGU_OLASILIK = (0.4, 0.2, 0.15, 0.1, 0.15)
# BGR
NEON_RENKLERI = ([230, 60, 240], [240, 230, 60], [80, 80, 250], [90, 250, 120], [40, 160, 255])
DUVAR_RENKLERI = ([245, 245, 245], [20, 20, 20], [30, 30, 200], [40, 200, 230], [200, 120, 40])
GRAFITI_RENKLERI = ([240, 240, 240], [60, 200, 250], [200, 60, 220], [60, 220, 60], [40, 40, 230])


# --- yazı çizimi ----------------------------------------------------------------------------------------------------
def _yazi_tipi_klasoru() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


ALTYAZI_YAZI_TIPLERI = ("segoeui.ttf", "arial.ttf", "calibri.ttf", "verdana.ttf", "tahoma.ttf", "bahnschrift.ttf",
                        "corbel.ttf")
DIKKAT_YAZI_TIPLERI = ("arialbd.ttf", "comicbd.ttf", "consolab.ttf", "constanb.ttf", "courbd.ttf", "verdanab.ttf",
                       "Candarab.ttf", "bahnschrift.ttf", "segoeuib.ttf")


class Yazici:
    """Bir yazı tipi ve boyda metni alfa maskesi olarak çizer (0..1). Pillow ya da dosya yoksa OpenCV'nin yazı tipi.

    Maskenin yüksekliği metinden bağımsızdır (yazı tipinin çıkıntı + iniş payı): aynı satırdaki parçalar ve satırlar
    aynı taban çizgisine oturur."""

    def __init__(self, dosya: str | None, boy: int):
        self.boy = boy
        self.font = None
        if dosya:
            try:
                from PIL import ImageFont

                self.font = ImageFont.truetype(str(_yazi_tipi_klasoru() / dosya), boy)
            except Exception:  # Pillow yok ya da yazı tipi yok: OpenCV
                self.font = None
        if self.font is not None:
            asc, desc = self.font.getmetrics()
            self.yukseklik = asc + desc
        else:
            self.olcek = boy / 30.0
            self.kalinlik = max(1, round(boy / 14))
            (_, th), base = cv2.getTextSize("Ag", cv2.FONT_HERSHEY_DUPLEX, self.olcek, self.kalinlik)
            self.taban = th + 2
            self.yukseklik = th + base + 4

    def genislik(self, metin: str) -> float:
        if self.font is not None:
            return float(self.font.getlength(metin))
        return float(cv2.getTextSize(metin, cv2.FONT_HERSHEY_DUPLEX, self.olcek, self.kalinlik)[0][0])

    def maske(self, metin: str, pay: int) -> np.ndarray:
        """Metnin maskesi; solda ve her yanda `pay` piksel boşlukla."""
        w = int(np.ceil(self.genislik(metin))) + 2 * pay
        h = self.yukseklik + 2 * pay
        if self.font is not None:
            from PIL import Image, ImageDraw

            im = Image.new("L", (w, h), 0)
            ImageDraw.Draw(im).text((pay, pay), metin, fill=255, font=self.font)
            return np.asarray(im, np.float32) / 255.0
        m = np.zeros((h, w), np.uint8)
        cv2.putText(m, metin, (pay, pay + self.taban), cv2.FONT_HERSHEY_DUPLEX, self.olcek, 255, self.kalinlik,
                    cv2.LINE_AA)
        return m.astype(np.float32) / 255.0


def _parcali_maske(yz: Yazici, parcalar: Sequence[tuple[str, bool]], pay: int
                   ) -> tuple[list[np.ndarray], list[tuple[str, bool]]]:
    """Yan yana parçalar (metin, vurgulu mu): her parçanın ortak tuvaldeki maskesi ve satırın kelimeleri (kelime,
    vurgulu mu). Kelime, bir harfi vurgulu parçadaysa vurguludur ("marina." → vurgulu)."""
    tam = "".join(t for t, _ in parcalar)
    vurgu = [v for t, v in parcalar for _ in t]
    w = int(np.ceil(yz.genislik(tam))) + 2 * pay
    h = yz.yukseklik + 2 * pay
    maskeler, bas = [], 0
    for metin, _ in parcalar:
        xi = int(round(yz.genislik(tam[:bas])))
        m = yz.maske(metin, pay)
        tuval = np.zeros((h, w), np.float32)
        x0, x1 = max(0, xi), min(w, xi + m.shape[1])
        tuval[:, x0:x1] = np.maximum(tuval[:, x0:x1], m[:, x0 - xi:x1 - xi])
        maskeler.append(tuval)
        bas += len(metin)
    kelimeler = [(k.group(), any(vurgu[i] for i in range(k.start(), k.end()) if tam[i].isalnum()))
                 for k in re.finditer(r"\S+", tam)]
    return maskeler, kelimeler


def _bindir(img: np.ndarray, alfa: np.ndarray, renk_bgr: Sequence[float] | np.ndarray, x: int, y: int) -> None:
    """Alfa maskesini (x, y) konumuna verilen renkle bindirir (img float32, yerinde). Taşan kısım kesilir."""
    H, W = img.shape[:2]
    h, w = alfa.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    a = alfa[y0 - y:y1 - y, x0 - x:x1 - x, None]
    renk = np.asarray(renk_bgr, np.float32)
    if renk.ndim == 3:
        renk = renk[y0 - y:y1 - y, x0 - x:x1 - x]
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - a) + renk * a


def _kenar(alfa: np.ndarray, r: float) -> np.ndarray:
    """Yazının r piksellik dış çizgisi (yumuşak kenarlı)."""
    k = max(1, int(np.ceil(r)))
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    d = cv2.dilate(alfa, el)
    return np.clip(cv2.GaussianBlur(d, (0, 0), 0.6), 0, 1) if r < k else d


def _kaydir(alfa: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return cv2.warpAffine(alfa, np.float32([[1, 0, dx], [0, 1, dy]]), (alfa.shape[1], alfa.shape[0]))


def _bgr(rgb: Sequence[int]) -> tuple[int, int, int]:
    return int(rgb[2]), int(rgb[1]), int(rgb[0])


# --- zeminler -------------------------------------------------------------------------------------------------------
def _gurultu(rng: np.random.Generator, h: int, w: int, olcek: float) -> np.ndarray:
    """Çok ölçekli düzgün gürültü (-1..1)."""
    out = np.zeros((h, w), np.float32)
    agirlik = 0.0
    for k, a in ((olcek, 1.0), (olcek / 3, 0.5), (olcek / 9, 0.25), (2.0, 0.15)):
        sh, sw = max(2, int(h / max(k, 1))), max(2, int(w / max(k, 1)))
        out += a * cv2.resize(rng.standard_normal((sh, sw)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        agirlik += a
    return np.clip(out / agirlik, -1.5, 1.5) / 1.5


def zemin(rng: np.random.Generator, tur: str, h: int, w: int, gorseller: Sequence[np.ndarray] = ()) -> np.ndarray:
    """Altyazı bölgesi boyunda zemin (BGR float32)."""
    if tur == "gorsel" and gorseller:
        g = gorseller[int(rng.integers(len(gorseller)))]
        s = max(w / g.shape[1], h / g.shape[0]) * rng.uniform(1.0, 1.6)
        g = cv2.resize(g, (max(w, int(g.shape[1] * s)), max(h, int(g.shape[0] * s))), interpolation=cv2.INTER_AREA)
        y, x = int(rng.integers(0, g.shape[0] - h + 1)), int(rng.integers(0, g.shape[1] - w + 1))
        return g[y:y + h, x:x + w].astype(np.float32)
    n = _gurultu(rng, h, w, rng.uniform(20, 120))[..., None]
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    if tur == "gece":
        img = 25 + 20 * n + np.zeros((h, w, 3), np.float32)
        for _ in range(int(rng.integers(5, 25))):
            c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
            col = [float(v) for v in rng.integers(40, 255, 3)]
            cv2.circle(img, c, int(rng.integers(2, 30)), col, -1)
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(1, 4))
    elif tur == "gunduz":
        ust = np.array(rng.uniform([170, 150, 120], [250, 220, 200]), np.float32)
        alt = np.array(rng.uniform(60, 160, 3), np.float32)
        img = ust * (1 - yy) + alt * yy + 30 * n
        for _ in range(int(rng.integers(2, 8))):  # binalar, arabalar
            x0, y0 = int(rng.integers(0, w)), int(rng.integers(0, h))
            cv2.rectangle(img, (x0, y0), (x0 + int(rng.integers(20, 300)), y0 + int(rng.integers(10, 120))),
                          [float(v) for v in rng.integers(30, 230, 3)], -1)
        img = cv2.GaussianBlur(img, (0, 0), 1.2) + 12 * _gurultu(rng, h, w, 4)[..., None]
    elif tur == "beton":
        img = rng.uniform(150, 215) + 28 * n + 18 * _gurultu(rng, h, w, 3)[..., None] + np.zeros((h, w, 3), np.float32)
        img *= np.array(rng.uniform(0.92, 1.05, 3), np.float32)
        for _ in range(int(rng.integers(0, 4))):  # çatlak, gölge
            p = [(int(rng.integers(0, w)), int(rng.integers(0, h))) for _ in range(3)]
            cv2.polylines(img, [np.array(p, np.int32)], False, [float(rng.uniform(60, 120))] * 3, 1)
    elif tur == "yesil":
        img = np.array([40, 110, 60], np.float32) * (0.6 + 0.6 * (n + 1) / 2) + 25 * _gurultu(rng, h, w, 3)[..., None]
    elif tur == "kum":
        img = np.array([150, 195, 220], np.float32) + 22 * n + 15 * _gurultu(rng, h, w, 2)[..., None]
    elif tur == "su":
        img = np.array([140, 100, 30], np.float32) * (0.7 + 0.5 * yy) + 30 * n
        isik = (_gurultu(rng, h, w, 3) > 0.55).astype(np.float32)[..., None]
        img = img * (1 - isik) + 245 * isik
    elif tur == "neon":
        img = 20 + 15 * n + np.zeros((h, w, 3), np.float32)
        for _ in range(int(rng.integers(2, 6))):
            col = [float(v) for v in rng.choice([[200, 40, 230], [230, 220, 40], [60, 40, 240], [80, 240, 120]])]
            x0, y0 = int(rng.integers(-50, w)), int(rng.integers(-20, h))
            cv2.rectangle(img, (x0, y0), (x0 + int(rng.integers(60, 400)), y0 + int(rng.integers(20, 150))), col, -1)
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(2, 8))
    else:  # acik: gökyüzü, beyaz duvar
        img = rng.uniform(205, 245) + 12 * n + np.zeros((h, w, 3), np.float32)
        img *= np.array(rng.uniform(0.95, 1.03, 3), np.float32)
    return np.clip(img, 0, 255).astype(np.float32)


def sikistir(img: np.ndarray, tur: str, rng: np.random.Generator) -> np.ndarray:
    """Yayın / uzaktan oynatma görüntüsü gibi sıkıştırma."""
    if tur == "temiz":
        return img

    def yuv420(x: np.ndarray) -> np.ndarray:
        h, w = x.shape[:2]
        x = x[: h - h % 2, : w - w % 2]
        y = cv2.cvtColor(cv2.cvtColor(x, cv2.COLOR_BGR2YUV_I420), cv2.COLOR_YUV2BGR_I420)
        return cv2.copyMakeBorder(y, 0, h % 2, 0, w % 2, cv2.BORDER_REPLICATE)

    def jpeg(x: np.ndarray, q: int) -> np.ndarray:
        return cv2.imdecode(cv2.imencode(".jpg", x, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)

    if tur == "jpeg":
        return jpeg(img, int(rng.integers(40, 86)))
    if tur == "yuv420":
        return yuv420(img)
    h, w = img.shape[:2]  # yayin: 720p akış, 1080p'ye büyütülmüş
    k = cv2.resize(img, (max(2, w * 2 // 3), max(2, h * 2 // 3)), interpolation=cv2.INTER_AREA)
    k = jpeg(yuv420(k), int(rng.integers(50, 75)))
    return cv2.resize(k, (w, h), interpolation=cv2.INTER_LINEAR)


# --- sahne ----------------------------------------------------------------------------------------------------------
@dataclass
class Nesne:
    """Karedeki bir yazı (doğru etiket): türü, metni, kapladığı alan; altyazıda kelimeler ve vurguları."""

    tur: str
    metin: str
    kutu: tuple[int, int, int, int]  # x0, y0, x1, y1
    kelimeler: list[tuple[str, bool]] = field(default_factory=list)


@dataclass
class Oturum:
    """Aynı bölge ve altyazı ayarıyla geçen sahneler."""

    W: int
    H: int
    orta: float  # altyazının yatay ortası (bölgede)
    yazici: Yazici
    stil: str  # "kenar+golge" | "golge" | "kenar" | "kenarsiz"
    beyaz: tuple[int, int, int]


def oturum_kur(rng: np.random.Generator, pil: bool) -> Oturum:
    W = int(rng.choice([960, 1213, 1400, 1600]))
    H = int(rng.choice([140, 180, 220]))
    boy = int(round(H * rng.uniform(0.13, 0.2)))
    kayma = 0.0 if rng.random() < 0.5 else rng.uniform(-0.15, 0.15) * W
    font = str(rng.choice(ALTYAZI_YAZI_TIPLERI)) if pil else None
    # GTA 6: ince koyu kenar + gölge; GTA V: yalnız gölge; kenarsız: yalnız beyaz dolgu (açık zeminde zor durum)
    stil = str(rng.choice(["kenar+golge", "golge", "kenar", "kenarsiz"], p=[0.6, 0.15, 0.15, 0.1]))
    # açık gri dolgu yalnız kenarlı / gölgeli yazıda (kenarsız gri yazı açık zeminde koyu yazı olur)
    beyaz = BEYAZLAR[int(rng.integers(len(BEYAZLAR) if stil != "kenarsiz" else 3))]
    return Oturum(W, H, W / 2 + kayma + rng.uniform(-2, 2), Yazici(font, boy), stil, beyaz)


def _altyazi_satirlari(rng: np.random.Generator, yz: Yazici, en_genis: float) -> list[list[tuple[str, bool]]]:
    """Altyazının satırları: her satır [(parça, vurgulu mu)], parçalar yan yana satırın metnini verir. Görev
    yazısında vurgu var, konuşmada yok."""
    cumle = str(rng.choice(GOREVLER if rng.random() < 0.4 else KONUSMALAR))
    duz, vurgu = "", []
    for p in re.split(r"(\[[^\]]+\])", cumle):
        duz += p.strip("[]")
        vurgu += [p.startswith("[")] * len(p.strip("[]"))
    # satırlara sığdır: sığıyorsa çoğunlukla tek satır, yoksa ortaya yakın bir kelime arasından ikiye
    araliklar = [m.start() for m in re.finditer(" ", duz)]
    if (yz.genislik(duz) <= en_genis and rng.random() < 0.75) or not araliklar:
        kesitler = [(0, len(duz))]
    else:
        k = min(araliklar, key=lambda a: abs(a - len(duz) / 2 + rng.uniform(-6, 6)))
        kesitler = [(0, k), (k + 1, len(duz))]
    out = []
    for a, b in kesitler:
        parcalar: list[tuple[str, bool]] = []
        for i in range(a, b):
            v = vurgu[i] if duz[i] != " " else (parcalar[-1][1] if parcalar else False)
            if parcalar and parcalar[-1][1] == v:
                parcalar[-1] = (parcalar[-1][0] + duz[i], v)
            else:
                parcalar.append((duz[i], v))
        out.append(parcalar)
    return out


def altyazi_bas(img: np.ndarray, rng: np.random.Generator, ot: Oturum) -> list[Nesne]:
    """Altyazıyı bölgenin altına, oturumun ortasına basar; satır başına bir nesne döndürür."""
    yz = ot.yazici
    satirlar = _altyazi_satirlari(rng, yz, 0.85 * ot.W)
    vurgu = VURGU_RENKLERI[int(rng.choice(len(VURGU_RENKLERI), p=VURGU_OLASILIK))]
    satir_h = yz.yukseklik * 1.15
    alt = ot.H - rng.uniform(0.04, 0.2) * ot.H
    ust = alt - satir_h * len(satirlar)
    pay = max(4, yz.boy // 3)
    saydam = 1.0 if rng.random() < 0.85 else rng.uniform(0.75, 1.0)  # belirme anı
    nesneler = []
    for i, satir in enumerate(satirlar):
        renkler = [vurgu if v else ot.beyaz for _, v in satir]
        maskeler, kelimeler = _parcali_maske(yz, satir, pay)
        birlesik = np.max(maskeler, axis=0)
        genis = yz.genislik("".join(p for p, _ in satir))
        x = int(round(ot.orta - genis / 2)) - pay
        y = int(round(ust + i * satir_h)) - pay
        if ot.stil in ("kenar+golge", "golge"):
            g = cv2.GaussianBlur(_kaydir(birlesik, 2, 2), (0, 0), 1.0)
            _bindir(img, g * rng.uniform(0.5, 0.85) * saydam, (0, 0, 0), x, y)
        if ot.stil in ("kenar+golge", "kenar"):
            r = max(1.0, yz.boy / 28) * rng.uniform(0.8, 1.3)
            _bindir(img, _kenar(birlesik, r) * rng.uniform(0.7, 0.95) * saydam, (12, 12, 12), x, y)
        for m, renk in zip(maskeler, renkler):
            _bindir(img, m * saydam, _bgr(renk), x, y)
        ys, xs = np.nonzero(birlesik > 0.3)
        kutu = (x + int(xs.min()), y + int(ys.min()), x + int(xs.max()) + 1, y + int(ys.max()) + 1)
        nesneler.append(Nesne("altyazi", " ".join(k for k, _ in kelimeler), kutu, kelimeler))
    return nesneler


def _plaka_metni(rng: np.random.Generator) -> str:
    harf = "ABCDEFGHJKLMNPRSTUVWXYZ"
    r = lambda n, k: "".join(str(rng.choice(list(k))) for _ in range(n))  # noqa: E731
    return str(rng.choice([f"{r(3, harf)} {r(4, '0123456789')}", f"{r(1, '123456789')}{r(3, harf)}{r(3, '0123456789')}",
                           f"{r(3, '0123456789')} {r(3, harf)}"]))


def _perspektif(maskeler: Sequence[np.ndarray], rng: np.random.Generator, aci: float, egim: float
                ) -> list[np.ndarray]:
    """Aynı boydaki maskeleri birlikte döndürür ve perspektif verir (aci derece, egim sağ kenarın kaymasının boya
    oranı)."""
    h, w = maskeler[0].shape[:2]
    pay = int(0.3 * h + abs(np.sin(np.radians(aci))) * w / 2) + 4
    H, W = h + 2 * pay, w + 2 * pay
    src = np.float32([[pay, pay], [pay + w, pay], [pay + w, pay + h], [pay, pay + h]])
    dst = src.copy()
    d = egim * h
    dst[1, 1] += rng.uniform(-d, d)
    dst[2, 1] += rng.uniform(-d, d)
    R = np.vstack([cv2.getRotationMatrix2D((W / 2, H / 2), aci, 1.0), [0, 0, 1]])
    M = R @ cv2.getPerspectiveTransform(src, dst)
    return [cv2.warpPerspective(cv2.copyMakeBorder(m, pay, pay, pay, pay, cv2.BORDER_CONSTANT, value=0), M, (W, H),
                                flags=cv2.INTER_LINEAR) for m in maskeler]


def dikkat_bas(img: np.ndarray, rng: np.random.Generator, ot: Oturum, tur: str, dolu: list[tuple[int, int, int, int]],
               pil: bool) -> Nesne | None:
    """Bir dikkat dağıtıcı basar (boş bir yere); yer bulunamazsa None."""
    H, W = img.shape[:2]
    boy_alt = ot.yazici.boy
    font = str(rng.choice(DIKKAT_YAZI_TIPLERI)) if pil else None
    if tur == "plaka":
        metin = _plaka_metni(rng)
    else:
        metin = str(rng.choice(DIKKAT_METINLERI[tur]))
    boy = int(boy_alt * {"hud": rng.uniform(0.6, 1.0), "ipucu": rng.uniform(0.7, 1.0), "plaka": rng.uniform(0.7, 1.2)
                         }.get(tur, rng.uniform(0.6, 2.0)))
    if pil and tur in ("hud", "ipucu"):  # oyunun kendi arayüz yazısı: altyazınınkine benzer düz yazı tipi
        font = str(rng.choice(ALTYAZI_YAZI_TIPLERI))
    yz = Yazici(font, max(9, boy))
    pay = max(4, yz.boy // 2)
    m = yz.maske(metin, pay)
    aci, egim = 0.0, 0.0
    if tur in ("plaka", "tabela", "arac"):
        aci, egim = rng.uniform(-6, 6), rng.uniform(0, 0.25)
    elif tur in ("duvar", "neon"):
        aci, egim = rng.uniform(-12, 12), rng.uniform(0, 0.3)
    elif tur == "grafiti":
        aci, egim = rng.uniform(-25, 25), rng.uniform(0, 0.2)
    if rng.random() < 0.3:
        aci = 0.0  # tam yatay olanlar da var
    panel = np.zeros_like(m)
    if tur in ("plaka", "tabela", "ipucu"):
        ph = int(yz.yukseklik * (0.35 if tur != "ipucu" else 0.25))
        cv2.rectangle(panel, (max(0, pay - ph), max(0, pay - ph // 2)),
                      (m.shape[1] - pay + ph, m.shape[0] - pay + ph // 2), 1.0, -1)
    if aci or egim:
        m, panel = _perspektif([m, panel], rng, aci, egim)
    h, w = m.shape
    if w >= W or h >= H:
        return None
    for _ in range(40):
        if tur == "hud":
            sol = rng.integers(0, max(1, int(0.12 * W)))
            x = int(rng.choice([sol, rng.integers(int(0.8 * W), max(int(0.8 * W) + 1, W - w))]))
            y = int(rng.integers(0, H - h))
        elif tur == "ipucu":
            x = int(rng.integers(0, max(1, int(0.15 * W))))
            y = int(rng.integers(0, max(1, H // 3)))
        elif tur == "plaka" and rng.random() < 0.5:  # oyuncunun arabası: ekranın ortasında
            x = int(np.clip(W / 2 - w / 2 + rng.uniform(-0.05, 0.05) * W, 0, W - w))
            y = int(rng.integers(0, H - h))
        else:
            x, y = int(rng.integers(0, W - w)), int(rng.integers(0, H - h))
        kutu = (x, y, x + w, y + h)
        if not any(kutu[0] < d[2] and d[0] < kutu[2] and kutu[1] < d[3] and d[1] < kutu[3] for d in dolu):
            break
    else:
        return None
    if tur == "plaka":
        zemin_renk = [float(v) for v in rng.choice([[235, 235, 235], [200, 230, 240], [240, 225, 200], [80, 210, 240]])]
        _bindir(img, panel, zemin_renk, x, y)
        _bindir(img, m, [float(v) for v in rng.choice([[90, 40, 10], [30, 30, 30], [40, 90, 20], [20, 20, 160]])], x, y)
    elif tur == "tabela":
        p_renk = [float(v) for v in rng.integers(0, 256, 3)]
        y_renk = [255.0 - c if abs(255 - 2 * c) > 60 else (255.0 if c < 128 else 0.0) for c in p_renk]
        if rng.random() < 0.5:
            y_renk = [245.0, 245.0, 245.0] if np.mean(p_renk) < 140 else [20.0, 20.0, 20.0]
        _bindir(img, panel, p_renk, x, y)
        _bindir(img, m, y_renk, x, y)
    elif tur == "ipucu":
        _bindir(img, panel * rng.uniform(0.55, 0.85), (15, 15, 15), x, y)
        _bindir(img, m, (240, 240, 240), x, y)
    elif tur == "neon":
        renk = [float(v) for v in rng.choice(NEON_RENKLERI)]
        _bindir(img, np.clip(cv2.GaussianBlur(m, (0, 0), yz.boy / 6) * 1.5, 0, 1) * 0.8, renk, x, y)
        _bindir(img, m, [min(255.0, c + 60) for c in renk], x, y)
    elif tur == "duvar":
        renk = [float(v) for v in rng.choice(DUVAR_RENKLERI)]
        doku = np.clip(m * (0.75 + 0.35 * _gurultu(rng, h, w, 3)), 0, 1)
        _bindir(img, doku, renk, x, y)
    elif tur == "grafiti":
        renk = [float(v) for v in rng.choice(GRAFITI_RENKLERI)]
        _bindir(img, _kenar(m, max(1.5, yz.boy / 12)), (15, 15, 15), x, y)
        _bindir(img, m, renk, x, y)
    elif tur == "arac":
        govde = [float(v) for v in rng.integers(20, 200, 3)]
        gx0, gy0 = max(0, x - w // 3), max(0, y - h)
        cv2.rectangle(img, (gx0, gy0), (min(W, x + w + w // 3), min(H, y + 2 * h)), govde, -1)
        renk = (240, 240, 240) if rng.random() < 0.6 else (20, 20, 20)
        _bindir(img, m, renk, x, y)
    else:  # hud: beyaz, gölgeli ya da kenarlı (altyazıya benzer)
        _bindir(img, cv2.GaussianBlur(_kaydir(m, 1, 1), (0, 0), 0.8) * 0.8, (0, 0, 0), x, y)
        if rng.random() < 0.5:
            _bindir(img, _kenar(m, 1.0) * 0.8, (10, 10, 10), x, y)
        _bindir(img, m, (240, 240, 240) if rng.random() < 0.8 else (90, 200, 120), x, y)
    ys, xs = np.nonzero(m > 0.3)
    if len(xs) == 0:
        return None
    return Nesne(tur, metin, (x + int(xs.min()), y + int(ys.min()), x + int(xs.max()) + 1, y + int(ys.max()) + 1))


@dataclass
class Sahne:
    oturum: int
    sira: int  # oturumdaki sırası
    zemin: str
    sikistirma: str
    stil: str  # altyazının kenar biçimi (oturumunki)
    kare: np.ndarray
    nesneler: list[Nesne]


def sahneler_uret(adet: int, tohum: int, gorseller: Sequence[np.ndarray] = (), pil: bool | None = None):
    """Sahneleri sırayla üretir (oturum oturum)."""
    if pil is None:
        pil = _pil_var()
    rng = np.random.default_rng(tohum)
    zeminler = ZEMINLER + (("gorsel",) * 3 if gorseller else ())
    ot = None
    for i in range(adet):
        if i % OTURUM_SAHNE == 0:
            ot = oturum_kur(rng, pil)
        zt = str(rng.choice(zeminler))
        img = zemin(rng, zt, ot.H, ot.W, gorseller)
        nesneler: list[Nesne] = []
        if rng.random() < 0.85:
            nesneler += altyazi_bas(img, rng, ot)
        dolu = [(n.kutu[0] - 10, n.kutu[1] - 10, n.kutu[2] + 10, n.kutu[3] + 10) for n in nesneler]
        for _ in range(int(rng.choice([0, 1, 2, 3], p=[0.25, 0.35, 0.28, 0.12]))):
            n = dikkat_bas(img, rng, ot, str(rng.choice(DIKKAT_TURLERI)), dolu, pil)
            if n is not None:
                nesneler.append(n)
                dolu.append((n.kutu[0] - 6, n.kutu[1] - 6, n.kutu[2] + 6, n.kutu[3] + 6))
        st = str(rng.choice(SIKISTIRMALAR, p=[0.15, 0.3, 0.25, 0.3]))
        kare = sikistir(np.clip(img, 0, 255).astype(np.uint8), st, rng)
        yield Sahne(i // OTURUM_SAHNE, i % OTURUM_SAHNE, zt, st, ot.stil, kare, nesneler)


def _pil_var() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return (_yazi_tipi_klasoru() / ALTYAZI_YAZI_TIPLERI[0]).is_file()


# --- etiketleme ve ölçüm --------------------------------------------------------------------------------------------
def etiketle(satir: OcrLine, nesneler: Sequence[Nesne]) -> Nesne | None:
    """Satırın denk geldiği nesne: kutusunun en az %40'ı onunla örtüşen en büyük örtüşme; yoksa None (gürültü)."""
    alan = max(1.0, satir.w * satir.h)
    en, secilen = 0.0, None
    for n in nesneler:
        ix = min(satir.x + satir.w, n.kutu[2]) - max(satir.x, n.kutu[0])
        iy = min(satir.y + satir.h, n.kutu[3]) - max(satir.y, n.kutu[1])
        if ix > 0 and iy > 0 and ix * iy > en:
            en, secilen = ix * iy, n
    return secilen if en >= 0.4 * alan else None


def _anahtar(k: str) -> str:
    return re.sub(r"[\W_]+", "", k.casefold())


def kelime_vurgulari(okunan: Sequence[tuple[str, bool | None]], dogru: Sequence[tuple[str, bool]]
                     ) -> list[tuple[bool, bool | None]]:
    """OCR kelimelerini doğru kelimelerle sırayla eşler: [(doğru vurgu, bulunan renk)]."""
    a = [_anahtar(k) for k, _ in okunan]
    b = [_anahtar(k) for k, _ in dogru]
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            out += [(dogru[j][1], okunan[i][1]) for i, j in zip(range(i1, i2), range(j1, j2))]
    return out


def _yazi_vurgulari(metin: str, spans: Sequence[tuple[int, int]]) -> set[str]:
    return {_anahtar(k) for s, e in spans for k in metin[s:e].split() if _anahtar(k)}


class Sayac:
    """Ölçüm sayaçları (önce / sonra karşılaştırması için JSON'a yazılır)."""

    def __init__(self) -> None:
        self.satir: Counter = Counter()  # (tür, "gecti" | "atildi")
        self.soguk: Counter = Counter()  # oturumun ilk sahnesindeki altyazı satırları
        self.vurgu: Counter = Counter()  # kelime: dogru_pozitif, yanlis_alarm, kacirma, dogru_negatif, belirsiz
        self.kare_vurgu: Counter = Counter()  # kare: vurgusuz kare / yanlış vurgu, vurgulu kare / tam bulunan
        self.atilan_altyazilar: list[str] = []
        self.gecen_dikkat: list[str] = []
        self.sure_suzgec: list[float] = []  # satır başına ms
        self.sure_vurgu: list[float] = []

    def sozluk(self) -> dict[str, Any]:
        return {"satir": {f"{t}|{d}": n for (t, d), n in self.satir.items()},
                "soguk": dict(self.soguk), "vurgu": dict(self.vurgu), "kare_vurgu": dict(self.kare_vurgu),
                "atilan_altyazilar": self.atilan_altyazilar, "gecen_dikkat": self.gecen_dikkat,
                "sure_suzgec_ms": _ozet(self.sure_suzgec), "sure_vurgu_ms": _ozet(self.sure_vurgu)}


def _ozet(x: Sequence[float]) -> dict[str, float]:
    if not x:
        return {}
    s = sorted(x)
    return {"ortalama": round(statistics.fmean(s), 3), "p50": round(s[len(s) // 2], 3),
            "p95": round(s[min(len(s) - 1, int(0.95 * len(s)))], 3), "en_cok": round(s[-1], 3)}


def suzgec_kur(acik: bool):
    """Süzgeç nesnesi (kapalıysa None: her satır geçer)."""
    if not acik:
        return None
    from altyazitr.subtitle_style import SubtitleFilter

    return SubtitleFilter()


def kare_olc(say: Sayac, suzgec, kare: np.ndarray, satirlar: list[OcrLine], nesneler: Sequence[Nesne] | None,
             soguk: bool, etiket: str = "") -> list[OcrLine]:
    """Bir karenin satırlarını süzgeçten geçirir, vurguları bulur ve sayar. nesneler None ise (yerel görsel) okunan
    her satır altyazı sayılır. Geçen satırları döndürür."""
    t = time.perf_counter()
    gecen = suzgec.keep(kare, satirlar) if suzgec is not None else list(satirlar)
    if satirlar:
        say.sure_suzgec.append((time.perf_counter() - t) * 1000 / len(satirlar))
    gecen_id = {id(s) for s in gecen}
    for s in satirlar:
        n = etiketle(s, nesneler) if nesneler is not None else Nesne("altyazi", s.text, (0, 0, 0, 0))
        tur = n.tur if n is not None else "gurultu"
        durum = "gecti" if id(s) in gecen_id else "atildi"
        say.satir[(tur, durum)] += 1
        if tur == "altyazi" and soguk:
            say.soguk[durum] += 1
        if tur == "altyazi" and durum == "atildi":
            say.atilan_altyazilar.append(f"{etiket}: {s.text!r}")
        elif tur not in ("altyazi", "gurultu") and durum == "gecti":
            say.gecen_dikkat.append(f"{etiket}: {TUR_ADI[tur]} {s.text!r}")
        if tur != "altyazi" or durum != "gecti" or nesneler is None:
            continue
        t = time.perf_counter()
        renkler = line_colors(kare, s)
        say.sure_vurgu.append((time.perf_counter() - t) * 1000)
        for dogru, bulunan in kelime_vurgulari(renkler, n.kelimeler):
            if bulunan is None:
                say.vurgu["belirsiz_vurgulu" if dogru else "belirsiz_vurgusuz"] += 1
            elif dogru:
                say.vurgu["dogru_pozitif" if bulunan else "kacirma"] += 1
            else:
                say.vurgu["yanlis_alarm" if bulunan else "dogru_negatif"] += 1
    if nesneler is not None:
        altyazi = [s for s in gecen if (n := etiketle(s, nesneler)) is not None and n.tur == "altyazi"]
        dogru_kume = {_anahtar(k) for n in nesneler if n.tur == "altyazi" for k, v in n.kelimeler if v}
        if altyazi:
            metin = assemble(gecen)[0]
            bulunan = _yazi_vurgulari(metin, highlight_spans(kare, gecen, metin))
            if dogru_kume:
                say.kare_vurgu["vurgulu_kare"] += 1
                say.kare_vurgu["tam_bulunan"] += int(dogru_kume <= bulunan)
                say.kare_vurgu["fazladan_vurgu"] += int(bool(bulunan - dogru_kume))
            else:
                say.kare_vurgu["vurgusuz_kare"] += 1
                say.kare_vurgu["yanlis_vurgulu_kare"] += int(bool(bulunan))
    return gecen


# --- rapor ----------------------------------------------------------------------------------------------------------
def _oran(a: int, b: int) -> str:
    return f"{a}/{b} (%{100 * a / b:.1f})" if b else "-"


def rapor(say: Sayac, baslik: str, sahne_sayisi: int) -> str:
    satir = say.satir
    turler = sorted({t for t, _ in satir}, key=lambda t: (t != "altyazi", t == "gurultu", t))
    out = [f"# {baslik}", "", f"{sahne_sayisi} sahne.", "", "## Satırlar", "",
           "| Tür | Okunan satır | Süzgeçten geçen | Atılan |", "|---|---|---|---|"]
    for t in turler:
        g, a = satir[(t, "gecti")], satir[(t, "atildi")]
        out.append(f"| {TUR_ADI.get(t, t)} | {g + a} | {_oran(g, g + a)} | {_oran(a, g + a)} |")
    dg = sum(n for (t, d), n in satir.items() if t not in ("altyazi", "gurultu") and d == "gecti")
    da = sum(n for (t, d), n in satir.items() if t not in ("altyazi", "gurultu") and d == "atildi")
    ag, aa = satir[("altyazi", "gecti")], satir[("altyazi", "atildi")]
    out += ["", f"- Altyazı satırı geri çağırma: {_oran(ag, ag + aa)}",
            f"- Oturumun ilk sahnesinde (henüz öğrenmeden): {_oran(say.soguk['gecti'], sum(say.soguk.values()))}",
            f"- Dikkat dağıtıcıların atılma oranı: {_oran(da, dg + da)}"]
    v = say.vurgu
    vurgusuz = v["yanlis_alarm"] + v["dogru_negatif"] + v["belirsiz_vurgusuz"]
    vurgulu = v["dogru_pozitif"] + v["kacirma"] + v["belirsiz_vurgulu"]
    k = say.kare_vurgu
    out += ["", "## Vurgu", "",
            f"- Vurgusuz kelimede vurgu (yanlış alarm): {_oran(v['yanlis_alarm'], vurgusuz)}",
            f"- Vurgulu kelimede vurgu yok (kaçırma, belirsiz dahil): "
            f"{_oran(v['kacirma'] + v['belirsiz_vurgulu'], vurgulu)}",
            f"- Vurgusuz karede ekranda vurgu: {_oran(k['yanlis_vurgulu_kare'], k['vurgusuz_kare'])}",
            f"- Vurgulu karede bütün vurgulu kelimeler bulundu: {_oran(k['tam_bulunan'], k['vurgulu_kare'])}; "
            f"fazladan vurgu: {_oran(k['fazladan_vurgu'], k['vurgulu_kare'])}"]
    out += ["", "## Süre (satır başına, ms)", ""]
    for ad, x in (("Süzgeç", say.sure_suzgec), ("Vurgu algılama", say.sure_vurgu)):
        o = _ozet(x)
        out.append(f"- {ad}: " + (f"ortalama {o['ortalama']}, p50 {o['p50']}, p95 {o['p95']}, en çok {o['en_cok']}"
                                  if o else "-"))
    if say.atilan_altyazilar:
        out += ["", "## Atılan altyazı satırları", ""] + [f"- {x}" for x in say.atilan_altyazilar[:60]]
    if say.gecen_dikkat:
        out += ["", "## Süzgeçten geçen dikkat dağıtıcılar", ""] + [f"- {x}" for x in say.gecen_dikkat[:60]]
    return "\n".join(out) + "\n"


# --- çalıştırma -----------------------------------------------------------------------------------------------------
def _gorselleri_oku(klasor: str | None) -> list[np.ndarray]:
    if not klasor:
        return []
    out = []
    for p in sorted(Path(klasor).iterdir()):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.append(img)
    if not out:
        raise SystemExit(f"Klasörde görsel yok: {klasor}")
    return out


def _kaydet(klasor: Path, sahne: Sahne, satirlar: Sequence[OcrLine], gecen: Sequence[OcrLine]) -> None:
    img = sahne.kare.copy()
    for n in sahne.nesneler:
        cv2.rectangle(img, n.kutu[:2], n.kutu[2:], (0, 200, 255) if n.tur == "altyazi" else (255, 160, 0), 1)
    gecen_id = {id(s) for s in gecen}
    for s in satirlar:
        renk = (0, 220, 0) if id(s) in gecen_id else (0, 0, 255)
        cv2.rectangle(img, (int(s.x), int(s.y)), (int(s.x + s.w), int(s.y + s.h)), renk, 2)
    klasor.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", img)[1].tofile(str(klasor / f"sahne_{sahne.oturum:03d}_{sahne.sira:02d}.png"))


def calistir(args: argparse.Namespace) -> int:
    from altyazitr.ocr import RapidOcrEngine
    from altyazitr.pipeline import CV_THREADS

    cv2.setNumThreads(CV_THREADS)  # işlem hattındaki gibi (süre ölçümü uygulamadakiyle aynı koşulda)
    motor = RapidOcrEngine("auto", "auto")
    gorseller = _gorselleri_oku(args.arka_plan)
    say = Sayac()
    suzgec = None
    for i, sahne in enumerate(sahneler_uret(args.sahne, args.tohum, gorseller)):
        if sahne.sira == 0:
            suzgec = suzgec_kur(args.suzgec == "acik")
        satirlar = motor.recognize(sahne.kare)
        etiket = f"oturum {sahne.oturum} sahne {sahne.sira} ({sahne.zemin}, {sahne.sikistirma})"
        gecen = kare_olc(say, suzgec, sahne.kare, satirlar, sahne.nesneler, sahne.sira == 0, etiket)
        if args.kayit and i < args.ornek:
            _kaydet(Path(args.kayit), sahne, satirlar, gecen)
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{args.sahne} sahne", file=sys.stderr, flush=True)
    gsay = Sayac()
    for yol in args.gorsel or ():
        img = cv2.imdecode(np.fromfile(yol, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"Görsel okunamadı: {yol}")
        rng = np.random.default_rng(args.tohum)
        for st in ("temiz", "jpeg", "yuv420", "yayin"):
            kare = sikistir(img, st, rng)
            satirlar = motor.recognize(kare)
            gecen = kare_olc(gsay, suzgec_kur(args.suzgec == "acik"), kare, satirlar, None, True,
                             f"{Path(yol).name} ({st})")
            if st == "temiz":
                for s in gecen:
                    renk = " ".join(f"{k}{'*' if f else '' if f is False else '?'}" for k, f in line_colors(kare, s))
                    print(f"{Path(yol).name}: {renk}", file=sys.stderr)
    metin = rapor(say, f"Altyazı biçimi testi (süzgeç {args.suzgec}, tohum {args.tohum})", args.sahne)
    if args.gorsel:
        metin += "\n" + rapor(gsay, "Verilen görseller (okunan her satır altyazı sayılır)", len(args.gorsel) * 4)
    if args.out:
        Path(args.out).write_text(metin, encoding="utf-8")
    else:
        sys.stdout.write(metin)
    if args.json:
        Path(args.json).write_text(json.dumps({"bicim": BICIM, "suzgec": args.suzgec, "tohum": args.tohum,
                                               "sahne": args.sahne, "sentetik": say.sozluk(),
                                               "gorseller": gsay.sozluk()}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


def _tam_sayi(en_az: int):
    def cevir(metin: str) -> int:
        try:
            n = int(metin)
        except ValueError:
            raise argparse.ArgumentTypeError(f"tam sayı olmalı: {metin}") from None
        if n < en_az:
            raise argparse.ArgumentTypeError(f"en az {en_az} olmalı: {metin}")
        return n
    return cevir


def _argumanlar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python tools/altyazi_bicimi_testi.py", description=__doc__, add_help=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="help", help="bu yardımı gösterir")
    p.add_argument("--sahne", type=_tam_sayi(0), default=240, metavar="N",
                   help="sentetik sahne sayısı (varsayılan 240)")
    p.add_argument("--tohum", type=int, default=1, metavar="N", help="rastgelelik tohumu (aynı tohum, aynı sahneler)")
    p.add_argument("--suzgec", choices=("acik", "kapali"), default="acik",
                   help="'kapali': süzgeçsiz ölçüm, her satır geçer (varsayılan: acik)")
    p.add_argument("--arka-plan", metavar="KLASOR", help="zemin olarak kesit alınacak yerel görseller")
    p.add_argument("--gorsel", action="append", metavar="DOSYA",
                   help="okunan her satırı altyazı sayılan görsel (birden çok kez verilebilir)")
    p.add_argument("--kayit", metavar="KLASOR", help="sahneleri kutularıyla PNG olarak kaydeder (yeşil: geçti)")
    p.add_argument("--ornek", type=_tam_sayi(0), default=20, metavar="N", help="--kayit ile kaydedilecek sahne sayısı")
    p.add_argument("--out", metavar="RAPOR.md", help="Markdown raporun yazılacağı dosya (verilmezse stdout)")
    p.add_argument("--json", metavar="SONUC.json", help="makine tarafından okunur sonucun yazılacağı dosya")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = _argumanlar().parse_args(argv)
    return calistir(args)


if __name__ == "__main__":
    raise SystemExit(main())
