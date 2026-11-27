"""README ve kullanım kılavuzu görsellerini üretir (docs/gorseller/).

Kullanım:  python tools/readme_gorselleri.py [banner] [demo] [arayuz] [gta6] [--kaynak KLASÖR ...]
           (bölüm adı verilmezse hepsi üretilir; gta6 bölümü yalnız --kaynak verilince çalışır)

Çıktı:
  banner.png          1280×640 tanıtım görseli; GitHub "Social preview" olarak da yüklenebilir
  ayirici.png         README bölümleri arasındaki ince ufuk çizgisi (saydam zemin)
  indir.png           README'deki "Hemen indir" düğmesi (2x çözünürlük, saydam köşeler)
  kick_takip.png      README'deki "Robz so good'u Kick'te takip et" düğmesi (2x, Kick yeşili; logo değiştirilmeden)
  demo.gif           sahte oyun sahnesi: İngilizce altyazı → Türkçe çeviri kutusu (döngü)
  demo_once.png       aynı sahnenin çevirisiz hâli
  demo_sonra.png      aynı sahnenin çevirili hâli
  basla.png           Başla sayfası: kurulum adımları (iki adım tamam)  (tools/arayuz_goruntusu.py çıktısından)
  sihirbaz.png        kurulum sihirbazı: Canlı'da vurgulanan önizleme ve "Adım 2 / 4" balonu
  ana_pencere.png     Canlı sayfası
  gorunum.png         Görünüm sayfası
  ceviri.png          Çeviri sayfası
  bolge_secimi.png    bölge seçici (sahte oyun sahnesinin üstünde)
  konum_ayari.png     "Konumu ayarla" modundaki ekran altyazısı kutusu
  obs_yardim.png      "OBS'e nasıl eklenir?" penceresi
  guncelleme.png      güncelleme penceresi (bu sürüm yeni yayınlanmış gibi; ağa gidilmez, notlar CHANGELOG.md'den)

  gta6 bölümü (resmî GTA VI tanıtım görselleri üzerinde; görseller --kaynak ile verilen klasörde aranır):
  gta6_kahraman.png   1280×640 README başı ve GitHub "Social preview": sahne + İngilizce altyazı + Türkçe kutu + ad
  gta6_once.png       İngilizce altyazılı sahne
  gta6_sonra.png      aynı sahne, üstünde Robz Vision'ın Türkçe kutusu (ekrandaki kutu, varsayılan görünüm)
  gta6_ustune.png     "Oyun altyazısının üzerine" kipi: üstte oyunun altyazısı, altta onu örten Türkçe kutu
  gta6_demo.gif       beş satırlık döngü (GTA6_DEMO): satır belirir → Türkçesi gelir → sonraki satır
  gta6_bolge_secimi.png  bölge seçici, sahnenin üstünde (ayrı süreçte; yalnız seçici ve ekran kutusu kurulur)
  gta6_konum_ayari.png   "Konumu ayarla" modundaki ekran altyazısı kutusu, sahnenin üstünde
  site/img/gta6_*.webp ve site/img/og.jpg (1200×630 sosyal önizleme) aynı çizimlerden

  Örnek:  python tools/readme_gorselleri.py gta6 --kaynak "C:\\...\\Gorseller\\gta6"

  Kaynak JPEG'ler depoya kopyalanmaz; yalnız bu çizimler girer. Altyazı cümleleri projeye özgüdür, oyunun
  senaryosundan alıntı değildir. Türkçe kutu uygulamanın kendi çizim koduyla (render.paint_subtitle,
  render.paint_cover; kapak kutusunun yeri ve yazı boyu display.cover_area / cover_fonts ve
  ScreenOverlay._place_cover ile) çizilir.

Arayüz yeniden tasarlandığında bu betiği yeniden çalıştırmanız yeterlidir.

GİZLİLİK: Görseller hiçbir zaman gerçek ekranı yakalamaz. Arayüz görüntüleri ayrı bir süreçte, masaüstünde
pencere açmadan alınır; o süreçte ekran/pencere yakalayan her işlev (mss, grab_screen_rect, WindowCapturer,
snapshot_window, pencere listesi, monitör listesi) sahte oyun sahnesi döndüren ya da hiçbir şey döndürmeyen
sürümlerle değiştirilir. Ana sayfanın canlı önizlemesi (SourceGrabber) hiç çalışmaz; önizlemede sahte oyun sahnesi
görünür (bkz. tools/arayuz_goruntusu.py). Böylece masaüstündeki sohbetler, sekmeler veya pencere adları görsellere
giremez.
"""

from __future__ import annotations

