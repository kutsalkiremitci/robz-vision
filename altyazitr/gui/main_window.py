"""Ana pencere: sol kenar çubuğu + 4 sayfa, pencere içi bildirimler, sistem tepsisi, bölge seçimi akışı.

Yerel yapay zekâ kurulumu (`ctx.ai`, altyazitr.ai_setup.AiSetup) arka planda sürerken kenar çubuğunda tıklanabilir bir
durum satırı görünür ("Yapay zekâ %34", "Yapay zekâ duraklatıldı", "Yapay zekâ kurulamadı"; tıklanınca Çeviri
sayfası). Pencere açmayan bildirimler tek kanaldan gelir (`ctx.notify()` → `ctx.notified`; işlem hattının notice'i
oraya AppContext'te bağlı): pencere görünürken sağ altta kısa bildirim (gui/toast.py), tepsideyken tepsi balonu.
Kurulumun sonucu (`ai.finished`) ve güncelleyen kullanıcıya bir kezlik yapay zekâ daveti (`settings.ai.invite_pending`)
de bu kanaldan verilir.

Kenar çubuğunun en altında geliştiricinin Kick bağlantısı durur (Kick logosu + "Robz so good"; gui/developer.py):
tıklanınca ya da odaktayken Enter ile kanal tarayıcıda açılır. Bildirim ya da açılır pencere göstermez.

Güncelleme (`ctx.updater`, altyazitr.update_service.UpdateService): yeni sürüm varken kenar çubuğunda "Güncelleme var"
satırı durur (indirme sürerken yüzdesi); tıklanınca güncelleme penceresi (gui/update_dialog.py). Yeni sürüm bu oturumda
ilk kez bulununca pencere içinde bir kez tıklanabilir kısa bildirim çıkar; çeviri çalışırken, pencere gizliyken ya da
tur açıkken beklenir (çeviri durunca, pencere açılınca). Tepsi balonu çıkmaz (yayına düşer).
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from altyazitr import APP_NAME, __version__, winutil
from altyazitr.ai_setup import TEXT_READY, TEXT_READY_LATER, Step, step_title
from altyazitr.capture import snapshot_window
from altyazitr.gui import developer
from altyazitr.gui.ai_setup_view import PAUSED, error_note, fmt_eta, fmt_pair, fmt_percent
from altyazitr.gui.dialogs import OBS_NOT_CONNECTED_TIP
from altyazitr.gui.home_page import HomePage
from altyazitr.gui.icons import icon, pixmap
from altyazitr.gui.region_dialog import RegionDialog
from altyazitr.gui.region_picker import ScreenRegionPicker
from altyazitr.gui.settings_page import SettingsPage
from altyazitr.gui.style_page import StylePage
from altyazitr.gui.theme import app_icon
from altyazitr.gui.toast import ToastHost
from altyazitr.gui.tokens import RADIUS, SPACE, C, F
from altyazitr.gui.translate_page import TranslatePage, invite_offered
from altyazitr.gui.update_dialog import TOAST_MS, TOAST_TEXT, UpdateDialog, sidebar_status

log = logging.getLogger(__name__)

PAGES = [
    ("home", "Ana sayfa", HomePage),
    ("style", "Görünüm", StylePage),
    ("translate", "Çeviri", TranslatePage),
    ("settings", "Ayarlar", SettingsPage),
]
NAV_ICONS = {"home": "home", "style": "palette", "translate": "languages", "settings": "settings"}

SIDEBAR_W = 212          # geniş kenar çubuğu
SIDEBAR_COMPACT_W = 64   # yalnız ikon
COMPACT_BELOW = 900      # pencere bundan darsa kenar çubuğu daralır
NAV_ITEM_H = 40
ICON_COL = 20            # ikon sütunu: marka, menü ve durum ikonları aynı eksende hizalanır
AI_ROW_H = 32            # yapay zekâ durum satırı (tıklanabilir)

# Yapay zekâ kurulumunun bildirimleri (başarı metinleri servisle ortak: ai_setup.TEXT_READY / TEXT_READY_LATER)
AI_FAILED = "Yapay zekâ kurulamadı; ayrıntı Çeviri sayfasında"
AI_INVITE = "Yeni: yapay zekâ çevirisi. Çeviri sayfasından kurabilirsiniz."
NOTICE_MS = 6000         # pencere açmayan bildirim, bir eyleme anlık yanıttan ("Bölge kaydedildi") uzun kalır


# --- küçük yardımcılar ------------------------------------------------------------------
def _qss_font(family: str, px: int, color: str) -> str:
    """Yerel QSS ile yazı tipi: uygulama genelindeki `*` yazı kuralını ezer."""
    return f"font-family: '{family}'; font-size: {px}px; color: {color}; background: transparent;"


def _font(family: str, px: int) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    return f


def _dpr(w: QWidget) -> float:
    try:
        return max(1.0, float(w.devicePixelRatioF()))
    except Exception:
        return 1.0


def _logo_source() -> QImage:
    """Marka logosu: paket içindeki logo.png; yoksa temanın çizdiği simge."""
    img = QImage()
    try:
        from altyazitr.paths import resource_dir

        path = resource_dir() / "data" / "logo.png"
        if path.exists():
            img = QImage(str(path))
    except Exception:
        log.debug("Logo okunamadı", exc_info=True)
    if img.isNull():
        try:
            from altyazitr.gui.theme import make_icon_pixmap

            img = make_icon_pixmap(128).toImage()
        except Exception:
            img = app_icon().pixmap(128, 128).toImage()
    return img


class _Logo(QWidget):
    """Logoyu ekranın piksel yoğunluğunda, yumuşak ölçeklemeyle çizer."""

    def __init__(self, size: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self._src = _logo_source()
        self._cache: tuple[float, QPixmap] | None = None
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e) -> None:  # noqa: N802
        dpr = _dpr(self)
        if self._cache is None or self._cache[0] != dpr:
            px = max(1, round(self._size * dpr))
            img = self._src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            self._cache = (dpr, pm)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self._cache[1])
        p.end()


# --- kenar çubuğu --------------------------------------------------------------------------
class _NavDelegate(QStyledItemDelegate):
    """Menü öğesi: ikon + Bahnschrift etiket. Seçili öğe RAISED zemin + 3 px camgöbeği sol çizgi."""

    def __init__(self, nav: _NavList):
        super().__init__(nav)
        self._nav = nav
        self._font = _font(F.DISPLAY, 14)
        self._icons: dict[tuple[str, str, float], QPixmap] = {}

    def _icon(self, name: str, color: str, dpr: float) -> QPixmap:
        key = (name, color, dpr)
        if key not in self._icons:
            self._icons[key] = pixmap(name, 20, color, dpr=max(dpr, 1.0))
        return self._icons[key]

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(SIDEBAR_COMPACT_W, NAV_ITEM_H + SPACE["xs"])

    def paint(self, p: QPainter, option, index) -> None:
        nav = self._nav
        st = option.state
        selected = bool(st & QStyle.StateFlag.State_Selected)
        hover = bool(st & QStyle.StateFlag.State_MouseOver)
        focus = bool(st & QStyle.StateFlag.State_HasFocus) and nav.hasFocus() and nav.kbd_focus
        r = QRectF(option.rect).adjusted(SPACE["s"], SPACE["xs"] / 2, -SPACE["s"], -SPACE["xs"] / 2)

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QPainterPath()
        pill.addRoundedRect(r, RADIUS["m"], RADIUS["m"])
        if selected:
            p.fillPath(pill, QColor(C.RAISED))
            bar = QRectF(r.left(), r.center().y() - 9, 3, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.CYAN))
            p.drawRoundedRect(bar, 1.5, 1.5)
        elif hover:
            bg = QColor(C.RAISED)
            bg.setAlphaF(0.55)
            p.fillPath(pill, bg)
        if focus:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(C.CYAN), 1.5))
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), RADIUS["m"], RADIUS["m"])

        color = C.TEXT if (selected or hover) else C.TEXT_2
        name = index.data(Qt.ItemDataRole.UserRole) or "help"
        dpr = p.device().devicePixelRatioF() if p.device() is not None else 1.0
        pm = self._icon(name, color, dpr)
        ix = r.center().x() - 10 if nav.compact else r.left() + SPACE["m"]  # darda ikon ortada
        p.drawPixmap(QPointF(ix, r.center().y() - 10), pm)
        if not nav.compact:
            p.setFont(self._font)
            p.setPen(QColor(color))
            tx = ix + 20 + SPACE["m"]
            tr = QRectF(tx, r.top(), r.right() - tx - SPACE["s"], r.height())
            text = QFontMetrics(self._font).elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                                       Qt.TextElideMode.ElideRight, int(tr.width()))
            p.drawText(tr, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.restore()


class _NavList(QListWidget):
    """Klavye ile gezilebilen sayfa menüsü (↑/↓, Home/End). `setCurrentRow` ile uyumludur."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("nav")
        self.compact = False
        self.kbd_focus = False
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.setItemDelegate(_NavDelegate(self))
        self.setAccessibleName("Sayfalar")
        # yerel QSS: temanın genel liste/öğe kuralları bu çizimi etkilemesin
        self.setStyleSheet("QListWidget#nav { background: transparent; border: none; padding: 0; margin: 0;"
                           " outline: 0; }")

    def add_page(self, title: str, icon_name: str) -> QListWidgetItem:
        it = QListWidgetItem(title, self)
        it.setData(Qt.ItemDataRole.UserRole, icon_name)
        return it

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        for i in range(self.count()):
            it = self.item(i)
            it.setToolTip(it.text() if compact else "")
        self.viewport().update()

    def focusInEvent(self, e) -> None:  # noqa: N802
        self.kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                        Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.kbd_focus = False
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        self.kbd_focus = True
        super().keyPressEvent(e)
        self.viewport().update()


