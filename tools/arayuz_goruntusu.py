"""Arayüzün tüm ekranlarını masaüstünde pencere açmadan PNG olarak kaydeder (tasarım denetimi için).

Kullanım:  python tools/arayuz_goruntusu.py ÇIKTI_KLASÖRÜ [--genislik 1100]
Çıktı: ana_pencere.png, sayfa_<ad>.png (tam yükseklik), secici.png, overlay_ayar.png, overlay_altyazi.png,
       bolge_penceresi.png, obs_yardim.png, ikonlar.png
Kullanıcının ayar dosyasına yazmaz; boş bir port seçer.
"""

from __future__ import annotations

import os
import socket
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


DOC_URL = "http://127.0.0.1:8765/"  # belgelerde gösterilen varsayılan OBS adresi


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "arayuz_goruntuleri")
    out.mkdir(parents=True, exist_ok=True)
    width = int(sys.argv[sys.argv.index("--genislik") + 1]) if "--genislik" in sys.argv else 1100

    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QScrollArea

    import altyazitr.app as appmod

    appmod.save_settings = lambda s: None
    errors: list[str] = []
    sys.excepthook = lambda *a: errors.append("".join(traceback.format_exception(*a)))

    from altyazitr.config import Settings
    from altyazitr.gui.theme import app_icon, apply_theme

    app = QApplication(sys.argv[:1])
    app.setWindowIcon(app_icon())
    appmod._install_translations(app)
    apply_theme(app)

    def hide_on_screen(w):
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

    def pump(sec=0.15):
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    s = Settings()
    s.first_run = False
    s.tour_seen = True  # tanıtım turu ekran görüntülerine karışmasın
    s.server.port = _free_port()
    s.capture.rect = [240, 820, 1440, 180]

    # GİZLİLİK: Ana sayfa canlı önizlemesi normalde GERÇEK ekranı yakalar. Bu araçta ekran yerine sahte bir
    # oyun sahnesi (tools/test_penceresi.paint_scene) kullanılır; görüntülere masaüstü içeriği karışmaz.
    _fake_frame = _fake_region_frame(s.capture.rect)
    import altyazitr.capture as capmod
    import altyazitr.gui.home_page as homemod

    def _fake_grab(_rect):
        return _fake_frame.copy()

    capmod.grab_screen_rect = _fake_grab
    if hasattr(homemod, "grab_screen_rect"):
        homemod.grab_screen_rect = _fake_grab
    ctx = appmod.AppContext(s)
    from altyazitr.gui.main_window import PAGES, MainWindow

    win = MainWindow(ctx)
    win.tray.hide()
    hide_on_screen(win)
    win.resize(width, 820)
    win.show()
    pump(0.5)
    # Araç çakışmasın diye rastgele boş bir portta çalışır; görüntülerde varsayılan adres görünsün
    home_url = getattr(win.pages.get("home"), "url", None)
    if home_url is not None:
        home_url.setText(DOC_URL)
    try:
        # örnek içerik: son çeviri + durum
        ctx.pipeline.subtitle.emit({"text": "Arabaya bin, Vice City'ye gidiyoruz.", "speaker": "LUCIA",
                                    "original": "Get in the car, we're going to Vice City.",
                                    "cached": False, "engine": "Yerel", "ocr_ms": 6.0, "tr_ms": 24.0,
                                    "latency_ms": 310.0})
        ctx.pipeline.status.emit("Seçilen ekran bölgesi okunuyor")
        for key, _title, _cls in PAGES:
            win.goto(key)
            pump(0.3)
            win.grab().save(str(out / f"ana_pencere_{key}.png"))
            pg = win.pages[key]
            sa = pg.findChild(QScrollArea)
            inner = sa.widget() if sa is not None else pg
            inner.resize(win.stack.width(), max(inner.sizeHint().height(), win.stack.height()))
            pump(0.1)
            inner.grab().save(str(out / f"sayfa_{key}.png"))
        win.goto("home")
        pump(0.2)
        win.grab().save(str(out / "ana_pencere.png"))

        # bölge seçici
        from altyazitr.gui import region_picker as rp

        picker = rp.ScreenRegionPicker(s.capture.rect)
        for w in picker._wins:
            hide_on_screen(w)
        picker.start()
        pump(0.2)
        w0 = picker._wins[0]
        from PySide6.QtTest import QTest

        QTest.mousePress(w0, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 800))
        QTest.mouseMove(w0, QPoint(1500, 960))
        QTest.mouseRelease(w0, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1500, 960))
        pump(0.1)
        bg = QPixmap(w0.size())
        bg.fill(QColor("#3b6d8f"))  # canlı oyunun yerini tutan düz zemin
        p = QPainter(bg)
        p.drawPixmap(0, 0, w0.grab())
        p.end()
        bg.save(str(out / "secici.png"))
        picker.finish(None)

        # ekran altyazısı: ayar modu + normal
        ov = ctx.overlay
        hide_on_screen(ov)
        ov.set_editing(True)
        pump(0.2)
        _on_bg(ov.grab()).save(str(out / "overlay_ayar.png"))
        ov.set_editing(False)
        ov.set_subtitle("Arabaya bin, Vice City'ye gidiyoruz.", "LUCIA", "Get in the car!")
        pump(0.5)
        _on_bg(ov.grab()).save(str(out / "overlay_altyazi.png"))

        # pencere modu bölge penceresi
        from altyazitr.gui.region_dialog import RegionDialog

        frame = (np.random.default_rng(0).random((720, 1280, 3)) * 60 + 40).astype(np.uint8)
        dlg = RegionDialog(frame, (0.12, 0.74, 0.76, 0.22), win)
        hide_on_screen(dlg)
        dlg.show()
        pump(0.2)
        dlg.grab().save(str(out / "bolge_penceresi.png"))
        dlg.close()

        # OBS yardım penceresi: exec() yerine build + show (asla modal bekleme / görünür pencere yok)
        from altyazitr.gui.dialogs import build_obs_help

        obs = build_obs_help(win, DOC_URL)
        hide_on_screen(obs)
        obs.show()
        pump(0.3)
        obs.grab().save(str(out / "obs_yardim.png"))
        obs.close()

        # ikon seti
        from altyazitr.gui import icons
        from altyazitr.gui.tokens import C

        names = icons.names()
        sheet = QPixmap(48 * 8, 48 * ((len(names) + 7) // 8))
        sheet.fill(QColor(C.BG))
        p = QPainter(sheet)
        for i, n in enumerate(names):
            p.drawPixmap(12 + (i % 8) * 48, 12 + (i // 8) * 48, icons.pixmap(n, 24, C.TEXT))
        p.end()
        sheet.save(str(out / "ikonlar.png"))
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        ctx.shutdown()
    print("Kaydedildi:", out.resolve())
    print("HATALAR:", "\n".join(errors) if errors else "yok")
    return 1 if errors else 0


def _fake_region_frame(rect):
    """Seçili bölgenin yerine sahte oyun sahnesinin aynı kesiti (BGR numpy)."""
    import numpy as np
    from PySide6.QtGui import QImage, QPainter

    sys.path.insert(0, str(ROOT / "tools"))
    from test_penceresi import paint_scene

    W, H = 1920, 1080
    img = QImage(W, H, QImage.Format.Format_RGB888)
    p = QPainter(img)
    paint_scene(p, 0.5, W, H)  # t=0.5: ilk altyazı ekranda
    p.end()
    a = np.frombuffer(img.constBits(), np.uint8).reshape(H, img.bytesPerLine())[:, :W * 3].reshape(H, W, 3)
    x, y, w, h = rect
    return np.ascontiguousarray(a[y:y + h, x:x + w, ::-1])


def _on_bg(pm):
    from PySide6.QtGui import QColor, QPainter, QPixmap

    bg = QPixmap(pm.size())
    bg.fill(QColor("#4a7fa3"))  # açık bir "oyun" zemini: yazının okunurluğunu görmek için
    p = QPainter(bg)
    p.drawPixmap(0, 0, pm)
    p.end()
    return bg


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
