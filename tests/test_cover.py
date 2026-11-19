""""Oyun altyazısının üzerine" (StyleCfg.cover_original): çeviri oyunun İngilizce altyazısının tam yerine yazılır, opak
bir kutu İngilizceyi örter. Okumanın kutusu (textproc.subtitle_box, işlem hattı), yer ve yazı boyu hesabı (display),
Windows ölçeği ve çok ekran (overlay.to_logical), yazının sığdırılması (render.cover_size, OBS sayfası), gösterim
kuralları (yalnız en yeni satır, oyun kaldırınca kalkma, üst sınır, öbür görünümün etkilenmemesi), ekrandaki kutu
(kaydedilen yerin korunması), sunucu iletisi ve Görünüm sayfası.

Ekran gerektirmez: Qt 'offscreen' QApplication'dan hemen önce, fikstürün içinde ayarlanır (bkz. test_profiles); kutular
gösterilmez (yalnız render), oluşturulan pencereler testten sonra silinir."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from altyazitr.config import Settings, StyleCfg
from altyazitr.ocr import OcrLine
from altyazitr.paths import resource_dir
from altyazitr.server import OverlayServer, clean_cover, style_vars
from altyazitr.textproc import assemble, subtitle_box

REGION = [0.1, 0.7, 0.8, 0.2]  # pencere modunda bölge: 1920x1080'de 1536x216 piksel
BOX = {"rect": [0.25, 0.5, 0.5, 0.25], "rows": 1, "line_h": 0.25, "roi": [1536, 216]}
BOX2 = {"rect": [0.1, 0.3, 0.8, 0.5], "rows": 2, "line_h": 0.24, "roi": [1536, 216]}


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
    assert app.platformName() == "offscreen"
    return app


# --- okumanın kutusu ----------------------------------------------------------------------------------------------
def _ln(text, x, y, w, h, conf=0.95):
    return OcrLine(text, conf, x, y, w, h)


def test_kutu_assemble_ile_ayni_satirlari_kapsar():
    """Kutu, metne giren satırların (en alttaki öbek, konuşmacı adı dahil) birleşik dikdörtgenidir; üstteki ayrı öbek
    (tabela vb.) metne girmediği gibi kutuya da girmez."""
    lines = [_ln("VICE CITY MOTEL", 300, 10, 200, 20),  # üstte ayrı öbek
             _ln("LUCIA: Get in the car,", 200, 120, 400, 30),
             _ln("we're going to Vice City.", 180, 155, 440, 28)]
    text, speaker, _ = assemble(lines)
    assert speaker == "LUCIA" and text == "Get in the car, we're going to Vice City."
    box = subtitle_box(lines, (800, 200))
    assert box["rect"] == pytest.approx([180 / 800, 120 / 200, 440 / 800, 63 / 200], abs=1e-5)
    assert box["rows"] == 2 and box["roi"] == [800, 200]
    assert box["line_h"] == pytest.approx(29 / 200, abs=1e-5)  # satır yüksekliklerinin ortancası
    every = subtitle_box(lines, (800, 200), keep_lowest_block=False)
    assert every["rows"] == 3 and every["rect"][1] == pytest.approx(10 / 200)


def test_kutu_yeri_bilinmiyorsa_yok():
    assert subtitle_box([], (800, 200)) is None
    assert subtitle_box([_ln("Hello there", 0, 0, 0, 0)], (800, 200)) is None  # motor kutu vermedi
    assert subtitle_box([_ln("Hello there", 10, 10, 100, 20)], (0, 0)) is None
    clipped = subtitle_box([_ln("Hello there", -5, 190, 100, 20)], (800, 200))  # görüntüden taşan kutu kırpılır
    assert clipped["rect"][0] == 0 and clipped["rect"][1] + clipped["rect"][3] == pytest.approx(1.0)


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    from altyazitr import pipeline as pl

    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")
    s = Settings()
    s.ocr.subtitle_only = False
    s.style.highlight = s.screen_style.highlight = False
    return pl.Pipeline(s)


def test_islem_hatti_kutuyu_olaya_ve_arayuze_tasir(pipe):
    from altyazitr.capture import RegionCapturer

    img = np.zeros((216, 1536, 3), np.uint8)
    read = [_ln("Get in the car, we're going to Vice City.", 400, 120, 700, 40)]
    engine = SimpleNamespace(recognize=lambda _img: read)
    [(kind, _t, ev)] = pipe._ocr_item(engine, ("text", 1000.0, img))
    assert kind == "sub" and ev["box"]["rect"] == pytest.approx([400 / 1536, 120 / 216, 700 / 1536, 40 / 216],
                                                                abs=1e-5)
    assert ev["box"]["roi"] == [1536, 216] and ev["box"]["screen"] is None  # yakalama yok: ekrandaki yeri bilinmiyor
    out = pipe._event(ev, "Arabaya bin, Vice City'ye gidiyoruz.", time.perf_counter(), False, "sahte")
    assert out["box"] is ev["box"]

    pipe.capturer = RegionCapturer([192, 756, 1536, 216])  # ekran modu: bölgenin ekrandaki yeri okumayla gider
    pipe.tracker.on_gone(1000.5)
    [(_, _, ev)] = pipe._ocr_item(engine, ("text", 1005.0, img))
    assert ev["box"]["screen"] == [192, 756, 1536, 216]

    text_only = SimpleNamespace(recognize=lambda _img: [_ln("Hello there my friend", 0, 0, 0, 0)])
    pipe.tracker.on_gone(1005.5)
    [(_, _, ev)] = pipe._ocr_item(text_only, ("text", 1010.0, img))
    assert ev["box"] is None and "box" not in pipe._event(ev, "x", time.perf_counter(), False, "sahte")


def test_bolgenin_ekrandaki_yeri(monkeypatch):
    from altyazitr import capture

    assert capture.roi_screen_rect(capture.RegionCapturer([10, 20, 300, 100])) == [10, 20, 300, 100]
    assert capture.roi_screen_rect(None) is None
    monkeypatch.setattr(capture.winutil, "client_rect_screen", lambda hwnd: (100, 50, 100 + 1600, 50 + 900))
    wc = capture.WindowCapturer(1, (0.1, 0.7, 0.8, 0.2))
    assert capture.roi_screen_rect(wc) == [260, 680, 1280, 180]  # pencere taşınsa da o anki istemci alanından

    s = Settings()
    s.capture.mode, s.capture.rect = "screen", [5, 6, 700, 80]
    assert capture.region_screen_rect(s.capture) == [5, 6, 700, 80]
    s.capture.mode, s.capture.region = "window", [0.1, 0.7, 0.8, 0.2]
    monkeypatch.setattr(capture.winutil, "find_window", lambda exe, title: SimpleNamespace(hwnd=7))
    assert capture.region_screen_rect(s.capture) == [260, 680, 1280, 180]
    monkeypatch.setattr(capture.winutil, "find_window", lambda exe, title: None)
    assert capture.region_screen_rect(s.capture) is None  # pencere açık değil


# --- yer, yazı boyu, ölçek ----------------------------------------------------------------------------------------
def test_kutu_payi_ve_yazi_boyu():
    from altyazitr.display import COVER_PAD, cover_area, cover_fonts

    rect, rows, lh = cover_area(BOX)
    px = COVER_PAD[0] * 0.25 * 216 / 1536  # yatay pay piksel olarak dikeyinkiyle aynı oranda (bölgenin en/boy oranı)
    py = COVER_PAD[1] * 0.25
    assert rect == pytest.approx((0.25 - px, 0.5 - py, 0.5 + 2 * px, 0.25 + 2 * py)) and (rows, lh) == (1, 0.25)
    assert cover_area(None) == ((0.0, 0.0, 1.0, 1.0), 1, None)  # yeri bilinmiyor: bölgenin tamamı
    assert cover_area({"rect": "bozuk"}) == ((0.0, 0.0, 1.0, 1.0), 1, None)

    assert cover_fonts(30, 44, 16) == (30, 22.5)  # oyunun boyu; sığmazsa %75'e kadar
    assert cover_fonts(60, 44, 16) == (44, 33)  # görünümün boyunu geçmez
    assert cover_fonts(18, 44, 16) == (18, 16)  # okunur alt sınır
    assert cover_fonts(12, 44, 16) == (12, 12)  # oyunun yazısı zaten küçük: küçültülmez


def test_fiziksel_pikselden_mantiksala():
    from altyazitr.overlay import to_logical

    one = [((0, 0, 1920, 1080), 1.0)]
    assert to_logical([100, 900, 800, 60], one) == ((100, 900, 800, 60), (0, 0, 1920, 1080))
    # %125 ölçekli 1080p ana ekran + sağında %150 ölçekli 4K ekran (Qt: sol üst köşeler aynı, içi ölçekle küçük)
    two = [((0, 0, 1536, 864), 1.25), ((1920, 0, 2560, 1440), 1.5)]
    assert to_logical([250, 900, 1000, 50], two) == ((200, 720, 800, 40), (0, 0, 1536, 864))
    assert to_logical([1920 + 600, 1800, 1500, 90], two) == ((1920 + 400, 1200, 1000, 60), (1920, 0, 2560, 1440))
    # hiçbir ekranda değil (ör. ekran ayrıldı): en yakın ekranın ölçeği
    assert to_logical([-500, 10, 100, 10], two)[1] == (0, 0, 1536, 864)
    assert to_logical([0, 0, 10, 10], []) is None


def test_yazi_sigdirma_kurali():
    """Satır sayısına sığan en büyük boy; sığmazsa en küçük boydaki satır sayısıyla yazılabilen en büyük boy."""
    from altyazitr.render import cover_size

    def lines(chars: int, width: float):
        return lambda s: max(1, -(-chars * s * 0.5 // width))  # yazı genişliği ~ harf × boy / 2

    assert cover_size(lines(40, 800), 1, 30, 22.5) == 30  # kısa: oyunun boyunda
    assert cover_size(lines(60, 800), 1, 30, 22.5) == 26  # biraz uzun: tek satıra sığana dek küçülür
    assert cover_size(lines(120, 800), 1, 30, 22.5) == 26  # çok uzun: iki satır, iki satıra sığan en büyük boy
    assert cover_size(lines(120, 800), 2, 30, 22.5) == 26
    assert cover_size(lines(40, 800), 2, 30, 22.5) == 30  # oyunda iki satır, Türkçe tek satır: küçülmez
    assert lines(500, 800)(22.5) == lines(500, 800)(25) == 8  # çok uzun: en küçük boyda bile 8 satır …
    assert cover_size(lines(500, 800), 1, 30, 22.5) == 25  # … 8 satırla yazılabilen en büyük boy


# --- gösterim kuralları (SubtitleDisplay) ----------------------------------------------------------------------------
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Out:
    """Sunucu ve köprü yerine: OBS'e giden iletileri sırayla kaydeder."""

    def __init__(self):
        self.lines, self.masks, self.covers, self.styles = [], [], [], []

    def submit(self, coro):
        asyncio.run(coro)

    async def set_lines(self, lines):
        self.lines.append(lines)

    async def set_mask(self, rect):
        self.masks.append(rect)

    async def set_cover(self, cover):
        self.covers.append(cover)

    async def set_style(self, vars_):
        self.styles.append(vars_)


