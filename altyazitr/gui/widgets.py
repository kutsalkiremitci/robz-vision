"""Ortak arayüz parçaları ve ayar bağlama yardımcıları."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from altyazitr.gui.tokens import RADIUS, SPACE, C, F

Changed = Callable[[], None]

# Bahnschrift ile yazılan etiket stilleri (gui/theme.py). Seçilebilir QLabel metni QTextDocument ile
# çizer ve satır üstünü kırpar; Bahnschrift'te "İ" noktası kaybolur. Bu yüzden bunlar seçilebilir yapılmaz.
DISPLAY_LABELS = frozenset({"pageTitle", "section", "status", "value", "pill", "pillOn", "pillWarn"})


def label(text: str, obj: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if wrap and obj not in DISPLAY_LABELS:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class Card(QFrame):
    """Sayfa bölümü: kutu değil; üstte 1 px ayırıcı çizgi, başlık ve boşluk (QFrame#card)."""

    def __init__(self, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, SPACE["xl"] - 4, 0, 0)
        self.lay.setSpacing(SPACE["m"])
        if title:
            self.lay.addWidget(label(title, "section"))

    def add(self, w: QWidget | None = None, layout=None) -> None:
        if w is not None:
            self.lay.addWidget(w)
        if layout is not None:
            self.lay.addLayout(layout)


def page(title: str | None) -> tuple[QScrollArea, QVBoxLayout]:
    """Kaydırılabilir sayfa iskeleti: 32 px kenar boşluğu, bölümler arası 24 px.
    title None: başlık kaydırılan alana konmaz (sayfa onu kendi sabit üst bandına yerleştirir)."""
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    m = SPACE["xxl"]
    lay.setContentsMargins(m, m - 6, m, m)
    lay.setSpacing(SPACE["xl"])
    if title:
        head = QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE["xs"])
        head.addWidget(label(title, "pageTitle"))
        lay.addLayout(head)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(inner)
    return sa, lay


def row(*widgets, stretch_last: bool = False) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(SPACE["s"])
    for w in widgets:
        if isinstance(w, int):
            h.addSpacing(w)
        elif w is None:
            h.addStretch(1)
        elif isinstance(w, (QLabel, QLineEdit)) and (not isinstance(w, QLabel) or w.wordWrap()):
            h.addWidget(w, 3)  # metin/alanlar boşluğu doldursun, dar sütunda kırılmasın
        else:
            h.addWidget(w)
    if stretch_last:
        h.addStretch(1)
    return h


def button_icon(name: str, color: str | None = None, size: int = 16, gap: int = 4,
                on_color: str | None = None) -> tuple[QIcon, QSize]:
    """Düğme ikonu + ikon boyutu. Qt ikon ile metin arasına yalnızca 4 px koyar; bu ikon sağında
    `gap` px saydam boşluk taşır, böylece toplam aralık ~8 px olur. Kullanım:
        ic, sz = button_icon("play", C.INVERSE); btn.setIcon(ic); btn.setIconSize(sz)
    `on_color` verilirse seçili (checked) durum için ayrı renk kullanılır."""
    from altyazitr.gui import icons

    def padded(col: str, scale: int) -> QPixmap:
        pm = QPixmap((size + gap) * scale, size * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.drawPixmap(0, 0, icons.pixmap(name, size, col, dpr=scale))
        p.end()
        return pm

    ic = QIcon()
    for scale in (1, 2):
        ic.addPixmap(padded(color or C.TEXT_2, scale), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(padded(on_color or color or C.TEXT_2, scale), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(padded(C.TEXT_DISABLED, scale), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(padded(C.TEXT_DISABLED, scale), QIcon.Mode.Disabled, QIcon.State.On)
    return ic, QSize(size + gap, size)


def color_chip_qss(color: str, ring: str | None = None) -> str:
    """Kullanıcının seçtiği rengi zemin yapan düğme stili (renk değeri Bahnschrift ile yazılır).
    `ring` verilirse kalın seçim halkası çizilir."""
    fg = C.INVERSE if QColor(color).lightness() > 140 else C.TEXT
    border = f"2px solid {ring}" if ring else f"1px solid {C.LINE_STRONG}"
    pad = "5px 11px" if ring else "6px 12px"
    return (f"QPushButton {{ background: {color}; color: {fg}; border: {border}; border-radius: {RADIUS['m']}px; "
            f"padding: {pad}; font-family: '{F.DISPLAY}'; font-size: {F.BUTTON - 1}px; }}"
            f"QPushButton:hover {{ border-color: {ring or C.TEXT_3}; }}"
            f"QPushButton[kbfocus=\"true\"]:focus {{ border: 2px solid {C.CYAN}; padding: 5px 11px; }}")


# --- ayar bağlama yardımcıları --------------------------------------------
def bind_check(text: str, obj: Any, attr: str, on_change: Changed, tip: str = "") -> QCheckBox:
    w = QCheckBox(text)
    w.setChecked(bool(getattr(obj, attr)))
    if tip:
        w.setToolTip(tip)

    def ch(v):
        setattr(obj, attr, bool(v))
        on_change()

    w.toggled.connect(ch)
    return w


def bind_spin(obj: Any, attr: str, lo: float, hi: float, on_change: Changed, step: float = 1,
              suffix: str = "", decimals: int = 0) -> QSpinBox | QDoubleSpinBox:
    if decimals:
        w: QSpinBox | QDoubleSpinBox = QDoubleSpinBox()
        w.setDecimals(decimals)
    else:
        w = QSpinBox()
    w.setRange(lo, hi)  # type: ignore[arg-type]
    w.setSingleStep(step)  # type: ignore[arg-type]
    if suffix:
        w.setSuffix(suffix)
    w.setValue(getattr(obj, attr))
    w.setMinimumWidth(110)
    # QSS'in yatay iç boşluğu sayı kutusunun metin alanına uygulanmıyor; metni kenardan ayır
    w.lineEdit().setTextMargins(SPACE["s"] - 2, 0, 0, 0)

    def ch(v):
        setattr(obj, attr, float(v) if decimals else int(v))
        on_change()

    w.valueChanged.connect(ch)
    return w


def bind_combo(obj: Any, attr: str, items: list[tuple[str, Any]], on_change: Changed) -> QComboBox:
    w = QComboBox()
    for text, data in items:
        w.addItem(text, data)
    cur = getattr(obj, attr)
    idx = next((i for i, (_, d) in enumerate(items) if d == cur), 0)
    w.setCurrentIndex(idx)

    def ch(i):
        setattr(obj, attr, w.itemData(i))
        on_change()

    w.currentIndexChanged.connect(ch)
    return w


def bind_line(obj: Any, attr: str, on_change: Changed, placeholder: str = "", password: bool = False) -> QLineEdit:
    w = QLineEdit(str(getattr(obj, attr)))
    w.setMinimumWidth(340)
    if placeholder:
        w.setPlaceholderText(placeholder)
    if password:
        w.setEchoMode(QLineEdit.EchoMode.Password)

    def ch():
        setattr(obj, attr, w.text())
        on_change()

    w.editingFinished.connect(ch)
    return w
