"""Uygulamanın kendi çizgi ikon seti (QPainter ile çizilir; dış bağımlılık/indirme yok).

Kurallar: 24 birimlik ızgara, 1.75 birim çizgi, yuvarlak uç ve birleşim, dolgu yok (durum noktaları
hariç). Tüm ikonlar aynı ağırlıkta görünmelidir. Emoji veya Unicode simge (▶ ■ ✔ ⚠ ↻ ⓘ) kullanmayın.

Optik ölçüler (ikonların aynı boyda görünmesi için):
* daire biçimli ikonlar r = 9.5 (merkez 12, 12)
* kare biçimli ikonlar 18 × 18 (3 … 21), köşe yarıçapı 2.5
* yatay dikdörtgenler 18 genişlik, 13–15 yükseklik
* iç köşeler `_rpath` ile ~2 birim yuvarlatılır

Kullanım:
    from altyazitr.gui.icons import icon
    btn.setIcon(icon("play", C.INVERSE)); btn.setIconSize(QSize(18, 18))

Yeni ikon eklemek (bu dosyada):
    @_icon("benim-ikonum")
    def _benim(p):  # p: kalemi ayarlanmış, 24 × 24 birime ölçeklenmiş QPainter
        p.drawEllipse(QPointF(12, 12), 9.5, 9.5)
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from altyazitr.gui.tokens import C

Draw = Callable[[QPainter], None]
_REG: dict[str, Draw] = {}

STROKE = 1.75
CIRCLE_R = 9.5


def _icon(name: str):
    def deco(fn: Draw) -> Draw:
        _REG[name] = fn
        return fn
    return deco


def names() -> list[str]:
    return sorted(_REG)


def pixmap(name: str, size: int = 20, color: str | None = None, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(color or C.TEXT_2), STROKE, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _REG.get(name, _REG["help"])(p)
    p.end()
    return pm


def icon(name: str, color: str | None = None, disabled_color: str | None = None) -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32):
        ic.addPixmap(pixmap(name, s, color), QIcon.Mode.Normal)
        ic.addPixmap(pixmap(name, s, disabled_color or C.TEXT_DISABLED), QIcon.Mode.Disabled)
    return ic


# --- çizim yardımcıları -------------------------------------------------------------
Pt = tuple[float, float]


def _poly(p: QPainter, *pts: Pt, close: bool = False) -> None:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _rpath(pts: list[Pt] | tuple[Pt, ...], r: float = 2.0, close: bool = False) -> QPainterPath:
    """Köşeleri ``r`` yarıçapıyla yuvarlatılmış çoklu çizgi (keskin köşeler ikonu olduğundan ağır gösterir)."""
    n = len(pts)

    def corner(i: int) -> tuple[QPointF, QPointF, QPointF]:
        u, v, w = pts[i - 1], pts[i], pts[(i + 1) % n]
        du, dw = math.dist(u, v), math.dist(v, w)
        d = min(r, du / 2, dw / 2)
        a = QPointF(v[0] + (u[0] - v[0]) / du * d, v[1] + (u[1] - v[1]) / du * d)
        b = QPointF(v[0] + (w[0] - v[0]) / dw * d, v[1] + (w[1] - v[1]) / dw * d)
        return a, QPointF(*v), b

    if close:
        start = QPointF((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
        path = QPainterPath(start)
        for i in range(n):
            a, v, b = corner(i)
            path.lineTo(a)
            path.quadTo(v, b)
        path.closeSubpath()
        return path
    path = QPainterPath(QPointF(*pts[0]))
    for i in range(1, n - 1):
        a, v, b = corner(i)
        path.lineTo(a)
        path.quadTo(v, b)
    path.lineTo(QPointF(*pts[-1]))
    return path


def _rpoly(p: QPainter, *pts: Pt, r: float = 2.0, close: bool = False) -> None:
    p.drawPath(_rpath(pts, r, close))


def _circle(p: QPainter, r: float = CIRCLE_R, cx: float = 12, cy: float = 12) -> None:
    p.drawEllipse(QPointF(cx, cy), r, r)


def _dot(p: QPainter, x: float, y: float, r: float = 0.25) -> None:
    """Çizgi kalınlığıyla uyumlu dolu nokta (ünlem/soru işareti noktası, palet boyaları)."""
    p.save()
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(x, y), r, r)
    p.restore()


def _arc(p: QPainter, cx: float, cy: float, r: float, start: float, span: float) -> None:
    """Derece cinsinden yay (Qt yönü: 0° = saat 3, pozitif = saat yönünün tersi)."""
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(rect, start)
    path.arcTo(rect, start, span)
    p.drawPath(path)


def _cut_line(p: QPainter, a: Pt, b: Pt) -> None:
    """Altındaki çizimi açarak (boşluk bırakarak) çapraz çizgi çizer: 'kapalı' ikonları için."""
    p.save()
    gap = QPen(p.pen())
    gap.setWidthF(STROKE + 2.6)
    p.setPen(gap)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    p.drawLine(QPointF(*a), QPointF(*b))
    p.restore()
    p.drawLine(QPointF(*a), QPointF(*b))


# --- gezinme ---------------------------------------------------------------------
@_icon("home")
def _home(p):
    _rpoly(p, (3, 10.5), (12, 3.5), (21, 10.5), r=1.2)
    _rpoly(p, (5.5, 8.8), (5.5, 20), (18.5, 20), (18.5, 8.8), r=2)
    _rpoly(p, (10, 20), (10, 14.5), (14, 14.5), (14, 20), r=1)


@_icon("palette")
def _palette(p):
    path = QPainterPath()
    path.moveTo(12, 2.5)
    path.cubicTo(6.75, 2.5, 2.5, 6.75, 2.5, 12)
    path.cubicTo(2.5, 17.25, 6.75, 21.5, 12, 21.5)
    path.cubicTo(13.4, 21.5, 14.1, 20.4, 13.6, 19.2)
    path.cubicTo(12.9, 17.5, 13.9, 16, 15.7, 16)
    path.lineTo(17.6, 16)
    path.cubicTo(19.8, 16, 21.5, 14.3, 21.5, 12)
    path.cubicTo(21.5, 6.75, 17.25, 2.5, 12, 2.5)
    p.drawPath(path)
    for x, y in ((7.4, 12), (9.2, 7.8), (14.2, 7.4), (17.3, 10.6)):
        _dot(p, x, y, 0.55)


@_icon("languages")
def _languages(p):
    # sol: yazı işareti, sağ: Latin "A" — "çeviri"
    _poly(p, (3, 5.5), (13, 5.5))
    _poly(p, (8, 3), (8, 5.5))
    _poly(p, (5.2, 8.6), (10.8, 14.2))
    _rpoly(p, (3.8, 15), (9.6, 9.2), (11.4, 5.5), r=1.5)
    _rpoly(p, (12.5, 21), (16.75, 11.5), (21, 21), r=1)
    _poly(p, (14, 17.6), (19.5, 17.6))


@_icon("settings")
def _settings(p):
    # 8 dişli çark: trapez dişler, dişler arası iç çember yayı; ortada mil deliği
    teeth, r_out, r_in = 8, 9.6, 7.1
    step = 360 / teeth
    path = QPainterPath()
    for i in range(teeth):
        a = i * step - 90
        pts = ((r_in, a - step * 0.30), (r_out, a - step * 0.17), (r_out, a + step * 0.17),
               (r_in, a + step * 0.30))
        for j, (r, deg) in enumerate(pts):
            pt = QPointF(12 + r * math.cos(math.radians(deg)), 12 + r * math.sin(math.radians(deg)))
            if i == 0 and j == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        # bir sonraki dişe iç çember boyunca
        nxt = a + step - step * 0.30
        rect = QRectF(12 - r_in, 12 - r_in, 2 * r_in, 2 * r_in)
        path.arcTo(rect, -(a + step * 0.30), -(nxt - (a + step * 0.30)))
    path.closeSubpath()
    p.drawPath(path)
    _circle(p, 3)


# --- eylemler ------------------------------------------------------------------------
@_icon("play")
def _play(p):
    _rpoly(p, (7.5, 4.8), (19.5, 12), (7.5, 19.2), r=1.6, close=True)


@_icon("stop")
def _stop(p):
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2.5, 2.5)


@_icon("play-circle")
def _play_circle(p):
    _circle(p)
    _rpoly(p, (10, 8.4), (15.8, 12), (10, 15.6), r=0.9, close=True)


@_icon("crop")
def _crop(p):
    _poly(p, (6.5, 3), (6.5, 15.5))
    _rpoly(p, (6.5, 15.5), (6.5, 17.5), (21, 17.5), r=1.2)
    _rpoly(p, (3, 6.5), (17.5, 6.5), (17.5, 21), r=1.2)


@_icon("monitor")
def _monitor(p):
    p.drawRoundedRect(QRectF(3, 3.5, 18, 13), 2.5, 2.5)
    _poly(p, (12, 16.5), (12, 20.5))
    _poly(p, (8, 20.5), (16, 20.5))


@_icon("app-window")
def _app_window(p):
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2.5, 2.5)
    _poly(p, (3, 9), (21, 9))
    _dot(p, 6.3, 6.75, 0.2)
    _dot(p, 8.9, 6.75, 0.2)


@_icon("refresh")
def _refresh(p):
    # saat yönünde: sağ üstte açık yay + ok ucu
    _arc(p, 12, 12, 8.5, 40, 290)
    _rpoly(p, (20.3, 3.6), (20.3, 8.6), (15.3, 8.6), r=1)


@_icon("reset")
def _reset(p):
    # saat yönünün tersine: sol üstte açık yay + ok ucu
    _arc(p, 12, 12, 8.5, 140, -290)
    _rpoly(p, (3.7, 3.6), (3.7, 8.6), (8.7, 8.6), r=1)


@_icon("eye")
def _eye(p):
    path = QPainterPath(QPointF(2.5, 12))
    path.cubicTo(5, 6.2, 19, 6.2, 21.5, 12)
    path.cubicTo(19, 17.8, 5, 17.8, 2.5, 12)
    p.drawPath(path)
    _circle(p, 2.9)


@_icon("eye-off")
def _eye_off(p):
    _eye(p)
    _cut_line(p, (4, 4), (20, 20))


@_icon("move")
def _move(p):
    _poly(p, (12, 3), (12, 21))
    _poly(p, (3, 12), (21, 12))
    _poly(p, (9.5, 5.5), (12, 3), (14.5, 5.5))
    _poly(p, (9.5, 18.5), (12, 21), (14.5, 18.5))
    _poly(p, (5.5, 9.5), (3, 12), (5.5, 14.5))
    _poly(p, (18.5, 9.5), (21, 12), (18.5, 14.5))


@_icon("copy")
def _copy(p):
    p.drawRoundedRect(QRectF(8.5, 8.5, 12.5, 12.5), 2.5, 2.5)
    _rpoly(p, (15.5, 8.5), (15.5, 3), (3, 3), (3, 15.5), (8.5, 15.5), r=2.5)


@_icon("external")
def _external(p):
    _poly(p, (14, 3.5), (20.5, 3.5), (20.5, 10))
    _poly(p, (20.5, 3.5), (11, 13))
    _rpoly(p, (17.5, 14), (17.5, 20.5), (3.5, 20.5), (3.5, 6.5), (10, 6.5), r=2.5)


@_icon("download")
def _download(p):
    _poly(p, (12, 3.5), (12, 15))
    _poly(p, (7.5, 10.5), (12, 15), (16.5, 10.5))
    _rpoly(p, (3.5, 15), (3.5, 20.5), (20.5, 20.5), (20.5, 15), r=2.5)


@_icon("folder")
def _folder(p):
    _rpoly(p, (3, 4.5), (3, 19.5), (21, 19.5), (21, 7.5), (12.3, 7.5), (10.2, 4.5), r=2.2, close=True)


@_icon("file-text")
def _file_text(p):
    _rpoly(p, (14, 2.5), (4.5, 2.5), (4.5, 21.5), (19.5, 21.5), (19.5, 8), r=2.2)
    _rpoly(p, (14, 2.5), (19.5, 8), r=0.1)
    _rpoly(p, (14, 2.5), (14, 8), (19.5, 8), r=1)
    _poly(p, (8.5, 13), (15.5, 13))
    _poly(p, (8.5, 17), (13, 17))


@_icon("trash")
def _trash(p):
    _poly(p, (3.5, 6.5), (20.5, 6.5))
    _rpoly(p, (9, 6.5), (9, 3.5), (15, 3.5), (15, 6.5), r=1)
    _rpoly(p, (5.5, 6.5), (6.6, 20.5), (17.4, 20.5), (18.5, 6.5), r=1.8)
    _poly(p, (10, 11), (10, 16.5))
    _poly(p, (14, 11), (14, 16.5))


# --- durum ---------------------------------------------------------------------------
@_icon("help")
def _help(p):
    _circle(p)
    path = QPainterPath(QPointF(9.3, 9.3))
    path.cubicTo(9.6, 6.6, 14.7, 6.6, 14.7, 9.6)
    path.cubicTo(14.7, 11.7, 12, 11.9, 12, 13.8)
    p.drawPath(path)
    _dot(p, 12, 17)


@_icon("info")
def _info(p):
    _circle(p)
    _poly(p, (12, 11), (12, 16.5))
    _dot(p, 12, 7.8)


@_icon("circle-check")
def _circle_check(p):
    _circle(p)
    _poly(p, (8.2, 12.3), (10.9, 15), (15.9, 9.4))


@_icon("circle-alert")
def _circle_alert(p):
    _circle(p)
    _poly(p, (12, 7.4), (12, 12.8))
    _dot(p, 12, 16.4)


@_icon("circle-x")
def _circle_x(p):
    _circle(p)
    _poly(p, (9, 9), (15, 15))
    _poly(p, (15, 9), (9, 15))


@_icon("check")
def _check(p):
    _poly(p, (5, 12.5), (10, 17.5), (19.5, 7))


@_icon("x")
def _x(p):
    _poly(p, (6, 6), (18, 18))
    _poly(p, (18, 6), (6, 18))


@_icon("alert")
def _alert(p):
    _rpoly(p, (12, 3), (21.8, 20), (2.2, 20), r=2.2, close=True)
    _poly(p, (12, 9.3), (12, 13.6))
    _dot(p, 12, 16.7)


@_icon("plus")
def _plus(p):
    _poly(p, (12, 5), (12, 19))
    _poly(p, (5, 12), (19, 12))


@_icon("minus")
def _minus(p):
    _poly(p, (5, 12), (19, 12))


@_icon("tag")
def _tag(p):
    """Etiket (sürüm): sol üstü kare, sağ alta sivri; delikli."""
    _rpoly(p, (3.5, 3.5), (11.5, 3.5), (20.5, 12.5), (12.5, 20.5), (3.5, 11.5), r=2.0, close=True)
    _circle(p, 1.6, 8, 8)


@_icon("dot")
def _dot_icon(p):
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(12, 12), 4, 4)


# --- yayın / altyazı ---------------------------------------------------------------------
@_icon("broadcast")
def _broadcast(p):
    _circle(p, 2)
    for r in (5.5, 9):
        _arc(p, 12, 12, r, 35, 110)
        _arc(p, 12, 12, r, 215, 110)


@_icon("captions")
def _captions(p):
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2.5, 2.5)
    _poly(p, (6.5, 12.5), (11, 12.5))
    _poly(p, (14, 12.5), (17.5, 12.5))
    _poly(p, (6.5, 16), (9.5, 16))
    _poly(p, (12.5, 16), (17.5, 16))


@_icon("sun")
def _sun(p):
    _circle(p, 4)
    for i in range(8):
        a = math.radians(i * 45)
        _poly(p, (12 + 7.2 * math.cos(a), 12 + 7.2 * math.sin(a)), (12 + 9.4 * math.cos(a), 12 + 9.4 * math.sin(a)))


@_icon("moon")
def _moon(p):
    path = QPainterPath()
    path.moveTo(20, 14.6)
    path.cubicTo(15.2, 16.8, 8.2, 12.6, 10.4, 3.8)
    path.cubicTo(5.2, 4.9, 2.5, 10.9, 5.2, 15.7)
    path.cubicTo(7.9, 20.5, 15.8, 21.2, 20, 14.6)
    p.drawPath(path)


@_icon("keyboard")
def _keyboard(p):
    p.drawRoundedRect(QRectF(2.5, 5.5, 19, 13), 2.5, 2.5)
    for x in (6.5, 9.5, 12.5, 15.5):
        _dot(p, x, 9.5, 0.2)
    for x in (8, 11, 14, 17):
        _dot(p, x, 12.2, 0.2)
    _poly(p, (8, 15), (16, 15))


@_icon("power")
def _power(p):
    _arc(p, 12, 12.8, 8.3, 125, 290)
    _poly(p, (12, 2.8), (12, 11.5))


# --- düzen -----------------------------------------------------------------------------------
@_icon("chevron-down")
def _chev_down(p):
    _poly(p, (6, 9), (12, 15), (18, 9))


@_icon("chevron-right")
def _chev_right(p):
    _poly(p, (9, 6), (15, 12), (9, 18))
