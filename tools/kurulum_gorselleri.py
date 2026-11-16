"""Kurulum sihirbazı (Inno Setup) görsellerini Robz Vision logosundan üretir.

Çıktı (installer/ klasörü, 24 bit BMP — Inno Setup'ın her sürümünde desteklenir):
  wizard-164.bmp / wizard-246.bmp / wizard-328.bmp   sol panel (karşılama ve bitiş sayfaları; %100/%150/%200)
  small-55.bmp  / small-83.bmp   / small-110.bmp      iç sayfaların sağ üst köşesi

Kullanım:  python tools/kurulum_gorselleri.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter,  # noqa: E402
                           QRadialGradient)

from altyazitr.gui.tokens import C, F  # noqa: E402

LOGO = ROOT / "altyazitr" / "data" / "logo.png"
OUT = ROOT / "installer"


def _to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data())))


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


def main() -> None:
    _app = QGuiApplication(sys.argv[:1])
    OUT.mkdir(exist_ok=True)
    for w in (164, 246, 328):
        h = round(w * 314 / 164)
        wizard_panel(w, h).save(OUT / f"wizard-{w}.bmp")
    for s in (55, 83, 110):
        small_image(s).save(OUT / f"small-{s}.bmp")
    print("Kurulum görselleri yazıldı:", OUT)


if __name__ == "__main__":
    main()