import contextlib
import io
import math
import os
import random
import runpy
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
OUT = ROOT / "docs" / "gorseller"
for _p in (str(ROOT), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Demo sahneleri: (tools/test_penceresi.SUBS içindeki sıra, konuşmacı, Türkçe çeviri)
SAHNELER = [
    (0, "LUCIA", "Sana buraya geri gelmemeni söylemiştim, Jason."),
    (1, "JASON", "Bizi izliyorlar. Hemen gitmemiz lazım!"),
    (2, None, "Kanka, iyi misin? O polis on sokaktır peşimizde."),
    (3, "LUCIA", "Arabaya bin. Vice City'ye gidiyoruz."),
]
DEMO_W, DEMO_H = 960, 540

# tools/arayuz_goruntusu.py çıktısı → docs/gorseller adı (+ isteğe bağlı kırpma, oransal x0, y0, x1, y1)
ARAYUZ = [
    ("ana_pencere_start.png", "basla.png", None),
    ("sihirbaz_3.png", "sihirbaz.png", None),
    ("ana_pencere.png", "ana_pencere.png", None),
    ("ana_pencere_style.png", "gorunum.png", None),
    ("ana_pencere_translate.png", "ceviri.png", None),
    ("secici.png", "bolge_secimi.png", (0.08, 0.58, 0.92, 1.0)),
    ("overlay_ayar.png", "konum_ayari.png", None),
    ("obs_yardim.png", "obs_yardim.png", None),
    ("guncelleme.png", "guncelleme.png", None),
]

# arayuz_goruntusu.py'nin "oyun zemini" olarak düz renkle doldurduğu yüzeyler; ayrı süreçte bu renkler
# yerine sahte oyun sahnesi çizilir (bölge seçici ve ekran altyazısı görüntüleri gerçekçi görünsün diye).
SAHNE_ZEMINLERI = {"#3b6d8f", "#4a7fa3"}


# --- ortak yardımcılar ----------------------------------------------------------------------------------
def _qt_app():
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def _to_pil(img):
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def _goreli(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _save_png(im, path: Path) -> None:
    im.save(path, optimize=True)
    print(f"  {_goreli(path)}  {im.width}×{im.height}  {path.stat().st_size // 1024} KB")


@contextlib.contextmanager
def _altyazisiz():
    """test_penceresi.paint_scene'in İngilizce altyazıyı çizmemesini sağlar (yalnız arka plan)."""
    import test_penceresi as tp

    orig = tp._draw_sub
    tp._draw_sub = lambda *a, **k: None
    try:
        yield
    finally:
        tp._draw_sub = orig


def _sahne_zamani(idx: int) -> float:
    """SUBS[idx] altyazısının ekranda tam göründüğü an."""
    import test_penceresi as tp

    return idx * (tp.SHOW + tp.GAP) + 1.6


def _paint_scene(p, t: float, W: int, H: int, altyazi: bool = True) -> None:
    import test_penceresi as tp

    if altyazi:
        tp.paint_scene(p, t, W, H)
    else:
        with _altyazisiz():
            tp.paint_scene(p, t, W, H)


# --- demo -----------------------------------------------------------------------------------------------
def _ingilizce_ust(W: int, H: int, idx: int) -> float:
    """Oyunun İngilizce altyazısının üst kenarı (test_penceresi._draw_sub ile aynı ölçüler)."""
    from PySide6.QtGui import QFont, QFontMetricsF

    import test_penceresi as tp
    from altyazitr.render import wrap_text

    speaker, text = tp.SUBS[idx]
    full = f"{speaker}: {text}" if speaker else text
    font = QFont("Arial")
    font.setPixelSize(int(H * 0.045))
    font.setBold(True)
    fm = QFontMetricsF(font)
    return H * 0.86 - fm.lineSpacing() * len(wrap_text(fm, full, W * 0.7))


def demo_kare(W: int, H: int, idx: int, ingilizce: bool, ceviri: tuple[str | None, str] | None):
    """Tek demo karesi: sahte oyun sahnesi + (isteğe bağlı) Robz Vision'ın Türkçe çeviri kutusu.

    Çeviri, uygulamadaki ekran altyazısı kutusuyla aynı kodla (render.paint_subtitle, in_box=True) ve
    varsayılan ekran görünümüyle (Settings().screen_style) çizilir; kutu oyunun altyazısının hemen
    üstüne konur."""
    from PySide6.QtGui import QImage, QPainter

    from altyazitr.config import Settings
    from altyazitr.render import paint_subtitle

    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    _paint_scene(p, _sahne_zamani(idx), W, H, altyazi=ingilizce)
    if ceviri:
        speaker, text = ceviri
        bottom = _ingilizce_ust(W, H, idx) - H * 0.025
        bw, bh = W * 0.8, H * 0.3
        p.save()
        p.translate((W - bw) / 2, bottom - bh)
        paint_subtitle(p, bw, bh, Settings().screen_style, text, speaker, in_box=True, ref_height=H)
        p.restore()
    p.end()
    return img


def demo() -> None:
    from PIL import Image

    _qt_app()
    print("Demo:")
    frames: list[Image.Image] = []
    durations: list[int] = []
    for idx, speaker, tr in SAHNELER:
        for ingilizce, ceviri, ms in ((False, None, 450), (True, None, 550), (True, (speaker, tr), 2600)):
            frames.append(_to_pil(demo_kare(DEMO_W, DEMO_H, idx, ingilizce, ceviri)))
            durations.append(ms)
    # Her sahne (boş → İngilizce → çevirili, 3 kare) kendi paletini çevirili kareden alır: sahne içindeki
    # kareler yalnızca altyazı bölgesinde farklıdır ve GIF bu farkı küçük bir dikdörtgen olarak kodlar.
    # Palet kaynağına konuşmacı adı ve yazı renginin geçiş şeritleri eklenir; yoksa birkaç piksellik sarı
    # konuşmacı adı paletten pay alamaz ve kumlu görünür.
    from altyazitr.config import Settings

    st = Settings().screen_style  # demo karelerindeki kutu ekran görünümüyle çizilir
    ramps = Image.new("RGB", (DEMO_W, 32))
    for row, color in enumerate((st.speaker_color, st.text_color)):
        c = tuple(int(color.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
        for x in range(DEMO_W):
            a = x / (DEMO_W - 1)
            ramps.paste(tuple(int(v * a + 12 * (1 - a)) for v in c), (x, row * 16, x + 1, row * 16 + 16))
    q = []
    for i in range(0, len(frames), 3):
        src = Image.new("RGB", (DEMO_W, DEMO_H + 32))
        src.paste(frames[i + 2], (0, 0))
        src.paste(ramps, (0, DEMO_H))
        palette = src.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        q += [f.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for f in frames[i:i + 3]]
    gif = OUT / "demo.gif"
    q[0].save(gif, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(f"  {gif.relative_to(ROOT)}  {DEMO_W}×{DEMO_H}  {len(frames)} kare  {gif.stat().st_size // 1024} KB")

    idx, speaker, tr = SAHNELER[0]
    _save_png(_to_pil(demo_kare(1280, 720, idx, True, None)), OUT / "demo_once.png")
    _save_png(_to_pil(demo_kare(1280, 720, idx, True, (speaker, tr))), OUT / "demo_sonra.png")


# --- banner, bölüm ayırıcı ve indirme düğmesi -----------------------------------------------------------
# Vice City / GTA VI esintili bir gün batımı; yalnızca esin. Oyunların logosu, yazı tipi ya da görselleri
# kullanılmaz: gökyüzü, çizgili güneş, deniz ve palmiyeler burada QPainter ile çizilir, marka işareti
# projenin kendi logosudur (altyazitr/data/logo.png). Token'lara ek olarak kullanılan tonlar logodan alındı.
BANNER_W, BANNER_H = 1280, 640
UFUK = 446                                   # ufuk çizgisinin y'si
GOK = ((0.0, "#0C0B1D"), (0.40, "#181040"), (0.70, "#2F1458"), (0.88, "#521A69"), (1.0, "#7C2170"))
GUNES = ((0.0, "#FFE27A"), (0.50, "#FF9A5C"), (1.0, "#FF4D94"))   # logodaki V: sarı → turuncu → pembe
DENIZ = ((0.0, "#1B1142"), (0.35, "#120D30"), (1.0, "#0A0918"))
PALMIYE_ON = "#07051A"                       # öndeki palmiyeler
PALMIYE_ARKA = "#1A0F38"                     # uzaktaki palmiye (pusla açılır)
SLOGAN = "İngilizce oyun altyazılarını Türkçeye çevirir."


def _pil_to_qimage(im):
    from PySide6.QtGui import QImage

    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = QImage()
    out.loadFromData(buf.getvalue(), "PNG")
    return out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _isilti(w: int, h: int, ciz, renk: str, yaricap: float, opaklik: float):
    """ciz(painter, renk) ile çizilen şeklin bulanık, tek renkli ışıltı katmanı (neon parlaması).

    Bulanıklık yalnız maskeye uygulanır; böylece saydam kenarlarda koyu saçak oluşmaz."""
    from PIL import Image, ImageFilter
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    maske = QImage(w, h, QImage.Format.Format_RGB32)
    maske.fill(Qt.GlobalColor.black)
    p = QPainter(maske)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    ciz(p, QColor("white"))
    p.end()
    a = _to_pil(maske).convert("L").filter(ImageFilter.GaussianBlur(yaricap))
    a = a.point(lambda v: min(255, round(v * opaklik)))
    c = QColor(renk)
    katman = Image.new("RGBA", (w, h), (c.red(), c.green(), c.blue(), 0))
    katman.putalpha(a)
    return _pil_to_qimage(katman)


def _gecis_rengi(duraklar, t: float):
    from PySide6.QtGui import QColor

    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(duraklar, duraklar[1:]):
        if t <= t1:
            a, b = QColor(c0), QColor(c1)
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                                   a.blueF() + (b.blueF() - a.blueF()) * k)
    return QColor(duraklar[-1][1])


def _dogrusal(x0, y0, x1, y1, duraklar):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QLinearGradient

    g = QLinearGradient(QPointF(x0, y0), QPointF(x1, y1))
    for t, c in duraklar:
        g.setColorAt(t, QColor(c) if isinstance(c, str) else c)
    return g


def _renk(hex_renk: str, alfa: float):
    from PySide6.QtGui import QColor

    c = QColor(hex_renk)
    c.setAlphaF(max(0.0, min(1.0, alfa)))
    return c


def _gunes_yolu(cx: float, cy: float, r: float, ufuk: float):
    """Retro güneş: ufukta kesilen daire; alt yarısında aşağı doğru kalınlaşan yatay boşluklar."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPainterPath

    yol = QPainterPath()
    yol.addEllipse(QPointF(cx, cy), r, r)
    ust = QPainterPath()
    ust.addRect(QRectF(cx - r - 2, cy - r - 2, 2 * r + 4, ufuk - (cy - r) + 2))
    yol = yol.intersected(ust)
    y0, n = cy - r * 0.40, 7
    adim = (ufuk - y0) / n
    for i in range(n):
        kalinlik = 2.0 + i * 1.6
        bosluk = QPainterPath()
        bosluk.addRect(QRectF(cx - r - 2, y0 + adim * (i + 0.55) - kalinlik / 2, 2 * r + 4, kalinlik))
        yol = yol.subtracted(bosluk)
    return yol


def _bezier(p0, c, p1, t: float):
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1])


def _bezier_teget(p0, c, p1, t: float):
    dx = 2 * (1 - t) * (c[0] - p0[0]) + 2 * t * (p1[0] - c[0])
    dy = 2 * (1 - t) * (c[1] - p0[1]) + 2 * t * (p1[1] - c[1])
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _cokgen(noktalar):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPolygonF

    yol = QPainterPath()
    yol.addPolygon(QPolygonF([QPointF(x, y) for x, y in noktalar]))
    yol.closeSubpath()
    return yol


def _yaprak(tepe, aci: float, boy: float, sarkma: float):
    """Tek palmiye yaprağı: kavisli ana damar boyunca, uca doğru incelen tırtıklı yaprakçıklar."""
    ca, sa = math.cos(math.radians(aci)), math.sin(math.radians(aci))
    p0 = tepe
    c = (tepe[0] + ca * boy * 0.55, tepe[1] + sa * boy * 0.55 - boy * 0.16)
    p1 = (tepe[0] + ca * boy, tepe[1] + sa * boy + boy * sarkma)
    genislik = boy * 0.19
    n = 15
    sol, sag = [], []
    for i in range(1, n + 1):
        t = i / (n + 1)
        x, y = _bezier(p0, c, p1, t)
        tx, ty = _bezier_teget(p0, c, p1, t)
        nx, ny = -ty, tx
        w = genislik * math.sin(math.pi * t ** 0.8) * (1.05 - 0.4 * t)
        for yon, liste in ((1, sol), (-1, sag)):
            asagi = (ny * yon) > 0  # yere bakan taraf: yaprakçıklar daha uzun sarkar
            k = 1.3 if asagi else 0.72
            uc = (x + nx * yon * w * k - tx * w * 0.55, y + ny * yon * w * k - ty * w * 0.55 + w * 0.45)
            xm, ym = _bezier(p0, c, p1, t + 0.5 / (n + 1))
            girinti = (xm + nx * yon * w * 0.14, ym + ny * yon * w * 0.14)
            liste += [uc, girinti]
    return _cokgen([p0] + sol + [p1] + sag[::-1])


# (açı°, boy oranı): 0° sağ, -90° yukarı, +90° aşağı (ekran koordinatı)
YAPRAKLAR = ((-176, 1.0), (-150, 1.06), (-124, 0.92), (-98, 0.78), (-74, 0.86), (-46, 1.0), (-16, 1.06),
             (14, 0.96), (166, 0.96), (52, 0.72), (128, 0.72))


def _palmiye(p, taban: tuple[float, float], boy: float, egim: float, tac: float, renk: str, yapraklar=YAPRAKLAR):
    """Palmiye silueti: kavisli, yukarı doğru incelen boğumlu gövde, yelpaze tacı ve hindistancevizleri."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QPainterPath

    tepe = (taban[0] + egim * boy, taban[1] - boy)
    kontrol = (taban[0] + egim * boy * 0.12, taban[1] - boy * 0.62)
    n = 90
    sol, sag = [], []
    for i in range(n + 1):
        t = i / n
        x, y = _bezier(taban, kontrol, tepe, t)
        tx, ty = _bezier_teget(taban, kontrol, tepe, t)
        w = boy * (0.040 * (1 - t) + 0.020 * t)
        if (t * boy / 15.0) % 1.0 < 0.22:  # gövde boğumları
            w *= 1.12
        sol.append((x - ty * w / 2, y + tx * w / 2))
        sag.append((x + ty * w / 2, y - tx * w / 2))
    yol = _cokgen(sol + sag[::-1])
    for aci, oran in yapraklar:
        yatay = math.cos(math.radians(aci)) ** 2
        yol = yol.united(_yaprak(tepe, aci, tac * oran, 0.12 + 0.30 * yatay))
    for dx, dy in ((-0.05, 0.05), (0.03, 0.06), (-0.01, 0.10)):
        hc = QPainterPath()
        hc.addEllipse(QPointF(tepe[0] + dx * tac, tepe[1] + dy * tac), tac * 0.042, tac * 0.042)
        yol = yol.united(hc)
    p.fillPath(yol, QColor(renk))


def _baslik_yolu(font, x: float, taban: float, metin: str):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath

    yol = QPainterPath()
    yol.addText(QPointF(x, taban), font, metin)
    return yol


def banner_ciz(W: int = BANNER_W, H: int = BANNER_H):
    """1280×640 tanıtım görseli (QImage). Ayrıca GitHub "Social preview" olarak yüklenebilir."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QRadialGradient

    from altyazitr.gui.tokens import C, F

    _qt_app()
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    for hint in (QPainter.RenderHint.Antialiasing, QPainter.RenderHint.SmoothPixmapTransform,
                 QPainter.RenderHint.TextAntialiasing):
        p.setRenderHint(hint)

    # yazı ölçüleri önce: güneş, yazı bloğunun sağında kalacak biçimde yerleşir
    baslik = QFont()
    baslik.setFamilies([F.DISPLAY_SEMIBOLD, F.DISPLAY, F.BODY_FALLBACK])
    baslik.setPixelSize(92)
    bfm = QFontMetricsF(baslik)
    slogan = QFont()
    slogan.setFamilies([F.BODY, F.BODY_FALLBACK])
    slogan.setPixelSize(26)
    sfm = QFontMetricsF(slogan)

    logo_kenar = 208
    lx, merkez_y = 86, 292
    ly = merkez_y - logo_kenar / 2
    tx = lx + logo_kenar + 52
    bosluk = 26
    blok_h = bfm.capHeight() + bosluk + sfm.ascent() + sfm.descent()
    baslik_taban = merkez_y - blok_h / 2 + bfm.capHeight()
    slogan_taban = baslik_taban + bosluk + sfm.ascent()
    yazi_sag = tx + max(bfm.horizontalAdvance("Robz Vision"), sfm.horizontalAdvance(SLOGAN))

    R = 168
    sx, sy = max(1050.0, yazi_sag + R + 60), UFUK - 40

    # gökyüzü
    p.fillRect(QRectF(0, 0, W, UFUK), _dogrusal(0, 0, 0, UFUK, GOK))
    # güneşin çevresindeki sıcak hava: ufka basık, geniş bir pembe-turuncu hale
    p.save()
    p.translate(sx, UFUK)
    p.scale(1.0, 0.5)
    hale = QRadialGradient(QPointF(0, 0), 720)
    for t, c, a in ((0.0, "#FF7A6B", 0.70), (0.30, "#FF4D94", 0.38), (0.62, "#9A2A8C", 0.14), (1.0, "#4A1A63", 0.0)):
        hale.setColorAt(t, _renk(c, a))
    p.fillRect(QRectF(-W * 2, -UFUK * 2 - 10, W * 4, UFUK * 2 + 10), hale)
    p.restore()
    # ufkun hemen üstünde ince ılık pus
    p.fillRect(QRectF(0, UFUK - 70, W, 70),
               _dogrusal(0, UFUK - 70, 0, UFUK, ((0.0, _renk("#FF6F8E", 0.0)), (1.0, _renk("#FF6F8E", 0.22)))))

    # yıldızlar: yalnızca gökyüzünün koyu üst kısmında, seyrek ve soluk
    rng = random.Random(1987)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(70):
        x, y = rng.uniform(0, W), rng.uniform(0, UFUK * 0.48)
        if lx - 30 < x < lx + logo_kenar + 30 and y > ly - 30:
            continue
        alfa = rng.uniform(0.18, 0.62) * (1 - y / (UFUK * 0.55))
        r = rng.choice((0.7, 0.9, 1.1, 1.4))
        p.setBrush(_renk(C.TEXT, alfa))
        p.drawEllipse(QPointF(x, y), r, r)

    # güneş: arkasında yumuşak parlama, sonra çizgili disk
    gunes = _gunes_yolu(sx, sy, R, UFUK)
    p.drawImage(0, 0, _isilti(W, H, lambda q, c: q.fillPath(gunes, c), "#FF6A88", 38, 0.55))
    p.fillPath(gunes, _dogrusal(0, sy - R, 0, UFUK, GUNES))

    # deniz
    p.fillRect(QRectF(0, UFUK, W, H - UFUK), _dogrusal(0, UFUK, 0, H, DENIZ))
    p.save()
    p.translate(sx, UFUK)
    p.scale(1.0, 0.28)
    yansima = QRadialGradient(QPointF(0, 0), 560)
    for t, c, a in ((0.0, "#FF5C8A", 0.42), (0.45, "#C0307F", 0.14), (1.0, "#4A1A63", 0.0)):
        yansima.setColorAt(t, _renk(c, a))
    p.fillRect(QRectF(-W * 2, 0, W * 4, (H - UFUK) * 4), yansima)
    p.restore()
    # su yüzeyi: perspektifte sıklaşan, kesik kesik soluk çizgiler
    rng = random.Random(42)
    for j in range(1, 17):
        d = (j / 17) ** 1.6
        y = UFUK + 4 + d * (H - UFUK - 6)
        x = rng.uniform(-80, 40)
        while x < W:
            uzun = rng.uniform(40, 220) * (0.6 + d)
            p.fillRect(QRectF(x, y, uzun, 1.0 + d * 1.2), _renk("#8E7BD6", 0.05 + 0.07 * (1 - d)))
            x += uzun + rng.uniform(30, 160)
    # güneşin denizdeki yansıması: uzaklaştıkça kısalan, aralığı açılan çizgiler
    rng = random.Random(7)
    y = UFUK + 5.0
    while y < H:
        d = (y - UFUK) / (H - UFUK)
        yari = R * (0.92 - 0.62 * d) * rng.uniform(0.78, 1.08)
        kalin = 1.6 + 3.6 * d
        renk = _gecis_rengi(GUNES, 1.0 - min(1.0, d * 1.25))
        renk.setAlphaF(max(0.0, 0.85 * (1 - d) ** 1.4))
        x0, x1 = sx - yari, sx + yari
        kesik = rng.uniform(0.25, 0.75)
        orta = x0 + (x1 - x0) * kesik
        acik = rng.uniform(4, 14) * (0.4 + d)
        p.fillRect(QRectF(x0, y, orta - acik / 2 - x0, kalin), renk)
        p.fillRect(QRectF(orta + acik / 2, y, x1 - orta - acik / 2, kalin), renk)
        y += kalin + 3.5 + 13 * d

    # neon ufuk çizgisi (camgöbeği), hafif parlamayla
    ufuk_yolu = _cokgen(((0, UFUK - 1), (W, UFUK - 1), (W, UFUK + 1), (0, UFUK + 1)))
    p.drawImage(0, 0, _isilti(W, H, lambda q, c: q.fillPath(ufuk_yolu, c), C.CYAN, 5, 0.9))
    p.fillRect(QRectF(0, UFUK - 0.75, W, 1.5),
               _dogrusal(0, 0, W, 0, ((0.0, _renk(C.CYAN, 0.55)), (0.5, _renk(C.CYAN, 0.95)),
                                      (1.0, _renk(C.CYAN, 0.55)))))

    # palmiyeler: kenarlarda; sağdaki uzak palmiye güneşin önünde
    _palmiye(p, (1140, H + 60), 360, 0.10, 118, PALMIYE_ARKA)
    _palmiye(p, (1322, H + 70), 660, -0.17, 176, PALMIYE_ON)
    _palmiye(p, (-34, H + 70), 640, 0.11, 168, PALMIYE_ON,
             yapraklar=((-150, 1.0), (-118, 0.9), (-92, 0.8), (-62, 0.92), (-34, 1.06), (-6, 1.08),
                        (22, 0.96), (56, 0.74), (150, 0.8)))

    # logo: kendi renkleriyle, üstüne çerçeve/gölge eklenmeden
    logo = QImage(str(ROOT / "altyazitr" / "data" / "logo.png"))
    p.drawImage(QRectF(lx, ly, logo_kenar, logo_kenar), logo)

    # ad: Bahnschrift SemiBold, beyazdan açık pembeye; arkasında pembe neon parlaması
    ad = _baslik_yolu(baslik, tx - 3, baslik_taban, "Robz Vision")
    p.drawImage(0, 0, _isilti(W, H, lambda q, c: q.fillPath(ad, c), C.PINK, 14, 0.85))
    p.drawImage(0, 0, _isilti(W, H, lambda q, c: q.fillPath(ad, c), "#FF7AB0", 3, 0.55))
    p.fillPath(ad, _dogrusal(0, baslik_taban - bfm.capHeight(), 0, baslik_taban,
                             ((0.0, "#FFFFFF"), (1.0, "#FFDCEB"))))
    p.setFont(slogan)
    p.setPen(_renk(C.TEXT, 0.86))
    p.drawText(QPointF(tx, slogan_taban), SLOGAN)
    p.end()
    return img.convertToFormat(QImage.Format.Format_RGB32)


def ayirici_ciz(W: int = 1280, H: int = 24):
    """Bölüm ayırıcı: uçlarda sönen camgöbeği, ortada pembe-turuncu ince ufuk çizgisi (saydam zemin)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    from altyazitr.gui.tokens import C

    _qt_app()
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    y = H / 2
    duraklar = ((0.0, _renk(C.CYAN, 0.0)), (0.22, _renk(C.CYAN, 0.85)), (0.42, _renk(C.PINK, 1.0)),
                (0.5, _renk(C.ORANGE, 1.0)), (0.58, _renk(C.PINK, 1.0)), (0.78, _renk(C.CYAN, 0.85)),
                (1.0, _renk(C.CYAN, 0.0)))
    # parlama yalnızca çizginin göründüğü yerde: ortada pembe, yanlarda daha soluk camgöbeği
    for renk, profil in ((C.PINK, ((0.24, 0.0), (0.5, 1.0), (0.76, 0.0))),
                         (C.CYAN, ((0.04, 0.0), (0.24, 0.8), (0.42, 0.0), (0.58, 0.0), (0.76, 0.8), (0.96, 0.0)))):
        maske = _dogrusal(0, 0, W, 0, tuple((t, _renk("#FFFFFF", a)) for t, a in profil))
        cizim = lambda q, _c, m=maske: q.fillRect(QRectF(0, y - 1, W, 2), m)  # noqa: E731
        p.drawImage(0, 0, _isilti(W, H, cizim, renk, 3.5, 0.75))
    p.fillRect(QRectF(0, y - 1, W, 2), _dogrusal(0, 0, W, 0, duraklar))
    p.end()
    return img


def indir_dugmesi_ciz(olcek: int = 2):
    """README'deki "Hemen indir" düğmesi: uygulamanın birincil düğmesi gibi pembe dolgu, koyu yazı, indirme ikonu.

    `olcek` kat çözünürlükte çizilir; README'de mantıksal genişlikle (img width) gösterilir."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

    from altyazitr.gui.tokens import C, F

    _qt_app()
    font = QFont()
    font.setFamilies([F.DISPLAY_SEMIBOLD, F.DISPLAY, F.BODY_FALLBACK])
    font.setPixelSize(20 * olcek)
    fm = QFontMetricsF(font)
    metin = "Hemen indir"
    ikon, ara, yan = 20 * olcek, 10 * olcek, 24 * olcek
    W = int(yan + ikon + ara + fm.horizontalAdvance(metin) + yan)
    H = 52 * olcek
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    zemin = QPainterPath()
    zemin.addRoundedRect(QRectF(0, 0, W, H), 6 * olcek, 6 * olcek)  # DESIGN.md: düğmeler 6 px
    p.fillPath(zemin, QColor(C.PINK))
    # indirme ikonu: uygulamanın ikon setiyle aynı dil (24 birim ızgara, 1.75 çizgi, yuvarlak uç)
    k = ikon / 24
    ox, oy = yan, (H - ikon) / 2
    kalem = QPen(QColor(C.INVERSE), 1.9 * k * 1.2)
    kalem.setCapStyle(Qt.PenCapStyle.RoundCap)
    kalem.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(kalem)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pt = lambda x, y: QPointF(ox + x * k, oy + y * k)  # noqa: E731
    p.drawLine(pt(12, 3.5), pt(12, 15))
    p.drawPolyline([pt(7, 10), pt(12, 15), pt(17, 10)])
    p.drawPolyline([pt(4, 15.5), pt(4, 20), pt(20, 20), pt(20, 15.5)])
    p.setFont(font)
    p.setPen(QColor(C.INVERSE))
    taban = (H + fm.capHeight()) / 2
    p.drawText(QPointF(yan + ikon + ara, taban), metin)
    p.end()
    return img


def kick_dugmesi_ciz(olcek: int = 2):
    """README'deki "Robz so good'u Kick'te takip et" düğmesi: Kick yeşili zemin, koyu yazı; solda gece yarısı rengi bir
    kare içinde Kick logosu (data/kick.svg, rengi ve oranı değiştirilmeden). Boyu "Hemen indir" düğmesiyle aynı.

    `olcek` kat çözünürlükte çizilir; README'de mantıksal genişlikle (img width) gösterilir."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath

    from altyazitr.gui.developer import KICK_GREEN, kick_pixmap
    from altyazitr.gui.tokens import C, F

    _qt_app()
    font = QFont()
    font.setFamilies([F.DISPLAY_SEMIBOLD, F.DISPLAY, F.BODY_FALLBACK])
    font.setPixelSize(20 * olcek)
    fm = QFontMetricsF(font)
    metin = "Robz so good'u Kick'te takip et"
    kare, ara, yan, H = 34 * olcek, 12 * olcek, 9 * olcek, 52 * olcek
    W = int(yan + kare + ara + fm.horizontalAdvance(metin) + 22 * olcek)
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    zemin = QPainterPath()
    zemin.addRoundedRect(QRectF(0, 0, W, H), 6 * olcek, 6 * olcek)  # DESIGN.md: düğmeler 6 px
    p.fillPath(zemin, QColor(KICK_GREEN))
    rozet = QPainterPath()
    rozet.addRoundedRect(QRectF(yan, (H - kare) / 2, kare, kare), 5 * olcek, 5 * olcek)
    p.fillPath(rozet, QColor(C.BG))
    logo = 20 * olcek
    pm = kick_pixmap(logo, 1.0)
    if not pm.isNull():
        p.drawPixmap(QPointF(yan + (kare - logo) / 2, (H - logo) / 2), pm)
    p.setFont(font)
    p.setPen(QColor(C.BG))
    p.drawText(QPointF(yan + kare + ara, (H + fm.capHeight()) / 2), metin)
    p.end()
    return img


def banner() -> None:
    from PIL import Image

    _qt_app()
    print("Banner:")
    _save_png(_to_pil(banner_ciz()), OUT / "banner.png")
    for ad, qimg in (("ayirici.png", ayirici_ciz()), ("indir.png", indir_dugmesi_ciz()),
                     ("kick_takip.png", kick_dugmesi_ciz())):
        buf = io.BytesIO(bytes(_qimage_png(qimg)))
        _save_png(Image.open(buf).convert("RGBA"), OUT / ad)


def _qimage_png(img) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# --- arayüz ekran görüntüleri ---------------------------------------------------------------------------
def arayuz() -> None:
    from PIL import Image

    print("Arayüz (tools/arayuz_goruntusu.py, ayrı süreçte, ekran yakalama kapalı):")
    with tempfile.TemporaryDirectory(prefix="robz_arayuz_") as tmp:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_arayuz", tmp], cwd=str(ROOT),
                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300)
        log = (r.stdout + r.stderr).strip()
        if log:
            print("  " + log.replace("\n", "\n  "))
        if r.returncode != 0:
            print(f"  UYARI: arayuz_goruntusu.py {r.returncode} koduyla bitti; var olan görüntüler kopyalanıyor.")
        for src_name, dst_name, crop in ARAYUZ:
            src = Path(tmp) / src_name
            if not src.exists():
                print(f"  EKSİK: {src_name} üretilmedi (araç değişmiş olabilir); {dst_name} güncellenmedi.")
                continue
            im = Image.open(src).convert("RGB")
            if crop:
                x0, y0, x1, y1 = crop
                im = im.crop((int(im.width * x0), int(im.height * y0), int(im.width * x1), int(im.height * y1)))
            _save_png(im, OUT / dst_name)


def _arayuz_alt_sureci(out_dir: str) -> None:
    """Ayrı süreç: gerçek ekran yakalamayı kapatıp tools/arayuz_goruntusu.py'yi çalıştırır."""
    import numpy as np

    sahne_cache: dict[tuple[int, int], np.ndarray] = {}

    def sahte_ekran(w: int, h: int) -> np.ndarray:
        """1920×1080 sahte oyun ekranı (BGRA); gerçek ekran yerine bu döner."""
        key = (w, h)
        if not _has_app():  # henüz Qt yok (çizim yapılamaz): siyah kare, yine de gerçek ekran değil
            return np.zeros((h, w, 4), np.uint8)
        if key not in sahne_cache:
            from PySide6.QtGui import QImage, QPainter

            img = QImage(w, h, QImage.Format.Format_ARGB32)
            p = QPainter(img)
            _paint_scene(p, _sahne_zamani(0), w, h)
            p.end()
            a = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4]
            sahne_cache[key] = a.reshape(h, w, 4).copy()  # ARGB32 bellekte B,G,R,A sırasındadır
        return sahne_cache[key]

    def kesit(rect) -> np.ndarray:
        x, y, w, h = (int(v) for v in rect)
        full = sahte_ekran(1920, 1080)
        out = np.zeros((max(1, h), max(1, w), 4), np.uint8)
        xs, ys = max(0, x), max(0, y)
        xe, ye = min(1920, x + w), min(1080, y + h)
        if xe > xs and ye > ys:
            out[ys - y:ye - y, xs - x:xe - x] = full[ys:ye, xs:xe]
        return out

    class SahteMss:
        monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}] * 2

        def grab(self, mon):
            if isinstance(mon, dict):
                rect = (mon["left"], mon["top"], mon["width"], mon["height"])
            else:
                rect = tuple(mon)
                rect = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
            return kesit(rect)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # 1) mss paketinin kendisi
    try:
        import mss

        mss.mss = lambda *a, **k: SahteMss()  # type: ignore[assignment]
        if hasattr(mss, "MSS"):
            mss.MSS = lambda *a, **k: SahteMss()  # type: ignore[assignment]
    except ImportError:
        pass

    # 2) uygulamanın yakalama ve pencere listeleme işlevleri
    import altyazitr.capture as cap
    import altyazitr.winutil as wu

    class KapaliPencereYakalama:
        def __init__(self, *a, **k):
            raise cap.CaptureError("Görsel üretiminde pencere yakalama kapalı")

    cap._new_mss = lambda: SahteMss()
    cap.grab_screen_rect = lambda rect: np.ascontiguousarray(kesit(rect)[:, :, :3])
    cap.snapshot_window = lambda *a, **k: None
    cap.WindowCapturer = KapaliPencereYakalama  # type: ignore[misc]
    wu.list_windows = lambda: []
    wu.find_window = lambda *a, **k: None

    # 3) uygulama modüllerini önceden yükle (yukarıdaki sahte işlevleri alırlar ve aşağıdaki
    #    QPixmap değişikliğinden etkilenmezler)
    import altyazitr.app  # noqa: F401
    import altyazitr.gui.home_page as hp
    import altyazitr.gui.main_window as mw

    for mod in (hp, mw):
        for name in ("grab_screen_rect", "snapshot_window", "WindowCapturer"):
            if hasattr(mod, name):
                setattr(mod, name, getattr(cap, name))

    # 4) arayuz_goruntusu.py'nin düz "oyun zemini" yerine sahte oyun sahnesi
    import PySide6.QtGui as G

    RealPixmap = G.QPixmap

    class SahnePixmap(RealPixmap):
        def fill(self, color=None):  # noqa: D401
            c = G.QColor(color) if color is not None else None
            if c is not None and c.name().lower() in SAHNE_ZEMINLERI:
                w, h = self.width(), self.height()
                p = G.QPainter(self)
                if w >= 1000 and h >= 600:  # tam ekran (bölge seçici): altyazılı sahne
                    import test_penceresi as tp

                    _paint_scene(p, _sahne_zamani(0), w, h, altyazi=False)
                    sp, tx = tp.SUBS[0]
                    # altyazıyı, arayuz_goruntusu.py'deki örnek sürüklemenin (x 300–1500) ortasına hizala
                    tp._draw_sub(p, f"{sp}: {tx}", min(w, 1800), h)
                else:  # ekran altyazısı kutusu: ekranın alt-orta kesiti, oyun altyazısı olmadan
                    p.translate(-(1920 - w) / 2, -(1080 * 0.9 - h))
                    _paint_scene(p, _sahne_zamani(0), 1920, 1080, altyazi=False)
                p.end()
                return None
            return super().fill(color) if color is not None else super().fill()

    G.QPixmap = SahnePixmap

    sys.argv = [str(TOOLS / "arayuz_goruntusu.py"), out_dir, "--sihirbaz"]
    runpy.run_path(str(TOOLS / "arayuz_goruntusu.py"), run_name="__main__")


