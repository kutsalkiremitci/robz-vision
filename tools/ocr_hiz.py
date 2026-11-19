"""OCR hız ve doğruluk testi: yakalama → algılama → OCR yolunu kodla üretilen altyazı sahneleriyle ölçer.

Uygulamanın gerçek işlem hattı çalışır (Pipeline'ın yakalama ve OCR iş parçacıkları, gerçek metin bulucu ve OCR
motoru); yalnız ekran yerine sahte bir pencere yakalayıcı kareleri verir ve çeviri yapılmaz (Ollama, ekran kartı,
internet kullanılmaz; ekran ya da pencere yakalanmaz). Kareler bellekte üretilir: kodla çizilen zeminler (gece
sokağı, kumlu asfalt, açık gökyüzü, beton, yeşillik, iç mekân, tabelalı sokak) kayarak akar, üzerine bu proje için
yazılmış özgün İngilizce altyazılar farklı biçimlerde çizilir (kenarlı, gölgeli, kenarsız, sarı, vurgulu kelimeli,
iki satır, "LUCIA:" konuşmacı adı, daktilo ve belirme efektleri), JPEG ile sıkıştırılır. Telifli görsel kullanılmaz.

Ölçülenler:
* gecikme: altyazının tamamı ekranda göründüğü an → doğru okunan metnin çeviri kuyruğuna girdiği an (ortanca, %95)
* doğruluk: karakter doğruluğu, tam eşleşen satır, kaçan satır, yanlış ya da yarım okunup gönderilen okuma,
  hiçbir altyazıya ait olmayan okuma ("hayalet": tabela, gürültü)
* işlemci: işlem süresinin duvar saatine oranı (%100 = bir çekirdek), sahne sahne; yazısız sahnelerde boşta yük
* OCR çağrıları: sayı ve süre (ortanca, %95)

Kullanım:
  python tools/ocr_hiz.py                            # akıllı algılama, konuşma sahneleri (~95 sn)
  python tools/ocr_hiz.py --mod interval             # sabit aralık algılaması (500 ms)
  python tools/ocr_hiz.py --sahne bos                # yazısız sahneler: boşta işlemci yükü
  python tools/ocr_hiz.py --sahne efekt --ayrinti    # daktilo ve belirme efektleri, olay dökümü
  python tools/ocr_hiz.py --sahne hizli,gece_kenarli # yalnız bu sahneler
  python tools/ocr_hiz.py --zayif 4 --yuk 2          # zayıf makine taklidi: 4 çekirdek, 2 meşgul süreç (oyun + OBS)
  python tools/ocr_hiz.py --set capture.fps=10 --set ocr.stable_frames=2 --json eski_ayar.json
  python tools/ocr_hiz.py --kaynak ../robzvision-1.2.1 --json eski.json   # başka bir kaynak ağacını ölçer
  python tools/ocr_hiz.py --karsilastir eski.json yeni.json

Ayarlar uygulamanın varsayılanlarıdır (Settings); kullanıcının ayar dosyası okunmaz, değiştirilmez. Varsayılan
bölge 1213x179 piksel (1080p ekranda altyazı şeridi), pencere yakalama (kareler tarama hızında teslim edilir).
Süre ölçen başka bir iş (oyun, derleme, başka bir test) aynı anda çalışırken sayılar şişer: ölçümü tekrarlayın.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BOYUT = (1213, 179)
KAYNAK_FPS = 30  # sahnelerin kare hızı (oyun); yakalayıcı bundan tarama hızında kare verir

# --- zeminler ------------------------------------------------------------------------------------------------------
YAZI_TIPLERI = {
    "arialbd": "arialbd.ttf", "arial": "arial.ttf", "seguisb": "seguisb.ttf", "tahomabd": "tahomabd.ttf",
    "trebucbd": "trebucbd.ttf", "verdana": "verdana.ttf",
}
BEYAZ, SARI, PEMBE = (240, 240, 240), (240, 205, 70), (0xEC, 0x8D, 0xC0)  # RGB
UC_NOKTALAMA = ".,!?;:\"'"


def zemin(tur: str, h: int, w: int, tohum: int = 0) -> np.ndarray:
    """Yatay kayan zemin şeridi (BGR), genişlik w (karenin birkaç katı)."""
    rng = np.random.default_rng(tohum)
    if tur == "gece":
        img = np.full((h, w, 3), (38, 30, 26), np.uint8)
        for _ in range(w // 40):
            c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
            cv2.circle(img, c, int(rng.integers(6, 34)), [int(v) for v in rng.integers(40, 255, 3)], -1)
        img = cv2.GaussianBlur(img, (0, 0), 4)
        for _ in range(w // 90):  # keskin küçük ışıklar (pencere, far)
            cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))), int(rng.integers(2, 5)),
                       (235, 240, 250), -1)
        return cv2.GaussianBlur(img, (0, 0), 0.8)
    if tur == "asfalt":
        g = np.clip(rng.normal(125, 22, (h, w)), 0, 255).astype(np.uint8)
        taneler = rng.random(g.shape) < 0.035
        g[taneler] = rng.integers(190, 245, int(taneler.sum())).astype(np.uint8)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        for x in range(60, w, 420):  # sarı yol çizgileri
            cv2.line(img, (x, h - 1), (x + 110, 0), (60, 200, 225), 12)
        return cv2.GaussianBlur(img, (0, 0), 0.6)
    if tur == "gunduz":
        ys = np.linspace(0, 1, h)[:, None, None]
        img = (np.array([235, 200, 150], np.float32) * (1 - ys) + np.array([175, 215, 235], np.float32) * ys)
        img = img.repeat(w, axis=1)
        bulut = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 25)
        bulut = np.clip(bulut / (np.abs(bulut).max() + 1e-6) * 2.2, 0, 1)[..., None]
        img = img * (1 - bulut) + 250 * bulut + rng.normal(0, 3, (h, w, 3))
        return np.clip(img, 0, 255).astype(np.uint8)
    if tur == "beton":
        g = cv2.GaussianBlur(rng.normal(205, 13, (h, w)).astype(np.float32), (0, 0), 1.0)
        return cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    if tur == "yesillik":
        kaba = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 6)
        ince = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2)
        v = np.clip(95 + 260 * kaba + 70 * ince, 15, 235)
        return np.clip(np.stack([v * 0.45, v, v * 0.62], axis=2), 0, 255).astype(np.uint8)
    if tur == "ic":
        xs, ys = np.linspace(0, 6 * math.pi, w)[None, :], np.linspace(0, 1, h)[:, None]
        v = 70 + 35 * np.sin(xs) * (1 - ys) + 20 * ys
        img = np.clip(np.stack([v * 0.7, v * 0.85, v], axis=2) + rng.normal(0, 2.5, (h, w, 3)), 0, 255)
        img = img.astype(np.uint8)
        for x in range(100, w, 500):
            cv2.rectangle(img, (x, int(h * 0.2)), (x + 160, h), (30, 34, 40), -1)
        return cv2.GaussianBlur(img, (0, 0), 1.5)
    if tur == "tabela":  # gece sokağı + üstte akan tabela yazıları (altyazı olmayan yazı)
        from PIL import Image, ImageDraw

        pil = Image.fromarray(cv2.cvtColor(zemin("gece", h, w, tohum + 11), cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        font = _yazi_tipi("arialbd", max(14, int(h * 0.17)))
        kelimeler = ["LIQUOR", "24 HR", "MOTEL", "PAWN", "TACOS", "GAS 3.99", "OPEN", "BAIL BONDS"]
        for i, x in enumerate(range(40, w, 330)):
            d.text((x, int(h * 0.06)), kelimeler[i % len(kelimeler)], font=font, fill=(250, 235, 180))
        return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    raise ValueError(tur)


def _yazi_tipi(ad: str, px: int):
    from PIL import ImageFont

    yol = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / YAZI_TIPLERI[ad]
    try:
        return ImageFont.truetype(str(yol), px)
    except OSError:  # Windows yazı tipi yoksa Pillow'un kendi yazı tipi
        return ImageFont.load_default(px)


# --- altyazı -------------------------------------------------------------------------------------------------------
@dataclass
class Bicim:
    yazi_tipi: str = "arialbd"
    px: int = 33  # 179 piksellik şeritte (1080p) yazı boyu
    renk: tuple[int, int, int] = BEYAZ
    kenar: int = 0  # dış çizgi kalınlığı
    golge: bool = False
    vurgu: tuple[int, int, int] = PEMBE


BICIMLER = {
    "kenarli": Bicim(kenar=2), "golgeli": Bicim(yazi_tipi="seguisb", golge=True), "duz": Bicim(yazi_tipi="arial"),
    "sari": Bicim(yazi_tipi="tahomabd", renk=SARI, kenar=2), "kalin": Bicim(yazi_tipi="trebucbd", kenar=3),
    "ince_kenarli": Bicim(yazi_tipi="verdana", kenar=1, golge=True),
}


def yazi_katmani(satirlar: list[str], w: int, h: int, b: Bicim, vurgulu: tuple[str, ...] = (),
                 goster: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Saydam katman (RGB, alfa 0..1): satırlar ortalı, alta yakın. goster: yalnız ilk N harf (daktilo efekti);
    yerleşim tam metne göre yapılır."""
    from PIL import Image, ImageDraw, ImageFilter

    px = max(8, round(b.px * h / 179))
    font = _yazi_tipi(b.yazi_tipi, px)
    katman = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(katman)
    satir_h = int(px * 1.28)
    y = h - int(0.16 * h) - satir_h * len(satirlar)
    kalan = goster
    for satir in satirlar:
        x = (w - d.textlength(satir, font=font)) / 2
        gorunen = satir if kalan is None else satir[:max(0, kalan)]
        if kalan is not None:
            kalan -= len(satir) + 1
        konum = 0
        for parca in gorunen.split(" "):  # kelime kelime (vurgu rengi için); konum tam satırdan
            if parca:
                renk = b.vurgu if parca.strip(UC_NOKTALAMA) in vurgulu else b.renk
                d.text((x + d.textlength(satir[:konum], font=font), y), parca, font=font, fill=(*renk, 255),
                       stroke_width=b.kenar, stroke_fill=(0, 0, 0, 255))
            konum += len(parca) + 1
        y += satir_h
    arr = np.asarray(katman)
    rgb, a = arr[:, :, :3].copy(), arr[:, :, 3].astype(np.float32) / 255
    if b.golge:
        bulanik = Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(max(1.0, px * 0.05)))
        k = max(1, px // 14)
        golge = np.roll(np.asarray(bulanik).astype(np.float32) / 255, (k, k), axis=(0, 1)) * 0.8
        dis = a + golge * (1 - a)  # gölge yazının altında
        rgb = (rgb * a[..., None] / np.maximum(dis[..., None], 1e-6)).astype(np.uint8)
        a = dis
    return rgb, a


def birlestir(zemin_bgr: np.ndarray, rgb: np.ndarray, a: np.ndarray, opaklik: float = 1.0) -> np.ndarray:
    aa = (a * opaklik)[..., None]
    out = zemin_bgr.astype(np.float32) * (1 - aa) + rgb[:, :, ::-1].astype(np.float32) * aa
    return np.clip(out, 0, 255).astype(np.uint8)


# --- sahneler ------------------------------------------------------------------------------------------------------
@dataclass
class Satir:
    metin: str  # ekrandaki yazı; iki satır "\n" ile
    sure: float
    bosluk: float = 0.5  # ardından yazısız süre (0: sonraki satır hemen yerine geçer)
    vurgulu: tuple[str, ...] = ()
    efekt: str = ""  # "" | "daktilo" | "belirme"
    t_on: float = 0.0  # mutlak anlar (zaman çizelgesinde)
    t_off: float = 0.0
    t_tam: float = 0.0  # metnin tamamının göründüğü an


@dataclass
class Sahne:
    ad: str
    zemin: str
    bicim: str
    satirlar: list[Satir]
    hiz: float = 120.0  # zemin kayma hızı (px/sn)
    bas: float = 1.0  # baştaki yazısız süre
    tohum: int = 0


def sahneler() -> list[Sahne]:
    """Özgün, oyun tarzı İngilizce altyazılar (hiçbir oyun ya da filmden alıntı değil)."""
    S = Satir
    return [
        Sahne("gece_kenarli", "gece", "kenarli", [
            S("LUCIA: I told you not to come back here, Jason.", 2.4, 0.6),
            S("They're watching us. We need to move, now!", 2.0, 0.5),
            S("JASON: Relax, nobody followed me.", 1.6, 0.6),
            S("You always say that, and then somebody shows up.", 2.2, 0.5),
            S("Give me five minutes. I'll take care of it.", 2.0, 0.6)], hiz=90),
        Sahne("asfalt_golgeli", "asfalt", "golgeli", [
            S("Hey bro, you good? That cop's been tailing us\nfor like ten blocks.", 2.4, 0.0),
            S("Take the next right and cut through the alley.", 1.8, 0.0),
            S("I know a guy who can swap the plates tonight.", 1.8, 0.0),
            S("How much is that gonna cost us?", 1.4, 0.0),
            S("Two grand, maybe three. He doesn't ask questions,\nand neither should you.", 2.6, 0.0),
            S("Fine. Just keep driving.", 1.4, 0.5)], hiz=420),
        Sahne("beton_duz", "beton", "duz", [
            S("We need to lose them before the bridge.", 2.0, 0.6),
            S("Come see me when you get the chance, old friend.", 2.2, 0.5),
            S("I never wanted any of this to happen.", 2.0, 0.6),
            S("Stay here and watch the door.", 1.6, 0.6)], hiz=160),
        Sahne("gunduz_golgeli", "gunduz", "golgeli", [
            S("Welcome to Vice City. Try not to get robbed.", 2.2, 0.5),
            S("LUCIA: The boat leaves at nine, with or without you.", 2.4, 0.5),
            S("Did you bring the money?", 1.4, 0.5),
            S("It's all there. Count it if you want.", 1.8, 0.6)], hiz=60),
        Sahne("yesillik_sari", "yesillik", "sari", [
            S("Keep your head down and stay quiet.", 1.8, 0.5),
            S("There are three guards by the gate.", 1.8, 0.4),
            S("Wait for my signal, then go around the back.", 2.2, 0.5),
            S("What signal? You never told me the signal!", 2.0, 0.6)], hiz=200),
        Sahne("ic_gorev", "ic", "ince_kenarli", [
            S("Go to the radar station and wait for my call.", 2.4, 0.5, ("radar", "station")),
            S("Pick up Lucia outside the motel.", 2.0, 0.5, ("Lucia", "motel")),
            S("Lose the cops before you reach the docks.", 2.2, 0.5, ("cops", "docks")),
            S("Steal a fast car and meet Jason\nat the Leonida Keys marina.", 2.6, 0.6, ("Leonida", "Keys", "marina")),
        ], hiz=40),
        Sahne("tabela_kalin", "tabela", "kalin", [
            S("Nobody said this was gonna be easy, alright?", 2.0, 0.5),
            S("JASON: I've seen worse plans. Not many, but a few.", 2.4, 0.5),
            S("Then stop complaining and grab the bag.", 1.8, 0.5),
            S("We split it fifty-fifty, like we agreed.", 2.0, 0.6)], hiz=110),
        Sahne("hizli", "gece", "kenarli", [
            S("Go, go, go!", 0.8, 0.0), S("Which way?", 0.7, 0.0), S("Left! No, the other left!", 0.9, 0.0),
            S("They're right behind us!", 0.8, 0.0), S("Hold on to something.", 0.8, 0.0),
            S("Are you out of your mind?", 0.9, 0.5)], hiz=300, tohum=5),
        Sahne("daktilo", "ic", "kenarli", [
            S("Wait... Is that him? No way. He said he left town!", 3.2, 0.6, efekt="daktilo"),
            S("Somebody has been lying to both of us.", 2.8, 0.6, efekt="daktilo"),
            S("Then we find out who, and we make it right.", 3.0, 0.6, efekt="daktilo")], hiz=30, tohum=3),
        Sahne("belirme", "gece", "golgeli", [
            S("I used to think this town owed me something.", 2.4, 0.6, efekt="belirme"),
            S("Turns out it doesn't owe anybody a thing.", 2.4, 0.6, efekt="belirme"),
            S("So we take what we need and we disappear.", 2.4, 0.6, efekt="belirme")], hiz=70, tohum=9),
        Sahne("bos_gece", "gece", "kenarli", [], hiz=150, bas=4.0, tohum=21),
        Sahne("bos_asfalt", "asfalt", "kenarli", [], hiz=420, bas=4.0, tohum=22),
        Sahne("bos_gunduz", "gunduz", "kenarli", [], hiz=80, bas=4.0, tohum=23),
    ]


GRUPLAR = {
    "konusma": ["gece_kenarli", "asfalt_golgeli", "beton_duz", "gunduz_golgeli", "yesillik_sari", "ic_gorev",
                "tabela_kalin", "hizli"],
    "efekt": ["daktilo", "belirme"],
    "bos": ["bos_gece", "bos_asfalt", "bos_gunduz"],
}


class Cizelge:
    """Sahnelerin art arda kareleri (JPEG baytları; istenince çözülür) ve altyazıların mutlak anları."""

    def __init__(self, secili: Sequence[Sahne], boyut: tuple[int, int] = BOYUT, fps: int = KAYNAK_FPS,
                 kalite: int = 72):
        self.fps, self.boyut = fps, boyut
        self.kareler: list[np.ndarray] = []
        self.satirlar: list[tuple[str, Satir]] = []
        self.bolumler: list[tuple[str, float, float]] = []
        for sahne in secili:
            bas = len(self.kareler) / fps
            self._ciz(sahne, bas, kalite)
            self.bolumler.append((sahne.ad, bas, len(self.kareler) / fps))
        self.sure = len(self.kareler) / fps
        self._son = (-1, None)

    def _ciz(self, sahne: Sahne, bas: float, kalite: int) -> None:
        w, h = self.boyut
        b = BICIMLER[sahne.bicim]
        t = sahne.bas
        for s in sahne.satirlar:  # anlar kare sınırlarına oturtulur
            s.t_on = round(t * self.fps) / self.fps
            s.t_off = round((t + s.sure) * self.fps) / self.fps
            s.t_tam = s.t_on
            t = s.t_off + s.bosluk
        n = math.ceil((t + 0.6) * self.fps)
        serit = zemin(sahne.zemin, h, w * 3, sahne.tohum)
        katmanlar: dict = {}
        for i in range(n):
            tt = (i + 0.5) / self.fps
            kay = int(sahne.hiz * tt) % max(1, serit.shape[1] - w)
            kare = serit[:, kay:kay + w]
            s = next((s for s in sahne.satirlar if s.t_on <= tt < s.t_off), None)
            if s is not None:
                goster, opaklik = None, 1.0
                if s.efekt == "daktilo":
                    harf = int((tt - s.t_on) * 28) + 1  # 28 harf/sn
                    if harf < len(s.metin):
                        goster, s.t_tam = harf, (i + 1) / self.fps
                elif s.efekt == "belirme":
                    opaklik = min(1.0, (tt - s.t_on) / 0.25 + 0.15)
                    if opaklik < 1.0:
                        s.t_tam = (i + 1) / self.fps
                anahtar = (id(s), goster)
                if anahtar not in katmanlar:
                    katmanlar[anahtar] = yazi_katmani(s.metin.split("\n"), w, h, b, s.vurgulu, goster)
                kare = birlestir(kare, *katmanlar[anahtar], opaklik)
            ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(kare), [cv2.IMWRITE_JPEG_QUALITY, kalite])
            self.kareler.append(buf)
        for s in sahne.satirlar:
            s.t_on, s.t_off, s.t_tam = s.t_on + bas, s.t_off + bas, s.t_tam + bas
            self.satirlar.append((sahne.ad, s))

    def kare(self, i: int) -> np.ndarray:
        i = max(0, min(i, len(self.kareler) - 1))
        if self._son[0] != i:
            self._son = (i, cv2.imdecode(self.kareler[i], cv2.IMREAD_COLOR))
        return self._son[1]