class _Box:
    """Ekrandaki kutunun yerine: set_lines / set_cover çağrılarını sırayla kaydeder."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def set_lines(self, lines):
        self.calls.append(("lines", [dict(x) for x in lines]))

    def set_cover(self, cover):
        self.calls.append(("cover", cover))

    def clear(self):
        self.calls.append(("lines", []))

    def update(self):
        pass

    def last(self, kind):
        return next((v for k, v in reversed(self.calls) if k == kind), None)


@pytest.fixture
def make_display(qapp):
    from altyazitr.display import SubtitleDisplay

    made = []

    def make(stream=False, screen=False, max_show=7.0, min_show=3.0):
        s = Settings()
        s.flow.max_show_s, s.flow.min_show_s = max_show, min_show
        s.ocr.hide_delay_ms = 700
        s.capture.mode, s.capture.region = "window", list(REGION)
        s.style.cover_original, s.screen_style.cover_original = stream, screen
        out, box = _Out(), _Box()
        d = SubtitleDisplay(s, out, out, box)
        d.clock = _Clock()
        made.append(d)
        return d, out, box

    try:
        yield make
    finally:
        for d in made:
            d._hide.stop()


def _ev(line, text, box=BOX, original=None):
    ev = {"line": line, "text": text, "speaker": None, "original": original or f"en {line}"}
    if box is not None:
        ev["box"] = box
    return ev


def _advance(d, sec):
    d.clock.t += sec
    if d._hide.isActive():
        d._tick()


def test_kapali_iken_hicbir_sey_degismez(make_display):
    d, out, box = make_display()
    d.on_subtitle(_ev(1, "Bir"))
    d.on_subtitle(_ev(2, "İki"))
    d.show_test()
    d.push_style()
    assert out.covers == [] and all(k == "lines" for k, _ in box.calls)  # kapak hiç gönderilmez
    assert [x["text"] for x in box.last("lines")][-1].startswith("Bu bir test")
    assert out.lines[-1] == box.last("lines")


def test_obs_kapagi_altyazinin_yerinde_yalniz_en_yeni_satir(make_display):
    from altyazitr.display import COVER_FONT, COVER_MIN_PX, COVER_PAD

    d, out, box = make_display(stream=True)
    d.on_subtitle(_ev(1, "Arabaya bin.", original="Get in the car."))
    c = out.covers[-1]
    px, py = COVER_PAD[0] * 0.25 * 216 / 1536, COVER_PAD[1] * 0.25
    rx, ry, rw, rh = REGION
    assert c["rect"] == pytest.approx([rx + (0.25 - px) * rw, ry + (0.5 - py) * rh, (0.5 + 2 * px) * rw,
                                       (0.25 + 2 * py) * rh], abs=1e-5)
    assert c["font"] == pytest.approx(COVER_FONT * 0.25 * rh, abs=1e-5)  # oyunun satırı boyunda (kareye oransal)
    assert c["min"] == pytest.approx(max(0.75 * c["font"], COVER_MIN_PX / 1080), abs=1e-5)
    assert c["rows"] == 1 and c["line"] == {"line": 1, "text": "Arabaya bin.", "speaker": None}  # orijinal gitmez
    assert out.lines[-1] == []  # bu görünüm akışı göstermez
    assert [x["text"] for x in box.last("lines")] == ["Arabaya bin."]  # ekrandaki kutu (kapak kapalı) eskisi gibi

    d.on_subtitle(_ev(2, "İki satırlık uzun bir altyazı.", BOX2))  # yeni satır: yeni yerine ve genişliğine
    c2 = out.covers[-1]
    assert c2["line"]["text"] == "İki satırlık uzun bir altyazı." and c2["rows"] == 2
    assert c2["rect"][2] > c["rect"][2] and c2["rect"][3] > c["rect"][3]
    assert [x["text"] for x in box.last("lines")] == ["Arabaya bin.", "İki satırlık uzun bir altyazı."]

    n = len(out.covers)
    d.on_subtitle(_ev(2, "İki satırlık uzun bir altyazı.", BOX2))  # aynı kapak: yeniden gönderilmez
    assert len(out.covers) == n


def test_kapak_oyun_altyaziyi_kaldirinca_kalkar(make_display):
    d, out, box = make_display(stream=True, min_show=3.0)
    d.on_subtitle(_ev(1, "Bir"))
    d.clock.t += 2.0
    d.on_gone()
    _advance(d, 0.5)
    assert out.covers[-1] is not None  # gizleme süresi (0,7 sn) dolmadı
    _advance(d, 0.25)
    assert out.covers[-1] is None  # oyun kaldırdı: kapak kalkar …
    assert [x["text"] for x in box.last("lines")] == ["Bir"]  # … ekrandaki akış satırı min_show kadar durur
    d.on_same()  # oyun aynı yazıyı yeniden gösteriyor (OCR bir an kaçırmıştı): İngilizce yine görünür, kapak geri
    assert out.covers[-1]["line"]["text"] == "Bir"
    d.on_subtitle(_ev(2, "İki"))
    assert out.covers[-1]["line"]["text"] == "İki"


def test_kapak_ust_sinirda_kalkar_ve_gorunurluk(make_display):
    d, out, _ = make_display(stream=True, max_show=7.0)
    d.on_subtitle(_ev(1, "Bir"))
    _advance(d, 6.9)
    assert out.covers[-1] is not None
    _advance(d, 0.2)
    assert out.covers[-1] is None
    d.on_gone()
    d.on_subtitle(_ev(2, "İki"))
    d.toggle_visible()
    assert out.covers[-1] is None
    d.toggle_visible()
    assert out.covers[-1]["line"]["text"] == "İki"
    d.clear()
    assert out.covers[-1] is None


def test_kapak_acikken_obs_maskesi_kullanilmaz(make_display):
    d, out, _ = make_display(stream=False)
    d.s.style.mask_original = True
    d.on_subtitle(_ev(1, "Bir"))
    assert out.masks[-1] is not None
    d.s.style.cover_original = True
    d.push_style()
    assert out.masks[-1] is None and out.covers[-1]["line"]["text"] == "Bir"


def test_yeri_bilinmeyen_satir_bolgenin_tamamini_ortur(make_display):
    d, out, _ = make_display(stream=True)
    d.on_subtitle(_ev(1, "Bir", box=None))
    c = out.covers[-1]
    assert c["rect"] == pytest.approx(REGION) and c["font"] == pytest.approx(44 / 1080, abs=1e-5)


def test_ekran_kapagi_mantiksal_pikselde_ve_akis_temizlenir(make_display, monkeypatch):
    """Ekrandaki kutu: bölgenin ekrandaki fiziksel yeri (okumayla gelir) %125 ölçekli ekranda mantıksala çevrilir;
    yazının üst sınırı kutunun normal boyudur. Yayın görünümü (kapak kapalı) akışı sürdürür."""
    import altyazitr.display as dmod
    from altyazitr.display import COVER_FONT, COVER_PAD
    from altyazitr.overlay import to_logical

    monkeypatch.setattr(dmod, "screen_area", lambda r: to_logical(r, [((0, 0, 1536, 864), 1.25)]))
    d, out, box = make_display(screen=True)
    d.s.overlay.enabled = True
    phys = [192, 756, 1536, 216]
    d.on_subtitle(_ev(1, "Bir", {**BOX, "screen": phys}))
    c = box.last("cover")
    lx, ly, lw, lh = (v / 1.25 for v in phys)
    px, py = COVER_PAD[0] * 0.25 * 216 / 1536, COVER_PAD[1] * 0.25
    assert c["rect"] == pytest.approx([lx + (0.25 - px) * lw, ly + (0.5 - py) * lh, (0.5 + 2 * px) * lw,
                                       (0.25 + 2 * py) * lh])
    assert c["bounds"] == [0, 0, 1536, 864]
    assert c["font"] == pytest.approx(COVER_FONT * 0.25 * lh)
    assert box.last("lines") is None  # akışta hiç satır gösterilmedi
    assert [x["text"] for x in out.lines[-1]] == ["Bir"]  # yayında akış

    d.s.screen_style.font_size = 20  # üst sınır: 20 × 864 / 1080 × ölçek
    d.push_style()
    assert box.last("cover")["font"] == pytest.approx(16.0)

    d.s.screen_style.cover_original = False  # kapatıldı: kapak kalkar, akış geri gelir
    d.push_style()
    assert box.calls[-2:] == [("cover", None), ("lines", box.calls[-1][1])]
    assert [x["text"] for x in box.last("lines")] == ["Bir"]
    d.s.screen_style.cover_original = True  # yeniden açıldı: akıştaki satırlar bir kez temizlenir, kapak gelir
    d.push_style()
    assert box.calls[-2] == ("lines", []) and box.calls[-1][0] == "cover"


def test_ornek_altyazi_kapakta(make_display, monkeypatch):
    import altyazitr.display as dmod

    d, out, box = make_display(stream=True, screen=True)
    d.s.overlay.enabled = True
    monkeypatch.setattr(dmod, "region_screen_rect", lambda c: None)  # oyun penceresi açık değil
    d.show_test()
    c = out.covers[-1]
    rx, ry, rw, rh = REGION
    assert c["rows"] == 1 and c["line"]["text"].startswith("Bu bir test") and "original" not in c["line"]
    assert c["font"] == pytest.approx(44 / 1080, abs=1e-5)  # görünümün boyunda
    assert rx <= c["rect"][0] and c["rect"][0] + c["rect"][2] <= rx + rw and ry <= c["rect"][1] <= ry + rh
    assert box.last("cover") is None and len(box.last("lines")) == 3  # ekranda yeri bilinmiyor: normal kutuda
    monkeypatch.setattr(dmod, "region_screen_rect", lambda c: [0, 0, 400, 100])
    d.show_test()
    assert box.last("cover")["line"]["text"].startswith("Bu bir test")
    _advance(d, 5.1)
    assert out.covers[-1] is None and box.last("cover") is None


# --- ekrandaki kutu (ScreenOverlay) ----------------------------------------------------------------------------------
@pytest.fixture
def overlay(qapp):
    from altyazitr.overlay import ScreenOverlay

    s = Settings()
    s.overlay.enabled = False  # pencere gösterilmez; çizim render() ile
    s.overlay.box = [50, 300, 600, 160]  # kullanıcının kaydettiği yer
    sc = s.screen_style
    sc.text_color, sc.mask_color, sc.shadow, sc.outline_width = "#FF0000", "#000000", False, 0.0
    ov = ScreenOverlay(s)
    ov._apply_win32 = lambda: None  # sahte pencerede Win32 çağrısı yok
    try:
        yield ov
    finally:
        ov._anim.stop()
        ov._shift.stop()
        ov.close()
        ov.deleteLater()


def _cover(text="Arabaya bin.", rect=(100, 500, 400, 40), rows=1, font=26.0, low=19.5):
    return {"rect": list(rect), "bounds": [0, 0, 800, 600], "rows": rows, "font": font, "min": low,
            "line": {"line": 1, "text": text, "speaker": None}}


def _settle(ov, opacity):
    ov._anim.stop()
    ov._opacity = opacity
    if opacity <= 0.001:
        ov._on_anim_done()


def _render(ov) -> np.ndarray:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    img = QImage(ov.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    ov.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def test_kutu_altyazinin_ustune_gider_ve_kaydedilen_yerine_doner(overlay):
    from PySide6.QtCore import QRect

    overlay.set_cover(_cover())
    g = overlay.geometry()
    assert g.contains(QRect(100, 500, 400, 40)) and g.bottom() == 539  # alt kenar oyunun altyazısıyla aynı
    assert overlay.s.overlay.box == [50, 300, 600, 160]  # kaydedilen yer değişmedi
    _settle(overlay, 1.0)
    px = _render(overlay)
    inner = px[4:-4, 4:-4]  # köşe yuvarlaklığı dışında her piksel tam opak: İngilizce arkadan görünmez
    assert inner[..., 3].min() == 255
    red = (px[..., 2].astype(int) - px[..., 1] > 120) & (px[..., 3] > 200)
    ys, xs = np.nonzero(red)
    assert red.sum() > 50 and abs((xs.min() + xs.max()) / 2 - g.width() / 2) < 6  # yazı yatayda ortalı
    assert abs((ys.min() + ys.max()) / 2 - g.height() / 2) < 8  # dikeyde ortalı

    overlay.save_geometry()  # kapakta kayıt yazılmaz
    assert overlay.s.overlay.box == [50, 300, 600, 160]
    overlay.set_cover(None)
    _settle(overlay, 0.0)
    assert overlay.geometry() == QRect(50, 300, 600, 160) and overlay._cover is None


def test_uzun_turkce_kutuyu_yukari_buyutur(overlay):
    short = overlay._place_cover(_cover())
    long = overlay._place_cover(_cover("Bu çeviri oyunun İngilizce altyazısından çok daha uzun, "
                                       "tek satıra hiç sığmıyor ve kaydırılması gerekiyor."))
    assert short[0].bottom() == long[0].bottom() == 539  # alt kenar sabit
    assert long[0].height() > short[0].height() and long[0].top() < 500  # yukarı doğru büyüdü
    assert long[1] < short[1]  # yazı da küçüldü (en az en küçük boy)
    assert long[1] >= 19.5
    edge = overlay._place_cover(_cover(rect=(700, 580, 300, 40)))  # ekrandan taşmaz
    assert edge[0].right() <= 800 and edge[0].bottom() <= 600


def test_ayar_modu_ve_akis_kapaktan_cikar(overlay):
    from PySide6.QtCore import QRect

    overlay._update_visibility = lambda: None  # ayar modunda pencere gösterilmez
    overlay.set_cover(_cover())
    overlay.set_editing(True)  # kullanıcının kendi kutusu düzenlenir
    assert overlay._cover is None and overlay.geometry() == QRect(50, 300, 600, 160)
    overlay.set_cover(_cover("Yeni satır"))  # ayar modunda gelen kapak bekler
    assert overlay._cover is None
    overlay.setGeometry(QRect(60, 310, 600, 160))
    overlay.set_editing(False)  # kayıt kullanıcının kutusu, sonra kapak yeniden uygulanır
    assert overlay.s.overlay.box == [60, 310, 600, 160]
    assert overlay._cover is not None and overlay._cover["line"]["text"] == "Yeni satır"
    overlay.set_lines([{"line": 2, "text": "Akış satırı", "speaker": None, "original": ""}])  # kapak kapatıldı
    assert overlay._cover is None and overlay.geometry() == QRect(60, 310, 600, 160)
    assert overlay.lines[-1]["text"] == "Akış satırı"


def test_kapak_ekran_bolgesi_okunurken_yakalamaya_girmez(overlay):
    from altyazitr.overlay import WDA_EXCLUDEFROMCAPTURE, WDA_NONE

    overlay.s.overlay.visible_in_stream = True
    overlay.s.capture.mode = "screen"
    assert overlay._affinity() == WDA_NONE
    overlay.set_cover(_cover())
    assert overlay._affinity() == WDA_EXCLUDEFROMCAPTURE  # OCR kendi kapağımızı okumasın
    overlay.s.capture.mode = "window"  # pencere yakalamada kutu zaten görüntüye girmez
    assert overlay._affinity() == WDA_NONE
    overlay.s.overlay.visible_in_stream = False
    assert overlay._affinity() == WDA_EXCLUDEFROMCAPTURE


# --- sunucu ve OBS sayfası -----------------------------------------------------------------------------------------
def test_sunucu_kapak_iletisi():
    server = OverlayServer(1)
    assert server.state["cover"] == {"type": "cover", "cover": None}
    good = {"rect": [0.3, 0.8, 0.4, 0.05], "rows": 1, "font": 0.026, "min": 0.02,
            "line": {"line": 4, "text": "Radar istasyonuna sür.", "speaker": None, "spans": [[0, 17], [5, 2]]}}
    asyncio.run(server.set_cover(good))
    c = server.state["cover"]["cover"]
    assert c["line"] == {"line": 4, "text": "Radar istasyonuna sür.", "speaker": None, "spans": [[0, 17]]}
    assert c["rect"] == [0.3, 0.8, 0.4, 0.05] and (c["rows"], c["font"], c["min"]) == (1, 0.026, 0.02)
    for bad in (None, {}, {**good, "rect": [0, 0, 0, 1]}, {**good, "font": float("nan")},
                {**good, "line": {"text": ""}}, {**good, "rect": "x"}):
        assert clean_cover(bad) is None
    assert style_vars(StyleCfg(mask_color="#0B1A3A", mask_opacity=0.5))["cover"] == "rgba(11,26,58,1.000)"  # hep opak


_COVER_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
class El {
  constructor(tag, cls) {
    this.tagName = tag; this.className = cls || ''; this.kids = []; this.parent = null; this.text = null;
    this.style = { setProperty(k, v) { this[k] = v; } };
    const self = this;
    const set = () => new Set(self.className.split(/\s+/).filter(Boolean));
    this.classList = {
      add(...c) { const s = set(); c.forEach((x) => s.add(x)); self.className = [...s].join(' '); },
      remove(...c) { const s = set(); c.forEach((x) => s.delete(x)); self.className = [...s].join(' '); },
      contains(c) { return set().has(c); },
    };
  }
  add(el) { el.parent = this; this.kids.push(el); return el; }
  append(...ns) {
    for (const n of ns) this.add(typeof n === 'string' ? Object.assign(new El('#text'), { text: n }) : n);
  }
  get textContent() { return this.text !== null ? this.text : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.text = null; if (v !== '') this.append(String(v)); }
  querySelector(sel) {
    for (const k of this.kids) {
      if (k.classList.contains(sel.slice(1))) return k;
      const r = k.querySelector(sel);
      if (r) return r;
    }
    return null;
  }
  // sahte yerleşim: harf genişliği yazı boyunun yarısı, satır yüksekliği 1,28 × boy
  getBoundingClientRect() {
    if (!this.classList.contains('tx')) return { top: 0, height: 0 };
    const size = parseFloat(this.parent.style.fontSize), width = parseFloat(this.style.width);
    const perLine = Math.max(1, Math.floor(width / (size * 0.5)));
    return { top: 0, height: Math.ceil(this.textContent.length / perLine) * size * 1.28 };
  }
}
const cover = new El('div');
const tx = cover.add(new El('div', 'tx'));
tx.add(new El('span', 'sp'));
tx.add(new El('span', 't'));
const els = { stack: new El('div'), mask: new El('div'), status: new El('div'), cover };
const sockets = [];
const win = {};
const ctx = {
  document: { getElementById: (id) => els[id], createElement: (t) => new El(t), documentElement: new El('html') },
  window: win, navigator: {}, location: { protocol: 'http:', host: '127.0.0.1:1', search: '' }, URLSearchParams,
  innerWidth: 1920, innerHeight: 1080,
  matchMedia: () => ({ matches: true }),
  getComputedStyle: () => ({ getPropertyValue: (k) => (k === '--size' ? '4.074vh' : '0'), transform: 'none' }),
  requestAnimationFrame: (f) => f(), setTimeout: () => 0, Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  WebSocket: class { constructor() { sockets.push(this); } },
};
vm.createContext(ctx);
vm.runInContext(script, ctx);
const ws = sockets[0];
ws.onopen();
const out = [];
for (const m of JSON.parse(process.argv[3])) {
  ws.onmessage({ data: JSON.stringify(m) });
  const style = Object.fromEntries(Object.entries(cover.style).filter(([, v]) => typeof v !== 'function'));
  const hl = tx.querySelector('.hl');
  out.push({ cls: cover.className, style, text: tx.textContent, hl: hl ? hl.textContent : null });
}
if (typeof win.onresize !== 'function') throw new Error('onresize yok');
process.stdout.write(JSON.stringify(out));
"""


