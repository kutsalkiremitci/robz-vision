"""Küçük bilgi/yardım pencereleri ve bu pencerelerin ortak görsel parçaları."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from altyazitr.gui import icons
from altyazitr.gui.tokens import ICON, SPACE, C, F
from altyazitr.paths import resource_dir


# --- yazı tipi yardımcıları (QPainter ile çizilen yüzeyler için) -------------------------------------
def display_font(px: int = F.BUTTON, family: str = F.DISPLAY_SEMIBOLD, tabular: bool = True) -> QFont:
    """Bahnschrift: başlık, düğme ve sayılar. tabular=True → rakamlar eşit genişlikte (sayı değişirken
    yazı titremez)."""
    f = QFont()
    f.setFamilies([family, F.DISPLAY, F.BODY_FALLBACK])
    f.setPixelSize(px)
    if tabular:
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except Exception:  # eski Qt: özellik yok, sorun değil
            pass
    return f


def body_font(px: int = F.BODY_SIZE, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Segoe UI Variable Text (yoksa Segoe UI): gövde metni ve ipuçları."""
    f = QFont()
    f.setFamilies([F.BODY, F.BODY_FALLBACK])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


_LOGO: list[QPixmap] = []


def brand_pixmap(size: int, dpr: float = 2.0) -> QPixmap:
    """Marka işareti (data/logo.png), size mantıksal pikselde, keskin (dpr). Dosya yoksa temanın simgesi."""
    if not _LOGO:
        src = QPixmap(str(resource_dir() / "data" / "logo.png"))
        _LOGO.append(src)
    src = _LOGO[0]
    if src.isNull():
        from altyazitr.gui.theme import make_icon_pixmap

        src = make_icon_pixmap(int(size * dpr))
    pm = src.scaled(int(size * dpr), int(size * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def font_css(family: str, px: int, color: str | None = None) -> str:
    """Tek bir pencere öğesine yazı tipi veren QSS (genel temadaki '*' kuralını bu öğe için geçersiz kılar)."""
    css = f"font-family: '{family}', '{F.BODY_FALLBACK}'; font-size: {px}px;"
    return css + (f" color: {color};" if color else "")


class AlertLabel(QLabel):
    """Solunda uyarı ikonu olan kısa hata satırı. Ayrıntı tooltip'te durur. Metin boşken ikon çizilmez."""

    def __init__(self, text: str = "", color: str = C.ORANGE, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._icon = icons.pixmap("alert", ICON["s"], color)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setContentsMargins(ICON["s"] + SPACE["s"], 0, 0, 0)
        self.setStyleSheet(font_css(F.BODY, F.BODY_SIZE, C.TEXT))

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt
        super().paintEvent(e)
        if not self.text():
            return
        # ikonu metnin ilk satırıyla aynı hizaya koy (etiket satırdan uzunsa metin dikeyde ortalanır)
        fm = self.fontMetrics()
        flags = int(self.alignment().value) | (int(Qt.TextFlag.TextWordWrap.value) if self.wordWrap() else 0)
        top = fm.boundingRect(self.contentsRect(), flags, self.text()).top()
        p = QPainter(self)
        p.drawPixmap(QPointF(0, top + (fm.height() - ICON["s"]) / 2), self._icon)
        p.end()


# --- OBS yardımı -------------------------------------------------------------------------------------
_NUM = f"<span style=\"font-family:'{F.DISPLAY}'\">{{}}</span>"  # metin içindeki sayılar Bahnschrift
_STEPS = (
    "OBS'te <b>Kaynaklar</b> altındaki <b>+</b> düğmesine basıp <b>Tarayıcı</b>'yı seçin.",
    "<b>Yerel dosya</b> kutusunu boş bırakın, <b>URL</b> alanına bu adresi yapıştırın:",
    f"Genişlik ve yüksekliği tuvalle aynı yapın, örneğin {_NUM.format('1920 × 1080')}. "
    f"{_NUM.format('800 × 600')} bırakırsanız altyazı küçük ve sol üstte kalır.",
    "Kaynağı listede oyun görüntüsünün üstüne taşıyın.",
)
_HELP_W = 540
# OBS, sayfayı Robz Vision kapalıyken yüklemeye çalışırsa kendiliğinden yeniden denemez.
OBS_NOT_CONNECTED_TIP = ("OBS'e bağlı tarayıcı kaynağı yok. OBS açıksa kaynağı seçip önizlemenin altındaki "
                         "Yenile'ye basın.")


def _step_number(n: int) -> QLabel:
    lb = QLabel(str(n))
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setFixedSize(24, 24)
    lb.setAccessibleName(f"Adım {n}")
    lb.setStyleSheet(f"QLabel {{ {font_css(F.DISPLAY_SEMIBOLD, F.SMALL + 1, C.TEXT)} background: {C.RAISED};"
                     f" border: none; border-radius: 12px; }}")
    return lb


def _body(html: str, color: str = C.TEXT) -> QLabel:
    lb = QLabel(html)
    lb.setTextFormat(Qt.TextFormat.RichText)
    lb.setWordWrap(True)
    lb.setStyleSheet(font_css(F.BODY, F.BODY_SIZE, color))
    return lb


def _copy_button(get_text) -> QPushButton:
    btn = QPushButton()
    btn.setObjectName("icon")
    btn.setFixedSize(32, 32)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    idle, done = icons.icon("copy", C.TEXT_2), icons.icon("check", C.TURQUOISE)
    btn.setIcon(idle)
    btn.setToolTip("Adresi kopyala")
    btn.setAccessibleName("Adresi kopyala")

    def restore() -> None:
        btn.setIcon(idle)
        btn.setToolTip("Adresi kopyala")

    def copy() -> None:
        QGuiApplication.clipboard().setText(get_text())
        btn.setIcon(done)
        btn.setToolTip("Kopyalandı")
        QTimer.singleShot(1600, btn, restore)

    btn.clicked.connect(copy)
    return btn


def build_obs_help(parent: QWidget | None, url: str) -> QDialog:
    """OBS'e tarayıcı kaynağı ekleme adımları. Gösterilmeden döner (ekran görüntüsü araçları için)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("OBS'e nasıl eklenir?")
    dlg.setFixedWidth(_HELP_W)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["l"])
    lay.setSpacing(0)

    title = QLabel("OBS'e tarayıcı kaynağı olarak ekleyin")
    title.setObjectName("section")
    lay.addWidget(title)
    lay.addSpacing(SPACE["l"])

    grid = QGridLayout()
    grid.setHorizontalSpacing(SPACE["m"])
    grid.setVerticalSpacing(SPACE["m"])
    grid.setColumnStretch(1, 1)
    for i, html in enumerate(_STEPS):
        grid.addWidget(_step_number(i + 1), i, 0, Qt.AlignmentFlag.AlignTop)
        cell = QVBoxLayout()
        cell.setSpacing(SPACE["s"])
        cell.setContentsMargins(0, 3, 0, 0)
        cell.addWidget(_body(html), 0, Qt.AlignmentFlag.AlignTop)
        if i == 1:
            field = QLineEdit(url)
            field.setReadOnly(True)
            field.setCursorPosition(0)
            field.setAccessibleName("OBS adresi")
            field.setToolTip("Bu adresi OBS'teki Tarayıcı kaynağının URL alanına yapıştırın")
            field.setMinimumHeight(32)
            row = QHBoxLayout()
            row.setSpacing(SPACE["s"])
            row.addWidget(field, 1)
            row.addWidget(_copy_button(field.text))
            cell.addLayout(row)
        grid.addLayout(cell, i, 1)
    lay.addLayout(grid)
    lay.addSpacing(SPACE["m"])
    lay.addWidget(_body("Görünüm değişiklikleri OBS'e anında yansır. Altyazı OBS'te çıkmıyorsa (örneğin OBS'i "
                        "Robz Vision'dan önce açtıysanız) kaynağı seçip önizlemenin altındaki <b>Yenile</b>'ye "
                        "basın.", C.TEXT_2))

    lay.addSpacing(SPACE["l"])
    rule = QFrame()
    rule.setFixedHeight(1)
    rule.setStyleSheet(f"background: {C.LINE}; border: none;")
    lay.addWidget(rule)
    lay.addSpacing(SPACE["m"])

    alt = QHBoxLayout()
    alt.setSpacing(SPACE["s"])
    info = QLabel()
    info.setPixmap(icons.pixmap("info", ICON["s"], C.TEXT_2))
    info.setFixedSize(ICON["s"], 20)
    alt.addWidget(info, 0, Qt.AlignmentFlag.AlignTop)
    alt.addWidget(_body("Ekran Yakalama kullanıyorsanız tarayıcı kaynağı gerekmez; Ana sayfa'da "
                        "“Yayında da görünsün”ü açın.", C.TEXT_2), 1)
    lay.addLayout(alt)

    lay.addSpacing(SPACE["xl"])
    btns = QHBoxLayout()
    btns.addStretch(1)
    close = QPushButton("Kapat")
    close.setDefault(True)
    close.clicked.connect(dlg.accept)
    btns.addWidget(close)
    lay.addLayout(btns)
    close.setFocus()
    # Kırılan satırların yüksekliği genişliğe bağlı: pencereyi içeriğe tam oturt (boş boşluk kalmasın).
    dlg.ensurePolished()
    lay.activate()
    h = lay.totalHeightForWidth(_HELP_W)
    if h > 0:
        dlg.setFixedHeight(h)
    return dlg


def show_obs_help(parent: QWidget, url: str) -> None:
    dlg = build_obs_help(parent, url)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    win = parent.window() if parent is not None else None
    if win is not None and win.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
        # Ekran dışı çizilen (görüntü alma) bir pencereden açıldıysa: ekranda gösterme, akışı bekletme.
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg.show()
        return
    dlg.exec()