class SahtePencere:
    """Pencere yakalama gibi: kareler duvar saatine göre, en az `aralik` sn arayla teslim edilir (wait_frame)."""

    def __init__(self, cizelge: Cizelge, aralik: float):
        self.c, self.aralik = cizelge, aralik
        self.t0: float | None = None
        self.alive, self.closed, self.hwnd = True, False, 0
        self.alinan = 0
        self._goruldu = -1

    def baslat(self) -> None:
        self.t0 = time.monotonic()

    def _tik(self) -> int:
        return int((time.monotonic() - self.t0) / self.aralik)

    def wait_frame(self, timeout: float) -> bool:
        if self.t0 is None:
            time.sleep(min(timeout, self.aralik))
            return True
        son = time.monotonic() + timeout
        while self._tik() <= self._goruldu:
            kalan = min(self.t0 + (self._goruldu + 1) * self.aralik, son) - time.monotonic()
            if kalan > 0:
                time.sleep(kalan)
            if time.monotonic() >= son:
                return self._tik() > self._goruldu
        return True

    def grab_roi(self) -> np.ndarray:
        self.alinan += 1
        if self.t0 is None:
            return self.c.kare(0)
        self._goruldu = self._tik()
        return self.c.kare(int(self._goruldu * self.aralik * self.c.fps + 1e-6))

    def set_region(self, region) -> None:
        pass

    def stop(self) -> None:
        pass


