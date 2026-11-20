"""Kurulum görsellerini üretir: Inno Setup sihirbazının görselleri ve kurulum rehberinin çizimleri.

Kullanım:
  python tools/kurulum_gorselleri.py                       hepsi (sihirbaz + rehber)
  python tools/kurulum_gorselleri.py sihirbaz              yalnız Inno Setup görselleri
  python tools/kurulum_gorselleri.py rehber                yalnız 8 rehber çizimi
  python tools/kurulum_gorselleri.py yol2_ps5_bilgisayar yayin_obs     adla tek tek
  python tools/kurulum_gorselleri.py --liste               çizim adlarını yazar
  python tools/kurulum_gorselleri.py rehber --kolaj ONIZLEME.png
                                                           ayrıca 600 px genişlikte önizleme kolajı yazar

1) Sihirbaz (installer/, 24 bit BMP; Inno Setup'ın her sürümünde desteklenir):
  wizard-164.bmp / wizard-246.bmp / wizard-328.bmp   sol panel (karşılama ve bitiş sayfaları; %100/%150/%200)
  small-55.bmp  / small-83.bmp   / small-110.bmp      iç sayfaların sağ üst köşesi

2) Rehber çizimleri (1600×900, yatay). README için docs/gorseller/kurulum/<ad>.png, site için
   site/img/kurulum/<ad>.webp (kalite 85; aynı çizim). Başlık ve açıklama sayfadaki metindedir; çizimde
   yalnız cihazlar, kablolar ve 1-3 kelimelik etiketler vardır:
  neler_lazim           4 + 5 ızgara: her malzemenin çizimi ve adı ("Capture kartı" altında "isteğe bağlı")
  yol_secimi            "Nerede oynuyorsun?" ve dört eşit kart: Yol 1-4 rozeti, simge, kısa ad
  robz_vision_kurulum   dört adım: İndir ve kur → Yapay zekâ ile → Bölge seç → Başlat
  yol1_bilgisayar       bilgisayar —HDMI— monitör; bilgisayarın adında Robz Vision logosu
  yol2_ps5_bilgisayar   PS5 —Ethernet— modem —Ethernet— bilgisayar —HDMI— monitör; DualSense —USB-C— bilgisayar
  yol3_ps5_tv           yol 2 ile aynı yerleşim, monitör yerine televizyon
  yol4_capture          PS5 —HDMI— capture kartı —USB— bilgisayar —HDMI— TV ya da monitör
  yayin_obs             Robz Vision (logo, 127.0.0.1:8765) → OBS · Tarayıcı kaynağı → Yayın

Çizimler QPainter ile vektör olarak çizilir, sonra rastera dökülür; böylece yazılar her makinede aynı
görünür. Çıktı belirlenimcidir (aynı girdi, aynı dosya): rastgelelik, saat ya da ağ kullanılmaz.
Yazı tipleri Windows'ta hazır gelir: etiketler Bahnschrift SemiBold SemiCondensed (sitenin Barlow Semi
Condensed woff2 dosyalarını Qt yükleyemiyor; Bahnschrift aynı DIN çizgisindedir), gövde Segoe UI.
Her çizimden sonra kenar boşluğu (en az 80 px), çakışma ve 40 px'ten küçük etiket denetlenir.

Kurallar: marka logosu çizilmez (PlayStation, Sony, Rockstar, GTA VI, TV markaları, Kick/Twitch/YouTube).
"PS5" ve "DualSense" yalnız düz yazı etiketidir; markalar yalnız yazıyla geçer. Robz Vision logosu
altyazitr/data/logo.png'den olduğu gibi konur. Ekrandaki örnek altyazı projeye özgüdür, oyundan alıntı değildir.
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient)

from altyazitr.gui.tokens import C, F  # noqa: E402

LOGO = ROOT / "altyazitr" / "data" / "logo.png"
OUT = ROOT / "installer"
REHBER_PNG = ROOT / "docs" / "gorseller" / "kurulum"
REHBER_WEBP = ROOT / "site" / "img" / "kurulum"


def _to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data())))


# =====================================================================================================
# 1) Inno Setup sihirbaz görselleri
# =====================================================================================================

def wizard_panel(w: int, h: int) -> Image.Image:
    """Sol panel: mor-lacivert gece, logo, yazı markası ve tek bir gün batımı çizgisi."""
    k = w / 164.0
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    bg = QLinearGradient(QPointF(0, 0), QPointF(0, h))
    bg.setColorAt(0.0, QColor(C.PANEL))
    bg.setColorAt(1.0, QColor(C.BG))
    p.fillRect(0, 0, w, h, bg)
    # logonun arkasında çok hafif bir pembe ışıma (logonun neonuyla bütünleşir)
    glow = QRadialGradient(QPointF(w / 2, h * 0.36), w * 0.62)
    g0 = QColor(C.PINK)
    g0.setAlphaF(0.16)
    g1 = QColor(C.PINK)
    g1.setAlphaF(0.0)
    glow.setColorAt(0, g0)
    glow.setColorAt(1, g1)
    p.fillRect(0, 0, w, h, glow)

    logo = QImage(str(LOGO))
    side = int(w * 0.70)
    lx, ly = (w - side) / 2, h * 0.36 - side / 2
    p.drawImage(QRectF(lx, ly, side, side), logo)

    f = QFont(F.DISPLAY_SEMIBOLD)
    f.setPixelSize(int(19 * k))
    p.setFont(f)
    p.setPen(QColor(C.TEXT))
    p.drawText(QRectF(0, ly + side + 14 * k, w, 26 * k), Qt.AlignmentFlag.AlignHCenter, "Robz Vision")
    f2 = QFont(F.BODY)
    f2.setPixelSize(int(10.5 * k))
    p.setFont(f2)
    p.setPen(QColor(C.TEXT_2))
    p.drawText(QRectF(0, ly + side + 40 * k, w, 20 * k), Qt.AlignmentFlag.AlignHCenter, "Canlı Türkçe altyazı")

    line = QLinearGradient(QPointF(w * 0.18, 0), QPointF(w * 0.82, 0))
    line.setColorAt(0, QColor(C.PINK))
    line.setColorAt(1, QColor(C.ORANGE))
    p.fillRect(QRectF(w * 0.18, h - 26 * k, w * 0.64, max(2.0, 2 * k)), line)
    p.end()
    return _to_pil(img).convert("RGB")


def small_image(size: int) -> Image.Image:
    """İç sayfaların başlık alanındaki küçük logo (beyaz başlık zemini üzerinde)."""
    logo = Image.open(LOGO).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", (size, size), (255, 255, 255))
    bg.paste(logo, (0, 0), logo)
    return bg


def sihirbaz_gorselleri() -> None:
    OUT.mkdir(exist_ok=True)
    for w in (164, 246, 328):
        h = round(w * 314 / 164)
        wizard_panel(w, h).save(OUT / f"wizard-{w}.bmp")
    for s in (55, 83, 110):
        small_image(s).save(OUT / f"small-{s}.bmp")
    print("Kurulum sihirbazı görselleri yazıldı:", OUT)


# =====================================================================================================
# 2) Kurulum rehberi çizimleri
# =====================================================================================================
#
# Sadelik kuralları (her çizimde):
#  - Başlık, açıklama cümlesi, lejant, not ya da uyarı kutusu yok; onlar sayfadaki metinde.
#  - Etiketler 1-3 kelime, en az 40 px; cihazlar ortak bir taban çizgisinde ya da düzgün ızgarada.
#  - Kenarlarda en az KENAR px boşluk (denetlenir); öğeler üst üste binmez (denetlenir).
#  - Kablolar düz ya da dik açılı; adı kablonun ortasında bir hapta. Üç renk: HDMI pembe, USB-C
#    camgöbeği, Ethernet turkuaz.
#  - Ekranlar düz, koyu ve hafif tonlu; tek ayrıntı Robz Vision'ın Türkçe kutusu ve altındaki soluk
#    İngilizce satır.
#  - Gölge, parlama ve geçiş yok. Vurgu yalnız Robz Vision'ın olduğu yerde (logo).

W, H = 1600, 900
KENAR = 80                   # kenarlarda en az bu kadar boşluk

# Renkler (arayüz token'larından; cihaz renkleri yalnız bu çizimlerde)
ZEMIN = C.BG                 # #0C0B1D
GOVDE = C.RAISED             # #1C1A38 cihaz gövdesi
GOVDE_2 = C.HOVER            # #262448 cihaz ayrıntısı, ayak, ön panel
CIZGI = C.LINE_STRONG        # #3B3762 cihaz kenarı
KART = C.PANEL               # #13122A kart zemini (yol seçimi)
YAZI = C.TEXT                # #F5F3FB
YAZI_2 = C.TEXT_2            # #AEAAC6
SOLUK = C.TEXT_3             # #8A86A6 oklar, küçük ayrıntılar
EKRAN = "#1F1B42"            # ekran yüzeyi: koyu, hafif mor tonlu düz renk
TR_KUTU = "#0A0912"          # Robz Vision'ın Türkçe kutusu
ACIK = "#E4E2EE"             # PS5 kanatları ve DualSense gövdesi
ACIK_KENAR = "#A9A4C4"
SIYAH = "#14121E"            # PS5 çekirdeği, DualSense ortası
METAL = "#CBC8DC"            # fiş uçları
RJ45_GOVDE = "#D8F3EE"

KABLO_RENK = {"hdmi": C.PINK, "usb": C.CYAN, "eth": C.TURQUOISE}
KABLO_KALIN = 8.0
FIS_OLCEK = 1.0

ETIKET_PX = 42               # cihaz adları
HAP_PX = 40                  # kablo adları (hap içinde)
KUCUK_PX = 30                # yalnız "isteğe bağlı" ve yayın adresi

# Örnek altyazı (projeye özgü, oyundan alıntı değil)
ORNEK_TR = "Hadi, sahile gidelim!"
ORNEK_EN = "Come on, let's hit the beach!"

# Yazı tipleri (piksel boyutu ayrıca verilir)
YT_ETIKET = F.DISPLAY_CONDENSED      # "Bahnschrift SemiBold SemiCondensed"
YT_GOVDE = "Segoe UI"


def qc(renk: str | QColor, alfa: float = 1.0) -> QColor:
    c = QColor(renk)
    if alfa < 1.0:
        c.setAlphaF(alfa)
    return c


def yazi_tipi(tur: str, px: float) -> QFont:
    """tur: etiket (Bahnschrift) · govde | govde_k | kalin (Segoe UI)."""
    if tur == "etiket":
        f = QFont(YT_ETIKET)
    else:
        f = QFont(YT_GOVDE)
        if tur == "govde_k":
            f.setWeight(QFont.Weight.DemiBold)
        elif tur == "kalin":
            f.setWeight(QFont.Weight.Bold)
    f.setPixelSize(round(px))
    return f


class Tuval:
    """Tek bir çizim: QImage + QPainter, yazı yardımcıları ve taşma/çakışma kaydı."""

    def __init__(self, ad: str) -> None:
        self.ad = ad
        self.img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        self.img.fill(QColor(ZEMIN))
        self.p = QPainter(self.img)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.kutular: list[tuple[QRectF, str]] = []
        self.uyarilar: list[str] = []

    # ---- kayıt ve denetim -------------------------------------------------------------------
    def kaydet_alan(self, r: QRectF, ne: str) -> None:
        self.kutular.append((QRectF(r), ne))

    def denetle(self) -> list[str]:
        """Kayıtlı yazı/cihaz alanları üst üste biniyor mu, kenar boşluğuna taşıyor mu?"""
        sorunlar = list(self.uyarilar)
        guvenli = QRectF(KENAR - 0.5, KENAR - 0.5, W - 2 * KENAR + 1, H - 2 * KENAR + 1)
        for r, ne in self.kutular:
            if not guvenli.contains(r):
                sorunlar.append(f"kenar boşluğu {KENAR} px'ten az: {ne} {tuple(round(v) for v in r.getRect())}")
        for i in range(len(self.kutular)):
            for j in range(i + 1, len(self.kutular)):
                a, an = self.kutular[i]
                b, bn = self.kutular[j]
                # "kap:" bir kapsayıcıdır (kart); içindeki öğeler çakışma sayılmaz, taşanlar sayılır
                if (an.startswith("kap:") and a.contains(b)) or (bn.startswith("kap:") and b.contains(a)):
                    continue
                k = a.intersected(b)
                if k.width() > 1.5 and k.height() > 1.5:
                    sorunlar.append(f"çakışma: {an} × {bn}")
        return sorunlar

    def bitir(self) -> Image.Image:
        self.p.end()
        img = self.img.convertToFormat(QImage.Format.Format_RGB888)
        return Image.frombuffer("RGB", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGB",
                                img.bytesPerLine(), 1).copy()

    # ---- yazı ---------------------------------------------------------------------------------
    def yaz(self, x: float, y: float, metin: str, tur: str = "etiket", px: float = ETIKET_PX, renk=YAZI,
            hiza: str = "sol", satir: float = 1.25, kayit: str | None = "yazı") -> QRectF:
        """Tek satır yazar; (x, y) sol üst (hiza="orta" ise x ortadır). Kapladığı alanı döner."""
        if "\n" in metin:
            self.uyarilar.append(f"çok satırlı yazı: {metin!r}")
        if kayit and tur == "etiket" and px < 40 and not kayit.startswith("küçük"):
            self.uyarilar.append(f"etiket 40 px'ten küçük ({px:.0f}): {metin!r}")
        p = self.p
        f = yazi_tipi(tur, px)
        fm = QFontMetricsF(f)
        lh = px * satir
        sw = fm.horizontalAdvance(metin)
        sx = x - sw / 2 if hiza == "orta" else (x - sw if hiza == "sag" else x)
        p.setFont(f)
        p.setPen(qc(renk))
        p.drawText(QPointF(sx, y + (lh - (fm.ascent() + fm.descent())) / 2 + fm.ascent()), metin)
        r = QRectF(sx, y, sw, lh)
        if kayit:
            self.kaydet_alan(r, f"{kayit}: {metin[:28]!r}")
        return r


def yazi_genisligi(metin: str, tur: str = "etiket", px: float = ETIKET_PX) -> float:
    return QFontMetricsF(yazi_tipi(tur, px)).horizontalAdvance(metin)


# ---- temel şekiller ---------------------------------------------------------------------------------

def kalem(renk, kalin: float, kesik: list[float] | None = None) -> QPen:
    pen = QPen(qc(renk), kalin, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    if kesik:
        pen.setDashPattern(kesik)
    return pen


def kutu(p: QPainter, r: QRectF, rad: float, dolgu=None, cizgi=None, kalin: float = 2.0,
         kesik: list[float] | None = None) -> None:
    p.setBrush(qc(dolgu) if dolgu is not None else Qt.BrushStyle.NoBrush)
    p.setPen(kalem(cizgi, kalin, kesik) if cizgi is not None else Qt.PenStyle.NoPen)
    p.drawRoundedRect(r, rad, rad)


def daire(p: QPainter, c: QPointF, r: float, dolgu=None, cizgi=None, kalin: float = 2.0) -> None:
    p.setBrush(qc(dolgu) if dolgu is not None else Qt.BrushStyle.NoBrush)
    p.setPen(kalem(cizgi, kalin) if cizgi is not None else Qt.PenStyle.NoPen)
    p.drawEllipse(c, r, r)


def sekil(p: QPainter, yol: QPainterPath, dolgu=None, cizgi=None, kalin: float = 2.0) -> None:
    p.setBrush(qc(dolgu) if dolgu is not None else Qt.BrushStyle.NoBrush)
    p.setPen(kalem(cizgi, kalin) if cizgi is not None else Qt.PenStyle.NoPen)
    p.drawPath(yol)


def poligon(p: QPainter, noktalar, dolgu=None, cizgi=None, kalin: float = 2.0) -> None:
    p.setBrush(qc(dolgu) if dolgu is not None else Qt.BrushStyle.NoBrush)
    p.setPen(kalem(cizgi, kalin) if cizgi is not None else Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([QPointF(*n) for n in noktalar]))


def kirik_yol(noktalar, r: float = 22.0) -> QPainterPath:
    """Köşeleri yuvarlatılmış kırık çizgi (kablolar ve oklar için)."""
    pts = [QPointF(*n) for n in noktalar]
    yol = QPainterPath(pts[0])
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        d1 = math.hypot(b.x() - a.x(), b.y() - a.y())
        d2 = math.hypot(c.x() - b.x(), c.y() - b.y())
        rr = min(r, d1 / 2, d2 / 2)
        g = QPointF(b.x() - (b.x() - a.x()) / d1 * rr, b.y() - (b.y() - a.y()) / d1 * rr)
        c2 = QPointF(b.x() + (c.x() - b.x()) / d2 * rr, b.y() + (c.y() - b.y()) / d2 * rr)
        yol.lineTo(g)
        yol.quadTo(b, c2)
    yol.lineTo(pts[-1])
    return yol


def ok(p: QPainter, noktalar, renk=SOLUK, kalin: float = 6.0, uc: float = 22.0) -> None:
    """Ucu son noktada olan ok."""
    (x1, y1), (x2, y2) = noktalar[-2], noktalar[-1]
    d = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / d, (y2 - y1) / d
    govde = list(noktalar[:-1]) + [(x2 - ux * uc * 0.8, y2 - uy * uc * 0.8)]
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(renk, kalin))
    p.drawPath(kirik_yol(govde))
    nx, ny = -uy, ux
    w = uc * 0.62
    poligon(p, [(x2, y2), (x2 - ux * uc + nx * w, y2 - uy * uc + ny * w),
                (x2 - ux * uc - nx * w, y2 - uy * uc - ny * w)], dolgu=renk, cizgi=renk, kalin=3)


# ---- kablolar, fişler, haplar ------------------------------------------------------------------------

FIS_BOY = {"hdmi": 40, "usbc": 36, "usba": 40, "rj45": 38}


def fis(p: QPainter, uc: tuple[float, float], yon: tuple[float, float], tur: str | None, renk: str,
        s: float = FIS_OLCEK) -> None:
    """Kablonun ucundaki sade fiş: kablo renginde gövde, gri metal uç. Ucu (x, y)'de; yon cihaza doğru."""
    if tur is None:
        return
    p.save()
    p.translate(*uc)
    p.rotate(math.degrees(math.atan2(yon[1], yon[0])))
    p.scale(s, s)
    if tur == "hdmi":
        kutu(p, QRectF(-40, -13, 28, 26), 5, renk)
        kutu(p, QRectF(-14, -10, 14, 20), 2, METAL)
    elif tur == "usbc":
        kutu(p, QRectF(-36, -10, 26, 20), 7, renk)
        kutu(p, QRectF(-13, -5, 13, 10), 3, METAL)
    elif tur == "usba":
        kutu(p, QRectF(-40, -12, 24, 24), 4, renk)
        kutu(p, QRectF(-18, -11, 18, 22), 1.5, METAL)
    elif tur == "rj45":
        kutu(p, QRectF(-38, -11, 22, 22), 4, renk)
        kutu(p, QRectF(-18, -12, 18, 24), 3, RJ45_GOVDE)
    p.restore()


