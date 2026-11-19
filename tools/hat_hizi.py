"""Hat hızı ölçümü: İngilizce altyazı oyunda belirdikten kaç milisaniye sonra Türkçesi ekrana çıkıyor?

Gerçek işlem hattı (altyazitr.pipeline.Pipeline: yakalama → değişim algılama → OCR → takip → hızlı çeviri → asıl
çeviri → `subtitle` olayı) sahte bir oyunla, gerçek zamanda çalıştırılır; her altyazı satırı için "satır oyunda
belirdi" anından "Türkçe altyazı olayı arayüze ulaştı" anına kadar geçen süre ölçülür. Aynı betik, aynı senaryoyla
hem yayımlanmış sürümün ağacını hem de çalışma ağacını ölçer (--tree): öncesi/sonrası karşılaştırması içindir.

Ne çalışır, ne sahtedir:

* Yakalama sahtedir, pencere açılmaz: Pipeline._open_capturer, ağacın kendi yakalayıcı sınıfından türetilmiş sahte
  bir yakalayıcı döndürür (--capture screen: RegionCapturer gibi her çağrıda o anki kare; window: WindowCapturer
  gibi en az `kare aralığı` kadar arayla teslim edilen son kare, wait_frame ile). Kareler bellekte üretilir: oyun
  gibi kayan zemin (gece sokağı, gündüz otoyolu, loş iç mekân), hareketli ışık ve gölgeler, kumlanma, altta ortada
  GTA tarzı altyazı (beyaz, koyu kenarlı ve gölgeli yazı; birkaç satırda tek kelime renkli). Bölge, 1920x1080
  karenin varsayılan altyazı şeridi kadardır (1460x238).
* Senaryo ~20 özgün satırdır (bu proje için yazıldı; oyun ya da film metninden alıntı yoktur), ~50 saniye sürer:
  tek ve iki satırlı altyazılar, çok kısa ("No.") ve uzun satırlar, arada boşluk olmadan birbirinin yerine geçen
  hızlı konuşma, ardından ekranın boş kaldığı satırlar. --seed zamanlamaya küçük kaymalar ve zemine başka bir
  görüntü verir; aynı tohum aynı senaryoyu üretir. Zaman hızlandırılmaz.
* Değişim algılama, OCR (RapidOCR), takip, gösterim sırası ve iş parçacıkları ağacın kendi kodudur.
* Hızlı çeviri: --quick real ağacın gerçek QuickMtTranslator'ı (model bu makinede olmalı; indirilmez),
  --quick fake ~25 ms uyuyan sahte motor.
* Asıl motor: varsayılan olarak sahte bir yerel yapay zekâdır (Ollama'ya dokunulmaz): altyazitr.pipeline içindeki
  create_translator değiştirilir, motor satır başına --main-ms kadar (±%8, metne bağlı) bekleyip işaretli bir çeviri
  döndürür (400 = ekran kartı, 1500 ve 2500 = zayıf makine). --main ollama ağacın kendi create_translator'ıyla
  gerçek Ollama'yı kullanır (Ollama açık ve model yüklü olmalı).
* Ayarlar kodda kurulur (Settings()); kullanıcının ayar dosyası okunmaz ve yazılmaz, çeviri önbelleği geçici bir
  dosyaya yönlendirilir (kullanıcının önbelleğine dokunulmaz, her ölçüm soğuk başlar). Ön ayarlar:
  --preset varsayilan: ağacın Settings() varsayılanları (akıllı algılama, karma mod açık, bekleme 1200 ms);
  --preset kullanici: bu projenin sahibinin 1.2.1 ayar dosyası (sabit aralık 500 ms, 10 kare/sn, kararlılık 2,
  karma mod kapalı, zaman aşımı 6 sn, akışta 3 satır), ağacın kendi yükleyicisinden (config.load_settings) geçmiş
  haliyle: yeni ağaçta sürüm taşımaları uygulanır (ör. 10 → 20 kare/sn, kararlılık 2 → 1), yani güncelleyen
  kullanıcının gerçekten çalıştıracağı ayarlar ölçülür; taşınan alanlar raporda yazar.
  İkisinde de translate.engine "ollama" yapılır (yoksa asıl motor hiç devreye girmez). --set ocr.interval_ms=250
  gibi tek tek ayar en son uygulanır (taşımayı geri almak için de: --set capture.fps=10); ağaçta olmayan alan
  uyarıyla atlanır (aynı komut iki ağaçta da çalışsın).

Ölçülenler (satır başına; süreler satırın oyunda belirdiği andan, yani o satırı içeren ilk karenin yakalayıcıdan
alınabildiği andan sayılır):

* first_ms: satır için gelen ilk `subtitle` olayına kadar (hangi motor olursa olsun) — izleyicinin beklediği süre.
* main_ms: `engine` alanı asıl motorun etiketi olan ilk olaya kadar.
* Aşamalar (first_ms'in dökümü): kare = satırı içeren ilk karenin hatta verilmesi (yakalama beklemesi); kapı = o
  kareden okunan kareye (değişim kapısı / sabit aralık beklemesi); sıra = OCR kuyruğu; OCR = okuma; onay = OCR
  bitiminden okumanın çeviri kuyruğuna girmesine (erken okunan kare geçidin onayını bekler; Pipeline._post_tr);
  çeviri = oradan olayın gelişine (hızlı çeviri, asıl çeviriyi bekleme, Qt kuyruğu).
* Hangi motor önce gösterildi, hızlı çeviri sonradan asıl çeviriyle değişti mi, satır hiç gösterilmedi mi (kayıp),
  yanlış mı okundu (benzerlik eşiğin altında; okunan metin raporda), aynı satır birden çok kez yeni satır olarak mı
  geldi (yineleme), ardından ekran boş kalan satırda `gone` işareti ne kadar sonra geldi ya da hiç gelmedi mi.
* Özet: first_ms ve main_ms için p50 / p90 / en yüksek, kayıp / yanlış / yinelenen / önce hızlı gösterilen satır
  sayıları, OCR çağrı sayısı ve ortalama süresi, zaman çizelgesi boyunca sürecin işlemci süresi.

Bir ölçüm ~70 saniye sürer: modellerin yüklenmesi, sayılmayan iki ısınma satırı, senaryo, son olayların beklenmesi.

Kullanım:
  python tools/hat_hizi.py                                         # çalışma ağacı, varsayılan ayarlar, sahte 400 ms
  python tools/hat_hizi.py --preset kullanici --main-ms 1500 --json yeni.json
  python tools/hat_hizi.py --tree C:\\yol\\eski_agac --preset kullanici --main-ms 1500 --json eski.json
  python tools/hat_hizi.py --compare eski.json yeni.json           # öncesi / sonrası tablosu
  python tools/hat_hizi.py --compare e1.json,e2.json y1.json,y2.json   # başka tohumlu yinelemeler birleştirilir
  python tools/hat_hizi.py --summary a.json b.json c.json          # çok sayıda ölçüm tek tabloda
  python tools/hat_hizi.py --reanalyze a.json                      # kayıtlı ham veriden yeniden çözümle
  python tools/hat_hizi.py --set ocr.interval_ms=250 --set capture.fps=15 --capture window
  python tools/hat_hizi.py --main ollama --preset kullanici        # gerçek Ollama ile (model yüklü olmalı)
  python tools/hat_hizi.py --limit 3 --quick fake --verbose        # kısa deneme: ilk 3 satır, olay dökümü

Eski ağaç depoyu değiştirmeden şöyle çıkarılır:  git archive HEAD | tar -x -C <boş klasör>

Sınırlar: kareler sentetiktir (gerçek oyun görüntüsü ve video sıkıştırması yoktur; okuma doğruluğu gerçek oyunu
temsil etmez), sahte yapay zekânın süresi satır uzunluğuna bağlı değildir, ekran kartı ve oyunla işlemci paylaşımı
yoktur, ekrandaki gösterim mantığı (display.py: en az / en çok ekranda kalma) ölçülmez. Tek ölçüm ~20 satırdır:
ortanca ±50 ms oynayabilir; kesin sayı için birkaç tohumla yineleyin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import statistics
import sys
import tempfile
import threading
import time
import weakref
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from rapidfuzz import fuzz

KOK = Path(__file__).resolve().parents[1]
BICIM = 1  # --json çıktısının biçim sürümü; alanlar değişirse artırılır
KAYNAK_FPS = 30  # sahte oyunun kare hızı
BOYUT = (1460, 238)  # 1920x1080 karede varsayılan bölge (capture.region = 0.12, 0.74, 0.76, 0.22)
YAZI_BOYU = 34 / 238  # altyazı yazı boyu / bölge yüksekliği (1080p'de 34 px)
ALT_PAY = 0.16  # son satırın altında kalan boşluk / bölge yüksekliği
YAZI_TIPLERI = ("arialbd.ttf", "segoeuib.ttf", "arial.ttf", "segoeui.ttf", "tahomabd.ttf")
RENKLER = {"beyaz": (240, 240, 240), "pembe": (236, 141, 192), "mavi": (93, 182, 229), "sari": (240, 205, 70)}  # RGB
NOKTALAMA = ".,!?;:\"'"
BAS_BOSLUK = 1.0  # senaryonun başındaki yazısız süre (sn)
ESLESME_ESIGI = 60.0  # olay bir satıra en az bu benzerlikle bağlanır
BENZERLIK_ESIGI = 90.0  # son okuması bunun altında kalan satır "yanlış okundu" sayılır
GONE_EN_AZ_BOSLUK = 0.6  # ardından en az bu kadar boş ekran olan satırda `gone` işareti beklenir (sn)
SAHTE_LLM_ETIKETI = "Sahte yapay zekâ (hat_hizi)"
SAHTE_HIZLI_ETIKETI = "Sahte hızlı çeviri (hat_hizi)"
ISINMA_SATIRLARI = ("This first line only warms up the engines.", "Second warm-up line, nothing to measure yet.")
ISINMA_EN_AZ_S = 2.0  # her ısınma satırı en az bu kadar ekranda kalır
ISINMA_ARA_S = 1.0  # ısınma satırları arasındaki boş ekran
ILK_ISINMA_EK_S = 10.0  # ilk ısınma satırına ek süre: modellerin ilk çıkarımı yavaş olabilir
OLCUM_ONCESI_S = 2.0  # son ısınma satırından sonra, senaryodan önce boş ekran
OCR_BEKLEME_S = 45.0  # OCR motorunun kurulduğu görülmezse ısınmaya en geç bu kadar sonra geçilir (sn)
KUYRUK_EN_AZ_S = 1.5  # senaryo bitince son olaylar için en az bu kadar beklenir
KUYRUK_SESSIZ_S = 2.0  # asıl motor boştayken bu kadar süre olay gelmezse beklenecek bir şey kalmamıştır
KUYRUK_SINIRI_S = 15.0  # son olaylar en çok bu kadar beklenir (gerçek yapay zekâda iki katı)
AYAR_BOLUMLERI = ("capture", "ocr", "translate", "flow")  # sonuca yazılan ayar bölümleri

Kayit = dict[str, Any]  # JSON'a giden sözlükler: sonuç, satır, olay

log = logging.getLogger("hat_hizi")


def _bilgi(mesaj: str) -> None:
    """Durum ve ilerleme mesajı. stderr'e gider; tablo stdout'a yazılır."""
    print(mesaj, file=sys.stderr, flush=True)


# --- senaryo ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Replik:
    """Senaryodaki bir altyazı. `metin` iki satırlıysa "\\n" içerir; `bosluk` 0 ise sonraki replik hemen yerine
    geçer (hızlı konuşma), değilse ardından o kadar saniye ekran boş kalır. `vurgu`: renkli çizilecek tek kelime."""

    metin: str
    sure: float
    bosluk: float
    vurgu: str = ""
    renk: str = "pembe"


# Özgün metin: bu ölçüm için yazıldı. Yeni satır eklerken de oyun, film ya da dizi metninden alıntı yapmayın.
SENARYO: tuple[tuple[str, tuple[Replik, ...]], ...] = (
    ("gece", (
        Replik("You said the warehouse would be empty tonight.", 2.4, 0.9),
        Replik("It was, until your cousin showed up with a camera.", 2.4, 0.8),
        Replik("No.", 1.0, 0.8),
        Replik("We take the back road past the old cannery,\nand nobody stops until we can see the water.", 3.2, 1.0),
        Replik("Meet the courier at the harbor before sunrise.", 2.4, 1.0, vurgu="harbor"),
    )),
    ("otoyol", (
        Replik("Is that a patrol car behind us?", 1.3, 0.0),
        Replik("Eyes on the road.", 1.0, 0.0),
        Replik("He just switched his lights on!", 1.2, 0.0),
        Replik("Then drive!", 0.9, 0.0),
        Replik("Cut through the parking lot, it's faster.", 1.5, 0.0),
        Replik("I am not paying for another windshield.", 1.6, 0.8),
        Replik("If they trace the plates, they find the garage,\nand if they find the garage, they find all of us.",
               3.0, 0.0),
        Replik("So we don't give them the chance.", 1.9, 1.2),
    )),
    ("ic", (
        Replik("Take the ledger to the laundromat on Fifth.", 2.4, 0.8, vurgu="ledger"),
        Replik("Okay.", 0.9, 0.8),
        Replik("My uncle ran this block for thirty years\nand never once raised his voice at a customer.", 3.2, 0.0),
        Replik("And look what it got him.", 1.4, 0.0),
        Replik("A quiet funeral.", 1.3, 0.9),
        Replik("Find Dario and ask him who made the call.", 2.4, 1.0, vurgu="Dario", renk="mavi"),
        Replik("When this is over, I'm buying a boat\nand forgetting every one of your names.", 2.8, 1.2),
    )),
)
ZEMINLER = tuple(ad for ad, _ in SENARYO)
ZEMIN_HIZI = {"gece": 70.0, "otoyol": 420.0, "ic": 35.0}  # zeminin yatay kayma hızı (px/sn)


@dataclass
class Satir:
    """Zaman çizelgesindeki bir altyazı: replik + kare sınırlarına oturtulmuş anları (kareler senaryonun başından)."""

    no: int  # 1'den başlar
    metin: str  # ekrandaki hali (iki satırlıysa "\n" ile)
    vurgu: str
    renk: str
    zemin: str
    kare_on: int  # göründüğü ilk kare
    kare_off: int  # artık görünmediği ilk kare
    bosluk: float  # ardından boş ekran (sn); 0: sonraki satır hemen yerine geçer
    fps: int = KAYNAK_FPS

    @property
    def duz(self) -> str:
        """Tek satıra indirilmiş metin: OCR'ın okuması beklenen."""
        return " ".join(self.metin.split())

    @property
    def t_on(self) -> float:
        return self.kare_on / self.fps

    @property
    def t_off(self) -> float:
        return self.kare_off / self.fps


@dataclass
class Cizelge:
    """Senaryonun zaman çizelgesi: hangi karede hangi satır ve hangi zemin var."""

    satirlar: list[Satir]
    bolumler: list[tuple[str, int]]  # (zemin, başladığı kare), sırayla
    kare_sayisi: int
    fps: int = KAYNAK_FPS

    def __post_init__(self) -> None:
        self._sahip: list[Satir | None] = [None] * self.kare_sayisi
        for s in self.satirlar:
            for k in range(s.kare_on, min(s.kare_off, self.kare_sayisi)):
                self._sahip[k] = s

    @property
    def sure_s(self) -> float:
        return self.kare_sayisi / self.fps

    def satir_at(self, k: int) -> Satir | None:
        """k. karede görünen satır (yoksa None)."""
        return self._sahip[k] if 0 <= k < self.kare_sayisi else None

    def zemin_at(self, k: int) -> str:
        ad = self.bolumler[0][0]
        for zemin, bas in self.bolumler:
            if k >= bas:
                ad = zemin
        return ad

    def ozet(self) -> str:
        """Senaryonun kısa özeti (iki ölçüm aynı senaryoyla mı yapıldı?)."""
        veri = [(s.metin, s.kare_on, s.kare_off, s.vurgu, s.zemin) for s in self.satirlar]
        return hashlib.sha1(json.dumps([veri, self.kare_sayisi, self.fps]).encode("utf-8")).hexdigest()[:12]