# --- ölçüm ---------------------------------------------------------------------------------------------------------
@dataclass
class Olcum:
    olaylar: list[tuple[float, tuple]] = field(default_factory=list)  # (an - t0, çeviri kuyruğuna giren öğe)
    ocr_ms: list[float] = field(default_factory=list)
    islemci: list[tuple[float, float]] = field(default_factory=list)  # (an - t0, işlem süresi)
    alinan_kare: int = 0
    atilan: int = 0


def olc(cizelge: Cizelge, ayar: Any, pipeline_mod: Any, isinma: np.ndarray, yuk: int = 0,
        maske: int = 0) -> Olcum:
    """İşlem hattının yakalama ve OCR iş parçacıklarını sahte pencereyle çalıştırır."""
    pl = pipeline_mod
    onbellek = pl.CACHE_FILE
    pl.CACHE_FILE = Path(tempfile.gettempdir()) / "ocr_hiz_onbellek_yok.json"  # kullanıcının çeviri önbelleği okunmaz
    try:
        pipe = pl.Pipeline(ayar)
    finally:
        pl.CACHE_FILE = onbellek
    pipe.preview_enabled = True
    cap = SahtePencere(cizelge, _teslim_araligi(ayar.capture.fps))
    pipe._open_capturer = lambda: cap
    sonuc = Olcum()
    t0_kutu = [0.0]
    pipe._post_tr = lambda oge: sonuc.olaylar.append((time.monotonic() - t0_kutu[0], oge))
    motorlar: list = []
    gercek = pl.create_engine

    class Zamanli:
        def __init__(self, motor):
            self.m, self.name = motor, getattr(motor, "name", "?")

        def recognize(self, img):
            t = time.perf_counter()
            try:
                return self.m.recognize(img)
            finally:
                sonuc.ocr_ms.append((time.perf_counter() - t) * 1000)

        def close(self):
            self.m.close()

    def olustur(*a, **k):
        motor = Zamanli(gercek(*a, **k))
        motorlar.append(motor)
        return motor
    pl.create_engine = olustur
    hatalar: list[str] = []

    def koru(fn):
        try:
            fn()
        except Exception:
            import traceback
            hatalar.append(traceback.format_exc())
    yukler = _yuk_baslat(yuk, maske)
    try:
        pipe._run.set()
        ipler = [threading.Thread(target=koru, args=(pipe._capture_loop,), daemon=True),
                 threading.Thread(target=koru, args=(pipe._ocr_loop,), daemon=True)]
        for th in ipler:
            th.start()
        son = time.monotonic() + 60  # ısınma: modeller yüklensin, ilk çıkarım yapılsın
        while not motorlar and time.monotonic() < son and not hatalar:
            time.sleep(0.05)
        for _ in range(3):
            pipe._ocr_q.put(("text", time.monotonic(), isinma))
        while not sonuc.olaylar and time.monotonic() < son and not hatalar:
            time.sleep(0.05)
        time.sleep(1.0)
        pipe._ocr_q.put(("gone", time.monotonic(), None))
        time.sleep(0.5)
        pipe.tracker = type(pipe.tracker)(0)
        sonuc.olaylar.clear()
        sonuc.ocr_ms.clear()
        dur = threading.Event()

        def ornekle():
            while not dur.is_set():
                sonuc.islemci.append((time.monotonic() - t0_kutu[0], time.process_time()))
                dur.wait(0.2)
        cap.alinan = 0
        cap.baslat()
        t0_kutu[0] = cap.t0
        threading.Thread(target=ornekle, daemon=True).start()
        while time.monotonic() - cap.t0 < cizelge.sure + 1.0 and not hatalar:
            time.sleep(0.1)
        dur.set()
        sonuc.islemci.append((time.monotonic() - cap.t0, time.process_time()))
        pipe._run.clear()
        for th in ipler:
            th.join(5)
    finally:
        pl.create_engine = gercek
        for p in yukler:
            p.kill()
    if hatalar:
        raise RuntimeError("İşlem hattı hata verdi:\n" + "\n".join(hatalar))
    sonuc.alinan_kare, sonuc.atilan = cap.alinan, pipe._ocr_q.dropped
    return sonuc


