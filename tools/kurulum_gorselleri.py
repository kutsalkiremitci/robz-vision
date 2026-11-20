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
   site/img/kurulum/<ad>.webp (kalite 85; aynı çizim):
  neler_lazim           malzeme tablosu; her malzemede hangi yolda gerektiğini gösteren 1-4 rozetleri
  yol_secimi            karar şeması: "Oyunu nerede oynuyorsun?" → Yol 1/2/3/4, altta yayın kutusu
  robz_vision_kurulum   indirme → Windows uyarısı → sihirbaz → ilk açılış → modeller → bölge → Başlat
  yol1_bilgisayar       bilgisayar → monitör; Robz Vision okur → çevirir → ekrana yazar
  yol2_ps5_bilgisayar   PS5 → modem → bilgisayar → monitör; kol bilgisayarda; chiaki-ng + Robz Vision
  yol3_ps5_tv           yol 2 gibi, ama bilgisayar → HDMI → televizyon (kablosuz Win+K seçeneği kesik)
  yol4_capture          PS5 → capture kartı → bilgisayar → TV/monitör; HDCP notu, passthrough uyarısı
  yayin_obs             Robz Vision adresi → OBS Tarayıcı kaynağı → yayın → izleyiciler

Çizimler QPainter ile vektör olarak çizilir, sonra rastera dökülür; böylece yazılar her makinede aynı
görünür. Çıktı belirlenimcidir (aynı girdi, aynı dosya): rastgelelik, saat ya da ağ kullanılmaz.
Yazı tipleri Windows'ta hazır gelir: başlık ve etiketler Bahnschrift (sitenin Barlow Semi Condensed
woff2 dosyalarını Qt yükleyemiyor; Bahnschrift aynı DIN çizgisindedir), gövde Segoe UI.

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

W, H = 1600, 900
M = 56                       # kenar boşluğu

# Cihaz renkleri (yalnız bu çizimlerde; arayüz token'ları C'den gelir)
BEYAZ = "#F3F2F8"            # PS5 kanatları, DualSense gövdesi
BEYAZ_GOLGE = "#CFCBE0"
BEYAZ_KENAR = "#A9A4C4"
SIYAH = "#14121E"            # PS5 çekirdeği, DualSense ortası
SIYAH_KENAR = "#3A3656"
GOVDE = "#2A2745"            # bilgisayar kasası, monitör, capture kartı
GOVDE_KOYU = "#1B1930"
GOVDE_KENAR = "#5B5690"
METAL = "#CBC8DC"
METAL_KOYU = "#8A86A6"
ALTIN = "#E7C267"

# Örnek altyazı (projeye özgü, oyundan alıntı değil)
ORNEK_TR = "Hadi, sahile gidelim!"
ORNEK_EN = "Come on, let's hit the beach!"

# Yazı tipleri (piksel boyutu ayrıca verilir)
YT_BASLIK = F.DISPLAY_CONDENSED      # "Bahnschrift SemiBold SemiCondensed"
YT_ETIKET = F.DISPLAY_CONDENSED
YT_GOVDE = "Segoe UI"


def qc(renk: str | QColor, alfa: float = 1.0) -> QColor:
    c = QColor(renk)
    if alfa < 1.0:
        c.setAlphaF(alfa)
    return c


def yazi_tipi(tur: str, px: float) -> QFont:
    """tur: baslik | etiket (Bahnschrift) · govde | govde_k | kalin (Segoe UI)."""
    if tur in ("baslik", "etiket"):
        f = QFont(YT_BASLIK)
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
        self.img.fill(QColor(C.BG))
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
        """Kayıtlı yazı/cihaz alanları üst üste biniyor mu, tuvalden taşıyor mu?"""
        sorunlar = list(self.uyarilar)
        tuval = QRectF(0, 0, W, H)
        for r, ne in self.kutular:
            if not tuval.contains(r):
                sorunlar.append(f"tuvalden taşıyor: {ne} {r.getRect()}")
        for i in range(len(self.kutular)):
            for j in range(i + 1, len(self.kutular)):
                a, an = self.kutular[i]
                b, bn = self.kutular[j]
                # "kap:" bir kapsayıcıdır (kart, bant); içindeki öğeler çakışma sayılmaz, taşanlar sayılır
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
    @staticmethod
    def sar(metin: str, fm: QFontMetricsF, gen: float | None) -> list[str]:
        """Sözcük kaydırma. Bölünmez boşluk (\\u00a0) iki sözcüğü aynı satırda tutar."""
        satirlar: list[str] = []
        for par in metin.split("\n"):
            if gen is None:
                satirlar.append(par)
                continue
            cur = ""
            for s in par.split(" "):
                aday = s if not cur else cur + " " + s
                if not cur or fm.horizontalAdvance(aday) <= gen:
                    cur = aday
                else:
                    satirlar.append(cur)
                    cur = s
            satirlar.append(cur)
        return satirlar

    def olc(self, metin: str, tur: str, px: float, gen: float | None = None, satir: float = 1.22):
        """(en geniş satır, toplam yükseklik, satır sayısı)"""
        f = yazi_tipi(tur, px)
        fm = QFontMetricsF(f)
        sat = self.sar(metin, fm, gen)
        return max(fm.horizontalAdvance(s) for s in sat), len(sat) * px * satir, len(sat)

    def yaz(self, x: float, y: float, metin: str, tur: str = "govde", px: float = 28, renk=C.TEXT,
            gen: float | None = None, hiza: str = "sol", satir: float = 1.22, en_cok: int | None = None,
            kayit: str | None = "yazı") -> QRectF:
        """Metni (x, y) sol üstünden yazar (hiza="orta" ve gen yoksa x ortadır). Kapladığı alanı döner."""
        p = self.p
        f = yazi_tipi(tur, px)
        fm = QFontMetricsF(f)
        satirlar = self.sar(metin, fm, gen)
        lh = px * satir
        p.setFont(f)
        p.setPen(qc(renk))
        sol, sag = 1e9, -1e9
        for i, s in enumerate(satirlar):
            sw = fm.horizontalAdvance(s)
            if gen is not None and sw > gen + 0.5:
                self.uyarilar.append(f"satır taşıyor ({sw:.0f} > {gen:.0f}): {s!r}")
            if hiza == "orta":
                sx = x + (gen - sw) / 2 if gen is not None else x - sw / 2
            elif hiza == "sag":
                sx = x + gen - sw if gen is not None else x - sw
            else:
                sx = x
            taban = y + i * lh + (lh - (fm.ascent() + fm.descent())) / 2 + fm.ascent()
            p.drawText(QPointF(sx, taban), s)
            sol, sag = min(sol, sx), max(sag, sx + sw)
        if en_cok is not None and len(satirlar) > en_cok:
            self.uyarilar.append(f"{len(satirlar)} satır (en çok {en_cok}): {metin!r}")
        r = QRectF(sol, y, sag - sol, len(satirlar) * lh)
        if kayit:
            self.kaydet_alan(r, f"{kayit}: {metin[:28]!r}")
        return r


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


def ok(p: QPainter, noktalar, renk=C.TEXT_2, kalin: float = 4.0, uc: float = 16.0,
       kesik: list[float] | None = None) -> None:
    """Ucu son noktada olan ok."""
    (x1, y1), (x2, y2) = noktalar[-2], noktalar[-1]
    d = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / d, (y2 - y1) / d
    govde = list(noktalar[:-1]) + [(x2 - ux * uc * 0.8, y2 - uy * uc * 0.8)]
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(renk, kalin, kesik))
    p.drawPath(kirik_yol(govde))
    nx, ny = -uy, ux
    w = uc * 0.62
    poligon(p, [(x2, y2), (x2 - ux * uc + nx * w, y2 - uy * uc + ny * w),
                (x2 - ux * uc - nx * w, y2 - uy * uc - ny * w)], dolgu=renk)


def imlec(p: QPainter, x: float, y: float, s: float = 1.0) -> None:
    """Fare imleci (tıklanacak yeri gösterir); ucu (x, y)."""
    pts = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
    poligon(p, [(x + a * s, y + b * s) for a, b in pts], dolgu="#FFFFFF", cizgi="#000000", kalin=2.0 * s)


# ---- kablolar ve fişler -----------------------------------------------------------------------------

# tür → (lejant adı, renk, kalınlık, desen, fişler)
KABLO = {
    "hdmi": ("HDMI", C.PINK, 10.0, "duz", ("hdmi", "hdmi")),
    "usb": ("USB-C kablo", C.CYAN, 7.0, "duz", ("usbc", "usba")),
    "eth": ("Ethernet", C.TURQUOISE, 12.0, "tup", ("rj45", "rj45")),
    "kablosuz": ("Wi-Fi / Bluetooth (kablosuz)", C.TEXT_2, 5.0, "kesik", (None, None)),
    "uyari": ("Burada çeviri görünmez", C.ORANGE, 6.0, "kesik", ("hdmi", "hdmi")),
}


