"""Yayıncının kendi ekranında görünen altyazı kutusu.

* Ekranın istediğiniz yerine taşınıp boyutlandırılabilir ("Konumu ayarla" modu); konumu OBS'teki
  altyazıdan bağımsızdır. Ayar modunda gerçek görünümüyle örnek yazı gösterir (canlı önizleme).
* Görünümü "Ekranımda" ayarlarıdır (Settings.screen_style); OBS sayfası ve maskesi "Yayınımda"
  ayarlarını (Settings.style) kullanır. Görünüm her çizimde ve her geçişte yeniden okunur: ayar
  değişince update() yeterlidir (bunu SubtitleDisplay.push_style yapar).
* Akış (Settings.flow): son çeviriler kutunun içinde alt alta durur; en yeni en altta ve tam görünür,
  üsttekiler soluktur. Kutu kısa kalırsa yalnızca sığan en yeni satırlar çizilir (bkz. render).
* Normalde çerçevesiz, her zaman üstte, odak çalmaz ve fareyi geçirir.
* Varsayılan olarak SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) ile ekran yakalamaya girmez:
  hem izleyiciler iki kez görmez hem de OCR kendi Türkçe yazımızı okumaz. İstenirse "yayında da
  görünsün" seçeneğiyle OBS Ekran Yakalama'da görünür hâle gelir.
* "Oyun altyazısının üzerine" (screen_style.cover_original; bkz. set_cover): kutu her satırda oyunun altyazısının
  üstüne taşınır, onu opak örter ve çeviriyi içinde ortalar. Kullanıcının kaydettiği kutu yeri değişmez: kapak
  kalkınca kutu oraya döner.
"""

from __future__ import annotations

import ctypes
import logging
import math
from collections.abc import Sequence
from ctypes import wintypes

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from altyazitr import APP_NAME
from altyazitr.config import Settings, StyleCfg
from altyazitr.gui import icons
from altyazitr.gui.dialogs import body_font
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F
from altyazitr.render import cover_text, paint_cover, paint_subtitle, stack_limit

log = logging.getLogger(__name__)

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
_u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
_u32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
_u32.SetWindowPos.argtypes = (wintypes.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_uint)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_NOACTIVATE = 0x08000000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10

SAMPLE = ("Altyazınız burada görünecek", APP_NAME, "Your subtitle will appear here")
# Ayar modunda akış örneği (eskiden yeniye; en yenisi SAMPLE): ayardaki satır sayısı kadarı gösterilir
SAMPLE_STACK = (
    ("Hızlı konuşmada önceki satırlar da okunur", "JASON", "Earlier lines stay readable in fast dialogue"),
    ("Daha eski satırlar yukarıda, iyice soluk", None, "Older lines stay above, faded"),
    ("Önceki çeviri, biraz daha soluk", "LUCIA", "The previous translation, a little dimmer"),
    SAMPLE,
)
EDIT_HINT = "Sürükleyerek taşıyın, kenarlardan boyutlandırın. Bitirmek için çift tıklayın."
EDGE = 12
MIN_W, MIN_H = 220, 70

Rect = tuple[float, float, float, float]


