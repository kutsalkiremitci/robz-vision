"""İki görünüm: ekrandaki kutu "Ekranımda" (Settings.screen_style), OBS sayfası ve OBS maskesi "Yayınımda"
(Settings.style) ayarlarıyla çizilir; push_style() ikisini de günceller (ekran gerektirmez; Qt 'offscreen').

PySide6 burada yalnızca fonksiyonların içinde içe aktarılır; 'offscreen' de QApplication kurulmadan hemen
önce ayarlanır (test_tour gibi). Modül yüklenirken ayarlansaydı, testler toplanırken bütün sürece yayılır
ve kendi QGuiApplication'ını kuran test_core'un çizim testini de 'offscreen'e taşırdı."""

import asyncio
import json
import os
import socket

import aiohttp
import numpy as np
import pytest

from altyazitr.config import Settings
from altyazitr.server import AsyncBridge, OverlayServer, style_vars

OBS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/127.0.6533.120 OBS/32.2.2 Safari/537.36")
RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)


def _settings() -> Settings:
    """Birbirinden çok farklı iki görünüm: yayın yeşil/büyük/maskeli, ekran kırmızı/küçük/maskesiz."""
    s = Settings()
    s.overlay.enabled = False  # kutu hiç gösterilmez (show() yok → Win32 çağrısı da yok); çizim render() ile
    s.capture.mode = "window"  # maske pencereye göre: sonuç bu makinenin monitörlerinden bağımsız
    s.capture.region = [0.1, 0.7, 0.8, 0.2]
    st, sc = s.style, s.screen_style
    st.text_color, st.font_size, st.fade_ms, st.show_original = "#00FF00", 80, 40, True
    st.mask_original, st.mask_pad_percent, st.mask_color, st.mask_opacity = True, 2.0, "#123456", 0.5
    sc.text_color, sc.font_size, sc.fade_ms, sc.show_original = "#FF0000", 24, 900, False
    sc.mask_original, sc.mask_pad_percent, sc.mask_color, sc.mask_opacity = False, 0.0, "#ABCDEF", 1.0
    for x in (st, sc):  # renk sayımı temiz olsun: kenar, gölge, arka plan yok; konuşmacı yazıyla aynı renk
        x.outline_width, x.shadow, x.bg_opacity = 0.0, False, 0.0
        x.speaker_color = x.text_color
    return s


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and (not isinstance(app, QApplication) or app.platformName() != "offscreen"):
        # başka bir test yalnız QGuiApplication (ya da ekrana bağlı bir uygulama) açmış olabilir;
        # QWidget için QApplication, masaüstünde hiçbir şey görünmesin diye 'offscreen' gerekir
        app.shutdown()
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # QApplication'dan hemen önce
        app = QApplication([])
    assert app.platformName() == "offscreen"
    return app


def _make_overlay(s: Settings):
    from altyazitr.overlay import ScreenOverlay

    ov = ScreenOverlay(s)
    ov.resize(900, 220)
    return ov


def _close_overlay(ov) -> None:
    ov._anim.stop()
    ov.deleteLater()


@pytest.fixture
def overlay(qapp):
    ov = _make_overlay(_settings())
    try:
        yield ov
    finally:
        _close_overlay(ov)


@pytest.fixture
def live(qapp):
    """Gerçek OBS sunucusu (boş portta) + gerçek ekran kutusu (gizli) + SubtitleDisplay."""
    from altyazitr.display import SubtitleDisplay

    s = _settings()
    bridge = AsyncBridge()
    bridge.start()
    server = OverlayServer(_free_port())
    bridge.submit(server.start()).result(5)
    ov = _make_overlay(s)
    display = SubtitleDisplay(s, bridge, server, ov)
    try:
        yield s, server, display, ov
    finally:
        display._hide.stop()
        _close_overlay(ov)
        bridge.submit(server.stop()).result(5)
        bridge.stop()


# --- yardımcılar -------------------------------------------------------------------------
def _opaque_now(ov) -> None:
    """Geçişi beklemeden tam görünür yap (çizim testi zamanlamaya bağlı olmasın)."""
    ov._anim.stop()
    ov._opacity = 1.0