def _kablo_ciz(p: QPainter, yol: QPainterPath, tur: str) -> None:
    _, renk, kalin, desen, _ = KABLO[tur]
    p.setBrush(Qt.BrushStyle.NoBrush)
    if desen == "duz":
        p.setPen(kalem(renk, kalin))
        p.drawPath(yol)
    elif desen == "tup":          # Ethernet: içi boş çift çizgi
        p.setPen(kalem(renk, kalin))
        p.drawPath(yol)
        p.setPen(kalem(C.BG, kalin - 6.5))
        p.drawPath(yol)
    else:                          # kablosuz ya da uyarı: kesik çizgi
        pen = QPen(qc(renk), kalin, Qt.PenStyle.CustomDashLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        pen.setDashPattern([1.6, 2.2])
        p.setPen(pen)
        p.drawPath(yol)


def fis(p: QPainter, uc: tuple[float, float], yon: tuple[float, float], tur: str | None, renk: str,
        s: float = 1.0) -> None:
    """Kablonun ucundaki fiş; ucu (x, y) noktasında, yon kablonun cihaza girdiği yön."""
    if tur is None:
        return
    p.save()
    p.translate(*uc)
    p.rotate(math.degrees(math.atan2(yon[1], yon[0])))
    p.scale(s, s)
    koyu = qc(renk).darker(135)
    if tur == "hdmi":
        kutu(p, QRectF(-54, -7, 14, 14), 3, koyu)                      # kablo koruması
        kutu(p, QRectF(-42, -13, 28, 26), 5, renk)                     # plastik gövde
        kutu(p, QRectF(-16, -10, 16, 20), 2, METAL, METAL_KOYU, 1.5)   # metal uç
        p.setPen(kalem(METAL_KOYU, 1.5))
        p.drawLine(QPointF(-12, -4), QPointF(-3, -4))
        p.drawLine(QPointF(-12, 4), QPointF(-3, 4))
    elif tur == "usbc":
        kutu(p, QRectF(-44, -6, 12, 12), 3, koyu)
        kutu(p, QRectF(-34, -10, 24, 20), 7, renk)
        kutu(p, QRectF(-12, -6.5, 12, 13), 6.5, METAL, METAL_KOYU, 1.5)  # oval uç
    elif tur == "usba":
        kutu(p, QRectF(-50, -6, 12, 12), 3, koyu)
        kutu(p, QRectF(-40, -12, 22, 24), 4, renk)
        kutu(p, QRectF(-20, -11, 20, 22), 1.5, METAL, METAL_KOYU, 1.5)   # dikdörtgen metal kabuk
        kutu(p, QRectF(-12, -6, 5, 4), 0.5, METAL_KOYU)
        kutu(p, QRectF(-12, 2, 5, 4), 0.5, METAL_KOYU)
    elif tur == "rj45":
        kutu(p, QRectF(-46, -8, 18, 16), 4, koyu)                       # kılıf
        kutu(p, QRectF(-30, -11, 30, 22), 3, "#D8F3EE", renk, 2)         # şeffaf gövde
        for i in range(5):
            p.setPen(kalem(ALTIN, 1.6))
            p.drawLine(QPointF(-7, -7 + i * 3.5), QPointF(-2, -7 + i * 3.5))
        p.setPen(kalem(renk, 2.6))                                     # tırnak
        p.drawLine(QPointF(-6, -11), QPointF(-20, -15))
    p.restore()


def kablo(t: Tuval, noktalar, tur: str, fisler: tuple | None = None, r: float = 22.0) -> None:
    yol = kirik_yol(noktalar, r)
    _kablo_ciz(t.p, yol, tur)
    _, renk, _, _, varsayilan = KABLO[tur]
    f0, f1 = fisler if fisler is not None else varsayilan
    (ax, ay), (bx, by) = noktalar[0], noktalar[1]
    d = math.hypot(ax - bx, ay - by)
    fis(t.p, (ax, ay), ((ax - bx) / d, (ay - by) / d), f0, renk)
    (ax, ay), (bx, by) = noktalar[-2], noktalar[-1]
    d = math.hypot(bx - ax, by - ay)
    fis(t.p, (bx, by), ((bx - ax) / d, (by - ay) / d), f1, renk)


def lejant(t: Tuval, turler, y: float = 852, x: float = M, adlar: dict | None = None) -> float:
    """Kablo açıklaması: örnek çizgi + ad. Bittiği x'i döner."""
    for tur in turler:
        ad = (adlar or {}).get(tur, KABLO[tur][0])
        _kablo_ciz(t.p, kirik_yol([(x + 4, y + 18), (x + 66, y + 18)]), tur)
        r = t.yaz(x + 82, y - 1, ad, "govde", 27, C.TEXT_2, kayit="lejant")
        x = r.right() + 44
    return x


# ---- cihazlar ---------------------------------------------------------------------------------------

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


PS5_ORAN = 200 / 360          # PS5 çiziminin genişlik/yükseklik oranı


def ps5(t: Tuval, x: float, y: float, yuk: float = 360, kayit: bool = True) -> QRectF:
    """Dik duran PS5, önden-yandan (3/4) görünüş: önde ince beyaz kanat ve siyah gövde, yanda üstü
    geniş, aşağı doğru daralan büyük beyaz kanat, altta yuvarlak ayak. Yerel ölçü 200×360."""
    s = yuk / 360
    with Yerel(t.p, x, y, s) as p:
        # ayak
        p.setBrush(qc(GOVDE))
        p.setPen(kalem(GOVDE_KENAR, 2))
        p.drawEllipse(QPointF(112, 346), 62, 10)
        # siyah gövde (iki kanadın arasında görünür)
        govde = QPainterPath(QPointF(24, 16))
        govde.lineTo(188, 28)
        govde.quadTo(192, 28, 192, 34)
        govde.lineTo(190, 332)
        govde.quadTo(190, 338, 184, 338)
        govde.lineTo(34, 342)
        govde.closeSubpath()
        sekil(p, govde, SIYAH, SIYAH_KENAR, 2)
        # öndeki kanat: kenardan görünür, ince; üstte dışa taşar
        on = QPainterPath(QPointF(6, 2))
        on.lineTo(28, 8)
        on.cubicTo(28, 120, 40, 250, 40, 338)
        on.lineTo(30, 338)
        on.cubicTo(26, 250, 4, 112, 6, 2)
        sekil(p, on, BEYAZ, BEYAZ_KENAR, 1.5)
        # yandaki büyük kanat: üstte geniş; ön kenarı içe kıvrılıp aşağıda daralır,
        # altta siyah gövde açığa çıkar (PS5'in tanınan çizgisi)
        yan = QPainterPath(QPointF(44, 0))
        yan.cubicTo(110, 4, 160, 10, 199, 18)
        yan.cubicTo(200, 110, 193, 240, 181, 330)
        yan.cubicTo(160, 333, 136, 335, 114, 337)
        yan.cubicTo(84, 250, 40, 112, 44, 0)
        sekil(p, yan, BEYAZ, BEYAZ_KENAR, 1.5)
        # kanadın kıvrımı (ön kenar boyunca gölge)
        g = QPainterPath(QPointF(56, 10))
        g.cubicTo(54, 110, 92, 240, 120, 326)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(kalem(BEYAZ_GOLGE, 6))
        p.drawPath(g)
        # ön yüzdeki USB girişleri ve düğme
        kutu(p, QRectF(35, 44, 12, 6), 1.5, "#4A4670")
        kutu(p, QRectF(36, 58, 10, 5), 2.5, "#4A4670")
        p.setPen(kalem("#4A4670", 2))
        p.drawLine(QPointF(34, 28), QPointF(48, 29))
    r = QRectF(x, y, 200 * s, 358 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: PS5")
    return r


def dualsense(t: Tuval, x: float, y: float, gen: float = 240, kayit: bool = True) -> QRectF:
    """DualSense: beyaz gövde ve tutamaklar, siyah orta yüz, üstte dokunmatik yüzey. Yerel ölçü 240×160."""
    s = gen / 240
    with Yerel(t.p, x, y, s) as p:
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
        sekil(p, g, BEYAZ, BEYAZ_KENAR, 1.5)
        ic = QPainterPath(QPointF(70, 36))
        ic.lineTo(170, 36)
        ic.cubicTo(190, 36, 204, 44, 207, 60)
        ic.cubicTo(210, 80, 199, 98, 182, 102)
        ic.cubicTo(160, 108, 80, 108, 58, 102)
        ic.cubicTo(41, 98, 30, 80, 33, 60)
        ic.cubicTo(36, 44, 50, 36, 70, 36)
        sekil(p, ic, SIYAH)
        kutu(p, QRectF(82, 12, 76, 46), 9, "#ECEAF4", BEYAZ_KENAR, 1.5)    # dokunmatik yüzey
        # yön tuşları
        for rr in (QRectF(52, 50, 9, 10), QRectF(52, 70, 9, 10), QRectF(41, 60, 10, 9), QRectF(62, 60, 10, 9)):
            kutu(p, rr, 2, "#8D89A8")
        # dört yuvarlak tuş (sembolsüz)
        for dx, dy in ((0, -12), (12, 0), (0, 12), (-12, 0)):
            daire(p, QPointF(184 + dx, 64 + dy), 5.5, "#2A2740", "#8D89A8", 1.6)
        # analog çubuklar
        for cx in (95, 145):
            daire(p, QPointF(cx, 88), 14, "#2E2B45", "#55517A", 1.5)
            daire(p, QPointF(cx, 88), 8.5, "#45415F")
        daire(p, QPointF(120, 92), 4, "#8D89A8")
    r = QRectF(x, y + 12 * s, 240 * s, 156 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: DualSense")
    return r


def kasa(t: Tuval, x: float, y: float, yuk: float = 340, kayit: bool = True) -> QRectF:
    """Masaüstü bilgisayar kasası (önden). Yerel ölçü 170×340."""
    s = yuk / 340
    with Yerel(t.p, x, y, s) as p:
        kutu(p, QRectF(18, 326, 30, 12), 3, GOVDE_KENAR)
        kutu(p, QRectF(122, 326, 30, 12), 3, GOVDE_KENAR)
        kutu(p, QRectF(0, 0, 170, 330), 12, GOVDE, GOVDE_KENAR, 3)
        kutu(p, QRectF(14, 64, 142, 252), 7, GOVDE_KOYU, "#3B3762", 2)
        daire(p, QPointF(85, 32), 9, None, C.CYAN, 3)
        kutu(p, QRectF(38, 26, 18, 6), 1.5, "#4A4670")
        kutu(p, QRectF(114, 26, 18, 6), 1.5, "#4A4670")
        for cy in (118, 190, 262):          # fanlar
            daire(p, QPointF(85, cy), 29, None, "#3B3762", 3)
            daire(p, QPointF(85, cy), 8, "#3B3762")
        p.setPen(kalem(C.TURQUOISE, 3))
        p.drawLine(QPointF(24, 80), QPointF(24, 300))
    r = QRectF(x, y, 170 * s, 338 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: bilgisayar")
    return r


def ekran_cihazi(t: Tuval, x: float, y: float, ekran_gen: float, tur: str = "monitor",
                 icerik: Callable[[QPainter, QRectF], None] | None = None, kayit: bool = True):
    """Monitör (ortada ayak) ya da televizyon (iki yanda ayak). (çerçeve, ekran) alanlarını döner."""
    p = t.p
    ekran_yuk = ekran_gen * 9 / 16
    cer = (12 if tur == "monitor" else 8) * max(0.5, ekran_gen / 600)
    alt = cer * (1.0 if tur == "monitor" else 1.6)
    cr = QRectF(x, y, ekran_gen + 2 * cer, ekran_yuk + cer + alt)
    er = QRectF(x + cer, y + cer, ekran_gen, ekran_yuk)
    k = ekran_gen / 600
    if tur == "monitor":
        cx = cr.center().x()
        kutu(p, QRectF(cx - 16 * k, cr.bottom() - 2, 32 * k, 52 * k), 3, GOVDE, GOVDE_KENAR, 2)
        kutu(p, QRectF(cx - 100 * k, cr.bottom() + 46 * k, 200 * k, 14 * k), 7 * k, GOVDE, GOVDE_KENAR, 2)
        taban = cr.bottom() + 60 * k
    else:
        for fx, yon in ((0.12, -1), (0.88, 1)):
            ax, b = cr.left() + cr.width() * fx, cr.bottom()
            poligon(p, [(ax - 6 * k, b - 2), (ax + 6 * k, b - 2), (ax + yon * 18 * k + 6 * k, b + 32 * k),
                        (ax + yon * 18 * k - 6 * k, b + 32 * k)], GOVDE, GOVDE_KENAR, 2)
        taban = cr.bottom() + 34 * k
    kutu(p, cr, 10 * k + 2, GOVDE_KOYU, GOVDE_KENAR, 3)
    if icerik:
        p.save()
        yol = QPainterPath()
        yol.addRoundedRect(er, 3, 3)
        p.setClipPath(yol)
        icerik(p, er)
        p.restore()
    if kayit:
        t.kaydet_alan(QRectF(cr.left(), cr.top(), cr.width(), taban - cr.top()), f"cihaz: {tur}")
    return cr, er


def modem(t: Tuval, x: float, y: float, gen: float = 200, kayit: bool = True) -> QRectF:
    """Antenli ev modemi. Yerel ölçü 200×140."""
    s = gen / 200
    with Yerel(t.p, x, y, s) as p:
        for ax, aci in ((44, -10), (156, 10)):
            p.save()
            p.translate(ax, 76)
            p.rotate(aci)
            kutu(p, QRectF(-6, -74, 12, 78), 6, "#D3D0E2", BEYAZ_KENAR, 1.5)
            p.restore()
        kutu(p, QRectF(0, 70, 200, 64), 16, "#E6E4F0", BEYAZ_KENAR, 2)
        kutu(p, QRectF(10, 112, 180, 14), 7, "#D3D0E2")
        for i, renk in enumerate((C.TURQUOISE, C.TURQUOISE, C.CYAN, "#B9B5CF", "#B9B5CF")):
            daire(p, QPointF(56 + i * 22, 94), 5, renk)
    r = QRectF(x, y, 200 * s, 136 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: modem")
    return r


def capture_karti(t: Tuval, x: float, y: float, gen: float = 170, kayit: bool = True) -> QRectF:
    """Küçük capture kartı kutusu. Yerel ölçü 170×86."""
    s = gen / 170
    with Yerel(t.p, x, y, s) as p:
        kutu(p, QRectF(0, 0, 170, 86), 14, GOVDE, GOVDE_KENAR, 3)
        kutu(p, QRectF(10, 10, 150, 30), 8, "#34305A")
        daire(p, QPointF(148, 25), 5, C.CYAN)
        for i in range(5):                         # havalandırma çizgileri
            p.setPen(kalem("#4A4670", 3))
            p.drawLine(QPointF(24 + i * 16, 18), QPointF(24 + i * 16, 32))
        # ön yüzde iki HDMI girişi (yamuk) ve bir USB-C (oval)
        for hx in (22, 70):
            poligon(p, [(hx, 54), (hx + 30, 54), (hx + 30, 62), (hx + 26, 68), (hx + 4, 68), (hx, 62)],
                    GOVDE_KOYU, METAL_KOYU, 1.5)
        kutu(p, QRectF(122, 56, 28, 11), 5.5, GOVDE_KOYU, METAL_KOYU, 1.5)
    r = QRectF(x, y, 170 * s, 86 * s)
    if kayit:
        t.kaydet_alan(r, "cihaz: capture kartı")
    return r


# ---- oyun ekranı ve Türkçe kutu ------------------------------------------------------------------------

def _palmiye(p: QPainter, x: float, taban: float, boy: float, yon: float = 1.0) -> None:
    renk = "#0B0817"
    govde = QPainterPath(QPointF(x, taban))
    tepe = QPointF(x + yon * boy * 0.16, taban - boy)
    govde.quadTo(QPointF(x + yon * boy * 0.02, taban - boy * 0.55), tepe)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(renk, boy * 0.055))
    p.drawPath(govde)
    for aci, uz in ((-160, 0.42), (-125, 0.36), (-60, 0.36), (-20, 0.42), (-195, 0.34), (15, 0.34)):
        a = math.radians(aci)
        uc = QPointF(tepe.x() + math.cos(a) * boy * uz, tepe.y() + math.sin(a) * boy * uz + boy * 0.12)
        orta = QPointF((tepe.x() + uc.x()) / 2, min(tepe.y(), uc.y()) - boy * 0.08)
        yap = QPainterPath(tepe)
        yap.quadTo(orta, uc)
        p.setPen(kalem(renk, boy * 0.045))
        p.drawPath(yap)


def oyun_sahnesi(p: QPainter, r: QRectF, tr: str | None = ORNEK_TR, en: str | None = ORNEK_EN,
                 karart: float = 0.0) -> dict:
    """Düz (flat) gece sahnesi: gün batımı, şehir, deniz, palmiyeler; altta İngilizce satır ve
    üstünde Robz Vision'ın Türkçe kutusu. Altyazı alanlarını döner."""
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    ufuk = y + h * 0.62
    p.save()
    p.setClipRect(r)
    for f0, f1, renk in ((0.0, 0.24, "#1D1340"), (0.24, 0.40, "#2C1752"), (0.40, 0.52, "#461C62"),
                         (0.52, 0.62, "#6A2467")):
        p.fillRect(QRectF(x, y + h * f0, w, h * (f1 - f0) + 1), qc(renk))
    # güneş: üstü turuncu, altı pembe, alt kısmı şeritli
    gx, gr = x + w * 0.66, h * 0.21
    p.save()
    p.setClipRect(QRectF(x, y, w, ufuk - y))
    daire(p, QPointF(gx, ufuk), gr, C.PINK)
    p.setClipRect(QRectF(x, ufuk - gr * 1.1, w, gr * 0.55))
    daire(p, QPointF(gx, ufuk), gr, C.ORANGE)
    p.restore()
    for i, f in enumerate((0.30, 0.18, 0.08)):
        p.fillRect(QRectF(gx - gr, ufuk - gr * f - 1.6 - i * 0.6, gr * 2, 2.2 + i * 1.0), qc("#6A2467"))
    # şehir silueti
    bx = x + w * 0.04
    for i, (bw, bh) in enumerate(((0.05, 0.18), (0.035, 0.26), (0.06, 0.14), (0.03, 0.31), (0.05, 0.20),
                                  (0.04, 0.12), (0.055, 0.23), (0.03, 0.15))):
        rr = QRectF(bx, ufuk - h * bh, w * bw, h * bh + 1)
        p.fillRect(rr, qc("#150F2E"))
        for k in range(int(bh * 20)):
            if (i + k) % 3 == 0:
                p.fillRect(QRectF(rr.x() + rr.width() * 0.3, rr.y() + 4 + k * h * 0.045, max(1.5, w * 0.006),
                                  max(1.5, h * 0.012)), qc("#FFB86B", 0.55))
        bx += w * bw + w * 0.008
    # deniz
    p.fillRect(QRectF(x, ufuk, w, h - (ufuk - y)), qc("#112447"))
    for i, (f, uz) in enumerate(((0.05, 0.16), (0.11, 0.12), (0.18, 0.08))):
        p.fillRect(QRectF(gx - w * uz / 2, ufuk + h * f, w * uz, max(2, h * 0.008)), qc(C.PINK, 0.55 - i * 0.12))
    _palmiye(p, x + w * 0.10, y + h * 1.02, h * 0.70, 1.0)
    _palmiye(p, x + w * 0.93, y + h * 1.02, h * 0.62, -1.0)
    if karart:
        p.fillRect(r, qc("#000000", karart))
    alanlar: dict = {}
    en_px, tr_px = h * 0.064, h * 0.070
    if h < 140 and (en or tr):
        # küçük ekran: okunamayacak yazı yerine çubuklar (beyaz İngilizce satır, üstünde koyu Türkçe kutu)
        cx = x + w / 2
        if en:
            kutu(p, QRectF(cx - w * 0.30, y + h * 0.86, w * 0.60, max(2.5, h * 0.05)), 2, "#FFFFFF")
        if tr:
            kr = QRectF(cx - w * 0.30, y + h * 0.67, w * 0.60, max(7.0, h * 0.14))
            kutu(p, kr, 2.5, qc("#0A0912", 0.9))
            kutu(p, kr.adjusted(w * 0.06, kr.height() * 0.36, -w * 0.06, -kr.height() * 0.36), 1.5, "#FFFFFF")
        p.restore()
        return alanlar
    if en:
        f = yazi_tipi("kalin", en_px)
        fm = QFontMetricsF(f)
        sw = fm.horizontalAdvance(en)
        taban = y + h * 0.925
        yol = QPainterPath()
        yol.addText(QPointF(x + (w - sw) / 2, taban), f, en)
        p.strokePath(yol, kalem("#000000", max(2.0, en_px * 0.2)))
        p.fillPath(yol, qc("#FFFFFF"))
        alanlar["en"] = QRectF(x + (w - sw) / 2, taban - fm.ascent(), sw, fm.ascent() + fm.descent())
    if tr:
        f = yazi_tipi("kalin", tr_px)
        fm = QFontMetricsF(f)
        sw = fm.horizontalAdvance(tr)
        kh = tr_px * 1.55
        ky = y + h * 0.925 - en_px * 1.1 - kh - h * 0.02
        kr = QRectF(x + (w - sw) / 2 - tr_px * 0.55, ky, sw + tr_px * 1.1, kh)
        kutu(p, kr, tr_px * 0.22, qc("#0A0912", 0.86))
        p.setFont(f)
        p.setPen(qc("#FFFFFF"))
        p.drawText(QPointF(x + (w - sw) / 2, ky + (kh - fm.ascent() - fm.descent()) / 2 + fm.ascent()), tr)
        alanlar["tr"] = kr
    p.restore()
    return alanlar


# ---- ortak parçalar -------------------------------------------------------------------------------------

def rozet(t: Tuval, x: float, y: float, metin: str, px: float = 34, dolgu=C.CYAN, yazi=C.INVERSE,
          yuk: float | None = None, kayit: str | None = "rozet") -> QRectF:
    f = yazi_tipi("etiket", px)
    fm = QFontMetricsF(f)
    yuk = yuk or px * 1.6
    r = QRectF(x, y, fm.horizontalAdvance(metin) + px * 1.0, yuk)
    kutu(t.p, r, yuk * 0.28, dolgu)
    t.p.setFont(f)
    t.p.setPen(qc(yazi))
    t.p.drawText(QPointF(r.x() + px * 0.5, r.y() + (yuk - fm.ascent() - fm.descent()) / 2 + fm.ascent()), metin)
    if kayit:
        t.kaydet_alan(r, f"{kayit}: {metin}")
    return r


def baslik(t: Tuval, ana: str, alt: str | None = None, etiket: str | None = None) -> None:
    x = M
    if etiket:
        r = rozet(t, M, 40, etiket, 36, yuk=60)
        x = r.right() + 20
    t.yaz(x, 34, ana, "baslik", 60, C.TEXT, satir=1.15)
    if alt:
        t.yaz(M, 116, alt, "govde", 29, C.TEXT_2, gen=W - 2 * M, en_cok=1)


def yol_rozetleri(t: Tuval, x: float, y: float, gerekli: set[int], boyut: float = 38) -> float:
    """1-4 rozetleri: gereken yol dolu camgöbeği, gerekmeyen soluk çerçeve."""
    f = yazi_tipi("etiket", boyut * 0.66)
    fm = QFontMetricsF(f)
    for i in range(1, 5):
        r = QRectF(x + (i - 1) * (boyut + 8), y, boyut, boyut)
        if i in gerekli:
            kutu(t.p, r, 8, C.CYAN)
            renk = C.INVERSE
        else:
            kutu(t.p, r.adjusted(1, 1, -1, -1), 8, None, C.LINE_STRONG, 2)
            renk = C.TEXT_DISABLED
        t.p.setFont(f)
        t.p.setPen(qc(renk))
        s = str(i)
        t.p.drawText(QPointF(r.center().x() - fm.horizontalAdvance(s) / 2,
                             r.y() + (boyut - fm.ascent() - fm.descent()) / 2 + fm.ascent()), s)
    t.kaydet_alan(QRectF(x, y, 4 * boyut + 24, boyut), "yol rozetleri")
    return x + 4 * boyut + 24


_LOGO_ONBELLEK: dict[int, QImage] = {}


def logo(p: QPainter, x: float, y: float, boyut: float) -> None:
    n = round(boyut)
    if n not in _LOGO_ONBELLEK:
        _LOGO_ONBELLEK[n] = QImage(str(LOGO)).scaled(n, n, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)
    p.drawImage(QPointF(x, y), _LOGO_ONBELLEK[n])


def simge(p: QPainter, tur: str, x: float, y: float, b: float) -> None:
    """Uygulama simgeleri: logo (Robz Vision), akis (uzaktan oynatma), kayit (OBS; logosu çizilmez)."""
    if tur == "logo":
        logo(p, x, y, b)
    elif tur == "akis":
        kutu(p, QRectF(x + 2, y + 2, b - 4, b - 4), b * 0.24, GOVDE_KOYU, C.CYAN, 3)
        poligon(p, [(x + b * 0.40, y + b * 0.30), (x + b * 0.40, y + b * 0.70), (x + b * 0.72, y + b * 0.50)],
                C.CYAN)
    elif tur == "kayit":
        kutu(p, QRectF(x + 2, y + 2, b - 4, b - 4), b * 0.24, GOVDE_KOYU, C.TEXT_2, 3)
        daire(p, QPointF(x + b / 2, y + b / 2), b * 0.22, None, C.TEXT, 3)
        daire(p, QPointF(x + b / 2, y + b / 2), b * 0.09, C.PINK)


def uygulama_cipi(t: Tuval, r: QRectF, tur: str, ad: str, aciklama: str) -> None:
    kutu(t.p, r, 12, C.RAISED, C.LINE_STRONG, 2)
    b = 54
    simge(t.p, tur, r.x() + 16, r.center().y() - b / 2, b)
    tx = r.x() + 16 + b + 16
    t.yaz(tx, r.y() + 4, ad, "etiket", 30, C.TEXT, gen=r.right() - tx - 12, en_cok=1, kayit=None)
    t.yaz(tx, r.y() + 36, aciklama, "govde", 25, C.TEXT_2, gen=r.right() - tx - 12, en_cok=1, kayit=None)


def bilgisayar_bandi(t: Tuval, y: float, pc_alan: QRectF, baslik_metni: str, cipler, baslik_renk=C.TEXT_2,
                     yuk: float = 136) -> QRectF:
    """Altta 'bilgisayarda çalışanlar' bandı; üst kenarındaki çentik bilgisayarı gösterir."""
    p = t.p
    r = QRectF(M, y, W - 2 * M, yuk)
    kutu(p, r, 14, C.PANEL, C.LINE_STRONG, 2)
    cx = pc_alan.center().x()
    # bilgisayardan banda ince bağlantı
    p.setPen(kalem(C.LINE_STRONG, 3, [1.5, 2.0]))
    p.drawLine(QPointF(cx, pc_alan.bottom() + 6), QPointF(cx, y - 12))
    poligon(p, [(cx - 14, y + 1), (cx, y - 13), (cx + 14, y + 1)], C.PANEL)
    p.setPen(kalem(C.LINE_STRONG, 2))
    p.drawLine(QPointF(cx - 14, y + 1), QPointF(cx, y - 13))
    p.drawLine(QPointF(cx, y - 13), QPointF(cx + 14, y + 1))
    t.yaz(r.x() + 24, r.y() + 10, baslik_metni, "govde_k", 26, baslik_renk, gen=r.width() - 48, en_cok=1,
          kayit="bant başlığı")
    n = len(cipler)
    gap = 20
    cw = (r.width() - 48 - gap * (n - 1)) / n
    for i, (tur, ad, ack) in enumerate(cipler):
        uygulama_cipi(t, QRectF(r.x() + 24 + i * (cw + gap), r.y() + 46, cw, 68), tur, ad, ack)
    t.kaydet_alan(r, "kap: bilgisayar bandı")
    return r


def bilgi_isareti(p: QPainter, cx: float, cy: float, renk: str, tur: str = "bilgi", b: float = 30) -> None:
    if tur == "uyari":
        poligon(p, [(cx, cy - b * 0.5), (cx + b * 0.56, cy + b * 0.46), (cx - b * 0.56, cy + b * 0.46)],
                None, renk, 3)
        p.setPen(kalem(renk, 3.2))
        p.drawLine(QPointF(cx, cy - b * 0.15), QPointF(cx, cy + b * 0.14))
        daire(p, QPointF(cx, cy + b * 0.3), 2.2, renk)
    elif tur == "tamam":
        daire(p, QPointF(cx, cy), b / 2, None, renk, 3)
        p.setPen(kalem(renk, 3.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        yol = QPainterPath(QPointF(cx - b * 0.22, cy + b * 0.02))
        yol.lineTo(cx - b * 0.05, cy + b * 0.18)
        yol.lineTo(cx + b * 0.24, cy - b * 0.16)
        p.drawPath(yol)
    elif tur == "carpi":
        daire(p, QPointF(cx, cy), b / 2, C.BG, renk, 3)
        p.setPen(kalem(renk, 3.6))
        d = b * 0.18
        p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
    else:
        daire(p, QPointF(cx, cy), b / 2, None, renk, 3)
        p.setPen(kalem(renk, 3.4))
        p.drawLine(QPointF(cx, cy - b * 0.02), QPointF(cx, cy + b * 0.24))
        daire(p, QPointF(cx, cy - b * 0.2), 2.4, renk)


def not_kutusu(t: Tuval, r: QRectF, metin: str, tur: str = "bilgi", px: float = 26, kalin_bas: str | None = None,
               kesik: bool = False) -> QRectF:
    """Solunda işaret olan not. tur: bilgi (camgöbeği) | uyari (mercan) | tamam (turkuaz)."""
    renk = {"bilgi": C.CYAN, "uyari": C.ORANGE, "tamam": C.TURQUOISE}[tur]
    kutu(t.p, r, 12, qc(renk, 0.08), qc(renk, 0.6), 2, [3, 2.5] if kesik else None)
    bilgi_isareti(t.p, r.x() + 34, r.y() + r.height() / 2, renk, tur, 30)
    tx = r.x() + 64
    gen = r.right() - tx - 18
    ic_yuk = 0.0
    if kalin_bas:
        ic_yuk += t.olc(kalin_bas, "govde_k", px, gen)[1]
    ic_yuk += t.olc(metin, "govde", px, gen)[1]
    y = r.y() + (r.height() - ic_yuk) / 2
    if kalin_bas:
        y = t.yaz(tx, y, kalin_bas, "govde_k", px, C.TEXT, gen=gen, kayit=None).bottom()
    t.yaz(tx, y, metin, "govde", px, C.TEXT if not kalin_bas else C.TEXT_2, gen=gen, kayit=None)
    if ic_yuk > r.height() - 8:
        t.uyarilar.append(f"not kutusu taşıyor: {metin[:30]!r}")
    t.kaydet_alan(r, f"not: {metin[:24]!r}")
    return r


def etiket_cizgisi(p: QPainter, a: tuple[float, float], b: tuple[float, float], renk=C.TEXT_2) -> None:
    """Açıklama yazısından nesneye ince çizgi ve uçta nokta."""
    p.setPen(kalem(renk, 2.5))
    p.drawLine(QPointF(*a), QPointF(*b))
    daire(p, QPointF(*b), 5, renk)


# =====================================================================================================
# Çizimler
# =====================================================================================================

def ciz_neler_lazim(t: Tuval) -> None:
    p = t.p
    baslik(t, "Neler lazım?", "Kutucuklardaki numaralar, malzemenin hangi yolda gerektiğini gösterir.")
    ust, alt = 172, 818
    # --- solda büyük hücre: bilgisayar (her yolda gerekir)
    hr = QRectF(M, ust, 380, alt - ust)
    kutu(p, hr, 14, C.PANEL, C.LINE, 2)
    kasa(t, hr.x() + 26, hr.y() + 96, 196, kayit=False)
    ekran_cihazi(t, hr.x() + 118, hr.y() + 40, 230, "monitor", lambda pp, er: oyun_sahnesi(pp, er, None, None),
                 kayit=False)
    y = hr.y() + 322
    y = t.yaz(hr.x() + 24, y, "Windows 10/11 bilgisayar", "etiket", 34, C.TEXT, gen=hr.width() - 48).bottom()
    y = t.yaz(hr.x() + 24, y + 4, "Kasa ya da dizüstü, ekranıyla", "govde", 25, C.TEXT_2,
              gen=hr.width() - 48).bottom()
    gr = QRectF(hr.x() + 20, y + 18, hr.width() - 40, 104)
    not_kutusu(t, gr, "NVIDIA 8 GB+ önerilir", "bilgi", 25, kalin_bas="Ekran kartı")
    yol_rozetleri(t, hr.x() + 24, hr.bottom() - 62, {1, 2, 3, 4})

    # --- sağda 4×2 ızgara
    gx0 = hr.right() + 20
    gap = 16
    cw = (W - M - gx0 - 3 * gap) / 4
    ch = (alt - ust - gap) / 2

    def hucre(i: int, ad: str, not_metni: str, gerekli: set[int], ciz: Callable[[QRectF], None]) -> None:
        col, row = i % 4, i // 4
        r = QRectF(gx0 + col * (cw + gap), ust + row * (ch + gap), cw, ch)
        kutu(p, r, 14, C.PANEL, C.LINE, 2)
        ciz(QRectF(r.x() + 12, r.y() + 12, r.width() - 24, 146))
        yy = t.yaz(r.x() + 18, r.y() + 156, ad, "etiket", 32, C.TEXT, gen=r.width() - 36, en_cok=1).bottom()
        t.yaz(r.x() + 18, yy, not_metni, "govde", 25, C.TEXT_2, gen=r.width() - 30, en_cok=2, satir=1.14)
        yol_rozetleri(t, r.x() + 18, r.bottom() - 50, gerekli, 36)

    def c_ps5(a: QRectF) -> None:
        ps5(t, a.center().x() - 140 * PS5_ORAN / 2, a.y() + 2, 140, kayit=False)

    def c_kol(a: QRectF) -> None:
        dualsense(t, a.center().x() - 95, a.y() + 10, 190, kayit=False)

    def c_usb(a: QRectF) -> None:
        cy = a.y() + 34
        _kablo_ciz(p, kirik_yol([(a.x() + 62, cy), (a.x() + 96, cy), (a.x() + 96, cy + 40), (a.right() - 96, cy + 40),
                                 (a.right() - 96, cy), (a.right() - 62, cy)], 20), "usb")
        fis(p, (a.x() + 12, cy), (-1, 0), "usba", C.CYAN, 1.2)
        fis(p, (a.right() - 12, cy), (1, 0), "usbc", C.CYAN, 1.2)
        # uçların ön yüzü ve adları (USB-A bilgisayara, USB-C kola takılır)
        yuz(p, "usba", QPointF(a.x() + 46, a.y() + 98))
        yuz(p, "usbc", QPointF(a.right() - 46, a.y() + 98))
        t.yaz(a.x() + 46, a.y() + 116, "USB-A", "govde_k", 19, C.TEXT_2, hiza="orta", kayit=None)
        t.yaz(a.right() - 46, a.y() + 116, "USB-C", "govde_k", 19, C.TEXT_2, hiza="orta", kayit=None)

    def c_hdmi(a: QRectF) -> None:
        cy = a.y() + 48
        _kablo_ciz(p, kirik_yol([(a.x() + 30, cy + 70), (a.x() + 30, cy), (a.right() - 92, cy)], 30), "hdmi")
        fis(p, (a.right() - 30, cy), (1, 0), "hdmi", C.PINK, 1.3)
        yuz(p, "hdmi", QPointF(a.center().x() + 10, a.y() + 116))

    def c_eth(a: QRectF) -> None:
        cy = a.y() + 56
        _kablo_ciz(p, kirik_yol([(a.x() + 30, cy + 70), (a.x() + 30, cy), (a.right() - 92, cy)], 30), "eth")
        fis(p, (a.right() - 34, cy), (1, 0), "rj45", C.TURQUOISE, 1.45)
        yuz(p, "rj45", QPointF(a.center().x() + 10, a.y() + 118))

    def c_modem(a: QRectF) -> None:
        modem(t, a.center().x() - 90, a.y() + 14, 180, kayit=False)

    def c_tv(a: QRectF) -> None:
        ekran_cihazi(t, a.center().x() - 104, a.y() + 14, 200, "tv",
                     lambda pp, er: oyun_sahnesi(pp, er, None, None), kayit=False)

    def c_capture(a: QRectF) -> None:
        capture_karti(t, a.center().x() - 85, a.y() + 36, 170, kayit=False)

    hucre(0, "PS5", "Her PS5 modeli olur", {2, 3, 4}, c_ps5)
    hucre(1, "DualSense kol", "Yol 2 ve 3'te bilgisayara bağlanır", {2, 3, 4}, c_kol)
    hucre(2, "USB-C kablo", "PS5 kutusundan çıkan şarj kablosu", {2, 3}, c_usb)
    hucre(3, "HDMI kablo", "Yol 4'te iki tane gerekir", {3, 4}, c_hdmi)
    hucre(4, "Ethernet kablosu", "Önerilir: Wi-Fi'den akıcı", {2, 3}, c_eth)
    hucre(5, "Modem", "Evdeki internet modemi", {2, 3}, c_modem)
    hucre(6, "Televizyon", "HDMI girişi olan her marka", {3}, c_tv)
    hucre(7, "Capture kartı", "İsteğe bağlı: en net görüntü", {4}, c_capture)

    # --- alt açıklama: yolların adları
    x = M
    for i, ad in enumerate(("Bilgisayarda", "PS5 + bilgisayar", "PS5 + bilgisayar + TV", "Capture kartı"), 1):
        r = QRectF(x, 842, 36, 36)
        kutu(p, r, 8, C.CYAN)
        f = yazi_tipi("etiket", 24)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(qc(C.INVERSE))
        p.drawText(QPointF(r.center().x() - fm.horizontalAdvance(str(i)) / 2,
                           r.y() + (36 - fm.ascent() - fm.descent()) / 2 + fm.ascent()), str(i))
        rr = t.yaz(x + 48, 843, ad, "govde", 26, C.TEXT_2, kayit="yol adı")
        x = rr.right() + 40


def yuz(p: QPainter, tur: str, c: QPointF) -> None:
    """Fişin ön yüzü (girişe giren kısım), gerçek biçimiyle: HDMI yamuk, USB-C oval, USB-A dikdörtgen,
    RJ45 tırnaklı. c merkez noktası."""
    x, y = c.x(), c.y()
    if tur == "hdmi":
        poligon(p, [(x - 34, y - 12), (x + 34, y - 12), (x + 34, y + 2), (x + 24, y + 12), (x - 24, y + 12),
                    (x - 34, y + 2)], METAL, METAL_KOYU, 2)
        poligon(p, [(x - 26, y - 6), (x + 26, y - 6), (x + 26, y + 1), (x + 19, y + 6), (x - 19, y + 6),
                    (x - 26, y + 1)], "#2A2740")
        kutu(p, QRectF(x - 20, y - 3, 40, 4), 1, ALTIN)
    elif tur == "usbc":
        kutu(p, QRectF(x - 26, y - 10, 52, 20), 10, METAL, METAL_KOYU, 2)
        kutu(p, QRectF(x - 18, y - 4, 36, 8), 4, "#2A2740")
    elif tur == "usba":
        kutu(p, QRectF(x - 32, y - 13, 64, 26), 2, METAL, METAL_KOYU, 2)
        kutu(p, QRectF(x - 26, y - 8, 52, 16), 1, "#2A2740")
        kutu(p, QRectF(x - 26, y - 8, 52, 7), 1, "#E8E6F2")
    elif tur == "rj45":
        kutu(p, QRectF(x - 24, y - 16, 48, 32), 3, "#D8F3EE", C.TURQUOISE, 2)
        for i in range(8):
            kutu(p, QRectF(x - 19 + i * 5, y - 14, 3, 9), 0.5, ALTIN)
        kutu(p, QRectF(x - 9, y + 6, 18, 12), 2, C.TURQUOISE)   # tırnak


def _kucuk_ok(p: QPainter, x1: float, x2: float, y: float) -> None:
    ok(p, [(x1, y), (x2, y)], C.TEXT_3, 3, 11)


def numara(p: QPainter, cx: float, cy: float, r: float, n: str, dolgu=C.CYAN, yazi=C.INVERSE) -> None:
    daire(p, QPointF(cx, cy), r, dolgu)
    f = yazi_tipi("etiket", r * 1.15)
    fm = QFontMetricsF(f)
    p.setFont(f)
    p.setPen(qc(yazi))
    p.drawText(QPointF(cx - fm.horizontalAdvance(n) / 2, cy - (fm.ascent() + fm.descent()) / 2 + fm.ascent()), n)


def ciz_yol_secimi(t: Tuval) -> None:
    p = t.p
    baslik(t, "Hangi yol senin?", "Oyunu nerede oynadığına göre seç. Robz Vision her yolda bilgisayarda çalışır.")
    q = QRectF(W / 2 - 330, 170, 660, 80)
    kutu(p, q, 16, C.RAISED, C.CYAN, 3)
    t.yaz(q.x(), q.y() + 14, "Oyunu nerede oynuyorsun?", "etiket", 42, C.TEXT, gen=q.width(), hiza="orta",
          kayit="soru")
    gap = 24
    cw = (W - 2 * M - 3 * gap) / 4
    merkezler = [M + cw / 2 + i * (cw + gap) for i in range(4)]
    bus_y = 280
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(C.TEXT_3, 4))
    p.drawLine(QPointF(W / 2, q.bottom()), QPointF(W / 2, bus_y))
    p.drawPath(kirik_yol([(merkezler[0], 296), (merkezler[0], bus_y), (merkezler[3], bus_y), (merkezler[3], 296)],
                         18))
    for cx in merkezler[1:3]:
        p.drawLine(QPointF(cx, bus_y), QPointF(cx, 296))
    cevaplar = ("Bilgisayarda\n(PC oyunu)", "PS5, bilgisayar ekranında", "PS5, televizyonda",
                "PS5, capture kartıyla\n(en net görüntü, yayın)")
    aciklamalar = ("Ek bir şey gerekmez", "chiaki-ng ile, aynı ağda", "Bilgisayardan TV'ye HDMI",
                   "OBS ile, en net görüntü")
    sonuclar = (("Yol 1", "Bilgisayarda"), ("Yol 2", "PS5 + bilgisayar"), ("Yol 3", "PS5 + bilgisayar + TV"),
                ("Yol 4", "Capture kartı"))
    for i, cx in enumerate(merkezler):
        x0 = cx - cw / 2
        ok(p, [(cx, 292), (cx, 320)], C.TEXT_3, 4, 16)
        a = QRectF(x0, 322, cw, 84)
        kutu(p, a, 14, None, C.LINE_STRONG, 2)
        _, ah, _ = t.olc(cevaplar[i], "govde_k", 27, cw - 32)
        t.yaz(x0 + 16, a.y() + (a.height() - ah) / 2, cevaplar[i], "govde_k", 27, C.TEXT, gen=cw - 32,
              hiza="orta", en_cok=2)
        ok(p, [(cx, a.bottom() + 4), (cx, a.bottom() + 34)], C.CYAN, 4, 16)
        s = QRectF(x0, a.bottom() + 38, cw, 304)
        kutu(p, s, 16, C.PANEL, C.CYAN, 2)
        rozet(t, x0 + 20, s.y() + 18, sonuclar[i][0], 30, yuk=48)
        yy = t.yaz(x0 + 20, s.y() + 76, sonuclar[i][1], "etiket", 36, C.TEXT, gen=cw - 40, en_cok=1).bottom()
        t.yaz(x0 + 20, yy + 2, aciklamalar[i], "govde", 25, C.TEXT_2, gen=cw - 40, en_cok=1)
        mini = QRectF(x0 + 12, s.bottom() - 134, cw - 24, 122)
        kutu(p, mini, 10, C.BG)
        _mini_zincir(t, i + 1, mini)
    # yayın kutusu
    b = QRectF(M, 778, W - 2 * M, 88)
    kutu(p, b, 16, qc(C.PINK, 0.07), C.PINK, 2.5, [3, 2.5])
    simge(p, "kayit", b.x() + 22, b.y() + 17, 54)
    r = t.yaz(b.x() + 96, b.y() + 22, "Yayın yapacak mısın?", "etiket", 36, C.TEXT, kayit="yayın sorusu")
    ok(p, [(r.right() + 22, b.center().y()), (r.right() + 70, b.center().y())], C.PINK, 4, 16)
    t.yaz(r.right() + 90, b.y() + 26, "OBS'e Tarayıcı kaynağı ekle. Her yolla çalışır.", "govde", 28, C.TEXT,
          gen=b.right() - r.right() - 110, en_cok=1, kayit="yayın yanıtı")


def _mini_zincir(t: Tuval, yol: int, a: QRectF) -> None:
    """Sonuç kartının altındaki küçük cihaz dizisi (ortalanır)."""
    p = t.p
    cy = a.center().y()
    sahne = lambda pp, er: oyun_sahnesi(pp, er)  # noqa: E731
    parcalar = {
        1: [("kasa", 50), ("monitor", 152)],
        2: [("ps5", 56), ("kasa", 48), ("monitor", 128)],
        3: [("ps5", 56), ("kasa", 48), ("tv", 130)],
        4: [("ps5", 56), ("capture", 86), ("kasa", 48)],
    }[yol]
    okg = 42
    toplam = sum(g for _, g in parcalar) + okg * (len(parcalar) - 1)
    x = a.x() + (a.width() - toplam) / 2
    for i, (tur, g) in enumerate(parcalar):
        if i:
            _kucuk_ok(p, x - okg + 8, x - 8, cy)
        if tur == "ps5":
            ps5(t, x, cy - 50, 100, kayit=False)
        elif tur == "kasa":
            kasa(t, x, cy - g, g * 2, kayit=False)
        elif tur == "monitor":
            ekran_cihazi(t, x, cy - 50, g - 12, "monitor", sahne, kayit=False)
        elif tur == "tv":
            ekran_cihazi(t, x, cy - 44, g - 8, "tv", sahne, kayit=False)
        elif tur == "capture":
            capture_karti(t, x, cy - 22, g, kayit=False)
        x += g + okg


# ---- Robz Vision kurulum adımlarının küçük ekran örnekleri ---------------------------------------------

def _m_indir(t: Tuval, m: QRectF) -> None:
    p = t.p
    w = m.adjusted(12, 12, -12, -12)
    kutu(p, w, 8, C.RAISED, C.LINE_STRONG, 2)
    kutu(p, QRectF(w.x(), w.y(), w.width(), 36), 8, C.PANEL)
    for i in range(3):
        daire(p, QPointF(w.x() + 18 + i * 14, w.y() + 18), 4, C.TEXT_3)
    t.yaz(w.x() + 66, w.y() + 5, "GitHub Releases", "govde_k", 19, C.TEXT_2, kayit=None)
    sat = QRectF(w.x() + 12, w.y() + 50, w.width() - 24, 50)
    kutu(p, sat, 8, qc(C.CYAN, 0.12), C.CYAN, 2.5)
    # indirme simgesi
    ix, iy = sat.x() + 22, sat.center().y()
    p.setPen(kalem(C.CYAN, 3))
    p.drawLine(QPointF(ix, iy - 12), QPointF(ix, iy + 4))
    p.drawLine(QPointF(ix - 7, iy - 3), QPointF(ix, iy + 4))
    p.drawLine(QPointF(ix + 7, iy - 3), QPointF(ix, iy + 4))
    p.drawLine(QPointF(ix - 10, iy + 11), QPointF(ix + 10, iy + 11))
    t.yaz(sat.x() + 40, sat.y() + 12, "RobzVision-Setup-x64.exe", "govde_k", 17, C.TEXT,
          gen=sat.width() - 58, kayit=None)
    for i, f in enumerate((0.62, 0.45)):
        kutu(p, QRectF(w.x() + 24, w.y() + 114 + i * 16, (w.width() - 48) * f, 7), 3.5, C.LINE_STRONG)
    imlec(p, sat.right() - 14, sat.bottom() - 10, 1.0)


def _m_uyari(t: Tuval, m: QRectF) -> None:
    p = t.p
    w = m.adjusted(12, 12, -12, -12)
    kutu(p, w, 6, "#1C5FA8")
    for i, f in enumerate((0.70, 0.86, 0.55)):
        kutu(p, QRectF(w.x() + 18, w.y() + 18 + i * 17, (w.width() - 36) * f, 8 if i == 0 else 6), 3,
             qc("#FFFFFF", 0.85 if i == 0 else 0.5))
    r = t.yaz(w.x() + 18, w.y() + 70, "Ek bilgi", "govde_k", 20, "#FFFFFF", kayit=None)
    p.setPen(kalem("#FFFFFF", 1.6))
    p.drawLine(QPointF(r.left(), r.bottom() - 2), QPointF(r.right(), r.bottom() - 2))
    f = yazi_tipi("govde_k", 19)
    bw = QFontMetricsF(f).horizontalAdvance("Yine de çalıştır") + 28
    b = QRectF(w.right() - bw - 14, w.bottom() - 46, bw, 34)
    kutu(p, b, 3, qc("#FFFFFF", 0.08), "#FFFFFF", 2)
    t.yaz(b.x(), b.y() + 4, "Yine de çalıştır", "govde_k", 19, "#FFFFFF", gen=b.width(), hiza="orta", kayit=None)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(C.CYAN, 3))
    p.drawRoundedRect(b.adjusted(-5, -5, 5, 5), 6, 6)
    imlec(p, b.right() - 8, b.bottom() - 6, 1.0)


def _m_sihirbaz(t: Tuval, m: QRectF) -> None:
    p = t.p
    w = m.adjusted(12, 12, -12, -12)
    kutu(p, w, 6, "#F3F2F8")
    sol = QRectF(w.x(), w.y(), 78, w.height())
    p.save()
    yol = QPainterPath()
    yol.addRoundedRect(w, 6, 6)
    p.setClipPath(yol)
    p.fillRect(sol, qc(C.PANEL))
    p.restore()
    logo(p, sol.center().x() - 24, sol.y() + 22, 48)
    for i, f in enumerate((0.75, 0.9, 0.6)):
        kutu(p, QRectF(sol.right() + 16, w.y() + 18 + i * 16, (w.width() - 110) * f, 7 if i == 0 else 6), 3,
             "#B9B5CF" if i else "#5C5878")
    f = yazi_tipi("govde_k", 19)
    for ad, sx, vurgu in (("Geri", 0.38, False), ("İleri", 0.70, True)):
        bw = QFontMetricsF(f).horizontalAdvance(ad) + 32
        b = QRectF(sol.right() + (w.right() - sol.right()) * sx - bw / 2, w.bottom() - 44, bw, 32)
        kutu(p, b, 4, "#FFFFFF" if vurgu else "#E6E4F0", "#8A86A6", 1.5)
        t.yaz(b.x(), b.y() + 3, ad, "govde_k", 19, "#1B1930" if vurgu else "#8A86A6", gen=b.width(), hiza="orta",
              kayit=None)
        if vurgu:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(kalem(C.CYAN, 3))
            p.drawRoundedRect(b.adjusted(-5, -5, 5, 5), 7, 7)
            imlec(p, b.right() - 6, b.bottom() - 6, 1.0)


def _m_secim(t: Tuval, m: QRectF) -> None:
    p = t.p
    t.yaz(m.x() + 16, m.y() + 8, "Çeviri nasıl yapılsın?", "govde_k", 19, C.TEXT_2, kayit=None)
    kw = (m.width() - 44) / 2
    for i, (ad, secili) in enumerate((("Yapay zekâ ile", True), ("Yalnız hızlı çeviri", False))):
        k = QRectF(m.x() + 16 + i * (kw + 12), m.y() + 42, kw, 108)
        if secili:
            kutu(p, k, 10, qc(C.CYAN, 0.14), C.CYAN, 3)
            bilgi_isareti(p, k.right() - 20, k.y() + 20, C.CYAN, "tamam", 22)
        else:
            kutu(p, k, 10, C.RAISED, C.LINE_STRONG, 2)
        t.yaz(k.x() + 12, k.y() + 40, ad, "govde_k", 19, C.TEXT if secili else C.TEXT_2, gen=kw - 22,
              kayit=None, satir=1.15)
    imlec(p, m.x() + 16 + kw - 22, m.y() + 128, 1.0)


def _m_modeller(t: Tuval, m: QRectF) -> None:
    p = t.p
    for i, (ad, oran, renk) in enumerate((("Hızlı çeviri modeli", 1.0, C.TURQUOISE), ("Yapay zekâ", 0.62, C.PINK))):
        y = m.y() + 18 + i * 72
        t.yaz(m.x() + 18, y, ad, "govde_k", 19, C.TEXT, kayit=None)
        bar = QRectF(m.x() + 18, y + 34, m.width() - 80, 10)
        kutu(p, bar, 5, C.LINE_STRONG)
        kutu(p, QRectF(bar.x(), bar.y(), bar.width() * oran, bar.height()), 5, renk)
        if oran >= 1.0:
            bilgi_isareti(p, bar.right() + 26, bar.center().y(), C.TURQUOISE, "tamam", 24)
        else:
            t.yaz(bar.right() + 8, y + 26, "%62", "etiket", 19, C.TEXT_2, kayit=None)


def _m_bolge(t: Tuval, m: QRectF) -> None:
    p = t.p
    alan = oyun_sahnesi(p, m, None, ORNEK_EN, karart=0.5)
    en = alan["en"]
    sec = en.adjusted(-12, -10, 12, 8)
    p.save()
    p.setClipRect(sec)
    oyun_sahnesi(p, m, None, ORNEK_EN)
    p.restore()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(C.CYAN, 3, [2.5, 1.6]))
    p.drawRect(sec)
    f = yazi_tipi("govde_k", 18)
    bw = QFontMetricsF(f).horizontalAdvance("Onayla") + 26
    b = QRectF(sec.right() - bw, sec.y() - 42, bw, 32)
    kutu(p, b, 6, C.PINK)
    t.yaz(b.x(), b.y() + 3, "Onayla", "govde_k", 18, C.INVERSE, gen=b.width(), hiza="orta", kayit=None)
    imlec(p, b.right() - 6, b.bottom() - 6, 1.0)


def _m_baslat(t: Tuval, m: QRectF) -> None:
    p = t.p
    band = QRectF(m.x() + 10, m.y() + 10, m.width() - 20, 62)
    kutu(p, band, 8, C.PANEL, C.LINE, 1.5)
    p.fillRect(QRectF(band.x() + 8, band.bottom() - 3, band.width() - 16, 3), qc(C.TURQUOISE))
    f = yazi_tipi("govde_k", 20)
    bw = QFontMetricsF(f).horizontalAdvance("Başlat") + 30
    b = QRectF(band.x() + 12, band.y() + 12, bw, 36)
    kutu(p, b, 6, C.PINK)
    t.yaz(b.x(), b.y() + 4, "Başlat", "govde_k", 20, C.INVERSE, gen=b.width(), hiza="orta", kayit=None)
    t.yaz(b.right() + 16, band.y() + 14, "Çalışıyor", "etiket", 24, C.TURQUOISE, kayit=None)
    imlec(p, b.right() - 6, b.bottom() - 6, 1.0)
    oyun_sahnesi(p, QRectF(m.x() + 10, band.bottom() + 8, m.width() - 20, m.bottom() - band.bottom() - 18))


def ciz_robz_vision_kurulum(t: Tuval) -> None:
    p = t.p
    baslik(t, "Robz Vision'ı kur", "Bir kez yapılır. Sonra her seferinde yalnız Başlat'a basarsın.")
    gap = 40
    cw = (W - 2 * M - 3 * gap) / 4
    ch = 300
    ys = (172, 172 + ch + 52)
    adimlar = (
        (_m_indir, "İndir", "GitHub Releases sayfasından"),
        (_m_uyari, "Windows uyarısı", "Ek bilgi →\nYine de çalıştır"),
        (_m_sihirbaz, "Kurulum sihirbazı", "İleri'ye bas, kurulsun"),
        (_m_secim, "Yapay zekâ ile", "Ekran kartın uygunsa seçili gelir"),
        (_m_modeller, "Modeller iner", "Kendiliğinden;\nyalnız ilk seferde"),
        (_m_bolge, "Bölge seç", "Altyazı alanını çiz\n→ Onayla"),
        (_m_baslat, "Başlat", "Türkçe altyazı\nekrana gelir"),
    )
    for i, (mock, ad, ack) in enumerate(adimlar):
        row, col = divmod(i, 4)
        x, y = M + col * (cw + gap), ys[row]
        r = QRectF(x, y, cw, ch)
        kutu(p, r, 16, C.PANEL, C.LINE_STRONG, 2)
        m = QRectF(x + 14, y + 14, cw - 28, 170)
        kutu(p, m, 10, C.BG)
        p.save()
        yol = QPainterPath()
        yol.addRoundedRect(m, 10, 10)
        p.setClipPath(yol)
        mock(t, m)
        p.restore()
        numara(p, x + 40, y + 222, 24, str(i + 1))
        t.yaz(x + 78, y + 202, ad, "etiket", 32, C.TEXT, gen=cw - 92, en_cok=1)
        t.yaz(x + 78, y + 241, ack, "govde", 25, C.TEXT_2, gen=cw - 92, en_cok=2, satir=1.12)
        t.kaydet_alan(r, f"kap: adım {i + 1}")
        if col < 3 and i < 6:
            ok(p, [(r.right() + 6, y + 99), (r.right() + gap - 6, y + 99)], C.CYAN, 4, 15)
    # 4. adımdan 5. adıma (alt satıra) dönen ok
    x4 = M + 3 * (cw + gap) + cw / 2
    x5 = M + cw / 2
    ok(p, [(x4, ys[0] + ch + 4), (x4, ys[0] + ch + 26), (x5, ys[0] + ch + 26), (x5, ys[1] - 4)], C.CYAN, 4, 15)
    # 8. kutu: unutma notu
    r = QRectF(M + 3 * (cw + gap), ys[1], cw, ch)
    kutu(p, r, 16, qc(C.TURQUOISE, 0.07), C.TURQUOISE, 2.5, [3, 2.5])
    bilgi_isareti(p, r.x() + 44, r.y() + 52, C.TURQUOISE, "tamam", 44)
    t.yaz(r.x() + 82, r.y() + 32, "Unutma", "etiket", 34, C.TEXT)
    t.yaz(r.x() + 24, r.y() + 100, "Oyunda İngilizce altyazıyı aç.", "govde_k", 28, C.TEXT, gen=cw - 48, en_cok=2)
    t.yaz(r.x() + 24, r.y() + 182, "Robz Vision ekranda gördüğü yazıyı okur; sesi dinlemez.", "govde", 24,
          C.TEXT_2, gen=cw - 48, en_cok=3, satir=1.15)
    t.kaydet_alan(r, "kap: unutma")


# ---- bağlantı şemaları --------------------------------------------------------------------------------------

def _ekran_etiketi(t: Tuval, sag_x: float, hedef: QRectF, metin: str, ust: bool = True) -> None:
    """Ekrandaki öğeye soldan açıklama: yazı sag_x'te biter, çizgi öğenin sol kenarına gider."""
    cy = hedef.center().y()
    _, h, _ = t.olc(metin, "govde_k", 27)
    r = t.yaz(sag_x, cy - h / 2, metin, "govde_k", 27, C.TEXT, hiza="sag", kayit="ekran etiketi")
    etiket_cizgisi(t.p, (r.right() + 12, cy), (hedef.left() - 14, cy), C.TEXT)


def ciz_yol1(t: Tuval) -> None:
    p = t.p
    baslik(t, "Bilgisayarda", "Oyun da Robz Vision da aynı bilgisayarda çalışır.", "Yol 1")
    pc = kasa(t, 70, 290, 360)
    t.yaz(pc.center().x(), 244, "Bilgisayar", "etiket", 30, C.TEXT, hiza="orta")
    alan: dict = {}
    cr, er = ekran_cihazi(t, 856, 190, 660, "monitor", lambda pp, r: alan.update(oyun_sahnesi(pp, r)))
    kablo(t, [(pc.right(), 400), (cr.left(), 400)], "hdmi")
    t.yaz((pc.right() + cr.left()) / 2, 348, "HDMI ya da DisplayPort", "govde", 26, C.TEXT_2, hiza="orta",
          kayit="kablo adı")
    _ekran_etiketi(t, 820, alan["tr"], "Robz Vision'ın Türkçe kutusu")
    _ekran_etiketi(t, 820, alan["en"], "Oyunun İngilizce altyazısı")
    # Robz Vision bandı: okur → çevirir → ekrana yazar
    band = QRectF(M, 712, W - 2 * M, 128)
    kutu(p, band, 14, C.PANEL, C.LINE_STRONG, 2)
    cx = pc.center().x()
    p.setPen(kalem(C.LINE_STRONG, 3, [1.5, 2.0]))
    p.drawLine(QPointF(cx, pc.bottom() + 6), QPointF(cx, band.y() - 12))
    poligon(p, [(cx - 14, band.y() + 1), (cx, band.y() - 13), (cx + 14, band.y() + 1)], C.PANEL)
    p.setPen(kalem(C.LINE_STRONG, 2))
    p.drawLine(QPointF(cx - 14, band.y() + 1), QPointF(cx, band.y() - 13))
    p.drawLine(QPointF(cx, band.y() - 13), QPointF(cx + 14, band.y() + 1))
    logo(p, band.x() + 22, band.y() + 22, 84)
    t.yaz(band.x() + 124, band.y() + 22, "Robz Vision", "etiket", 36, C.TEXT, kayit="band")
    t.yaz(band.x() + 124, band.y() + 68, "bilgisayarda çalışır", "govde", 25, C.TEXT_2, kayit="band")
    adimlar = ("Altyazıyı okur", "Türkçeye çevirir", "Ekrana yazar")
    x0, x1 = band.x() + 440, band.right() - 30
    genlik = [t.olc(a, "etiket", 32)[0] + 60 for a in adimlar]
    bosluk = (x1 - x0 - sum(genlik)) / (len(adimlar) - 1)
    x = x0
    for i, a in enumerate(adimlar):
        numara(p, x + 22, band.center().y(), 22, str(i + 1))
        t.yaz(x + 56, band.center().y() - 20, a, "etiket", 32, C.TEXT, kayit="adım")
        x += genlik[i]
        if i < len(adimlar) - 1:
            ok(p, [(x + 14, band.center().y()), (x + bosluk - 14, band.center().y())], C.CYAN, 4, 15)
            x += bosluk
    lejant(t, ["hdmi"], adlar={"hdmi": "HDMI ya da DisplayPort (görüntü kablosu)"})


def _ps5_ag_ve_kol(t: Tuval) -> QRectF:
    """Yol 2 ve 3'ün ortak sol yarısı: PS5 —Ethernet— modem —Ethernet/Wi-Fi— bilgisayar, kol bilgisayarda.
    Bilgisayarın alanını döner."""
    p = t.p
    ps = ps5(t, 56, 340, 300)
    t.yaz(ps.center().x() - 6, 648, "PS5", "etiket", 30, C.TEXT, hiza="orta")
    md = modem(t, 320, 170, 180)
    t.yaz(md.left() - 14, 178, "Modem", "etiket", 30, C.TEXT, hiza="sag")
    pc = kasa(t, 605, 330, 340)
    t.yaz(pc.center().x() + 30, 290, "Bilgisayar", "etiket", 30, C.TEXT)
    kablo(t, [(219, 410), (270, 410), (270, 262), (md.left(), 262)], "eth")
    t.yaz(256, 272, "Ethernet\n(önerilir)", "govde", 25, C.TEXT_2, hiza="sag", kayit="kablo adı")
    kablo(t, [(md.right(), 262), (pc.center().x(), 262), (pc.center().x(), pc.top())], "eth")
    t.yaz(md.right() + 8, 218, "Ethernet ya da Wi-Fi", "govde", 26, C.TEXT_2, kayit="kablo adı")
    # aynı ağ notu
    bilgi_isareti(p, 318, 340, C.TURQUOISE, "bilgi", 28)
    t.yaz(342, 322, "PS5 ile bilgisayar\naynı ağda", "govde_k", 26, C.TURQUOISE, kayit="ağ notu")
    kol = dualsense(t, 300, 512, 220)
    kablo(t, [(kol.center().x(), kol.top() + 4), (kol.center().x(), 470), (pc.left(), 470)], "usb")
    t.yaz(kol.center().x(), 674, "DualSense: USB-C kablo ya da Bluetooth", "govde", 26, C.TEXT_2, hiza="orta",
          kayit="kol notu")
    return pc


def ciz_yol2(t: Tuval) -> None:
    baslik(t, "PS5 + bilgisayar", "PS5'i bilgisayar ekranında oynarsın; Türkçe altyazı aynı ekranda çıkar.", "Yol 2")
    pc = _ps5_ag_ve_kol(t)
    cr, er = ekran_cihazi(t, 940, 292, 580, "monitor", lambda pp, r: oyun_sahnesi(pp, r))
    t.yaz(cr.left(), 250, "Monitör", "etiket", 30, C.TEXT)
    kablo(t, [(pc.right(), 470), (cr.left(), 470)], "hdmi")
    t.yaz((pc.right() + cr.left()) / 2, 424, "HDMI", "govde", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    bilgisayar_bandi(t, 712, pc, "Bilgisayarda iki uygulama açık:",
                     [("akis", "chiaki-ng", "PS5 görüntüsü, 1080p 60 FPS"),
                      ("logo", "Robz Vision", "Türkçe altyazı")], yuk=128)
    lejant(t, ["eth", "usb", "hdmi"])


def ciz_yol3(t: Tuval) -> None:
    baslik(t, "PS5 + bilgisayar + TV", "Bilgisayar görüntüyü televizyona verir; Türkçe altyazı TV'de görünür.",
           "Yol 3")
    pc = _ps5_ag_ve_kol(t)
    cr, er = ekran_cihazi(t, 905, 294, 622, "tv", lambda pp, r: oyun_sahnesi(pp, r))
    not_kutusu(t, QRectF(cr.left(), 172, cr.width(), 96), "LG, Samsung, Sony, Philips, Vestel…",
               "bilgi", 25, kalin_bas="TV markası fark etmez: HDMI girişi yeter")
    kablo(t, [(pc.right(), 404), (cr.left(), 404)], "kablosuz")
    t.yaz((pc.right() + cr.left()) / 2, 358, "Win+K", "govde_k", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    kablo(t, [(pc.right(), 520), (cr.left(), 520)], "hdmi")
    t.yaz((pc.right() + cr.left()) / 2, 474, "HDMI", "govde", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    bilgisayar_bandi(t, 712, pc, "Robz Vision TV'de değil, bilgisayarda çalışır; TV bilgisayarın ekranı olur.",
                     [("akis", "chiaki-ng", "PS5 görüntüsü, 1080p 60 FPS"),
                      ("logo", "Robz Vision", "Türkçe altyazı")], baslik_renk=C.CYAN, yuk=128)
    lejant(t, ["eth", "usb", "hdmi", "kablosuz"],
           adlar={"kablosuz": "Kablosuz: Windows'ta Win+K ile yansıt (gecikme artar)"})


def ciz_yol4(t: Tuval) -> None:
    p = t.p
    baslik(t, "Capture kartı", "PS5 görüntüsü kart üzerinden bilgisayara gelir: en net görüntü, yayına hazır.",
           "Yol 4")
    not_kutusu(t, QRectF(M, 168, 604, 96), "Ayarlar → Sistem → HDMI → HDCP'yi Etkinleştir", "uyari", 24,
               kalin_bas="PS5'te HDCP'yi kapat:")
    ps = ps5(t, 56, 330, 290)
    t.yaz(ps.center().x() - 6, 630, "PS5", "etiket", 30, C.TEXT, hiza="orta")
    ck = capture_karti(t, 345, 428, 170)
    t.yaz(ck.center().x(), 384, "Capture kartı", "etiket", 30, C.TEXT, hiza="orta")
    pc = kasa(t, 660, 300, 340)
    t.yaz(pc.center().x(), 256, "Bilgisayar", "etiket", 30, C.TEXT, hiza="orta")
    cr, er = ekran_cihazi(t, 965, 300, 562, "tv", lambda pp, r: oyun_sahnesi(pp, r))
    t.yaz(cr.left(), 256, "TV ya da monitör", "etiket", 30, C.TEXT)
    kablo(t, [(212, 471), (ck.left(), 471)], "hdmi")
    t.yaz((212 + ck.left()) / 2, 424, "HDMI", "govde", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    kablo(t, [(ck.right(), 471), (pc.left(), 471)], "usb", ("usbc", "usba"))
    t.yaz((ck.right() + pc.left()) / 2, 424, "USB", "govde", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    kablo(t, [(pc.right(), 471), (cr.left(), 471)], "hdmi")
    t.yaz((pc.right() + cr.left()) / 2, 424, "HDMI", "govde", 27, C.TEXT_2, hiza="orta", kayit="kablo adı")
    # passthrough uyarısı: kartın kendi HDMI çıkışı → TV (çeviri görünmez)
    yx = 664
    kablo(t, [(ck.center().x(), ck.bottom()), (ck.center().x(), yx), (cr.center().x(), yx),
              (cr.center().x(), cr.bottom())], "uyari", ("hdmi", None))
    bilgi_isareti(p, 780, yx, C.ORANGE, "carpi", 34)
    t.yaz(812, yx + 6, "Kartın kendi HDMI çıkışında (passthrough) çeviri görünmez", "govde_k", 24, C.ORANGE,
          kayit="passthrough")
    bilgisayar_bandi(t, 712, pc, "Bilgisayarda:",
                     [("kayit", "OBS", "Capture kartı kaynağı → Pencereli Projektör"),
                      ("logo", "Robz Vision", "Bu pencereyi okur ve çevirir")], yuk=128)
    lejant(t, ["hdmi", "usb", "uyari"], adlar={"usb": "USB", "uyari": "Passthrough: çeviri görünmez"})


def _goz(p: QPainter, cx: float, cy: float, renk=C.TEXT_2) -> None:
    yol = QPainterPath(QPointF(cx - 12, cy))
    yol.quadTo(QPointF(cx, cy - 11), QPointF(cx + 12, cy))
    yol.quadTo(QPointF(cx, cy + 11), QPointF(cx - 12, cy))
    sekil(p, yol, None, renk, 2.2)
    daire(p, QPointF(cx, cy), 3.5, renk)


def ciz_yayin(t: Tuval) -> None:
    p = t.p
    baslik(t, "OBS'e ekle", "Yayın yapıyorsan izleyiciler de Türkçe altyazıyı görür.", "Yayın")
    ust, alt = 180, 700
    oy = (ust + alt) / 2
    # 1) Robz Vision
    a = QRectF(M, ust, 304, alt - ust)
    kutu(p, a, 16, C.PANEL, C.LINE_STRONG, 2)
    logo(p, a.center().x() - 56, a.y() + 34, 112)
    t.yaz(a.x(), a.y() + 160, "Robz Vision", "etiket", 36, C.TEXT, gen=a.width(), hiza="orta")
    t.yaz(a.x(), a.y() + 222, "Yayın adresi:", "govde", 24, C.TEXT_2, gen=a.width(), hiza="orta")
    u = QRectF(a.x() + 14, a.y() + 262, a.width() - 28, 56)
    kutu(p, u, 10, C.RAISED, C.CYAN, 2.5)
    t.yaz(u.x(), u.y() + 11, "http://127.0.0.1:8765/", "govde_k", 24, C.CYAN, gen=u.width(), hiza="orta",
          kayit=None)
    t.yaz(a.x() + 20, a.y() + 340, "Ana sayfadan kopyala, OBS'e yapıştır.", "govde", 24, C.TEXT_2,
          gen=a.width() - 40, hiza="orta", en_cok=2)
    ok(p, [(a.right() + 8, oy), (a.right() + 52, oy)], C.CYAN, 5, 18)
    # 2) OBS
    b = QRectF(a.right() + 60, ust, 536, alt - ust)
    kutu(p, b, 16, C.PANEL, C.LINE_STRONG, 2)
    simge(p, "kayit", b.x() + 20, b.y() + 18, 52)
    t.yaz(b.x() + 86, b.y() + 22, "OBS", "etiket", 36, C.TEXT)
    t.yaz(b.x() + 160, b.y() + 30, "Tarayıcı kaynağı, oyunun üstünde", "govde", 24, C.TEXT_2)
    on = QRectF(b.x() + 20, b.y() + 88, b.width() - 40, (b.width() - 40) * 9 / 16)
    oyun_sahnesi(p, on)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(kalem(C.CYAN, 3, [2.5, 1.8]))
    p.drawRect(on.adjusted(1.5, 1.5, -1.5, -1.5))
    etk = rozet(t, on.x() + 10, on.y() + 10, "Tarayıcı 1920×1080", 21, yuk=36, kayit=None)
    del etk
    ky = on.bottom() + 16
    for i, (ad, ack, vurgu) in enumerate((("Robz Vision", "Tarayıcı", True), ("Oyun görüntüsü", "", False))):
        sr = QRectF(b.x() + 20, ky + i * 48, b.width() - 40, 42)
        kutu(p, sr, 8, qc(C.CYAN, 0.14) if vurgu else C.RAISED, C.CYAN if vurgu else C.LINE_STRONG, 2)
        _goz(p, sr.x() + 26, sr.center().y(), C.TEXT if vurgu else C.TEXT_2)
        r = t.yaz(sr.x() + 50, sr.y() + 6, ad, "govde_k", 23, C.TEXT if vurgu else C.TEXT_2, kayit=None)
        if ack:
            t.yaz(r.right() + 10, sr.y() + 6, f"({ack})", "govde", 23, C.TEXT_2, kayit=None)
        t.yaz(sr.right() - 14, sr.y() + 6, "üstte" if vurgu else "altta", "govde", 22, C.TEXT_2, hiza="sag",
              kayit=None)
    ok(p, [(b.right() + 8, oy), (b.right() + 52, oy)], C.CYAN, 5, 18)
    # 3) Yayın
    c = QRectF(b.right() + 60, ust, 224, alt - ust)
    kutu(p, c, 16, C.PANEL, C.LINE_STRONG, 2)
    t.yaz(c.x(), c.y() + 30, "Yayın", "etiket", 36, C.TEXT, gen=c.width(), hiza="orta")
    for i, ad in enumerate(("Kick", "Twitch", "YouTube")):
        pr = QRectF(c.x() + 24, c.y() + 112 + i * 92, c.width() - 48, 68)
        kutu(p, pr, 12, C.RAISED, C.LINE_STRONG, 2)
        t.yaz(pr.x(), pr.y() + 15, ad, "govde_k", 28, C.TEXT, gen=pr.width(), hiza="orta", kayit=None)
    t.yaz(c.x() + 16, c.y() + 400, "ya da başka bir yayın sitesi", "govde", 23, C.TEXT_2, gen=c.width() - 32,
          hiza="orta", en_cok=2)
    ok(p, [(c.right() + 8, oy), (c.right() + 52, oy)], C.CYAN, 5, 18)
    # 4) İzleyiciler
    d = QRectF(c.right() + 60, ust, W - M - c.right() - 60, alt - ust)
    kutu(p, d, 16, C.PANEL, C.LINE_STRONG, 2)
    t.yaz(d.x(), d.y() + 30, "İzleyiciler", "etiket", 36, C.TEXT, gen=d.width(), hiza="orta")
    tel = QRectF(d.x() + 18, d.y() + 112, d.width() - 36, (d.width() - 36) * 0.56)
    kutu(p, tel.adjusted(-8, -8, 8, 8), 14, GOVDE_KOYU, GOVDE_KENAR, 3)
    oyun_sahnesi(p, tel)
    for i in range(3):            # izleyici simgeleri
        cx = d.center().x() + (i - 1) * 50
        cy = tel.bottom() + 66
        daire(p, QPointF(cx, cy - 12), 11, None, C.TEXT_2, 3)
        yol = QPainterPath(QPointF(cx - 20, cy + 22))
        yol.cubicTo(QPointF(cx - 18, cy + 2), QPointF(cx + 18, cy + 2), QPointF(cx + 20, cy + 22))
        sekil(p, yol, None, C.TEXT_2, 3)
    t.yaz(d.x() + 16, d.y() + 372, "Türkçe altyazıyı görür", "govde_k", 27, C.TEXT, gen=d.width() - 32,
          hiza="orta", en_cok=2)
    # alt not
    not_kutusu(t, QRectF(M, 724, W - 2 * M, 96),
               "Ekrandaki kutu varsayılan olarak yayına girmez; izleyiciler altyazıyı tarayıcı kaynağından, "
               "bir kez görür.",
               "bilgi", 25, kalin_bas="OBS'te: Kaynaklar → + düğmesi → Tarayıcı → adresi yapıştır, boyut 1920×1080")
    x = M
    r = t.yaz(x, 846, "Her yolla çalışır:", "govde_k", 26, C.TEXT_2, kayit="yollar")
    yol_rozetleri(t, r.right() + 16, 843, {1, 2, 3, 4}, 36)


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
