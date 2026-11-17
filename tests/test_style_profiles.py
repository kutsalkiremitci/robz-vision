"""Görünüm sayfasındaki "Ekranımda | Yayınımda" seçimi: her denetim yalnız seçili görünümü değiştirir,
seçim değişince hiçbir şey kaydedilmez, kopyalama ve "Varsayılana dön" yalnız seçili görünüme uygulanır
(ekran gerektirmez; Qt 'offscreen').

PySide6 yalnız fonksiyonların içinde içe aktarılır; 'offscreen' QApplication'dan hemen önce ayarlanır
(bkz. test_profiles.py): modül yüklenirken ayarlansaydı test_core'un çizim testini de etkilerdi."""

import os
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from altyazitr.config import Settings, StyleCfg


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


def _two_looks() -> Settings:
    """Ekranda küçük sarı yazı, yayında varsayılan; ikisinin yalnız-yayın alanları da farklı."""
    s = Settings()
    sc = s.screen_style
    sc.font_size, sc.text_color, sc.bg_opacity = 30, "#FFE14D", 0.0
    sc.bottom_percent, sc.mask_color = 20.0, "#2A2A2A"
    s.style.mask_original, s.style.bottom_percent = True, 12.0
    return s


def _page(qapp, s: Settings | None = None):
    from altyazitr.gui.style_page import StylePage

    s = s or _two_looks()
    calls = {"save": 0, "push": 0, "apply": 0, "toast": []}

    def bump(key):
        calls[key] += 1

    ctx = SimpleNamespace(
        s=s,
        save_later=lambda: bump("save"),
        display=SimpleNamespace(push_style=lambda: bump("push"), show_test=lambda: None),
        overlay=SimpleNamespace(apply_settings=lambda: bump("apply")),  # geometry yok: varsayılan kutu
    )
    win = SimpleNamespace(ctx=ctx, toast=lambda msg, kind="info": calls["toast"].append((msg, kind)))
    pg = StylePage(win)
    return pg, s, calls


def _snapshot(s: Settings):
    return asdict(s.style), asdict(s.screen_style), s.overlay.scale


def _rows_visible(pg):
    """Satırların sayfa açıldığında görünüp görünmeyeceği (katlanır bölüm açıkken)."""
    from altyazitr.gui.controls import Collapsible

    for c in pg.findChildren(Collapsible):
        c.set_expanded(True)
    names = ("_mask_row", "_mask_rows", "_bottom_row", "_maxw_row", "_scale_row", "_orig_row")
    return {n: getattr(pg, n).isVisibleTo(pg) for n in names}


def _touch_shared_controls(pg):
    """Her iki görünümde de bulunan denetimleri kullanıcı gibi değiştirir."""
    pg.cards[2].click()  # Kutulu
    pg.font.changed.emit("Arial")
    pg.weight.group.button(2).click()  # Çok kalın
    pg.size.slider.setValue(77)
    pg.color._buttons[2][1].click()  # Açık mavi
    pg.outline.group.button(3).click()  # Kalın
    pg.shadow.group.button(0).click()  # Kapalı
    pg.bg_color._buttons[2][1].click()  # Lacivert
    pg.speaker._buttons[3][1].click()  # Yeşil
    pg.fade.slider.setValue(400)
    pg.show_orig.group.button(1).click()  # Göster
    pg.orig_size.slider.setValue(70)


def _check_shared_changed(st: StyleCfg):
    assert st.font_family == "Arial"
    assert (st.font_weight, st.font_size, st.text_color) == (900, 77, "#8BE9FD")
    assert (st.outline_width, st.shadow, st.bg_color) == (5.0, False, "#0B1A3A")
    assert st.bg_opacity == pytest.approx(0.85)  # Kutulu
    assert (st.speaker_color, st.fade_ms, st.show_original, st.original_size_percent) == ("#6EE7A0", 400, True, 70)


# --- varsayılan seçim ----------------------------------------------------------------
def test_default_side_follows_screen_box(qapp):
    pg, s, _ = _page(qapp)
    assert pg.side_key == "screen" and pg.st is s.screen_style
    s2 = _two_looks()
    s2.overlay.enabled = False  # ekrandaki kutu kapalı: düzenlenecek tek görünüm yayındaki
    pg2, _, _ = _page(qapp, s2)
    assert pg2.side_key == "stream" and pg2.st is s2.style


# --- denetimler yalnız seçili görünümü değiştirir ---------------------------------------
def test_screen_side_edits_screen_style_only(qapp):
    pg, s, calls = _page(qapp)
    stream_before = asdict(s.style)
    _touch_shared_controls(pg)
    _check_shared_changed(s.screen_style)
    assert asdict(s.style) == stream_before
    assert calls["save"] > 0 and calls["push"] == calls["save"]
    # ölçek yalnız ekrandaki kutuyu etkiler: görünümlere dokunmaz
    screen_before = asdict(s.screen_style)
    pg.ov_scale.slider.setValue(15)
    assert s.overlay.scale == pytest.approx(1.5) and calls["apply"] == 1
    assert asdict(s.screen_style) == screen_before and asdict(s.style) == stream_before
    assert pg.preview.scale == pytest.approx(1.5)


def test_stream_side_edits_stream_style_only(qapp):
    pg, s, calls = _page(qapp)
    pg.side.group.button(1).click()
    assert pg.side_key == "stream" and pg.st is s.style
    screen_before = asdict(s.screen_style)
    _touch_shared_controls(pg)
    pg.bottom.slider.setValue(15)
    pg.maxw.slider.setValue(60)
    pg.mask.group.button(0).click()  # Kapalı
    pg.mask_color._buttons[2][1].click()
    pg.mask_alpha.slider.setValue(50)
    _check_shared_changed(s.style)
    assert (s.style.bottom_percent, s.style.max_width_percent) == (15.0, 60)
    assert (s.style.mask_original, s.style.mask_color, s.style.mask_opacity) == (False, "#0B1A3A", 0.5)
    assert asdict(s.screen_style) == screen_before
    assert calls["save"] > 0