def _teslim_araligi(fps: int) -> float:
    """Pencere yakalamanın kare teslim aralığı (sn): kaynak ağacın kendi hesabı, yoksa eski kural."""
    try:
        from altyazitr.capture import frame_interval_ms
        return frame_interval_ms(fps) / 1000.0
    except ImportError:  # 1.2.1 ve öncesi: tarama hızının iki katı
        return max(16, int(1000 / (max(1, fps) * 2))) / 1000.0


def _yuk_baslat(n: int, maske: int) -> list:
    """Arka plan yükü: aynı çekirdeklere bağlı n meşgul süreç (zayıf makinede oyun + OBS taklidi)."""
    kod = ("import ctypes,sys\nm=int(sys.argv[1])\n"
           "if m and sys.platform=='win32':\n"
           " k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p\n"
           " k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]\n"
           " k.SetProcessAffinityMask(k.GetCurrentProcess(),m)\n"
           "x=0\nwhile True:\n x+=1\n")
    return [subprocess.Popen([sys.executable, "-c", kod, str(maske)]) for _ in range(n)]


def zayif_makine(cekirdek: int) -> int:
    """Süreci `cekirdek` mantıksal işlemciye bağlar (ayrı fiziksel çekirdekler seçilir) ve os.cpu_count'u o sayıya
    indirir (OCR iş parçacığı sayısı ona göre seçilir). Bağlama maskesini döndürür (Windows dışında 0)."""
    toplam = os.cpu_count() or cekirdek
    adim = 2 if toplam >= 2 * cekirdek else 1
    maske = 0
    for i in range(cekirdek):
        maske |= 1 << (i * adim)
    os.cpu_count = lambda: cekirdek
    if sys.platform != "win32":
        return 0
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k32.SetProcessAffinityMask(k32.GetCurrentProcess(), maske)
    return maske