class _StatusRow(QWidget):
    """Kenar çubuğu altındaki durum satırı: ikon + kısa durum yazısı (daraltılınca yalnız ikon)."""

    def __init__(self, icon_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["m"])
        self.ic = QLabel()
        self.ic.setFixedSize(ICON_COL, ICON_COL)
        self.ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tx = QLabel()
        h.addWidget(self.ic)
        h.addWidget(self.tx, 1)
        self.setFixedHeight(28)

    def set_state(self, on: bool, text: str, tip: str) -> None:
        color = C.TURQUOISE if on else C.TEXT_3
        self.ic.setPixmap(pixmap(self._icon_name, 16, color, dpr=max(2.0, _dpr(self))))
        self.tx.setText(text)
        self.tx.setStyleSheet(_qss_font(F.DISPLAY, 13, C.TEXT if on else C.TEXT_3))
        self.setToolTip(tip)
        self.setAccessibleName(text)

    def set_compact(self, compact: bool) -> None:
        self.tx.setVisible(not compact)


def ai_status(ai: Any, fraction: float | None = None) -> tuple[str, str, str] | None:
    """Kenar çubuğundaki yapay zekâ satırı: (durum, kısa yazı, ipucu); iş yoksa None (satır gizli).
    Durum: running ("Yapay zekâ %34"; ipucu "Gemma 4 12B indiriliyor: 2,7 / 8,0 GB, kalan ~9 dk"), paused, failed.
    fraction: toplam ilerleme (Çeviri sayfasındaki panelinkiyle aynı olsun diye oradan); verilmezse servisinki."""
    state = str(getattr(ai, "state", "") or "")
    message = str(getattr(ai, "message", "") or "")
    if state == "running":
        if fraction is None:
            fraction = float(getattr(ai, "fraction", 0.0) or 0.0)
        text = f"Yapay zekâ {fmt_percent(fraction)}"
        step = getattr(ai, "step", None)
        done, total = int(getattr(ai, "done_bytes", 0) or 0), int(getattr(ai, "total_bytes", 0) or 0)
        if total > 0 and isinstance(step, Step):  # indirme adımı: adı ve miktarı
            head = f"{step_title(step, str(getattr(ai, 'model', '') or ''))}: {fmt_pair(done, total)}"
        else:
            head = message or "Hazırlanıyor…"
        eta = fmt_eta(getattr(ai, "eta_s", None))  # "Kalan ~9 dk"
        tip = f"{head}, {eta[:1].lower()}{eta[1:]}" if eta else head
        if message and not head.startswith(message):  # ağ beklemesi, hafif modele geçiş, iptal ediliyor
            tip += f"\n{message}"
        return "running", text, tip
    if state == "paused":
        return "paused", "Yapay zekâ duraklatıldı", f"{PAUSED}. Çeviri durunca kaldığı yerden sürer."
    if state == "failed":
        return "failed", "Yapay zekâ kurulamadı", error_note(str(getattr(ai, "error", "") or ""))
    return None