def _render(ov) -> np.ndarray:
    """Kutuyu ekranda göstermeden şeffaf bir resme çizer → (y, x, BGRA)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    img = QImage(ov.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    ov.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def _count(px: np.ndarray, rgb: tuple[int, int, int]) -> int:
    b, g, r, a = (px[..., i].astype(int) for i in range(4))
    tr, tg, tb = rgb
    return int(((abs(r - tr) < 60) & (abs(g - tg) < 60) & (abs(b - tb) < 60) & (a > 200)).sum())


def _text_height(px: np.ndarray) -> int:
    rows = np.nonzero((px[..., 3] > 128).any(axis=1))[0]
    return int(rows[-1] - rows[0] + 1) if rows.size else 0


async def _obs_state(server: OverlayServer) -> dict:
    """OBS sayfasının bağlanınca aldığı son durum: {"style": …, "sub": …, "mask": …}."""
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(f"ws://127.0.0.1:{server.port}/ws", headers={"User-Agent": OBS_UA}) as ws:
            got: dict = {}
            while len(got) < 3:
                m = json.loads((await ws.receive(timeout=5)).data)
                got[m["type"]] = m
            return got


def _state(server: OverlayServer) -> dict:
    return asyncio.run(_obs_state(server))


# --- OBS sayfası: yayın görünümü --------------------------------------------------------------
def test_obs_sayfasi_yayin_gorunumunu_alir(live):
    s, server, display, _ = live
    display.push_style()
    vars_ = _state(server)["style"]["vars"]
    assert vars_ == style_vars(s.style)
    assert vars_ != style_vars(s.screen_style)
    assert vars_["color"] == "#00FF00" and vars_["orig-display"] == "block"
    assert vars_["mask"] == "rgba(18,52,86,0.500)"  # maske rengi de yayın görünümünden

    # ekran görünümü değişince OBS'e giden değişmez …
    s.screen_style.font_size, s.screen_style.text_color = 99, "#0000FF"
    display.push_style()
    assert _state(server)["style"]["vars"] == style_vars(s.style)
    # … yayın görünümü değişince aynı çağrıyla OBS'e gider
    s.style.text_color = "#FFFF00"
    display.push_style()
    assert _state(server)["style"]["vars"]["color"] == "#FFFF00"


def test_obs_maskesi_yayin_gorunumunu_kullanir(live):
    s, server, display, _ = live
    display.push_style()
    display.show_test()
    # bölge [0.1, 0.7, 0.8, 0.2] + yayının %2 payı (ekranın payı 0 olsaydı maske bölgeyle aynı olurdu)
    assert _state(server)["mask"]["rect"] == pytest.approx([0.08, 0.68, 0.84, 0.24])

    # maskeyi açıp kapatan da yayın görünümü: ekranınki açık olsa da OBS'te maske yok
    s.style.mask_original, s.screen_style.mask_original = False, True
    display.push_style()
    assert _state(server)["mask"]["rect"] is None


# --- ekrandaki kutu: ekran görünümü ------------------------------------------------------------
def test_ekran_kutusu_ekran_gorunumuyle_cizilir(overlay):
    s = overlay.s
    overlay.set_subtitle("Arabaya bin", None, "Get in the car")
    _opaque_now(overlay)
    px = _render(overlay)
    assert _count(px, RED) > 100  # ekran görünümünün rengi
    assert _count(px, GREEN) == 0  # yayın görünümünün rengi hiç yok
    assert _count(px, WHITE) == 0  # ekranda "orijinali göster" kapalı (yayında açık)

    small = _text_height(px)
    s.screen_style.font_size = 72
    big = _text_height(_render(overlay))
    assert big > 2 * small
    s.style.font_size = 10  # yayın görünümünü değiştirmek kutuyu etkilemez
    assert _text_height(_render(overlay)) == big


def test_kutu_cizim_ve_ayar_modu_ekran_gorunumunu_verir(overlay, monkeypatch):
    import altyazitr.overlay as ovmod

    seen = []
    monkeypatch.setattr(ovmod, "paint_subtitle", lambda p, W, H, st, *a, **k: seen.append(st))
    overlay.set_subtitle("Arabaya bin", None, "")
    _opaque_now(overlay)
    _render(overlay)
    overlay.editing = True  # ayar modu (set_editing pencereyi gösterirdi): örnek yazı da ekran görünümüyle
    overlay.text = ""
    _render(overlay)
    assert len(seen) == 2 and all(st is overlay.s.screen_style for st in seen)


def test_gecis_suresi_ekran_gorunumunden_ve_degisince_uyar(overlay):
    s = overlay.s
    overlay.set_subtitle("Arabaya bin", None, "")
    assert overlay._anim.duration() == 900  # yayınınki 40 ms
    s.screen_style.fade_ms = 250  # önbellek yok: bir sonraki geçiş yeni süreyi kullanır
    _opaque_now(overlay)
    overlay.clear()
    assert overlay._anim.duration() == 250


def test_push_style_kutuyu_guncel_ekran_gorunumuyle_ciziyor(live):
    s, _, display, ov = live
    display.show_test()
    _opaque_now(ov)
    assert _count(_render(ov), RED) > 100
    s.screen_style.text_color = s.screen_style.speaker_color = "#0000FF"
    display.push_style()
    px = _render(ov)
    assert _count(px, BLUE) > 100 and _count(px, RED) == 0 and _count(px, GREEN) == 0


# --- Test düğmesi: iki çıktı, her biri kendi görünümüyle --------------------------------------
def test_test_dugmesi_iki_ciktida_kendi_gorunumuyle(live):
    _, server, display, ov = live
    display.push_style()
    display.show_test()
    st = _state(server)
    assert "test altyazısıdır" in st["sub"]["text"] and st["style"]["vars"]["color"] == "#00FF00"
    assert "test altyazısıdır" in ov.text
    _opaque_now(ov)
    px = _render(ov)
    assert _count(px, RED) > 100 and _count(px, GREEN) == 0
