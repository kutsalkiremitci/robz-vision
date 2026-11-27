"""Arayüzün tüm ekranlarını masaüstünde pencere açmadan PNG olarak kaydeder (tasarım denetimi için).

Kullanım:  python tools/arayuz_goruntusu.py ÇIKTI_KLASÖRÜ [--genislik 1280] [--yukseklik 840] [--sihirbaz]
--sihirbaz: kurulum sihirbazının her parçası da kaydedilir (sihirbaz_1.png …; Başla → Kuruluma başla).
Çıktı: ana_pencere.png, sayfa_<ad>.png (tam yükseklik), secici.png, overlay_ayar.png, overlay_altyazi.png,
       bolge_penceresi.png, obs_yardim.png, guncelleme.png, ikonlar.png
Pencere varsayılan olarak uygulamanın 1080p ekrandaki açılış boyutunda (1280×840): Ana sayfa geniş düzende.
Kullanıcının ayar dosyasına yazmaz; boş bir port seçer.

GİZLİLİK: Gerçek ekran ve pencereler hiçbir zaman yakalanmaz. Ana sayfanın canlı önizlemesi (SourceGrabber) hiç
çalışmaz; yerine sahte oyun sahnesi (tools/test_penceresi.paint_scene) gösterilir. Monitör listesi sabit tek
1920×1080 ekran, pencere listesi boştur; mss, pencere yakalama ve pencere görüntüsü alma kapatılır.
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
    width = int(sys.argv[sys.argv.index("--genislik") + 1]) if "--genislik" in sys.argv else 1280
    height = int(sys.argv[sys.argv.index("--yukseklik") + 1]) if "--yukseklik" in sys.argv else 840

    import numpy as np
    from PySide6.QtCore import QPoint, QSize, Qt
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
    from altyazitr.config import DEFAULT_REGION
    from altyazitr.gui.source_view import frac_to_rect

    s.capture.mode = "screen"
    s.capture.rect = frac_to_rect(DEFAULT_REGION, FAKE_MON)  # yeni kullanıcının varsayılan altyazı şeridi

    # GİZLİLİK: Ana sayfanın canlı önizlemesi normalde GERÇEK ekranı (ya da seçili pencereyi) yakalar. Bu araçta
    # ekranı ya da pencereleri okuyan her işlev sahte sürümüyle değiştirilir; önizlemede sahte bir oyun sahnesi
    # (tools/test_penceresi.paint_scene) görünür. Görüntülere masaüstü içeriği, pencere adı ya da sohbet karışmaz.
    scene = _fake_scene()
    _fake_frame = _fake_region_frame(s.capture.rect)
    import altyazitr.capture as capmod
    import altyazitr.gui.home_page as homemod
    import altyazitr.gui.main_window as mwmod
    import altyazitr.gui.source_view as svmod
    import altyazitr.pipeline as plmod
    import altyazitr.winutil as wu

    def _fake_grab(_rect):
        return _fake_frame.copy()

    def _no_mss(*_a, **_k):
        raise capmod.CaptureError("Görsel üretiminde ekran yakalama kapalı")

    class _NoCapture:
        def __init__(self, *_a, **_k):
            raise capmod.CaptureError("Görsel üretiminde ekran ve pencere yakalama kapalı")

    capmod.grab_screen_rect = _fake_grab
    capmod._new_mss = _no_mss
    capmod.snapshot_window = lambda *a, **k: None
    capmod.WindowCapturer = capmod.RegionCapturer = _NoCapture
    svmod.SourceGrabber.start = lambda self: None  # önizleme yakalayıcısı hiç çalışmaz
    svmod.list_monitors = lambda: [FAKE_MON]
    wu.list_windows = lambda: []
    wu.find_window = lambda *a, **k: None
    for mod in (homemod, mwmod, plmod):  # adıyla içe aktarılmış kopyalar da
        for name, fake in (("grab_screen_rect", _fake_grab), ("snapshot_window", capmod.snapshot_window),
                           ("WindowCapturer", _NoCapture), ("RegionCapturer", _NoCapture),
                           ("list_monitors", svmod.list_monitors)):
            if hasattr(mod, name):
                setattr(mod, name, fake)
    ctx = appmod.AppContext(s)
    from altyazitr.gui.main_window import PAGES, MainWindow

    win = MainWindow(ctx)
    win.tray.hide()
    hide_on_screen(win)
    win.resize(width, height)
    win.show()
    pump(0.5)
    # Araç çakışmasın diye rastgele boş bir portta çalışır; görüntülerde varsayılan adres görünsün
    home_url = getattr(win.pages.get("home"), "url", None)
    if home_url is not None:
        home_url.setText(DOC_URL)

    def fake_source() -> None:
        """Ana sayfanın canlı önizlemesine sahte oyun sahnesi (yakalayıcı çalışmadığı için kare hiç gelmez)."""
        src = getattr(win.pages.get("home"), "source", None)
        if src is not None:
            src.set_frame(scene, QSize(*FAKE_MON[2:]))
            src.set_message("")
            src.set_notice("")
            pump(0.05)

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
            if key == "home":
                fake_source()
            win.grab().save(str(out / f"ana_pencere_{key}.png"))
            pg = win.pages[key]
            sa = pg.findChild(QScrollArea)
            inner = sa.widget() if sa is not None else pg
            inner.resize(win.stack.width(), max(inner.sizeHint().height(), win.stack.height()))
            pump(0.1)
            inner.grab().save(str(out / f"sayfa_{key}.png"))
        win.goto("home")
        pump(0.2)
        fake_source()
        win.grab().save(str(out / "ana_pencere.png"))

        if "--sihirbaz" in sys.argv:  # kurulum sihirbazı: her parçada sayfa, vurgu ve balon
            from altyazitr.gui import tour

            s.guide_done = []
            win.goto("start")
            pump(0.2)
            tour.start_setup_wizard(win, "source")
            wiz = tour.active_tour(win)
            for i in range(wiz.count if wiz is not None else 0):
                pump(0.3)
                if wiz.steps[wiz.index].page == "home":
                    fake_source()
                win.grab().save(str(out / f"sihirbaz_{i + 1}.png"))
                wiz.next()
            pump(0.1)

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
        from altyazitr.gui.region_dialog import PRESET_BOTTOM, RegionDialog

        frame = (np.random.default_rng(0).random((720, 1280, 3)) * 60 + 40).astype(np.uint8)
        dlg = RegionDialog(frame, PRESET_BOTTOM, win)
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

        # güncelleme penceresi: ağa gitmeden "yeni sürüm var" durumu (bkz. _fake_update), ana pencerenin açtığı gibi
        _fake_update(ctx.updater)
        win.open_update_dialog()
        pump(0.3)
        win._update_dlg.grab().save(str(out / "guncelleme.png"))
        win._update_dlg.reject()

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


FAKE_MON = (0, 0, 1920, 1080)  # görsellerdeki tek sahte ekran (fiziksel piksel)
_SCENE = []


def _fake_scene():
    """Gerçek ekranın yerine 1920×1080 sahte oyun sahnesi (QImage, RGB888); t=0.5: ilk altyazı ekranda."""
    if not _SCENE:
        from PySide6.QtGui import QImage, QPainter

        sys.path.insert(0, str(ROOT / "tools"))
        from test_penceresi import paint_scene

        W, H = FAKE_MON[2:]
        img = QImage(W, H, QImage.Format.Format_RGB888)
        p = QPainter(img)
        paint_scene(p, 0.5, W, H)
        p.end()
        _SCENE.append(img)
    return _SCENE[0]


def _fake_region_frame(rect):
    """Seçili bölgenin yerine sahte oyun sahnesinin aynı kesiti (BGR numpy)."""
    import numpy as np

    img = _fake_scene()
    W, H = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8).reshape(H, img.bytesPerLine())[:, :W * 3].reshape(H, W, 3)
    x, y, w, h = rect
    return np.ascontiguousarray(a[y:y + h, x:x + w, ::-1])


def _fake_update(upd) -> None:
    """Güncelleme servisi ağsız "yeni sürüm var" durumuna getirilir: yeni yayınlanmış sürüm, CHANGELOG.md'de yeni
    özellik ("### Eklendi") içeren en son sürüm (yalnız düzeltme içeren küçük sürümlerin notu vitrinde zayıf kalır);
    kullanılan sürüm ondan bir önceki. Notlar sürüm sayfasındaki gibi CHANGELOG'daki bölüm (tools/surum_notlari.py).
    Kurulu kopyanın görünümü (kind "user", kurulum dosyası var: Güncelle düğmesi). Hiçbir şey inmez."""
    import re

    from altyazitr import __version__
    from altyazitr.updater import SUMS_NAME, Asset, Release, setup_name

    sys.path.insert(0, str(ROOT / "tools"))
    from surum_notlari import changelog_section

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    v = next((x for x in versions if "### Eklendi" in changelog_section(x, text)), __version__)
    prev = versions[versions.index(v) + 1] if v in versions[:-1] else v
    upd.release = Release(v, f"v{v}", f"{upd.source.releases_url}/tag/v{v}", changelog_section(v, text),
                          Asset(setup_name(v), "", 1), Asset(SUMS_NAME, "", 1))
    upd.kind, upd.version, upd.status = "user", prev, "available"
    upd.changed.emit()


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
