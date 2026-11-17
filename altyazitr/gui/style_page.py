"""Görünüm sayfası: üstte sabit bantta "Ekranımda | Yayınımda" seçimi (sayfanın hangi görünümü düzenlediği);
altında canlı önizleme, hazır stiller ve sade bölümler (yazı, kenar ve gölge, arka plan, konum/ölçek,
gelişmiş). Geniş pencerede önizleme sağ sütunda sabit kalır, ayarlar solda kayar; böylece aşağıdaki bir ayarı
değiştirirken sonucu hep görürsünüz.

İki görünüm birbirinden bağımsızdır: Ekranımda = Settings.screen_style (oyunun üstündeki kutu),
Yayınımda = Settings.style (OBS tarayıcı kaynağı ve OBS maskesi). Sayfadaki her denetim seçili olanı okur
ve yazar; seçim değişince denetimler sinyal göndermeden yeniden doldurulur, hiçbir şey kaydedilmez."""

from __future__ import annotations

import dataclasses
import math

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from altyazitr.config import StyleCfg
from altyazitr.gui import icons
from altyazitr.gui.controls import Collapsible, FontPicker, PresetCard, Segmented, SliderRow, SubtitlePreview, SwatchRow
from altyazitr.gui.tokens import C, ICON, RADIUS, SPACE
from altyazitr.gui.widgets import Card, button_icon, label, page, row

# --- iki görünüm ---------------------------------------------------------------------
SCREEN, STREAM = "screen", "stream"
# Yalnız yayında işe yarayan alanlar. Ekrandaki kutu bunları kullanmaz: render.paint_subtitle(in_box=True)
# konumu ve genişliği kutudan alır; maske yalnız OBS'te çizilir. Kopyalama bunlara dokunmaz.
STREAM_ONLY = frozenset({"bottom_percent", "max_width_percent", "mask_original", "mask_color", "mask_opacity",
                         "mask_pad_percent"})
LOOK_FIELDS = tuple(f.name for f in dataclasses.fields(StyleCfg) if f.name not in STREAM_ONLY)
DEFAULT_BOX = (0.66, 0.17)  # ekrandaki kutunun ekrana oranı bilinmiyorsa (overlay.default_box ile aynı)

SIDE_TEXT = {
    SCREEN: dict(
        hint="Oyunun üstünde yalnız sizin gördüğünüz kutu. Yayındaki altyazı ayrı ayarlanır.",
        copy="Yayınımdakini buraya kopyala",
        copy_tip="Yayındaki yazı tipini, boyutu, renkleri, kenarı ve arka planı ekranınızdaki kutuya da uygular",
        copied="Yayındaki görünüm ekranınızdaki kutuya kopyalandı",
        reset_tip="Ekranınızdaki kutunun görünümünü ilk hâline döndürür; yayın değişmez",
        size_tip="1080p ekrana göre piksel cinsinden yazı boyutu; ekranınız büyükse oranla büyür",
        pos_title="Ölçek",
    ),
    STREAM: dict(
        hint="İzleyicilerin OBS'te gördüğü altyazı. Ekranınızdaki kutu ayrı ayarlanır.",
        copy="Ekranımdakini buraya kopyala",
        copy_tip="Ekranınızdaki kutunun yazı tipini, boyutunu, renklerini, kenarını ve arka planını yayına da "
                 "uygular. OBS'teki konum, genişlik ve maske değişmez.",
        copied="Ekranınızdaki görünüm yayına kopyalandı",
        reset_tip="Yayındaki altyazının görünümünü ilk hâline döndürür; ekranınızdaki kutu değişmez",
        size_tip="1080p yayına göre piksel cinsinden yazı boyutu",
        pos_title="Konum ve genişlik",
    ),
}
SAME_TIP = "İki görünüm şu an aynı"

PRESETS: list[tuple[str, dict]] = [
    ("Klasik", dict(text_color="#FFFFFF", outline_color="#000000", outline_width=3.0, shadow=True,
                    bg_color="#000000", bg_opacity=0.45, font_weight=700)),
    ("Sinema", dict(text_color="#FFFFFF", outline_color="#000000", outline_width=3.0, shadow=True,
                    bg_opacity=0.0, font_weight=700)),
    ("Kutulu", dict(text_color="#FFFFFF", outline_width=0.0, shadow=False, bg_color="#000000",
                    bg_opacity=0.85, font_weight=700)),
    ("Sarı", dict(text_color="#FFE14D", outline_color="#000000", outline_width=3.0, shadow=True,
                  bg_opacity=0.0, font_weight=700)),
    ("Minimal", dict(text_color="#FFFFFF", outline_width=0.0, shadow=True, bg_color="#000000",
                     bg_opacity=0.30, font_weight=400)),
]