class _RowButton(QAbstractButton):
    """Kenar çubuğunda tıklanabilir satır: ikon + kısa yazı (durum satırlarıyla aynı eksenlerde; dar kenar çubuğunda
    yalnız ikon, yazı ipucuna geçer). Düğmedir: tıklanınca, odaktayken Enter ya da Boşluk ile `clicked`. Üzerine
    gelince menü öğeleri gibi yarı saydam RAISED zemin, klavye odağında camgöbeği halka."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(AI_ROW_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["m"])
        self.ic = QLabel()
        self.ic.setFixedSize(ICON_COL, ICON_COL)
        self.ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tx = QLabel()
        self.tx.setMinimumWidth(1)
        for w in (self.ic, self.tx):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        h.addWidget(self.ic)
        h.addWidget(self.tx, 1)
        self._compact = False

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.tx.setVisible(not compact)
        self._sync_tip()

    def _sync_tip(self) -> None:
        """İpucu (dar kenar çubuğunda yazı da ipucuna geçer); alt sınıflar belirler."""

    def _hover_changed(self, _hover: bool) -> None:
        """Fare satıra girip çıkınca (alt sınıf yazı rengini değiştirebilir)."""

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.isAutoRepeat():
            self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover_changed(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover_changed(False)
        self.update()
        super().leaveEvent(e)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(SIDEBAR_COMPACT_W, AI_ROW_H)

    def paintEvent(self, _e) -> None:  # noqa: N802
        hover = self.underMouse() or self.isDown()
        focus = self.hasFocus() and bool(self.property("kbfocus"))
        if not (hover or focus):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if hover:
            bg = QColor(C.RAISED)
            bg.setAlphaF(1.0 if self.isDown() else 0.55)
            path = QPainterPath()
            path.addRoundedRect(r, RADIUS["m"], RADIUS["m"])
            p.fillPath(path, bg)
        if focus:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(C.CYAN), 1.5))
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), RADIUS["m"], RADIUS["m"])
        p.end()


class _AiRow(_RowButton):
    """Kenar çubuğundaki yapay zekâ kurulum satırı: ikon + kısa durum; tıklanınca Çeviri sayfası."""

    LOOK = {  # durum → (ikon, ikon rengi, yazı rengi)
        "running": ("download", C.CYAN, C.TEXT),
        "paused": ("download", C.TEXT_3, C.TEXT_2),
        "failed": ("circle-alert", C.ORANGE, C.TEXT),
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("aiStatus")
        self._look: tuple[str, str, str] | None = None
        self._tip = ""
        self.setAccessibleDescription("Çeviri sayfasını açar")

    def set_status(self, state: str, text: str, tip: str) -> None:
        look = self.LOOK.get(state, self.LOOK["running"])
        if look != self._look:  # ikon yalnız durum değişince çizilir (ilerleme sık güncellenir)
            self._look = look
            name, icon_color, text_color = look
            self.ic.setPixmap(pixmap(name, 16, icon_color, dpr=max(2.0, _dpr(self))))
            self.tx.setStyleSheet(_qss_font(F.DISPLAY, 13, text_color))
        self.tx.setText(text)
        self._tip = tip
        self.setAccessibleName(text)
        self._sync_tip()

    def _sync_tip(self) -> None:
        text = self.tx.text()
        if self._compact:
            self.setToolTip(f"{text}\n{self._tip}" if self._tip else text)
        else:
            self.setToolTip(self._tip or text)


class _UpdateRow(_AiRow):
    """Kenar çubuğundaki güncelleme satırı ("Güncelleme var", "Güncelleme %34"); tıklanınca güncelleme penceresi."""

    LOOK = {  # durum → (ikon, ikon rengi, yazı rengi); bkz. update_dialog.sidebar_status
        "available": ("download", C.CYAN, C.TEXT),
        "running": ("download", C.CYAN, C.TEXT),
        "failed": ("circle-alert", C.ORANGE, C.TEXT),
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("updateStatus")
        self.setAccessibleDescription("Güncelleme penceresini açar")


class _KickRow(_RowButton):
    """Kenar çubuğunun en altındaki geliştirici bağlantısı (bkz. gui/developer.py): Kick logosu + "Robz so good"; dar
    kenar çubuğunda yalnız logo. Tıklanınca, odaktayken Enter ya da Boşluk ile geliştiricinin Kick kanalı varsayılan
    tarayıcıda açılır. Yazı sessizdir (TEXT_2, üzerine gelince TEXT); Kick yeşili yalnız logoda."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("kickLink")
        # durum ikonları gibi 16 px; dolu "K" çizgi ikonlarla aynı ağırlıkta görünsün diye kutuda 14 px
        self.ic.setPixmap(developer.kick_pixmap(16, max(2.0, _dpr(self)), developer.ICON_SCALE))
        self.tx.setText(developer.DEVELOPER)
        self._hover_changed(False)
        self.setToolTip(developer.FOLLOW_TIP)  # yazıyı da kapsar: dar kenar çubuğunda da aynı
        self.setAccessibleName(developer.FOLLOW_TEXT)
        self.setAccessibleDescription(developer.KICK_DESC)
        self.clicked.connect(developer.open_kick)

    def _hover_changed(self, hover: bool) -> None:
        self.tx.setStyleSheet(_qss_font(F.DISPLAY, 13, C.TEXT if hover else C.TEXT_2))