def hap(t: Tuval, cx: float, cy: float, metin: str, renk: str) -> QRectF:
    """Kablonun ortasındaki ad: koyu hap, kablo renginde ince kenar, açık yazı."""
    w = yazi_genisligi(metin, "etiket", HAP_PX) + 2 * 18
    h = HAP_PX * 1.45
    r = QRectF(cx - w / 2, cy - h / 2, w, h)
    kutu(t.p, r, h / 2, ZEMIN, renk, 3)
    t.yaz(cx, r.y() + (h - HAP_PX * 1.25) / 2, metin, "etiket", HAP_PX, YAZI, hiza="orta", kayit=None)
    t.kaydet_alan(r, f"hap: {metin}")
    return r


def kablo(t: Tuval, noktalar, tur: str, fisler: tuple, ad: str | None = None, parca: int | None = None,
          konum: float = 0.5, r: float = 24.0) -> None:
    """Dik açılı kablo; uçlarda fiş, ad (varsa) seçilen parçanın görünen kısmında bir hapta (konum: 0 baş,
    0.5 orta, 1 son). parca verilmezse en uzun parça seçilir."""
    renk = KABLO_RENK[tur]
    t.p.setBrush(Qt.BrushStyle.NoBrush)
    t.p.setPen(kalem(renk, KABLO_KALIN))
    t.p.drawPath(kirik_yol(noktalar, r))
    for uc, onceki, f in ((noktalar[0], noktalar[1], fisler[0]), (noktalar[-1], noktalar[-2], fisler[1])):
        d = math.hypot(uc[0] - onceki[0], uc[1] - onceki[1])
        fis(t.p, uc, ((uc[0] - onceki[0]) / d, (uc[1] - onceki[1]) / d), f, renk)
    if not ad:
        return
    son = len(noktalar) - 2
    if parca is None:
        parca = max(range(son + 1), key=lambda i: math.hypot(noktalar[i + 1][0] - noktalar[i][0],
                                                             noktalar[i + 1][1] - noktalar[i][1]))
    (ax, ay), (bx, by) = noktalar[parca], noktalar[parca + 1]
    d = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / d, (by - ay) / d
    bas = FIS_BOY.get(fisler[0], 0) * FIS_OLCEK if parca == 0 and fisler[0] else (r if parca > 0 else 0)
    bit = FIS_BOY.get(fisler[1], 0) * FIS_OLCEK if parca == son and fisler[1] else (r if parca < son else 0)
    hy = HAP_PX * 1.45 / 2 + 12 if abs(uy) > 0.5 else 0.0     # dikey parçada hap yüksekliğine yer bırak
    orta = (bas + hy) + (d - bit - hy - (bas + hy)) * konum
    hap(t, ax + ux * orta, ay + uy * orta, ad, renk)