# Kullanıcının seçebileceği altyazı renkleri (veri; arayüz renkleri değil).
TEXT_COLORS = [("#FFFFFF", "Beyaz"), ("#FFE14D", "Sarı"), ("#8BE9FD", "Açık mavi"), ("#A3F7A1", "Açık yeşil"),
               ("#FFB347", "Turuncu"), ("#FF9ECF", "Pembe")]
OUTLINE_COLORS = [("#000000", "Siyah"), ("#333333", "Koyu gri"), ("#0B1A3A", "Lacivert"), ("#FFFFFF", "Beyaz")]
BG_COLORS = [("#000000", "Siyah"), ("#1E1E1E", "Koyu gri"), ("#0B1A3A", "Lacivert"), ("#0D2A1A", "Koyu yeşil"),
             ("#3A0B12", "Bordo")]
SPEAKER_COLORS = [("#FFD54A", "Sarı"), ("#FFB347", "Turuncu"), ("#8BE9FD", "Açık mavi"), ("#6EE7A0", "Yeşil"),
                  ("#FF9ECF", "Pembe"), ("#FFFFFF", "Beyaz")]
MASK_COLORS = [("#101010", "Siyah"), ("#2A2A2A", "Koyu gri"), ("#0B1A3A", "Lacivert")]

# --- düzen ölçüleri ----------------------------------------------------------------
WIDE_MIN = 1080            # sayfa bu genişlikten itibaren iki sütun: ayarlar solda, önizleme sağda sabit
LEFT_MIN, LEFT_MAX = 560, 700   # iki sütunda ayar sütununun genişliği
SIDE_MAX = 920             # önizleme sütunu en fazla bu kadar genişler; artan boşluk sağda kalır
CONTROL_MAX = 440          # kaydırıcılı satırlar bu genişlikte durur (çok geniş pencerede uzamaz)
SLIDER_MIN = 120           # 780 px pencerede bile yatay taşma olmasın
PREVIEW_MIN_H = 160
_QMAX = 16777215           # QWIDGETSIZE_MAX


class _Field(QWidget):
    """Bir ayar satırı: soldaki etiket sütunu + kontrol. Etiket genişliği sayfa genelinde eşitlenir."""

    def __init__(self, text: str, control: QWidget, stretch: bool = False, tip: str = "", indent: int = 0):
        super().__init__()
        self.indent = indent  # katlanır bölümün girintisi: kontrol sütunu yine hizalı kalsın
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["m"])
        self.label = QLabel(text)
        self.label.setObjectName("field")
        lay.addWidget(self.label)
        if stretch:
            control.setMaximumWidth(CONTROL_MAX)
            lay.addWidget(control, 100)  # önce kontrol büyür, en fazla CONTROL_MAX
        else:
            lay.addWidget(control)
        lay.addStretch(1)
        target = getattr(control, "slider", control)
        if not target.accessibleName():
            target.setAccessibleName(text)
        self.control = control
        tip = tip or control.toolTip()
        if tip:
            self.label.setToolTip(tip)
            if not control.toolTip():
                control.setToolTip(tip)

    def set_tip(self, tip: str) -> None:
        self.label.setToolTip(tip)
        self.control.setToolTip(tip)