# --- rapor ---------------------------------------------------------------------------------------------------------
def yuzdelik(xs: Sequence[float], p: float) -> float | None:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


def degerlendir(cizelge: Cizelge, olcum: Olcum) -> dict:
    """Okumaları altyazılarla eşleştirir; gecikme, doğruluk ve işlemci özetini döndürür."""
    from rapidfuzz import fuzz
    from rapidfuzz.distance import Levenshtein

    from altyazitr.ocr import OcrLine
    from altyazitr.textproc import assemble, norm_key

    def dogru(s: Satir) -> tuple[str, str | None]:
        satirlar = [OcrLine(t, 0.99, 10, 10 + 60 * i, 400, 30) for i, t in enumerate(s.metin.split("\n"))]
        metin, konusmaci, _ = assemble(satirlar)
        return metin, konusmaci

    hedefler = [(ad, s, *dogru(s)) for ad, s in cizelge.satirlar]
    eslesen: dict[int, list] = {i: [] for i in range(len(hedefler))}
    hayaletler, isaretler = [], {"gone": 0, "same": 0}
    for t, (tur, _t, ev) in olcum.olaylar:
        if tur != "sub":
            isaretler[tur] = isaretler.get(tur, 0) + 1
            continue
        k = norm_key(ev["text"])
        en, en_i = 0.0, -1
        for i, (_ad, s, metin, _k) in enumerate(hedefler):
            sonraki = hedefler[i + 1][1].t_on if i + 1 < len(hedefler) else math.inf
            if t < s.t_on - 0.02 or t > min(sonraki, s.t_off) + 1.2:
                continue
            hk = norm_key(metin)
            benzer = max(fuzz.ratio(k, hk), (fuzz.partial_ratio(k, hk) - 8) if len(k) >= 4 else 0)
            if benzer > en:
                en, en_i = benzer, i
        if en >= 60:
            eslesen[en_i].append((t, ev))
        else:
            hayaletler.append((round(t, 2), ev["text"]))
    satirlar, gecikmeler, uzaklik, uzunluk = [], [], 0, 0
    sahneler_: dict[str, dict] = {}
    for i, (ad, s, metin, konusmaci) in enumerate(hedefler):
        evs = eslesen[i]
        hk = norm_key(metin)
        ilk_dogru = next((t for t, ev in evs if norm_key(ev["text"]) == hk), None)
        yanlis = [(round(t - s.t_on, 3), ev["text"]) for t, ev in evs if norm_key(ev["text"]) != hk]
        son = evs[-1][1] if evs else None
        d = Levenshtein.distance(son["text"], metin) if son else len(metin)
        tam = bool(son) and son["text"] == metin and son["speaker"] == konusmaci
        gecikme = (ilk_dogru - s.t_tam) * 1000 if ilk_dogru is not None else None
        uzaklik += d
        uzunluk += len(metin)
        if gecikme is not None:
            gecikmeler.append(gecikme)
        sc = sahneler_.setdefault(ad, {"n": 0, "gecikme": [], "kacan": 0, "tam": 0, "yanlis": 0})
        sc["n"] += 1
        sc["kacan"] += not evs
        sc["tam"] += tam
        sc["yanlis"] += len(yanlis)
        if gecikme is not None:
            sc["gecikme"].append(gecikme)
        satirlar.append({"sahne": ad, "dogru": metin, "son": son["text"] if son else None, "gecikme_ms": gecikme,
                         "tam": tam, "yanlis": yanlis, "okuma": len(evs)})

    def islemci(a: float, b: float) -> float | None:
        if len(olcum.islemci) < 2:
            return None
        (ta, ca) = min(olcum.islemci, key=lambda x: abs(x[0] - a))
        (tb, cb) = min(olcum.islemci, key=lambda x: abs(x[0] - b))
        return round(100.0 * (cb - ca) / max(1e-6, tb - ta), 1)

    def yuvarla(x):
        return None if x is None else round(x, 1)
    return {
        "satir": len(hedefler), "sure_s": round(cizelge.sure, 1),
        "gecikme_ortanca_ms": yuvarla(statistics.median(gecikmeler)) if gecikmeler else None,
        "gecikme_p95_ms": yuvarla(yuzdelik(gecikmeler, 0.95)), "gecikme_enb_ms": yuvarla(max(gecikmeler, default=None)),
        "karakter_dogrulugu": round(100 * (1 - uzaklik / max(1, uzunluk)), 2),
        "tam_eslesen": sum(r["tam"] for r in satirlar),
        "kacan": sum(r["okuma"] == 0 for r in satirlar),  # hiç okunmayan satır
        "hic_dogru_degil": sum(r["okuma"] > 0 and r["gecikme_ms"] is None for r in satirlar),  # okundu, hep hatalı
        "yanlis_gonderilen": sum(len(r["yanlis"]) for r in satirlar), "hayalet": len(hayaletler),
        "isaretler": isaretler,
        "islemci_yuzde": islemci(0, cizelge.sure),
        "islemci_sahne": {ad: islemci(a, b) for ad, a, b in cizelge.bolumler},
        "ocr_cagri": len(olcum.ocr_ms),
        "ocr_ortanca_ms": yuvarla(statistics.median(olcum.ocr_ms)) if olcum.ocr_ms else None,
        "ocr_p95_ms": yuvarla(yuzdelik(olcum.ocr_ms, 0.95)),
        "kare_sn": round(olcum.alinan_kare / max(1e-6, cizelge.sure), 1), "atilan_kare": olcum.atilan,
        "sahneler": {ad: {"n": v["n"], "gecikme_ortanca_ms": yuvarla(statistics.median(v["gecikme"])) if v["gecikme"]
                          else None, "kacan": v["kacan"], "tam": v["tam"], "yanlis": v["yanlis"]}
                     for ad, v in sahneler_.items()},
        "satirlar": satirlar, "hayaletler": hayaletler,
    }


