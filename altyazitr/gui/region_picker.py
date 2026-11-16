"""Canlı ekran bölgesi seçici (Ekran Alıntısı Aracı benzeri).

Her monitörün üstüne yarı karartılmış bir katman açılır; seçilen alan net kalır ve altındaki
oyun canlı olarak görünmeye devam eder. Seçim fiziksel piksel olarak döndürülür.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from altyazitr.gui import icons
from altyazitr.gui.dialogs import body_font, display_font
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F, qcolor

MIN_W, MIN_H = 24, 12


def logical_to_physical(screen: QScreen, r: QRect) -> list[int]:
    """Qt6 (Windows): ekranın sol-üst köşesi yerel koordinatla aynıdır, boyut dpr ile ölçeklenir."""
    g, dpr = screen.geometry(), screen.devicePixelRatio() or 1.0
    return [int(round(g.x() + r.x() * dpr)), int(round(g.y() + r.y() * dpr)),
            int(round(r.width() * dpr)), int(round(r.height() * dpr))]


def physical_to_local(screen: QScreen, rect: list[int]) -> QRect | None:
    if len(rect) != 4:
        return None
    g, dpr = screen.geometry(), screen.devicePixelRatio() or 1.0
    x, y = (rect[0] - g.x()) / dpr, (rect[1] - g.y()) / dpr
    r = QRect(int(round(x)), int(round(y)), int(round(rect[2] / dpr)), int(round(rect[3] / dpr)))
    return r if QRect(0, 0, g.width(), g.height()).intersects(r) else None


def paint_selection_frame(p: QPainter, r: QRectF, color: str = C.CYAN) -> None:
    """Seçim çerçevesi: ince düz çizgi + köşelerde kısa kalın L işaretleri (kırpma işareti).
    r seçimin kendisidir; çizgiler dışına taşar, seçilen pikselleri örtmez."""
    p.setPen(QPen(QColor(color), 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(r.adjusted(-0.75, -0.75, 0.75, 0.75))
    o = r.adjusted(-1.5, -1.5, 1.5, 1.5)
    ln = min(14.0, o.width() / 3, o.height() / 3)
    p.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    for x, y, dx, dy in ((o.left(), o.top(), 1, 1), (o.right(), o.top(), -1, 1),
                         (o.left(), o.bottom(), 1, -1), (o.right(), o.bottom(), -1, -1)):
        path = QPainterPath(QPointF(x + dx * ln, y))
        path.lineTo(QPointF(x, y))
        path.lineTo(QPointF(x, y + dy * ln))
        p.drawPath(path)


class _PickWindow(QWidget):
    def __init__(self, picker: ScreenRegionPicker, screen: QScreen, current: list[int]):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.picker = picker
        self.scr = screen
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(screen.geometry())
        self.old = physical_to_local(screen, current)
        self.origin = QPoint()
        self.sel = QRect()
        self.phase = "idle"  # idle | drag | done

        self.bar = QFrame(self)
        self.bar.setObjectName("pickBar")
        self.bar.setStyleSheet(f"QFrame#pickBar {{ background: {C.PANEL}; border: 1px solid {C.LINE_STRONG};"
                               f" border-radius: {RADIUS['l']}px; }}")
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(6)
        ok = QPushButton("Onayla")
        ok.setObjectName("primary")
        ok.setIcon(icons.icon("check", C.INVERSE))
        ok.setToolTip("Bu bölgeyi kullan (Enter)")
        ok.clicked.connect(self.confirm)
        again = QPushButton("Yeniden seç")
        again.setIcon(icons.icon("refresh", C.TEXT_2))
        again.setToolTip("Seçimi temizleyip yeniden çizin")
        again.clicked.connect(self.reset)
        cancel = QPushButton("İptal")
        cancel.setObjectName("ghost")
        cancel.setIcon(icons.icon("x", C.TEXT_2))
        cancel.setToolTip("Vazgeç (Esc ya da sağ tık)")
        cancel.clicked.connect(lambda: self.picker.finish(None))
        for b in (ok, again, cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIconSize(QSize(ICON["s"], ICON["s"]))
            h.addWidget(b)
        self.bar.adjustSize()
        self.bar.hide()

    # --- seçim ----------------------------------------------------------------
    def reset(self) -> None:
        self.phase = "idle"
        self.sel = QRect()
        self.bar.hide()
        self.update()

    def confirm(self) -> None:
        if self.phase == "done" and self.sel.isValid():
            self.picker.finish(logical_to_physical(self.scr, self.sel))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.picker.finish(None)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.picker.clear_others(self)
        self.bar.hide()
        self.phase = "drag"
        self.origin = e.position().toPoint()
        self.sel = QRect(self.origin, self.origin)
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.phase == "drag":
            p = e.position().toPoint()
            x0, x1 = sorted((self.origin.x(), p.x()))
            y0, y1 = sorted((self.origin.y(), p.y()))
            self.sel = QRect(x0, y0, x1 - x0, y1 - y0) & self.rect()
            self.update()

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self.phase != "drag":
            return
        if self.sel.width() < MIN_W or self.sel.height() < MIN_H:
            self.reset()
            return
        self.phase = "done"
        bw, bh = self.bar.sizeHint().width(), self.bar.sizeHint().height()
        x = min(max(8, self.sel.right() - bw), self.width() - bw - 8)
        y = self.sel.bottom() + 10
        if y + bh > self.height() - 8:
            y = max(8, self.sel.top() - bh - 10)
        self.bar.setGeometry(x, y, bw, bh)
        self.bar.show()
        self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.picker.finish(None)
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()

    # --- çizim --------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        dim = QPainterPath()
        dim.addRect(full)
        active = self.sel.isValid() and self.phase != "idle"
        if active:
            hole = QPainterPath()
            hole.addRect(QRectF(self.sel))
            dim = dim.subtracted(hole)
            p.fillRect(self.sel, QColor(0, 0, 0, 1))  # tamamen şeffaf olmasın: tıklamalar oyuna gitmesin
        p.fillPath(dim, qcolor(C.BG, 0.59))  # gece yarısı tonunda karartma

        if self.old is not None and self.phase == "idle":
            self._paint_old(p)
        if active:
            paint_selection_frame(p, QRectF(self.sel))
            self._paint_size(p)
        if self.phase == "idle":
            self._paint_hint(p)
        p.end()

    def _paint_old(self, p: QPainter) -> None:
        """Kayıtlı bölge: kesik çizgi ve küçük 'Mevcut bölge' etiketi."""
        pen = QPen(qcolor(C.TEXT_2, 0.82), 1.25, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(self.old).adjusted(0.5, 0.5, -0.5, -0.5))
        text = "Mevcut bölge"
        p.setFont(body_font(F.SMALL))
        tw, h = p.fontMetrics().horizontalAdvance(text) + 2 * SPACE["s"], 22
        room = self.old.top() > h + 2 * SPACE["xs"]
        y = self.old.top() - h - SPACE["xs"] if room else self.old.top() + SPACE["xs"]
        pill = QRectF(self.old.left(), y, tw, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(C.PANEL, 0.92))
        p.drawRoundedRect(pill, RADIUS["s"], RADIUS["s"])
        p.setPen(QColor(C.TEXT_2))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_size(self, p: QPainter) -> None:
        """Seçimin fiziksel piksel boyutu, Bahnschrift rakamlarla, çerçevenin sol üstünde."""
        phys = logical_to_physical(self.scr, self.sel)
        label = f"{phys[2]} × {phys[3]}"
        p.setFont(display_font(F.BODY_SIZE))
        tw, h, gap = p.fontMetrics().horizontalAdvance(label) + 2 * SPACE["s"] + 2, 24, SPACE["s"]
        room = self.sel.top() > h + 2 * gap
        pill = QRectF(self.sel.left() - 1.5 if room else self.sel.left() + gap,
                      self.sel.top() - h - gap if room else self.sel.top() + gap, tw, h)
        p.setPen(QPen(qcolor(C.CYAN, 0.47), 1))
        p.setBrush(qcolor(C.PANEL, 0.95))
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["s"], RADIUS["s"])
        p.setPen(QColor(C.TEXT))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_hint(self, p: QPainter) -> None:
        """Üst ortada yönerge: ikon, 'Çevrilecek alanı fareyle seçin', Esc tuşu ve 'iptal'."""
        msg, key, action = "Çevrilecek alanı fareyle seçin", "Esc", "iptal"
        f_msg, f_key, f_act = body_font(F.BODY_SIZE + 3), display_font(F.SMALL), body_font(F.BODY_SIZE)
        p.setFont(f_msg)
        w_msg = p.fontMetrics().horizontalAdvance(msg)
        p.setFont(f_key)
        w_key = p.fontMetrics().horizontalAdvance(key) + 2 * SPACE["s"]
        p.setFont(f_act)
        w_act = p.fontMetrics().horizontalAdvance(action)
        icon_sz, pad, h = ICON["m"], SPACE["l"] + 2, 48
        inner = icon_sz + SPACE["m"] + w_msg + SPACE["xl"] + w_key + SPACE["s"] + w_act
        r = QRectF(round((self.width() - inner - 2 * pad) / 2), SPACE["xxl"], inner + 2 * pad, h)

        p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.setBrush(qcolor(C.PANEL, 0.96))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["l"], RADIUS["l"])

        x, cy = r.left() + pad, r.center().y()
        p.drawPixmap(QPointF(x, cy - icon_sz / 2), icons.pixmap("crop", icon_sz, C.CYAN))
        x += icon_sz + SPACE["m"]
        left_mid = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        p.setFont(f_msg)
        p.setPen(QColor(C.TEXT))
        p.drawText(QRectF(x, r.top(), w_msg + 2, h), left_mid, msg)
        x += w_msg + SPACE["xl"]
        cap = QRectF(x, cy - 11, w_key, 22)
        p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.setBrush(QColor(C.RAISED))
        p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["s"], RADIUS["s"])
        p.setFont(f_key)
        p.setPen(QColor(C.TEXT_2))
        p.drawText(cap, Qt.AlignmentFlag.AlignCenter, key)
        x += w_key + SPACE["s"]
        p.setFont(f_act)
        p.drawText(QRectF(x, r.top(), w_act + 2, h), left_mid, action)


class ScreenRegionPicker(QObject):
    """Tüm monitörlerde seçim katmanını açar; bittiğinde finished(rect | None) yayar."""

    finished = Signal(object)

    def __init__(self, current: list[int] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._wins = [_PickWindow(self, scr, list(current or [])) for scr in QGuiApplication.screens()]
        self._done = False

    def start(self) -> None:
        under = QGuiApplication.screenAt(QCursor.pos())
        for w in self._wins:
            w.show()
            w.raise_()
        for w in self._wins:
            if w.scr is under:
                w.activateWindow()
                w.setFocus()

    def clear_others(self, src: _PickWindow) -> None:
        for w in self._wins:
            if w is not src and w.phase != "idle":
                w.reset()

    def finish(self, rect: list[int] | None) -> None:
        if self._done:
            return
        self._done = True
        for w in self._wins:
            w.close()
        self.finished.emit(rect)
