"""Koyu tema "Ocean Drive gecesi" (Fusion + QPalette + QSS) ve uygulama simgesi (hazır logo).

Tüm renkler/yazılar gui/tokens.py'den gelir; bileşen sözleşmesi DESIGN.md'de anlatılır.
Klavye odağı "focus-visible" mantığıyla gösterilir: düğme, onay kutusu, kaydırıcı gibi öğelerde
camgöbeği odak halkası yalnızca Tab/kısayolla gelindiğinde çizilir (fareyle tıklayınca değil);
metin giriş alanlarında ise odak her zaman görünür.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from altyazitr.gui.tokens import RADIUS, C, F, rgba

# QSS yazı ailesi dizgeleri
_DISPLAY = f"'{F.DISPLAY}', '{F.BODY_FALLBACK}'"
_DISPLAY_SB = f"'{F.DISPLAY_SEMIBOLD}', '{F.DISPLAY}', '{F.BODY_FALLBACK}'"
_DISPLAY_COND = f"'{F.DISPLAY_CONDENSED}', '{F.DISPLAY_SEMIBOLD}', '{F.BODY_FALLBACK}'"

_RS, _RM, _RL = RADIUS["s"], RADIUS["m"], RADIUS["l"]
_BORDER_HOVER = rgba(C.TEXT_3, 0.55)      # giriş/düğme kenarı, üzerine gelince
_PRESSED_DANGER = rgba(C.DANGER, 0.24)

# Klavye odağı seçicisi (bkz. _FocusRing): yalnızca Tab/kısayolla odaklanınca eşleşir.
_KB = '[kbfocus="true"]:focus'
_KBP = '[kbfocus="true"]'  # alt denetimli seçicilerde: QCheckBox{_KBP}::indicator:focus


def build_qss() -> str:
    """Görsel gerektirmeyen temel stil sayfası."""
    return f"""
/* ---------- zeminler ---------- */
QMainWindow, QDialog, QMessageBox {{ background: {C.BG}; }}
QWidget#page {{ background: {C.BG}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QStackedWidget {{ background: {C.BG}; }}

/* ---------- yazı ---------- */
QLabel {{ color: {C.TEXT}; background: transparent; }}
QLabel:disabled {{ color: {C.TEXT_DISABLED}; }}
/* Bahnschrift'te "İ" noktası satır üstünü aşar: üstte pay bırak (seçilebilir etiket yapmayın, bkz. widgets.label) */
QLabel#pageTitle {{
    font-family: {_DISPLAY_COND}; font-size: {F.PAGE_TITLE}px; color: {C.TEXT}; padding-top: 3px;
}}
QLabel#section {{ font-family: {_DISPLAY_SB}; font-size: {F.SECTION}px; color: {C.TEXT}; padding-top: 2px; }}
QLabel#status {{ font-family: {_DISPLAY_SB}; font-size: {F.STATUS}px; color: {C.TEXT}; padding-top: 3px; }}
/* QSS iç boşluğu QLabel'e çerçeve kazandırır ve Qt metni yarım "x" kadar içeri iter; bunu sıfırla */
QLabel#pageTitle, QLabel#section, QLabel#status, QLabel#value {{ qproperty-indent: 0; }}
QLabel#muted, QLabel#field {{ color: {C.TEXT_2}; }}
QLabel#muted:disabled, QLabel#field:disabled {{ color: {C.TEXT_DISABLED}; }}
QLabel#field {{ min-width: 120px; }}
QLabel#value {{ font-family: {_DISPLAY}; font-size: {F.BUTTON}px; color: {C.TEXT_2}; padding-top: 2px; }}
QLabel#pill, QLabel#pillOn, QLabel#pillWarn {{
    font-family: {_DISPLAY}; font-size: {F.SMALL + 1}px; border-radius: 10px; padding: 3px 10px;
    min-height: 16px; max-height: 16px;  /* toplam 22 px; köşe yarıçapı yüksekliğin yarısını aşmasın */
}}
QLabel#pill {{ background: {C.RAISED}; color: {C.TEXT_2}; }}
QLabel#pillOn {{ background: {C.TURQ_SOFT}; color: {C.TURQUOISE}; }}
QLabel#pillWarn {{ background: {C.ORANGE_SOFT}; color: {C.ORANGE}; }}
QLabel#preview {{ background: {C.PANEL}; border: 1px solid {C.LINE}; border-radius: {_RL}px; }}

