"""README ve kullanım kılavuzu görsellerini üretir (docs/gorseller/).

Kullanım:  python tools/readme_gorselleri.py [banner] [demo] [arayuz]
           (bölüm adı verilmezse hepsi üretilir)

Çıktı:
  banner.png          1280×640 tanıtım görseli; GitHub "Social preview" olarak da yüklenebilir
  demo.gif            sahte oyun sahnesi: İngilizce altyazı → Türkçe çeviri kutusu (döngü)
  demo_once.png       aynı sahnenin çevirisiz hâli
  demo_sonra.png      aynı sahnenin çevirili hâli
  ana_pencere.png     Ana sayfa              (tools/arayuz_goruntusu.py çıktısından)
  gorunum.png         Görünüm sayfası
  ceviri.png          Çeviri sayfası
  bolge_secimi.png    bölge seçici (sahte oyun sahnesinin üstünde)
  konum_ayari.png     "Konumu ayarla" modundaki ekran altyazısı kutusu
  obs_yardim.png      "OBS'e nasıl eklenir?" penceresi

Arayüz yeniden tasarlandığında bu betiği yeniden çalıştırmanız yeterlidir.

GİZLİLİK: Görseller hiçbir zaman gerçek ekranı yakalamaz. Arayüz görüntüleri ayrı bir süreçte, masaüstünde
pencere açmadan alınır; o süreçte ekran/pencere yakalayan her işlev (mss, grab_screen_rect, WindowCapturer,
snapshot_window, pencere listesi) sahte oyun sahnesi döndüren ya da hiçbir şey döndürmeyen sürümlerle
değiştirilir. Böylece masaüstündeki sohbetler, sekmeler veya pencere adları görsellere giremez.
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import subprocess
import sys
import tempfile
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
    ("ana_pencere.png", "ana_pencere.png", None),
    ("ana_pencere_style.png", "gorunum.png", None),
    ("ana_pencere_translate.png", "ceviri.png", None),
    ("secici.png", "bolge_secimi.png", (0.08, 0.58, 0.92, 1.0)),
    ("overlay_ayar.png", "konum_ayari.png", None),
    ("obs_yardim.png", "obs_yardim.png", None),
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


def _save_png(im, path: Path) -> None:
    im.save(path, optimize=True)
    print(f"  {path.relative_to(ROOT)}  {im.width}×{im.height}  {path.stat().st_size // 1024} KB")


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
    varsayılan görünümle (config.StyleCfg()) çizilir; kutu oyunun altyazısının hemen üstüne konur."""
    from PySide6.QtGui import QImage, QPainter

    from altyazitr.config import StyleCfg
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
        paint_subtitle(p, bw, bh, StyleCfg(), text, speaker, in_box=True, ref_height=H)
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
    from altyazitr.config import StyleCfg

    st = StyleCfg()
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