def _has_app() -> bool:
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance() is not None


# --- GTA 6 sahneleri ------------------------------------------------------------------------------------------------
# Resmî GTA VI tanıtım görsellerinin üzerinde Robz Vision. Kaynak görseller depoda değildir: --kaynak ile verilen
# klasörlerde (alt klasörler dahil) adıyla aranır; bulunamazsa bölüm atlanır. Depoya yalnız buradaki çizimler girer
# (oyun görseli © Rockstar Games / Take-Two Interactive; atıf README'de, sitede ve THIRD_PARTY_NOTICES.md'de).
# Rockstar'ın logoları kullanılmaz. İngilizce cümleler projeye özgüdür, oyunun senaryosundan alıntı değildir.
SITE_IMG = ROOT / "site" / "img"


@dataclass(frozen=True)
class Gta6Sahne:
    dosya: str                  # kaynak klasördeki dosya adı
    odak: tuple[float, float]   # kırpılırken korunacak nokta (görsele oransal x, y)
    ingilizce: str              # oyunun altyazısı (özgün cümle)
    ing_vurgu: str              # oyunun pembe yazdığı kelime
    turkce: str                 # Robz Vision'ın çevirisi
    tr_vurgu: str               # vurgulu kelimenin çevirideki karşılığı


GTA6_SAHNELER = {
    "araba": Gta6Sahne("Jason_Duval_02.jpg", (0.5, 0.3),
                       "You've got two minutes. If the cops show up, I'm gone.", "cops",
                       "İki dakikan var. Polis gelirse basar giderim.", "Polis"),
    "araba2": Gta6Sahne("Jason_Duval_02.jpg", (0.5, 0.3),
                        "Relax. I've done this a hundred times.", "",
                        "Sakin ol. Bunu yüz kere yaptım.", ""),
    "ikili": Gta6Sahne("Jason_and_Lucia_01.jpg", (0.5, 0.0),
                       "Stay close. If this goes wrong, we meet at the marina.", "marina",
                       "Yanımdan ayrılma. İş ters giderse marinada buluşuruz.", "marinada"),
    "ikili2": Gta6Sahne("Jason_and_Lucia_01.jpg", (0.5, 0.0),
                        "Then don't be late this time, Jason.", "",
                        "O zaman bu sefer geç kalma, Jason.", ""),
    "gece": Gta6Sahne("Jason_and_Lucia_14.jpg", (0.5, 0.5),
                      "Forget the job for one night. Tonight, Vice City is ours.", "Vice City",
                      "Bir geceliğine işi unut. Bu gece Vice City bizim.", "Vice City"),
}
# Döngünün sırası: (sahne, son kare). Her satır: altyazısız → İngilizce → çevirisiyle; son satır "üzerine" kipinde.
GTA6_DEMO = (("araba", "kutu"), ("araba2", "kutu"), ("ikili", "kutu"), ("ikili2", "kutu"), ("gece", "kapak"))
GTA6_YAZI = 0.036      # oyunun altyazısının yazı boyu (kare yüksekliğine oranla; 1080p'de ~39 px)
GTA6_TABAN = 0.915     # son satırın taban çizgisi (oransal y)
GTA6_GENISLIK = 0.64   # altyazının en geniş satırı (kare genişliğine oranla)
SLOGAN_GTA6 = "GTA 6'yı Türkçe oyna."
ACIKLAMA_GTA6 = "Oyundaki İngilizce altyazıyı okur, anında Türkçeye çevirir."
KICK_ADRES = "kick.com/robzsogood"