def rapor_yaz(sonuc: dict, ayrinti: bool = False, olaylar: list | None = None) -> str:
    def ms(x):
        return "-" if x is None else f"{x:.0f} ms"
    o = [f"Ayarlar: {sonuc.get('ayarlar', {})}",
         f"Satır: {sonuc['satir']} ({sonuc['sure_s']} sn)   gecikme ortanca {ms(sonuc['gecikme_ortanca_ms'])}, "
         f"%95 {ms(sonuc['gecikme_p95_ms'])}, en çok {ms(sonuc['gecikme_enb_ms'])}",
         f"Doğruluk: karakter %{sonuc['karakter_dogrulugu']}, tam eşleşen {sonuc['tam_eslesen']}/{sonuc['satir']}, "
         f"kaçan {sonuc['kacan']}, hiç doğru okunmayan {sonuc['hic_dogru_degil']}, yanlış/yarım gönderilen "
         f"{sonuc['yanlis_gonderilen']}, hayalet {sonuc['hayalet']}",
         f"İşlemci: %{sonuc['islemci_yuzde']} çekirdek   OCR: {sonuc['ocr_cagri']} çağrı, ortanca "
         f"{ms(sonuc['ocr_ortanca_ms'])}, %95 {ms(sonuc['ocr_p95_ms'])}   tarama {sonuc['kare_sn']} kare/sn", ""]
    for ad, v in sonuc["sahneler"].items():
        o.append(f"  {ad:<16} satır {v['n']}  gecikme {ms(v['gecikme_ortanca_ms']):>7}  kaçan {v['kacan']}  "
                 f"tam {v['tam']}/{v['n']}  yanlış {v['yanlis']}  işlemci %{sonuc['islemci_sahne'].get(ad)}")
    for ad, yuzde in sonuc["islemci_sahne"].items():
        if ad not in sonuc["sahneler"]:
            o.append(f"  {ad:<16} (yazısız)  işlemci %{yuzde}")
    for r in sonuc["satirlar"]:
        if not r["tam"] or r["yanlis"] or r["gecikme_ms"] is None:
            o.append(f"   ! [{r['sahne']}] doğru={r['dogru']!r} son={r['son']!r} gecikme={ms(r['gecikme_ms'])} "
                     f"yanlış={r['yanlis']}")
    for h in sonuc["hayaletler"][:12]:
        o.append(f"   ? hayalet {h}")
    if ayrinti and olaylar:
        o.append("   --- olaylar ---")
        for t, (tur, _t, ev) in olaylar:
            ek = " (güncelleme)" if ev and ev.get("replace") else ""
            o.append(f"   {t:7.3f} {tur:<5} {(ev or {}).get('text', '')!r}{ek}")
    return "\n".join(o)