def cizelge_kur(tohum: int = 1, limit: int | None = None, fps: int = KAYNAK_FPS) -> Cizelge:
    """Senaryoyu zaman çizelgesine döker. Tohum, sürelere ±%5 ve boşluklara ±0,1 sn oynatır (satırların sabit
    aralık kipindeki okuma anlarına göre fazı her tohumda başka olsun); sıfır boşluklar sıfır kalır."""
    rng = random.Random(tohum)
    t = BAS_BOSLUK + rng.uniform(0.0, 0.3)
    satirlar: list[Satir] = []
    bolumler: list[tuple[str, int]] = []
    onceki_off = 0
    for zemin, replikler in SENARYO:
        for r in replikler:
            if limit is not None and len(satirlar) >= limit:
                break
            sure = r.sure * rng.uniform(0.95, 1.05)
            bosluk = max(0.7, r.bosluk + rng.uniform(-0.1, 0.1)) if r.bosluk > 0 else 0.0
            on = max(onceki_off, round(t * fps))
            off = on + max(2, round(sure * fps))
            if not bolumler:
                bolumler.append((zemin, 0))
            elif bolumler[-1][0] != zemin:  # sahne, iki satırın arasındaki boşluğun ortasında değişir
                bolumler.append((zemin, (onceki_off + on) // 2))
            satirlar.append(Satir(len(satirlar) + 1, r.metin, r.vurgu, r.renk, zemin, on, off, bosluk, fps))
            t = off / fps + bosluk
            onceki_off = off
    if not satirlar:
        raise ValueError("Senaryoda satır yok")
    son_bosluk = max(satirlar[-1].bosluk, 1.0)
    return Cizelge(satirlar, bolumler, satirlar[-1].kare_off + round(son_bosluk * fps), fps)


# --- kare üretimi ----------------------------------------------------------------------------------------------------
@dataclass
class Katman:
    """Önceden çizilmiş altyazı yazısı (yalnız yazının kutusu): kareye bir çarpma ve bir toplamayla bindirilir."""

    x: int
    y: int
    carpan: np.ndarray  # uint8 [h, w, 3]: zeminin kalan payı (255 = yazı yok)
    eklenen: np.ndarray  # uint8 [h, w, 3]: yazının rengi x saydamlığı (BGR)


def _yazi_tipi(px: int) -> Any:
    try:
        from PIL import ImageFont
    except ImportError:
        raise SystemExit("Pillow yüklü değil (requirements-dev.txt): altyazı yazısı çizilemiyor.") from None
    klasor = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for ad in YAZI_TIPLERI:
        if (klasor / ad).is_file():
            return ImageFont.truetype(str(klasor / ad), px)
    raise SystemExit(f"Yazı tipi bulunamadı ({klasor}): {', '.join(YAZI_TIPLERI)}")


def yazi_katmani(metin: str, w: int, h: int, vurgu: str = "", renk: str = "pembe") -> Katman:
    """GTA tarzı altyazı: altta ortada, beyaz, koyu kenarlı ve yumuşak gölgeli yazı; `vurgu` kelimesi renkli."""
    from PIL import Image, ImageDraw

    px = max(10, round(YAZI_BOYU * h))
    font = _yazi_tipi(px)
    kenar = max(1, round(px / 17))
    tuval = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ciz = ImageDraw.Draw(tuval)
    satirlar = metin.split("\n")
    satir_h = round(px * 1.26)
    y = h - round(ALT_PAY * h) - satir_h * len(satirlar)
    for ln in satirlar:
        x = (w - ciz.textlength(ln, font=font)) / 2
        konum = 0
        for parca in re.findall(r"\S+|\s+", ln):  # kelime kelime (vurgu rengi için); konum tam satırdan
            if not parca.isspace():
                dolgu = RENKLER[renk] if vurgu and parca.strip(NOKTALAMA) == vurgu else RENKLER["beyaz"]
                ciz.text((x + ciz.textlength(ln[:konum], font=font), y), parca, font=font, fill=(*dolgu, 255),
                         stroke_width=kenar, stroke_fill=(0, 0, 0, 255))
            konum += len(parca)
        y += satir_h
    arr = np.asarray(tuval).astype(np.float32) / 255.0
    rgb, a = arr[:, :, :3], arr[:, :, 3]
    kay = max(1, px // 14)
    golge = np.roll(cv2.GaussianBlur(a, (0, 0), max(1.0, px * 0.06)), (kay, kay), axis=(0, 1)) * 0.75
    toplam = a + golge * (1.0 - a)  # gölge yazının altında
    ys, xs = np.nonzero(toplam > 0.004)
    if ys.size == 0:
        raise ValueError(f"Yazı çizilemedi: {metin!r}")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    eklenen = np.round(rgb[y0:y1, x0:x1, ::-1] * a[y0:y1, x0:x1, None] * 255.0).astype(np.uint8)
    kalan = np.round((1.0 - toplam[y0:y1, x0:x1]) * 255.0).astype(np.uint8)
    return Katman(x0, y0, np.ascontiguousarray(np.repeat(kalan[:, :, None], 3, axis=2)),
                  np.ascontiguousarray(eklenen))


def bindir(kare: np.ndarray, katman: Katman) -> None:
    """Yazıyı kareye yerinde bindirir: zemin x kalan pay + yazı."""
    h, w = katman.carpan.shape[:2]
    bolge = kare[katman.y:katman.y + h, katman.x:katman.x + w]
    cv2.multiply(bolge, katman.carpan, dst=bolge, scale=1.0 / 255.0)
    cv2.add(bolge, katman.eklenen, dst=bolge)


def _sarmal_bulanik(img: np.ndarray, sigma: float) -> np.ndarray:
    """Yatayda sarmalı Gauss bulanıklığı: şeridin iki ucu dikişsiz birleşsin."""
    pay = int(math.ceil(3 * sigma)) + 1
    genis = np.concatenate([img[:, -pay:], img, img[:, :pay]], axis=1)
    return cv2.GaussianBlur(genis, (0, 0), sigma)[:, pay:-pay]


def _daire(img: np.ndarray, x: int, y: int, r: int, renk: tuple[float, float, float]) -> None:
    """Dolu daire; şeridin ucundan taşan kısmı öbür uçtan girer."""
    s = img.shape[1]
    for dx in (-s, 0, s):
        cv2.circle(img, (x + dx, y), r, renk, -1, cv2.LINE_AA)


def _serit(img: np.ndarray, w: int) -> np.ndarray:
    """Kayan şerit: sonuna başından `w` piksel eklenir; her kayma konumunda kesintisiz `w` piksel alınabilsin."""
    img = np.clip(img, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(np.concatenate([img, img[:, :w]], axis=1))


def _serit_gece(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """Gece sokağı: koyu binalar, aydınlık pencereler, neon ve sokak ışıkları."""
    s = 2 * w
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    ust, alt = np.array([62, 36, 34], np.float32), np.array([26, 20, 24], np.float32)  # BGR
    img = np.repeat(ust * (1 - y) + alt * y, s, axis=1)
    pencere = ((120, 215, 250), (235, 225, 170), (90, 170, 240))
    neon = ((190, 80, 235), (230, 200, 60), (60, 140, 250), (120, 235, 130))
    x = 0
    while x < s:
        gen, yuk = int(rng.integers(110, 280)), int(rng.integers(int(h * 0.35), int(h * 0.95)))
        img[h - yuk:, x:x + gen] *= float(rng.uniform(0.5, 0.85))
        for _ in range(int(rng.integers(2, 9))):
            px = x + int(rng.integers(8, max(9, gen - 20)))
            py = h - yuk + int(rng.integers(6, max(7, yuk - 12)))
            cv2.rectangle(img, (px, py), (px + 10, py + 7), pencere[int(rng.integers(len(pencere)))], -1)
        x += gen + int(rng.integers(0, 50))
    for _ in range(s // 110):
        _daire(img, int(rng.integers(0, s)), int(rng.integers(0, h)), int(rng.integers(8, 30)),
               neon[int(rng.integers(len(neon)))])
    img = _sarmal_bulanik(img, 3.5)
    for _ in range(s // 160):  # keskin küçük ışıklar (far, lamba)
        _daire(img, int(rng.integers(0, s)), int(rng.integers(0, h)), int(rng.integers(2, 5)), (235, 240, 250))
    return _serit(_sarmal_bulanik(img, 0.8), w)


def _serit_otoyol(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """Gündüz otoyolu: kumlu açık asfalt, sarı çizgi, yazının arkasından geçen kesikli şerit çizgisi, yamalar."""
    s = 2 * w
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    g = rng.normal(0.0, 12.0, (h, s)).astype(np.float32) + (150.0 - 34.0 * y)
    benek = rng.random((h, s)) < 0.02
    g[benek] = rng.uniform(185, 235, int(benek.sum())).astype(np.float32)
    for x in range(s // 9, s, s // 3):  # asfalt yamaları (koyu)
        g[int(h * 0.3):, x:x + s // 11] *= 0.84
    img = np.stack([g * 0.97, g, g * 1.02], axis=2)
    cv2.line(img, (0, int(h * 0.20)), (s, int(h * 0.20)), (70, 200, 228), 6)
    for x in range(0, s, s // 8):
        cv2.line(img, (x, int(h * 0.66)), (x + s // 20, int(h * 0.66)), (218, 218, 218), 8)
    return _serit(_sarmal_bulanik(img, 0.9), w)


def _serit_ic(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """Loş iç mekân: sıcak tonlu yumuşak ışık bantları, koyu mobilya, açık pencereler."""
    s = 2 * w
    x = np.linspace(0.0, 6 * math.pi, s, endpoint=False, dtype=np.float32)[None, :]
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    v = 74.0 + 30.0 * np.sin(x) * (1.0 - 0.6 * y) - 22.0 * y
    img = np.stack([v * 0.62, v * 0.80, v], axis=2)
    for x0 in range(140, s - 200, s // 5):
        cv2.rectangle(img, (x0, int(h * 0.30)), (x0 + 190, h), (26, 30, 38), -1)
    for x0 in range(520, s - 160, s // 3):
        cv2.rectangle(img, (x0, int(h * 0.05)), (x0 + 150, int(h * 0.45)), (205, 222, 236), -1)
    img += rng.normal(0.0, 2.0, img.shape).astype(np.float32)
    return _serit(_sarmal_bulanik(img, 1.6), w)


_SERITLER: dict[str, Callable[[int, int, np.random.Generator], np.ndarray]] = {
    "gece": _serit_gece, "otoyol": _serit_otoyol, "ic": _serit_ic,
}


def _parilti(r: int, renk: tuple[int, int, int], guc: float = 1.0) -> np.ndarray:
    """Yumuşak ışık lekesi (BGR)."""
    yy, xx = np.mgrid[-2 * r:2 * r + 1, -2 * r:2 * r + 1].astype(np.float32)
    g = np.exp(-(xx * xx + yy * yy) / (2.0 * (0.8 * r) ** 2)) * guc
    return np.clip(g[:, :, None] * np.array(renk, np.float32), 0, 255).astype(np.uint8)


def _golge(gen: int, yuk: int, koyuluk: int) -> np.ndarray:
    """Yumuşak kenarlı koyu dikdörtgen (araç, köprü gölgesi): kareden çıkarılır."""
    pay = 12
    g = np.zeros((yuk + 2 * pay, gen + 2 * pay), np.float32)
    g[pay:-pay, pay:-pay] = koyuluk
    g = cv2.GaussianBlur(g, (0, 0), 5.0)
    return np.repeat(g[:, :, None], 3, axis=2).astype(np.uint8)


@dataclass
class Isik:
    """Zeminin üstünde kendi hızıyla gezen ışık ya da gölge."""

    doku: np.ndarray
    cikar: bool  # True: gölge (kareden çıkarılır), False: ışık (eklenir)
    x0: float
    y0: float
    hiz: float  # yatay hız, px/sn (eksi: sola)
    salinim: float = 0.0  # dikey salınım genliği (px)
    periyot: float = 3.0

    def ciz(self, kare: np.ndarray, t: float) -> None:
        hh, ww = self.doku.shape[:2]
        yuk, gen = kare.shape[:2]
        x = int((self.x0 + self.hiz * t) % (gen + ww)) - ww
        y = int(self.y0 + self.salinim * math.sin(2 * math.pi * t / self.periyot))
        a, b, c, d = max(0, x), max(0, y), min(gen, x + ww), min(yuk, y + hh)
        if a >= c or b >= d:
            return
        bolge, parca = kare[b:d, a:c], self.doku[b - y:d - y, a - x:c - x]
        if self.cikar:
            cv2.subtract(bolge, parca, dst=bolge)
        else:
            cv2.add(bolge, parca, dst=bolge)


def _isiklar(zemin: str, w: int, h: int, rng: np.random.Generator) -> list[Isik]:
    def x0() -> float:
        return float(rng.uniform(0, w))

    if zemin == "gece":
        return [Isik(_parilti(20, (200, 235, 255)), False, x0(), 0.20 * h, -260.0),  # karşıdan gelen far
                Isik(_parilti(14, (60, 60, 235)), False, x0(), 0.42 * h, 150.0),  # stop lambası
                Isik(_parilti(34, (190, 120, 40), 0.8), False, x0(), 0.05 * h, 25.0, 12.0, 2.6)]
    if zemin == "otoyol":
        return [Isik(_golge(260, 80, 70), True, x0(), -0.05 * h, -170.0),  # yandaki araç
                Isik(_golge(420, h, 38), True, x0(), -12.0, -520.0),  # üstten geçen gölge
                Isik(_parilti(9, (255, 255, 255)), False, x0(), 0.08 * h, -170.0)]
    return [Isik(_parilti(60, (60, 90, 120), 0.5), False, x0(), 0.02 * h, 14.0, 10.0, 2.2)]


class Sahne:
    """Sahte oyunun görüntüsü: kayan zemin + gezen ışıklar ve gölgeler + kumlanma (+ altyazı). Kare, kaynak kare
    sırasının (k) saf işlevidir: aynı tohum ve aynı k her zaman aynı pikselleri verir."""

    def __init__(self, boyut: tuple[int, int] = BOYUT, tohum: int = 1, fps: int = KAYNAK_FPS):
        self.w, self.h = boyut
        self.fps = fps
        rng = np.random.default_rng(tohum)
        self._serit = {ad: _SERITLER[ad](self.w, self.h, rng) for ad in ZEMINLER}
        self._isik = {ad: _isiklar(ad, self.w, self.h, rng) for ad in ZEMINLER}
        self._kum: list[tuple[np.ndarray, np.ndarray]] = []
        for _ in range(6):  # kareden kareye değişen kumlanma (video gürültüsü)
            g = rng.normal(0.0, 2.2, (self.h, self.w, 3))
            self._kum.append((np.clip(g, 0, 255).astype(np.uint8), np.clip(-g, 0, 255).astype(np.uint8)))

    def kare(self, zemin: str, k: int, katman: Katman | None = None) -> np.ndarray:
        serit = self._serit[zemin]
        kayma = int(ZEMIN_HIZI[zemin] * k / self.fps) % (serit.shape[1] - self.w)
        kare = serit[:, kayma:kayma + self.w].copy()
        for isik in self._isik[zemin]:
            isik.ciz(kare, k / self.fps)
        arti, eksi = self._kum[k % len(self._kum)]
        cv2.add(kare, arti, dst=kare)
        cv2.subtract(kare, eksi, dst=kare)
        if katman is not None:
            bindir(kare, katman)
        return kare


# --- sahte yakalayıcı ------------------------------------------------------------------------------------------------
class KareKaynagi:
    """Sahte oyun: duvar saatine göre o anki kareyi verir ve her satır için, o satırı içeren ilk karenin işlem
    hattına verildiği anı kaydeder. Önce yalnız zemin gösterilir (modeller yüklenirken), ısınma satırları `goster`
    ile açılıp kapanır, `basla` senaryoyu bir kare sınırına oturtarak başlatır.

    teslim_s > 0 ise Windows Graphics Capture taklit edilir: kare en az bu aralıkla "teslim edilir", arada alınan
    görüntü son teslim edilen karedir (`bekle` bir sonraki teslimi bekler). 0 ise her çağrı o anki kareyi verir
    (ekran bölgesi yakalama)."""

    def __init__(self, sahne: Sahne, cizelge: Cizelge, isinma: Sequence[Katman] = (), *, teslim_s: float = 0.0,
                 saat: Callable[[], float] = time.perf_counter, uyu: Callable[[float], None] = time.sleep):
        self.sahne, self.cizelge, self.saat, self.uyu = sahne, cizelge, saat, uyu
        self.fps = cizelge.fps
        self.teslim_s = max(0.0, teslim_s)
        self._isinma = list(isinma)
        self._katman = [yazi_katmani(s.metin, sahne.w, sahne.h, s.vurgu, s.renk) for s in cizelge.satirlar]
        self.t_sifir = saat()
        self.k0: int | None = None  # senaryonun başladığı kaynak kare
        self._isinma_no: int | None = None
        self._isinma_k = 0  # ısınma satırı bu kareden itibaren geçerli
        self.ilk_kare: dict[int, float] = {}  # satır no → o satırı içeren ilk karenin verildiği an (saat())
        self.verilen = 0  # alınan görüntü sayısı
        self.uretilen = 0  # üretilen (birbirinden farklı) kare sayısı
        self.sentez_s = 0.0  # kare üretimine giden süre (ölçümün kendi yükü)
        self._son: tuple[int, np.ndarray, Satir | None] | None = None
        self._son_tik = -1
        self._kayit: dict[int, tuple[Any, float, int]] = {}  # id(kare) → (zayıf başvuru, verildiği an, satır no)

    # --- zaman ---
    def _tik(self, simdi: float) -> int:
        return int((simdi - self.t_sifir) / self.teslim_s) if self.teslim_s > 0 else -1

    def kare_no(self, simdi: float | None = None) -> int:
        """O an alınabilen kaynak karenin sırası (teslim aralığı varsa son teslim edilen)."""
        gecen = (self.saat() if simdi is None else simdi) - self.t_sifir
        if self.teslim_s > 0:
            gecen = int(gecen / self.teslim_s) * self.teslim_s
        return int(gecen * self.fps + 1e-6)

    @property
    def t0(self) -> float | None:
        """Senaryonun başladığı an (saat() cinsinden); başlamadıysa None."""
        return None if self.k0 is None else self.t_sifir + self.k0 / self.fps

    def belirme(self, satir: Satir) -> float:
        """Satırın oyunda belirdiği an: onu içeren ilk kaynak karenin başladığı an."""
        assert self.k0 is not None
        return self.t_sifir + (self.k0 + satir.kare_on) / self.fps

    def basla(self, gecikme_s: float = 0.1) -> float:
        """Senaryoyu `gecikme_s` sonraki kare sınırında başlatır; başlangıç anını döndürür."""
        self.k0 = int((self.saat() - self.t_sifir) * self.fps) + max(1, math.ceil(gecikme_s * self.fps))
        return self.t_sifir + self.k0 / self.fps

    def goster(self, isinma_no: int | None) -> None:
        """Isınma satırını gösterir (None: gizler); bir sonraki kareden itibaren geçerlidir."""
        self._isinma_k = int((self.saat() - self.t_sifir) * self.fps) + 1
        self._isinma_no = isinma_no

    # --- yakalayıcı arayüzü ---
    def ver(self) -> np.ndarray:
        """O anki kare (grab_roi). Aynı kaynak kare yeniden istenirse aynı dizi döner."""
        simdi = self.saat()
        k = self.kare_no(simdi)
        self._son_tik = self._tik(simdi)
        self.verilen += 1
        if self._son is not None and self._son[0] == k:
            return self._son[1]
        satir, katman, zemin = None, None, self.cizelge.bolumler[0][0]
        if self.k0 is not None and k >= self.k0:
            j = k - self.k0
            zemin = self.cizelge.zemin_at(min(j, self.cizelge.kare_sayisi - 1))
            satir = self.cizelge.satir_at(j)
            if satir is not None:
                katman = self._katman[satir.no - 1]
        elif self._isinma_no is not None and k >= self._isinma_k:
            katman = self._isinma[self._isinma_no]
        kare = self.sahne.kare(zemin, k, katman)
        bitti = self.saat()
        self.sentez_s += bitti - simdi
        self.uretilen += 1
        self._son = (k, kare, satir)
        if satir is not None and satir.no not in self.ilk_kare:
            self.ilk_kare[satir.no] = bitti
        self._kaydet(kare, bitti, satir.no if satir is not None else 0)
        return kare

    def bekle(self, zaman_asimi: float = 0.5) -> bool:
        """Yeni kare teslim edilene kadar bekler (wait_frame); teslim edildiyse True."""
        if self.teslim_s <= 0:
            self.uyu(max(0.0, min(zaman_asimi, 1.0 / self.fps)))
            return True
        son = self.saat() + max(0.0, zaman_asimi)
        while self._tik(self.saat()) <= self._son_tik:
            kalan = min(son, self.t_sifir + (self._son_tik + 1) * self.teslim_s) - self.saat()
            self.uyu(max(kalan, 0.0005))
            if self.saat() >= son:
                return self._tik(self.saat()) > self._son_tik
        return True

    # --- OCR çağrısının hangi kareyi okuduğu ---
    def _kaydet(self, kare: np.ndarray, t: float, satir_no: int) -> None:
        kimlik = id(kare)
        self._kayit[kimlik] = (weakref.ref(kare, lambda _r, k=kimlik: self._kayit.pop(k, None)), t, satir_no)

    def kare_bilgisi(self, img: Any) -> tuple[float, int] | None:
        """Görüntü bu kaynağın verdiği bir kareyse (ya da onun bir görünümüyse): (verildiği an, satır no; 0 = yazısız
        ya da ısınma)."""
        for _ in range(4):
            if not isinstance(img, np.ndarray):
                return None
            kayit = self._kayit.get(id(img))
            if kayit is not None and kayit[0]() is img:
                return kayit[1], kayit[2]
            img = img.base
        return None


def teslim_araligi(cap_mod: Any, fps: int) -> float:
    """Pencere yakalamada iki kare teslimi arasındaki en kısa süre (sn): ağacın kendi hesabı varsa o, yoksa
    yayımlanmış sürümdeki kural (kontrol hızının iki katı, en az 16 ms)."""
    hesap = getattr(cap_mod, "frame_interval_ms", None)
    if callable(hesap):
        try:
            return float(hesap(fps)) / 1000.0
        except Exception:
            log.debug("frame_interval_ms çağrılamadı", exc_info=True)
    return max(16, int(1000 / (max(1, fps) * 2))) / 1000.0


def yakalayici_sinifi(cap_mod: Any, kaynak: KareKaynagi, tur: str, capture_cfg: Any) -> type:
    """Ağacın yakalayıcı sınıfından (RegionCapturer / WindowCapturer) türetilmiş sahte yakalayıcı. Türetildiği
    için hattaki isinstance denetimleri gerçek yakalayıcıdaki yoldan geçer; görüntü alan ve pencereye dokunan
    yöntemler ezilir, hattın sonradan çağırmaya başlayabileceği diğer yöntemler gerçek sınıftan kalır."""
    pencere = tur == "window"
    taban = getattr(cap_mod, "WindowCapturer" if pencere else "RegionCapturer", object)
    rect = (0, 0, kaynak.sahne.w, kaynak.sahne.h)

    class SahteYakalayici(taban):  # type: ignore[misc, valid-type]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            try:  # gerçek sınıfın alanları da kurulsun (yalnız alan atar; yakalamayı start() başlatır)
                if pencere:
                    taban.__init__(self, 0, tuple(capture_cfg.region), int(capture_cfg.fps))
                else:
                    taban.__init__(self, rect)
            except Exception:
                log.debug("Gerçek yakalayıcının kurucusu çağrılamadı", exc_info=True)
            self.rect = rect
            self.hwnd = 0
            self.closed = False

        alive = property(lambda self: True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def grab_roi(self, *_a: Any, **_k: Any) -> np.ndarray:
            return kaynak.ver()

        def grab_full(self, timeout: float = 2.0) -> np.ndarray:
            return kaynak.ver()

        def set_region(self, region: Any) -> None:
            self.region = tuple(region)

        def set_rect(self, rect: Any) -> None:
            self.rect = tuple(int(v) for v in rect)

    # Kare teslimini bekleme (wait_frame) pencere yakalamada var. Ekran bölgesinde yalnız gerçek sınıfta da varsa
    # eklenir: hattın "bekleyebiliyor muyum" denetimi gerçek yakalayıcıdaki gibi sonuçlansın (gerçeğinki ekrana
    # dokunurdu).
    if pencere or callable(getattr(taban, "wait_frame", None)):
        def wait_frame(self: Any, timeout: float = 0.5, *_a: Any, **_k: Any) -> bool:
            return kaynak.bekle(float(timeout))

        SahteYakalayici.wait_frame = wait_frame  # type: ignore[attr-defined]
    return SahteYakalayici


# --- sahte çeviri motorları ------------------------------------------------------------------------------------------
def sahte_llm_sinifi(taban: type, hata: type[Exception], gecikme_ms: float, zaman_asimi_s: float,
                     oynama: float = 0.08) -> type:
    """Ağacın Translator sınıfından türetilmiş sahte yerel yapay zekâ: satır başına `gecikme_ms` (±oynama; metne
    bağlı, iki ağaçta aynı metin aynı süre) bekler, işaretli bir çeviri döndürür. Süre istek zaman aşımını aşıyorsa
    gerçek motor gibi zaman aşımı kadar bekleyip hata verir."""

    class SahteLlm(taban):  # type: ignore[misc, valid-type]
        key = "ollama"
        label = SAHTE_LLM_ETIKETI

        def __init__(self) -> None:
            self.model = "sahte-llm"
            self._dur = threading.Event()

        def prepare(self, status: Callable[[str], None]) -> None:
            status("Sahte yapay zekâ hazır")

        def _cevir(self, text: str) -> str:
            pay = zlib.crc32(text.encode("utf-8")) / 2 ** 32  # 0..1, metne bağlı
            sure = max(0.0, gecikme_ms / 1000.0 * (1.0 + oynama * (2.0 * pay - 1.0)))
            if self._dur.wait(min(sure, zaman_asimi_s)):
                raise hata("Ölçüm bitti; sahte çeviri yarıda kesildi.")
            if sure > zaman_asimi_s:
                raise hata(f"Sahte yapay zekâ zaman aşımı ({zaman_asimi_s:g} sn)")
            return f"TR: {text}"

        def translate(self, text: str, context: Any = ()) -> str:
            return self._cevir(text)

        def translate_marked(self, text: str, context: Any = ()) -> str:
            return self._cevir(text)  # [[...]] işaretleri gerçek yapay zekâdaki gibi çıktıda kalır

        def abort(self) -> None:
            pass  # hazırlık anlıktır

        def keep_warm(self, *_a: Any, **_k: Any) -> None:
            pass  # bellekte tutulacak model yok

        def durdur(self) -> None:
            """Süren ve sonraki çevirileri keser: ölçüm bitince hat beklemeden kapansın."""
            self._dur.set()

        def close(self) -> None:
            self._dur.set()

    return SahteLlm


def sahte_hizli_sinifi(taban: type, gecikme_ms: float) -> type:
    """Ağacın Translator sınıfından türetilmiş sahte hızlı çeviri (QuickMtTranslator'ın yerine): `gecikme_ms` uyur."""

    class SahteHizli(taban):  # type: ignore[misc, valid-type]
        key = "quickmt"
        label = SAHTE_HIZLI_ETIKETI

        def __init__(self, glossary: Any = None, device: str = "cpu", *_a: Any, **_k: Any) -> None:
            self.glossary, self.device, self.active_device = glossary, device, "cpu"

        def prepare(self, status: Callable[[str], None]) -> None:
            pass

        def translate(self, text: str, context: Any = ()) -> str:
            time.sleep(gecikme_ms / 1000.0)
            return f"HZ: {text}"

        def translate_marked(self, text: str, context: Any = ()) -> str:
            return self.translate(text, context)

    return SahteHizli


# --- ağaç ve ayarlar -------------------------------------------------------------------------------------------------
@dataclass
class Agac:
    """Ölçülen kaynak ağacı ve ondan içe aktarılan modüller."""

    yol: Path
    surum: str
    pl: Any  # altyazitr.pipeline
    config: Any
    capture: Any
    translate: Any
    detect: Any
    paths: Any
    ocr: Any = None
    ollama_runtime: Any = None

    def kod_ozeti(self) -> str:
        """Hattı belirleyen dosyaların özeti: hangi kod ölçüldü (çalışma ağacı ölçümler arasında değişebilir)."""
        h = hashlib.sha1()
        for ad in ("pipeline", "detect", "ocr", "translate", "config", "capture", "textproc"):
            try:
                h.update((self.yol / "altyazitr" / f"{ad}.py").read_bytes().replace(b"\r\n", b"\n"))
            except OSError:
                h.update(b"?")
        return h.hexdigest()[:12]


def agaci_yukle(yol: Path) -> Agac:
    """`altyazitr` paketini verilen ağaçtan içe aktarır. Paket başka yerden yüklenmişse (ya da yükleniyorsa)
    ölçüm yapılmaz: bir ağaçtan içe aktarıp ötekini ölçmek sonuçları sessizce bozar."""
    yol = yol.resolve()
    if not (yol / "altyazitr" / "__init__.py").is_file():
        raise SystemExit(f"Bu klasörde altyazitr paketi yok: {yol}")
    sys.path.insert(0, str(yol))
    import importlib

    paket = importlib.import_module("altyazitr")
    gercek = Path(paket.__file__ or "").resolve().parent
    if gercek != yol / "altyazitr":
        raise SystemExit(f"altyazitr başka yerden yüklendi ({gercek}); beklenen ağaç: {yol}")
    mod = {ad: importlib.import_module(f"altyazitr.{ad}")
           for ad in ("pipeline", "config", "capture", "translate", "detect", "paths", "ocr")}
    try:
        mod["ollama_runtime"] = importlib.import_module("altyazitr.ollama_runtime")
    except ImportError:
        mod["ollama_runtime"] = None
    for m in mod.values():
        if m is not None and Path(m.__file__ or "").resolve().parent != yol / "altyazitr":
            raise SystemExit(f"{m.__name__} başka ağaçtan yüklendi: {m.__file__}")
    return Agac(yol, str(getattr(paket, "__version__", "?")), mod["pipeline"], mod["config"], mod["capture"],
                mod["translate"], mod["detect"], mod["paths"], mod["ocr"], mod["ollama_runtime"])


# Bu projenin sahibinin ayar dosyası (1.2.1'in yazdığı biçim, settings_version 4): yalnız ölçümü ilgilendiren alanlar,
# gerisi varsayılan. Ön ayar bunu ağacın kendi yükleyicisinden geçirir: yeni sürümün taşımaları (ör. sürüm 5: tarama
# hızı 10 → 20, kararlılık 2 → 1) güncelleyen kullanıcıda olduğu gibi uygulanır.
KULLANICI_DOSYASI: dict[str, Any] = {
    "settings_version": 4,
    "capture": {"fps": 10},
    "ocr": {"detect_mode": "interval", "interval_ms": 500, "stable_frames": 2, "preprocess": "auto",
            "text_detector": True},
    "translate": {"engine": "ollama", "hybrid": False, "hybrid_wait_ms": 1200, "timeout_s": 6.0},
    "flow": {"max_lines": 3},
}
ON_AYARLAR: dict[str, dict[str, Any] | None] = {
    "varsayilan": None,  # Settings() varsayılanları (ağacın kendi varsayılanları)
    "kullanici": KULLANICI_DOSYASI,  # kullanıcının ayar dosyası, ağacın yükleyicisinden geçmiş haliyle
}


def _donustur(metin: str, ornek: Any) -> Any:
    """Komut satırındaki değeri, alanın şimdiki değerinin türüne çevirir."""
    if isinstance(ornek, bool):
        dusuk = metin.strip().casefold()
        if dusuk in ("1", "true", "evet", "acik", "açık", "on"):
            return True
        if dusuk in ("0", "false", "hayir", "hayır", "kapali", "kapalı", "off"):
            return False
        raise ValueError(f"doğru/yanlış bekleniyordu: {metin}")
    if isinstance(ornek, int):
        return int(metin)
    if isinstance(ornek, float):
        return float(metin.replace(",", "."))
    if isinstance(ornek, (list, dict)):
        return json.loads(metin)
    return metin


def ayar_ata(ayar: Any, yol: str, deger: Any, metinden: bool = False) -> bool:
    """`ocr.interval_ms` gibi bir alanı atar. Alan bu ağaçta yoksa False (atlanır); `metinden` ise değer alanın
    türüne çevrilir (çevrilemezse ValueError)."""
    bolum, _, alan = yol.partition(".")
    hedef = getattr(ayar, bolum, None) if alan else ayar
    alan = alan or bolum
    if hedef is None or not hasattr(hedef, alan):
        return False
    setattr(hedef, alan, _donustur(deger, getattr(hedef, alan)) if metinden else deger)
    return True


def _dosya_alanlari(veri: dict[str, Any]) -> list[tuple[str, Any]]:
    """Ayar dosyası içeriğinin alanları: [("ocr.interval_ms", 500), …] (sürüm alanı hariç)."""
    return [(f"{bolum}.{ad}", deger) for bolum, alanlar in veri.items() if isinstance(alanlar, dict)
            for ad, deger in alanlar.items()]


def dosyadan_yukle(config: Any, veri: dict[str, Any]) -> tuple[Any, Kayit]:
    """Ayar dosyası içeriği → ağacın kendi yükleyicisinden (config.load_settings; eski sürüm dosyasının taşınması
    dahil) geçmiş ayarlar. Yükleyici geçici bir dosyayı okur (config.SETTINGS_FILE kısa süre ona yönlendirilir);
    kullanıcının ayar dosyası okunmaz, yazılmaz. Yükleyici yoksa değerler doğrudan atanır.
    → (ayarlar, döküm: yükleyici, sürüm, taşınan alanlar)"""
    yukle = getattr(config, "load_settings", None)
    if callable(yukle) and hasattr(config, "SETTINGS_FILE"):
        with tempfile.TemporaryDirectory(prefix="hat_hizi_ayar_", ignore_cleanup_errors=True) as klasor:
            yol = Path(klasor) / "ayarlar.json"
            yol.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
            eski = config.SETTINGS_FILE
            config.SETTINGS_FILE = yol
            try:
                ayar = yukle()
            finally:
                config.SETTINGS_FILE = eski
        yukleyici = "load_settings"
    else:
        ayar = config.Settings()
        for alan, deger in _dosya_alanlari(veri):
            ayar_ata(ayar, alan, deger)
        yukleyici = "doğrudan"
    tasinan, okunmadi = [], []
    for alan, deger in _dosya_alanlari(veri):
        bolum, _, ad = alan.partition(".")
        if not hasattr(getattr(ayar, bolum, None), ad):
            continue  # bu ağaçta olmayan alan
        simdi = getattr(getattr(ayar, bolum), ad)
        if simdi != deger:
            tasinan.append(f"{alan}: {deger} → {simdi}")
            if alan in ("ocr.detect_mode", "translate.engine", "translate.hybrid"):
                okunmadi.append(alan)
    if okunmadi:  # bu alanları hiçbir taşıma değiştirmez: yükleyici dosyayı okuyamadı (varsayılana düştü)
        raise SystemExit(f"Ağacın ayar yükleyicisi kullanıcı ayarlarını okuyamadı ({', '.join(okunmadi)}).")
    return ayar, {"yukleyici": yukleyici, "dosya_surumu": veri.get("settings_version"),
                  "surum": getattr(ayar, "settings_version", None), "tasinan": tasinan}


def ayarlari_kur(config: Any, on_ayar: str, ustune: Sequence[str] = (), *, yakalama: str = "screen",
                 boyut: tuple[int, int] = BOYUT) -> tuple[Any, Kayit]:
    """Ölçümün ayarları: ön ayar (Settings() ya da ağacın yükleyicisinden geçmiş kullanıcı dosyası) + --set değerleri
    + sahte yakalamanın gerektirdikleri. Kullanıcının gerçek ayar dosyası okunmaz. → (ayarlar, döküm)."""
    dosya = ON_AYARLAR[on_ayar]
    dokum: Kayit = {"on_ayar": on_ayar, "uygulanan": [], "atlanan": []}
    if dosya is None:
        ayar = config.Settings()
    else:
        ayar, yukleme = dosyadan_yukle(config, dosya)
        dokum.update(yukleme)
    istenen: list[tuple[str, Any, bool]] = [("translate.engine", "ollama", False)]
    for kv in ustune:
        yol, esit, deger = kv.partition("=")
        if not esit or not yol.strip():
            raise SystemExit(f"--set biçimi bolum.alan=deger olmalı: {kv}")
        istenen.append((yol.strip(), deger.strip(), True))
    for yol, deger, metinden in istenen:
        try:
            oldu = ayar_ata(ayar, yol, deger, metinden)
        except ValueError as e:
            raise SystemExit(f"--set {yol}: {e}") from None
        (dokum["uygulanan"] if oldu else dokum["atlanan"]).append(f"{yol}={deger}")
    # sahte yakalama: gerçek pencere ya da ekran bölgesi aranmasın
    ayar.capture.mode = "window" if yakalama == "window" else "screen"
    ayar.capture.rect = [0, 0, boyut[0], boyut[1]]
    ayar.capture.window_exe = "hat_hizi_sahte.exe"
    return ayar, dokum


def ayar_dokumu(ayar: Any) -> Kayit:
    """Sonuca yazılan ayarlar. Anahtar ve sözlük alanları yazılmaz."""
    out: Kayit = {}
    for bolum in AYAR_BOLUMLERI:
        b = getattr(ayar, bolum, None)
        if b is not None and is_dataclass(b):
            out[bolum] = {f.name: getattr(b, f.name) for f in fields(b)
                          if "key" not in f.name and f.name not in ("glossary", "game_context")}
    return out


# --- ağaca takılan ölçüm noktaları -----------------------------------------------------------------------------------
class Yamalar:
    """Ağaca takılan sahteler ve ölçüm noktaları; `geri_al` hepsini söker."""

    def __init__(self) -> None:
        self.asil: Any = None  # asıl çeviri motoru (create_translator'ın döndürdüğü)
        self.asil_etiket = ""
        self.asil_bitti = threading.Event()  # asıl motorun prepare()'i döndü ya da hata verdi
        self.asil_hata: BaseException | None = None
        self.ocr_hazir = threading.Event()  # OCR motoru oluşturuldu (modeli yüklendi)
        # (yöntem, başlangıç, bitiş, okunan karenin (verildiği an, satır no) bilgisi ya da None)
        self.ocr_cagrilari: list[tuple[str, float, float, tuple[float, int] | None]] = []
        self.bulucu_cagrilari: list[tuple[float, float]] = []  # metin bulucu: (başlangıç, bitiş)
        self.asil_cagrilari: list[tuple[float, float, str, bool]] = []  # (başlangıç, bitiş, metin, başarılı mı)
        self.asil_mesgul = 0  # asıl motorda şu an süren çeviri sayısı
        self._geri: list[Callable[[], None]] = []

    def degistir(self, hedef: Any, ad: str, yeni: Any) -> None:
        eski = getattr(hedef, ad)
        setattr(hedef, ad, yeni)
        self._geri.append(lambda: setattr(hedef, ad, eski))

    def geri_al(self) -> None:
        while self._geri:
            self._geri.pop()()


def _motoru_olc(motor: Any, yama: Yamalar, kaynak: KareKaynagi | None) -> None:
    """OCR motorunun dışa açık yöntemlerini süre ölçen sarmalayıcılarla değiştirir (örnek üzerinde: motorun sınıfı
    aynı kalır). Yalnız en dıştaki çağrı kaydedilir: (yöntem, başlangıç, bitiş, okunan karenin bilgisi)."""
    yerel = threading.local()

    def sar(ad: str, asil: Callable[..., Any]) -> Callable[..., Any]:
        def sureli(*a: Any, **k: Any) -> Any:
            if getattr(yerel, "icte", False):
                return asil(*a, **k)
            yerel.icte = True
            bilgi = None
            if kaynak is not None:
                bilgi = next((b for b in (kaynak.kare_bilgisi(x) for x in a) if b is not None), None)
            t = time.perf_counter()
            try:
                return asil(*a, **k)
            finally:
                yama.ocr_cagrilari.append((ad, t, time.perf_counter(), bilgi))
                yerel.icte = False

        return sureli

    for ad in dir(motor):
        if ad.startswith("_") or ad == "close":
            continue
        try:
            asil = getattr(motor, ad)
            if callable(asil) and not isinstance(asil, type):
                setattr(motor, ad, sar(ad, asil))
        except Exception:
            log.debug("OCR motorunun %s yöntemi sarılamadı", ad, exc_info=True)


def _ceviriyi_olc(tr: Any, yama: Yamalar) -> None:
    """Asıl motorun translate / translate_marked çağrılarını kaydeder (örnek üzerinde; yalnız en dıştaki çağrı:
    temel sınıfın translate_marked'ı translate'i çağırır)."""
    yerel = threading.local()

    def sar(asil: Callable[..., Any]) -> Callable[..., Any]:
        def sureli(text: Any = "", *a: Any, **k: Any) -> Any:
            if getattr(yerel, "icte", False):
                return asil(text, *a, **k)
            yerel.icte = True
            yama.asil_mesgul += 1
            t, oldu = time.perf_counter(), False
            try:
                out = asil(text, *a, **k)
                oldu = True
                return out
            finally:
                yama.asil_cagrilari.append((t, time.perf_counter(), str(text), oldu))
                yama.asil_mesgul -= 1
                yerel.icte = False

        return sureli

    for ad in ("translate", "translate_marked"):
        asil = getattr(tr, ad, None)
        if callable(asil):
            try:
                setattr(tr, ad, sar(asil))
            except Exception:
                log.debug("Çeviri motorunun %s yöntemi sarılamadı", ad, exc_info=True)


def _ollamayi_kapat(agac: Any, yama: Yamalar, hata: type[Exception]) -> None:
    """Sahte modda güvenlik ağı: hat asıl motoru bilinmeyen bir yoldan kursa da gerçek Ollama'ya istek gitmez,
    Ollama başlatılmaz, model yüklenmez ya da boşaltılmaz. (Sahte motor hattın create_translator'ı üzerinden gelir;
    bu yalnız o yol değişirse devreye girer: o zaman asıl motor hazırlanamaz ve ölçüm anlaşılır bir hatayla durur.)"""
    ceviri = agac.translate

    def yasak(*_a: Any, **_k: Any) -> Any:
        raise hata("hat_hizi sahte modda gerçek Ollama'yı kullanmaz.")

    def bos(*_a: Any, **_k: Any) -> None:
        return None

    sinif = getattr(ceviri, "OllamaTranslator", None)
    if sinif is not None:
        for ad, yeni in (("prepare", yasak), ("translate", yasak), ("translate_marked", yasak), ("_post", yasak),
                         ("_request", yasak), ("_warm_up", yasak), ("keep_warm", bos), ("close", bos)):
            if ad in vars(sinif):
                yama.degistir(sinif, ad, yeni)
    for ad, yeni in (("ollama_list", bos), ("ollama_loaded", lambda *_a, **_k: False), ("release_ollama_models", bos),
                     ("ollama_pull", yasak)):
        if callable(getattr(ceviri, ad, None)):
            yama.degistir(ceviri, ad, yeni)
    calisma = getattr(agac, "ollama_runtime", None)
    if calisma is not None and callable(getattr(calisma, "ensure_running", None)):
        yama.degistir(calisma, "ensure_running", lambda *_a, **_k: False)


def yamalari_kur(agac: Agac, ayar: Any, kaynak: KareKaynagi | None, *, asil: str = "fake", asil_ms: float = 400.0,
                 hizli: str = "real", hizli_ms: float = 25.0) -> Yamalar:
    """Sahte motorları ve ölçüm noktalarını ağaca takar. Hepsi özellik denetimlidir: ağaçta olmayan nokta atlanır
    (o ölçü raporda boş kalır), ölçüm yine çalışır."""
    pl, ceviri = agac.pl, agac.translate
    yama = Yamalar()
    taban = getattr(ceviri, "Translator", object)
    hata = getattr(ceviri, "TranslatorError", RuntimeError)
    uzak = tuple(getattr(pl, "REMOTE_ENGINES", ("ollama", "gemini", "deepl")))
    sahte_hizli = sahte_hizli_sinifi(taban, hizli_ms)

    # hiçbir koşulda model indirilmesin
    if hasattr(ceviri, "download_quickmt"):
        def indirme_yok(*_a: Any, **_k: Any) -> None:
            raise hata("hat_hizi model indirmez: quickmt modeli bu makinede yok (--quick fake kullanın).")

        yama.degistir(ceviri, "download_quickmt", indirme_yok)
    if hizli == "fake" and hasattr(pl, "QuickMtTranslator"):
        yama.degistir(pl, "QuickMtTranslator", sahte_hizli)
    if asil == "fake":
        _ollamayi_kapat(agac, yama, hata)

    ilk_olustur = getattr(pl, "create_translator", None)
    if ilk_olustur is None:
        raise SystemExit("Bu ağaçta altyazitr.pipeline.create_translator yok: asıl motor değiştirilemiyor.")
    zaman_asimi = float(getattr(ayar.translate, "timeout_s", 6.0) or 6.0)

    def create_translator(cfg: Any, glossary: Any, *a: Any, **k: Any) -> Any:
        motor_adi = str(getattr(cfg, "engine", ""))
        if asil == "fake" and motor_adi in uzak:
            tr = sahte_llm_sinifi(taban, hata, asil_ms, zaman_asimi)()
        elif hizli == "fake" and motor_adi == "quickmt":
            tr = sahte_hizli(glossary)
        else:
            tr = ilk_olustur(cfg, glossary, *a, **k)
        yama.asil, yama.asil_etiket = tr, str(getattr(tr, "label", ""))
        ilk_hazirla = tr.prepare

        def prepare(*pa: Any, **pk: Any) -> Any:
            try:
                return ilk_hazirla(*pa, **pk)
            except BaseException as e:
                yama.asil_hata = e
                raise
            finally:
                yama.asil_bitti.set()

        tr.prepare = prepare
        _ceviriyi_olc(tr, yama)
        return tr

    yama.degistir(pl, "create_translator", create_translator)
    if callable(getattr(ceviri, "create_translator", None)):  # hat motoru modülün kendisinden de kurabilir
        yama.degistir(ceviri, "create_translator", create_translator)

    # OCR motoru: hattın içe aktardığı create_engine ve ocr modülününki (hangisinden kurulursa kurulsun izlenir).
    # İkisi de yoksa OCR motorunun nerede kurulduğu bilinmez: ölçüm yine yapılır, OCR çağrıları sayılmaz.
    def olcen(ilk_motor: Callable[..., Any]) -> Callable[..., Any]:
        def create_engine(*a: Any, **k: Any) -> Any:
            motor = ilk_motor(*a, **k)
            _motoru_olc(motor, yama, kaynak)
            yama.ocr_hazir.set()
            return motor

        return create_engine

    izlenen = False
    for mod in (pl, getattr(agac, "ocr", None)):
        ilk_motor = getattr(mod, "create_engine", None) if mod is not None else None
        if callable(ilk_motor):
            yama.degistir(mod, "create_engine", olcen(ilk_motor))
            izlenen = True
    if not izlenen:
        yama.ocr_hazir.set()

    bulucu = getattr(agac.detect, "TextDetector", None)
    ilk_kutular = getattr(bulucu, "boxes", None)
    if bulucu is not None and callable(ilk_kutular):
        def boxes(self: Any, *a: Any, **k: Any) -> Any:
            t = time.perf_counter()
            try:
                return ilk_kutular(self, *a, **k)
            finally:
                yama.bulucu_cagrilari.append((t, time.perf_counter()))

        yama.degistir(bulucu, "boxes", boxes)
    return yama


# --- çözümleme -------------------------------------------------------------------------------------------------------
def _anahtar(metin: str) -> str:
    """Karşılaştırma anahtarı (textproc.norm_key kuralı): küçük harf, yalnız harf / rakam / kesme işareti."""
    s = re.sub(r"[^a-z0-9' ]+", " ", metin.casefold())
    return re.sub(r"\s+", " ", s).strip()


def benzerlik(okunan: str, beklenen: str) -> float:
    """İki metnin benzerliği, 0..100 (noktalama ve büyük/küçük harf sayılmaz)."""
    return float(fuzz.ratio(_anahtar(okunan), _anahtar(beklenen)))


def _baglanma_puani(okunan: str, beklenen: str) -> float:
    """Olayı satıra bağlarken kullanılan puan: yarım okunan satır da (iki satırlı altyazının tek satırı, kesik
    metin) kendi satırına bağlansın diye kısmi eşleşme de sayılır; "yanlış okundu" kararı benzerlik'e göredir."""
    a, b = _anahtar(okunan), _anahtar(beklenen)
    puan = float(fuzz.ratio(a, b))
    if len(a) >= 6 and len(b) >= 6:
        puan = max(puan, float(fuzz.partial_ratio(a, b)) - 15.0)
    return puan


def olaylari_esle(satirlar: Sequence[Kayit], olaylar: Sequence[Kayit]) -> tuple[dict[int, list[Kayit]], list[Kayit]]:
    """Altyazı olaylarını senaryodaki satırlara bağlar: olayın İngilizce metni (`original`), o ana kadar belirmiş
    satırlar içinde en çok benzeyene yazılır (en az ESLESME_ESIGI; eşitlikte en son belirene). Hiçbirine benzemeyen
    olay ikinci listeye girer. → ({satır no: olayları, geliş sırasıyla}, eşleşmeyenler)"""
    bagli: dict[int, list[Kayit]] = {s["no"]: [] for s in satirlar}
    yetim: list[Kayit] = []
    for ev in olaylar:
        if ev.get("tur") != "subtitle":
            continue
        en_iyi, hedef = 0.0, None
        for s in satirlar:
            if ev["t"] < s["t_on"] - 0.01:
                continue  # satır daha belirmemişti
            puan = _baglanma_puani(str(ev.get("original") or ""), s["metin"])
            if puan > en_iyi or (puan == en_iyi and hedef is not None and puan > 0):
                en_iyi, hedef = puan, s
        if hedef is not None and en_iyi >= ESLESME_ESIGI:
            bagli[hedef["no"]].append(ev)
        else:
            yetim.append(ev)
    return bagli, yetim


def _okuma_cagrisi(ev: Kayit, satir_no: int, cagrilar: Sequence[Kayit], t_gonderim: float | None = None
                   ) -> Kayit | None:
    """Olaydaki okumayı yapan OCR çağrısı: (biliniyorsa) bu satırın karesini okuyan, süresi olaydaki `ocr_ms` ile
    tutan ve okuma çeviri kuyruğuna girmeden (bilinmiyorsa olaydan) önce biten çağrı. Aynı satırın süreleri neredeyse
    aynı birkaç okuması olabilir: gönderim anı biliniyorsa ondan hemen önce biten (OCR iş parçacığı okumayı biter
    bitmez ya da geçidin onayıyla gönderir), bilinmiyorsa satırın ilk tutan okuması seçilir."""
    hedef = ev.get("ocr_ms")
    if hedef is None:
        return None
    sinir = (ev["t"] if t_gonderim is None else t_gonderim) + 0.002
    pay = max(2.0, 0.03 * hedef)
    adaylar = [c for c in cagrilar if c["t_son"] <= sinir and c.get("satir") in (satir_no, None)
               and abs(c["ms"] - hedef) <= pay]
    if not adaylar:
        return None
    if t_gonderim is not None:
        return max(adaylar, key=lambda c: c["t_son"])
    return min(adaylar, key=lambda c: (c.get("satir") is None, c["t_son"]))


def satirlari_cozumle(satirlar: Sequence[Kayit], olaylar: Sequence[Kayit], asil_etiket: str,
                      ocr_cagrilari: Sequence[Kayit] = (), esik: float = BENZERLIK_ESIGI,
                      asil_cagrilari: Sequence[Kayit] = (), gonderimler: Sequence[Kayit] = ()
                      ) -> tuple[list[Kayit], list[Kayit]]:
    """Satır başına ölçüler. Girdiler düz sözlüklerdir, anlar senaryonun başından saniyedir:

    satirlar: no, metin (tek satıra indirilmiş), t_on, t_off, bosluk, t_kare (o satırı içeren ilk karenin hatta
    verildiği an; hiç verilmediyse None) ve rapora aynen geçen ek alanlar.
    olaylar: t, tur ("subtitle" | "gone" | "same"); altyazı olayında original, engine, line, ocr_ms, tr_ms,
    latency_ms, cached, vurgu.
    ocr_cagrilari: t_bas, t_son, ms, satir (okunan karedeki satır no, 0 = yazısız, None = bilinmiyor), t_kare.
    asil_cagrilari: asıl motorun çeviri çağrıları: t_bas, t_son, ms, metin.
    gonderimler: OCR'dan çeviri kuyruğuna giren öğeler (Pipeline._post_tr): t, tur ("sub" | "gone" | "same"), metin.

    → (satır kayıtları, hiçbir satıra bağlanamayan altyazı olayları)"""
    bagli, yetim = olaylari_esle(satirlar, olaylar)
    ilk_olay_ani = {no: evs[0]["t"] for no, evs in bagli.items() if evs}
    kimlik_sahibi: dict[Any, int] = {}  # hattın satır kimliği → onu ilk kullanan senaryo satırı
    kayitlar: list[Kayit] = []
    for i, s in enumerate(satirlar):
        evs = bagli[s["no"]]
        r: Kayit = {**s, "olay": len(evs), "durum": "kayıp", "first_ms": None, "main_ms": None, "ilk_motor": None,
                    "yukseltildi": False, "okunan": None, "benzerlik": None, "ilk_benzerlik": None, "yineleme": 0,
                    "birlesti": False, "ocr_ms": None, "kare_ms": None, "kapi_ms": None, "sira_ms": None,
                    "onay_ms": None, "ceviri_ms": None, "asil_sira_ms": None, "asil_sure_ms": None, "gone_ms": None,
                    "gone_yok": False, "erken_gone": 0, "vurgu_bulundu": None}
        if s.get("t_kare") is not None:
            r["kare_ms"] = (s["t_kare"] - s["t_on"]) * 1000
        if evs:
            ilk = evs[0]
            asil = next((e for e in evs if e.get("engine") == asil_etiket), None)
            r["first_ms"] = (ilk["t"] - s["t_on"]) * 1000
            r["main_ms"] = (asil["t"] - s["t_on"]) * 1000 if asil is not None else None
            r["ilk_motor"] = "asıl" if ilk.get("engine") == asil_etiket else "hızlı"
            r["yukseltildi"] = r["ilk_motor"] == "hızlı" and asil is not None
            r["okunan"] = evs[-1].get("original")
            r["benzerlik"] = benzerlik(str(evs[-1].get("original") or ""), s["metin"])
            r["ilk_benzerlik"] = benzerlik(str(ilk.get("original") or ""), s["metin"])
            r["durum"] = "tamam" if r["benzerlik"] >= esik else "yanlış"
            kimlikler = list(dict.fromkeys(e.get("line") for e in evs if e.get("line") is not None))
            r["yineleme"] = max(0, len(kimlikler) - 1)
            r["birlesti"] = any(kimlik_sahibi.setdefault(k, s["no"]) != s["no"] for k in kimlikler)
            r["ocr_ms"] = ilk.get("ocr_ms")
            if s.get("vurgu"):
                r["vurgu_bulundu"] = any(e.get("vurgu") for e in evs)
            # okumanın çeviri kuyruğuna girdiği an: OCR bitiminden oraya kadar geçen süre "onay"dır (erken okunan
            # kare, geçit yazının durduğunu doğrulayana kadar tutulur); oradan olaya kadar "çeviri"
            gonderim = next((g for g in gonderimler if g.get("tur") == "sub" and g.get("metin") == ilk.get("original")
                             and s["t_on"] - 0.01 <= g["t"] <= ilk["t"] + 0.002), None)
            t_cevir = gonderim["t"] if gonderim is not None else None
            # aşamalar: ilk olayın okuması hangi kareden, hangi OCR çağrısından geldi
            cagri = _okuma_cagrisi(ilk, s["no"], ocr_cagrilari, t_cevir)
            t_okunan = cagri.get("t_kare") if cagri is not None else None
            if t_okunan is None and ilk.get("latency_ms") is not None:
                t_okunan = ilk["t"] - ilk["latency_ms"] / 1000  # hattın kendi ölçümü: okunan kare → gösterim
            if t_okunan is not None and s.get("t_kare") is not None:
                r["kapi_ms"] = max(0.0, (t_okunan - s["t_kare"]) * 1000)
            if cagri is not None:
                if cagri.get("t_kare") is not None:
                    r["sira_ms"] = max(0.0, (cagri["t_bas"] - cagri["t_kare"]) * 1000)
                r["ocr_ms"] = cagri["ms"]
                if t_cevir is not None:
                    r["onay_ms"] = max(0.0, (t_cevir - cagri["t_son"]) * 1000)
                else:
                    t_cevir = cagri["t_son"]
            if t_cevir is not None:
                r["ceviri_ms"] = max(0.0, (ilk["t"] - t_cevir) * 1000)
            # asıl motorun bu satır için ilk çağrısı: okumanın çeviri kuyruğuna girmesinden (bilinmiyorsa OCR
            # bitiminden) çağrının başlamasına kadar = (karma modda) hızlı çeviri + asıl motorun önceki satırlarla
            # meşgul olduğu süre (hızlı konuşmada sıra)
            istek = next((c for c in asil_cagrilari if c["t_bas"] >= s["t_on"]
                          and _baglanma_puani(c["metin"].replace("[[", "").replace("]]", ""), s["metin"])
                          >= ESLESME_ESIGI), None)
            if istek is not None:
                r["asil_sure_ms"] = istek["ms"]
                if t_cevir is not None:
                    r["asil_sira_ms"] = max(0.0, (istek["t_bas"] - t_cevir) * 1000)
            # `gone` işareti: ardından ekran boş kalan satırda, satırın gösteriminden sonra ve sonraki satırın
            # gösteriminden önce gelen ilk işaret (işaretler olaylarla aynı sırada gelir)
            sonraki = min((ilk_olay_ani[n["no"]] for n in satirlar[i + 1:] if n["no"] in ilk_olay_ani),
                          default=math.inf)
            if s.get("bosluk", 0) >= GONE_EN_AZ_BOSLUK:
                bas = max(ilk["t"], s["t_off"] - 0.05)
                gone = next((e["t"] for e in olaylar if e.get("tur") == "gone" and bas <= e["t"] < sonraki), None)
                if gone is not None:
                    r["gone_ms"] = (gone - s["t_off"]) * 1000
                else:
                    r["gone_yok"] = True
            # satır oyunda dururken (ve çevirisi gösterilmişken) gelen `gone`: çeviri erken gizlenir
            r["erken_gone"] = sum(1 for e in olaylar if e.get("tur") == "gone"
                                  and ilk["t"] < e["t"] < min(s["t_off"] - 0.1, sonraki))
        kayitlar.append(r)
    return kayitlar, yetim


def yuzdelik(degerler: Sequence[float], p: float) -> float | None:
    """Doğrusal aradeğerlemeli yüzdelik (numpy'nin varsayılanı gibi); boş listede None."""
    if not degerler:
        return None
    s = sorted(degerler)
    k = (len(s) - 1) * p / 100
    alt, ust = math.floor(k), math.ceil(k)
    return s[alt] + (s[ust] - s[alt]) * (k - alt)


def _yuvarla(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(v, n)


def _dagilim(degerler: Sequence[float]) -> Kayit:
    return {"n": len(degerler), "p50": _yuvarla(yuzdelik(degerler, 50)), "p90": _yuvarla(yuzdelik(degerler, 90)),
            "en_yuksek": _yuvarla(max(degerler, default=None)),
            "ort": _yuvarla(statistics.fmean(degerler) if degerler else None)}


def _ort(kayitlar: Sequence[Kayit], alan: str) -> float | None:
    v = [r[alan] for r in kayitlar if r.get(alan) is not None]
    return _yuvarla(statistics.fmean(v)) if v else None


def ozet_hesapla(kayitlar: Sequence[Kayit], yetim: Sequence[Kayit] = ()) -> Kayit:
    """Satır kayıtlarından özet: gecikme dağılımları, sayımlar, aşama ortalamaları."""
    gosterilen = [r for r in kayitlar if r["first_ms"] is not None]
    gone = [r["gone_ms"] for r in kayitlar if r.get("gone_ms") is not None]
    return {
        "satir": len(kayitlar),
        "gosterilen": len(gosterilen),
        "kayip": sum(r["durum"] == "kayıp" for r in kayitlar),
        "kayip_no": [r["no"] for r in kayitlar if r["durum"] == "kayıp"],
        "yanlis": sum(r["durum"] == "yanlış" for r in kayitlar),
        "yinelenen": sum(r["yineleme"] > 0 for r in kayitlar),
        "birlesen": sum(bool(r.get("birlesti")) for r in kayitlar),
        "once_hizli": sum(r["ilk_motor"] == "hızlı" for r in kayitlar),
        "yukseltilen": sum(bool(r["yukseltildi"]) for r in kayitlar),
        "asilsiz": sum(r["first_ms"] is not None and r["main_ms"] is None for r in kayitlar),
        "eslesmeyen": len(yetim),
        "first_ms": _dagilim([r["first_ms"] for r in gosterilen]),
        "main_ms": _dagilim([r["main_ms"] for r in kayitlar if r["main_ms"] is not None]),
        # satırı içeren ilk karenin hatta verildiği andan (yakalama beklemesi hariç)
        "first_kareden_ms": _dagilim([r["first_ms"] - r["kare_ms"] for r in gosterilen
                                      if r.get("kare_ms") is not None]),
        "asamalar_ms": {ad: _ort(gosterilen, f"{ad}_ms")
                        for ad in ("kare", "kapi", "sira", "ocr", "onay", "ceviri")},
        # çeviri kuyruğuna girişten asıl motorun bu satırı çevirmeye başlamasına kadar (hızlı çeviri + meşguliyet)
        "asil_sira_ms": _dagilim([r["asil_sira_ms"] for r in kayitlar if r.get("asil_sira_ms") is not None]),
        "gone_ms": _dagilim(gone),
        "gone_yok": sum(bool(r.get("gone_yok")) for r in kayitlar),
        "erken_gone": sum(int(r.get("erken_gone") or 0) for r in kayitlar),
        "vurgu": {"satir": sum(r.get("vurgu_bulundu") is not None for r in kayitlar),
                  "bulunan": sum(r.get("vurgu_bulundu") is True for r in kayitlar)},
    }


# --- ölçüm -----------------------------------------------------------------------------------------------------------
class _GunlukToplayici(logging.Handler):
    """İşlem hattının uyarı ve hata günlüklerini toplar (kuyruk taşması, çeviri hatası, iş parçacığı hatası)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.kayitlar: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            metin = f"{record.levelname} {record.name}: {record.getMessage()}"
            if record.exc_info and record.exc_info[1] is not None:
                metin += f" [{type(record.exc_info[1]).__name__}: {record.exc_info[1]}]"
            if len(self.kayitlar) < 200:
                self.kayitlar.append(metin)
        except Exception:
            pass


def _olay_kaydi(t: float, tur: str, ev: dict | None) -> Kayit:
    """Hattın olayını çözümlemenin beklediği düz kayda çevirir (anlar sonradan senaryonun başına göre kaydırılır)."""
    r: Kayit = {"t": t, "tur": tur}
    if ev is not None:
        r.update(original=ev.get("original"), text=ev.get("text"), engine=ev.get("engine"), line=ev.get("line"),
                 ocr_ms=ev.get("ocr_ms"), tr_ms=ev.get("tr_ms"), latency_ms=ev.get("latency_ms"),
                 cached=bool(ev.get("cached")), vurgu=bool(ev.get("spans")))
    return r


def _hazirlik_denetimi(agac: Agac, args: argparse.Namespace) -> None:
    """Ölçümden önce: hiçbir şey indirilmeyecek, eksik model varsa anlaşılır bir mesajla çıkılır."""
    if args.quick == "real":
        indi = getattr(agac.translate, "quickmt_downloaded", None)
        if callable(indi) and not indi():
            raise SystemExit("quickmt modeli bu makinede yok; hat_hizi model indirmez. --quick fake kullanın.")
    try:
        import rapidocr

        modeller = Path(rapidocr.__file__).resolve().parent / "models"
        eksik = [n for n in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx") if not (modeller / n).is_file()]
        if eksik:
            _bilgi(f"Uyarı: RapidOCR modelleri pakette yok ({', '.join(eksik)}); motor indirmeye çalışabilir.")
    except ImportError:
        pass


def olcumu_calistir(args: argparse.Namespace) -> tuple[int, Kayit | None]:
    """Bir ölçüm: ağacı yükler, hattı sahte oyunla çalıştırır, sonucu döndürür. → (çıkış kodu, sonuç ya da None)"""
    baslangic = datetime.now()
    t_surec = time.perf_counter()
    agac = agaci_yukle(Path(args.tree))
    kod = agac.kod_ozeti()  # içe aktarılan (ölçülen) kod; ağaç ölçüm sırasında değişse de bu ölçülür
    _hazirlik_denetimi(agac, args)
    boyut = args.size
    ayar, dokum = ayarlari_kur(agac.config, args.preset, args.set or (), yakalama=args.capture, boyut=boyut)
    for atlanan in dokum["atlanan"]:
        _bilgi(f"Uyarı: bu ağaçta böyle bir ayar yok, atlandı: {atlanan}")
    if dokum.get("tasinan"):
        _bilgi(f"Ayar dosyası bu ağacın yükleyicisiyle taşındı: {'; '.join(dokum['tasinan'])}")
    cizelge = cizelge_kur(args.seed, args.limit)
    sahne = Sahne(boyut, args.seed)
    isinma = [yazi_katmani(m, sahne.w, sahne.h) for m in ISINMA_SATIRLARI]
    teslim = teslim_araligi(agac.capture, int(ayar.capture.fps)) if args.capture == "window" else 0.0

    from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Slot

    app = QCoreApplication.instance() or QCoreApplication([])
    gunluk = _GunlukToplayici()
    logging.getLogger().addHandler(gunluk)
    gecici = tempfile.TemporaryDirectory(prefix="hat_hizi_", ignore_cleanup_errors=True)
    pl = agac.pl
    asil_ms = float(args.main_ms)

    kaynak = KareKaynagi(sahne, cizelge, isinma, teslim_s=teslim)
    yama = yamalari_kur(agac, ayar, kaynak, asil=args.main, asil_ms=asil_ms, hizli=args.quick,
                        hizli_ms=float(args.quick_ms))
    # kullanıcının dosyalarına dokunulmaz: önbellek ve (hiç kullanılmasa da) ayar dosyası geçici klasöre bakar
    yama.degistir(pl, "CACHE_FILE", Path(gecici.name) / "ceviri_onbellegi.json")
    for mod in (agac.paths, agac.config):
        if hasattr(mod, "SETTINGS_FILE"):
            yama.degistir(mod, "SETTINGS_FILE", Path(gecici.name) / "ayarlar.json")

    hat = pl.Pipeline(ayar)
    gonderilen: list[tuple[float, str, Any]] = []  # OCR'dan çeviri kuyruğuna giren öğeler: (an, tür, metin)
    ilk_gonder = getattr(hat, "_post_tr", None)
    if callable(ilk_gonder):
        def _post_tr(item: Any, *a: Any, **k: Any) -> Any:
            try:
                ev = item[2] if item[0] == "sub" else None
                gonderilen.append((time.perf_counter(), str(item[0]), ev.get("text") if isinstance(ev, dict) else None))
            except Exception:
                pass
            return ilk_gonder(item, *a, **k)

        hat._post_tr = _post_tr  # noqa: SLF001 - örnek üzerinde: hat kendi yöntemini çağırdığında buraya gelir
    if hasattr(hat, "preview_enabled"):
        hat.preview_enabled = not args.no_preview
    sinif = yakalayici_sinifi(agac.capture, kaynak, args.capture, ayar.capture)
    if not hasattr(hat, "_open_capturer"):
        raise SystemExit("Bu ağaçta Pipeline._open_capturer yok: sahte yakalayıcı takılamıyor.")
    hat._open_capturer = lambda *_a, **_k: sinif()  # noqa: SLF001 - hattın yakalayıcıyı açtığı tek yer

    gercek = args.main != "fake"
    hazirlik_s = 660.0 if gercek else 120.0  # gerçek yapay zekânın belleğe yüklenmesi dakikaları bulabilir
    isinma_sinir_s = 30.0 if gercek else max(6.0, 2.0 * asil_ms / 1000.0 + 4.0)
    kuyruk_sinir_s = 2 * KUYRUK_SINIRI_S if gercek else KUYRUK_SINIRI_S

    class Kosucu(QObject):
        """Olayları geliş anıyla kaydeder ve ölçümün aşamalarını yürütür: yükleme → ısınma → ölçüm → kuyruk."""

        def __init__(self) -> None:
            super().__init__()
            self.olaylar: list[tuple[float, str, dict | None]] = []
            self.hatalar: list[str] = []
            self.bildirimler: list[str] = []
            self.durumlar: list[str] = []  # hattın son durum iletileri
            self.asama = "yukleme"
            self.neden = ""  # ölçüm yarıda kesildiyse nedeni
            self.t_asama = time.perf_counter()
            self.calisti = False
            self.is_no, self.is_acik = 0, False
            self.t0: float | None = None
            self.t_son = 0.0
            self.cpu: list[tuple[float, float, float, int]] = []  # (an, işlemci süresi, sentez süresi, alınan kare)

        @Slot(dict)
        def altyazi(self, ev: dict) -> None:
            self.olaylar.append((time.perf_counter(), "subtitle", dict(ev)))

        @Slot()
        def kayboldu(self) -> None:
            self.olaylar.append((time.perf_counter(), "gone", None))

        @Slot()
        def ayni(self) -> None:
            self.olaylar.append((time.perf_counter(), "same", None))

        @Slot(str)
        def hata(self, mesaj: str) -> None:
            self.hatalar.append(mesaj)

        @Slot(str)
        def bildirim(self, mesaj: str) -> None:
            self.bildirimler.append(mesaj)

        @Slot(str)
        def durum(self, mesaj: str) -> None:
            self.durumlar.append(mesaj)
            del self.durumlar[:-12]

        @Slot(bool)
        def calisiyor(self, v: bool) -> None:
            if v:
                self.calisti = True
            elif self.asama != "bitti":
                self._bitir("işlem hattı kendiliğinden durdu")

        def _gec(self, asama: str, simdi: float) -> None:
            self.asama, self.t_asama = asama, simdi

        def _bitir(self, neden: str = "") -> None:
            if self.asama == "bitti":
                return
            self.asama, self.neden = "bitti", neden
            zamanlayici.stop()
            durdur = getattr(yama.asil, "durdur", None)
            if callable(durdur):
                durdur()
            app.quit()

        def _ornekle(self, simdi: float) -> None:
            self.cpu.append((simdi, time.process_time(), kaynak.sentez_s, kaynak.verilen))

        def _isinma_olayi(self, sonra: float, metin: str, asil_sart: bool) -> bool:
            for t, tur, ev in self.olaylar:
                if tur == "subtitle" and t >= sonra and _baglanma_puani(str(ev.get("original") or ""), metin) >= 60:
                    if not asil_sart or ev.get("engine") == yama.asil_etiket:
                        return True
            return False

        @Slot()
        def tik(self) -> None:
            simdi = time.perf_counter()
            gecen = simdi - self.t_asama
            if self.hatalar:
                return self._bitir("işlem hattı hata bildirdi: " + self.hatalar[0].replace("\n", " | "))
            if self.asama == "yukleme":
                ocr_tamam = yama.ocr_hazir.is_set() or gecen >= OCR_BEKLEME_S
                if ocr_tamam and yama.asil_bitti.is_set():
                    if not yama.ocr_hazir.is_set():
                        _bilgi("Uyarı: OCR motorunun kurulduğu görülmedi (hat başka yoldan kuruyor olabilir); "
                               "OCR çağrıları ölçülmeyecek.")
                    if yama.asil_hata is not None:
                        return self._bitir(f"asıl çeviri motoru hazırlanamadı: {yama.asil_hata}")
                    _bilgi(f"Modeller hazır ({simdi - t_surec:.1f} sn). Isınma satırları gösteriliyor…")
                    self.is_no, self.is_acik = 0, True
                    kaynak.goster(0)
                    self._gec("isinma", simdi)
                elif gecen > hazirlik_s:
                    self._bitir(f"modeller {hazirlik_s:.0f} sn içinde hazır olmadı")
            elif self.asama == "isinma":
                metin = ISINMA_SATIRLARI[self.is_no]
                if self.is_acik:
                    geldi = self._isinma_olayi(self.t_asama, metin, asil_sart=True)
                    sinir = isinma_sinir_s + (ILK_ISINMA_EK_S if self.is_no == 0 else 0.0)  # ilk çıkarımlar yavaş
                    if (geldi and gecen >= ISINMA_EN_AZ_S) or gecen >= sinir:
                        if not self._isinma_olayi(self.t_asama, metin, asil_sart=False):
                            return self._bitir(f"ısınma satırı {sinir:.0f} sn içinde hiç gösterilmedi "
                                               "(hat altyazı üretmiyor)")
                        kaynak.goster(None)
                        self.is_acik = False
                        self._gec("isinma", simdi)
                else:
                    son_satir = self.is_no + 1 >= len(ISINMA_SATIRLARI)
                    if gecen >= (OLCUM_ONCESI_S if son_satir else ISINMA_ARA_S):
                        if son_satir:
                            self.t0 = kaynak.basla(0.1)
                            self.t_son = self.t0 + cizelge.sure_s
                            _bilgi(f"Ölçüm başladı: {len(cizelge.satirlar)} satır, {cizelge.sure_s:.1f} sn.")
                            self._gec("bekle", simdi)
                        else:
                            self.is_no, self.is_acik = self.is_no + 1, True
                            kaynak.goster(self.is_no)
                            self._gec("isinma", simdi)
            elif self.asama == "bekle":
                if simdi >= (self.t0 or 0.0):
                    self._ornekle(simdi)
                    self._gec("olcum", simdi)
            elif self.asama == "olcum":
                if simdi >= self.t_son:
                    self._ornekle(simdi)
                    self._gec("kuyruk", simdi)
            elif self.asama == "kuyruk":
                # Son olaylar beklenir: asıl motor çalışırken (sırada satır var) ve son olaydan ya da son çeviriden
                # sonra KUYRUK_SESSIZ_S geçene kadar; her satırın asıl çevirisi geldiyse beklenmez.
                son = max([self.t_son, *(t for t, tur, _ in self.olaylar if tur != "same"),
                           *(b for _a, b, _m, _o in yama.asil_cagrilari[-1:])])
                sustu = yama.asil_mesgul <= 0 and simdi - son >= KUYRUK_SESSIZ_S
                if gecen >= kuyruk_sinir_s or (gecen >= KUYRUK_EN_AZ_S and (sustu or self._hepsi_tamam())):
                    self._bitir()

        def _hepsi_tamam(self) -> bool:
            """Her satırın asıl çevirisi geldi mi (kuyrukta beklenecek bir şey kalmadı mı)?"""
            bas = self.t0 or 0.0
            metinler = [str(ev.get("original") or "") for t, tur, ev in self.olaylar
                        if tur == "subtitle" and t >= bas and ev.get("engine") == yama.asil_etiket]
            return all(any(_baglanma_puani(m, s.duz) >= ESLESME_ESIGI for m in metinler) for s in cizelge.satirlar)

    kosucu = Kosucu()
    sira = Qt.ConnectionType.QueuedConnection  # arayüzdeki gibi: olay ana iş parçacığının kuyruğundan gelir
    hat.subtitle.connect(kosucu.altyazi, sira)
    hat.gone.connect(kosucu.kayboldu, sira)
    hat.same.connect(kosucu.ayni, sira)
    hat.error.connect(kosucu.hata, sira)
    if hasattr(hat, "status"):
        hat.status.connect(kosucu.durum, sira)
    if hasattr(hat, "notice"):
        hat.notice.connect(kosucu.bildirim, sira)
    hat.running_changed.connect(kosucu.calisiyor, sira)
    zamanlayici = QTimer()
    # Kaba zamanlayıcı: hassas olanı sürecin zamanlayıcı çözünürlüğünü 1 ms'ye çeker, hattın beklemeleri
    # uygulamadakinden farklı olurdu. Olayların geliş anı zamanlayıcıdan bağımsızdır (alıcıda ölçülür).
    zamanlayici.setTimerType(Qt.TimerType.CoarseTimer)
    zamanlayici.setInterval(20)
    zamanlayici.timeout.connect(kosucu.tik)

    _bilgi(f"Ağaç: {agac.yol} (sürüm {agac.surum}, kod {kod}). Modeller yükleniyor…")
    try:
        hat.start()
        zamanlayici.start()
        app.exec()
    except KeyboardInterrupt:
        kosucu.neden = "iptal edildi"
    finally:
        zamanlayici.stop()
        durdur = getattr(yama.asil, "durdur", None)
        if callable(durdur):
            durdur()
        try:
            if hasattr(hat, "stop_and_wait"):
                hat.stop_and_wait(6.0)
            else:
                hat.stop()
                time.sleep(1.0)
        except Exception:
            log.warning("İşlem hattı durdurulamadı", exc_info=True)
        for th in threading.enumerate():  # uzak çeviri iş parçacığı önbelleği geçici klasöre yazıp bitsin
            if th is not threading.current_thread() and th.name.startswith(("ceviri", "ocr", "yakalama")):
                th.join(3.0)
        app.processEvents()
        yama.geri_al()
        logging.getLogger().removeHandler(gunluk)
        gecici.cleanup()

    if kosucu.neden or kosucu.t0 is None or len(kosucu.cpu) < 2:
        _bilgi(f"Ölçüm tamamlanamadı: {kosucu.neden or 'senaryo başlamadı'}")
        if kosucu.durumlar:
            _bilgi("  Hattın son durum iletileri: " + " | ".join(kosucu.durumlar[-4:]))
        for u in gunluk.kayitlar[:12]:
            _bilgi(f"  {u}")
        return (130 if kosucu.neden == "iptal edildi" else 1), None

    t0 = kosucu.t0
    satirlar = [{"no": s.no, "metin": s.duz, "satir_sayisi": s.metin.count("\n") + 1, "vurgu": s.vurgu,
                 "zemin": s.zemin, "t_on": round(s.t_on, 4), "t_off": round(s.t_off, 4), "bosluk": round(s.bosluk, 3),
                 "t_kare": (kaynak.ilk_kare[s.no] - t0) if s.no in kaynak.ilk_kare else None}
                for s in cizelge.satirlar]
    olaylar = [_olay_kaydi(t - t0, tur, ev) for t, tur, ev in kosucu.olaylar if t >= t0]
    cagrilar = [{"ad": ad, "t_bas": a - t0, "t_son": b - t0, "ms": (b - a) * 1000,
                 "satir": bilgi[1] if bilgi is not None else None,
                 "t_kare": (bilgi[0] - t0) if bilgi is not None else None}
                for ad, a, b, bilgi in yama.ocr_cagrilari if a >= t0]
    asil_cagri = [{"t_bas": a - t0, "t_son": b - t0, "ms": (b - a) * 1000, "metin": m, "oldu": oldu}
                  for a, b, m, oldu in yama.asil_cagrilari if a >= t0]
    gonderimler = [{"t": t - t0, "tur": tur, "metin": m} for t, tur, m in gonderilen if t >= t0]
    kayitlar, yetim = satirlari_cozumle(satirlar, olaylar, yama.asil_etiket, cagrilar, args.threshold, asil_cagri,
                                        gonderimler)
    ozet = ozet_hesapla(kayitlar, yetim)

    (ta, ca, sa, va), (tb, cb, sb, vb) = kosucu.cpu[0], kosucu.cpu[-1]
    sure = tb - ta
    pencere = [c for c in cagrilar if c["t_bas"] <= cizelge.sure_s]
    bulucu = [(a, b) for a, b in yama.bulucu_cagrilari if t0 <= a <= t0 + cizelge.sure_s]
    olay_ocr = [e["ocr_ms"] for e in olaylar if e["tur"] == "subtitle" and e.get("ocr_ms") is not None]
    ozet.update({
        "sure_s": round(sure, 2),
        "cpu_s": round(cb - ca, 2),
        "cpu_yuzde": round(100.0 * (cb - ca) / sure, 1),  # bir çekirdeğin yüzdesi (100 = tam bir çekirdek)
        "sentez_s": round(sb - sa, 2),  # sahte oyunun kare üretimi (işlemci süresinin içinde)
        "yakalama_hz": round((vb - va) / sure, 1),
        "ocr_cagri": len(pencere),
        "ocr_cagri_ort_ms": _yuvarla(statistics.fmean(c["ms"] for c in pencere) if pencere else None),
        "ocr_toplam_s": round(sum(c["ms"] for c in pencere) / 1000, 2),
        "ocr_olay_ort_ms": _yuvarla(statistics.fmean(olay_ocr) if olay_ocr else None),
        "bulucu_cagri": len(bulucu),
        "bulucu_toplam_s": round(sum(b - a for a, b in bulucu), 2),
        "asil_cagri": len(asil_cagri),
        "asil_hata": sum(not c["oldu"] for c in asil_cagri),
        "asil_ort_ms": _yuvarla(statistics.fmean(c["ms"] for c in asil_cagri) if asil_cagri else None),
        "isaret": {"gone": sum(e["tur"] == "gone" for e in olaylar), "same": sum(e["tur"] == "same" for e in olaylar)},
        "kuyruk_atilan": _atilan(hat),
        "uyari": len(gunluk.kayitlar),
    })
    kosul = {"asil": args.main, "asil_ms": asil_ms if args.main == "fake" else None, "hizli": args.quick,
             "hizli_ms": float(args.quick_ms) if args.quick == "fake" else None, "yakalama": args.capture,
             "teslim_ms": round(teslim * 1000, 1) if teslim else None, "tohum": args.seed, "boyut": list(boyut),
             "kaynak_fps": cizelge.fps, "senaryo": cizelge.ozet(), "senaryo_s": round(cizelge.sure_s, 2),
             "onizleme": not args.no_preview, "benzerlik_esigi": args.threshold, "asil_etiket": yama.asil_etiket}
    etiket = args.label or f"{agac.yol.name}/{args.preset}/{args.main if args.main != 'fake' else int(asil_ms)}"
    sonuc: Kayit = {
        "bicim": BICIM, "arac": "hat_hizi", "etiket": etiket, "tarih": baslangic.isoformat(timespec="seconds"),
        "agac": str(agac.yol), "surum": agac.surum, "kod": kod, "kod_sonra": agac.kod_ozeti(), "kosul": kosul,
        "ayar_dokumu": dokum, "ayarlar": ayar_dokumu(ayar), "toplam_s": round(time.perf_counter() - t_surec, 1),
        "ozet": ozet, "satirlar": kayitlar,
        "eslesmeyen": [{"t": e["t"], "original": e.get("original"), "engine": e.get("engine")} for e in yetim],
        "olaylar": [e for e in olaylar if e["tur"] != "same"],
        "ocr_cagrilari": cagrilar, "asil_cagrilari": asil_cagri,
        "gonderimler": [g for g in gonderimler if g["tur"] != "same"],
        "uyarilar": gunluk.kayitlar[:50], "bildirimler": kosucu.bildirimler[:10],
    }
    if sonuc["kod_sonra"] != kod:
        _bilgi(f"Uyarı: ağacın kodu ölçüm sırasında değişti ({kod} → {sonuc['kod_sonra']}); ölçülen, başlangıçtaki "
               "koddur (modüller başta içe aktarıldı).")
    return 0, _sade(sonuc)


def _sade(v: Any) -> Any:
    """JSON için: kayan noktalı sayılar 3 basamağa yuvarlanır (anlar ms, süreler mikrosaniye duyarlığında kalır)."""
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, dict):
        return {k: _sade(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sade(x) for x in v]
    return v


_SATIR_ALANLARI = ("no", "metin", "satir_sayisi", "vurgu", "zemin", "t_on", "t_off", "bosluk", "t_kare")


def yeniden_cozumle(sonuc: Kayit) -> Kayit:
    """Kaydedilmiş bir sonucu ham verisinden (senaryo satırları, olaylar, OCR ve asıl motor çağrıları, gönderimler)
    bu sürümün çözümlemesiyle yeniden hesaplar; ölçüm başına değerler (işlemci, çağrı sayıları) korunur."""
    satirlar = [{k: r[k] for k in _SATIR_ALANLARI if k in r} for r in sonuc["satirlar"]]
    kosul = sonuc["kosul"]
    kayitlar, yetim = satirlari_cozumle(
        satirlar, sonuc.get("olaylar", []), kosul.get("asil_etiket", ""), sonuc.get("ocr_cagrilari", []),
        float(kosul.get("benzerlik_esigi", BENZERLIK_ESIGI)), sonuc.get("asil_cagrilari", []),
        sonuc.get("gonderimler", []))
    return _sade({**sonuc, "satirlar": kayitlar, "ozet": {**sonuc["ozet"], **ozet_hesapla(kayitlar, yetim)},
                  "eslesmeyen": [{"t": e["t"], "original": e.get("original"), "engine": e.get("engine")}
                                 for e in yetim]})


def _atilan(hat: Any) -> Kayit | None:
    """Hattın kuyruklarından taşıp atılan öğe sayıları (kuyruklar bu adlarla duruyorsa)."""
    out: Kayit = {}
    for ad in ("_ocr_q", "_tr_q", "_remote_q"):
        q = getattr(hat, ad, None)
        if q is not None and hasattr(q, "dropped"):
            out[ad.strip("_")] = int(q.dropped)
    return out or None


# --- rapor -----------------------------------------------------------------------------------------------------------
def _ms(v: Any) -> str:
    return "-" if v is None else f"{v:.0f}"


def _kisalt(metin: str, n: int) -> str:
    return metin if len(metin) <= n else metin[:n - 1] + "…"


def _satir_notu(r: Kayit) -> str:
    notlar: list[str] = []
    if r["durum"] == "kayıp":
        return "KAYIP (hiç gösterilmedi)"
    if r["durum"] == "yanlış":
        notlar.append(f"YANLIŞ OKUNDU: {r['okunan']!r}")
    elif r.get("ilk_benzerlik") is not None and r["ilk_benzerlik"] < r["benzerlik"]:
        notlar.append("ilk okuma bozuktu, düzeldi")
    if r["yineleme"]:
        notlar.append(f"{r['yineleme'] + 1} kez yeni satır")
    if r.get("birlesti"):
        notlar.append("önceki satırın kimliğiyle geldi")
    if r["yukseltildi"]:
        notlar.append(f"asıl çeviri +{r['main_ms'] - r['first_ms']:.0f} ms sonra")
    elif r["main_ms"] is None:
        notlar.append("asıl çeviri gelmedi")
    if r.get("gone_yok"):
        notlar.append("kayboldu işareti gelmedi")
    if r.get("erken_gone"):
        notlar.append("erken kayboldu işareti")
    if r.get("vurgu_bulundu") is False:
        notlar.append("vurgu bulunmadı")
    return "; ".join(notlar)


def _baslik(s: Kayit) -> str:
    k = s["kosul"]
    asil = f"sahte {k['asil_ms']:.0f} ms" if k["asil"] == "fake" else "Ollama"
    hizli = "gerçek quickmt" if k["hizli"] == "real" else f"sahte {k['hizli_ms']:.0f} ms"
    ocr, cev = s["ayarlar"].get("ocr", {}), s["ayarlar"].get("translate", {})
    kip = f"sabit aralık {ocr.get('interval_ms')} ms" if ocr.get("detect_mode") == "interval" else "akıllı algılama"
    karma = f"karma açık ({cev.get('hybrid_wait_ms')} ms)" if cev.get("hybrid") else "karma kapalı"
    tasinan = s["ayar_dokumu"].get("tasinan")
    ek = f"\n  ayar dosyası taşındı (sürüm {s['ayar_dokumu'].get('surum')}): {'; '.join(tasinan)}" if tasinan else ""
    return (f"{s['etiket']} — {s['tarih']} — sürüm {s['surum']} (kod {s['kod']})\n"
            f"  ağaç: {s['agac']}\n"
            f"  ön ayar {s['ayar_dokumu']['on_ayar']}: {kip}, {karma}, {s['ayarlar'].get('capture', {}).get('fps')} "
            f"kare/sn yakalama ({k['yakalama']}), kararlılık {ocr.get('stable_frames')} kare | asıl motor: {asil} | "
            f"hızlı: {hizli} | tohum {k['tohum']}{ek}")


def rapor(s: Kayit, ayrinti: bool = False) -> str:
    """İnsan için tablo: satır satır süreler ve özet."""
    o = s["ozet"]
    out = [_baslik(s), "",
           "Süreler ms; satırın oyunda belirdiği andan sayılır. kare: ilk karenin hatta verilmesi, kapı: okunan kareye "
           "kadar,", "sıra: OCR kuyruğu, onay: OCR bitiminden çeviri kuyruğuna (erken okuma geçidin onayını bekler),",
           "çeviri: çeviri kuyruğundan olayın gelişine (hızlı çeviri, asıl çeviriyi bekleme, Qt kuyruğu).", "",
           f"{'no':>3} {'belirme':>7} {'ekranda':>7} {'ilk':>6} {'asıl':>6} {'önce':>5} {'kare':>5} {'kapı':>5} "
           f"{'sıra':>5} {'OCR':>5} {'onay':>5} {'çeviri':>6} {'benz':>4}  metin"]
    for r in s["satirlar"]:
        out.append(
            f"{r['no']:>3} {r['t_on']:>7.2f} {r['t_off'] - r['t_on']:>7.2f} {_ms(r['first_ms']):>6} "
            f"{_ms(r['main_ms']):>6} {r['ilk_motor'] or '-':>5} {_ms(r['kare_ms']):>5} {_ms(r['kapi_ms']):>5} "
            f"{_ms(r['sira_ms']):>5} {_ms(r['ocr_ms']):>5} {_ms(r.get('onay_ms')):>5} {_ms(r['ceviri_ms']):>6} "
            f"{_ms(r['benzerlik']):>4}  "
            f"{_kisalt(r['metin'], 44)}")
        notu = _satir_notu(r)
        if notu:
            out.append(f"{'':>81}↳ {notu}")
    f, m, fk, a = o["first_ms"], o["main_ms"], o["first_kareden_ms"], o["asamalar_ms"]
    out += ["",
            f"Özet: {o['satir']} satır, {o['sure_s']} sn",
            f"  ilk gösterim   p50 {_ms(f['p50'])}   p90 {_ms(f['p90'])}   en yüksek {_ms(f['en_yuksek'])} ms"
            f"   ({f['n']} satır; ilk kareden sayılırsa p50 {_ms(fk['p50'])})",
            f"  asıl çeviri    p50 {_ms(m['p50'])}   p90 {_ms(m['p90'])}   en yüksek {_ms(m['en_yuksek'])} ms"
            f"   ({m['n']} satır)",
            f"  kayıp {o['kayip']}{' (no ' + ', '.join(map(str, o['kayip_no'])) + ')' if o['kayip_no'] else ''}"
            f" · yanlış okunan {o['yanlis']} · yinelenen {o['yinelenen']} · önce hızlı gösterilen {o['once_hizli']}"
            f" (sonradan asıl: {o['yukseltilen']}) · asıl çevirisi gelmeyen {o['asilsiz']}"
            f" · eşleşmeyen olay {o['eslesmeyen']}",
            f"  kayboldu işareti: ortanca {_ms(o['gone_ms']['p50'])} ms ({o['gone_ms']['n']} satır), "
            f"gelmeyen {o['gone_yok']}, erken gelen {o['erken_gone']}"
            f" · vurgulu kelime bulunan {o['vurgu']['bulunan']}/{o['vurgu']['satir']}",
            f"  aşamalar (ortalama): yakalama {_ms(a['kare'])} + kapı {_ms(a['kapi'])} + OCR sırası {_ms(a['sira'])}"
            f" + OCR {_ms(a['ocr'])} + onay {_ms(a.get('onay'))} + çeviri/bekleme {_ms(a['ceviri'])} ms",
            f"  OCR: {o['ocr_cagri']} çağrı, ortalama {_ms(o['ocr_cagri_ort_ms'])} ms (toplam {o['ocr_toplam_s']} sn)"
            f" · metin bulucu: {o['bulucu_cagri']} çağrı ({o['bulucu_toplam_s']} sn)",
            f"  asıl motor: {o['asil_cagri']} çağrı ({o['asil_hata']} hata), ortalama {_ms(o['asil_ort_ms'])} ms; "
            f"çeviri kuyruğundan asıl motora (hızlı çeviri + sıra): ortanca {_ms(o['asil_sira_ms']['p50'])}, "
            f"en yüksek {_ms(o['asil_sira_ms']['en_yuksek'])} ms",
            f"  işlemci: {o['cpu_s']} sn (%{o['cpu_yuzde']:.0f} çekirdek; bunun ~{o['sentez_s']} sn'si sahte oyun)"
            f" · yakalama {o['yakalama_hz']} kare/sn · günlükte {o['uyari']} uyarı"]
    if s["eslesmeyen"]:
        out.append("  Eşleşmeyen olaylar: "
                   + "; ".join(f"{e['t']:.1f} sn {e['original']!r}" for e in s["eslesmeyen"][:8]))
    if s["uyarilar"]:
        out.append("  Günlük: " + " | ".join(_kisalt(u, 110) for u in s["uyarilar"][:4]))
    if ayrinti:
        out += ["", "Olaylar (sn, senaryonun başından):"]
        for e in s["olaylar"]:
            if e["tur"] == "subtitle":
                motor = "asıl" if e.get("engine") == s["kosul"]["asil_etiket"] else "hızlı"
                out.append(f"  {e['t']:7.3f} altyazı  kimlik {e.get('line')}  {motor:<5} {e.get('original')!r}")
            else:
                out.append(f"  {e['t']:7.3f} {e['tur']}")
    return "\n".join(out) + "\n"


def _sonuc_oku(yol: Path) -> Kayit:
    try:
        s = json.loads(yol.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Sonuç dosyası bulunamadı: {yol}") from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"Sonuç dosyası geçerli JSON değil ({yol}): {e}") from None
    if not isinstance(s, dict) or s.get("arac") != "hat_hizi" or "ozet" not in s:
        raise SystemExit(f"Bu dosya hat_hizi sonucu değil: {yol}")
    return s


# ölçüm başına tek değer olan özet alanları: birleştirirken ortalanır
_OLCUM_BASINA = ("sure_s", "cpu_s", "cpu_yuzde", "sentez_s", "yakalama_hz", "ocr_cagri", "ocr_cagri_ort_ms",
                 "ocr_toplam_s", "ocr_olay_ort_ms", "bulucu_cagri", "bulucu_toplam_s", "asil_cagri", "asil_hata",
                 "asil_ort_ms", "uyari")


def sonuclari_birlestir(sonuclar: Sequence[Kayit]) -> Kayit:
    """Aynı koşulun yinelenen ölçümlerini (başka tohumlar) tek sonuç gibi birleştirir: satırlar havuzlanır
    (yüzdelikler bütün satırlardan, sayımlar toplam), ölçüm başına değerler (işlemci, OCR çağrısı) ortalanır."""
    ilk = sonuclar[0]
    satirlar = [r for x in sonuclar for r in x["satirlar"]]
    yetim = [e for x in sonuclar for e in x.get("eslesmeyen", [])]
    ozet = ozet_hesapla(satirlar, yetim)
    for alan in _OLCUM_BASINA:
        v = [x["ozet"][alan] for x in sonuclar if x["ozet"].get(alan) is not None]
        ozet[alan] = round(statistics.fmean(v), 1) if v else None
    kosul = {**ilk["kosul"], "tohum": [x["kosul"].get("tohum") for x in sonuclar],
             "senaryo": "+".join(sorted({str(x["kosul"].get("senaryo")) for x in sonuclar}))}
    return {**ilk, "etiket": f"{ilk['etiket']} x{len(sonuclar)}", "kosul": kosul, "ozet": ozet,
            "satirlar": satirlar, "eslesmeyen": yetim}


def _sonuclari_oku(yollar: str) -> Kayit:
    """Tek sonuç dosyası ya da virgülle ayrılmış birkaç dosya (birleştirilir)."""
    sonuclar = [_sonuc_oku(Path(y.strip())) for y in str(yollar).split(",") if y.strip()]
    if not sonuclar:
        raise SystemExit("Sonuç dosyası verilmedi.")
    return sonuclar[0] if len(sonuclar) == 1 else sonuclari_birlestir(sonuclar)


def _satir_ortalamalari(sonuc: Kayit) -> dict[int, Kayit]:
    """Satır no → o satırın (birleştirilmiş sonuçta bütün ölçümlerdeki) ortalama süreleri ve ilk gösteren motor."""
    out: dict[int, Kayit] = {}
    for r in sonuc["satirlar"]:
        out.setdefault(r["no"], {"metin": r["metin"], "kayitlar": []})["kayitlar"].append(r)
    for v in out.values():
        k = v.pop("kayitlar")
        ilk = [r["first_ms"] for r in k if r["first_ms"] is not None]
        asil = [r["main_ms"] for r in k if r["main_ms"] is not None]
        motorlar = [r["ilk_motor"] or "KAYIP" for r in k]
        v.update(first_ms=statistics.fmean(ilk) if ilk else None, main_ms=statistics.fmean(asil) if asil else None,
                 ilk_motor=max(dict.fromkeys(motorlar), key=motorlar.count))
    return out


def _fark(a: Any, b: Any, yuzde: bool = True) -> str:
    if a is None or b is None:
        return ""
    d = b - a
    ek = f" (%{100 * d / a:+.0f})" if yuzde and abs(a) >= 10 else ""  # küçük tabanda yüzde anlamsız
    return f"{d:+.0f}{ek}"


_OZET_SATIRLARI: tuple[tuple[str, Callable[[Kayit], Any]], ...] = (
    ("ilk gösterim p50 (ms)", lambda o: o["first_ms"]["p50"]),
    ("ilk gösterim p90 (ms)", lambda o: o["first_ms"]["p90"]),
    ("ilk gösterim en yüksek (ms)", lambda o: o["first_ms"]["en_yuksek"]),
    ("asıl çeviri p50 (ms)", lambda o: o["main_ms"]["p50"]),
    ("asıl çeviri p90 (ms)", lambda o: o["main_ms"]["p90"]),
    ("asıl çeviri en yüksek (ms)", lambda o: o["main_ms"]["en_yuksek"]),
    ("kayıp satır", lambda o: o["kayip"]),
    ("yanlış okunan satır", lambda o: o["yanlis"]),
    ("yinelenen satır", lambda o: o["yinelenen"]),
    ("önce hızlı gösterilen", lambda o: o["once_hizli"]),
    ("asıl çevirisi gelmeyen", lambda o: o["asilsiz"]),
    ("eşleşmeyen olay", lambda o: o["eslesmeyen"]),
    ("kayboldu işareti p50 (ms)", lambda o: o["gone_ms"]["p50"]),
    ("kayboldu işareti gelmeyen", lambda o: o["gone_yok"]),
    ("aşama: yakalama (ms)", lambda o: o["asamalar_ms"]["kare"]),
    ("aşama: kapı (ms)", lambda o: o["asamalar_ms"]["kapi"]),
    ("aşama: OCR sırası (ms)", lambda o: o["asamalar_ms"]["sira"]),
    ("aşama: OCR (ms)", lambda o: o["asamalar_ms"]["ocr"]),
    ("aşama: onay (ms)", lambda o: o["asamalar_ms"].get("onay")),
    ("aşama: çeviri/bekleme (ms)", lambda o: o["asamalar_ms"]["ceviri"]),
    ("kuyruk → asıl motor p50 (ms)", lambda o: o["asil_sira_ms"]["p50"]),
    ("kuyruk → asıl motor en yüksek", lambda o: o["asil_sira_ms"]["en_yuksek"]),
    ("OCR çağrısı", lambda o: o.get("ocr_cagri")),
    ("OCR çağrısı ortalama (ms)", lambda o: o.get("ocr_cagri_ort_ms")),
    ("metin bulucu çağrısı", lambda o: o.get("bulucu_cagri")),
    ("işlemci (% çekirdek)", lambda o: o.get("cpu_yuzde")),
)


def karsilastirma_raporu(a_yol: Path | str, b_yol: Path | str) -> str:
    """İki ölçümün öncesi (A) / sonrası (B) tablosu: özet ve satır satır süreler. Bir taraf virgülle ayrılmış
    birkaç dosyaysa (aynı koşul, başka tohumlar) o ölçümler birleştirilir: tek ölçümün ~20 satırlık örneklemi
    ortancayı ±50 ms oynatabilir."""
    a, b = _sonuclari_oku(str(a_yol)), _sonuclari_oku(str(b_yol))
    out = ["Karşılaştırma  A → B", "A: " + _baslik(a), "B: " + _baslik(b), ""]
    ka, kb = a["kosul"], b["kosul"]
    for ad, alan in (("senaryo", "senaryo"), ("asıl motor süresi", "asil_ms"), ("hızlı çeviri", "hizli"),
                     ("yakalama türü", "yakalama")):
        if ka.get(alan) != kb.get(alan):
            out.append(f"UYARI: {ad} farklı (A: {ka.get(alan)}, B: {kb.get(alan)}); süreler doğrudan kıyaslanamaz.")
    if a["ayar_dokumu"]["on_ayar"] != b["ayar_dokumu"]["on_ayar"]:
        out.append(f"UYARI: ön ayar farklı (A: {a['ayar_dokumu']['on_ayar']}, B: {b['ayar_dokumu']['on_ayar']}).")
    out.append(f"{'':<30} {'A':>8} {'B':>8}   fark (B − A)")
    for ad, al in _OZET_SATIRLARI:
        va, vb = al(a["ozet"]), al(b["ozet"])
        out.append(f"{ad:<30} {_ms(va):>8} {_ms(vb):>8}   {_fark(va, vb)}")
    out += ["", f"{'no':>3} {'A ilk':>7} {'B ilk':>7} {'fark':>7} {'A asıl':>7} {'B asıl':>7} {'fark':>7}  "
                f"{'A':>5} {'B':>5}  metin"]
    b_satir = _satir_ortalamalari(b)
    for no, ra in _satir_ortalamalari(a).items():
        rb = b_satir.get(no)
        if rb is None:
            continue
        out.append(
            f"{no:>3} {_ms(ra['first_ms']):>7} {_ms(rb['first_ms']):>7} "
            f"{_fark(ra['first_ms'], rb['first_ms'], False):>7} {_ms(ra['main_ms']):>7} {_ms(rb['main_ms']):>7} "
            f"{_fark(ra['main_ms'], rb['main_ms'], False):>7}  {ra['ilk_motor']:>5} {rb['ilk_motor']:>5}  "
            f"{_kisalt(ra['metin'], 40)}")
    return "\n".join(out) + "\n"


def ozet_tablosu(yollar: Sequence[Path]) -> str:
    """Birden çok ölçüm, her biri bir satır."""
    out = [f"{'ölçüm':<30} {'ilk p50':>7} {'p90':>6} {'maks':>6} | {'asıl p50':>8} {'p90':>6} {'maks':>6} | "
           f"{'kayıp':>5} {'yanlış':>6} {'yinel.':>6} {'önce hızlı':>10} {'asılsız':>7} | {'OCR ms':>6} "
           f"{'çağrı':>5} {'işlemci':>7}"]
    for yol in yollar:
        s = _sonuc_oku(yol)
        o = s["ozet"]
        f, m = o["first_ms"], o["main_ms"]
        out.append(
            f"{_kisalt(s['etiket'], 30):<30} {_ms(f['p50']):>7} {_ms(f['p90']):>6} {_ms(f['en_yuksek']):>6} | "
            f"{_ms(m['p50']):>8} {_ms(m['p90']):>6} {_ms(m['en_yuksek']):>6} | {o['kayip']:>5} {o['yanlis']:>6} "
            f"{o['yinelenen']:>6} {o['once_hizli']:>10} {o['asilsiz']:>7} | {_ms(o.get('ocr_cagri_ort_ms')):>6} "
            f"{_ms(o.get('ocr_cagri')):>5} {'%' + _ms(o.get('cpu_yuzde')):>7}")
    return "\n".join(out) + "\n"


# --- komut satırı ----------------------------------------------------------------------------------------------------
def _boyut(metin: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in metin.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"GENİŞLİKxYÜKSEKLİK olmalı: {metin}") from None
    if w < 320 or h < 60:
        raise argparse.ArgumentTypeError(f"en az 320x60 olmalı: {metin}")
    return w, h


def _argumanlar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python tools/hat_hizi.py", description=__doc__, add_help=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="help", help="bu yardımı gösterir")
    p.add_argument("--tree", default=str(KOK), metavar="KLASÖR",
                   help="altyazitr paketinin içe aktarılacağı kaynak ağacı (varsayılan: bu betiğin deposu)")
    p.add_argument("--preset", choices=tuple(ON_AYARLAR), default="varsayilan",
                   help="ön ayar: varsayilan (Settings() varsayılanları) | kullanici (sahibinin 1.2.1 ayar dosyası: "
                        "sabit aralık 500 ms, karma kapalı; ağacın yükleyicisinden taşınarak)")
    p.add_argument("--set", action="append", metavar="BÖLÜM.ALAN=DEĞER",
                   help="tek bir ayarı değiştirir, ör. ocr.interval_ms=250 (yinelenebilir)")
    p.add_argument("--main", choices=("fake", "ollama"), default="fake",
                   help="asıl motor: fake = sahte yapay zekâ (varsayılan) | ollama = ağacın gerçek Ollama motoru")
    p.add_argument("--main-ms", type=float, default=400.0, metavar="MS",
                   help="sahte yapay zekânın satır başına süresi (varsayılan 400; zayıf makine için 1500, 2500)")
    p.add_argument("--quick", choices=("real", "fake"), default="real",
                   help="hızlı çeviri: real = gerçek quickmt (varsayılan) | fake = sahte")
    p.add_argument("--quick-ms", type=float, default=25.0, metavar="MS", help="sahte hızlı çevirinin süresi")
    p.add_argument("--capture", choices=("screen", "window"), default="screen",
                   help="taklit edilen yakalama: screen = ekran bölgesi (varsayılan) | window = pencere (kare teslimi)")
    p.add_argument("--seed", type=int, default=1, metavar="N", help="senaryonun tohumu (varsayılan 1)")
    p.add_argument("--size", type=_boyut, default=BOYUT, metavar="GxY",
                   help=f"yakalanan bölgenin boyutu (varsayılan {BOYUT[0]}x{BOYUT[1]})")
    p.add_argument("--limit", type=int, metavar="N", help="yalnız ilk N satır (kısa deneme)")
    p.add_argument("--threshold", type=float, default=BENZERLIK_ESIGI, metavar="0-100",
                   help=f"bunun altındaki benzerlik yanlış okuma sayılır (varsayılan {BENZERLIK_ESIGI:.0f})")
    p.add_argument("--no-preview", action="store_true", help="hattın önizleme görüntüsünü kapatır")
    p.add_argument("--label", metavar="AD", help="sonuçtaki etiket (varsayılan: ağaç/ön ayar/asıl motor)")
    p.add_argument("--json", metavar="SONUC.json", help="makine tarafından okunur sonucun yazılacağı dosya")
    p.add_argument("--verbose", action="store_true", help="tablonun altına olay dökümünü de yazar")
    p.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                   help="iki --json sonucunun öncesi / sonrası tablosu (ölçüm yapmaz); bir taraf virgülle ayrılmış "
                        "birkaç dosya olabilir: aynı koşulun başka tohumlarla yinelenen ölçümleri birleştirilir")
    p.add_argument("--reanalyze", nargs="+", metavar="SONUC.json",
                   help="kaydedilmiş sonuçları ham verilerinden bu sürümün çözümlemesiyle yeniden hesaplar ve "
                        "dosyanın üstüne yazar (ölçüm yapmaz)")
    p.add_argument("--summary", nargs="+", metavar="SONUC.json",
                   help="verilen --json sonuçlarını tek tabloda özetler (ölçüm yapmaz)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    for akis in (sys.stdout, sys.stderr):  # Windows konsolunda (cp1252) Türkçe harfler bozulmasın
        if hasattr(akis, "reconfigure"):
            akis.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _argumanlar().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.compare:
            sys.stdout.write(karsilastirma_raporu(args.compare[0], args.compare[1]))
            return 0
        if args.summary:
            sys.stdout.write(ozet_tablosu([Path(y) for y in args.summary]))
            return 0
        if args.reanalyze:
            for yol in map(Path, args.reanalyze):
                sonuc = yeniden_cozumle(_sonuc_oku(yol))
                yol.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
                sys.stdout.write(rapor(sonuc, args.verbose))
                _bilgi(f"Yeniden çözümlendi: {yol}")
            return 0
        kod, sonuc = olcumu_calistir(args)
        if sonuc is None:
            return kod
        sys.stdout.write(rapor(sonuc, args.verbose))
        sys.stdout.flush()
        if args.json:
            hedef = Path(args.json)
            hedef.parent.mkdir(parents=True, exist_ok=True)
            hedef.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
            _bilgi(f"JSON sonucu yazıldı: {hedef}")
        return kod
    except KeyboardInterrupt:
        _bilgi("İptal edildi.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