/* ---------- bölümler ---------- */
QFrame#card {{ background: transparent; border: none; border-top: 1px solid {C.LINE}; border-radius: 0; }}
QFrame#live {{ background: {C.PANEL}; border: 1px solid {C.LINE}; border-radius: {_RL}px; }}

/* ---------- düğmeler ---------- */
QPushButton {{
    font-family: {_DISPLAY}; font-size: {F.BUTTON}px; color: {C.TEXT};
    background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; border-radius: {_RM}px; padding: 6px 14px;
    outline: none;
}}
QPushButton:hover {{ background: {C.HOVER}; border-color: {_BORDER_HOVER}; }}
QPushButton:pressed {{ background: {C.PRESSED}; }}
QPushButton:checked {{ background: {C.CYAN_SOFT}; border-color: {C.CYAN}; }}
QPushButton:disabled {{ color: {C.TEXT_DISABLED}; background: transparent; border-color: {C.LINE}; }}
QPushButton{_KB} {{ border-color: {C.CYAN}; }}

QPushButton#primary {{
    font-family: {_DISPLAY_SB}; color: {C.INVERSE}; background: {C.PINK}; border: 1px solid {C.PINK};
    padding: 6px 18px;
}}
QPushButton#primary:hover {{ background: {C.PINK_HOVER}; border-color: {C.PINK_HOVER}; }}
QPushButton#primary:pressed {{ background: {C.PINK_PRESSED}; border-color: {C.PINK_PRESSED}; }}
QPushButton#primary:disabled {{ color: {C.TEXT_DISABLED}; background: {C.RAISED}; border-color: {C.LINE}; }}
QPushButton#primary{_KB} {{ border: 2px solid {C.CYAN}; padding: 5px 17px; }}

QPushButton#danger {{
    font-family: {_DISPLAY_SB}; color: {C.DANGER}; background: transparent; border: 1px solid {C.DANGER};
    padding: 6px 18px;
}}
QPushButton#danger:hover {{ background: {C.DANGER_SOFT}; }}
QPushButton#danger:pressed {{ background: {_PRESSED_DANGER}; }}
QPushButton#danger:disabled {{ color: {C.TEXT_DISABLED}; border-color: {C.LINE}; }}
QPushButton#danger{_KB} {{ border: 2px solid {C.CYAN}; padding: 5px 17px; }}

QPushButton#ghost {{ color: {C.TEXT_2}; background: transparent; border: 1px solid transparent; }}
QPushButton#ghost:hover {{ color: {C.TEXT}; background: {C.HOVER}; }}
QPushButton#ghost:pressed {{ background: {C.PRESSED}; }}
QPushButton#ghost:checked {{ color: {C.TEXT}; background: {C.CYAN_SOFT}; border-color: {C.CYAN}; }}
QPushButton#ghost:disabled {{ color: {C.TEXT_DISABLED}; background: transparent; }}
QPushButton#ghost{_KB} {{ border-color: {C.CYAN}; }}

QPushButton#icon {{
    padding: 0; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
}}
QPushButton#icon:disabled {{ background: transparent; }}

/* katlanır bölüm başlığı */
QPushButton#collapse {{
    font-family: {_DISPLAY}; font-size: {F.BUTTON}px; color: {C.TEXT_2}; background: transparent;
    border: 1px solid transparent; border-radius: {_RS}px; padding: 5px 8px 5px 2px; text-align: left;
}}
QPushButton#collapse:hover {{ color: {C.TEXT}; background: transparent; }}
QPushButton#collapse:pressed {{ background: transparent; }}
QPushButton#collapse{_KB} {{ border-color: {C.CYAN}; }}