def _gta6_bul(klasorler: list[Path], ad: str) -> Path | None:
    for k in klasorler:
        if (k / ad).is_file():
            return k / ad
        for f in sorted(k.rglob(ad)):
            return f
    return None


def _gta6_zemin(yol: Path, W: int, H: int, odak: tuple[float, float]):
    """Kaynak görsel: W×H'yi tam kaplayacak biçimde ölçeklenir, odak noktası çevresinden kırpılır (PIL, RGB)."""
    from PIL import Image

    im = Image.open(yol).convert("RGB")
    s = max(W / im.width, H / im.height)
    nw, nh = max(W, round(im.width * s)), max(H, round(im.height * s))
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x = round(min(max(odak[0] * nw - W / 2, 0), nw - W))
    y = round(min(max(odak[1] * nh - H / 2, 0), nh - H))
    return im.crop((x, y, x + W, y + H))


def _vurgu_araligi(metin: str, ifade: str) -> list[list[int]]:
    i = metin.find(ifade) if ifade else -1
    return [[i, i + len(ifade)]] if i >= 0 else []


def _gta_altyazi(p, W: int, H: int, metin: str, vurgu: str, taban: float = GTA6_TABAN) -> tuple[dict, float]:
    """Oyunun İngilizce altyazısı, GTA tarzı: beyaz, ince koyu kenar ve yumuşak gölge, ortalı; `vurgu` kelimesi pembe
    (oyunun görev yazısındaki gibi; uygulamanın varsayılan vurgu rengi). Dönüş: OCR'ın bu altyazı için vereceği kutu
    (textproc.subtitle_box biçiminde, kareye oransal; kapak kutusu bundan hesaplanır) ve yazının üst kenarı (ekrandaki
    Türkçe kutu bunun üstüne konur). Satır kutusunun yüksekliği L: display.COVER_FONT ölçümüne göre Segoe UI Bold'un
    harfleri L'nin 0,65'i boyundadır, OCR kutusu harflerin her yanından ~0,15 L taşar."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

    from altyazitr.config import StyleCfg
    from altyazitr.display import COVER_FONT
    from altyazitr.render import wrap_text

    px = max(10, round(H * GTA6_YAZI))
    font = QFont("Segoe UI")
    font.setPixelSize(px)
    font.setWeight(QFont.Weight.Bold)
    fm = QFontMetricsF(font)
    satirlar = wrap_text(fm, metin, W * GTA6_GENISLIK)
    ara = fm.lineSpacing()
    tabanlar = [H * taban - (len(satirlar) - 1 - i) * ara for i in range(len(satirlar))]
    # Satır parçalara bölünür (vurgudan önce, vurgu, sonra); kenar, gölge ve dolgu aynı parçalardan çizilir, böylece
    # pembe kelime küçük boylarda da kendi kenarının içinde kalır.
    beyaz, renkli = QPainterPath(), QPainterPath()
    kenarlar: list[tuple[float, float]] = []
    for satir, taban in zip(satirlar, tabanlar):
        g = fm.horizontalAdvance(satir)
        x0 = (W - g) / 2
        kenarlar.append((x0, x0 + g))
        i = satir.find(vurgu) if vurgu else -1
        parcalar = ((0, len(satir), beyaz),) if i < 0 else (
            (0, i, beyaz), (i, i + len(vurgu), renkli), (i + len(vurgu), len(satir), beyaz))
        for s, e, hedef in parcalar:
            if e > s:
                hedef.addText(QPointF(x0 + fm.horizontalAdvance(satir[:s]), taban), font, satir[s:e])
    yazi = beyaz.united(renkli)

    kenar = max(1.5, px * 0.1)

    def golge(q, c):
        q.translate(0, px * 0.07)
        q.fillPath(yazi, c)
        q.strokePath(yazi, QPen(c, kenar * 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))

    p.drawImage(0, 0, _isilti(W, H, golge, "#000000", px * 0.16, 0.8))
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.strokePath(yazi, QPen(QColor(0, 0, 0, 225), kenar, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
    p.fillPath(beyaz, QColor("#FFFFFF"))
    p.fillPath(renkli, QColor(StyleCfg().highlight_color))

    L = px / COVER_FONT
    pay = 0.15 * L
    merkezler = [t - fm.capHeight() / 2 + px * 0.06 for t in tabanlar]
    x0, x1 = min(a for a, _ in kenarlar) - pay, max(b for _, b in kenarlar) + pay
    y0, y1 = merkezler[0] - L / 2, merkezler[-1] + L / 2
    kutu = {"rect": [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H], "rows": len(satirlar), "line_h": L / H,
            "roi": [W, H]}
    return kutu, tabanlar[0] - fm.ascent()


def _ekran_kutusu(p, W: int, H: int, metin: str, spans, alt: float) -> None:
    """Robz Vision'ın ekrandaki Türkçe kutusu (varsayılan "Ekranımda" görünümü), alt kenarı `alt`ta. Uygulamadaki
    kutuyla aynı kod: render.paint_subtitle, in_box=True, yazı boyu ekran yüksekliğine göre (demo_kare gibi)."""
    from altyazitr.config import Settings
    from altyazitr.render import paint_subtitle

    bw, bh = W * 0.8, H * 0.3
    p.save()
    p.translate((W - bw) / 2, alt - bh)
    paint_subtitle(p, bw, bh, Settings().screen_style, metin, None, in_box=True, ref_height=H, spans=spans)
    p.restore()


def _kapak(p, W: int, H: int, kutu: dict, metin: str, spans) -> None:
    """"Oyun altyazısının üzerine" kipi, uygulamadaki hesapla: kutunun yeri ve yazı boyları SubtitleDisplay._cover ve
    _screen_cover'daki gibi (display.cover_area, cover_fonts; bölge bütün kare, ölçek 1), Türkçenin kutuya sığdırılması
    ScreenOverlay._place_cover, çizim render.paint_cover (varsayılan "Ekranımda" görünümü)."""
    from types import SimpleNamespace

    from altyazitr.config import Settings
    from altyazitr.display import COVER_FONT, COVER_MIN_PX, cover_area, cover_fonts
    from altyazitr.overlay import ScreenOverlay
    from altyazitr.render import paint_cover

    st = Settings().screen_style
    k = H / 1080.0
    rect, rows, lh = cover_area(kutu)
    ust = float(st.font_size) * k
    font, low = cover_fonts(COVER_FONT * lh * H if lh else ust, ust, COVER_MIN_PX * k)
    line = {"text": metin, "speaker": None, "spans": spans}
    cover = {"rect": [rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H], "bounds": [0, 0, W, H], "rows": rows,
             "font": font, "min": low, "line": line}
    # _place_cover yalnız görünümü (self.look) kullanır; pencere açmadan aynı yerleştirme kuralı
    yer = ScreenOverlay._place_cover(SimpleNamespace(look=st), cover)
    if yer is None:
        raise RuntimeError("kapak kutusu yerleştirilemedi")
    r, boy = yer
    p.save()
    p.translate(r.x(), r.y())
    paint_cover(p, r.width(), r.height(), st, line, boy)
    p.restore()


def _gta6_katman(p, W: int, H: int, sahne: Gta6Sahne, kip: str) -> None:
    """kip: "bos" (altyazısız), "ingilizce", "kutu" (İngilizce + Türkçe kutu), "kapak" (İngilizce örtülü)."""
    if kip == "bos":
        return
    kutu, ust = _gta_altyazi(p, W, H, sahne.ingilizce, sahne.ing_vurgu)
    spans = _vurgu_araligi(sahne.turkce, sahne.tr_vurgu)
    if kip == "kutu":
        _ekran_kutusu(p, W, H, sahne.turkce, spans, ust - H * 0.022)
    elif kip == "kapak":
        _kapak(p, W, H, kutu, sahne.turkce, spans)


def gta6_kare(kaynak: Path, sahne: Gta6Sahne, W: int, H: int, kip: str):
    from PySide6.QtGui import QImage, QPainter

    _qt_app()
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.drawImage(0, 0, _pil_to_qimage(_gta6_zemin(kaynak, W, H, sahne.odak)))
    _gta6_katman(p, W, H, sahne, kip)
    p.end()
    return img


def gta6_arayuz(zemin: Path, kutu_zemini: Path) -> None:
    """Bölge seçici ve "Konumu ayarla" kutusu, GTA VI sahnesinin üstünde (ayrı süreçte, _gta6_arayuz_alt_sureci)."""
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="robz_gta6_arayuz_") as tmp:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_gta6_arayuz", tmp, str(zemin),
                            str(kutu_zemini)],
                           cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        log = (r.stdout + r.stderr).strip()
        if log:
            print("  " + log.replace("\n", "\n  "))
        for src_name, dst_name, crop in (("secici.png", "gta6_bolge_secimi.png", (0.08, 0.58, 0.92, 1.0)),
                                         ("overlay_ayar.png", "gta6_konum_ayari.png", None)):
            src = Path(tmp) / src_name
            if not src.exists():
                print(f"  EKSİK: {src_name} üretilmedi; {dst_name} güncellenmedi.")
                continue
            im = Image.open(src).convert("RGB")
            if crop:
                x0, y0, x1, y1 = crop
                im = im.crop((int(im.width * x0), int(im.height * y0), int(im.width * x1), int(im.height * y1)))
            _save_png(im, OUT / dst_name)


def _gta6_arayuz_alt_sureci(out_dir: str, zemin_yolu: str, kutu_zemini: str) -> None:
    """Ayrı süreç: uygulamanın bölge seçicisi ve "Konumu ayarla" kipindeki ekran kutusu, GTA VI sahnesinin üstünde.
    Yalnız bu iki pencere kurulur, WA_DontShowOnScreen ile masaüstünde görünmez; ana pencere, OBS sunucusu ve çeviri
    sayfası kurulmaz, Ollama'ya ya da ağa istek gitmez. Ekran yakalayan işlevler yine de kapatılır: görüntüye
    masaüstünden hiçbir şey giremez (pencerelerin kendi çizimi grab() ile alınır, zemine sahne çizilir)."""
    import time

    import numpy as np
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import altyazitr.capture as cap

    class Kapali:
        def __init__(self, *a, **k):
            raise cap.CaptureError("Görsel üretiminde ekran yakalama kapalı")

    cap.grab_screen_rect = lambda rect: np.zeros((max(1, int(rect[3])), max(1, int(rect[2])), 3), np.uint8)
    cap.snapshot_window = lambda *a, **k: None
    cap.WindowCapturer = Kapali  # type: ignore[misc]
    cap._new_mss = Kapali

    from altyazitr.config import Settings
    from altyazitr.gui import region_picker as rp
    from altyazitr.gui.theme import apply_theme
    from altyazitr.overlay import ScreenOverlay

    app = QApplication(sys.argv[:1])
    apply_theme(app)

    def pump(sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    out, zemin = Path(out_dir), Path(zemin_yolu)
    sahne = GTA6_SAHNELER["ikili"]
    gizli = Qt.WidgetAttribute.WA_DontShowOnScreen

    # bölge seçici: altyazının çevresine çizilmiş seçim (tools/arayuz_goruntusu.py'deki sürüklemeyle aynı oranlar)
    picker = rp.ScreenRegionPicker([])
    for w in picker._wins:
        w.setAttribute(gizli, True)
    picker.start()
    pump(0.2)
    w0 = picker._wins[0]
    W, H = w0.width(), w0.height()
    a, b = QPoint(round(W * 0.156), round(H * 0.741)), QPoint(round(W * 0.781), round(H * 0.889))
    sol = Qt.MouseButton.LeftButton
    QTest.mousePress(w0, sol, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(w0, b)
    QTest.mouseRelease(w0, sol, Qt.KeyboardModifier.NoModifier, b)
    pump(0.1)
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.drawImage(0, 0, _pil_to_qimage(_gta6_zemin(zemin, W, H, sahne.odak)))
    _gta_altyazi(p, W, H, sahne.ingilizce, sahne.ing_vurgu, taban=0.86)
    p.drawPixmap(QRect(0, 0, W, H), w0.grab())
    p.end()
    img.save(str(out / "secici.png"))
    picker.finish(None)

    # "Konumu ayarla": 1080p ekrandaki varsayılan kutu (ScreenOverlay.default_box), arkasında gece sahnesinin o kesiti
    ov = ScreenOverlay(Settings())
    ov.setAttribute(gizli, True)
    ov.setGeometry(QRect(0, 0, 1267, 302))
    ov.set_editing(True)
    pump(0.3)
    w, h = ov.width(), ov.height()
    SW, SH = 1920, 1080
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.translate(-(SW - w) / 2, -(SH - h - SH * 0.05))
    p.drawImage(0, 0, _pil_to_qimage(_gta6_zemin(Path(kutu_zemini), SW, SH, GTA6_SAHNELER["gece"].odak)))
    p.resetTransform()
    p.drawPixmap(QRect(0, 0, w, h), ov.grab())
    p.end()
    img.save(str(out / "overlay_ayar.png"))
    ov.set_editing(False)
    ov.hide()
    print("Arayüz (GTA VI zemin): bölge seçici ve konum ayarı alındı.")


def _sag_ust_karartma(im):
    """Sağ üst köşeyi gece yarısı rengine doğru karartır (ad ve slogan okunsun; yüzler ve altyazı alanı açık kalır)."""
    import numpy as np
    from PIL import Image

    W, H = im.size
    x = np.linspace(0.0, 1.0, W)[None, :]
    y = np.linspace(0.0, 1.0, H)[:, None]

    def yumusak(a, b, t):
        t = np.clip((t - a) / (b - a), 0.0, 1.0)
        return t * t * (3 - 2 * t)

    alfa = 0.8 * yumusak(0.47, 0.72, x) * (1.0 - yumusak(0.48, 0.80, y))
    zemin = np.asarray(im, dtype=np.float32)
    gece = np.array([12, 11, 29], dtype=np.float32)
    out = zemin * (1 - alfa[..., None]) + gece * alfa[..., None]
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def gta6_kahraman_ciz(kaynak: Path, W: int = BANNER_W, H: int = BANNER_H):
    """README'nin başı ve GitHub "Social preview": GTA VI sahnesi, altta oyunun İngilizce altyazısı ve üstünde Robz
    Vision'ın Türkçe kutusu; sağ üstte logo, ad, slogan ve Kick adresi (Kick logosu, değiştirilmeden)."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

    from altyazitr.gui.developer import kick_pixmap
    from altyazitr.gui.tokens import C, F
    from altyazitr.render import wrap_text

    _qt_app()
    sahne = GTA6_SAHNELER["ikili"]
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    for hint in (QPainter.RenderHint.Antialiasing, QPainter.RenderHint.SmoothPixmapTransform,
                 QPainter.RenderHint.TextAntialiasing):
        p.setRenderHint(hint)
    p.drawImage(0, 0, _pil_to_qimage(_sag_ust_karartma(_gta6_zemin(kaynak, W, H, sahne.odak))))
    _gta6_katman(p, W, H, sahne, "kutu")

    k = H / 640.0
    kenar_payi = 50 * k
    logo_kenar = 80 * k
    ad_font = QFont()
    ad_font.setFamilies([F.DISPLAY_SEMIBOLD, F.DISPLAY, F.BODY_FALLBACK])
    ad_font.setPixelSize(round(54 * k))
    afm = QFontMetricsF(ad_font)
    slogan_font = QFont(ad_font)
    slogan_font.setPixelSize(round(40 * k))
    sfm = QFontMetricsF(slogan_font)
    acik_font = QFont()
    acik_font.setFamilies([F.BODY, F.BODY_FALLBACK])
    acik_font.setPixelSize(round(19 * k))
    acfm = QFontMetricsF(acik_font)
    kick_font = QFont(ad_font)
    kick_font.setPixelSize(round(20 * k))
    kfm = QFontMetricsF(kick_font)

    ad_ara = 18 * k
    blok_w = max(logo_kenar + ad_ara + afm.horizontalAdvance("Robz Vision"), sfm.horizontalAdvance(SLOGAN_GTA6))
    sol = W - kenar_payi - blok_w
    ust = kenar_payi

    logo = QImage(str(ROOT / "altyazitr" / "data" / "logo.png"))
    p.drawImage(QRectF(sol, ust, logo_kenar, logo_kenar), logo)
    ad_taban = ust + logo_kenar / 2 + afm.capHeight() / 2
    ad = _baslik_yolu(ad_font, sol + logo_kenar + ad_ara, ad_taban, "Robz Vision")
    p.drawImage(0, 0, _isilti(W, H, lambda q, c: q.fillPath(ad, c), C.PINK, 12 * k, 0.8))
    p.fillPath(ad, _dogrusal(0, ad_taban - afm.capHeight(), 0, ad_taban, ((0.0, "#FFFFFF"), (1.0, "#FFDCEB"))))

    slogan_taban = ust + logo_kenar + 30 * k + sfm.capHeight()
    on, vurgu, son = "GTA 6'yı ", "Türkçe", " oyna."
    x = sol
    for parca, renk in ((on, C.TEXT), (vurgu, C.PINK), (son, C.TEXT)):
        yol = QPainterPath()
        yol.addText(QPointF(x, slogan_taban), slogan_font, parca)
        p.fillPath(yol, QColor(renk))
        x += sfm.horizontalAdvance(parca)

    p.setFont(acik_font)
    p.setPen(_renk(C.TEXT, 0.88))
    y = slogan_taban + 16 * k + acfm.ascent()
    for satir in wrap_text(acfm, ACIKLAMA_GTA6, blok_w):
        p.drawText(QPointF(sol, y), satir)
        y += acfm.lineSpacing()

    # Kick adresi: gece yarısı zeminli hap; Kick yeşili yalnız logoda
    hap_h, ic, logo_px = 40 * k, 14 * k, 20 * k
    hap = QRectF(sol, y - acfm.ascent() + 22 * k, ic + logo_px + 10 * k + kfm.horizontalAdvance(KICK_ADRES) + ic, hap_h)
    yol = QPainterPath()
    yol.addRoundedRect(hap, hap_h / 2, hap_h / 2)
    p.fillPath(yol, _renk(C.BG, 0.82))
    p.strokePath(yol, QPen(QColor(C.LINE_STRONG), max(1.0, k)))
    pm = kick_pixmap(round(logo_px), 3.0)
    if not pm.isNull():
        p.drawPixmap(QPointF(hap.x() + ic, hap.y() + (hap_h - logo_px) / 2), pm)
    p.setFont(kick_font)
    p.setPen(QColor(C.TEXT))
    p.drawText(QPointF(hap.x() + ic + logo_px + 10 * k, hap.y() + (hap_h + kfm.capHeight()) / 2), KICK_ADRES)
    p.end()
    return img


