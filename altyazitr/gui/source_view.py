"""Ana sayfanın canlı kaynak önizlemesi (OBS önizlemesi gibi): kaynağın tamamı, üstünde okunan bölge. Bölge doğrudan
önizlemenin üzerinde çizilir, taşınır ve boyutlandırılır; tam ekran seçiciye gitmek gerekmez.

* SourceView: çizim ve fare/klavye etkileşimi. Bölge kaynağa oransaldır: (x, y, genişlik, yükseklik), 0..1.
  Sürüklerken `edited`, bırakınca (ya da ok tuşlarıyla) `committed` yayılır; kaydetmek sayfanın işidir.
* SourceGrabber: kaynağın küçültülmüş karesini arka planda, düşük hızla alır. Ekran modunda bir monitörün tamamı
  (mss, fiziksel piksel), pencere modunda pencerenin istemci alanı (Windows Graphics Capture). Çeviri sürerken
  pencere modunda işlem hattının kendi yakalaması paylaşılır (`borrow`); ikinci bir yakalama açılmaz.
  Sayfa görünmüyorken ya da pencere simge durumundayken sayfa onu durdurur (bkz. home_page).

Gizlilik: önizleme yalnız ekranda gösterilir; hiçbir yere kaydedilmez ve gönderilmez.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from altyazitr.gui.tokens import RADIUS, SPACE, C, F, qcolor

log = logging.getLogger(__name__)

Frac = tuple[float, float, float, float]
Mon = tuple[int, int, int, int]  # monitör: sol, üst, genişlik, yükseklik (fiziksel piksel)

MIN_FRAC = 0.01        # bölgenin en küçük genişliği / yüksekliği (kaynağa oranla)
KEY_STEP = 0.005       # ok tuşlarıyla adım (Shift: boyut)
HANDLE_PX = 9          # tutamaç karesi
GRAB_PX = 10           # kenar/köşe yakalama payı
PREVIEW_MAX_W = 1280   # önizleme karesi en çok bu genişlikte tutulur (HiDPI'da da keskin, belleği şişirmez)
IDLE_S, RUNNING_S = 0.25, 0.5  # boşta 4, çeviri sürerken 2 kare/sn


# --- saf yardımcılar (test edilebilir) ---------------------------------------------------------------
def clamp_frac(r: Sequence[float], min_frac: float = MIN_FRAC) -> Frac:
    """Bölgeyi kaynağın içine sığdırır ve en küçük boyutu korur."""
    x, y, w, h = (float(v) for v in r)
    w = min(1.0, max(min_frac, w))
    h = min(1.0, max(min_frac, h))
    x = min(max(0.0, x), 1.0 - w)
    y = min(max(0.0, y), 1.0 - h)
    return x, y, w, h


def rect_to_frac(rect: Sequence[int], mon: Mon) -> Frac | None:
    """Ekran bölgesinin (fiziksel piksel) monitöre oranı; bölgenin ortası bu monitörde değilse None."""
    if len(rect) != 4 or mon[2] <= 0 or mon[3] <= 0:
        return None
    x, y, w, h = (float(v) for v in rect)
    cx, cy = x + w / 2, y + h / 2
    if not (mon[0] <= cx < mon[0] + mon[2] and mon[1] <= cy < mon[1] + mon[3]):
        return None
    return clamp_frac(((x - mon[0]) / mon[2], (y - mon[1]) / mon[3], w / mon[2], h / mon[3]), 0.0)


def frac_to_rect(frac: Sequence[float], mon: Mon) -> list[int]:
    """Oransal bölgenin ekrandaki yeri [x, y, genişlik, yükseklik] (fiziksel piksel)."""
    x, y, w, h = clamp_frac(frac)
    return [int(round(mon[0] + x * mon[2])), int(round(mon[1] + y * mon[3])),
            max(1, int(round(w * mon[2]))), max(1, int(round(h * mon[3])))]


def monitor_index_for(rect: Sequence[int], mons: Sequence[Mon]) -> int:
    """Bölgenin ortasını içeren monitörün sırası; yoksa 0 (birincil)."""
    for i, m in enumerate(mons):
        if rect_to_frac(rect, m) is not None:
            return i
    return 0


def list_monitors() -> list[Mon]:
    """Monitörler (fiziksel piksel), mss sırasıyla; okunamazsa boş."""
    try:
        from altyazitr.capture import _new_mss

        with _new_mss() as sct:
            return [(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors[1:]]
    except Exception:
        log.debug("Monitörler okunamadı", exc_info=True)
        return []


def drag_region(kind: str, start: Frac, dx: float, dy: float) -> Frac:
    """Sürükleme: kind "move" ya da kenar/köşe ("n", "se" …); dx, dy kaynağa oranla. Ters çevrilen kenar karşı
    kenarı geçemez (en küçük boyut korunur)."""
    x, y, w, h = start
    if kind == "move":
        return clamp_frac((x + dx, y + dy, w, h))
    x0, y0, x1, y1 = x, y, x + w, y + h
    if "w" in kind:
        x0 = min(max(0.0, x0 + dx), x1 - MIN_FRAC)
    if "e" in kind:
        x1 = max(min(1.0, x1 + dx), x0 + MIN_FRAC)
    if "n" in kind:
        y0 = min(max(0.0, y0 + dy), y1 - MIN_FRAC)
    if "s" in kind:
        y1 = max(min(1.0, y1 + dy), y0 + MIN_FRAC)
    return clamp_frac((x0, y0, x1 - x0, y1 - y0))


def span_region(a: tuple[float, float], b: tuple[float, float]) -> Frac:
    """İki köşe noktasından (oransal) bölge; kaynağın dışına taşan nokta kenara çekilir."""
    ax, ay = (min(1.0, max(0.0, v)) for v in a)
    bx, by = (min(1.0, max(0.0, v)) for v in b)
    return min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)


def downscale_bgr(frame: np.ndarray, max_w: int = PREVIEW_MAX_W) -> QImage:
    """BGR ya da BGRA kareyi en çok max_w genişliğe küçültüp QImage'a çevirir (kopya: numpy belleğine bağlı kalmaz).
    mss'in BGRA karesi dört kanalıyla küçültülür ve RGB32 olarak okunur: kanal kesmek bitişik olmayan bir görünüm
    üretir, cv2 onu önce kopyalar (1080p'de 8,2 ms yerine 1,8 ms; önizleme genişliğinde 0,7 ms)."""
    four = frame.ndim == 3 and frame.shape[2] == 4
    h, w = frame.shape[:2]
    img = frame
    if w > max_w:
        img = cv2.resize(frame, (max_w, max(1, round(h * max_w / w))), interpolation=cv2.INTER_AREA)
    img = np.ascontiguousarray(img)
    hh, ww = img.shape[:2]
    fmt = QImage.Format.Format_RGB32 if four else QImage.Format.Format_BGR888
    return QImage(img.data, ww, hh, img.strides[0], fmt).copy()


# --- görünüm ---------------------------------------------------------------------------------------
_CURSORS = {
    "move": Qt.CursorShape.SizeAllCursor, "new": Qt.CursorShape.CrossCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
}


class SourceView(QWidget):
    """Kaynağın canlı görüntüsü ve üstünde düzenlenebilir okunan bölge."""

    edited = Signal(tuple)     # sürüklerken (oransal bölge)
    committed = Signal(tuple)  # bırakınca ya da ok tuşuyla

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._img: QImage | None = None
        self._src = QSize(1920, 1080)
        self._region: Frac | None = None
        self._message = ""
        self._hint = ""
        self._notice = ""       # uyarı (ör. "Pencere simge durumunda"): sol üstte, turuncu kenarlı
        self._hover_hint = ""   # bölge varken fare üstündeyken altta görünen kısa kullanım ipucu
        self._editable = True
        self._drag: tuple[str, QPointF, Frac | None] | None = None
        self._hover = ""
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(240, 135)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setAccessibleName("Kaynağın canlı görüntüsü ve çevrilecek bölge")
        self.setAccessibleDescription("Bölgeyi fareyle çizin, ortasından taşıyın, köşelerinden boyutlandırın. "
                                      "Ok tuşları taşır, Shift ile ok tuşları boyutlandırır.")

    # --- durum -------------------------------------------------------------------------------
    @property
    def region(self) -> Frac | None:
        return self._region

    @property
    def source_size(self) -> QSize:
        return QSize(self._src)

    def set_frame(self, img: QImage | None, src: QSize | None = None) -> None:
        resized = src is not None and src.isValid() and src != self._src
        self._img = img if img is not None and not img.isNull() else None
        if src is not None and src.isValid():
            self._src = QSize(src)
        if resized:
            self.updateGeometry()  # en-boy oranı değişti (pencere modu)
        self.update()

    def set_region(self, frac: Sequence[float] | None) -> None:
        if self._drag is not None:  # kullanıcı sürüklüyor: dışarıdan gelen eski değer üstüne yazılmasın
            return
        self._region = clamp_frac(frac, 0.0) if frac is not None and len(frac) == 4 else None
        self.update()

    def set_message(self, text: str) -> None:
        """Görüntü yokken ortada yazan açıklama (ör. "Listeden bir pencere seçin")."""
        if text != self._message:
            self._message = text
            self.update()

    def set_hint(self, text: str) -> None:
        """Görüntünün üstünde duran kısa ipucu (bölge yokken: "Altyazının çıktığı alanı fareyle çizin")."""
        if text != self._hint:
            self._hint = text
            self.update()

    def set_notice(self, text: str) -> None:
        if text != self._notice:
            self._notice = text
            self.update()

    def set_hover_hint(self, text: str) -> None:
        self._hover_hint = text
        self.update()

    def set_editable(self, on: bool) -> None:
        self._editable = bool(on)
        if not on:
            self._drag = None
            self.unsetCursor()
        self.update()

    # --- boyut -------------------------------------------------------------------------------
    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802 - Qt
        sw, sh = max(1, self._src.width()), max(1, self._src.height())
        return max(self.minimumHeight(), min(int(w * sh / sw), int(w * 1.25)))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt
        return QSize(640, self.heightForWidth(640))

    # --- geometri ----------------------------------------------------------------------------
    def _img_rect(self) -> QRectF:
        """Kaynağın widget içindeki yeri (oran korunur, ortalanır)."""
        r = QRectF(self.rect())
        sw, sh = max(1, self._src.width()), max(1, self._src.height())
        k = min(r.width() / sw, r.height() / sh)
        w, h = sw * k, sh * k
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)

    def _to_frac(self, p: QPointF) -> tuple[float, float]:
        ir = self._img_rect()
        return (p.x() - ir.x()) / max(1.0, ir.width()), (p.y() - ir.y()) / max(1.0, ir.height())

    def _region_rect(self, frac: Frac | None = None) -> QRectF | None:
        f = frac if frac is not None else self._region
        if f is None:
            return None
        ir = self._img_rect()
        return QRectF(ir.x() + f[0] * ir.width(), ir.y() + f[1] * ir.height(), f[2] * ir.width(),
                      f[3] * ir.height())

    def hit(self, p: QPointF) -> str:
        """Fare noktasının anlamı: kenar/köşe adı, "move" (bölgenin içi), "new" (görüntünün başka yeri) ya da ""."""
        if not self._editable:
            return ""
        ir = self._img_rect()
        rr = self._region_rect()
        if rr is not None:
            near_x = rr.left() - GRAB_PX <= p.x() <= rr.right() + GRAB_PX
            near_y = rr.top() - GRAB_PX <= p.y() <= rr.bottom() + GRAB_PX
            if near_x and near_y:
                kind = ""
                if abs(p.y() - rr.top()) <= GRAB_PX:
                    kind += "n"
                elif abs(p.y() - rr.bottom()) <= GRAB_PX:
                    kind += "s"
                if abs(p.x() - rr.left()) <= GRAB_PX:
                    kind += "w"
                elif abs(p.x() - rr.right()) <= GRAB_PX:
                    kind += "e"
                if kind:
                    return kind
                if rr.contains(p):
                    return "move"
        return "new" if ir.contains(p) else ""

    # --- fare ve klavye ------------------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt
        if e.button() != Qt.MouseButton.LeftButton or not self._editable:
            return super().mousePressEvent(e)
        p = e.position()
        kind = self.hit(p)
        if not kind:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._drag = (kind, p, self._region)
        if kind == "new":
            fx, fy = self._to_frac(p)
            self._region = span_region((fx, fy), (fx, fy))
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt
        p = e.position()
        if self._drag is None:
            kind = self.hit(p)
            if kind != self._hover:
                self._hover = kind
                if kind:
                    self.setCursor(_CURSORS[kind])
                else:
                    self.unsetCursor()
                self.update()
            return
        kind, start, before = self._drag
        if kind == "new":
            self._region = span_region(self._to_frac(start), self._to_frac(p))
        elif before is not None:
            ir = self._img_rect()
            dx = (p.x() - start.x()) / max(1.0, ir.width())
            dy = (p.y() - start.y()) / max(1.0, ir.height())
            self._region = drag_region(kind, before, dx, dy)
        self.edited.emit(tuple(self._region or ()))
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt
        if self._drag is None or e.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(e)
        kind, _start, before = self._drag
        self._drag = None
        r = self._region
        if kind == "new" and (r is None or r[2] < MIN_FRAC or r[3] < MIN_FRAC):
            self._region = before  # tıklama: yeni bölge çizilmedi
        elif r is not None and r != before:
            self._region = clamp_frac(r)
            self.committed.emit(self._region)
        self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802 - Qt
        if e.key() == Qt.Key.Key_Escape and self._drag is not None:
            self._region = self._drag[2]
            self._drag = None
            self.update()
            return
        steps = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1),
                 Qt.Key.Key_Down: (0, 1)}
        if e.key() not in steps or self._region is None or not self._editable or self._drag is not None:
            return super().keyPressEvent(e)
        sx, sy = steps[e.key()]
        x, y, w, h = self._region
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            new = clamp_frac((x, y, w + sx * KEY_STEP, h + sy * KEY_STEP))
        else:
            new = clamp_frac((x + sx * KEY_STEP, y + sy * KEY_STEP, w, h))
        if new != self._region:
            self._region = new
            self.committed.emit(new)
            self.update()

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt
        self.update()  # fare üstündeyken kullanım ipucu
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt
        if self._hover and self._drag is None:
            self._hover = ""
            self.unsetCursor()
        self.update()
        super().leaveEvent(e)

    # --- çizim ---------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(outer, RADIUS["l"], RADIUS["l"])
        p.fillPath(clip, QColor(C.PANEL))
        p.setClipPath(clip)
        ir = self._img_rect()
        if self._img is not None:
            p.drawImage(ir, self._img)
        elif self._message:
            p.setPen(QColor(C.TEXT_3))
            p.setFont(self._font(F.BODY, 13))
            p.drawText(QRectF(self.rect()).adjusted(SPACE["l"], 0, -SPACE["l"], 0),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)
        rr = self._region_rect()
        if rr is not None and self._img is not None:
            shade = QPainterPath()
            shade.addRect(ir)
            hole = QPainterPath()
            hole.addRect(rr)
            p.fillPath(shade.subtracted(hole), qcolor(C.BG, 0.55))
            p.setPen(QPen(QColor(C.CYAN), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rr)
            if self._editable and (self._hover or self._drag is not None or self.hasFocus()):
                self._paint_handles(p, rr)
            self._paint_size(p, rr, ir)
        if self._hint and self._img is not None and self._drag is None:
            self._paint_pill(p, self._hint, QPointF(ir.center().x(), ir.top() + SPACE["l"]), center=True)
        elif (self._hover_hint and self._img is not None and self._region is not None and self._drag is None
              and self._editable and self.underMouse()):
            self._paint_pill(p, self._hover_hint, QPointF(ir.center().x(), ir.top() + SPACE["s"]), center=True)
        if self._notice:
            self._paint_pill(p, self._notice, QPointF(ir.left() + SPACE["s"], ir.top() + SPACE["s"]),
                             border=C.ORANGE)
        p.setClipping(False)
        p.setPen(QPen(QColor(C.CYAN if self.hasFocus() else C.LINE_STRONG), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, RADIUS["l"], RADIUS["l"])
        p.end()

    @staticmethod
    def _font(family: str, px: int, bold: bool = False) -> QFont:
        f = QFont()
        f.setFamilies([family, F.BODY_FALLBACK])
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    @staticmethod
    def _paint_handles(p: QPainter, rr: QRectF) -> None:
        s = HANDLE_PX
        p.setPen(QPen(QColor(C.BG), 1))
        p.setBrush(QColor(C.CYAN))
        for x in (rr.left(), rr.center().x(), rr.right()):
            for y in (rr.top(), rr.center().y(), rr.bottom()):
                if x == rr.center().x() and y == rr.center().y():
                    continue
                p.drawRect(QRectF(x - s / 2, y - s / 2, s, s))

    def _paint_size(self, p: QPainter, rr: QRectF, ir: QRectF) -> None:
        """Bölgenin kaynak pikseli cinsinden boyutu, çerçevenin üstünde (sığmazsa içinde)."""
        r = self._region
        if r is None:
            return
        text = f"{round(r[2] * self._src.width())} × {round(r[3] * self._src.height())}"
        f = self._font(F.DISPLAY, 12)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(text) + 2 * SPACE["s"]
        th = p.fontMetrics().height() + SPACE["xs"]
        y = rr.top() - th - 3 if rr.top() - th - 3 >= ir.top() else rr.top() + 3
        x = min(max(ir.left(), rr.left()), ir.right() - tw)
        box = QRectF(x, y, tw, th)
        path = QPainterPath()
        path.addRoundedRect(box, RADIUS["s"], RADIUS["s"])
        p.fillPath(path, qcolor(C.BG, 0.85))
        p.setPen(QColor(C.CYAN))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_pill(self, p: QPainter, text: str, at: QPointF, center: bool = False, border: str = C.CYAN) -> None:
        p.setFont(self._font(F.BODY, 13))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 2 * SPACE["m"]
        th = fm.height() + SPACE["s"]
        x = at.x() - tw / 2 if center else at.x()
        box = QRectF(x, at.y(), tw, th)
        path = QPainterPath()
        path.addRoundedRect(box, th / 2, th / 2)
        p.fillPath(path, qcolor(C.BG, 0.88))
        p.setPen(QPen(QColor(border), 1))
        p.drawPath(path)
        p.setPen(QColor(C.TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


# --- yakalayıcı -------------------------------------------------------------------------------------
class SourceGrabber(QObject):
    """Kaynağın küçültülmüş karesini arka plan iş parçacığında alır ve `frame` ile ana iş parçacığına verir."""

    frame = Signal(QImage, QSize)  # küçültülmüş kare, kaynağın gerçek boyutu (piksel)
    failed = Signal(str)           # kare alınamıyor (ör. pencere simge durumunda); boş: düzeldi

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._target: tuple = ("none",)
        self._borrow: Callable[[], np.ndarray | None] | None = None
        self._interval = IDLE_S
        self._max_w = PREVIEW_MAX_W
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._wincap = None
        self._wincap_hwnd = 0
        self._last_err = ""

    # --- ayar (ana iş parçacığı) -----------------------------------------------------------------
    def set_screen(self, mon: Mon | None) -> None:
        self._set_target(("screen", tuple(mon)) if mon else ("none",))

    def set_window(self, hwnd: int | None) -> None:
        self._set_target(("window", int(hwnd)) if hwnd else ("none",))

    def set_borrow(self, fn: Callable[[], np.ndarray | None] | None) -> None:
        """Çalışan işlem hattının pencere yakalamasından kare (pencere modunda); None: kendi yakalaması."""
        with self._lock:
            self._borrow = fn
        self._wake.set()

    def set_interval(self, seconds: float) -> None:
        self._interval = max(0.05, float(seconds))
        self._wake.set()

    def set_max_width(self, px: int) -> None:
        """Kare, önizlemenin ekrandaki gerçek piksel genişliğine küçültülür (fazlası boşuna işlenir)."""
        self._max_w = max(320, min(PREVIEW_MAX_W, int(px)))

    def _set_target(self, target: tuple) -> None:
        with self._lock:
            if target == self._target:
                return
            self._target = target
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, name="kaynak-onizleme", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        t, self._thread = self._thread, None
        if t is None:
            return
        self._quit.set()
        self._wake.set()
        t.join(timeout=2.0)

    # --- iş parçacığı ----------------------------------------------------------------------------
    def _run(self) -> None:
        sct = None
        last, last_w = None, 0
        try:
            while not self._quit.is_set():
                t0 = time.perf_counter()
                with self._lock:
                    target, borrow = self._target, self._borrow
                frame, err = None, ""
                try:
                    if target[0] == "screen":
                        self._drop_wincap()
                        if sct is None:
                            from altyazitr.capture import _new_mss

                            sct = _new_mss()
                        left, top, w, h = target[1]
                        shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
                        frame = np.asarray(shot)
                    elif target[0] == "window":
                        frame, err = self._window_frame(target[1], borrow)
                    else:
                        self._drop_wincap()
                except Exception as e:  # tek kare kaçar; döngü sürer
                    log.debug("Önizleme karesi alınamadı", exc_info=True)
                    err = str(e) or type(e).__name__
                # Pencere modunda son tam kare değişmediyse (pencere durgun: Windows yeni kare vermez, peek_full aynı
                # diziyi döndürür) yeniden küçültülüp çizilmez. Genişlik değiştiyse yine gönderilir.
                same = frame is not None and frame is last and self._max_w == last_w
                try:
                    if frame is not None and frame.size and not same:
                        h, w = frame.shape[:2]
                        self.frame.emit(downscale_bgr(frame, self._max_w), QSize(w, h))
                        last, last_w = frame, self._max_w
                    if err != self._last_err:
                        self._last_err = err
                        self.failed.emit(err)
                except RuntimeError:  # uygulama kapanıyor: alıcı nesne silinmiş
                    break
                self._wake.wait(max(0.0, self._interval - (time.perf_counter() - t0)))
                self._wake.clear()
        finally:
            self._drop_wincap()
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass

    def _window_frame(self, hwnd: int, borrow) -> tuple[np.ndarray | None, str]:
        from altyazitr import winutil

        if borrow is not None:  # işlem hattının yakalaması: ikinci yakalama açılmaz
            self._drop_wincap()
            return borrow(), ""
        if not winutil.is_window(hwnd):
            self._drop_wincap()
            return None, "Pencere kapandı"
        if winutil.is_minimized(hwnd):
            return None, "Pencere simge durumunda"
        if self._wincap is None or self._wincap_hwnd != hwnd or not self._wincap.alive:
            self._drop_wincap()
            from altyazitr.capture import WindowCapturer

            cap = WindowCapturer(hwnd, (0.0, 0.0, 1.0, 1.0), max(1, round(1 / max(0.05, self._interval))))
            cap.start()
            self._wincap, self._wincap_hwnd = cap, hwnd
        return self._wincap.peek_full(), ""

    def _drop_wincap(self) -> None:
        cap, self._wincap = self._wincap, None
        self._wincap_hwnd = 0
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                log.debug("Önizleme yakalaması durdurulamadı", exc_info=True)
