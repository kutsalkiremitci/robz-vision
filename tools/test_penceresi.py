"""Sahte "oyun" penceresi: gerçek bir oyun olmadan Robz Vision'ı denemek için.

Hareketli bir arka plan üzerinde sırayla İngilizce altyazılar gösterir (konuşmacı adlı,
iki satırlı ve daktilo efektli örnekler dahil).

Kullanım:  python tools/test_penceresi.py
Robz Vision'da "Bölge seç" ile pencerenin altyazı alanını seçin (veya "Uygulama penceresi"
modunda 'Robz Vision Test Oyunu' penceresini seçin).
"""

from __future__ import annotations

import math
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

SUBS = [
    ("LUCIA", "I told you not to come back here, Jason."),
    ("JASON", "They're watching us. We need to move, now!"),
    (None, "Hey bro, you good? That cop's been tailing us for like ten blocks."),
    ("LUCIA", "Get in the car. We're going to Vice City."),
    (None, "Mission failed. You abandoned your partner."),
    ("JASON", "Wait... Is that him? No way. Mr. Smith said he left town!"),
    (None, "Come on, man! We gotta go before they call it in."),
]
SHOW, GAP = 3.2, 0.9


def paint_scene(p: QPainter, t: float, W: int, H: int) -> str | None:
    """t anındaki sahneyi çizer; ekrandaki altyazı metnini döndürür (testler için)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor.fromHsv(int(t * 20) % 360, 160, 120))
    g.setColorAt(1, QColor.fromHsv(int(t * 20 + 140) % 360, 180, 60))
    p.fillRect(0, 0, W, H, g)
    for i in range(9):  # hareketli parlak şekiller (gökyüzü/ışık taklidi)
        x = (math.sin(t * 0.6 + i) * 0.5 + 0.5) * W
        y = (math.cos(t * 0.4 + i * 1.7) * 0.5 + 0.5) * H
        p.setBrush(QColor.fromHsv((i * 40) % 360, 90, 230, 160))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x, y), 60 + 20 * i, 40 + 10 * i)

    cycle = SHOW + GAP
    idx = int(t // cycle) % len(SUBS)
    local = t % cycle
    if local >= SHOW:
        return None
    speaker, text = SUBS[idx]
    if idx == 5:  # daktilo efekti
        text = text[: max(1, int(len(text) * min(1.0, local / 1.2)))]
    full = f"{speaker}: {text}" if speaker else text
    _draw_sub(p, full, W, H)
    return full


class FakeGame(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Robz Vision Test Oyunu")
        self.resize(1280, 720)
        self.t0 = time.monotonic()
        self.timer = QTimer(self, interval=33, timeout=self.update)
        self.timer.start()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        paint_scene(p, time.monotonic() - self.t0, self.width(), self.height())
        p.end()


def _draw_sub(p: QPainter, text: str, W: int, H: int) -> None:
    font = QFont("Arial")
    font.setPixelSize(int(H * 0.045))
    font.setBold(True)
    fm = QFontMetricsF(font)
    words, lines, cur = text.split(), [], ""
    for w in words:
        tt = f"{cur} {w}".strip()
        if cur and fm.horizontalAdvance(tt) > W * 0.7:
            lines.append(cur)
            cur = w
        else:
            cur = tt
    lines.append(cur)
    y = H * 0.86 - fm.lineSpacing() * len(lines)
    for line in lines:
        path = QPainterPath()
        path.addText(QPointF((W - fm.horizontalAdvance(line)) / 2, y + fm.ascent()), font, line)
        p.strokePath(path, QPen(QColor(0, 0, 0), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, QColor(255, 255, 255))
        y += fm.lineSpacing()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = FakeGame()
    if "--arkada" in sys.argv:  # diğer pencerelerin arkasında, odak çalmadan (otomatik testler için)
        w.setWindowFlag(Qt.WindowType.WindowStaysOnBottomHint, True)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    if "--konum" in sys.argv:  # ör. --konum -1400,100 (testlerde ekran dışına almak için)
        x, y = (int(v) for v in sys.argv[sys.argv.index("--konum") + 1].split(","))
        w.move(x, y)
    w.show()
    sys.exit(app.exec())
