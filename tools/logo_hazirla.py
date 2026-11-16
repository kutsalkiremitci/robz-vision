"""Robz Vision logosunu uygulama varlıklarına dönüştürür.

Girdi: assets/robz_vision_kaynak.jpg (koyu zeminli kare logo)
Çıktı: altyazitr/data/logo.png (512×512, köşeleri şeffaf; uygulama içi kullanım)
       assets/robzvision.ico (16–256 px; Windows/PyInstaller simgesi)

Kullanım:  python tools/logo_hazirla.py [kaynak.jpg]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]


def frame_box(a: np.ndarray, thr: float = 150) -> tuple[int, int, int, int]:
    """Parlak dış çerçevenin sınırlarını orta satır/sütundan bulur."""
    lum = a.mean(axis=2)
    h, w = lum.shape
    row, col = lum[h // 2], lum[:, w // 2]
    left = int(np.argmax(row > thr))
    right = int(w - 1 - np.argmax(row[::-1] > thr))
    top = int(np.argmax(col > thr))
    # alt kenar daha koyu (mor): daha düşük eşik
    bottom = int(h - 1 - np.argmax(col[::-1] > 70))
    return left, top, right, bottom


def corner_radius(crop: np.ndarray, thr: float = 150) -> float:
    """Kırpılmış görüntünün üst kenarına yakın satırda çerçevenin başladığı x'ten köşe yarıçapını kestirir."""
    lum = crop.mean(axis=2)
    estimates = []
    for y in (3, 6, 10):
        x = int(np.argmax(lum[y] > thr))
        # daire denklemi: x = r - sqrt(r^2 - (r - y)^2)  →  r = (x^2 + y^2) / (2(x + y) - ...) çözümü
        # sayısal: r için tara
        best = min(range(20, 260), key=lambda r: abs((r - np.sqrt(max(0.0, r * r - (r - y) ** 2))) - x))
        estimates.append(best)
    return float(np.median(estimates))


def build(src: Path) -> None:
    im = Image.open(src).convert("RGB")
    a = np.asarray(im)
    l, t, r, b = frame_box(a)
    side = max(r - l, b - t) + 1
    cx, cy = (l + r) / 2, (t + b) / 2
    box = (int(round(cx - side / 2)), int(round(cy - side / 2)), int(round(cx + side / 2)), int(round(cy + side / 2)))
    crop = im.crop(box)
    rad = corner_radius(np.asarray(crop))
    print(f"çerçeve {l,t,r,b}  kırpma {box}  kenar {side}px  köşe yarıçapı ≈{rad:.0f}px")

    # 4x süper örnekleme ile yumuşak kenarlı yuvarlak kare maske (çerçevenin 1 px içinden)
    S = 4
    big = side * S
    mask = Image.new("L", (big, big), 0)
    inset = 1 * S
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, big - inset - 1, big - inset - 1),
                                           radius=int(rad * S), fill=255)
    mask = mask.resize((side, side), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(0.4))
    rgba = crop.convert("RGBA")
    rgba.putalpha(mask)

    out_png = ROOT / "altyazitr" / "data" / "logo.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    rgba.resize((512, 512), Image.Resampling.LANCZOS).save(out_png, optimize=True)
    out_ico = ROOT / "assets" / "robzvision.ico"
    out_ico.parent.mkdir(parents=True, exist_ok=True)
    rgba.resize((256, 256), Image.Resampling.LANCZOS).save(
        out_ico, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("yazıldı:", out_png, out_ico)


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "robz_vision_kaynak.jpg")