ANAHTARLAR = (("gecikme_ortanca_ms", "gecikme ortanca (ms)"), ("gecikme_p95_ms", "gecikme %95 (ms)"),
              ("karakter_dogrulugu", "karakter doğruluğu (%)"), ("tam_eslesen", "tam eşleşen"), ("kacan", "kaçan"),
              ("hic_dogru_degil", "hiç doğru okunmayan"),
              ("yanlis_gonderilen", "yanlış/yarım gönderilen"), ("hayalet", "hayalet"),
              ("islemci_yuzde", "işlemci (% çekirdek)"), ("ocr_ortanca_ms", "OCR çağrısı ortanca (ms)"),
              ("ocr_cagri", "OCR çağrısı"))


def karsilastir(a: dict, b: dict, ad_a: str = "A", ad_b: str = "B") -> str:
    o = [f"{'ölçü':<26} {ad_a:>14} {ad_b:>14}"]
    for k, baslik in ANAHTARLAR:
        o.append(f"{baslik:<26} {a.get(k)!s:>14} {b.get(k)!s:>14}")
    return "\n".join(o)


# --- komut satırı --------------------------------------------------------------------------------------------------
def sahne_sec(tanim: str) -> list[Sahne]:
    hepsi = {s.ad: s for s in sahneler()}
    adlar: list[str] = []
    for parca in tanim.split(","):
        parca = parca.strip()
        if parca == "hepsi":
            adlar += [a for g in GRUPLAR.values() for a in g]
        elif parca in GRUPLAR:
            adlar += GRUPLAR[parca]
        elif parca in hepsi:
            adlar.append(parca)
        else:
            raise SystemExit(f"Bilinmeyen sahne: {parca} (gruplar: {', '.join(GRUPLAR)}, hepsi; sahneler: "
                             f"{', '.join(hepsi)})")
    return [hepsi[a] for a in adlar]


