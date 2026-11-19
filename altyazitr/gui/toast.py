"""Pencere içi kısa bildirim ("toast"): sağ altta belirir, birkaç saniye sonra kendiliğinden kaybolur.

Modal değildir, odak çalmaz, tek satırdır. En fazla üç bildirim üst üste dizilir; en yenisi en alttadır.
Belirme/kaybolma süresi `tokens.motion_ms()` ile gelir (Windows'ta animasyonlar kapalıysa anında).

Kullanım (ana pencere):
    self._toasts = ToastHost(self)
    self._toasts.show("Bölge kaydedildi", "success")
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from altyazitr.gui.icons import pixmap
from altyazitr.gui.tokens import RADIUS, SPACE, C, F, motion_ms

log = logging.getLogger(__name__)

# tür → (ikon, vurgu rengi, ekran okuyucu için ad)
KINDS: dict[str, tuple[str, str, str]] = {
    "info": ("info", C.CYAN, "Bilgi"),
    "success": ("circle-check", C.TURQUOISE, "Tamam"),
    "warn": ("circle-alert", C.ORANGE, "Uyarı"),
    "error": ("circle-x", C.DANGER, "Hata"),
}
_ALIASES = {"warning": "warn", "ok": "success", "danger": "error"}

TIMEOUT_MS = 3000
TIMEOUT_LONG_MS = 4500   # uyarı ve hata biraz daha uzun kalır
MAX_VISIBLE = 3
MAX_WIDTH = 440
HEIGHT = 44
ICON = 18


def _kind(kind: str) -> str:
    kind = _ALIASES.get(kind, kind)
    return kind if kind in KINDS else "info"


def _body_font() -> QFont:
    f = QFont(F.BODY)
    f.setFamilies([F.BODY, F.BODY_FALLBACK])
    f.setPixelSize(F.BODY_SIZE)
    return f


class Toast(QWidget):
    """Tek bir bildirim. Kendini çizer (QSS'e bağlı değildir); tıklanınca kapanır, üzerine gelince bekler.
    `clicked`: kapanmadan hemen önce (bildirimin bir eylemi varsa, ör. "güncellemek için tıklayın")."""

    closed = Signal()
    clicked = Signal()

    def __init__(self, text: str, kind: str = "info", parent: QWidget | None = None,
                 timeout_ms: int | None = None):
        super().__init__(parent)
        self.kind = _kind(kind)
        self.text = " ".join(str(text).split())  # tek satır
        icon_name, self._accent, spoken = KINDS[self.kind]
        self._icon = pixmap(icon_name, ICON, self._accent)
        self._font = _body_font()
        self._opacity = 0.0
        self._closing = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{spoken}: {self.text}")
        self.setToolTip(self.text)

        self._timer = QTimer(self, singleShot=True, timeout=self.dismiss)
        if timeout_ms is None:
            timeout_ms = TIMEOUT_LONG_MS if self.kind in ("warn", "error") else TIMEOUT_MS
        self._timeout = timeout_ms

        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_opacity)
        self._anim.finished.connect(self._anim_done)
        self._fit(MAX_WIDTH)

    # --- boyut ------------------------------------------------------------------------
    def _text_x(self) -> int:
        return SPACE["l"] + ICON + SPACE["m"]

    def _fit(self, max_w: int) -> None:
        fm = QFontMetrics(self._font)
        w = self._text_x() + fm.horizontalAdvance(self.text) + SPACE["l"] + 2
        self.resize(max(160, min(int(w), max(160, max_w))), HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    # --- gösterme / kapatma -----------------------------------------------------------------
    def popup(self) -> None:
        self.show()
        self.raise_()
        self._animate(1.0, motion_ms("base"))
        self._restart_timer()
        _announce(self, self.accessibleName())

    def bump(self) -> None:
        """Aynı bildirim yeniden gelirse yenisini açmak yerine süresini tazeler."""
        if self._closing:
            return
        self._animate(1.0, motion_ms("fast"))
        self._restart_timer()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._animate(0.0, motion_ms("base"))

    def _restart_timer(self, ms: int | None = None) -> None:
        if self._timeout > 0:
            self._timer.start(ms if ms is not None else self._timeout)

    def _animate(self, target: float, ms: int) -> None:
        self._anim.stop()
        if ms <= 0:
            self._set_opacity(target)
            self._anim_done()
            return
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(target)
        self._anim.setDuration(ms)
        self._anim.start()

    def _set_opacity(self, v) -> None:
        self._opacity = float(v)
        self.update()

    def _anim_done(self) -> None:
        if self._closing and self._opacity <= 0.001:
            self.hide()
            self.closed.emit()
            self.deleteLater()

    # --- etkileşim ------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            if not self._closing:
                self.clicked.emit()
            self.dismiss()
        e.accept()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()  # okurken kaybolmasın
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if not self._closing:
            self._restart_timer(1500)
        super().leaveEvent(e)

    # --- çizim ----------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._opacity <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._opacity)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS["l"], RADIUS["l"])
        p.fillPath(path, QColor(C.RAISED))
        border = QColor(self._accent)
        border.setAlphaF(0.45)
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        y_icon = (self.height() - ICON) / 2
        p.drawPixmap(QPointF(SPACE["l"], y_icon), self._icon)

        p.setFont(self._font)
        p.setPen(QColor(C.TEXT))
        tx = self._text_x()
        tr = QRectF(tx, 0, self.width() - tx - SPACE["l"], self.height())
        text = QFontMetrics(self._font).elidedText(self.text, Qt.TextElideMode.ElideRight, int(tr.width()))
        p.drawText(tr, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.end()


class ToastHost(QObject):
    """Bir pencerenin sağ alt köşesinde bildirimleri dizer ve pencere boyutu değişince yerlerini günceller."""

    def __init__(self, host: QWidget, margin: int = SPACE["xl"], spacing: int = SPACE["s"]):
        super().__init__(host)
        self._host = host
        self._margin = margin
        self._spacing = spacing
        self._toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, text: str, kind: str = "info", timeout_ms: int | None = None) -> Toast | None:
        text = " ".join(str(text or "").split())
        if not text:
            return None
        kind = _kind(kind)
        live = [t for t in self._toasts if not t._closing]
        if live and live[-1].text == text and live[-1].kind == kind:
            live[-1].bump()
            return live[-1]
        while len(live) >= MAX_VISIBLE:  # en eskisi yer açar
            oldest = live.pop(0)
            oldest.dismiss()
            self._forget(oldest)
        t = Toast(text, kind, self._host, timeout_ms)
        t.closed.connect(lambda t=t: self._forget(t))
        self._toasts.append(t)
        self._layout()
        t.popup()
        return t

    def _forget(self, t: Toast) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
            self._layout()

    def _layout(self) -> None:
        host = self._host
        max_w = max(160, min(MAX_WIDTH, host.width() - 2 * self._margin))
        y = host.height() - self._margin
        for t in reversed(self._toasts):  # en yenisi en altta
            t._fit(max_w)
            y -= t.height()
            t.move(host.width() - self._margin - t.width(), y)
            t.raise_()
            y -= self._spacing

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self._host and self._toasts and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                                                                QEvent.Type.LayoutRequest):
            self._layout()
        return False


def _announce(widget: QWidget, text: str) -> None:
    """Ekran okuyuculara bildirimi duyurur (Qt 6.8+; yoksa sessizce geçer)."""
    try:
        from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent

        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(widget, text))
    except Exception:  # eski Qt: duyuru olayı yok; bildirim yine görünür
        log.debug("Erişilebilirlik duyurusu yapılamadı", exc_info=True)