# --- seçim değiştirmek hiçbir şeyi değiştirmez ------------------------------------------
def test_switching_sides_saves_nothing_and_reloads_widgets(qapp):
    pg, s, calls = _page(qapp)
    before = _snapshot(s)
    assert pg.size.slider.value() == 30 and pg.preview.st is s.screen_style
    assert pg.preview.box is not None and pg.preview.caption() == "Ekranınızdaki kutu"
    assert all(c.base is s.screen_style for c in pg.cards)
    assert pg.cards[3].matches(s.screen_style)  # Sarı: sarı yazı, arka plan yok

    pg.side.group.button(1).click()
    assert pg.size.slider.value() == s.style.font_size == 44
    assert pg.bg_mode.group.checkedId() == 1  # Yarı saydam
    assert pg.mask.group.checkedId() == 1  # yayında maske açık
    assert pg.preview.st is s.style and pg.preview.box is None
    assert pg.preview.caption() == "1080p yayın oranında önizleme"
    assert all(c.base is s.style for c in pg.cards)
    assert pg.cards[0].matches(s.style) and not pg.cards[3].matches(s.style)  # Klasik seçili

    pg.side.group.button(0).click()
    pg.side.group.button(0).click()  # zaten seçili olana tıklamak da bir şey yapmaz
    pg.set_side("stream")
    pg.set_side("screen")
    assert pg.size.slider.value() == 30
    assert _snapshot(s) == before
    assert calls == {"save": 0, "push": 0, "apply": 0, "toast": []}


# --- yalnız bir tarafta işe yarayan satırlar ---------------------------------------------
def test_stream_only_rows_hidden_on_screen_side(qapp):
    pg, s, _ = _page(qapp)
    vis = _rows_visible(pg)
    assert not vis["_mask_row"] and not vis["_mask_rows"]  # maske yalnız OBS'te
    assert not vis["_bottom_row"] and not vis["_maxw_row"]  # kutu konumu/genişliği Ana sayfadan
    assert vis["_scale_row"]
    pg.show_orig.group.button(1).click()
    assert _rows_visible(pg)["_orig_row"]  # ortak ayrıntı satırı ekranda da açılır

    pg.side.group.button(1).click()
    vis = _rows_visible(pg)
    assert vis["_mask_row"] and vis["_mask_rows"]  # yayında maske açık → renk/opaklık görünür
    assert vis["_bottom_row"] and vis["_maxw_row"] and not vis["_scale_row"]
    assert pg._pos_title.text() == "Konum ve genişlik"
    pg.mask.group.button(0).click()
    assert _rows_visible(pg)["_mask_row"] and not _rows_visible(pg)["_mask_rows"]
    pg.side.group.button(0).click()
    assert pg._pos_title.text() == "Ölçek"


# --- kopyalama ve varsayılana dönüş ------------------------------------------------------
def test_copy_other_side_copies_look_in_place(qapp):
    from altyazitr.gui.style_page import LOOK_FIELDS, STREAM_ONLY

    pg, s, calls = _page(qapp)
    screen_obj, stream_obj = s.screen_style, s.style
    stream_before = asdict(s.style)
    assert pg._copy_btn.isEnabled() and pg._copy_btn.text() == "Yayınımdakini buraya kopyala"
    pg._copy_btn.click()
    assert s.screen_style is screen_obj and s.style is stream_obj  # yerinde: başkaları bu nesneleri tutar
    assert all(getattr(s.screen_style, n) == getattr(s.style, n) for n in LOOK_FIELDS)
    assert (s.screen_style.bottom_percent, s.screen_style.mask_color) == (20.0, "#2A2A2A")  # yayına özgü: dokunulmaz
    assert asdict(s.style) == stream_before
    assert pg.size.slider.value() == 44  # denetimler kopyayı gösterir
    assert calls["save"] == 1 and calls["push"] == 1
    assert calls["toast"] == [("Yayındaki görünüm ekranınızdaki kutuya kopyalandı", "success")]
    assert not pg._copy_btn.isEnabled()  # iki görünüm artık aynı

    # ters yön: ekrandaki görünüm yayına; OBS konumu, genişliği ve maskesi değişmez
    pg.size.slider.setValue(28)
    pg.side.group.button(1).click()
    assert pg._copy_btn.isEnabled() and pg._copy_btn.text() == "Ekranımdakini buraya kopyala"
    pg._copy_btn.click()
    assert s.style.font_size == 28
    assert {n: getattr(s.style, n) for n in STREAM_ONLY} == {n: stream_before[n] for n in STREAM_ONLY}
    assert calls["toast"][-1] == ("Ekranınızdaki görünüm yayına kopyalandı", "success")


def test_reset_only_active_side(qapp):
    pg, s, calls = _page(qapp)
    pg.side.group.button(1).click()
    pg.size.slider.setValue(90)
    screen_before = asdict(s.screen_style)
    pg._reset_btn.click()
    assert asdict(s.style) == asdict(StyleCfg())
    assert asdict(s.screen_style) == screen_before
    assert pg.size.slider.value() == StyleCfg().font_size

    pg.side.group.button(0).click()
    stream_before = asdict(s.style)
    pg._reset_btn.click()
    assert s.screen_style.font_size == StyleCfg().font_size and asdict(s.style) == stream_before