class _PresetGrid(QWidget):
    """Hazır stil kartlarını genişliğe göre dengeli satırlara dizer; dar pencerede yana taşmaz."""

    def __init__(self, cards: list[PresetCard], gap: int):
        super().__init__()
        self._cards = cards
        self._gap = gap
        self._cols = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(gap)
        self._grid.setVerticalSpacing(gap)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Genişliği üst düzen belirler (kartlar asgari genişliği zorlamaz); yükseklik satır sayısından gelir.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._reflow(len(cards))

    def _card_w(self) -> int:
        c = self._cards[0]
        return max(c.minimumWidth(), c.sizeHint().width(), 1)

    def _reflow(self, fit: int) -> None:
        n = len(self._cards)
        fit = max(1, min(n, fit))
        rows = math.ceil(n / fit)
        cols = math.ceil(n / rows)  # dengeli: 5 kart, 4 sığıyorsa 3 + 2
        if cols == self._cols:
            return
        self._cols = cols
        for c in self._cards:
            self._grid.removeWidget(c)
        for i, c in enumerate(self._cards):
            self._grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._reflow((self.width() + self._gap) // (self._card_w() + self._gap))


class StylePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = win.ctx
        ov = self.ctx.s.overlay
        # Varsayılan: Ekranımda. Herkes kendi ekranındaki kutuyu görür, değişikliğin sonucu hemen önündedir;
        # yalnız OBS kullanan (kutuyu kapatmış) yayıncı için düzenlenecek tek görünüm yayındakidir.
        self._prof = SCREEN if getattr(ov, "enabled", True) else STREAM
        self.st: StyleCfg = self._style(self._prof)
        self._loading = False  # sync() denetimleri doldururken gelen değişiklikler ayara yazılmaz
        sa, lay = page(None)
        self._sa, self._lay = sa, lay
        # Başlık ve görünüm seçimi kaydırılmayan üst bantta: aşağıdaki bir ayarı değiştirirken de hangi
        # görünümün düzenlendiği görünür. Bant + içerik boşluğu, diğer sayfalardaki başlık boşluğuyla aynı.
        m = lay.contentsMargins()
        head_gap = SPACE["l"]
        lay.setContentsMargins(m.left(), lay.spacing() - head_gap, m.right(), m.bottom())
        self._header = self._build_header(m.left(), m.top(), m.right(), head_gap)
        self._stage_index = lay.count()  # dar düzende önizleme en üste girer
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header)
        root.addLayout(outer, 1)
        self._fields: list[_Field] = []
        self._wide = False
        self._compact: bool | None = None
        self._reset_full_w: int | None = None
        self._label_w = 140

        # --- önizleme sahnesi: önizleme + sahne seçimi + eylemler ----------------------
        self._stage = QFrame()
        self._stage.setObjectName("stage")
        self._stage.setStyleSheet(f"QFrame#stage {{ background: {C.PANEL}; border: 1px solid {C.LINE}; "
                                  f"border-radius: {RADIUS['l']}px; }}")
        stl = QVBoxLayout(self._stage)
        stl.setContentsMargins(SPACE["s"], SPACE["s"], SPACE["s"], SPACE["s"])
        stl.setSpacing(SPACE["s"])
        self.preview = SubtitlePreview(self.st)
        stl.addWidget(self.preview)
        self.scene = Segmented([("Aydınlık", False), ("Karanlık", True)], icons=["sun", "moon"])
        self.scene.group.button(0).setToolTip("Önizlemeyi gündüz sahnesinde göster")
        self.scene.group.button(1).setToolTip("Önizlemeyi gece sahnesinde göster")
        self.scene.group.button(0).setAccessibleName("Aydınlık sahne")
        self.scene.group.button(1).setAccessibleName("Karanlık sahne")
        self.scene.setValue(False)
        self.scene.changed.connect(self.preview.set_dark)
        self._reset_btn = QPushButton("Varsayılana dön")
        self._reset_btn.setObjectName("ghost")
        self._reset_btn.setIcon(icons.icon("reset", C.TEXT_2))
        self._reset_btn.setIconSize(QSize(ICON["s"], ICON["s"]))
        self._reset_btn.setToolTip("Tüm görünüm ayarlarını ilk hâline döndürür")
        self._reset_btn.setAccessibleName("Varsayılana dön")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.clicked.connect(self._reset)
        self._test_btn = QPushButton("Test et")
        self._test_btn.setObjectName("primary")
        self._test_btn.setIcon(icons.icon("captions", C.INVERSE))
        self._test_btn.setIconSize(QSize(ICON["s"], ICON["s"]))
        self._test_btn.setToolTip("Ekranınızda ve OBS'te 5 saniyelik örnek altyazı gösterir")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(self.ctx.display.show_test)
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["s"])
        bar.addWidget(self.scene)
        bar.addStretch(1)
        bar.addWidget(self._reset_btn)
        bar.addWidget(self._test_btn)
        stl.addLayout(bar)
        self._preview_max_h = self.preview.maximumHeight()
        self.preview.installEventFilter(self)  # sabit yükseklikli önizleme: dar alanda 16:9'a göre kısalt

        # --- hazır stiller ------------------------------------------------------------------
        self.cards: list[PresetCard] = []
        for name, vals in PRESETS:
            card = PresetCard(name, vals, self.st)
            card.clicked.connect(lambda _=False, v=vals: self._apply(v))
            self.cards.append(card)
        prc = Card("Hazır stiller")
        prc.add(_PresetGrid(self.cards, SPACE["m"]))
        prc.setToolTip("Bir stile tıklayın; sonra yazı tipi, boyut ve renkleri dilediğiniz gibi değiştirin")

        # Önizleme ile hazır stiller birlikte taşınır: dar pencerede en üstte, geniş pencerede sağ sütunda.
        self.stage_group = QWidget()
        sg = QVBoxLayout(self.stage_group)
        sg.setContentsMargins(0, 0, 0, 0)
        sg.setSpacing(lay.spacing())
        sg.addWidget(self._stage)
        sg.addWidget(prc)
        lay.addWidget(self.stage_group)

        # --- yazı ----------------------------------------------------------------------------
        tc = Card("Yazı")
        self.font = FontPicker()
        self.font.changed.connect(lambda f: self._set("font_family", f))
        self.weight = Segmented([("Normal", 400), ("Kalın", 700), ("Çok kalın", 900)])
        self.weight.changed.connect(lambda v: self._set("font_weight", v))
        self.size = SliderRow(16, 110, 1, "{:.0f} px", minus="A−", plus="A+")
        self.size.changed.connect(lambda v: self._set("font_size", int(v)))
        self.color = SwatchRow(TEXT_COLORS)
        self.color.changed.connect(lambda c: self._set("text_color", c))
        self.font.setToolTip("Listede olmayan bir yazı tipi için listenin en altındaki seçeneği kullanın")
        tc.add(self._field("Yazı tipi", self.font))
        tc.add(self._field("Kalınlık", self.weight))
        self._size_row = self._field("Boyut", self.size, stretch=True, tip=SIDE_TEXT[STREAM]["size_tip"])
        tc.add(self._size_row)
        tc.add(self._field("Renk", self.color))
        lay.addWidget(tc)

        # --- kenar ve gölge -------------------------------------------------------------------
        oc = Card("Kenar ve gölge")
        self.outline = Segmented([("Yok", 0.0), ("İnce", 2.0), ("Orta", 3.0), ("Kalın", 5.0)])
        self.outline.changed.connect(lambda v: self._set("outline_width", float(v)))
        self.outline_color = SwatchRow(OUTLINE_COLORS)
        self.outline_color.changed.connect(lambda c: self._set("outline_color", c))
        self.shadow = Segmented([("Kapalı", False), ("Açık", True)])
        self.shadow.changed.connect(lambda v: self._set("shadow", bool(v)))
        oc.add(self._field("Kenar", self.outline, tip="Harflerin çevresindeki çizgi; açık sahnelerde okumayı "
                                                      "kolaylaştırır"))
        self.outline_color_row = self._field("Kenar rengi", self.outline_color)
        oc.add(self.outline_color_row)
        oc.add(self._field("Gölge", self.shadow))
        lay.addWidget(oc)

        # --- arka plan -------------------------------------------------------------------------
        bc = Card("Arka plan")
        self.bg_mode = Segmented([("Yok", 0.0), ("Yarı saydam", 0.45), ("Opak", 0.9)])
        self.bg_mode.changed.connect(lambda v: self._set("bg_opacity", float(v)))
        self.bg_color = SwatchRow(BG_COLORS)
        self.bg_color.changed.connect(lambda c: self._set("bg_color", c))
        self.bg_alpha = SliderRow(0.05, 1.0, 0.05, "{:.0%}", scale=100)
        self.bg_alpha.changed.connect(lambda v: self._set("bg_opacity", round(v, 2)))
        bc.add(self._field("Kutu", self.bg_mode, tip="Yazının arkasındaki kutu"))
        self.bg_details = QWidget()
        bd = QVBoxLayout(self.bg_details)
        bd.setContentsMargins(0, 0, 0, 0)
        bd.setSpacing(bc.lay.spacing())
        bd.addWidget(self._field("Kutu rengi", self.bg_color))
        bd.addWidget(self._field("Opaklık", self.bg_alpha, stretch=True, tip="Kutunun arkayı ne kadar kapattığı"))
        bc.add(self.bg_details)
        lay.addWidget(bc)

        # --- konum ve genişlik (Yayınımda) / ölçek (Ekranımda) -----------------------------------
        pos = Card(SIDE_TEXT[STREAM]["pos_title"])
        self._pos_title: QLabel = pos.lay.itemAt(0).widget()
        self.bottom = SliderRow(0, 45, 0.5, "%{:.0f}")
        self.bottom.changed.connect(lambda v: self._set("bottom_percent", round(v, 1)))
        self.bottom.setToolTip("OBS'teki altyazının alt kenara uzaklığı (önizlemedeki konum)")
        self.maxw = SliderRow(30, 100, 1, "%{:.0f}")
        self.maxw.changed.connect(lambda v: self._set("max_width_percent", round(v)))
        self.maxw.setToolTip("OBS'teki altyazının en fazla genişliği; daha uzun satırlar alta kayar")
        self.ov_scale = SliderRow(0.5, 2.0, 0.1, "{:.1f}x", scale=10, minus="−", plus="+")
        self.ov_scale.setValue(ov.scale)
        self.ov_scale.changed.connect(self._set_scale)
        self.ov_scale.setToolTip("Kutudaki altyazıyı yazı, kenar ve arka planıyla birlikte büyütür ya da "
                                 "küçültür; yayını etkilemez. Kutunun yerini Ana sayfadaki 'Konumu ayarla' ile "
                                 "değiştirin.")
        self._bottom_row = self._field("OBS'te alttan", self.bottom, stretch=True)
        self._maxw_row = self._field("OBS'te genişlik", self.maxw, stretch=True)
        self._scale_row = self._field("Yazı ölçeği", self.ov_scale, stretch=True)
        pos.add(self._bottom_row)
        pos.add(self._maxw_row)
        pos.add(self._scale_row)
        lay.addWidget(pos)

        # --- gelişmiş ------------------------------------------------------------------------------
        self.speaker = SwatchRow(SPEAKER_COLORS)
        self.speaker.changed.connect(lambda c: self._set("speaker_color", c))
        self.fade = SliderRow(0, 800, 20, "{:.0f} ms")
        self.fade.changed.connect(lambda v: self._set("fade_ms", int(v)))
        self.show_orig = Segmented([("Gizle", False), ("Göster", True)])
        self.show_orig.changed.connect(lambda v: self._set("show_original", bool(v)))
        self.orig_size = SliderRow(30, 100, 5, "%{:.0f}")
        self.orig_size.changed.connect(lambda v: self._set("original_size_percent", int(v)))
        self.mask = Segmented([("Kapalı", False), ("Açık", True)])
        self.mask.changed.connect(lambda v: self._set("mask_original", bool(v)))
        self.mask.setToolTip("OBS'te, seçtiğiniz bölgenin üstüne kutu koyarak İngilizce altyazıyı gizler. "
                             "Oyun görüntüsü OBS tuvalini tam kaplıyorsa (Ctrl+F) birebir oturur.")
        self.mask_color = SwatchRow(MASK_COLORS)
        self.mask_color.changed.connect(lambda c: self._set("mask_color", c))
        self.mask_alpha = SliderRow(0.1, 1.0, 0.05, "{:.0%}", scale=100)
        self.mask_alpha.changed.connect(lambda v: self._set("mask_opacity", round(v, 2)))

        adv = Collapsible("Gelişmiş ayarlar")
        ind = adv.body_lay.contentsMargins().left()
        adv.add(self._field("Konuşmacı rengi", self.speaker, tip="Konuşanın adı (ör. LUCIA) bu renkte yazılır",
                             indent=ind))
        adv.add(self._field("Geçiş süresi", self.fade, stretch=True, tip="Altyazının belirme ve kaybolma süresi",
                             indent=ind))
        adv.add(self._field("İngilizce orijinal", self.show_orig,
                             tip="Çevirinin altında İngilizce metni de gösterir", indent=ind))
        self._orig_row = self._field("Orijinal boyutu", self.orig_size, stretch=True,
                                     tip="İngilizce metnin, çeviriye göre boyutu", indent=ind)
        adv.add(self._orig_row)
        self._mask_row = self._field("OBS'te orijinali kapat", self.mask, indent=ind)
        adv.add(self._mask_row)
        self._mask_rows = QWidget()
        mr = QVBoxLayout(self._mask_rows)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(adv.body_lay.spacing())
        mr.addWidget(self._field("Maske rengi", self.mask_color, indent=ind))
        mr.addWidget(self._field("Maske opaklığı", self.mask_alpha, stretch=True, indent=ind))
        adv.add(self._mask_rows)
        advc = Card()
        advc.add(adv)
        lay.addWidget(advc)
        lay.addStretch(1)

        # --- geniş düzen: sağda sabit önizleme sütunu (başlangıçta gizli) ------------------------------
        self._side = QScrollArea()
        self._side.setWidgetResizable(True)
        self._side.setFrameShape(QFrame.Shape.NoFrame)
        self._side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._side.setMaximumWidth(SIDE_MAX)
        side_inner = QWidget()
        side_inner.setObjectName("page")
        self._side_lay = QVBoxLayout(side_inner)
        m = lay.contentsMargins()
        self._side_lay.setContentsMargins(SPACE["s"], m.top(), m.right(), m.bottom())
        self._side_lay.setSpacing(0)
        self._side_lay.addStretch(1)
        self._side.setWidget(side_inner)
        self._side.hide()
        outer.addWidget(self._side, 1)

        sig = getattr(getattr(self.ctx, "overlay", None), "geometry_saved", None)
        if sig is not None:  # kutu Ana sayfada taşınıp boyutlandırılınca ekran önizlemesi de uysun
            sig.connect(self._refresh_preview)
        self._equalize_labels()
        self._load_side()

    # --- yapı yardımcıları --------------------------------------------------------------------
    def _build_header(self, left: int, top: int, right: int, bottom: int) -> QWidget:
        """Sabit üst bant: sayfa başlığı, Ekranımda | Yayınımda seçimi, kopyalama eylemi, tek satır açıklama."""
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(left, top, right, bottom)
        hl.setSpacing(0)
        hl.addWidget(label("Görünüm", "pageTitle"))
        hl.addSpacing(SPACE["m"])

        self.side = Segmented([("Ekranımda", SCREEN), ("Yayınımda", STREAM)], icons=["monitor", "broadcast"])
        b0, b1 = self.side.group.button(0), self.side.group.button(1)
        b0.setToolTip("Oyunun üstünde sizin gördüğünüz kutunun görünümünü düzenleyin")
        b1.setToolTip("İzleyicilerin OBS'te gördüğü altyazının görünümünü düzenleyin")
        b0.setAccessibleName("Ekranımdaki görünüm")
        b1.setAccessibleName("Yayınımdaki görünüm")
        self.side.setAccessibleName("Düzenlenen görünüm")
        self.side.setValue(self._prof)
        self.side.changed.connect(self.set_side)

        self._copy_btn = QPushButton()
        self._copy_btn.setObjectName("ghost")
        ic, sz = button_icon("copy", C.TEXT_2, ICON["s"])
        self._copy_btn.setIcon(ic)
        self._copy_btn.setIconSize(sz)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._copy_other)

        r = QHBoxLayout()
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(SPACE["m"])
        r.addWidget(self.side)
        r.addWidget(self._copy_btn)
        r.addStretch(1)
        hl.addLayout(r)
        hl.addSpacing(SPACE["s"])
        self._side_hint = label("", "muted")
        hl.addWidget(self._side_hint)
        return head

    def _field(self, text: str, control: QWidget, stretch: bool = False, tip: str = "",
               indent: int = 0) -> _Field:
        if isinstance(control, SliderRow):
            control.slider.setMinimumWidth(SLIDER_MIN)
        f = _Field(text, control, stretch, tip, indent)
        self._fields.append(f)
        return f

    def _equalize_labels(self) -> None:
        """Tüm bölümlerde etiket sütunu aynı genişlikte olsun (en uzun etikete göre)."""
        w = 0
        for f in self._fields:
            f.label.ensurePolished()
            w = max(w, f.label.fontMetrics().horizontalAdvance(f.label.text()) + f.indent)
        w = max(96, min(220, w + SPACE["s"]))
        self._label_w = w
        for f in self._fields:
            # Genişlik kendi stil sayfasıyla verilir: temanın QLabel#field min-width kuralı yeniden
            # uygulandığında (ör. üst öğe değişince) sabit genişliği ezmesin.
            lw = w - f.indent
            f.label.setStyleSheet(f"QLabel {{ min-width: {lw}px; max-width: {lw}px; }}")

    # --- duyarlı düzen ------------------------------------------------------------------------
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self) -> None:
        w = self.width()
        wide = w >= WIDE_MIN
        if wide != self._wide:
            self._wide = wide
            if wide:
                self._lay.removeWidget(self.stage_group)
                self._side_lay.insertWidget(0, self.stage_group)
                self._side.show()
            else:
                self._side_lay.removeWidget(self.stage_group)
                self._lay.insertWidget(self._stage_index, self.stage_group)
                self._side.hide()
                self._sa.setMinimumWidth(0)
                self._sa.setMaximumWidth(_QMAX)
            self.stage_group.show()
        if wide:
            # Ayar sütunu ihtiyacı kadar (etiket + en geniş kontrol), ama sayfanın yarısını geçmez;
            # kalan genişlik önizlemeye gider.
            m = self._lay.contentsMargins()
            need = (m.left() + m.right() + self._label_w + SPACE["m"] + CONTROL_MAX
                    + self._sa.verticalScrollBar().sizeHint().width())
            left = max(LEFT_MIN, min(need, LEFT_MAX, w // 2))
            if self._sa.maximumWidth() != left or self._sa.minimumWidth() != left:
                self._sa.setFixedWidth(left)
        self._fit_toolbar()

    def _fit_toolbar(self) -> None:
        """Dar alanda 'Varsayılana dön' yalnız ikon olur (ipucu ve erişilebilir adı kalır)."""
        if self._reset_full_w is None:
            self._reset_btn.ensurePolished()
            self._reset_full_w = self._reset_btn.sizeHint().width()
        m = self._lay.contentsMargins()
        if self._wide:
            sm = self._side_lay.contentsMargins()
            avail = min(self.width() - self._sa.maximumWidth(), SIDE_MAX) - sm.left() - sm.right()
        else:
            avail = self.width() - m.left() - m.right() - self._sa.verticalScrollBar().sizeHint().width()
        avail -= 2 * (SPACE["s"] + 1)  # sahne çerçevesinin iç boşluğu ve kenarı
        need = (self.scene.sizeHint().width() + self._reset_full_w + self._test_btn.sizeHint().width()
                + 3 * SPACE["s"])
        compact = avail < need
        if compact != self._compact:
            self._compact = compact
            self._reset_btn.setText("" if compact else "Varsayılana dön")

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.preview and ev.type() == QEvent.Type.Resize:
            # Sabit yükseklikli önizlemeyi dar alanda 16:9'a indir (üstte/altta boş bant kalmasın).
            # Büyütmede küçük eşik: kaydırma çubuğu belirip kaybolurken yükseklik gidip gelmesin.
            target = max(PREVIEW_MIN_H, min(self._preview_max_h, int(self.preview.width() * 9 / 16)))
            cur = self.preview.minimumHeight()
            if target < cur or target > cur + 8 or (target == self._preview_max_h and target != cur):
                self.preview.setFixedHeight(target)
        return super().eventFilter(obj, ev)

    # --- hangi görünüm düzenleniyor ---------------------------------------------------------
    def _style(self, side: str) -> StyleCfg:
        return self.ctx.s.screen_style if side == SCREEN else self.ctx.s.style

    @property
    def side_key(self) -> str:
        return self._prof

    def set_side(self, side: str) -> None:
        """Sayfanın düzenlediği görünümü değiştirir. Hiçbir ayarı değiştirmez, kaydetmez."""
        self.side.setValue(side)
        if side == self._prof or side not in (SCREEN, STREAM):
            return
        self._prof = side
        self._load_side()

    def _load_side(self) -> None:
        self.st = self._style(self._prof)
        for c in self.cards:
            c.base = self.st
        t = SIDE_TEXT[self._prof]
        screen = self._prof == SCREEN
        self._side_hint.setText(t["hint"])
        self._copy_btn.setText(t["copy"])
        self._copy_btn.setAccessibleName(t["copy"])
        self._reset_btn.setToolTip(t["reset_tip"])
        self._size_row.set_tip(t["size_tip"])
        self._pos_title.setText(t["pos_title"])
        # yalnız bir tarafta işe yarayan satırlar (bkz. STREAM_ONLY); ölçek yalnız ekrandaki kutuyu büyütür
        self._bottom_row.setVisible(not screen)
        self._maxw_row.setVisible(not screen)
        self._scale_row.setVisible(screen)
        self.sync()

    def _screen_box(self) -> tuple[float, float]:
        """Ekrandaki kutunun, bulunduğu ekrana oranı (genişlik, yükseklik); bilinmiyorsa varsayılan kutu."""
        ov = getattr(self.ctx, "overlay", None)
        try:
            g = ov.geometry()
            scr = ov.screen() or QGuiApplication.primaryScreen()
            sg = scr.geometry()
            if g.width() > 0 and g.height() > 0 and sg.width() > 0 and sg.height() > 0:
                return min(1.0, g.width() / sg.width()), min(1.0, g.height() / sg.height())
        except Exception:
            pass
        return DEFAULT_BOX

    def _refresh_preview(self) -> None:
        if self._prof == SCREEN:
            self.preview.set_profile(self.st, self._screen_box(), float(self.ctx.s.overlay.scale))
        else:
            self.preview.set_profile(self.st)
        for c in self.cards:
            c.update()
        same = all(getattr(self.ctx.s.style, n) == getattr(self.ctx.s.screen_style, n) for n in LOOK_FIELDS)
        self._copy_btn.setEnabled(not same)
        self._copy_btn.setToolTip(SAME_TIP if same else SIDE_TEXT[self._prof]["copy_tip"])

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._refresh_preview()  # kutu Ana sayfada değişmiş ya da görünüm başka yerden değişmiş olabilir

    # --- ayar ↔ kontrol --------------------------------------------------------------------
    def sync(self) -> None:
        """Tüm kontrolleri seçili görünümden doldurur (sinyal göndermeden, hiçbir şey kaydetmeden)."""
        st = self.st
        self._loading = True
        try:
            self.font.setFamily(st.font_family)
            self.weight.setNearest(st.font_weight)
            self.size.setValue(st.font_size)
            self.color.setColor(st.text_color)
            self.outline.setNearest(st.outline_width)
            self.outline_color.setColor(st.outline_color)
            self.shadow.setValue(bool(st.shadow))
            self._sync_bg()
            self.bg_color.setColor(st.bg_color)
            self.bottom.setValue(st.bottom_percent)
            self.maxw.setValue(st.max_width_percent)
            self.ov_scale.setValue(self.ctx.s.overlay.scale)
            self.speaker.setColor(st.speaker_color)
            self.fade.setValue(st.fade_ms)
            self.show_orig.setValue(bool(st.show_original))
            self.orig_size.setValue(st.original_size_percent)
            self.mask.setValue(bool(st.mask_original))
            self.mask_color.setColor(st.mask_color)
            self.mask_alpha.setValue(st.mask_opacity)
        finally:
            self._loading = False
        self._refresh_preview()

    def _sync_bg(self) -> None:
        """Arka plan seçimini eşitler ve yalnızca işe yarayan ayrıntı satırlarını gösterir."""
        op = self.st.bg_opacity
        if op <= 0.001:
            self.bg_mode.setValue(0.0)
        elif op < 0.7:
            self.bg_mode.setValue(0.45)
        else:
            self.bg_mode.setValue(0.9)
        self.bg_alpha.setValue(max(0.05, op))
        self.bg_details.setVisible(op > 0.001)
        self.outline_color_row.setVisible(self.st.outline_width > 0)
        self._orig_row.setVisible(bool(self.st.show_original))
        stream = self._prof == STREAM  # OBS maskesi yalnız yayında
        self._mask_row.setVisible(stream)
        self._mask_rows.setVisible(stream and bool(self.st.mask_original))

    def _set(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.st, attr, value)
        if attr in ("bg_opacity", "outline_width", "show_original", "mask_original"):
            self._sync_bg()
        self._changed()

    def _apply(self, values: dict) -> None:
        for k, v in values.items():
            setattr(self.st, k, v)
        self.sync()
        self._changed()

    def _reset(self) -> None:
        """Yalnız seçili görünümü ilk hâline döndürür."""
        d = StyleCfg()
        for f in dataclasses.fields(StyleCfg):
            setattr(self.st, f.name, getattr(d, f.name))
        self.sync()
        self._changed()

    def _copy_other(self) -> None:
        """Öbür görünümün yazı/renk/kenar/arka plan ayarlarını seçili görünüme kopyalar (yerinde; başka
        bileşenler bu nesneleri tutar). Yalnız yayına ait konum, genişlik ve maske kopyalanmaz."""
        src = self._style(STREAM if self._prof == SCREEN else SCREEN)
        for name in LOOK_FIELDS:
            setattr(self.st, name, getattr(src, name))
        self.sync()
        self._changed()
        toast = getattr(self.win, "toast", None)
        if toast is not None:
            toast(SIDE_TEXT[self._prof]["copied"], "success")

    def _changed(self) -> None:
        self.ctx.save_later()
        self.ctx.display.push_style()
        self._refresh_preview()

    def _set_scale(self, v: float) -> None:
        if self._loading:
            return
        self.ctx.s.overlay.scale = round(v, 1)
        self._overlay_changed()
        self._refresh_preview()

    def _overlay_changed(self) -> None:
        self.ctx.save_later()
        self.ctx.overlay.apply_settings()
        self.ctx.display.push_style()
