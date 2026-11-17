"""Sade, dokunması kolay kontrol bileşenleri: segmentli seçici, renk yuvarlakları, kaydırıcı,
yazı tipi seçici, katlanabilir bölüm, canlı altyazı önizlemesi ve hazır stil kartları."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)

from altyazitr.config import StyleCfg
from altyazitr.gui import icons
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F
from altyazitr.gui.widgets import button_icon, color_chip_qss
from altyazitr.render import paint_subtitle

SAMPLE_TEXT = "Arabaya bin, Vice City'ye gidiyoruz. Çabuk ol!"
SAMPLE_SPEAKER = "LUCIA"
SAMPLE_ORIGINAL = "Get in the car, we're going to Vice City. Hurry up!"


# --- segmentli seçici --------------------------------------------------------
class Segmented(QWidget):
    """Yan yana düğmelerden tek seçim (ör. Yok | İnce | Kalın).

    Düğmeler `QFrame#segmented` kanalının içinde durur; seçili segment camgöbeği yarı saydam zemin ve
    camgöbeği kenarla gösterilir. `icons` verilirse her seçeneğin solunda o ikon (gui/icons adı) çizilir."""

    changed = Signal(object)

    def __init__(self, options: Sequence[tuple[str, object]], parent: QWidget | None = None,
                 icons: list[str] | None = None):
        super().__init__(parent)
        self._values = [v for _, v in options]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.track = QFrame()
        self.track.setObjectName("segmented")
        tl = QHBoxLayout(self.track)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        n = len(options)
        for i, (text, _v) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setObjectName("segL" if i == 0 else ("segR" if i == n - 1 else "segM"))
            if icons and i < len(icons) and icons[i]:
                ic, sz = button_icon(icons[i], C.TEXT_2, ICON["s"], on_color=C.TEXT)
                b.setIcon(ic)
                b.setIconSize(sz)
            self.group.addButton(b, i)
            tl.addWidget(b)
        lay.addWidget(self.track)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))

    def setValue(self, value) -> None:  # noqa: N802
        idx = self._values.index(value) if value in self._values else -1
        self._check(idx)

    def setNearest(self, value: float) -> None:  # noqa: N802
        nums = [float(v) for v in self._values]
        self._check(min(range(len(nums)), key=lambda i: abs(nums[i] - value)))

    def _check(self, idx: int) -> None:
        self.group.blockSignals(True)
        if idx < 0:
            self.group.setExclusive(False)
            for b in self.group.buttons():
                b.setChecked(False)
            self.group.setExclusive(True)
        else:
            self.group.button(idx).setChecked(True)
        self.group.blockSignals(False)


# --- renk yuvarlakları ------------------------------------------------------
class _Swatch(QPushButton):
    """Tek renk yuvarlağı. Seçiliyken camgöbeği halka + onay ikonu (renge bakmadan da okunur);
    klavye odağında yalnız camgöbeği halka; üzerine gelince ince açık halka."""

    D = 30  # dış çap

    def __init__(self, color: str, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.hex = color
        self.setObjectName("swatch")
        self.setFixedSize(self.D, self.D)
        self.setCheckable(True)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        col = QColor(self.hex)
        focus = self.hasFocus() and bool(self.property("kbfocus"))
        ring = self.isChecked() or focus
        if ring:
            p.setPen(QPen(QColor(C.CYAN), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, self.D / 2 - 1.5, self.D / 2 - 1.5)
        elif self.underMouse():
            p.setPen(QPen(QColor(C.TEXT_3), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, self.D / 2 - 1.5, self.D / 2 - 1.5)
        # dolgu: koyu renkler zeminde kaybolmasın diye ince açık kenar
        edge = QColor(C.TEXT)
        edge.setAlphaF(0.22 if col.lightness() < 90 else 0.0)
        p.setPen(QPen(edge, 1) if edge.alpha() else Qt.PenStyle.NoPen)
        p.setBrush(col)
        r = self.D / 2 - (5 if ring else 3)
        p.drawEllipse(c, r, r)
        if self.isChecked():
            ink = C.INVERSE if col.lightness() > 140 else C.TEXT
            sz = 14
            p.drawPixmap(QRectF(c.x() - sz / 2, c.y() - sz / 2, sz, sz).toRect(), icons.pixmap("check", sz, ink))
        p.end()


class SwatchRow(QWidget):
    changed = Signal(str)

    def __init__(self, colors: Sequence[tuple[str, str]], parent: QWidget | None = None):
        """colors: [(hex, ad)]"""
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["xs"] + 2)
        self._buttons: list[tuple[str, QPushButton]] = []
        for hexc, name in colors:
            b = _Swatch(hexc, name)
            b.clicked.connect(lambda _=False, c=hexc: self._pick(c))
            lay.addWidget(b)
            self._buttons.append((hexc.lower(), b))
        lay.addSpacing(SPACE["xs"])
        self.custom = QPushButton("Özel…")
        self.custom.setCheckable(True)
        self.custom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.custom.setToolTip("Başka bir renk seçin")
        self.custom.clicked.connect(self._custom)
        lay.addWidget(self.custom)
        lay.addStretch(1)
        self._color = "#ffffff"

    def setColor(self, c: str) -> None:  # noqa: N802
        self._color = c
        matched = False
        for hexc, b in self._buttons:
            on = hexc == c.lower()
            b.setChecked(on)
            matched |= on
        self.custom.setChecked(not matched)
        if matched:
            self.custom.setText("Özel…")
            self.custom.setStyleSheet("")
        else:
            self.custom.setText(c.upper())
            self.custom.setStyleSheet(color_chip_qss(c, ring=C.CYAN))

    def _pick(self, c: str) -> None:
        self.setColor(c)
        self.changed.emit(c)

    def _custom(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Renk seçin")
        if c.isValid():
            self._pick(c.name())
        else:
            self.setColor(self._color)


# --- kaydırıcı (A− / A+) ------------------------------------------------------
class SliderRow(QWidget):
    changed = Signal(float)

    def __init__(self, lo: float, hi: float, step: float = 1.0, fmt: str = "{:.0f}", scale: float = 1.0,
                 minus: str | None = None, plus: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.scale, self.fmt = scale, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["s"])
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo * scale)), int(round(hi * scale)))
        self.slider.setSingleStep(max(1, int(round(step * scale))))
        self.slider.setPageStep(max(1, int(round(step * scale * 4))))
        self.slider.setMinimumWidth(200)
        self.val = QLabel()
        self.val.setObjectName("value")
        self.val.setMinimumWidth(56)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if minus:
            bm = self._step_button("minus", "Azalt", minus)
            bm.clicked.connect(lambda: self.slider.setValue(self.slider.value() - self.slider.singleStep()))
            lay.addWidget(bm)
        lay.addWidget(self.slider, 1)
        if plus:
            bp = self._step_button("plus", "Artır", plus)
            bp.clicked.connect(lambda: self.slider.setValue(self.slider.value() + self.slider.singleStep()))
            lay.addWidget(bp)
        lay.addWidget(self.val)
        self.slider.valueChanged.connect(self._moved)

    @staticmethod
    def _step_button(icon_name: str, tip: str, text: str) -> QPushButton:
        """32×32 ikon düğmesi (basılı tutunca tekrarlar). `text` eski etiketti (A−/A+); erişilebilir
        açıklama olarak korunur."""
        b = QPushButton()
        b.setObjectName("icon")
        b.setIcon(icons.icon(icon_name, C.TEXT_2))
        b.setIconSize(QSize(ICON["s"], ICON["s"]))
        b.setFixedSize(32, 32)
        b.setAutoRepeat(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setAccessibleDescription(text)
        return b

    def _moved(self, v: int) -> None:
        val = v / self.scale
        self.val.setText(self.fmt.format(val))
        self.changed.emit(val)

    def setValue(self, v: float) -> None:  # noqa: N802
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.val.setText(self.fmt.format(v))


# --- yazı tipi seçici ------------------------------------------------------------
CURATED_FONTS = ["Segoe UI", "Arial", "Bahnschrift", "Calibri", "Verdana", "Tahoma", "Trebuchet MS",
                 "Franklin Gothic Medium", "Roboto", "Open Sans", "Montserrat", "Poppins", "Inter", "Nunito",
                 "Lato", "Oswald", "Georgia", "Impact", "Comic Sans MS", "Consolas"]
_OTHER = "__diger__"


class _FontDialog(QDialog):
    def __init__(self, current: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Yazı tipi seçin")
        self.combo = QFontComboBox()
        self.combo.setWritingSystem(QFontDatabase.WritingSystem.Latin)
        self.combo.setCurrentFont(QFont(current))
        self.sample = QLabel("Arabaya bin — ğüşıöç ĞÜŞİÖÇ 0123")
        self.sample.setMinimumHeight(70)
        self.combo.currentFontChanged.connect(self._upd)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Seç")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Bilgisayarınızdaki tüm yazı tipleri:"))
        lay.addWidget(self.combo)
        lay.addWidget(self.sample)
        lay.addWidget(bb)
        self.resize(460, 200)
        self._upd(self.combo.currentFont())

    def _upd(self, f: QFont) -> None:
        f2 = QFont(f)
        f2.setPointSize(20)
        self.sample.setFont(f2)

    def family(self) -> str:
        return self.combo.currentFont().family()


class FontPicker(QComboBox):
    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setMaxVisibleItems(16)
        available = set(QFontDatabase.families())
        for fam in CURATED_FONTS:
            if fam in available:
                self._add(fam)
        self.addItem("Diğer yazı tipleri…", _OTHER)
        self._current = ""
        self.activated.connect(self._activated)

    def _add(self, fam: str, at: int | None = None) -> None:
        i = self.count() if at is None else at
        self.insertItem(i, fam, fam)
        f = QFont(fam)
        f.setPointSize(11)
        self.setItemData(i, f, Qt.ItemDataRole.FontRole)

    def setFamily(self, fam: str) -> None:  # noqa: N802
        self.blockSignals(True)
        idx = self.findData(fam)
        if idx < 0:
            self._add(fam, at=max(0, self.count() - 1))
            idx = self.findData(fam)
        self.setCurrentIndex(idx)
        f = QFont(fam)
        f.setPointSize(10)
        self.setFont(f)
        self._current = fam
        self.blockSignals(False)

    def _activated(self, i: int) -> None:
        data = self.itemData(i)
        if data == _OTHER:
            dlg = _FontDialog(self._current or "Segoe UI", self)
            if dlg.exec():
                fam = dlg.family()
                self.setFamily(fam)
                self.changed.emit(fam)
            else:
                self.setFamily(self._current)
            return
        self.setFamily(data)
        self.changed.emit(data)


# --- kısayol kutusu ----------------------------------------------------------------
class HotkeyEdit(QLineEdit):
    """Basılan tuş kombinasyonunu 'Ctrl+Alt+T' biçiminde yakalar. (Qt'nin QKeySequenceEdit'i Türkçe
    çeviride Alt tuşunu 'Seçenek' diye gösteriyor; Windows kullanıcıları için kafa karıştırıcı.)"""

    _MODS = ((Qt.KeyboardModifier.ControlModifier, "Ctrl"), (Qt.KeyboardModifier.AltModifier, "Alt"),
             (Qt.KeyboardModifier.ShiftModifier, "Shift"), (Qt.KeyboardModifier.MetaModifier, "Win"))
    _IGNORE = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}

    def __init__(self, seq: str = "", parent: QWidget | None = None):
        super().__init__(seq, parent)
        self.setObjectName("hotkey")
        self.setReadOnly(True)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPlaceholderText("Kısayola basın…")
        self.setToolTip("Tıklayın ve yeni tuş kombinasyonuna basın (Delete: kısayolu kaldır)")
        self._seq = seq

    def sequence(self) -> str:
        return self._seq

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self.setText("")

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self.setText(self._seq)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        key = e.key()
        if key in self._IGNORE:
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not e.modifiers():
            self._seq = ""
            self.setText("")
            return
        mods = [name for flag, name in self._MODS if e.modifiers() & flag]
        name = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
        if not name:
            return
        self._seq = "+".join(mods + [name])
        self.setText(self._seq)
        self.clearFocus()


# --- katlanabilir bölüm ----------------------------------------------------------
class Collapsible(QWidget):
    def __init__(self, title: str, expanded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.btn = QPushButton()
        self.btn.setObjectName("collapse")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setAccessibleName(title)
        self.btn.clicked.connect(self.toggle)
        self._icons = {False: button_icon("chevron-right", C.TEXT_2, ICON["s"], gap=2),
                       True: button_icon("chevron-down", C.TEXT_2, ICON["s"], gap=2)}
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(ICON["s"] + SPACE["xs"], SPACE["xs"], 0, 0)
        self.body_lay.setSpacing(SPACE["m"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["xs"])
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.body)
        self.set_expanded(expanded)

    def add(self, w: QWidget | None = None, layout=None) -> None:
        if w is not None:
            self.body_lay.addWidget(w)
        if layout is not None:
            self.body_lay.addLayout(layout)

    def set_expanded(self, on: bool) -> None:
        self.body.setVisible(on)
        ic, sz = self._icons[bool(on)]
        self.btn.setIcon(ic)
        self.btn.setIconSize(sz)
        self.btn.setText(self.title)
        self.btn.setAccessibleDescription("Açık" if on else "Kapalı")

    def toggle(self) -> None:
        self.set_expanded(not self.body.isVisible())


# --- önizleme sahnesi ------------------------------------------------------------------
def paint_scene(p: QPainter, r: QRectF, dark: bool) -> None:
    """Oyun benzeri örnek arka plan: aydınlık (gündüz, parlak gökyüzü) veya karanlık (gece)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = r.width(), r.height()
    sky = QLinearGradient(r.topLeft(), r.bottomLeft())
    if dark:
        sky.setColorAt(0, QColor("#0b1026"))
        sky.setColorAt(0.65, QColor("#2a1f4a"))
        sky.setColorAt(1, QColor("#120c1c"))
    else:
        sky.setColorAt(0, QColor("#7cc4f2"))
        sky.setColorAt(0.6, QColor("#f7d9a6"))
        sky.setColorAt(1, QColor("#d9a066"))
    p.fillRect(r, sky)
    glow = QRadialGradient(QPointF(r.left() + W * 0.72, r.top() + H * 0.42), H * 0.35)
    glow.setColorAt(0, QColor(255, 250, 220, 230 if not dark else 90))
    glow.setColorAt(1, QColor(255, 250, 220, 0))
    p.fillRect(r, glow)
    # şehir silueti
    p.setPen(Qt.PenStyle.NoPen)
    base_y = r.top() + H * 0.70
    xs = [0, .06, .11, .17, .24, .29, .36, .42, .5, .55, .63, .69, .76, .82, .9, .95, 1.0]
    hs = [.18, .28, .16, .35, .22, .30, .14, .26, .40, .20, .31, .17, .27, .23, .33, .19]
    bcol = QColor("#2b2f3a") if not dark else QColor("#05070d")
    for i in range(len(hs)):
        x0, x1 = r.left() + xs[i] * W, r.left() + xs[i + 1] * W
        p.setBrush(bcol)
        p.drawRect(QRectF(x0, base_y - hs[i] * H, x1 - x0 + 1, hs[i] * H + 1))
        if dark:
            p.setBrush(QColor(255, 214, 120, 170))
            for k in range(3):
                p.drawRect(QRectF(x0 + (x1 - x0) * (0.25 + 0.2 * k), base_y - hs[i] * H * (0.4 + 0.15 * k), 3, 3))
    road = QLinearGradient(QPointF(0, base_y), r.bottomLeft())
    road.setColorAt(0, QColor("#3a3d45") if not dark else QColor("#15161c"))
    road.setColorAt(1, QColor("#5b5e66") if not dark else QColor("#22232b"))
    p.fillRect(QRectF(r.left(), base_y, W, r.bottom() - base_y), road)
    p.setBrush(QColor(255, 255, 255, 160 if not dark else 90))
    for k in range(6):
        p.drawRect(QRectF(r.left() + W * (0.05 + k * 0.17), r.top() + H * 0.86, W * 0.08, H * 0.012))
    p.restore()


