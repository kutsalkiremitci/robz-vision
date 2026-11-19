"""Yalnız altyazıları oku (subtitle_style): altyazı biçimindeki satırlar geçer; plaka, tabela, neon, eğik duvar
yazısı, HUD atılır. Altyazının ortası bölgenin ortası değilse öğrenilir.

Ekran, ağ ve OCR motoru gerektirmez. Kareler sentetiktir (OpenCV yazı tipi, altyazı cümleleri özgün); satır kutuları
OCR'ın vereceği gibi yazının kapladığı dikdörtgendir. Kareler yayın görüntüsü gibi JPEG ve 4:2:0 ile de sıkıştırılır.
Ölçümün kendisi (gerçek OCR, yüzlerce sahne): tools/altyazi_bicimi_testi.py.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from altyazitr.config import Settings
from altyazitr.ocr import OcrLine
from altyazitr.subtitle_style import SubtitleFilter, line_style, lower_words, plate_like, sentence_like

FONT = cv2.FONT_HERSHEY_DUPLEX
W, H = 1200, 180
# BGR
WHITE, PINK, NEON = (236, 236, 236), (155, 128, 216), (240, 60, 230)


def _zemin(kind: str = "gece", seed: int = 0) -> np.ndarray:
    """Koyu (gece), açık (beton) ya da renkli ışıklı (neon) zemin."""
    rng = np.random.default_rng(seed)
    if kind == "beton":
        img = np.full((H, W, 3), 196, np.float32) + rng.normal(0, 8, (H, W, 1))
    else:
        img = np.full((H, W, 3), 30, np.float32) + rng.normal(0, 4, (H, W, 1))
    if kind == "neon":
        for _ in range(10):
            c = (int(rng.integers(0, W)), int(rng.integers(0, H)))
            cv2.circle(img, c, int(rng.integers(20, 60)), [float(v) for v in rng.integers(60, 255, 3)], -1)
        img = cv2.GaussianBlur(img, (0, 0), 4)
    return np.clip(img, 0, 255).astype(np.uint8)


def _yaz(img: np.ndarray, parts, x: float | None = None, y: int = 150, scale: float = 1.1, thick: int = 2,
         outline: bool = True, shadow: bool = True, angle: float = 0.0, cx: float | None = None) -> OcrLine:
    """Yazıyı basar (parçalar: [(metin, BGR)]) ve satırın OCR kutusunu döndürür. cx verilirse yatay ortası orada;
    angle derece (duvar yazısı). Kenar: ince koyu dış çizgi; gölge: sağ alta kaymış yumuşak koyu."""
    text = "".join(t for t, _ in parts)
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
    if cx is not None:
        x = cx - tw / 2
    layers, union, pos = [], np.zeros(img.shape[:2], np.uint8), int(x)
    for t, color in parts:
        m = np.zeros(img.shape[:2], np.uint8)
        cv2.putText(m, t, (pos, y), FONT, scale, 255, thick, cv2.LINE_AA)
        pos += cv2.getTextSize(t, FONT, scale, thick)[0][0]
        layers.append((m, color))
    if angle:
        rot = cv2.getRotationMatrix2D((x + tw / 2, y - th / 2), angle, 1.0)
        layers = [(cv2.warpAffine(m, rot, (img.shape[1], img.shape[0])), c) for m, c in layers]
    for m, _ in layers:
        union = np.maximum(union, m)
    f = img.astype(np.float32)
    if shadow:
        a = cv2.GaussianBlur(np.roll(union, (2, 2), (0, 1)).astype(np.float32), (0, 0), 1.2) / 255 * 0.7
        f *= 1 - a[..., None]
    if outline:
        ring = cv2.dilate(union, np.ones((3, 3), np.uint8)).astype(np.float32) / 255 * 0.9
        f = f * (1 - ring[..., None]) + 12 * ring[..., None]
    for m, color in layers:
        a = (m.astype(np.float32) / 255)[..., None]
        f = f * (1 - a) + np.array(color, np.float32) * a
    img[:] = np.clip(f, 0, 255).astype(np.uint8)
    ys, xs = np.nonzero(union > 60)
    return OcrLine(text, 0.95, float(xs.min() - 3), float(ys.min() - 3), float(xs.max() - xs.min() + 6),
                   float(ys.max() - ys.min() + 6))


def _panel(img: np.ndarray, text: str, x: int, y: int, panel_bgr, text_bgr, scale: float = 1.0) -> OcrLine:
    """Düz renkli levhada yazı (plaka, tabela)."""
    (tw, th), base = cv2.getTextSize(text, FONT, scale, 2)
    cv2.rectangle(img, (x - 10, y - th - 10), (x + tw + 10, y + base + 8), panel_bgr, -1)
    return _yaz(img, [(text, text_bgr)], x=x, y=y, scale=scale, outline=False, shadow=False)


def _yayin(img: np.ndarray, kind: str) -> np.ndarray:
    """Yayın görüntüsü gibi sıkıştırma: 4:2:0 renk alt örneklemesi ya da JPEG."""
    if kind == "yuv420":
        return cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420), cv2.COLOR_YUV2BGR_I420)
    if kind == "jpeg":
        return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 55])[1], cv2.IMREAD_COLOR)
    return img


def _texts(lines) -> list[str]:
    return [ln.text for ln in lines]


# --- biçim ölçümü ----------------------------------------------------------------------------------------------------
def test_kenarli_beyaz_altyazi_belirgin_tabela_ve_egik_yazi_degil():
    img = _zemin()
    sub = _yaz(img, [("Keep the engine running, I will be right back.", WHITE)], cx=W / 2)
    sign = _panel(img, "OPEN", 120, 60, (60, 160, 40), (245, 245, 245))  # yeşil levhada beyaz yazı: kenarı koyu değil
    neon = _yaz(img, [("CLUB", NEON)], x=900, y=60, outline=False, shadow=False)
    wall = _yaz(img, [("KEEP OUT HERE", WHITE)], x=420, y=70, outline=False, shadow=False, angle=12)
    st = line_style(img, sub)
    assert st.strong and not st.colored and abs(st.tilt) < 1
    assert not line_style(img, sign).strong
    assert line_style(img, neon).colored
    assert line_style(img, wall).tilted


def test_metin_ipuclari():
    assert plate_like("LND 4821") and plate_like("6ABC123") and not plate_like("Go to 5th street")
    assert lower_words("Come where?") and not lower_words("KEEP OUT") and not lower_words("Okay.")
    assert sentence_like("Meet me at the old marina tonight.") and not sentence_like("PAWN SHOP OPEN")


# --- süzgeç ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("comp", ["temiz", "jpeg", "yuv420"])
def test_altyazi_gecer_dikkat_dagiticilar_atilir(comp):
    """Aynı karede: ortalı kenarlı altyazı (geçer); ortadaki araç plakası, kenardaki HUD, neon tabela, eğik duvar
    yazısı (atılır). Yayın sıkıştırmasıyla da."""
    img = _zemin("neon", 3)
    sub = _yaz(img, [("We need to get out of here before the cops show up.", WHITE)], cx=W / 2)
    plate = _panel(img, "LND 4821", 535, 40, (235, 235, 235), (90, 40, 10), scale=0.9)
    hud = _yaz(img, [("$12,450", WHITE)], x=1080, y=40, scale=0.7, thick=1)
    neon = _yaz(img, [("LIVE MUSIC", NEON)], x=150, y=50, outline=False, shadow=False)
    wall = _yaz(img, [("PRIVATE PROPERTY", WHITE)], x=760, y=95, scale=0.8, outline=False, shadow=False, angle=-10)
    img = _yayin(img, comp)
    kept = SubtitleFilter().keep(img, [plate, hud, neon, wall, sub])
    assert _texts(kept) == [sub.text]


def test_iki_satirli_vurgulu_altyazi_ve_tamami_vurgulu_satir():
    """İki satırlı görev yazısı: ilk satırda pembe kelime, ikinci satırın tamamı pembe (renkli satır, öbür satıra
    bağlı olduğu için geçer)."""
    img = _zemin("neon", 5)
    a = _yaz(img, [("Take the Phoenix to the ", WHITE), ("garage", PINK)], cx=W / 2, y=115)
    b = _yaz(img, [("Pay and Spray.", PINK)], cx=W / 2, y=155)
    img = _yayin(img, "yuv420")
    assert line_style(img, b).colored
    assert _texts(SubtitleFilter().keep(img, [a, b])) == [a.text, b.text]


def test_bolunmus_satirin_parcalari_gecer():
    """RapidOCR bir satırı iki kutuda okuyabilir: parçalar tek başına ortalı değil, birlikte ortalı."""
    img = _zemin()
    full = _yaz(img, [("Stay low and wait for my signal, then we go.", WHITE)], cx=W / 2)
    mid = full.x + full.w * 0.45
    left = OcrLine("Stay low and wait for", 0.95, full.x, full.y, mid - full.x - 4, full.h)
    right = OcrLine("my signal, then we go.", 0.95, mid + 4, full.y, full.x + full.w - mid - 4, full.h)
    assert _texts(SubtitleFilter().keep(img, [left, right])) == [left.text, right.text]


def _learned_filter(cx: float) -> SubtitleFilter:
    """Ortası cx olan altyazıyı üç farklı satırla öğrenmiş süzgeç."""
    f = SubtitleFilter()
    for i, text in enumerate(["You said this would be easy.", "Nobody told me the boat was this small.",
                              "Relax, nobody is following us."]):
        img = _zemin(seed=i)
        assert len(f.keep(img, [_yaz(img, [(text, WHITE)], cx=cx)])) == 1
    return f


def test_bolge_simetrik_degilse_orta_ogrenilir():
    """Altyazının ortası bölgenin ortasından 0,12 genişlik sağda. Öğrenince: o ortadaki, kenarı seçilmeyen (açık
    zeminde kenarsız beyaz) altyazı geçer; bölgenin ortasındaki aynı boydaki biçimi zayıf büyük harfli yazı atılır."""
    cx = W / 2 + 0.12 * W
    f = _learned_filter(cx)
    learned = f.learned()
    assert learned is not None and abs(learned.cx - cx) < 5
    img = _zemin("beton", 7)
    faint = _yaz(img, [("Hold on, something feels wrong.", (252, 252, 252))], cx=cx, outline=False, shadow=False)
    assert not line_style(img, faint).strong
    sign = _yaz(img, [("DELIVERIES", (250, 250, 250))], cx=W / 2 - 250, y=60, outline=False, shadow=False)
    assert _texts(f.keep(img, [sign, faint])) == [faint.text]


def test_ogrenince_kenardaki_ve_baska_boydaki_satir_atilir():
    f = _learned_filter(W / 2)
    img = _zemin(seed=9)
    sub = _yaz(img, [("Come on, we still have work to do.", WHITE)], cx=W / 2)
    side = _yaz(img, [("Press E to enter", WHITE)], x=40, y=40, scale=0.9)  # tuş ipucu: bölgenin kenarında
    big = _yaz(img, [("Big Sale today", WHITE)], x=640, y=80, scale=2.2, thick=4)  # ortada ama dev boy
    assert _texts(f.keep(img, [side, big, sub])) == [sub.text]


def test_acik_zeminde_kenarsiz_beyaz_altyazi_gecer():
    """Proje bilinen durumu: açık zeminde kenarsız beyaz yazı. Kenar seçilmez, ortalık ve küçük harf yeter."""
    img = _zemin("beton", 2)
    sub = _yaz(img, [("I told you, the guy at the counter knows my face.", (252, 252, 252))], cx=W / 2,
               outline=False, shadow=False)
    img = _yayin(img, "jpeg")
    assert _texts(SubtitleFilter().keep(img, [sub])) == [sub.text]


def test_bolge_degisince_ogrendigini_unutur():
    f = _learned_filter(W / 2 + 0.12 * W)
    assert f.learned() is not None
    f.keep(np.zeros((H + 40, W, 3), np.uint8), [OcrLine("x", 0.9, 1, 1, 10, 10)])
    assert f.learned() is None


def test_sahte_kare_ve_bos_liste_oldugu_gibi():
    lines = [OcrLine("Hello there.", 0.9, 10, 10, 100, 20)]
    f = SubtitleFilter()
    assert f.keep(object(), lines) == lines  # testlerin sahte karesi: süzülmez
    assert f.keep(_zemin(), []) == []


# --- işlem hattı ve ayar ---------------------------------------------------------------------------------------------
@pytest.fixture
def pipe(tmp_path, monkeypatch):
    from altyazitr import pipeline as pl

    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")
    s = Settings()
    s.translate.engine = "quickmt"
    return pl.Pipeline(s)


@pytest.mark.parametrize("plate_text", ["LND 4821", "VICE CITY"])
def test_islem_hatti_ayar_acikken_plakayi_atar(pipe, plate_text):
    """Altyazının altındaki, ortadaki araç plakası: süzgeç açıkken atılır, altyazı okunur. Ayar kapalıyken eski
    davranış: plaka en alttaki öbek olduğu için altyazının yerine geçer (numara geçersiz metin: hiçbir şey okunmaz)."""
    img = _zemin(seed=4)
    sub = _yaz(img, [("Meet me at the diner after dark.", WHITE)], cx=W / 2, y=110)
    plate = _panel(img, plate_text, 535, 165, (235, 235, 235), (90, 40, 10), scale=0.8)
    engine = SimpleNamespace(recognize=lambda _img: [sub, plate])
    [(kind, _t, ev)] = pipe._ocr_item(engine, ("text", 1000.0, img))
    assert kind == "sub" and ev["text"] == sub.text
    pipe.s.ocr.subtitle_only = False
    pipe.tracker.on_gone(1000.5)
    out = pipe._ocr_item(engine, ("text", 1005.0, img))
    assert all(ev["text"] != sub.text for kind, _t, ev in out if kind == "sub")


def test_eski_ayar_dosyasinda_alan_yoksa_acik(tmp_path, monkeypatch):
    from altyazitr import config as cfg

    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    path.write_text(json.dumps({"ocr": {"engine": "rapidocr"}}), encoding="utf-8")
    assert cfg.load_settings().ocr.subtitle_only is True
    path.write_text(json.dumps({"ocr": {"subtitle_only": False}}), encoding="utf-8")
    assert cfg.load_settings().ocr.subtitle_only is False


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and (not isinstance(app, QApplication) or app.platformName() != "offscreen"):
        app.shutdown()
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # QApplication'dan hemen önce
        app = QApplication([])
    return app


def test_ayarlar_sayfasinda_onay_kutusu(qapp):
    """Metin okuma kartında "Yalnız altyazıları oku": değişince ayar kaydedilir, hat yeniden başlatılmaz (her okumada
    ayara bakılır)."""
    from PySide6.QtWidgets import QCheckBox

    from altyazitr.gui.settings_page import SettingsPage

    s = Settings()
    calls = {"save": 0, "restart": 0}

    def bump(key: str) -> None:
        calls[key] += 1

    ctx = SimpleNamespace(s=s, save_later=lambda: bump("save"), request_restart=lambda: bump("restart"),
                          display=SimpleNamespace(push_style=lambda: None), hotkey_errors=[], server_error="",
                          register_hotkeys=lambda: [], server=SimpleNamespace(url="http://127.0.0.1:1"))
    page = SettingsPage(SimpleNamespace(ctx=ctx))
    try:
        [box] = [w for w in page.findChildren(QCheckBox) if w.text() == "Yalnız altyazıları oku"]
        assert box.isChecked() and "Plaka, tabela, duvar yazısı" in box.toolTip()
        box.setChecked(False)
        assert s.ocr.subtitle_only is False and calls == {"save": 1, "restart": 0}
    finally:
        page.deleteLater()