# ---- cihazlar ---------------------------------------------------------------------------------------
# Her cihaz sol üst köşesinden ve tek bir ölçüden (yükseklik ya da genişlik) yerine konur; kapladığı
# alanı döner. Görünür yükseklik/genişlik oranları aşağıdaki sabitlerdedir.

class Yerel:
    """Yerel koordinat: cihaz kendi ölçüsünde çizilir, (x, y) ve ölçekle yerine konur."""

    def __init__(self, p: QPainter, x: float, y: float, s: float) -> None:
        self.p, self.x, self.y, self.s = p, x, y, s

    def __enter__(self) -> QPainter:
        self.p.save()
        self.p.translate(self.x, self.y)
        self.p.scale(self.s, self.s)
        return self.p

    def __exit__(self, *_a) -> None:
        self.p.restore()


PS5_ORAN = 200 / 358          # genişlik / yükseklik
KASA_ORAN = 170 / 338
KOL_ORAN = 156 / 240          # yükseklik / genişlik
MODEM_ORAN = 136 / 200
KART_ORAN = 86 / 170


def ps5(t: Tuval, x: float, y: float, yuk: float, kayit: bool = True) -> QRectF:
    """Dik duran PS5, önden-yandan görünüş: ince ön kanat, siyah gövde, üstü geniş yan kanat, yuvarlak
    ayak. Yerel ölçü 200×358."""
    s = yuk / 358
    with Yerel(t.p, x, y, s) as p:
        p.setBrush(qc(GOVDE_2))
        p.setPen(kalem(CIZGI, 2))
        p.drawEllipse(QPointF(112, 346), 62, 10)
        govde = QPainterPath(QPointF(24, 16))
        govde.lineTo(188, 28)
        govde.quadTo(192, 28, 192, 34)
        govde.lineTo(190, 332)
        govde.quadTo(190, 338, 184, 338)
        govde.lineTo(34, 342)
        govde.closeSubpath()
        sekil(p, govde, SIYAH, CIZGI, 2)
        on = QPainterPath(QPointF(6, 2))
        on.lineTo(28, 8)
        on.cubicTo(28, 120, 40, 250, 40, 338)
        on.lineTo(30, 338)
        on.cubicTo(26, 250, 4, 112, 6, 2)
        sekil(p, on, ACIK, ACIK_KENAR, 1.5)
        yan = QPainterPath(QPointF(44, 0))
        yan.cubicTo(110, 4, 160, 10, 199, 18)
        yan.cubicTo(200, 110, 193, 240, 181, 330)
        yan.cubicTo(160, 333, 136, 335, 114, 337)
        yan.cubicTo(84, 250, 40, 112, 44, 0)
        sekil(p, yan, ACIK, ACIK_KENAR, 1.5)
    r = QRectF(x, y, 200 * s, 358 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: PS5")
    return r


def dualsense(t: Tuval, x: float, y: float, gen: float, kayit: bool = True) -> QRectF:
    """DualSense: açık gövde ve tutamaklar, koyu orta yüz, üstte dokunmatik yüzey. (x, y) görünür sol
    üst köşe. Yerel ölçü 240×156."""
    s = gen / 240
    with Yerel(t.p, x, y - 12 * s, s) as p:
        g = QPainterPath(QPointF(70, 16))
        g.lineTo(170, 16)
        g.cubicTo(205, 16, 228, 28, 236, 60)
        g.cubicTo(244, 94, 242, 140, 222, 154)
        g.cubicTo(204, 166, 186, 152, 176, 130)
        g.cubicTo(168, 114, 158, 108, 146, 108)
        g.lineTo(94, 108)
        g.cubicTo(82, 108, 72, 114, 64, 130)
        g.cubicTo(54, 152, 36, 166, 18, 154)
        g.cubicTo(-2, 140, -4, 94, 4, 60)
        g.cubicTo(12, 28, 35, 16, 70, 16)
        sekil(p, g, ACIK, ACIK_KENAR, 1.5)
        ic = QPainterPath(QPointF(70, 36))
        ic.lineTo(170, 36)
        ic.cubicTo(190, 36, 204, 44, 207, 60)
        ic.cubicTo(210, 80, 199, 98, 182, 102)
        ic.cubicTo(160, 108, 80, 108, 58, 102)
        ic.cubicTo(41, 98, 30, 80, 33, 60)
        ic.cubicTo(36, 44, 50, 36, 70, 36)
        sekil(p, ic, SIYAH)
        kutu(p, QRectF(82, 12, 76, 46), 9, ACIK, ACIK_KENAR, 1.5)    # dokunmatik yüzey
        for rr in (QRectF(52, 50, 9, 10), QRectF(52, 70, 9, 10), QRectF(41, 60, 10, 9), QRectF(62, 60, 10, 9)):
            kutu(p, rr, 2, "#8D89A8")
        for dx, dy in ((0, -12), (12, 0), (0, 12), (-12, 0)):
            daire(p, QPointF(184 + dx, 64 + dy), 5.5, "#2A2740", "#8D89A8", 1.6)
        for cx in (95, 145):
            daire(p, QPointF(cx, 88), 14, "#2E2B45", "#55517A", 1.5)
            daire(p, QPointF(cx, 88), 8.5, "#45415F")
    r = QRectF(x, y, gen, gen * KOL_ORAN)
    if kayit:
        t.kaydet_alan(r, "cihaz: DualSense")
    return r


def kasa(t: Tuval, x: float, y: float, yuk: float, kayit: bool = True) -> QRectF:
    """Masaüstü bilgisayar kasası (önden): koyu gövde, ön panelde üç fan. Yerel ölçü 170×338."""
    s = yuk / 338
    with Yerel(t.p, x, y, s) as p:
        kutu(p, QRectF(20, 320, 30, 18), 4, GOVDE_2)
        kutu(p, QRectF(120, 320, 30, 18), 4, GOVDE_2)
        kutu(p, QRectF(0, 0, 170, 328), 14, GOVDE, CIZGI, 3)
        daire(p, QPointF(85, 34), 9, None, SOLUK, 3)
        kutu(p, QRectF(34, 30, 24, 8), 3, GOVDE_2)
        kutu(p, QRectF(112, 30, 24, 8), 3, GOVDE_2)
        kutu(p, QRectF(16, 64, 138, 248), 8, GOVDE_2)
        for cy in (122, 190, 258):
            daire(p, QPointF(85, cy), 27, None, CIZGI, 3)
            daire(p, QPointF(85, cy), 7, CIZGI)
    r = QRectF(x, y, 170 * s, 338 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: bilgisayar")
    return r


def ekran_olcu(ekran_gen: float, tur: str = "monitor") -> tuple[float, float, float, float]:
    """(toplam genişlik, toplam yükseklik, çerçeve, alt çerçeve) — monitör ayaklı, TV iki bacaklı."""
    k = ekran_gen / 600
    if tur == "monitor":
        cer = max(6.0, 14 * k)
        alt = cer
        ayak = max(22.0, 62 * k)
    else:
        cer = max(4.0, 9 * k)
        alt = cer * 1.5
        ayak = max(14.0, 34 * k)
    return ekran_gen + 2 * cer, ekran_gen * 9 / 16 + cer + alt + ayak, cer, alt


def ekran_cihazi(t: Tuval, x: float, y: float, ekran_gen: float, tur: str = "monitor", yazi: bool = True,
                 kayit: bool = True) -> tuple[QRectF, QRectF]:
    """Monitör (ortada ayak) ya da televizyon (iki yanda bacak). (çerçeve, ekran) alanlarını döner."""
    p = t.p
    gen, yuk, cer, alt = ekran_olcu(ekran_gen, tur)
    ekran_yuk = ekran_gen * 9 / 16
    cr = QRectF(x, y, gen, ekran_yuk + cer + alt)
    er = QRectF(x + cer, y + cer, ekran_gen, ekran_yuk)
    taban = y + yuk
    if tur == "monitor":
        cx = cr.center().x()
        ayak_yuk = max(5.0, 14 * ekran_gen / 600)
        boyun_gen = max(14.0, 40 * ekran_gen / 600)
        kutu(p, QRectF(cx - boyun_gen / 2, cr.bottom() - 4, boyun_gen, taban - cr.bottom()), 3, GOVDE_2,
             CIZGI, 2)
        tg = max(70.0, 230 * ekran_gen / 600)
        kutu(p, QRectF(cx - tg / 2, taban - ayak_yuk, tg, ayak_yuk), ayak_yuk / 2, GOVDE_2, CIZGI, 2)
    else:
        k = ekran_gen / 600
        b = cr.bottom()
        for fx, yon in ((0.14, -1), (0.86, 1)):
            ax = cr.left() + cr.width() * fx
            poligon(p, [(ax - 7 * k, b - 2), (ax + 7 * k, b - 2), (ax + yon * 20 * k + 7 * k, taban),
                        (ax + yon * 20 * k - 7 * k, taban)], GOVDE_2, CIZGI, 2)
    kutu(p, cr, max(6.0, 14 * ekran_gen / 600), GOVDE, CIZGI, 3)
    kutu(p, er, max(3.0, 6 * ekran_gen / 600), EKRAN)
    if yazi:
        altyazi(p, er)
    alan = QRectF(cr.left(), cr.top(), cr.width(), taban - cr.top())
    if tur == "tv":
        k = ekran_gen / 600
        alan = alan.united(QRectF(cr.left() + cr.width() * 0.14 - 27 * k, b, cr.width() * 0.72 + 54 * k, taban - b))
    if kayit:
        t.kaydet_alan(alan, f"cihaz: {tur}")
    return cr, er


def altyazi(p: QPainter, r: QRectF) -> None:
    """Ekranın tek ayrıntısı: Robz Vision'ın Türkçe kutusu ve altında oyunun soluk İngilizce satırı."""
    w, h = r.width(), r.height()
    cx = r.center().x()
    tr_px, en_px = h * 0.105, h * 0.066
    fe = yazi_tipi("govde_k", en_px)
    fme = QFontMetricsF(fe)
    taban_en = r.y() + h * 0.885
    p.setFont(fe)
    p.setPen(qc(YAZI_2, 0.55))
    p.drawText(QPointF(cx - fme.horizontalAdvance(ORNEK_EN) / 2, taban_en), ORNEK_EN)
    ft = yazi_tipi("govde_k", tr_px)
    fmt = QFontMetricsF(ft)
    sw = fmt.horizontalAdvance(ORNEK_TR)
    kh = tr_px * 1.62
    ky = taban_en - fme.ascent() - h * 0.05 - kh
    kutu(p, QRectF(cx - sw / 2 - tr_px * 0.62, ky, sw + tr_px * 1.24, kh), tr_px * 0.22, TR_KUTU)
    p.setFont(ft)
    p.setPen(qc("#FFFFFF"))
    p.drawText(QPointF(cx - sw / 2, ky + (kh - fmt.ascent() - fmt.descent()) / 2 + fmt.ascent()), ORNEK_TR)


def modem(t: Tuval, x: float, y: float, gen: float, kayit: bool = True) -> QRectF:
    """Antenli ev modemi. Yerel ölçü 200×136; gövdenin ortası yerel y=102."""
    s = gen / 200
    with Yerel(t.p, x, y, s) as p:
        for ax, aci in ((44, -10), (156, 10)):
            p.save()
            p.translate(ax, 76)
            p.rotate(aci)
            kutu(p, QRectF(-6, -74, 12, 80), 6, GOVDE_2, CIZGI, 2)
            p.restore()
        kutu(p, QRectF(0, 70, 200, 64), 18, GOVDE, CIZGI, 3)
        for i in range(4):
            daire(p, QPointF(64 + i * 24, 102), 5, SOLUK)
    r = QRectF(x, y, gen, gen * MODEM_ORAN)
    if kayit:
        t.kaydet_alan(r, "cihaz: modem")
    return r


def capture_karti(t: Tuval, x: float, y: float, gen: float, kayit: bool = True) -> QRectF:
    """Küçük capture kartı kutusu: üstte havalandırma, önde iki HDMI ve bir USB-C girişi. Yerel 170×86."""
    s = gen / 170
    with Yerel(t.p, x, y, s) as p:
        kutu(p, QRectF(0, 0, 170, 86), 16, GOVDE, CIZGI, 3)
        for i in range(5):
            p.setPen(kalem(CIZGI, 3))
            p.drawLine(QPointF(24 + i * 14, 18), QPointF(24 + i * 14, 32))
        daire(p, QPointF(146, 25), 5, SOLUK)
        for hx in (20, 66):
            poligon(p, [(hx, 52), (hx + 32, 52), (hx + 32, 60), (hx + 27, 66), (hx + 5, 66), (hx, 60)],
                    ZEMIN, CIZGI, 2)
        kutu(p, QRectF(118, 53, 32, 12), 6, ZEMIN, CIZGI, 2)
    r = QRectF(x, y, gen, gen * KART_ORAN)
    if kayit:
        t.kaydet_alan(r, "cihaz: capture kartı")
    return r


def kablo_simgesi(t: Tuval, cx: float, taban: float, tur: str, uclar: tuple[str, str], kayit: str) -> QRectF:
    """Malzeme hücresindeki kablo: U biçiminde kıvrılmış kablo, iki ucunda yukarı bakan fiş."""
    renk = KABLO_RENK[tur]
    s = 1.55
    yar, ust = 74, taban - 176
    t.p.setBrush(Qt.BrushStyle.NoBrush)
    t.p.setPen(kalem(renk, 10))
    t.p.drawPath(kirik_yol([(cx - yar, ust + 10), (cx - yar, taban - 6), (cx + yar, taban - 6),
                            (cx + yar, ust + 10)], 60))
    fis(t.p, (cx - yar, ust), (0, -1), uclar[0], renk, s)
    fis(t.p, (cx + yar, ust), (0, -1), uclar[1], renk, s)
    r = QRectF(cx - yar - 14 * s, ust, 2 * yar + 28 * s, taban - ust)
    t.kaydet_alan(r, kayit)
    return r


# ---- ortak parçalar -------------------------------------------------------------------------------------

_LOGO_ONBELLEK: dict[int, QImage] = {}


def logo(p: QPainter, x: float, y: float, boyut: float) -> None:
    """Robz Vision logosu, altyazitr/data/logo.png'den olduğu gibi (çerçeve, gölge yok)."""
    n = round(boyut)
    if n not in _LOGO_ONBELLEK:
        _LOGO_ONBELLEK[n] = QImage(str(LOGO)).scaled(n, n, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)
    p.drawImage(QPointF(round(x), round(y)), _LOGO_ONBELLEK[n])


def etiket(t: Tuval, cx: float, y: float, metin: str, px: float = ETIKET_PX, logo_ile: bool = False,
           renk=YAZI) -> QRectF:
    """Cihaz adı, cx'e ortalı; logo_ile=True ise solunda küçük Robz Vision logosu (bu cihazda çalışır)."""
    lh = px * 1.25
    lb = round(px * 1.4) if logo_ile else 0
    ara = round(px * 0.34) if logo_ile else 0
    tw = yazi_genisligi(metin, "etiket", px)
    x0 = cx - (lb + ara + tw) / 2
    if logo_ile:
        logo(t.p, x0, y + (lh - lb) / 2, lb)
    t.yaz(x0 + lb + ara, y, metin, "etiket", px, renk, kayit=None)
    r = QRectF(x0, y + min(0.0, (lh - lb) / 2), lb + ara + tw, max(lh, lb))
    t.kaydet_alan(r, f"etiket: {metin}")
    return r


def rozet(t: Tuval, x: float, y: float, metin: str, px: float = 40) -> QRectF:
    """Küçük "Yol 1" rozeti: koyu dolgu, camgöbeği yazı."""
    yuk = px * 1.5
    r = QRectF(x, y, yazi_genisligi(metin, "etiket", px) + px * 0.9, yuk)
    kutu(t.p, r, yuk * 0.3, GOVDE_2)
    t.yaz(r.center().x(), y + (yuk - px * 1.25) / 2, metin, "etiket", px, C.CYAN, hiza="orta", kayit=None)
    t.kaydet_alan(r, f"rozet: {metin}")
    return r


def numara(p: QPainter, cx: float, cy: float, r: float, n: str) -> None:
    daire(p, QPointF(cx, cy), r, GOVDE_2)
    f = yazi_tipi("etiket", r * 1.25)
    fm = QFontMetricsF(f)
    p.setFont(f)
    p.setPen(qc(YAZI))
    p.drawText(QPointF(cx - fm.horizontalAdvance(n) / 2, cy - (fm.ascent() + fm.descent()) / 2 + fm.ascent()), n)


def karo(t: Tuval, cx: float, y: float, b: float, ne: str) -> QRectF:
    """Adım/düğüm karosu: yuvarlak köşeli koyu kare."""
    r = QRectF(cx - b / 2, y, b, b)
    kutu(t.p, r, b * 0.17, GOVDE, CIZGI, 3)
    t.kaydet_alan(r, f"karo: {ne}")
    return r


# ---- karo simgeleri (b: karo kenarı, c: karo merkezi) --------------------------------------------------

def _s_indir(p: QPainter, c: QPointF, b: float) -> None:
    x, y = c.x(), c.y()
    k = kalem(YAZI, b * 0.05)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(k)
    p.drawLine(QPointF(x, y - b * 0.27), QPointF(x, y + b * 0.07))
    p.drawPath(kirik_yol([(x - b * 0.13, y - b * 0.06), (x, y + b * 0.07), (x + b * 0.13, y - b * 0.06)], 2))
    p.drawPath(kirik_yol([(x - b * 0.25, y + b * 0.08), (x - b * 0.25, y + b * 0.24), (x + b * 0.25, y + b * 0.24),
                          (x + b * 0.25, y + b * 0.08)], b * 0.04))


def _yildiz(p: QPainter, x: float, y: float, r: float, renk) -> None:
    yol = QPainterPath(QPointF(x, y - r))
    for a, b_ in (((x, y), (x + r, y)), ((x, y), (x, y + r)), ((x, y), (x - r, y)), ((x, y), (x, y - r))):
        yol.quadTo(QPointF(*a), QPointF(*b_))
    sekil(p, yol, renk)


def _s_yapay_zeka(p: QPainter, c: QPointF, b: float) -> None:
    _yildiz(p, c.x() - b * 0.05, c.y() + b * 0.05, b * 0.27, YAZI)
    _yildiz(p, c.x() + b * 0.22, c.y() - b * 0.19, b * 0.11, YAZI)


def _s_bolge(p: QPainter, c: QPointF, b: float) -> None:
    x, y = c.x(), c.y()
    r = QRectF(x - b * 0.31, y - b * 0.18, b * 0.62, b * 0.36)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(C.CYAN, b * 0.022, [2.2, 1.8]))
    p.drawRect(r)
    kutu(p, QRectF(x - b * 0.2, y - b * 0.03, b * 0.4, b * 0.06), b * 0.03, YAZI_2)
    t = b * 0.06
    for kx, ky in ((r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()), (r.right(), r.bottom())):
        kutu(p, QRectF(kx - t / 2, ky - t / 2, t, t), 2, C.CYAN)


def _s_baslat(p: QPainter, c: QPointF, b: float) -> None:
    x, y = c.x(), c.y()
    poligon(p, [(x - b * 0.11, y - b * 0.19), (x - b * 0.11, y + b * 0.19), (x + b * 0.2, y)], C.PINK, C.PINK,
            b * 0.06)


def _s_obs(p: QPainter, c: QPointF, b: float) -> None:
    """Kayıt yazılımı penceresi (logo çizilmez): ekranda Robz Vision'ın Türkçe kutusu."""
    x, y = c.x(), c.y()
    pen = b * 0.02
    w = QRectF(x - b * 0.32, y - b * 0.23, b * 0.64, b * 0.46)
    kutu(p, w, b * 0.04, EKRAN, YAZI_2, pen)
    p.setPen(kalem(YAZI_2, pen))
    p.drawLine(QPointF(w.left(), w.top() + b * 0.08), QPointF(w.right(), w.top() + b * 0.08))
    for i in range(3):
        daire(p, QPointF(w.left() + b * (0.045 + i * 0.04), w.top() + b * 0.04), b * 0.012, YAZI_2)
    kr = QRectF(x - b * 0.2, w.bottom() - b * 0.15, b * 0.4, b * 0.09)
    kutu(p, kr, b * 0.02, TR_KUTU)
    kutu(p, kr.adjusted(b * 0.05, b * 0.033, -b * 0.05, -b * 0.033), b * 0.012, "#FFFFFF")


def _kisi(p: QPainter, x: float, y: float, r: float, renk, dolgu) -> None:
    """Basit izleyici: baş ve omuzlar; (x, y) başın merkezi."""
    omuz = QPainterPath(QPointF(x - r * 1.9, y + r * 3.1))
    omuz.cubicTo(QPointF(x - r * 1.9, y + r * 1.3), QPointF(x + r * 1.9, y + r * 1.3), QPointF(x + r * 1.9, y + r * 3.1))
    omuz.closeSubpath()
    sekil(p, omuz, dolgu, renk, r * 0.32)
    daire(p, QPointF(x, y), r, dolgu, renk, r * 0.32)


def _s_izleyici(p: QPainter, c: QPointF, b: float) -> None:
    x, y = c.x(), c.y() - b * 0.1
    r = b * 0.075
    p.save()
    yol = QPainterPath()
    yol.addRoundedRect(QRectF(c.x() - b / 2, c.y() - b / 2, b, b), b * 0.17, b * 0.17)
    p.setClipPath(yol)
    _kisi(p, x - b * 0.2, y + b * 0.02, r * 0.85, YAZI_2, GOVDE)
    _kisi(p, x + b * 0.2, y + b * 0.02, r * 0.85, YAZI_2, GOVDE)
    _kisi(p, x, y - b * 0.03, r, YAZI, GOVDE)
    p.restore()


# =====================================================================================================
# Çizimler
# =====================================================================================================

def ciz_neler_lazim(t: Tuval) -> None:
    """4 + 5 düzenli ızgara: her hücrede yalnız malzeme çizimi ve altında adı (hücre çerçevesi yok)."""
    px = 40
    g = 20
    ust_b, alt_b = 330, 688                         # iki satırın taban çizgileri
    uw = (W - 2 * KENAR - 3 * g) / 4
    aw = (W - 2 * KENAR - 4 * g) / 5
    ust = [KENAR + uw * (i + 0.5) + g * i for i in range(4)]
    alt = [KENAR + aw * (i + 0.5) + g * i for i in range(5)]

    # --- üst satır: büyük cihazlar
    cx = ust[0]
    pc_h, mg = 190, 222
    mw, mh, _, _ = ekran_olcu(mg, "monitor")
    pw = pc_h * KASA_ORAN
    x0 = cx - (pw + 16 + mw) / 2
    kasa(t, x0, ust_b - pc_h, pc_h, kayit=False)
    ekran_cihazi(t, x0 + pw + 16, ust_b - mh, mg, "monitor", yazi=False, kayit=False)
    t.kaydet_alan(QRectF(x0, ust_b - max(pc_h, mh), pw + 16 + mw, max(pc_h, mh)), "cihaz: bilgisayar ve monitör")
    etiket(t, cx, ust_b + 20, "Bilgisayar ve monitör", px)

    ph = 236
    ps5(t, ust[1] - ph * PS5_ORAN / 2, ust_b - ph, ph)
    etiket(t, ust[1], ust_b + 20, "PS5", px)

    kg = 250
    dualsense(t, ust[2] - kg / 2, ust_b - kg * KOL_ORAN, kg)
    etiket(t, ust[2], ust_b + 20, "DualSense", px)

    tg = 300
    tw, th, _, _ = ekran_olcu(tg, "tv")
    ekran_cihazi(t, ust[3] - tw / 2, ust_b - th, tg, "tv", yazi=False)
    etiket(t, ust[3], ust_b + 20, "Televizyon", px)

    # --- alt satır: modem, kablolar, capture kartı
    mdg = 210
    modem(t, alt[0] - mdg / 2, alt_b - mdg * MODEM_ORAN, mdg)
    etiket(t, alt[0], alt_b + 20, "Modem", px)

    kablo_simgesi(t, alt[1], alt_b, "eth", ("rj45", "rj45"), "cihaz: Ethernet kablosu")
    etiket(t, alt[1], alt_b + 20, "Ethernet kablosu", px)
    kablo_simgesi(t, alt[2], alt_b, "usb", ("usba", "usbc"), "cihaz: USB-C kablo")
    etiket(t, alt[2], alt_b + 20, "USB-C kablo", px)
    kablo_simgesi(t, alt[3], alt_b, "hdmi", ("hdmi", "hdmi"), "cihaz: HDMI kablo")
    etiket(t, alt[3], alt_b + 20, "HDMI kablo", px)

    cg = 214
    capture_karti(t, alt[4] - cg / 2, alt_b - cg * KART_ORAN, cg)
    r = etiket(t, alt[4], alt_b + 20, "Capture kartı", px)
    t.yaz(alt[4], r.bottom() + 2, "isteğe bağlı", "govde", KUCUK_PX, SOLUK, hiza="orta", kayit="küçük")


def _ikili_simge(t: Tuval, cx: float, taban: float, sol: str, sag: str) -> None:
    """Yol kartındaki büyük simge: iki cihaz yan yana, ortak tabanda."""
    olc = {"kasa": (164 * KASA_ORAN, 164), "ps5": (176 * PS5_ORAN, 176), "monitor": ekran_olcu(176, "monitor")[:2],
           "tv": ekran_olcu(186, "tv")[:2], "capture": (176, 176 * KART_ORAN)}
    ara = 18
    (w1, h1), (w2, h2) = olc[sol], olc[sag]
    x = cx - (w1 + ara + w2) / 2
    for tur, (w, h) in ((sol, (w1, h1)), (sag, (w2, h2))):
        if tur == "kasa":
            kasa(t, x, taban - h, h, kayit=False)
        elif tur == "ps5":
            ps5(t, x, taban - h, h, kayit=False)
        elif tur == "monitor":
            ekran_cihazi(t, x, taban - h, 176, "monitor", yazi=False, kayit=False)
        elif tur == "tv":
            ekran_cihazi(t, x, taban - h, 186, "tv", yazi=False, kayit=False)
        elif tur == "capture":
            capture_karti(t, x, taban - h, w, kayit=False)
        x += w + ara
    t.kaydet_alan(QRectF(cx - (w1 + ara + w2) / 2, taban - max(h1, h2), w1 + ara + w2, max(h1, h2)),
                  f"simge: {sol}+{sag}")


def ciz_yol_secimi(t: Tuval) -> None:
    """Tek soru ve altında dört eşit kart: rozet, büyük simge, kısa ad."""
    t.yaz(W / 2, 108, "Nerede oynuyorsun?", "etiket", 56, YAZI, hiza="orta", kayit="soru")
    g = 32
    kw = (W - 2 * KENAR - 3 * g) / 4
    ky, kh = 248, 520
    kartlar = (("Yol 1", "Bilgisayarda", ("kasa", "monitor")),
               ("Yol 2", "PS5 + bilgisayar", ("ps5", "monitor")),
               ("Yol 3", "PS5 + TV", ("ps5", "tv")),
               ("Yol 4", "Capture kartı", ("ps5", "capture")))
    for i, (yol, ad, (sol, sag)) in enumerate(kartlar):
        x = KENAR + i * (kw + g)
        r = QRectF(x, ky, kw, kh)
        kutu(t.p, r, 24, KART, CIZGI, 2)
        t.kaydet_alan(r, f"kap: {yol}")
        rozet(t, x + 28, ky + 28, yol)
        _ikili_simge(t, r.center().x(), ky + 350, sol, sag)
        etiket(t, r.center().x(), ky + kh - 104, ad, 44)


def ciz_robz_vision_kurulum(t: Tuval) -> None:
    """Soldan sağa dört büyük adım, aralarında ok: numara, büyük sade simge, adımın adı."""
    adimlar = (("İndir ve kur", _s_indir), ("Yapay zekâ ile", _s_yapay_zeka), ("Bölge seç", _s_bolge),
               ("Başlat", _s_baslat))
    sut = (W - 2 * KENAR) / 4
    b, ky = 262, 270
    onceki: QRectF | None = None
    for i, (ad, simge_ciz) in enumerate(adimlar):
        cx = KENAR + sut * (i + 0.5)
        r = karo(t, cx, ky, b, ad)
        simge_ciz(t.p, r.center(), b)
        if onceki is not None:
            ok(t.p, [(onceki.right() + 24, r.center().y()), (r.left() - 24, r.center().y())])
        onceki = r
        # numara + ad, birlikte ortalı
        px, nr = 44, 27
        tw = yazi_genisligi(ad, "etiket", px)
        x0 = cx - (2 * nr + 16 + tw) / 2
        y = r.bottom() + 40
        numara(t.p, x0 + nr, y + px * 1.25 / 2, nr, str(i + 1))
        t.yaz(x0 + 2 * nr + 16, y, ad, "etiket", px, YAZI, kayit=None)
        t.kaydet_alan(QRectF(x0, y, 2 * nr + 16 + tw, px * 1.25), f"etiket: {ad}")


def _ps5_ag_kol(t: Tuval, B: float) -> QRectF:
    """Yol 2 ve 3'ün ortak sol yarısı. PS5 ve bilgisayar tabanda, modem üstte ortada; Ethernet kabloları
    modemden inip ikisine üstten girer. DualSense ikisinin arasında, USB-C ile bilgisayara bağlanır.
    Bilgisayarın alanını döner."""
    ps = ps5(t, 96, B - 300, 300)
    etiket(t, ps.center().x(), B + 16, "PS5")
    kg = 186
    kol = dualsense(t, 294, B - kg * KOL_ORAN, kg)
    etiket(t, kol.center().x(), B + 16, "DualSense")
    pc = kasa(t, 614, B - 300, 300)
    etiket(t, pc.center().x(), B + 16, "Bilgisayar", logo_ile=True)
    mg = 210
    mcx = (ps.center().x() + pc.center().x()) / 2
    md = modem(t, mcx - mg / 2, B - 614, mg)
    ye = md.y() + 102 * mg / 200                    # modem gövdesinin ortası
    etiket(t, mcx, md.bottom() + 6, "Modem")
    kablo(t, [(md.left(), ye), (ps.center().x(), ye), (ps.center().x(), ps.top() + 2)], "eth", ("rj45", "rj45"),
          "Ethernet", 1, 0.85)
    kablo(t, [(md.right(), ye), (pc.center().x(), ye), (pc.center().x(), pc.top())], "eth", ("rj45", "rj45"),
          "Ethernet", 1, 0.85)
    yu = B - 214
    kablo(t, [(kol.center().x(), kol.top() + 4), (kol.center().x(), yu), (pc.left(), yu)], "usb", ("usbc", "usba"),
          "USB-C", 1)
    return pc


def ciz_yol1(t: Tuval) -> None:
    """Solda bilgisayar (altında logo ve adı), sağda büyük monitör, arada HDMI."""
    B = 684
    pc_h, mg, ara = 400, 740, 300
    mw, mh, _, _ = ekran_olcu(mg, "monitor")
    pw = pc_h * KASA_ORAN
    x0 = (W - (pw + ara + mw)) / 2
    pc = kasa(t, x0, B - pc_h, pc_h)
    cr, _ = ekran_cihazi(t, pc.right() + ara, B - mh, mg, "monitor")
    yk = B - 220
    kablo(t, [(pc.right(), yk), (cr.left(), yk)], "hdmi", ("hdmi", "hdmi"), "HDMI")
    etiket(t, pc.center().x(), B + 16, "Bilgisayar", logo_ile=True)
    etiket(t, cr.center().x(), B + 16, "Monitör")


def ciz_yol2(t: Tuval) -> None:
    """PS5 —Ethernet— Modem —Ethernet— Bilgisayar —HDMI— Monitör; DualSense —USB-C— Bilgisayar."""
    B = 714
    pc = _ps5_ag_kol(t, B)
    mg = 470
    mw, mh, _, _ = ekran_olcu(mg, "monitor")
    cr, _ = ekran_cihazi(t, W - KENAR - mw, B - mh, mg, "monitor")
    yk = B - 186
    kablo(t, [(pc.right(), yk), (cr.left(), yk)], "hdmi", ("hdmi", "hdmi"), "HDMI")
    etiket(t, cr.center().x(), B + 16, "Monitör")


def ciz_yol3(t: Tuval) -> None:
    """Yol 2 ile aynı yerleşim; monitör yerine büyük televizyon."""
    B = 714
    pc = _ps5_ag_kol(t, B)
    tg = 505
    tw, th, _, _ = ekran_olcu(tg, "tv")
    cr, _ = ekran_cihazi(t, W - KENAR - tw, B - th, tg, "tv")
    yk = B - 186
    kablo(t, [(pc.right(), yk), (cr.left(), yk)], "hdmi", ("hdmi", "hdmi"), "HDMI")
    etiket(t, cr.center().x(), B + 16, "Televizyon")


def ciz_yol4(t: Tuval) -> None:
    """PS5 —HDMI— Capture kartı —USB— Bilgisayar —HDMI— TV ya da monitör. Kart üstte ortada."""
    B = 696
    ps = ps5(t, 100, B - 288, 288)
    etiket(t, ps.center().x(), B + 16, "PS5")
    pc = kasa(t, 565, B - 300, 300)
    etiket(t, pc.center().x(), B + 16, "Bilgisayar", logo_ile=True)
    kg = 220
    kcx = (ps.center().x() + pc.center().x()) / 2
    kh = kg * KART_ORAN
    yc = B - 464
    ck = capture_karti(t, kcx - kg / 2, yc - kh / 2, kg)
    etiket(t, kcx, ck.top() - 6 - ETIKET_PX * 1.25, "Capture kartı")
    kablo(t, [(ck.left(), yc), (ps.center().x(), yc), (ps.center().x(), ps.top() + 2)], "hdmi", ("hdmi", "hdmi"),
          "HDMI", 1)
    kablo(t, [(ck.right(), yc), (pc.center().x(), yc), (pc.center().x(), pc.top())], "usb", ("usbc", "usba"),
          "USB", 1)
    tg = 560
    tw, th, _, _ = ekran_olcu(tg, "tv")
    cr, _ = ekran_cihazi(t, W - KENAR - tw, B - th, tg, "tv")
    yk = B - 170
    kablo(t, [(pc.right(), yk), (cr.left(), yk)], "hdmi", ("hdmi", "hdmi"), "HDMI")
    etiket(t, cr.center().x(), B + 16, "TV ya da monitör")


def ciz_yayin(t: Tuval) -> None:
    """Robz Vision (logo) → OBS · Tarayıcı kaynağı → Yayın (izleyiciler)."""
    sut = (W - 2 * KENAR) / 3
    b, ky = 270, 262
    cx = [KENAR + sut * (i + 0.5) for i in range(3)]
    a = QRectF(cx[0] - b / 2, ky, b, b)
    logo(t.p, a.x(), a.y(), b)
    t.kaydet_alan(a, "logo: Robz Vision")
    r = etiket(t, cx[0], a.bottom() + 34, "Robz Vision")
    t.yaz(cx[0], r.bottom() + 4, "127.0.0.1:8765", "govde", KUCUK_PX, YAZI_2, hiza="orta", kayit="küçük: adres")
    o = karo(t, cx[1], ky, b, "OBS")
    _s_obs(t.p, o.center(), b)
    etiket(t, cx[1], o.bottom() + 34, "OBS · Tarayıcı kaynağı")
    y = karo(t, cx[2], ky, b, "Yayın")
    _s_izleyici(t.p, y.center(), b)
    etiket(t, cx[2], y.bottom() + 34, "Yayın")
    oy = ky + b / 2
    ok(t.p, [(a.right() + 30, oy), (o.left() - 30, oy)])
    ok(t.p, [(o.right() + 30, oy), (y.left() - 30, oy)])


CIZIMLER: dict[str, Callable[[Tuval], None]] = {
    "neler_lazim": ciz_neler_lazim,
    "yol_secimi": ciz_yol_secimi,
    "robz_vision_kurulum": ciz_robz_vision_kurulum,
    "yol1_bilgisayar": ciz_yol1,
    "yol2_ps5_bilgisayar": ciz_yol2,
    "yol3_ps5_tv": ciz_yol3,
    "yol4_capture": ciz_yol4,
    "yayin_obs": ciz_yayin,
}


def rehber_gorseli(ad: str) -> tuple[Image.Image, list[str]]:
    t = Tuval(ad)
    CIZIMLER[ad](t)
    sorunlar = t.denetle()
    return t.bitir(), sorunlar


def rehber_gorselleri(adlar: list[str], kolaj: Path | None = None) -> bool:
    REHBER_PNG.mkdir(parents=True, exist_ok=True)
    REHBER_WEBP.mkdir(parents=True, exist_ok=True)
    temiz = True
    kucukler: list[Image.Image] = []
    for ad in adlar:
        im, sorunlar = rehber_gorseli(ad)
        png, webp = REHBER_PNG / f"{ad}.png", REHBER_WEBP / f"{ad}.webp"
        im.save(png, "PNG", optimize=True)
        im.save(webp, "WEBP", quality=85, method=6)
        print(f"  {ad}: {png.stat().st_size // 1024} KB png, {webp.stat().st_size // 1024} KB webp")
        for s in sorunlar:
            temiz = False
            print(f"    UYARI {s}")
        kucukler.append(im.resize((600, round(600 * H / W)), Image.Resampling.LANCZOS))
    if kolaj and kucukler:
        sut = 2
        tw, th = kucukler[0].size
        satir = (len(kucukler) + sut - 1) // sut
        k = Image.new("RGB", (sut * tw + (sut + 1) * 12, satir * th + (satir + 1) * 12), (40, 38, 60))
        for i, im in enumerate(kucukler):
            k.paste(im, (12 + (i % sut) * (tw + 12), 12 + (i // sut) * (th + 12)))
        k.save(kolaj)
        print("  önizleme kolajı:", kolaj)
    print("Kurulum rehberi görselleri yazıldı:", REHBER_PNG, "ve", REHBER_WEBP)
    return temiz


def main() -> None:
    ap = argparse.ArgumentParser(description="Kurulum sihirbazı ve kurulum rehberi görsellerini üretir.")
    ap.add_argument("adlar", nargs="*", help="sihirbaz, rehber ya da çizim adları (boşsa hepsi)")
    ap.add_argument("--liste", action="store_true", help="çizim adlarını yaz ve çık")
    ap.add_argument("--kolaj", type=Path, help="rehber çizimlerinin 600 px önizleme kolajını bu dosyaya yaz")
    args = ap.parse_args()
    if args.liste:
        print("sihirbaz\nrehber\n" + "\n".join(CIZIMLER))
        return
    istek = args.adlar or ["sihirbaz", "rehber"]
    bilinmeyen = [a for a in istek if a not in ("sihirbaz", "rehber") and a not in CIZIMLER]
    if bilinmeyen:
        ap.error("bilinmeyen ad: " + ", ".join(bilinmeyen) + " (--liste ile adları görün)")
    _app = QGuiApplication(sys.argv[:1])
    if "sihirbaz" in istek:
        sihirbaz_gorselleri()
    rehber = [a for a in CIZIMLER if "rehber" in istek or a in istek]
    if rehber:
        rehber_gorselleri(rehber, args.kolaj)


if __name__ == "__main__":
    main()