def _etiketli(im, etiket: str):
    """Görselin sol üstüne küçük bir ad etiketi (gece yarısı zemin, Bahnschrift)."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

    from altyazitr.gui.tokens import C, F

    img = _pil_to_qimage(im)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont()
    font.setFamilies([F.DISPLAY_SEMIBOLD, F.DISPLAY, F.BODY_FALLBACK])
    font.setPixelSize(22)
    fm = QFontMetricsF(font)
    h, ic = 38.0, 14.0
    r = QRectF(20, 18, fm.horizontalAdvance(etiket) + 2 * ic, h)
    yol = QPainterPath()
    yol.addRoundedRect(r, 6, 6)
    p.fillPath(yol, _renk(C.BG, 0.84))
    p.strokePath(yol, QPen(QColor(C.LINE_STRONG), 1.0))
    p.setFont(font)
    p.setPen(QColor(C.TEXT))
    p.drawText(QPointF(r.x() + ic, r.y() + (h + fm.capHeight()) / 2), etiket)
    p.end()
    return _to_pil(img)


def _foto_png(im, path: Path) -> None:
    """Fotoğraf içerikli PNG: tam renkli dosya büyükse 256 renge indirilir (Floyd–Steinberg); ~1,5 MB sınırı."""
    from PIL import Image

    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    if buf.tell() > 1_400_000:
        im = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    _save_png(im, path)


def _webp(im, path: Path, kalite: int = 80) -> None:
    im.save(path, "WEBP", quality=kalite, method=6)
    print(f"  {_goreli(path)}  {im.width}×{im.height}  {path.stat().st_size // 1024} KB")


def gta6(klasorler: list[Path]) -> None:
    from PIL import Image

    print("GTA 6 sahneleri:")
    if not klasorler:
        print("  atlandı: resmî GTA VI görsellerinin klasörünü --kaynak KLASÖR ile verin (görseller depoda yok).")
        return
    kaynak: dict[str, Path] = {}
    for ad, sahne in GTA6_SAHNELER.items():
        yol = _gta6_bul(klasorler, sahne.dosya)
        if yol is None:
            print(f"  atlandı: {sahne.dosya} bulunamadı ({', '.join(str(k) for k in klasorler)}).")
            return
        kaynak[ad] = yol
    _qt_app()
    SITE_IMG.mkdir(parents=True, exist_ok=True)

    kahraman = _to_pil(gta6_kahraman_ciz(kaynak["ikili"]))
    _foto_png(kahraman, OUT / "gta6_kahraman.png")
    og = _to_pil(gta6_kahraman_ciz(kaynak["ikili"], 1200, 630))
    og.save(SITE_IMG / "og.jpg", "JPEG", quality=86, optimize=True, progressive=True)
    print(f"  {_goreli(SITE_IMG / 'og.jpg')}  1200×630  {(SITE_IMG / 'og.jpg').stat().st_size // 1024} KB")

    araba = GTA6_SAHNELER["araba"]
    for kip, ad in (("ingilizce", "gta6_once"), ("kutu", "gta6_sonra")):
        im = _to_pil(gta6_kare(kaynak["araba"], araba, 1280, 720, kip))
        _foto_png(im, OUT / f"{ad}.png")
        _webp(im, SITE_IMG / f"{ad}.webp")

    # Üzerine yazma: aynı sahnenin alt kısmı, üstte oyunun altyazısı, altta onu örten Türkçe kutu
    gece = GTA6_SAHNELER["gece"]
    W, H, kes = 1280, 720, 236
    bantlar = [_etiketli(_to_pil(gta6_kare(kaynak["gece"], gece, W, H, kip)).crop((0, kes, W, H)), etiket)
               for kip, etiket in (("ingilizce", "Oyunun altyazısı"),
                                   ("kapak", "Robz Vision: Oyun altyazısının üzerine"))]
    ara = 8
    ustune = Image.new("RGB", (W, 2 * (H - kes) + ara), (12, 11, 29))
    ustune.paste(bantlar[0], (0, 0))
    ustune.paste(bantlar[1], (0, H - kes + ara))
    _foto_png(ustune, OUT / "gta6_ustune.png")
    _webp(ustune, SITE_IMG / "gta6_ustune.webp")

    gta6_arayuz(kaynak["ikili"], kaynak["gece"])

    # Döngü (GTA6_DEMO): her satır altyazısız → İngilizce → çevirisiyle; son satır "üzerine" kipinde. Kareler 1080p
    # çizilip yarıya küçültülür: küçük yazı boylarındaki ipucu (hinting) kaymaları görünmez, kenarlar yumuşak olur.
    DW, DH = 960, 540
    gruplar: list[list[Image.Image]] = []  # aynı görselin kareleri (tek paletle kodlanır)
    durations: list[int] = []
    onceki = None
    for ad, son in GTA6_DEMO:
        sahne = GTA6_SAHNELER[ad]
        if sahne.dosya != onceki:
            gruplar.append([])
            onceki = sahne.dosya
        for kip, ms in (("bos", 450), ("ingilizce", 900), (son, 2900)):
            kare = _to_pil(gta6_kare(kaynak[ad], sahne, DW * 2, DH * 2, kip))
            gruplar[-1].append(kare.resize((DW, DH), Image.Resampling.LANCZOS))
            durations.append(ms)
    # Aynı görselin kareleri tek paletle: yalnız altyazı bölgesinde farklıdırlar, GIF bu farkı küçük bir dikdörtgen
    # olarak kodlar. Palet o görselin altyazılı karelerinden çıkarılır; yazı renkleri (beyaz, kenar siyahı, vurgu
    # pembesi, kapak zemini) ve kenar geçişleri paletin sonuna tam değerleriyle eklenir: yoksa birkaç piksellik pembe
    # kelime paletten pay alamaz, titreşimle (dither) bozulur.
    from altyazitr.config import StyleCfg

    st = StyleCfg()

    def rgb(h: str) -> tuple[int, int, int]:
        return tuple(int(h.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))

    def karisim(a, b, t):
        return tuple(round(x * (1 - t) + y * t) for x, y in zip(a, b))

    beyaz, siyah, pembe, kapak = rgb(st.text_color), (0, 0, 0), rgb(st.highlight_color), rgb(st.mask_color)
    sabit = [beyaz, siyah, pembe, kapak] + [karisim(c, z, t) for c in (beyaz, pembe) for z in (siyah, kapak)
                                             for t in (0.33, 0.66)]
    q = []
    for kareler in gruplar:
        altyazili = [k for i, k in enumerate(kareler) if i % 3]  # her satırın 2. ve 3. karesi
        src = Image.new("RGB", (DW, DH * len(altyazili)))
        for j, k in enumerate(altyazili):
            src.paste(k, (0, j * DH))
        taban = src.quantize(colors=256 - len(sabit), method=Image.Quantize.MEDIANCUT, kmeans=2)
        renkler = taban.getpalette()[:(256 - len(sabit)) * 3] + [v for c in sabit for v in c]
        palette = Image.new("P", (1, 1))
        palette.putpalette(renkler)
        q += [f.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for f in kareler]
    gif = OUT / "gta6_demo.gif"
    q[0].save(gif, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(f"  {_goreli(gif)}  {DW}×{DH}  {len(q)} kare  {gif.stat().st_size // 1024} KB")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--_arayuz":
        _arayuz_alt_sureci(sys.argv[2])
        return 0
    if len(sys.argv) > 4 and sys.argv[1] == "--_gta6_arayuz":
        _gta6_arayuz_alt_sureci(sys.argv[2], sys.argv[3], sys.argv[4])
        return 0
    args, kaynaklar = sys.argv[1:], []
    while "--kaynak" in args:  # --kaynak KLASÖR (birden çok kez verilebilir)
        i = args.index("--kaynak")
        if i + 1 >= len(args):
            print("--kaynak bir klasör ister")
            return 2
        kaynaklar.append(Path(args[i + 1]).expanduser())
        del args[i:i + 2]
    eksik = [str(k) for k in kaynaklar if not k.is_dir()]
    if eksik:
        print("Klasör bulunamadı:", ", ".join(eksik))
        return 2
    gecerli = ("banner", "demo", "arayuz", "gta6")
    parts = [a for a in args if not a.startswith("-")] or list(gecerli)
    unknown = set(parts) - set(gecerli)
    if unknown:
        print("Bilinmeyen bölüm:", ", ".join(sorted(unknown)), f"(geçerli: {', '.join(gecerli)})")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    if "arayuz" in parts:  # önce: ayrı süreç kendi QApplication'ını kurar
        arayuz()
    if "banner" in parts:
        banner()
    if "demo" in parts:
        demo()
    if "gta6" in parts:
        gta6(kaynaklar)
    print("Bitti:", OUT)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