/* hazır stil kartı (seçili halka kodda çizilir) */
QPushButton#preset {{ background: {C.PANEL}; border: 1px solid {C.LINE}; border-radius: {_RL}px; padding: 0; }}
QPushButton#preset:hover {{ background: {C.RAISED}; border-color: {C.LINE_STRONG}; }}
QPushButton#preset:pressed {{ background: {C.PRESSED}; }}
QPushButton#preset{_KB} {{ border-color: {C.CYAN}; }}

/* renk yuvarlağı (tamamen kodda çizilir) */
QPushButton#swatch, QPushButton#swatch:hover, QPushButton#swatch:pressed, QPushButton#swatch:checked {{
    background: transparent; border: none; padding: 0; min-width: 0; min-height: 0;
}}

/* ---------- segmentli seçici ---------- */
QFrame#segmented {{ background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; border-radius: 7px; }}
QFrame#segmented:disabled {{ background: transparent; border-color: {C.LINE}; }}
QFrame#segmented QPushButton#segL, QFrame#segmented QPushButton#segM, QFrame#segmented QPushButton#segR {{
    font-family: {_DISPLAY}; font-size: {F.BODY_SIZE + 1}px; color: {C.TEXT_2}; background: transparent;
    border: 1px solid transparent; border-radius: {_RS + 1}px; padding: 3px 12px; margin: 0;
}}
QFrame#segmented QPushButton#segL:hover, QFrame#segmented QPushButton#segM:hover,
QFrame#segmented QPushButton#segR:hover {{ color: {C.TEXT}; background: {C.HOVER}; }}
QFrame#segmented QPushButton#segL:checked, QFrame#segmented QPushButton#segM:checked,
QFrame#segmented QPushButton#segR:checked {{ color: {C.TEXT}; background: {C.CYAN_SOFT}; border-color: {C.CYAN}; }}
QFrame#segmented QPushButton#segL:disabled, QFrame#segmented QPushButton#segM:disabled,
QFrame#segmented QPushButton#segR:disabled {{ color: {C.TEXT_DISABLED}; background: transparent; }}
QFrame#segmented QPushButton#segL:checked:disabled, QFrame#segmented QPushButton#segM:checked:disabled,
QFrame#segmented QPushButton#segR:checked:disabled {{ background: {C.LINE}; border-color: {C.LINE_STRONG}; }}
QFrame#segmented QPushButton#segL{_KB}, QFrame#segmented QPushButton#segM{_KB},
QFrame#segmented QPushButton#segR{_KB} {{ border-color: {C.CYAN}; }}

/* ---------- metin girişleri ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    color: {C.TEXT}; background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; border-radius: {_RM}px;
    padding: 5px 10px; selection-background-color: {C.CYAN}; selection-color: {C.INVERSE};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {_BORDER_HOVER}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:editable:focus, QComboBox:on,
QComboBox{_KB} {{ border-color: {C.CYAN}; }}
QLineEdit:read-only {{ background: {C.PANEL}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled {{ color: {C.TEXT_DISABLED}; background: {C.PANEL}; border-color: {C.LINE}; }}
QLineEdit {{ placeholder-text-color: {C.TEXT_3}; }}
/* bileşik denetimlerin içindeki satır kutusu */
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit, QComboBox QLineEdit {{
    border: none; background: transparent; padding: 0; min-width: 0;
}}
QSpinBox, QDoubleSpinBox {{ font-family: {_DISPLAY}; font-size: {F.BUTTON}px; padding: 4px 8px; }}

/* kısayol kutusu: odaktayken tuş dinler */
QLineEdit#hotkey {{ font-family: {_DISPLAY}; font-size: {F.BUTTON}px; background: {C.RAISED}; }}
QLineEdit#hotkey:hover {{ background: {C.HOVER}; }}
QLineEdit#hotkey:focus {{ background: {C.CYAN_SOFT}; border-color: {C.CYAN}; }}

