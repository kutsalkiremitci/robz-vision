"""Ana sayfa: canlı başlık bandı (başlat/durdur + durum), çevrilecek bölge ve canlı önizleme, son çeviri,
ekran altyazısı, OBS.

Sayfanın tek cesur öğesi canlı başlık bandıdır (DESIGN.md → "Cesaret tek yerde"); geri kalan bölümler
başlık + ince ayırıcıyla ayrılan sade bantlardır.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from altyazitr import winutil
from altyazitr.capture import WindowCapturer, grab_screen_rect
from altyazitr.gui import icons
from altyazitr.gui.common import _repolish, _tip
from altyazitr.gui.controls import Segmented
from altyazitr.gui.dialogs import show_obs_help
from altyazitr.gui.region_dialog import bgr_to_pixmap
from altyazitr.gui.region_picker import logical_to_physical
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, SUNSET_STOPS, C, F, motion_ms
from altyazitr.gui.widgets import Card, bind_check, label, page

log = logging.getLogger(__name__)

PREVIEW_TIP = ("Seçilen bölgenin canlı görüntüsü. Çalışırken yeşil boyalı alanlar 'metin' olarak algılanan "
               "yerlerdir.")
PILL_H = 22  # durum rozeti yüksekliği (temadaki rozet köşe yarıçapının iki katı)


# --- küçük yardımcılar -------------------------------------------------------------------------
def _css(family: str, px: int, color: str, weight: int | None = None) -> str:
    """Tek bir öğeye yazı tipi/renk veren QSS (genel temadaki yazı tipi kuralını bu öğe için geçersiz kılar)."""
    css = f"font-family: '{family}', '{F.BODY_FALLBACK}'; font-size: {px}px; color: {color}; background: transparent;"
    return css + (f" font-weight: {weight};" if weight else "")


def _set_icon(btn: QPushButton, name: str, color: str, size: int = ICON["s"]) -> None:
    btn.setIcon(icons.icon(name, color))
    btn.setIconSize(QSize(size, size))


def _button(text: str, icon_name: str, obj: str = "", color: str = C.TEXT_2) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    _set_icon(b, icon_name, color)
    return b


def _icon_button(icon_name: str, tip: str) -> QPushButton:
    """Yalnız ikonlu kare düğme: anlamı tooltip ve erişilebilir adla taşınır."""
    b = QPushButton()
    b.setObjectName("icon")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    _set_icon(b, icon_name, C.TEXT_2, ICON["m"] - 2)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    return b


def _section(title: str, *trailing: QWidget) -> Card:
    """Sade bölüm (widgets.Card); verilen öğeler başlık satırının sağına yerleşir."""
    card = Card(title)
    if not trailing:
        return card
    lay = card.lay
    head = lay.itemAt(0).widget()  # Card'ın başlık etiketi
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(SPACE["m"])
    lay.removeWidget(head)
    h.addWidget(head)
    h.addStretch(1)
    for w in trailing:
        h.addWidget(w)
    lay.insertLayout(0, h)
    return card


def _pill(lb: QLabel) -> QLabel:
    """Durum rozeti doğal yüksekliğinde kalsın ve temadaki yuvarlak uçlar çizilebilsin (Qt, köşe yarıçapı
    yüksekliğin yarısından büyükse yuvarlamayı tamamen bırakır)."""
    lb.setMinimumHeight(PILL_H)
    lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return lb


def _hbox(*items, spacing: int = SPACE["s"]) -> QHBoxLayout:
    """None → esneyen boşluk; int → sabit boşluk; (öğe, esneme) → esneme payıyla öğe."""
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for it in items:
        if it is None:
            h.addStretch(1)
        elif isinstance(it, int):
            h.addSpacing(it)
        elif isinstance(it, tuple):
            h.addWidget(it[0], it[1])
        else:
            h.addWidget(it)
    return h


# --- canlı başlık bandı --------------------------------------------------------------------------
class LiveBand(QFrame):
    """Sayfanın tek cesur öğesi. Zemin ve çerçeveyi tema çizer (QFrame#live: PANEL + 1 px LINE, RADIUS l);
    burada yalnızca alt kenardaki 2 px çizgi çizilir: boştayken gün batımı geçişi (pembe → turuncu),
    çalışırken düz turkuaz. Geçiş kullanıcının Başlat/Durdur eylemine yanıt olarak yumuşakça olur
    (Windows'ta animasyonlar kapalıysa anında)."""

    LINE_PX = 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("live")
        self._t = 0.0  # 0 = gün batımı, 1 = turkuaz
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)

    def set_running(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        self._anim.stop()
        ms = motion_ms("base")
        if ms <= 0 or not self.isVisible() or abs(self._t - target) < 1e-3:
            self._step(target)
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.start()

    def _step(self, v) -> None:
        self._t = float(v)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = float(RADIUS["l"])
        # alt çizgi: yuvarlak köşeleri izleyen 2 px kenar (CSS'teki border-bottom gibi)
        body = QPainterPath()
        body.addRoundedRect(r, rad, rad)
        inner = QPainterPath()
        inner.addRoundedRect(r.adjusted(0, 0, 0, -self.LINE_PX), rad, rad)
        line = body.subtracted(inner)
        if self._t < 1.0:
            g = QLinearGradient(r.topLeft(), r.topRight())
            for pos, col in SUNSET_STOPS:
                g.setColorAt(pos, QColor(col))
            p.fillPath(line, g)
        if self._t > 0.0:
            c = QColor(C.TURQUOISE)
            c.setAlphaF(min(1.0, self._t))
            p.fillPath(line, c)
        p.end()


class _IconPill(QLabel):
    """Solunda ikon olan durum rozeti (ör. uyarı). İkon, metnin girintisine dikey ortalı çizilir."""

    _PX = ICON["s"] - 2

    def __init__(self, text: str, icon_name: str, color: str, obj: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName(obj)
        self._pm = icons.pixmap(icon_name, self._PX, color)
        self.setIndent(self._PX + SPACE["s"] - 2)

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt
        super().paintEvent(e)
        r = self.contentsRect()
        p = QPainter(self)
        p.drawPixmap(r.left(), r.top() + (r.height() - self._PX) // 2, self._pm)
        p.end()


class _ElidedLabel(QLabel):
    """Tek satır, sığmazsa sonu '…' ile kısalan etiket. text() her zaman tam metni döndürür."""

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        p = QPainter(self)
        r = self.contentsRect()
        txt = " ".join(self.text().split())
        txt = self.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, r.width())
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        p.end()


class _Metrics(QWidget):
    """Son çevirinin süreleri: ayrı küçük sayı öğeleri (ad + Bahnschrift sayı). Ayrıntı ipucunda."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["l"])
        self._items: dict[str, QLabel] = {}
        for key in ("ocr", "tr"):
            lb = QLabel()
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setStyleSheet(_css(F.BODY, F.SMALL, C.TEXT_3))
            lay.addWidget(lb)
            self._items[key] = lb
        self.setAccessibleName("Son çevirinin süreleri")

    @staticmethod
    def _item(name: str, ms: float) -> str:
        return (f"{name}&nbsp;&nbsp;<span style=\"font-family: '{F.DISPLAY_SEMIBOLD}'; font-size: {F.BODY_SIZE}px; "
                f"color: {C.TEXT_2};\">{ms:.0f} ms</span>")

    def set_values(self, ocr_ms: float, tr_ms: float) -> None:
        self._items["ocr"].setText(self._item("OCR", ocr_ms))
        self._items["tr"].setText(self._item("Çeviri", tr_ms))


# --- sayfa ---------------------------------------------------------------------------------------
class HomePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = ctx = win.ctx
        s = ctx.s
        sa, lay = page("Ana sayfa")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(sa)

        lay.addWidget(self._build_live())
        lay.addWidget(self._build_region())
        lay.addWidget(self._build_last())
        lay.addWidget(self._build_overlay())
        lay.addWidget(self._build_obs())
        lay.addStretch(1)

        # --- sinyaller
        p = ctx.pipeline
        p.running_changed.connect(self._on_running)
        p.status.connect(self._on_status)
        p.preview.connect(self._on_pipeline_preview)
        p.subtitle.connect(self._on_sub)
        ctx.obs_clients.connect(self._on_obs)
        ctx.display.visibility_changed.connect(self._on_visibility)
        ctx.overlay.editing_changed.connect(self._on_editing)
        ctx.overlay.geometry_saved.connect(self._check_overlap)
        self.vis.setToolTip(f"Altyazıyı gizle/göster ({s.hotkeys.visibility})")
        self.btn.setToolTip(f"Çeviriyi başlat/durdur ({s.hotkeys.toggle})")

        self._prev_timer = QTimer(self, interval=300, timeout=self._tick_preview)
        self._wincap: WindowCapturer | None = None
        self._wincap_key: tuple | None = None
        self._on_running(ctx.pipeline.running)
        self._on_visibility(ctx.display.visible)
        self.refresh_windows()
        self._mode_changed(s.capture.mode, initial=True)

    # --- kurulum -------------------------------------------------------------------------
    def _build_live(self) -> QFrame:
        ctx = self.ctx
        self.live = band = LiveBand()
        v = QVBoxLayout(band)
        v.setContentsMargins(SPACE["xl"], SPACE["l"], SPACE["xl"], SPACE["l"] + LiveBand.LINE_PX)
        v.setSpacing(0)

        self.btn = QPushButton("Başlat")
        self.btn.setObjectName("primary")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setMinimumSize(152, 44)
        self.btn.setIconSize(QSize(ICON["m"], ICON["m"]))
        self.btn.clicked.connect(ctx.toggle_running)

        # büyük durum yazısı ("Hazır" / "Çalışıyor") ve altında ayrıntılı durum satırı
        self.state = label("Hazır", "status")
        self.status = _ElidedLabel("")
        self.status.setObjectName("muted")
        self.status.setMinimumWidth(10)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setMinimumHeight(20)
        self.status.hide()  # ilk durum mesajına kadar "Hazır" düğmeyle aynı hizada, ortada durur
        colw = QWidget()
        col = QVBoxLayout(colw)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addStretch(1)
        col.addWidget(self.state)
        col.addWidget(self.status)
        col.addStretch(1)
        self.state.ensurePolished()
        colw.setMinimumHeight(self.state.sizeHint().height() + self.status.minimumHeight())  # bant zıplamasın

        test = _tip(_button("Test", "captions", "ghost"), "Ekranınızda ve OBS'te 5 saniyelik örnek altyazı gösterir")
        test.clicked.connect(ctx.display.show_test)
        self.vis = _button("Gizle", "eye-off", "ghost")
        self.vis.clicked.connect(ctx.display.toggle_visible)

        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["l"])
        h.addWidget(self.btn)
        h.addWidget(colw, 1)
        h.addWidget(test)
        h.addWidget(self.vis)
        v.addLayout(h)
        return band

    def _build_region(self) -> Card:
        s = self.ctx.s
        self.mode = Segmented([("Ekran bölgesi", "screen"), ("Uygulama penceresi", "window")],
                              icons=["monitor", "app-window"])
        self.mode.group.button(0).setToolTip("Ekranın herhangi bir yerini fareyle seçin (önerilen)")
        self.mode.group.button(1).setToolTip("Belirli bir pencereden okuyun. Pencere başka pencerelerin "
                                             "arkasında kalsa da okunur; simge durumuna küçültmeyin.")
        self.mode.setValue(s.capture.mode)
        self.mode.changed.connect(self._mode_changed)
        rc = _section("Çevrilecek bölge", self.mode)

        self.win_row = QWidget()
        self.win_combo = QComboBox()
        self.win_combo.setMinimumWidth(220)
        self.win_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.win_combo.setToolTip("Altyazının okunacağı pencere")
        self.win_combo.setAccessibleName("Okunacak pencere")
        self.win_combo.activated.connect(self._window_chosen)
        refresh = _icon_button("refresh", "Pencere listesini yenile")
        refresh.clicked.connect(self.refresh_windows)
        wr = _hbox((self.win_combo, 1), refresh)
        self.win_row.setLayout(wr)
        rc.add(self.win_row)

        # önizleme yüzeyi; bölge seçilmemişken içinde boş durum (ikon + açıklama + Bölge seç) durur
        self.preview = QLabel()
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(148)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.preview.setToolTip(PREVIEW_TIP)
        self.preview.setAccessibleName("Bölge önizlemesi")

        self.b_region = QPushButton("Bölge seç")
        self.b_region.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_region.setIconSize(QSize(ICON["s"], ICON["s"]))
        self.b_region.setToolTip("Altyazının çıktığı alanı fareyle işaretleyin")
        self.b_region.clicked.connect(self.win.select_region)

        self._empty = QWidget(self.preview)
        el = QVBoxLayout(self._empty)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(SPACE["s"])
        mark = QLabel()
        mark.setPixmap(icons.pixmap("crop", ICON["l"], C.TEXT_3))
        mark.setStyleSheet("background: transparent;")
        msg = label("Henüz bölge seçilmedi", "muted")
        el.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(msg, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addSpacing(SPACE["xs"])
        self._empty_lay = el
        pv = QVBoxLayout(self.preview)
        pv.setContentsMargins(SPACE["l"], SPACE["l"], SPACE["l"], SPACE["l"])
        pv.addWidget(self._empty, 0, Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        self._empty_on = False
        rc.add(self.preview)

        self.ink = _pill(label("", "pill"))
        self.ink.hide()
        self._region_bar = QWidget()
        self._bar_lay = _hbox(self.b_region, None, self.ink)
        self._region_bar.setLayout(self._bar_lay)
        rc.add(self._region_bar)
        return rc

    def _build_last(self) -> Card:
        self.metrics = _Metrics()
        self.metrics.hide()
        last = _section("Son çeviri", self.metrics)
        self._last_empty = label("Henüz çeviri yok", "muted")
        self._last_empty.setStyleSheet(f"color: {C.TEXT_3};")
        self.tr = label("—", "tr", wrap=True)
        self.en = label("", "en", wrap=True)
        self.tr.hide()
        self.en.hide()
        last.add(self._last_empty)
        last.add(self.tr)
        last.add(self.en)
        return last

    def _build_overlay(self) -> Card:
        ctx, ov = self.ctx, self.ctx.s.overlay
        self.ov_on = Segmented([("Kapalı", False), ("Açık", True)])
        self.ov_on.setToolTip("Çeviriyi kendi ekranınızda gösterir")
        self.ov_on.setAccessibleName("Ekranımda göster")
        self.ov_on.setValue(bool(ov.enabled))
        self.ov_on.changed.connect(self._ov_toggle)
        oc = _section("Ekranımda", self.ov_on)

        self.ov_edit = _tip(_button("Konumu ayarla", "move"),
                            "Altyazı kutusu ekranda gerçek görünümüyle belirir; sürükleyerek taşıyın, "
                            "kenarlarından boyutlandırın. Bitirmek için çift tıklayın veya tekrar basın.")
        self.ov_edit.clicked.connect(lambda: ctx.overlay.set_editing(not ctx.overlay.editing))
        ov_reset = _tip(_button("Sıfırla", "reset", "ghost"), "Altyazı kutusunu varsayılan konuma al")
        ov_reset.clicked.connect(ctx.overlay.reset_position)
        oc.add(layout=_hbox(self.ov_edit, ov_reset, None))

        stream = bind_check("Yayında da görünsün", ov, "visible_in_stream", self._ov_changed,
                            tip="Açık: OBS 'Ekran Yakalama' bu altyazıyı da yayına alır (tarayıcı kaynağına "
                                "gerek kalmaz).\nKapalı: yalnızca siz görürsünüz; izleyiciler OBS tarayıcı "
                                "kaynağındaki altyazıyı görür.")
        self.ov_warn = _IconPill("Kutu okunan bölgenin üstünde", "alert", C.ORANGE, "pillWarn")
        self.ov_warn.setToolTip("'Yayında da görünsün' açıkken altyazı kutusu çevrilen bölgenin üstündeyse "
                                "program kendi Türkçe yazısını okuyabilir. Kutuyu başka yere taşıyın.")
        self.ov_warn.setAccessibleName("Uyarı: kutu okunan bölgenin üstünde")
        _pill(self.ov_warn)
        self.ov_warn.hide()
        oc.add(layout=_hbox(stream, None, self.ov_warn, spacing=SPACE["m"]))
        return oc

    def _build_obs(self) -> Card:
        ctx = self.ctx
        self.obs_state = _pill(label("Bağlı değil", "pill"))
        obs = _section("OBS", self.obs_state)
        self.url = QLineEdit(ctx.server.url)
        self.url.setReadOnly(True)
        self.url.setCursorPosition(0)
        self.url.setToolTip("OBS'e Tarayıcı Kaynağı olarak ekleyin")
        self.url.setAccessibleName("OBS tarayıcı kaynağı adresi")
        copy = _icon_button("copy", "Adresi kopyala")
        copy.clicked.connect(self._copy_url)
        helpb = _icon_button("help", "OBS'e nasıl eklenir?")
        helpb.clicked.connect(lambda: show_obs_help(self, ctx.server.url))
        obs.add(layout=_hbox((self.url, 1), copy, helpb))
        return obs

    # --- genel ----------------------------------------------------------------------
    def refresh(self) -> None:
        c = self.ctx.s.capture
        ready = c.is_ready() and (c.mode == "window" or len(c.rect) == 4)
        self.b_region.setText("Bölgeyi değiştir" if ready else "Bölge seç")
        _repolish(self.b_region, "" if ready else "primary")
        _set_icon(self.b_region, "crop", C.TEXT_2 if ready else C.INVERSE)
        if not ready and not self.ctx.pipeline.running:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("")
            self._set_empty(True)
        else:
            self._set_empty(False)
        self._check_overlap()

    def _set_empty(self, on: bool) -> None:
        """Boş durum: önizleme yüzeyinde ikon + 'Henüz bölge seçilmedi' + Bölge seç (b_region buraya taşınır).
        Bölge seçiliyken b_region önizlemenin altındaki satıra döner."""
        if on == self._empty_on:
            return
        self._empty_on = on
        if on:
            self._bar_lay.removeWidget(self.b_region)
            self._empty_lay.addWidget(self.b_region, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            self._empty_lay.removeWidget(self.b_region)
            self._bar_lay.insertWidget(0, self.b_region)
        self.b_region.show()
        self._empty.setVisible(on)
        self._region_bar.setVisible(not on)
        self.preview.setToolTip("" if on else PREVIEW_TIP)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._prev_timer.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._prev_timer.stop()
        self._stop_wincap()

    # --- bölge / pencere ------------------------------------------------------------------
    def _mode_changed(self, mode, initial: bool = False) -> None:
        c = self.ctx.s.capture
        self.win_row.setVisible(mode == "window")
        self._stop_wincap()
        if not initial and mode != c.mode:
            c.mode = mode
            self.ctx.save_later()
            self.ctx.request_restart()
        self.refresh()

    def refresh_windows(self) -> None:
        c = self.ctx.s.capture
        self.win_combo.blockSignals(True)
        self.win_combo.clear()
        sel = -1
        for w in sorted(winutil.list_windows(), key=lambda w: w.title.lower()):
            self.win_combo.addItem(w.label, (w.exe, w.title))
            if sel < 0 and w.exe.lower() == c.window_exe.lower() and (not c.window_title or w.title == c.window_title):
                sel = self.win_combo.count() - 1
        if sel < 0:
            if c.window_exe or c.window_title:
                self.win_combo.insertItem(0, f"(açık değil) {c.window_title or c.window_exe}",
                                          (c.window_exe, c.window_title))
            else:
                self.win_combo.insertItem(0, "Pencere seçin…", None)
            sel = 0
        self.win_combo.setCurrentIndex(sel)
        self.win_combo.blockSignals(False)

    def _window_chosen(self, i: int) -> None:
        data = self.win_combo.itemData(i)
        if not data:
            return
        c = self.ctx.s.capture
        c.window_exe, c.window_title = data
        self._stop_wincap()
        self.ctx.save_later()
        self.ctx.request_restart()
        self.refresh()

    # --- canlı önizleme ------------------------------------------------------------------------
    def _stop_wincap(self) -> None:
        if self._wincap is not None:
            self._wincap.stop()
            self._wincap = None
            self._wincap_key = None

    def _tick_preview(self) -> None:
        if self.ctx.pipeline.running:
            self._stop_wincap()
            return
        c = self.ctx.s.capture
        img = None
        if c.mode == "screen":
            if len(c.rect) == 4:
                img = grab_screen_rect(c.rect)
        elif c.window_exe or c.window_title:
            key = (c.window_exe, c.window_title)
            if self._wincap is None or self._wincap_key != key or not self._wincap.alive:
                self._stop_wincap()
                w = winutil.find_window(c.window_exe, c.window_title)
                if w is not None:
                    try:
                        self._wincap = WindowCapturer(w.hwnd, tuple(c.region), 5)
                        self._wincap.start()
                        self._wincap_key = key
                    except Exception:
                        log.debug("Önizleme yakalaması başlatılamadı", exc_info=True)
                        self._wincap = None
            if self._wincap is not None:
                self._wincap.set_region(tuple(c.region))
                img = self._wincap.grab_roi()
        if img is not None and img.size:
            self._set_preview(bgr_to_pixmap(img))

    def _set_preview(self, pm: QPixmap) -> None:
        if self._empty_on:  # bölge yokken boş durumun altına görüntü çizme
            return
        w, h = max(100, self.preview.width() - 8), max(60, self.preview.height() - 8)
        self.preview.setPixmap(pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def _on_pipeline_preview(self, img, ink: float) -> None:
        if not self.isVisible():
            return
        self._set_empty(False)
        self._set_preview(QPixmap.fromImage(img))
        on = ink >= 0.003
        self.ink.setText("Metin var" if on else "Metin yok")
        self.ink.show()
        _repolish(self.ink, "pillOn" if on else "pill")

    # --- durum ---------------------------------------------------------------------------------
    def _on_running(self, running: bool) -> None:
        self.btn.setText("Durdur" if running else "Başlat")
        self.btn.setIcon(icons.icon("stop" if running else "play", C.DANGER if running else C.INVERSE))
        _repolish(self.btn, "danger" if running else "primary")
        self.state.setText("Çalışıyor" if running else "Hazır")
        # 2 px dikey pay: Bahnschrift'te aksan/nokta (Ç, İ) kırpılmasın
        self.state.setStyleSheet(f"color: {C.TURQUOISE if running else C.TEXT_2}; padding: 2px 0;")
        self.live.set_running(running)
        if not running:
            self.ink.hide()

    def _on_status(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.setToolTip(msg)
        self.status.setVisible(bool(msg))

    def _on_visibility(self, visible: bool) -> None:
        self.vis.setText("Gizle" if visible else "Göster")
        _set_icon(self.vis, "eye-off" if visible else "eye", C.TEXT_2)

    def _on_sub(self, ev: dict) -> None:
        sp = f"{ev['speaker']}: " if ev.get("speaker") else ""
        self.tr.setText(sp + ev["text"])
        self.en.setText(sp + ev["original"])
        self._last_empty.hide()
        self.tr.show()
        self.en.setVisible(bool(ev.get("original")))
        self.metrics.set_values(ev.get("ocr_ms", 0), ev.get("tr_ms", 0))
        tip = [f"Toplam gecikme {ev.get('latency_ms', 0):.0f} ms"]
        if ev.get("engine"):
            tip.append(f"Motor: {ev['engine']}")
        if ev.get("cached"):
            tip.append("Önbellekten geldi")
        self.metrics.setToolTip("\n".join(tip))
        self.metrics.show()

    def _on_obs(self, n: int) -> None:
        self.obs_state.setText("Bağlı" if n else "Bağlı değil")
        _repolish(self.obs_state, "pillOn" if n else "pill")

    def _copy_url(self) -> None:
        QGuiApplication.clipboard().setText(self.ctx.server.url)
        self.win.toast("Adres kopyalandı")

    # --- ekran altyazısı ---------------------------------------------------------------------------
    def _ov_toggle(self, on) -> None:
        self.ctx.s.overlay.enabled = bool(on)
        self._ov_changed()

    def _ov_changed(self) -> None:
        self.ctx.save_later()
        self.ctx.overlay.apply_settings()
        self._check_overlap()

    def _on_editing(self, on: bool) -> None:
        self.ov_edit.setText("Konumu kaydet" if on else "Konumu ayarla")
        _set_icon(self.ov_edit, "check" if on else "move", C.INVERSE if on else C.TEXT_2)
        _repolish(self.ov_edit, "primary" if on else "")
        self._check_overlap()

    def _check_overlap(self) -> None:
        c, ov = self.ctx.s.capture, self.ctx.overlay
        show = False
        if self.ctx.s.overlay.visible_in_stream and c.mode == "screen" and len(c.rect) == 4:
            scr = ov.screen() or QGuiApplication.primaryScreen()
            local = ov.geometry().translated(-scr.geometry().topLeft())
            box = QRect(*logical_to_physical(scr, local))
            show = box.intersects(QRect(*c.rect))
        self.ov_warn.setVisible(show)