class _Sidebar(QWidget):
    """PANEL zeminli kenar çubuğu; sağda 1 px LINE ayırıcı. Kendini çizer (QSS'e bağlı değil)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sidebar")

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C.PANEL))
        p.fillRect(self.width() - 1, 0, 1, self.height(), QColor(C.LINE))
        p.end()


# --- ana pencere -------------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        # Ana sayfanın geniş düzeni (canlı kaynak sağda her zaman görünür) 1080p ekranda varsayılan olsun: ekran
        # yeterince büyükse pencere 1280×840'a kadar büyür; eski varsayılanın (980×760) altına hiç inmez.
        scr = QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        w, h = 980, 760
        if avail is not None:
            w = max(w, min(1280, int(avail.width() * 0.92)))
            h = max(h, min(840, int(avail.height() * 0.92)))
        self.resize(w, h)
        self.setMinimumSize(780, 560)
        self._tray_notified = False
        self._quitting = False
        self._picker: ScreenRegionPicker | None = None
        self._compact: bool | None = None
        self._startup_done = False  # açılıştaki tur kararı verildi (davet bildirimi ondan sonra)
        self._advice: Any = None  # donanım önerisi (Çeviri sayfası arka planda okur)
        self._advice_known = False
        self._upd = getattr(ctx, "updater", None)  # güncelleme servisi (araçlarda ve testlerde olmayabilir)
        self._update_dlg: UpdateDialog | None = None
        self._update_toast_for = ""  # bildirimi bekleyen sürüm (çeviri durunca / pencere açılınca)
        self._update_toast: Any = None

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.side = side = _Sidebar()
        side.setFixedWidth(SIDEBAR_W)
        sv = QVBoxLayout(side)
        # alt boşluk 8 px: en alttaki Kick satırının logosu kendi satırında 9 px içeride, kenara uzaklığı ~16 px kalır
        sv.setContentsMargins(0, SPACE["l"] + SPACE["xs"], 1, SPACE["s"])
        sv.setSpacing(0)

        # marka: logo + "Robz Vision"
        self._brand = QWidget()
        bh = QHBoxLayout(self._brand)
        bh.setSpacing(SPACE["m"] - 2)
        self._logo = _Logo(32)
        self._logo.setToolTip(APP_NAME)
        self._wordmark = QLabel(APP_NAME)
        self._wordmark.setStyleSheet(_qss_font(F.DISPLAY_SEMIBOLD, 18, C.TEXT))
        self._wordmark.setAccessibleName(APP_NAME)
        bh.addWidget(self._logo)
        bh.addWidget(self._wordmark, 1)
        sv.addWidget(self._brand)
        sv.addSpacing(SPACE["xl"])

        self.nav = _NavList()
        sv.addWidget(self.nav, 1)

        # güncelleme: yalnız yeni sürüm varken (ya da indirilirken); tıklanınca güncelleme penceresi. Yapay zekâ satırı
        # gibi kenarlardan 8 px içeride (bkz. _apply_compact).
        self._update_row = _UpdateRow()
        self._update_row.clicked.connect(self.open_update_dialog)
        self._update_box = QWidget()
        ub = QHBoxLayout(self._update_box)
        ub.setContentsMargins(SPACE["s"], 0, SPACE["s"], SPACE["xs"])
        ub.addWidget(self._update_row)
        self._update_box.hide()
        sv.addWidget(self._update_box)

        # yapay zekâ kurulumu: yalnız iş sürerken ya da hata varken; tıklanınca Çeviri sayfası. Menü öğeleri gibi
        # kenarlardan 8 px içeride durur (üzerine gelince zemini onlarınkiyle aynı genişlikte), ikonu ve yazısı
        # durum satırlarıyla aynı eksende (bkz. _apply_compact).
        self._ai_row = _AiRow()
        self._ai_row.clicked.connect(self._open_translate)
        self._ai_box = QWidget()
        ab = QHBoxLayout(self._ai_box)
        ab.setContentsMargins(SPACE["s"], 0, SPACE["s"], SPACE["xs"])
        ab.addWidget(self._ai_row)
        self._ai_box.hide()
        sv.addWidget(self._ai_box)

        # alt bilgi: canlı durum, OBS bağlantısı, sürüm
        self._footer = QWidget()
        fv = QVBoxLayout(self._footer)
        fv.setSpacing(SPACE["xs"])
        self._live = _StatusRow("dot")
        self._obs = _StatusRow("broadcast")
        self._ver = QLabel(f"Sürüm {__version__}")
        self._ver.setStyleSheet(_qss_font(F.DISPLAY, 12, C.TEXT_3))
        self._ver.setToolTip(f"{APP_NAME} {__version__}")
        fv.addWidget(self._live)
        fv.addWidget(self._obs)
        fv.addSpacing(SPACE["s"])
        fv.addWidget(self._ver)
        sv.addWidget(self._footer)

        # geliştirici bağlantısı, en altta: Kick logosu + "Robz so good" (bkz. gui/developer.py). Yapay zekâ satırı
        # gibi kenarlardan 8 px içeride, logosu durum ikonlarıyla aynı eksende (bkz. _apply_compact).
        self._kick_row = _KickRow()
        self._kick_box = QWidget()
        kb = QHBoxLayout(self._kick_box)
        kb.setContentsMargins(SPACE["s"], SPACE["s"], SPACE["s"], 0)
        kb.addWidget(self._kick_row)
        sv.addWidget(self._kick_box)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        for key, title, cls in PAGES:
            pg = cls(self)
            self.pages[key] = pg
            self.stack.addWidget(pg)
            self.nav.add_page(title, NAV_ICONS.get(key, "help"))
        # donanım önerisini Çeviri sayfası arka planda okur (davet için); sonuç burada da kullanılır. Kurulumun
        # yüzdesi de oradan: kenar çubuğu ile sayfadaki ilerleme paneli aynı sayıyı göstersin.
        self.pages["translate"].advice_ready.connect(self._on_advice)
        self.pages["translate"].ai_progress.connect(self._update_ai_row)
        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)
        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._toasts = ToastHost(self)

        ctx.pipeline.error.connect(self._show_error)
        ctx.notified.connect(self._on_notified)  # pipeline.notice da buradan gelir (AppContext'te bağlı)
        ctx.pipeline.running_changed.connect(self._update_tray)
        ctx.pipeline.running_changed.connect(self._set_live)
        ctx.obs_clients.connect(self._set_obs)
        ctx.needs_region.connect(self.select_region)
        ctx.ai.changed.connect(self._update_ai_row)
        ctx.ai.finished.connect(self._on_ai_finished)
        ctx.pipeline.running_changed.connect(self._on_running_for_update)
        if self._upd is not None:
            self._upd.changed.connect(self._sync_update_row)
            self._upd.found.connect(self._on_update_found)
        self._set_live(bool(getattr(ctx.pipeline, "running", False)))
        self._set_obs(len(getattr(getattr(ctx, "server", None), "obs_clients", ()) or ()))
        self._update_ai_row()
        self._sync_update_row()
        self._apply_compact(self.width() < COMPACT_BELOW)
        self._build_tray()
        if ctx.server_error:
            QMessageBox.warning(self, APP_NAME, ctx.server_error)
        # ilk açılış: tanıtım turu (tur, ctx.s.tour_seen'e kendisi bakar)
        QTimer.singleShot(700, self._maybe_tour)

    # --- gezinme -------------------------------------------------------------
    def _on_nav(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.ctx.pipeline.preview_enabled = PAGES[i][0] == "home"

    def goto(self, key: str) -> None:
        for i, (k, _t, _c) in enumerate(PAGES):
            if k == key:
                self.nav.setCurrentRow(i)
                return

    def _open_translate(self) -> None:
        """Yapay zekâ durum satırı: ilerleme, İptal ve hatanın ayrıntısı Çeviri sayfasında."""
        self.goto("translate")

    def refresh_all(self) -> None:
        self.pages["home"].refresh()  # type: ignore[attr-defined]

    # --- kenar çubuğu: daralma ve durum ----------------------------------------------
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._apply_compact(self.width() < COMPACT_BELOW)

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.side.setFixedWidth(SIDEBAR_COMPACT_W if compact else SIDEBAR_W)
        self._wordmark.setVisible(not compact)
        self._ver.setVisible(not compact)
        self.nav.set_compact(compact)
        for row in (self._live, self._obs, self._update_row, self._ai_row, self._kick_row):
            row.set_compact(compact)
        # İki hizalama ekseni: ikonlar (genişte x = 30, menü ikonlarıyla aynı; darda kenar çubuğunun ortası)
        # ve yazılar (x = 52: marka adı, menü etiketleri, durum yazıları ve sürüm aynı sol kenarda).
        if compact:
            logo_x = (SIDEBAR_COMPACT_W - 1 - self._logo.width()) // 2
            icon_x = (SIDEBAR_COMPACT_W - 1 - ICON_COL) // 2
        else:
            axis = SPACE["s"] + SPACE["m"] + ICON_COL // 2
            logo_x = axis - self._logo.width() // 2
            icon_x = axis - ICON_COL // 2
        text_x = icon_x + ICON_COL + SPACE["m"]
        self._brand.layout().setContentsMargins(logo_x, 0, SPACE["l"], 0)
        self._brand.layout().setSpacing(max(SPACE["xs"], text_x - logo_x - self._logo.width()))
        self._footer.layout().setContentsMargins(icon_x, 0, SPACE["l"], 0)
        self._ver.setContentsMargins(ICON_COL + SPACE["m"], 0, 0, 0)
        # güncelleme, yapay zekâ ve Kick satırları kenarlardan SPACE["s"] içeride başlar (bkz. _update_box, _ai_box,
        # _kick_box): ikonları yine icon_x'te
        for row in (self._update_row, self._ai_row, self._kick_row):
            row.layout().setContentsMargins(icon_x - SPACE["s"], 0, SPACE["s"], 0)

    def _set_live(self, running: bool) -> None:
        if running:
            self._live.set_state(True, "Çalışıyor", "Çeviri çalışıyor")
        else:
            self._live.set_state(False, "Durduruldu", "Çeviri durduruldu")

    def _set_obs(self, n: int) -> None:
        n = int(n or 0)
        if n > 0:
            tip = "OBS'te tarayıcı kaynağı bağlı" if n == 1 else f"OBS'te {n} tarayıcı kaynağı bağlı"
            self._obs.set_state(True, "OBS bağlı", tip)
        else:
            self._obs.set_state(False, "OBS bağlı değil", OBS_NOT_CONNECTED_TIP)

    def _update_ai_row(self, *_args) -> None:
        """Yapay zekâ durum satırı: yalnız iş sürerken ya da hata varken görünür."""
        status = ai_status(self.ctx.ai, self.pages["translate"].ai_fraction)
        if status is not None:
            self._ai_row.set_status(*status)
        self._ai_box.setVisible(status is not None)

    def _sync_update_row(self) -> None:
        """Güncelleme satırı: yalnız yeni sürüm varken (atlanmamış) ya da indirme/kurulum/hata durumunda görünür."""
        status = sidebar_status(self._upd)
        if status is not None:
            self._update_row.set_status(*status)
        self._update_box.setVisible(status is not None)

    # --- güncelleme ----------------------------------------------------------------------------------------------
    def _on_update_found(self, release: Any, manual: bool) -> None:
        """Atlanmamış yeni sürüm bulundu. Şimdi denetle'den: pencere hemen açılır. Kendiliğinden denetimde: bir kez
        kısa, tıklanabilir bildirim (uygun an gelince; bkz. _maybe_update_toast)."""
        self._sync_update_row()
        if manual:
            self.open_update_dialog()
            return
        self._update_toast_for = str(getattr(release, "version", "") or "")
        self._maybe_update_toast()

    def _maybe_update_toast(self) -> None:
        """Bekleyen "yeni sürüm" bildirimini gösterir: açılış kararlarından sonra, çeviri çalışmıyorken (yayına ve oyuna
        dikkat dağıtmasın), pencere görünürken ve tur açık değilken. Tepsi balonu yok: kenar çubuğundaki satır kalır."""
        version, upd = self._update_toast_for, self._upd
        if not version or upd is None or not self._startup_done:
            return
        avail = upd.available
        if avail is None or avail.version != version or upd.phase != "idle":
            self._update_toast_for = ""  # atlandı, indiriliyor ya da artık yeni değil
            return
        if bool(getattr(self.ctx.pipeline, "running", False)):
            return  # çeviri durunca (_on_running_for_update)
        if not self.isVisible() or self.isMinimized():
            return  # pencere açılınca (showEvent)
        if self._update_dlg is not None and self._update_dlg.isVisible():
            self._update_toast_for = ""
            return
        from altyazitr.gui import tour

        if tour.active_tour(self) is not None:
            return  # tur bitince sonraki fırsatta (çeviri durunca, pencere açılınca)
        self._update_toast_for = ""
        try:
            t = self._toasts.show(TOAST_TEXT.format(v=version), "info", TOAST_MS)
        except Exception:
            log.exception("Güncelleme bildirimi gösterilemedi")
            return
        if t is not None and t is not self._update_toast:
            self._update_toast = t
            t.clicked.connect(self.open_update_dialog)

    def _on_running_for_update(self, running: bool) -> None:
        if not running:
            self._maybe_update_toast()

    def open_update_dialog(self) -> None:
        """Güncelleme penceresi (kenar çubuğu satırı, bildirim, Ayarlar → Güncelle…). Tek pencere; açıksa öne gelir."""
        upd = self._upd
        if upd is None or upd.shown is None:
            return
        if not self.isVisible() or self.isMinimized():
            self._restore()
        dlg = self._update_dlg
        if dlg is None:
            dlg = UpdateDialog(self, upd, running=lambda: bool(getattr(self.ctx.pipeline, "running", False)),
                               on_install=self.install_update)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dlg.finished.connect(self._update_dialog_closed)
            if self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):  # testler: ekranda açılmasın
                dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self._update_dlg = dlg
        dlg.open()
        dlg.raise_()
        dlg.activateWindow()

    def _update_dialog_closed(self, *_args) -> None:
        self._update_dlg = None

    def install_update(self) -> None:
        """Güncelleme penceresinden: doğrulanmış kurulum başlar ve uygulama kapanır (AppContext.shutdown ayarları
        kaydeder, hattı durdurur, yapay zekâ modelini boşaltır; kurulum süreç bitince dosyaları değiştirir ve uygulamayı
        yeniden açar). Kurulum başlatılamazsa pencere hatayı gösterir, uygulama açık kalır."""
        if self._upd is not None and self._upd.install():
            if self._update_dlg is not None:
                self._update_dlg.hide()
            self.quit_app()

    # --- bildirim --------------------------------------------------------------------
    def toast(self, msg: str, kind: str = "info", timeout_ms: int | None = None) -> None:
        """Sağ altta kısa, kendiliğinden kaybolan bildirim. kind: info / success / warn / error."""
        try:
            self._toasts.show(msg, kind, timeout_ms)
        except Exception:
            log.exception("Bildirim gösterilemedi")

    def _on_notified(self, text: str, detail: str, level: str) -> None:
        """Pencere açmayan bildirimlerin tek kanalı (AppContext.notified: kısa metin, ayrıntı, "info" | "warn").
        Pencere görünürken sağ altta kısa bildirim (yalnız kısa metin); tepsideyken ya da simge durumundayken tepsi
        balonu, ayrıntısıyla (uzun işin sonucu kaçmasın). Çeviri çalışırken tepsi balonu çıkmaz: yayıncı ekranını
        yakalıyorsa Windows bildirimi yayına düşer. Yedek çeviriye geçildiğini Ana sayfadaki durum satırı zaten
        gösterir (pipeline.status)."""
        if self.isVisible() and not self.isMinimized():
            self.toast(text, level, NOTICE_MS)
        elif self.tray.isVisible() and not self.ctx.pipeline.running:
            warn = level in ("warn", "error")
            icon_kind = QSystemTrayIcon.MessageIcon.Warning if warn else QSystemTrayIcon.MessageIcon.Information
            self.tray.showMessage(APP_NAME, f"{text}\n{detail}" if detail else text, icon_kind, 8000)

    def _on_ai_finished(self, ok: bool, message: str) -> None:
        """Yapay zekâ kurulumu bitti: sonuç ctx.notify ile (başarı "info", hata "warn"). Başarıda motor zaten yapay
        zekâya geçti (servis); çeviri çalışıyorsa yeniden başlatılmaz, bir sonraki Başlat'ta devreye girer. Kullanıcı
        iptal ettiyse bildirim yok (iptalden sonra servisin durumu "idle", hatadan sonra "failed")."""
        ai = self.ctx.ai
        if ok:
            plan = getattr(ai, "active_plan", None)
            if plan is not None and not getattr(plan, "apply", True):  # "Modeli indir": yalnız model indi
                self.ctx.notify(message or "Model indirildi", "info")
            elif getattr(ai, "speed_verdict", "") == "slow":  # hız testi (Faz 2): motor değişmedi, ileti açıklar
                self.ctx.notify(message, "warn")
            else:
                running = bool(getattr(self.ctx.pipeline, "running", False))
                self.ctx.notify(TEXT_READY_LATER if running else TEXT_READY, "info")
        elif str(getattr(ai, "state", "") or "") == "failed":
            self.ctx.notify(f"{AI_FAILED}\n{error_note(message)}", "warn")  # ayrıntı: hatanın kısa metni

    def _on_advice(self, advice: Any) -> None:
        self._advice, self._advice_known = advice, True
        self._maybe_invite()

    def _maybe_invite(self) -> None:
        """1.1.0 ve öncesinden güncelleyen kullanıcıya bir kez "Yeni: yapay zekâ çevirisi" (settings.ai.invite_pending).
        Açılıştaki tur kararından sonra, pencere görünürken ve tur açık değilken gösterilir; sonra işaret kalkar ve
        kaydedilir. Kart uygun değilse (kademe none/weak) ya da kurulum zaten başladıysa bildirim gösterilmeden
        kalkar. Donanım önerisi okunamadıysa sonraki açılışa kalır."""
        cfg = self.ctx.s.ai
        if not cfg.invite_pending or not self._startup_done or not self._advice_known or self._advice is None:
            return
        if not self.isVisible():
            return  # tepsiden açılınca (showEvent)
        from altyazitr.gui import tour

        if tour.active_tour(self) is not None:
            return  # tur karartması altında kaybolmasın: sonraki açılışta
        idle = str(getattr(self.ctx.ai, "state", "") or "idle") == "idle"
        if invite_offered(self._advice) and idle and self.ctx.s.translate.engine == "quickmt":
            self.ctx.notify(AI_INVITE, "info")
        cfg.invite_pending = False
        self.ctx.save_later()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._maybe_invite()
        self._maybe_update_toast()

    def _show_update_result(self) -> None:
        """Güncellemeden sonraki ilk açılış: sonuç bir kez ("Robz Vision X sürümüne güncellendi" ya da kurulamadı)."""
        note = getattr(self._upd, "post_note", None)
        if note:
            self._upd.post_note = None
            text, level = note
            self.ctx.notify(text, level)

    # --- bölge seçimi --------------------------------------------------------
    def select_region(self) -> None:
        if self.ctx.s.capture.mode == "window":
            self._select_in_window()
        else:
            self._select_on_screen()

    def _select_on_screen(self) -> None:
        if self._picker is not None:
            return
        was_visible = self.isVisible()
        self.hide()  # ana pencere seçilecek alanı kapatmasın

        def done(rect) -> None:
            self._picker = None
            if was_visible:
                self.showNormal()
                self.raise_()
                self.activateWindow()
            if rect:
                self.ctx.s.capture.rect = [int(v) for v in rect]
                self._region_saved()

        self._picker = ScreenRegionPicker(self.ctx.s.capture.rect)
        self._picker.finished.connect(done)
        QTimer.singleShot(150, self._picker.start)  # ana pencere gizlensin, sonra katman açılsın

    def _select_in_window(self) -> None:
        c = self.ctx.s.capture
        if not (c.window_exe or c.window_title):
            if self.isVisible():
                self.toast("Önce listeden bir pencere seçin", "warn")
            else:
                QMessageBox.information(self, APP_NAME, "Önce listeden bir pencere seçin.")
            return
        frame = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self.ctx.pipeline.running:
                frame = self.ctx.pipeline.grab_full_frame(2.0)
            if frame is None:
                win = winutil.find_window(c.window_exe, c.window_title)
                if win is not None and not winutil.is_minimized(win.hwnd):
                    frame = snapshot_window(win.hwnd, 3.0)
        except Exception:
            log.exception("Pencere görüntüsü alınamadı")
        finally:
            QApplication.restoreOverrideCursor()
        if frame is None or frame.size == 0:
            QMessageBox.warning(self, APP_NAME, "Pencereden görüntü alınamadı. Pencerenin açık olduğundan ve "
                                                "simge durumunda olmadığından emin olun.")
            return
        dlg = RegionDialog(frame, tuple(c.region), self)
        if dlg.exec() and dlg.result_fraction:
            c.region = [round(v, 4) for v in dlg.result_fraction]
            self._region_saved()

    def _region_saved(self, toast: bool = True) -> None:
        self.ctx.save_later()
        self.ctx.pipeline.tracker.key = None
        self.refresh_all()
        self.ctx.display.push_style()
        if toast:
            self.toast("Bölge kaydedildi", "success")

    def region_edited(self) -> None:
        """Ana sayfanın canlı önizlemesinde bölge değişti (sürükleyip bırakma, ok tuşu): kaydedilir ve hemen geçerli
        olur; değişikliği kullanıcı zaten gördüğü için bildirim yok."""
        self._region_saved(toast=False)

    # --- hata / ilk çalıştırma -------------------------------------------------
    def _show_error(self, msg: str) -> None:
        if self.isVisible():
            QMessageBox.warning(self, APP_NAME, msg)
        elif self.tray.isVisible():
            self.tray.showMessage(APP_NAME, msg[:250], QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _maybe_tour(self) -> None:
        """Açılıştan kısa süre sonra: tur daha önce görülmediyse başlatır. Tur hatası açılışı asla bozmaz. Ardından
        (tur yoksa) güncelleyen kullanıcıya yapay zekâ daveti."""
        try:
            if self.isVisible():  # tepsiden başlatıldıysa tur bir sonraki görünür açılışa kalır
                from altyazitr.gui import tour

                tour.maybe_start_tour(self)
        except Exception:
            log.exception("Tanıtım turu başlatılamadı")
        self._first_run()
        self._startup_done = True
        self._maybe_invite()
        self._show_update_result()
        self._maybe_update_toast()

    def _first_run(self) -> None:
        if self.ctx.s.first_run:
            self.ctx.s.first_run = False
            self.ctx.save_later()

    def start_tour(self) -> None:
        """Tanıtım turunu baştan başlatır (tepsi menüsü). Pencereyi önce öne getirir."""
        self._restore()

        def go() -> None:
            try:
                from altyazitr.gui import tour

                tour.start_tour(self)
            except Exception:
                log.exception("Tanıtım turu başlatılamadı")

        QTimer.singleShot(150, go)  # pencere gösterilip yerleşsin, sonra tur hedefleri ölçsün

    # --- sistem tepsisi ---------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        a_show = QAction(icon("app-window", C.TEXT_2), "Göster", self)
        a_show.triggered.connect(self._restore)
        self.a_toggle = QAction(icon("play", C.TEXT_2), "Başlat", self)
        self.a_toggle.triggered.connect(self.ctx.toggle_running)
        a_vis = QAction(icon("eye", C.TEXT_2), "Altyazıyı gizle / göster", self)
        a_vis.triggered.connect(self.ctx.display.toggle_visible)
        self.a_tour = QAction(icon("help", C.TEXT_2), "Tanıtım turu", self)
        self.a_tour.triggered.connect(self.start_tour)
        a_quit = QAction(icon("power", C.TEXT_2), "Çıkış", self)
        a_quit.triggered.connect(self.quit_app)
        for a in (a_show, self.a_toggle, a_vis):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.a_tour)
        menu.addSeparator()
        menu.addAction(a_quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore() if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                         QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()

    def _update_tray(self, running: bool) -> None:
        self.a_toggle.setText("Durdur" if running else "Başlat")
        self.a_toggle.setIcon(icon("stop" if running else "play", C.TEXT_2))
        self.tray.setToolTip(f"{APP_NAME} {'çalışıyor' if running else 'durduruldu'}")

    def _restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        if self._picker is not None:  # bölge seçimi sırasında gizlenmiş olabilir
            e.ignore()
            return
        if self._quitting or not self.ctx.s.minimize_to_tray or not QSystemTrayIcon.isSystemTrayAvailable():
            self._quitting = True
            self.tray.hide()
            e.accept()
            QApplication.quit()
            return
        e.ignore()
        self.hide()
        if not self._tray_notified:
            self._tray_notified = True
            self.tray.showMessage(APP_NAME, "Arka planda çalışmaya devam ediyor. Kapatmak için tepsi simgesine "
                                            "sağ tıklayıp Çıkış'ı seçin.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)