/* açılır liste */
QComboBox {{ padding-right: 30px; combobox-popup: 0; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none;
}}
QComboBox QAbstractItemView {{
    background: {C.RAISED}; color: {C.TEXT}; border: 1px solid {C.LINE_STRONG}; padding: 4px; outline: 0;
    selection-background-color: {C.CYAN_SOFT}; selection-color: {C.TEXT};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: {_RS}px; }}
QComboBox QAbstractItemView::item:hover {{ background: {C.HOVER}; }}
QComboBox QAbstractItemView::item:selected {{ background: {C.CYAN_SOFT}; color: {C.TEXT}; }}

/* ---------- onay kutusu ---------- */
QCheckBox {{ color: {C.TEXT}; spacing: 10px; background: transparent; outline: none; }}
QCheckBox:disabled {{ color: {C.TEXT_DISABLED}; }}

/* ---------- kaydırıcı ---------- */
QSlider {{ background: transparent; min-height: 22px; outline: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {C.LINE_STRONG}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ height: 4px; background: {C.CYAN}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ height: 4px; background: {C.LINE_STRONG}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {C.TEXT}; border: 2px solid {C.TEXT}; width: 12px; height: 12px; margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:pressed {{ background: {C.CYAN}; border-color: {C.CYAN}; }}
QSlider{_KBP}::handle:horizontal:focus {{ background: {C.TEXT}; border-color: {C.CYAN}; }}
QSlider::sub-page:horizontal:disabled {{ background: {C.TEXT_DISABLED}; }}
QSlider::handle:horizontal:disabled {{ background: {C.TEXT_DISABLED}; border-color: {C.TEXT_DISABLED}; }}

/* ---------- ilerleme çubuğu (ince) ---------- */
QProgressBar {{
    background: {C.LINE}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px;
    color: transparent; text-align: center;
}}
QProgressBar::chunk {{ background: {C.PINK}; border-radius: 3px; }}

/* ---------- tablo ---------- */
QTableView, QTableWidget, QListView {{
    background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.LINE}; border-radius: {_RM}px;
    gridline-color: {C.LINE}; alternate-background-color: {C.RAISED}; outline: 0;
    selection-background-color: {C.CYAN_SOFT}; selection-color: {C.TEXT};
}}
QTableView:focus, QTableWidget:focus {{ border-color: {C.LINE_STRONG}; }}
QTableView::item {{ padding: 4px 8px; }}
QTableView::item:selected {{ background: transparent; color: {C.TEXT}; }}  /* zemin: selection-background */
QTableView QLineEdit {{ border: 1px solid {C.CYAN}; border-radius: 0; padding: 0 6px; background: {C.RAISED}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    font-family: {_DISPLAY}; font-size: {F.BODY_SIZE}px; color: {C.TEXT_2}; background: {C.PANEL};
    border: none; border-bottom: 1px solid {C.LINE_STRONG}; padding: 7px 10px;
}}
QTableCornerButton::section {{ background: {C.PANEL}; border: none; }}

/* ---------- kaydırma çubuğu ---------- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px 2px 2px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px 2px 2px 2px; }}
QScrollBar::handle:vertical {{ background: {C.LINE_STRONG}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: {C.LINE_STRONG}; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {C.TEXT_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- açılır yüzeyler ---------- */
QToolTip {{
    color: {C.TEXT}; background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; padding: 6px 8px;
}}
QMenu {{ color: {C.TEXT}; background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: {_RS}px; background: transparent; }}
QMenu::item:selected {{ background: {C.HOVER}; }}
QMenu::item:disabled {{ color: {C.TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {C.LINE_STRONG}; margin: 6px 4px; }}
QMessageBox QLabel {{ color: {C.TEXT}; }}
QMessageBox QPushButton, QDialogButtonBox QPushButton {{ min-width: 72px; }}
"""


QSS = build_qss()


def _ui_images() -> dict[str, str]:
    """QSS için ok ve onay işareti görsellerini çizip kaydeder (SVG eklentisine bağımlı olmamak
    için). Dosya adları temaya özgüdür; böylece çalışan eski bir sürümün görselleri değişmez."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPen, QPolygonF

    from altyazitr.paths import DATA_DIR

    d = DATA_DIR / "arayuz"
    d.mkdir(parents=True, exist_ok=True)
    out = {}

    def save(name: str, draw) -> None:
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(p)
        p.end()
        path = d / f"od_{name}.png"
        pm.save(str(path))
        out[name] = path.as_posix()

    def pen(color: str, width: float) -> QPen:
        return QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def chevron(up: bool, color: str):
        def draw(p: QPainter) -> None:
            p.setPen(pen(color, 3.0))
            pts = [QPointF(8, 20), QPointF(16, 12), QPointF(24, 20)] if up else \
                [QPointF(8, 12), QPointF(16, 20), QPointF(24, 12)]
            p.drawPolyline(QPolygonF(pts))
        return draw

    def check(color: str):
        def draw(p: QPainter) -> None:
            p.setPen(pen(color, 3.6))
            p.drawPolyline(QPolygonF([QPointF(8, 16.5), QPointF(13.5, 22), QPointF(24, 10.5)]))
        return draw

    save("yukari", chevron(True, C.TEXT_2))
    save("asagi", chevron(False, C.TEXT_2))
    save("yukari_pasif", chevron(True, C.TEXT_DISABLED))
    save("asagi_pasif", chevron(False, C.TEXT_DISABLED))
    save("onay", check(C.INVERSE))
    save("onay_pasif", check(C.TEXT_3))
    return out


def _extra_qss(img: dict[str, str]) -> str:
    """Çizilmiş görsellere dayanan kurallar: oklar ve onay işareti."""
    return f"""
QSpinBox, QDoubleSpinBox {{ padding-right: 26px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none;
    border-left: 1px solid {C.LINE_STRONG}; border-top-right-radius: {_RM}px; background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none;
    border-left: 1px solid {C.LINE_STRONG}; border-bottom-right-radius: {_RM}px; background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {C.HOVER}; }}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: {C.PRESSED}; }}
QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled {{ border-left-color: {C.LINE}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{img['yukari']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{img['asagi']}"); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:off {{ image: url("{img['yukari_pasif']}"); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled,
QSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:off {{ image: url("{img['asagi_pasif']}"); }}
QComboBox::down-arrow {{ image: url("{img['asagi']}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url("{img['asagi_pasif']}"); }}

QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px; border: 1px solid {C.TEXT_3}; background: {C.RAISED};
}}
QCheckBox::indicator:hover {{ border-color: {C.TEXT_2}; background: {C.HOVER}; }}
QCheckBox::indicator:checked {{ background: {C.CYAN}; border-color: {C.CYAN}; image: url("{img['onay']}"); }}
QCheckBox::indicator:checked:hover {{ background: {C.CYAN}; border-color: {C.TEXT}; }}
QCheckBox::indicator:disabled {{ background: {C.PANEL}; border-color: {C.LINE_STRONG}; }}
QCheckBox::indicator:checked:disabled {{
    background: {C.LINE_STRONG}; border-color: {C.LINE_STRONG}; image: url("{img['onay_pasif']}");
}}
QCheckBox{_KBP}::indicator:focus {{ border: 2px solid {C.CYAN}; width: 16px; height: 16px; }}
"""


# --- klavye odağı ("focus-visible") ------------------------------------------------------------
class _FocusRing(QObject):
    """Odak Tab/kısayolla geldiyse odaklanan öğeye `kbfocus=true` özelliği verir; QSS bunu
    `[kbfocus="true"]:focus` ile seçip camgöbeği halkayı çizer. Fareyle tıklamada halka çıkmaz.
    Yalnızca odak değiştiğinde çalışır (olay süzgeci yok, maliyeti yok denecek kadar az)."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        app.focusChanged.connect(self._changed)

    @staticmethod
    def _set(w: QWidget, on: bool) -> None:
        if bool(w.property("kbfocus")) == on:
            return
        w.setProperty("kbfocus", on)
        st = w.style()
        st.unpolish(w)
        st.polish(w)
        w.update()

    def _changed(self, old: QWidget | None, new: QWidget | None) -> None:
        try:
            if old is not None:
                self._set(old, False)
            if new is not None:
                kb = new.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
                self._set(new, kb)
        except RuntimeError:  # silinmiş nesne
            pass


def _palette() -> QPalette:
    pal = QPalette()
    R = QPalette.ColorRole
    roles = {
        R.Window: C.BG, R.WindowText: C.TEXT, R.Base: C.RAISED, R.AlternateBase: C.PANEL,
        R.Text: C.TEXT, R.Button: C.RAISED, R.ButtonText: C.TEXT, R.BrightText: C.TEXT,
        R.Highlight: C.CYAN, R.HighlightedText: C.INVERSE, R.ToolTipBase: C.RAISED, R.ToolTipText: C.TEXT,
        R.PlaceholderText: C.TEXT_3, R.Link: C.CYAN, R.LinkVisited: C.CYAN,
        R.Light: C.HOVER, R.Midlight: C.RAISED, R.Mid: C.LINE_STRONG, R.Dark: C.PANEL, R.Shadow: C.BG,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    G = QPalette.ColorGroup.Disabled
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(G, role, QColor(C.TEXT_DISABLED))
    pal.setColor(G, R.Base, QColor(C.PANEL))
    pal.setColor(G, R.Button, QColor(C.PANEL))
    pal.setColor(G, R.Highlight, QColor(C.LINE_STRONG))
    return pal


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass
    font = QFont()
    font.setFamilies([F.BODY, F.BODY_FALLBACK])
    font.setPointSize(F.BODY_PT)
    app.setFont(font)
    app.setPalette(_palette())
    try:
        extra = _extra_qss(_ui_images())
    except Exception:  # görseller yazılamazsa temel tema yeterli
        extra = ""
    app.setStyleSheet(QSS + extra)
    if not any(isinstance(o, _FocusRing) for o in app.children()):
        _FocusRing(app)


# --- uygulama simgesi: hazır logo ----------------------------------------------------------------
_LOGO: QPixmap | None = None
_LOGO_CACHE: dict[int, QPixmap] = {}


def _logo() -> QPixmap | None:
    global _LOGO
    if _LOGO is None:
        from altyazitr.paths import resource_dir

        pm = QPixmap(str(resource_dir() / "data" / "logo.png"))
        _LOGO = pm
    return None if _LOGO.isNull() else _LOGO


def _fallback_icon(size: int) -> QPixmap:
    """logo.png bulunamazsa: sade, yuvarlak köşeli koyu kare (marka değil, yalnızca yer tutucu)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    path = QPainterPath()
    path.addRoundedRect(r, size * 0.22, size * 0.22)
    p.fillPath(path, QColor(C.PANEL))
    p.setPen(QColor(C.PINK))
    f = QFont(F.DISPLAY_SEMIBOLD)
    f.setPixelSize(max(6, int(size * 0.42)))
    p.setFont(f)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "RV")
    p.end()
    return pm


def make_icon_pixmap(size: int) -> QPixmap:
    """Logonun `size`×`size` yumuşak ölçeklenmiş kopyası (önbellekli)."""
    size = max(1, int(size))
    pm = _LOGO_CACHE.get(size)
    if pm is not None:
        return pm
    logo = _logo()
    if logo is None:
        pm = _fallback_icon(size)
    else:
        pm = logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    _LOGO_CACHE[size] = pm
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_icon_pixmap(s))
    return icon