# --- banner ---------------------------------------------------------------------------------------------
def banner() -> None:
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QRadialGradient

    from altyazitr.config import StyleCfg
    from altyazitr.gui.tokens import C, F
    from altyazitr.render import make_font, paint_subtitle

    _qt_app()
    print("Banner:")
    W, H = 1280, 640
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(0, 0, W, H, QColor(C.BG))

    title = QFont()
    title.setFamilies([F.DISPLAY_CONDENSED, F.DISPLAY, F.BODY_FALLBACK])
    title.setPixelSize(100)
    tfm = QFontMetricsF(title)
    body = QFont()
    body.setFamilies([F.BODY, F.BODY_FALLBACK])
    body.setPixelSize(29)
    bfm = QFontMetricsF(body)
    src = QFont(body)
    src.setPixelSize(20)
    sfm = QFontMetricsF(src)
    tagline = ("Oyunlardaki İngilizce altyazıları ekrandan okur,", "yerel olarak Türkçeye çevirir.")
    st = StyleCfg()
    sample_px = 30
    sample = "Sana buraya geri gelmemeni söylemiştim."
    sample_en = "I told you not to come back here."
    sfont = make_font(st, sample_px)
    sample_w = QFontMetricsF(sfont).horizontalAdvance(sample)
    box_w, box_h = sample_w + sample_px * 1.4 + 8, QFontMetricsF(sfont).lineSpacing() + sample_px * 0.36 + 6

    # dikey düzen: ad, gün batımı çizgisi, açıklama, örnek çeviri; blok ve logo dikeyde ortalanır
    rule_gap, rule_h, after_rule = 12, 4, 30
    tag_h = bfm.lineSpacing() * 1.08 * len(tagline)
    block_h = (tfm.ascent() + tfm.descent() + rule_gap + rule_h + after_rule + tag_h + 36
               + sfm.lineSpacing() + 4 + box_h)
    top = (H - block_h) / 2

    # logonun arkasında çok hafif bir pembe ışıma (kurulum sihirbazı görselleriyle aynı dil)
    logo_side = 236
    lx, ly = 104, (H - logo_side) / 2
    glow = QRadialGradient(QPointF(lx + logo_side / 2, ly + logo_side / 2), logo_side * 1.15)
    g0, g1 = QColor(C.PINK), QColor(C.PINK)
    g0.setAlphaF(0.13)
    g1.setAlphaF(0.0)
    glow.setColorAt(0, g0)
    glow.setColorAt(1, g1)
    p.fillRect(0, 0, W, H, glow)
    logo = QImage(str(ROOT / "altyazitr" / "data" / "logo.png"))
    p.drawImage(QRectF(lx, ly, logo_side, logo_side), logo)

    x = lx + logo_side + 64
    p.setFont(title)
    p.setPen(QColor(C.TEXT))
    base = top + tfm.ascent()
    p.drawText(QPointF(x - 4, base), "Robz Vision")
    # gün batımı çizgisi (canlı başlık bandının alt çizgisiyle aynı geçiş)
    rule_y = base + tfm.descent() + rule_gap
    grad = QLinearGradient(QPointF(x, 0), QPointF(x + 132, 0))
    grad.setColorAt(0, QColor(C.PINK))
    grad.setColorAt(1, QColor(C.ORANGE))
    p.fillRect(QRectF(x, rule_y, 132, rule_h), grad)
    p.setFont(body)
    p.setPen(QColor(C.TEXT_2))
    y = rule_y + rule_h + after_rule
    for line in tagline:
        p.drawText(QPointF(x, y + bfm.ascent()), line)
        y += bfm.lineSpacing() * 1.08

    # örnek: oyundaki İngilizce satır ve uygulamanın kendi altyazı çizimiyle Türkçesi
    y += 36
    p.setFont(src)
    p.setPen(QColor(C.TEXT_3))
    p.drawText(QPointF(x, y + sfm.ascent()), sample_en)
    y += sfm.lineSpacing() + 4
    p.save()
    p.translate(x - (box_w - sample_w) / 2, y)  # yazı sola hizalı, kutu dolgusu soldan taşar
    paint_subtitle(p, box_w, box_h, st, sample, in_box=True, ref_height=1080 * sample_px / st.font_size)
    p.restore()
    p.end()
    _save_png(_to_pil(img), OUT / "banner.png")


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

    sys.argv = [str(TOOLS / "arayuz_goruntusu.py"), out_dir]
    runpy.run_path(str(TOOLS / "arayuz_goruntusu.py"), run_name="__main__")


def _has_app() -> bool:
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance() is not None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--_arayuz":
        _arayuz_alt_sureci(sys.argv[2])
        return 0
    parts = [a for a in sys.argv[1:] if not a.startswith("-")] or ["banner", "demo", "arayuz"]
    unknown = set(parts) - {"banner", "demo", "arayuz"}
    if unknown:
        print("Bilinmeyen bölüm:", ", ".join(sorted(unknown)), "(geçerli: banner, demo, arayuz)")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    if "arayuz" in parts:  # önce: ayrı süreç kendi QApplication'ını kurar
        arayuz()
    if "banner" in parts:
        banner()
    if "demo" in parts:
        demo()
    print("Bitti:", OUT)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
