"""Altyazı bölgesi seçimi: hedefin anlık görüntüsü üzerinde fareyle dikdörtgen çizilir.
Bölge oransal (0..1) saklanır; çözünürlük değişse de doğru kalır."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QRubberBand, QVBoxLayout

from altyazitr.config import DEFAULT_REGION
from altyazitr.gui import icons
from altyazitr.gui.dialogs import AlertLabel
from altyazitr.gui.region_picker import paint_selection_frame
from altyazitr.gui.tokens import ICON, SPACE, C

PRESET_BOTTOM = DEFAULT_REGION  # "alt şerit" hazır seçimi = varsayılan altyazı şeridi


def bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    img = np.ascontiguousarray(bgr)
    h, w = img.shape[:2]
    return QPixmap.fromImage(QImage(img.data, w, h, img.strides[0], QImage.Format.Format_BGR888).copy())


class _Band(QRubberBand):
    """Seçimi tutan lastik bant. Kendisi görünmez; çerçeveyi tuval tasarım sistemine göre çizer."""

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        pass


class _Canvas(QLabel):
    def __init__(self, pm: QPixmap, initial: tuple[float, float, float, float] | None):
        super().__init__()
        self.setStyleSheet("background: transparent; border: none; padding: 0;")
        self.setPixmap(pm)
        self.setFixedSize(pm.size())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.band = _Band(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self.sel = QRect()
        if initial:
            self.set_fraction(initial)

    def set_fraction(self, fr: tuple[float, float, float, float]) -> None:
        W, H = self.width(), self.height()
        self.sel = QRect(int(fr[0] * W), int(fr[1] * H), int(fr[2] * W), int(fr[3] * H))
        self.band.setGeometry(self.sel)
        self.band.show()
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        self.origin = e.position().toPoint()
        self.band.setGeometry(QRect(self.origin, QSize()))
        self.band.show()
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        self.band.setGeometry(QRect(self.origin, e.position().toPoint()).normalized() & self.rect())
        self.update()

    def mouseReleaseEvent(self, _e):  # noqa: N802
        self.sel = self.band.geometry()
        self.update()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # sürüklerken bandı, bırakınca kaydedilen seçimi göster
        r = self.band.geometry() if not self.band.isHidden() else self.sel
        if r.isValid() and r.width() > 4:
            shade = QColor(C.BG)
            shade.setAlpha(150)
            p.fillRect(0, 0, W, r.top(), shade)
            p.fillRect(0, r.bottom() + 1, W, H - r.bottom() - 1, shade)
            p.fillRect(0, r.top(), r.left(), r.height(), shade)
            p.fillRect(r.right() + 1, r.top(), W - r.right() - 1, r.height(), shade)
            paint_selection_frame(p, QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5))
        p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, W - 1, H - 1))
        p.end()

    def fraction(self) -> tuple[float, float, float, float] | None:
        r, W, H = self.sel, self.width(), self.height()
        if r.width() < 10 or r.height() < 6:
            return None
        return (max(0.0, r.x() / W), max(0.0, r.y() / H), min(1.0, r.width() / W), min(1.0, r.height() / H))


class RegionDialog(QDialog):
    def __init__(self, frame_bgr: np.ndarray, current: tuple[float, float, float, float], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Altyazı bölgesini seçin")
        pm = bgr_to_pixmap(frame_bgr)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        max_w, max_h = int(scr.width() * 0.82), int(scr.height() * 0.72)
        if pm.width() > max_w or pm.height() > max_h:
            pm = pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self.canvas = _Canvas(pm, current)

        title = QLabel("Altyazı bölgesini seçin")
        title.setObjectName("section")
        info = QLabel("Altyazının çıktığı alanı fareyle çizin.")
        info.setObjectName("muted")
        info.setToolTip("İki satırlık altyazıyı da kapsayacak kadar biraz geniş tutun; HUD/mini harita gibi "
                        "sabit yazıları dışarıda bırakın.")
        preset = QPushButton("Alt bant")
        preset.setIcon(icons.icon("captions", C.TEXT_2))
        preset.setIconSize(QSize(ICON["s"], ICON["s"]))
        preset.setToolTip("Önerilen bölge: ekranın alt bandı, altyazıların çoğu burada çıkar")
        preset.clicked.connect(lambda: self.canvas.set_fraction(PRESET_BOTTOM))
        ok = QPushButton("Kaydet")
        ok.setObjectName("primary")
        ok.setIcon(icons.icon("check", C.INVERSE))
        ok.setIconSize(QSize(ICON["s"], ICON["s"]))
        ok.clicked.connect(self._accept)
        cancel = QPushButton("İptal")
        cancel.clicked.connect(self.reject)
        self.err = AlertLabel("")
        self.err.setWordWrap(False)

        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        btns.addWidget(preset)
        btns.addSpacing(SPACE["m"])
        btns.addWidget(self.err, 1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["l"])
        lay.setSpacing(0)
        lay.addWidget(title)
        lay.addSpacing(SPACE["xs"])
        lay.addWidget(info)
        lay.addSpacing(SPACE["l"])
        lay.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(SPACE["l"])
        lay.addLayout(btns)
        self.result_fraction: tuple[float, float, float, float] | None = None

    def _accept(self) -> None:
        fr = self.canvas.fraction()
        if fr is None:
            self.err.setText("Önce fareyle bir alan çizin.")
            return
        self.result_fraction = fr
        self.accept()