def ayar_kur(args: argparse.Namespace) -> Any:
    from altyazitr.config import Settings

    s = Settings()
    s.capture.mode, s.capture.window_exe, s.capture.rect = "window", "sahte.exe", [0, 0, *args.boyut]
    s.ocr.detect_mode = args.mod
    for kv in args.set:
        anahtar, _, deger = kv.partition("=")
        bolum, _, alan = anahtar.partition(".")
        nesne = getattr(s, bolum, None)
        if nesne is None or not hasattr(nesne, alan):
            raise SystemExit(f"Bilinmeyen ayar: {anahtar}")
        simdiki = getattr(nesne, alan)
        if isinstance(simdiki, bool):
            yeni: Any = deger.strip().lower() in ("1", "true", "evet", "açık", "acik")
        else:
            yeni = type(simdiki)(deger)
        setattr(nesne, alan, yeni)
    return s


def _boyut(metin: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in metin.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"GENxYUK olmalı: {metin}") from None
    return w, h


def _argumanlar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python tools/ocr_hiz.py", description=__doc__, add_help=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="help", help="bu yardımı gösterir")
    p.add_argument("--mod", choices=("smart", "interval"), default="smart",
                   help="algılama kipi: smart (akıllı, varsayılan) | interval (sabit aralık)")
    p.add_argument("--sahne", default="konusma", metavar="AD[,AD]",
                   help="sahne ya da grup: konusma (varsayılan), efekt, bos, hepsi ya da sahne adları")
    p.add_argument("--set", action="append", default=[], metavar="BOLUM.ALAN=DEGER",
                   help="ayarı değiştirir (ör. capture.fps=10, ocr.interval_ms=250, ocr.text_detector=hayır)")
    p.add_argument("--boyut", type=_boyut, default=BOYUT, metavar="GENxYUK", help="bölge boyutu (1213x179)")
    p.add_argument("--zayif", type=int, default=0, metavar="N",
                   help="zayıf makine taklidi: süreç N çekirdeğe bağlanır, iş parçacıkları N'e göre seçilir")
    p.add_argument("--yuk", type=int, default=0, metavar="N", help="aynı çekirdeklerde N meşgul süreç (oyun, OBS)")
    p.add_argument("--kaynak", metavar="KLASÖR", help="ölçülecek başka bir kaynak ağacı (içinde altyazitr/ olan)")
    p.add_argument("--json", metavar="SONUC.json", help="sonucun yazılacağı dosya")
    p.add_argument("--ayrinti", action="store_true", help="olay dökümünü de yazar")
    p.add_argument("--karsilastir", nargs=2, metavar=("A.json", "B.json"), help="iki sonucu karşılaştırır")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    for akis in (sys.stdout, sys.stderr):  # Windows konsolu (cp1252) Türkçe harfte hata vermesin
        try:
            akis.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _argumanlar().parse_args(argv)
    if args.karsilastir:
        a, b = (json.loads(Path(x).read_text(encoding="utf-8")) for x in args.karsilastir)
        print(karsilastir(a, b, Path(args.karsilastir[0]).stem, Path(args.karsilastir[1]).stem))
        return 0
    kaynak = Path(args.kaynak).resolve() if args.kaynak else ROOT
    if not (kaynak / "altyazitr").is_dir():
        raise SystemExit(f"Kaynak ağacında altyazitr/ yok: {kaynak}")
    sys.path.insert(0, str(kaynak))
    maske = zayif_makine(args.zayif) if args.zayif else 0
    from altyazitr import pipeline

    secili = sahne_sec(args.sahne)
    print(f"Sahneler hazırlanıyor: {', '.join(s.ad for s in secili)}", file=sys.stderr, flush=True)
    cizelge = Cizelge(secili, args.boyut)
    w, h = args.boyut
    isinma = birlestir(zemin("gece", h, w), *yazi_katmani(["Warm up the engine first."], w, h, BICIMLER["kenarli"]))
    ayar = ayar_kur(args)
    print(f"Ölçülüyor ({cizelge.sure:.0f} sn)…", file=sys.stderr, flush=True)
    olcum = olc(cizelge, ayar, pipeline, isinma, args.yuk, maske)
    sonuc = degerlendir(cizelge, olcum)
    sonuc["ayarlar"] = {"kaynak": str(kaynak), "mod": ayar.ocr.detect_mode, "fps": ayar.capture.fps,
                        "kararlilik": ayar.ocr.stable_frames, "aralik_ms": ayar.ocr.interval_ms,
                        "metin_bulucu": ayar.ocr.text_detector, "zayif": args.zayif, "yuk": args.yuk,
                        "sahne": args.sahne, "set": args.set}
    print(rapor_yaz(sonuc, args.ayrinti, olcum.olaylar))
    if args.json:
        Path(args.json).write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