def to_logical(rect: Sequence[float], screens: Sequence[tuple[Sequence[float], float]]) -> tuple[Rect, Rect] | None:
    """Fiziksel piksel dikdörtgenini (yakalama ve Win32 koordinatı) Qt'nin mantıksal koordinatına çevirir:
    (dikdörtgen, bulunduğu ekranın mantıksal alanı). screens: [((x, y, genişlik, yükseklik) mantıksal,
    devicePixelRatio)]. Qt her ekranın sol üst köşesini iki koordinatta da aynı tutar, ekranın içi ölçekle küçülür
    (Windows %125 / %150 ölçek). Ekran, dikdörtgenin ortasının fiziksel olarak düştüğü ekrandır; hiçbiri değilse en
    yakını (çok ekranlı kurulumda her ekranın kendi ölçeği kullanılır)."""
    x, y, w, h = (float(v) for v in rect)
    cx, cy = x + w / 2, y + h / 2
    best: tuple[Rect, float] | None = None
    best_d = math.inf
    for geo, dpr in screens:
        sx, sy, sw, sh = (float(v) for v in geo)
        dpr = dpr if dpr > 0 else 1.0
        dx = max(sx - cx, 0.0, cx - (sx + sw * dpr))
        dy = max(sy - cy, 0.0, cy - (sy + sh * dpr))
        if dx * dx + dy * dy < best_d:
            best, best_d = ((sx, sy, sw, sh), dpr), dx * dx + dy * dy
    if best is None:
        return None
    (sx, sy, sw, sh), dpr = best
    return (sx + (x - sx) / dpr, sy + (y - sy) / dpr, w / dpr, h / dpr), (sx, sy, sw, sh)


def screen_area(rect: Sequence[float]) -> tuple[Rect, Rect] | None:
    """to_logical, bu bilgisayarın ekranlarıyla."""
    screens = []
    for scr in QGuiApplication.screens():
        g = scr.geometry()
        screens.append(((g.x(), g.y(), g.width(), g.height()), scr.devicePixelRatio()))
    return to_logical(rect, screens)


