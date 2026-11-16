"""Ana pencere: sol kenar çubuğu + 4 sayfa, pencere içi bildirimler, sistem tepsisi, bölge seçimi akışı."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
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
from altyazitr.capture import snapshot_window
from altyazitr.gui.home_page import HomePage
from altyazitr.gui.icons import icon, pixmap
from altyazitr.gui.region_dialog import RegionDialog
from altyazitr.gui.region_picker import ScreenRegionPicker
from altyazitr.gui.settings_page import SettingsPage
from altyazitr.gui.style_page import StylePage
from altyazitr.gui.theme import app_icon
from altyazitr.gui.toast import ToastHost
from altyazitr.gui.tokens import RADIUS, SPACE, C, F
from altyazitr.gui.translate_page import TranslatePage

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
        self._on = False
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
        self._on = on
        color = C.TURQUOISE if on else C.TEXT_3
        self.ic.setPixmap(pixmap(self._icon_name, 16, color, dpr=max(2.0, _dpr(self))))
        self.tx.setText(text)
        self.tx.setStyleSheet(_qss_font(F.DISPLAY, 13, C.TEXT if on else C.TEXT_3))
        self.setToolTip(tip)
        self.setAccessibleName(text)

    def set_compact(self, compact: bool) -> None:
        self.tx.setVisible(not compact)


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
        self.resize(980, 760)
        self.setMinimumSize(780, 560)
        self._tray_notified = False
        self._quitting = False
        self._picker: ScreenRegionPicker | None = None
        self._compact: bool | None = None

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.side = side = _Sidebar()
        side.setFixedWidth(SIDEBAR_W)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, SPACE["l"] + SPACE["xs"], 1, SPACE["l"])
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

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        for key, title, cls in PAGES:
            pg = cls(self)
            self.pages[key] = pg
            self.stack.addWidget(pg)
            self.nav.add_page(title, NAV_ICONS.get(key, "help"))
        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)
        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._toasts = ToastHost(self)

        ctx.pipeline.error.connect(self._show_error)
        ctx.pipeline.running_changed.connect(self._update_tray)
        ctx.pipeline.running_changed.connect(self._set_live)
        ctx.obs_clients.connect(self._set_obs)
        ctx.needs_region.connect(self.select_region)
        self._set_live(bool(getattr(ctx.pipeline, "running", False)))
        self._set_obs(len(getattr(getattr(ctx, "server", None), "clients", ()) or ()))
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
        for row in (self._live, self._obs):
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
            self._obs.set_state(False, "OBS bağlı değil", "OBS'e bağlı tarayıcı kaynağı yok")

    # --- bildirim --------------------------------------------------------------------
    def toast(self, msg: str, kind: str = "info") -> None:
        """Sağ altta kısa, kendiliğinden kaybolan bildirim. kind: info / success / warn / error."""
        try:
            self._toasts.show(msg, kind)
        except Exception:
            log.exception("Bildirim gösterilemedi")

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

    def _region_saved(self) -> None:
        self.ctx.save_later()
        self.ctx.pipeline.tracker.key = None
        self.refresh_all()
        self.ctx.display.push_style()
        self.toast("Bölge kaydedildi", "success")

    # --- hata / ilk çalıştırma -------------------------------------------------
    def _show_error(self, msg: str) -> None:
        if self.isVisible():
            QMessageBox.warning(self, APP_NAME, msg)
        elif self.tray.isVisible():
            self.tray.showMessage(APP_NAME, msg[:250], QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _maybe_tour(self) -> None:
        """Açılıştan kısa süre sonra: tur daha önce görülmediyse başlatır. Tur hatası açılışı asla bozmaz."""
        try:
            if self.isVisible():  # tepsiden başlatıldıysa tur bir sonraki görünür açılışa kalır
                from altyazitr.gui import tour

                tour.maybe_start_tour(self)
        except Exception:
            log.exception("Tanıtım turu başlatılamadı")
        self._first_run()

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