def _fit_16_9(w: float, h: float) -> QRectF:
    if w / max(1.0, h) > 16 / 9:
        ph = h
        pw = h * 16 / 9
    else:
        pw = w
        ph = w * 9 / 16
    return QRectF((w - pw) / 2, (h - ph) / 2, pw, ph)


STREAM_CAPTION = "1080p yayın oranında önizleme"
SCREEN_CAPTION = "Ekranınızdaki kutu"
BOX_BOTTOM = 0.05  # ekrandaki kutunun varsayılan alt boşluğu (overlay.default_box ile aynı)


class SubtitlePreview(QWidget):
    """16:9 karenin alt kısmı: yazı/arka plan ayarlarının nasıl görüneceği. Kare genişliğe sığdırılır ve
    üstten kırpılır; böylece yazı okunacak kadar büyük kalır ama ekrana oranı gerçektekiyle aynıdır.

    * Yayın (box None): OBS'teki 1080p kare; konum ve genişlik görünümün kendi ayarlarından.
    * Ekran (box = kutunun ekrana oranı): altyazı, ekrandaki kutuyla aynı kuralla kutunun içine çizilir
      (render.paint_subtitle(in_box=True)); kutu ortada ve alta yakın durur, yeri Ana sayfadan ayarlanır."""

    def __init__(self, style: StyleCfg, parent: QWidget | None = None):
        super().__init__(parent)
        self.st = style
        self.dark = False
        self.box: tuple[float, float] | None = None
        self.scale = 1.0
        self.setMinimumHeight(300)
        self.setMaximumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_dark(self, on: bool) -> None:
        self.dark = on
        self.update()

    def set_profile(self, style: StyleCfg, box: tuple[float, float] | None = None, scale: float = 1.0) -> None:
        """Önizlenen görünüm. box verilirse ekrandaki kutu (genişlik, yükseklik; ekrana oranla) çizilir."""
        self.st, self.box, self.scale = style, box, scale
        self.update()

    def caption(self) -> str:
        return STREAM_CAPTION if self.box is None else SCREEN_CAPTION

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        view = QRectF(0, 0, self.width(), self.height())
        fw = view.width()
        fh = fw * 9 / 16
        if fh < view.height():  # dar pencere: tüm kareyi sığdır
            frame = _fit_16_9(view.width(), view.height())
        else:  # kareyi genişliğe sığdır, alt kısmını göster
            frame = QRectF(0, view.height() - fh, fw, fh)
        clip = QPainterPath()
        clip.addRoundedRect(view.intersected(frame), RADIUS["l"], RADIUS["l"])
        p.setClipPath(clip)
        paint_scene(p, frame, self.dark)
        p.translate(frame.topLeft())
        if self.box is None:
            paint_subtitle(p, frame.width(), frame.height(), self.st, SAMPLE_TEXT, SAMPLE_SPEAKER, SAMPLE_ORIGINAL)
        else:
            bw, bh = frame.width() * self.box[0], frame.height() * self.box[1]
            p.translate((frame.width() - bw) / 2, frame.height() * (1 - BOX_BOTTOM) - bh)
            paint_subtitle(p, bw, bh, self.st, SAMPLE_TEXT, SAMPLE_SPEAKER, SAMPLE_ORIGINAL, self.scale,
                           ref_height=frame.height(), in_box=True)
        p.resetTransform()
        p.setClipping(False)
        vis = view.intersected(frame)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(C.LINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(vis.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["l"], RADIUS["l"])
        # köşe etiketi: aydınlık sahnede de okunsun diye koyu zemin üstünde
        f = QFont(F.DISPLAY)
        f.setPixelSize(F.SMALL)
        p.setFont(f)
        text = self.caption()
        fm = QFontMetricsF(f)
        tw, th = fm.horizontalAdvance(text) + 16, fm.height() + 6
        tag = QRectF(vis.right() - tw - 10, vis.top() + 10, tw, th)
        bg = QColor(C.BG)
        bg.setAlphaF(0.72)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(tag, RADIUS["s"], RADIUS["s"])
        p.setPen(QColor(C.TEXT_2))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class PresetCard(QPushButton):
    """Hazır stil düğmesi: stilin küçük bir önizlemesini çizer."""

    def __init__(self, name: str, values: dict, base: StyleCfg, parent: QWidget | None = None):
        super().__init__(parent)
        self.name, self.values, self.base = name, values, base
        self.setFixedSize(QSize(150, 104))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"'{name}' stilini uygula")
        self.setObjectName("preset")

    def matches(self, st: StyleCfg) -> bool:
        for k, v in self.values.items():
            cur = getattr(st, k)
            if isinstance(v, float):
                if abs(float(cur) - v) > 0.02:
                    return False
            elif str(cur).lower() != str(v).lower():
                return False
        return True

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(6, 6, self.width() - 12, self.height() - 32)
        clip = QPainterPath()
        clip.addRoundedRect(r, RADIUS["m"], RADIUS["m"])
        p.save()
        p.setClipPath(clip)
        paint_scene(p, r, False)
        st = dataclasses.replace(self.base, **self.values)
        st.show_original = False
        st.bottom_percent = 16
        st.max_width_percent = 96
        st.font_size = 230
        p.translate(r.topLeft())
        paint_subtitle(p, r.width(), r.height(), st, "Merhaba!")
        p.restore()
        on = self.matches(self.base)
        if on:  # seçili: camgöbeği halka + köşede onay rozeti (yalnız renge dayanmasın)
            p.setPen(QPen(QColor(C.CYAN), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS["l"] - 1, RADIUS["l"] - 1)
            badge = QPointF(r.right() - 10, r.top() + 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.CYAN))
            p.drawEllipse(badge, 8, 8)
            p.drawPixmap(QRectF(badge.x() - 6, badge.y() - 6, 12, 12).toRect(), icons.pixmap("check", 12, C.INVERSE))
        f = QFont(F.DISPLAY_SEMIBOLD if on else F.DISPLAY)
        f.setPixelSize(F.BODY_SIZE)
        p.setFont(f)
        p.setPen(QColor(C.TEXT if on else C.TEXT_2))
        p.drawText(QRectF(0, self.height() - 26, self.width(), 22), Qt.AlignmentFlag.AlignCenter, self.name)
        p.end()