class ScreenOverlay(QWidget):
    editing_changed = Signal(bool)
    geometry_saved = Signal()

    def __init__(self, settings: Settings):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle(f"{APP_NAME} ekran altyazısı")
        self.setMouseTracking(True)
        self.s = settings
        # Gösterilen satırlar, eskiden yeniye: {"line", "text", "speaker", "original"}.
        # text/speaker/original her zaman en yeni satırınkidir (tek satırlık eski arayüz).
        self.lines: list[dict] = []
        self.text = ""
        self.speaker: str | None = None
        self.original = ""
        self.editing = False
        self._opacity = 0.0
        self._drag: tuple[set[str], QPoint, QRect] | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._anim.finished.connect(self._on_anim_done)
        # Yığına yeni satır eklenince: yeni satır belirir, eskiler yukarı kayar (0 → 1)
        self._progress = 1.0
        self._shift = QVariantAnimation(self)
        self._shift.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._shift.setStartValue(0.0)
        self._shift.setEndValue(1.0)
        self._shift.valueChanged.connect(self._on_shift)
        self._top_timer = QTimer(self, interval=2000, timeout=self._raise_topmost)
        # "Oyun altyazısının üzerine" (bkz. set_cover): gösterilen satır ve yazı boyu; None ise kutu kendi yerinde.
        # _cover_req: son istenen kapak (ayar modu bitince yeniden uygulanır)
        self._cover: dict | None = None
        self._cover_req: dict | None = None
        self._restore_geometry()

    @property
    def look(self) -> StyleCfg:
        """Kutunun görünümü: her zaman güncel "Ekranımda" ayarları (yayınınki değil)."""
        return self.s.screen_style

    # --- konum ---------------------------------------------------------------
    @staticmethod
    def default_box() -> QRect:
        g = QGuiApplication.primaryScreen().availableGeometry()
        # 0.28: akışta (son 3 satır alt alta) varsayılan yazı boyunda 1080p'de üç satır sığar; kutu saydamdır,
        # yalnızca yazı çizilir, fazladan yükseklik oyunu kapatmaz
        w, h = int(g.width() * 0.66), int(g.height() * 0.28)
        return QRect(g.x() + (g.width() - w) // 2, g.y() + g.height() - h - int(g.height() * 0.05), w, h)

    def _restore_geometry(self) -> None:
        box = self.s.overlay.box
        rect = QRect(*box) if len(box) == 4 else None
        if rect is None or rect.width() < MIN_W or rect.height() < MIN_H or not any(
                scr.geometry().intersects(rect) for scr in QGuiApplication.screens()):
            rect = self.default_box()
        self.setGeometry(rect)

    def save_geometry(self) -> None:
        if self._cover is not None:  # kutu oyunun altyazısının üstünde: kullanıcının kaydettiği yer değişmez
            return
        g = self.geometry()
        self.s.overlay.box = [g.x(), g.y(), g.width(), g.height()]
        self.geometry_saved.emit()

    def reset_position(self) -> None:
        if self._cover is not None:  # kapak yerinde kalır; kayıtlı yer yine sıfırlanır, kapak kalkınca oraya döner
            g = self.default_box()
            self.s.overlay.box = [g.x(), g.y(), g.width(), g.height()]
            self.geometry_saved.emit()
            return
        self.setGeometry(self.default_box())
        self.save_geometry()

    # --- dış arayüz ------------------------------------------------------------
    def start(self) -> None:
        self._top_timer.start()

    def stop(self) -> None:
        self._top_timer.stop()
        self.hide()

    def set_lines(self, lines: list[dict]) -> None:
        """Gösterilecek satırlar, eskiden yeniye (SubtitleDisplay'in OBS'e gönderdiği listeyle aynı:
        {"line", "text", "speaker", "original"} ve vurgu varsa "spans"). Boş liste kutuyu soldurur. Yığına yeni bir
        satır eklenince o satır belirir ve eskiler yukarı kayar; tek satırda yazı eskisi gibi anında değişir."""
        lines = [{"line": ln.get("line"), "text": ln.get("text", ""), "speaker": ln.get("speaker"),
                  "original": ln.get("original", "") or "", "spans": ln.get("spans") or []}
                 for ln in lines if ln.get("text")]
        if not lines:
            self.clear()
            return
        if self._cover is not None:  # "üzerine yerleştir" kapandı: kutu hemen kaydedilen yerine döner
            self._leave_cover()
        showing = self._opacity > 0.001 and bool(self.text)
        old_ids = {ln["line"] for ln in self.lines} if showing else set()
        self.lines = lines
        new = lines[-1]
        self.text, self.speaker, self.original = new["text"], new["speaker"], new["original"]
        if len(lines) > 1 and showing and new["line"] not in old_ids:
            self._shift.stop()
            self._shift.setDuration(max(1, int(self.look.fade_ms)))  # her geçişte okunur
            self._progress = 0.0
            self._shift.start()
        elif not showing:
            self._shift.stop()
            self._progress = 1.0
        self._update_visibility()
        self._fade_to(1.0)
        self.update()

    def set_subtitle(self, text: str, speaker: str | None, original: str) -> None:
        """Tek satır gösterir (akışsız)."""
        if not text:
            self.clear()
            return
        self.set_lines([{"line": None, "text": text, "speaker": speaker, "original": original}])

    def clear(self) -> None:
        self._fade_to(0.0)

    def set_cover(self, cover: dict | None) -> None:
        """Oyun altyazısının üzerine yerleştirir ("Ekranımda" görünümünde açıkken; yerini SubtitleDisplay hesaplar):
        kutu oyunun altyazısının üstüne taşınır, onu opak örter, çeviri içinde ortalanır. cover: {"rect": [x, y, w, h]
        oyunun altyazısı + pay, "bounds": bulunduğu ekran (ikisi de mantıksal piksel), "rows": oyundaki satır sayısı,
        "font" / "min": yazının hedef ve en küçük boyu (px), "line": {"text", "speaker", "spans"}}. None: kutu solar,
        sonra kullanıcının kaydettiği yerine döner. Ayar modunda ("Konumu ayarla") kullanıcının kendi kutusu
        düzenlenir; istenen kapak ayar modu bitince uygulanır."""
        self._cover_req = cover
        if self.editing:
            return
        placed = self._place_cover(cover) if cover else None
        if placed is None:
            if self._cover is not None:
                self._fade_to(0.0)
            return
        rect, size = placed
        entering = self._cover is None
        if entering:  # akıştan kapağa: kutudaki satırlar hemen bırakılır
            self._anim.stop()
            self._shift.stop()
            self._opacity, self._progress = 0.0, 1.0
            self.lines = []
        line = cover["line"]
        self._cover = {"line": dict(line), "size": size}
        self.text, self.speaker, self.original = str(line.get("text") or ""), line.get("speaker"), ""
        if self.geometry() != rect:
            self.setGeometry(rect)
        self._update_visibility()
        if entering:
            self._apply_win32()  # ekran yakalamaya görünürlük kapakta değişebilir (bkz. _affinity)
        self._fade_to(1.0)
        self.update()

    def _place_cover(self, cover: dict) -> tuple[QRect, float] | None:
        """Kapak kutusunun pencere alanı (mantıksal piksel) ve yazı boyu. Kutu oyunun altyazısının dikdörtgenidir;
        Türkçe sığmazsa yukarı doğru büyür (alt kenar sabit: oyun altyazısı alttan hizalıdır), tek kelime bile
        sığmıyorsa iki yana genişler; ekrandan taşmaz. Yazı boyu render.cover_text ile seçilir."""
        try:
            x, y, w, h = (float(v) for v in cover["rect"])
            bx, by, bw, bh = (float(v) for v in cover["bounds"])
            fit = cover_text(self.look, cover["line"], w, int(cover.get("rows") or 1), float(cover["font"]),
                             float(cover["min"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if fit is None or w <= 0 or h <= 0 or bw <= 0 or bh <= 0:
            return None
        W, H = min(max(w, fit.w), bw), min(max(h, fit.h), bh)
        left = min(max(x + (w - W) / 2, bx), bx + bw - W)
        top = min(max(y + h - H, by), by + bh - H)
        x0, y0 = math.floor(left), math.floor(top)
        return QRect(x0, y0, math.ceil(left + W) - x0, math.ceil(top + H) - y0), fit.size

    def _leave_cover(self) -> None:
        """Kapaktan hemen çıkar: kutu kaydedilen yerine döner (geçişsiz)."""
        self._anim.stop()
        self._opacity = 0.0
        self._forget()
        self._apply_win32()

    def set_editing(self, on: bool) -> None:
        if on == self.editing:
            return
        if on and self._cover is not None:  # ayar modunda kullanıcının kendi kutusu düzenlenir
            self._leave_cover()
        self.editing = on
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)
        if not on:
            self._drag = None
            self.unsetCursor()
            self.save_geometry()
        self._update_visibility()
        self._apply_win32()
        self.update()
        self.editing_changed.emit(on)
        if not on and self._cover_req is not None:
            self.set_cover(self._cover_req)

    def apply_settings(self) -> None:
        self._update_visibility()
        self._apply_win32()
        self.update()

    def _update_visibility(self) -> None:
        want = self.editing or (self.s.overlay.enabled and (bool(self.text) or self._opacity > 0.001))
        if want and not self.isVisible():
            self.show()
        elif not want and self.isVisible():
            self.hide()

    # --- Win32 -----------------------------------------------------------------------
    def _hwnd(self) -> int:
        return int(self.winId())

    def _raise_topmost(self) -> None:
        if self.isVisible():
            _u32.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def _apply_win32(self) -> None:
        if not self.isVisible():
            return
        h = self._hwnd()
        ex = _u32.GetWindowLongPtrW(h, GWL_EXSTYLE)
        if self.editing:
            ex &= ~WS_EX_TRANSPARENT
        else:
            ex |= WS_EX_TRANSPARENT
        _u32.SetWindowLongPtrW(h, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        if not _u32.SetWindowDisplayAffinity(h, self._affinity()):
            log.warning("SetWindowDisplayAffinity başarısız: %s", ctypes.get_last_error())
        self._raise_topmost()

    def _affinity(self) -> int:
        """Ekran yakalamaya görünürlük: "yayında da görünsün" açıksa görünür. Kapak ekran bölgesi okunurken (ekran
        modu) hiçbir zaman görünmez: görünseydi OCR oyunun altyazısı yerine kapağı okur, altyazı "kaybolur", kapak
        kalkar ve yazı yeniden okunur (titreme). Pencere modunda yalnız oyunun penceresi yakalanır, sorun yok."""
        covering_roi = self._cover is not None and self.s.capture.mode != "window"
        return WDA_NONE if self.s.overlay.visible_in_stream and not covering_roi else WDA_EXCLUDEFROMCAPTURE

    def showEvent(self, e) -> None:  # noqa: N802 - Qt adı
        super().showEvent(e)
        self._apply_win32()

    # --- animasyon ---------------------------------------------------------------------
    def _fade_to(self, target: float) -> None:
        if abs(self._opacity - target) < 1e-3:
            if target <= 0.001 and self._anim.state() != QVariantAnimation.State.Running:
                self._forget()
                self._update_visibility()
            return
        self._anim.stop()
        self._anim.setDuration(max(1, int(self.look.fade_ms)))  # her geçişte okunur: ayar değişince uyar
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim(self, v) -> None:
        self._opacity = float(v)
        self.update()

    def _on_anim_done(self) -> None:
        if self._opacity <= 0.001:
            self._forget()
            self._update_visibility()

    def _on_shift(self, v) -> None:
        self._progress = float(v)
        self.update()

    def _forget(self) -> None:
        """Kutu tamamen kaybolunca gösterilen satırlar unutulur; kapak kutusu kullanıcının kaydettiği yere döner."""
        self.text = ""
        self.lines = []
        self._shift.stop()
        self._progress = 1.0
        if self._cover is not None:
            self._cover = None
            self._restore_geometry()

    # --- fareyle taşıma / boyutlandırma (yalnızca ayar modunda) ------------------------
    def _edges(self, pos: QPoint) -> set[str]:
        e = set()
        if pos.x() <= EDGE:
            e.add("l")
        if pos.x() >= self.width() - EDGE:
            e.add("r")
        if pos.y() <= EDGE:
            e.add("t")
        if pos.y() >= self.height() - EDGE:
            e.add("b")
        return e

    @staticmethod
    def _cursor_for(edges: set[str]) -> Qt.CursorShape:
        if edges in ({"l", "t"}, {"r", "b"}):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in ({"r", "t"}, {"l", "b"}):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & {"l", "r"}:
            return Qt.CursorShape.SizeHorCursor
        if edges & {"t", "b"}:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeAllCursor

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.editing and e.button() == Qt.MouseButton.LeftButton:
            self._drag = (self._edges(e.position().toPoint()), QCursor.pos(), QRect(self.geometry()))

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if not self.editing:
            return
        if self._drag is None:
            self.setCursor(self._cursor_for(self._edges(e.position().toPoint())))
            return
        edges, start, g0 = self._drag
        d = QCursor.pos() - start
        g = QRect(g0)
        if not edges:
            g.moveTo(g0.topLeft() + d)
        else:
            if "l" in edges:
                g.setLeft(min(g0.left() + d.x(), g0.right() - MIN_W))
            if "r" in edges:
                g.setRight(max(g0.right() + d.x(), g0.left() + MIN_W))
            if "t" in edges:
                g.setTop(min(g0.top() + d.y(), g0.bottom() - MIN_H))
            if "b" in edges:
                g.setBottom(max(g0.bottom() + d.y(), g0.top() + MIN_H))
        self.setGeometry(g)

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.save_geometry()

    def mouseDoubleClickEvent(self, _e) -> None:  # noqa: N802
        if self.editing:
            self.set_editing(False)

    # --- çizim -------------------------------------------------------------------------------
    def sample_lines(self) -> list[dict]:
        """Ayar modundaki örnek: akışta ayardaki satır sayısı kadar örnek satır, tek satır modunda SAMPLE."""
        n = min(stack_limit(self.s.flow), len(SAMPLE_STACK))
        return [{"line": -i, "text": t, "speaker": sp, "original": o}
                for i, (t, sp, o) in enumerate(SAMPLE_STACK[-n:])]

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        W, H = self.width(), self.height()
        if self.editing:
            self._paint_edit_chrome(p, W, H)
            showing = bool(self.text and self.lines)
            self._paint_lines(p, W, H, self.lines if showing else self.sample_lines(),
                              self._progress if showing else 1.0)
        elif self._cover is not None:
            if self._opacity > 0.001:
                p.setOpacity(self._opacity)
                paint_cover(p, W, H, self.look, self._cover["line"], self._cover["size"])
        elif self._opacity > 0.001 and self.text and self.lines:
            p.setOpacity(self._opacity)
            self._paint_lines(p, W, H, self.lines, self._progress)
        p.end()

    def _paint_lines(self, p: QPainter, W: int, H: int, lines: list[dict], progress: float) -> None:
        """Satırları kutuya çizer: en yeni altta, eskiler üstte ve soluk (tek paint_subtitle çağrısı)."""
        *older, new = lines
        ref_h = (self.screen() or QGuiApplication.primaryScreen()).geometry().height()
        paint_subtitle(p, W, H, self.look, new["text"], new.get("speaker"), new.get("original", ""),
                       self.s.overlay.scale, ref_height=ref_h, in_box=True, older=older, progress=progress,
                       spans=new.get("spans") or ())

    @staticmethod
    def _paint_edit_chrome(p: QPainter, W: int, H: int) -> None:
        """Ayar modu çerçevesi: hafif karartma, kesik camgöbeği kenar, köşe tutamakları ve ipucu."""
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1, 1, W - 2, H - 2)
        shade = QColor(C.BG)
        shade.setAlpha(96)  # alfa > 0: ayar modunda tıklamalar yakalanır
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shade)
        p.drawRoundedRect(box, RADIUS["m"], RADIUS["m"])
        pen = QPen(QColor(C.CYAN), 1.5, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, RADIUS["m"], RADIUS["m"])

        # köşe tutamakları: kenarlardan ve köşelerden boyutlandırılabildiğini gösterir
        hs = 8.0
        p.setPen(QPen(QColor(C.BG), 1.5))
        p.setBrush(QColor(C.CYAN))
        for x, y in ((1, 1), (W - 1 - hs, 1), (1, H - 1 - hs), (W - 1 - hs, H - 1 - hs)):
            p.drawRoundedRect(QRectF(x, y, hs, hs), 2, 2)

        # ipucu (dar kutuda yalnızca taşıma ikonu)
        pad, h = SPACE["s"] + 2, 28
        p.setFont(body_font(F.BODY_SIZE))
        fm = p.fontMetrics()
        avail = W - 2 * SPACE["m"] - 2 * pad - ICON["s"] - SPACE["s"]
        msg = fm.elidedText(EDIT_HINT, Qt.TextElideMode.ElideRight, int(avail)) if avail >= 260 else ""
        text_w = SPACE["s"] + fm.horizontalAdvance(msg) if msg else 0
        pill = QRectF(SPACE["m"], SPACE["m"], 2 * pad + ICON["s"] + text_w, h)
        p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.setBrush(QColor(C.PANEL))
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["m"], RADIUS["m"])
        p.drawPixmap(QPointF(pill.left() + pad, pill.center().y() - ICON["s"] / 2),
                     icons.pixmap("move", ICON["s"], C.CYAN))
        p.setPen(QColor(C.TEXT))
        p.drawText(QRectF(pill.left() + pad + ICON["s"] + SPACE["s"], pill.top(), pill.width(), h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, msg)