def test_obs_sayfasi_kapagi_cizer(tmp_path):
    """Sayfa betiği sahte DOM'da: kapak altyazının yerine konur, yazı aynı kuralla sığdırılır (render.cover_size ile
    aynı sonuç), uzun Türkçede kutu alttan sabit yukarı büyür; null kapağı kaldırır."""
    from altyazitr.render import COVER_INNER, cover_size

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js yok: OBS sayfasının betiği çalıştırılamadı")
    rect = [0.3, 0.8, 0.4, 0.05]  # 768 × 54 piksel
    short = {"type": "cover", "cover": {"rect": rect, "rows": 1, "font": 0.026, "min": 0.0195,
                                        "line": {"text": "Phoenix'i Pay 'n' Spray'e götür", "speaker": None,
                                                 "spans": [[10, 25]]}}}
    long_text = "Bu çeviri oyunun İngilizce altyazısından çok daha uzun, tek satıra sığmıyor ve kaydırılıyor."
    long = {"type": "cover", "cover": {**short["cover"], "line": {"text": long_text, "speaker": "LUCIA"}}}
    harness = tmp_path / "kapak.js"
    harness.write_text(_COVER_HARNESS, encoding="utf-8")
    msgs = [short, long, {"type": "cover", "cover": None}]
    r = subprocess.run([node, str(harness), str(resource_dir() / "web" / "overlay.html"), json.dumps(msgs)],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    a, b, gone = json.loads(r.stdout)

    def expect(text):
        w = 0.4 * 1920

        def lines(s):
            per = max(1, int((w - 2 * COVER_INNER[0] * s) // (s * 0.5)))
            return -(-len(text) // per)
        s = cover_size(lines, 1, 0.026 * 1080, 0.0195 * 1080)
        return s, lines(s)

    s, n = expect(short["cover"]["line"]["text"])
    assert "on" in a["cls"].split() and a["hl"] == "Pay 'n' Spray'e"
    assert float(a["style"]["fontSize"][:-2]) == pytest.approx(s) and n == 1
    assert [float(a["style"][k][:-2]) for k in ("left", "top", "width", "height")] == pytest.approx(
        [576, 864, 768, 54])  # tek satır sığdı: kutu tam altyazının yerinde

    s, n = expect("LUCIA: " + long_text)
    assert float(b["style"]["fontSize"][:-2]) == pytest.approx(s) and n == 2 and b["text"].startswith("LUCIA: ")
    top, height = float(b["style"]["top"][:-2]), float(b["style"]["height"][:-2])
    assert top + height == pytest.approx(864 + 54) and height == pytest.approx(n * s * 1.28 + 2 * COVER_INNER[1] * s)
    assert "on" not in gone["cls"].split()


def test_sayfa_eski_iletileri_bozmaz(tmp_path):
    """Kapak alanı eklendi; 'lines' ve 'mask' iletileri eskisi gibi, sunucu durumunda sıra korunur."""
    server = OverlayServer(1)
    assert list(server.state) == ["style", "sub", "mask", "lines", "cover"]
    page = (resource_dir() / "web" / "overlay.html").read_text(encoding="utf-8")
    assert "m.type === 'lines'" in page and "m.type === 'cover'" in page and "id=\"cover\"" in page


# --- ayar ve Görünüm sayfası ------------------------------------------------------------------------------------------
def test_ayar_varsayilan_kapali_ve_eski_dosya(tmp_path, monkeypatch):
    from altyazitr import config as cfg

    assert StyleCfg().cover_original is False
    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    path.write_text(json.dumps({"style": {"font_size": 50}, "screen_style": {"cover_original": True}}),
                    encoding="utf-8")
    s = cfg.load_settings()
    assert s.style.cover_original is False and s.screen_style.cover_original is True


_PAGES: list = []


@pytest.fixture
def page(qapp):
    from PySide6.QtCore import QCoreApplication, QEvent

    from altyazitr.gui.style_page import StylePage

    def make():
        s = Settings()
        s.style.mask_original = True
        calls = {"save": 0, "push": 0}
        ctx = SimpleNamespace(
            s=s, save_later=lambda: calls.__setitem__("save", calls["save"] + 1),
            display=SimpleNamespace(push_style=lambda: calls.__setitem__("push", calls["push"] + 1),
                                    show_test=lambda: None),
            overlay=SimpleNamespace(apply_settings=lambda: None))
        pg = StylePage(SimpleNamespace(ctx=ctx, toast=lambda *a, **k: None))
        _PAGES.append(pg)
        return pg, s, calls

    try:
        yield make
    finally:
        for pg in _PAGES:
            pg.close()
            pg.deleteLater()
        _PAGES.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _vis(pg, name):
    from altyazitr.gui.controls import Collapsible

    for c in pg.findChildren(Collapsible):
        c.set_expanded(True)
    return getattr(pg, name).isVisibleTo(pg)


def test_gorunum_sayfasinda_iki_gorunumde_ayri(page):
    pg, s, calls = page()
    assert pg.side_key == "screen" and pg.cover.toolTip().startswith("Oyunun İngilizce altyazısını koyu")
    assert not _vis(pg, "_cover_color_row") and not _vis(pg, "_cover_hint")
    pg.cover.group.button(1).click()  # Ekranımda: açık
    assert s.screen_style.cover_original is True and s.style.cover_original is False
    assert calls["save"] == calls["push"] == 1
    assert _vis(pg, "_cover_color_row") and "Konumu ayarla" in pg._cover_hint.text()
    pg.cover_color._buttons[2][1].click()  # kutu rengi = maske rengi
    assert s.screen_style.mask_color == "#0B1A3A" and s.style.mask_color == StyleCfg().mask_color

    pg.side.group.button(1).click()  # Yayınımda: kapalı; konum ve maske denetimleri etkin
    assert pg.cover.group.checkedId() == 0 and pg._bottom_row.isEnabled() and pg._mask_row.isEnabled()
    assert _vis(pg, "_mask_rows")
    pg.cover.group.button(1).click()
    assert s.style.cover_original is True
    assert not pg._bottom_row.isEnabled() and not pg._maxw_row.isEnabled() and not pg._mask_row.isEnabled()
    assert not _vis(pg, "_mask_rows") and "OBS" in pg._cover_hint.text()
    pg.mask_color._buttons[1][1].click()  # maske rengi seçicisi de aynı alanı gösterir
    assert s.style.mask_color == "#2A2A2A" and pg.cover_color._color == "#2A2A2A"
    assert pg.cover_color._buttons[1][1].isChecked()

    before = asdict(s.screen_style)
    pg._copy_btn.click()  # kopyalama yerleşimi taşımaz
    assert s.style.cover_original is True and s.screen_style.cover_original == before["cover_original"]
