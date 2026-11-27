"""Kaydırırken yanlışlıkla değer değişmesin. Açılır liste (QComboBox) tekerlekle hiç değişmez (yalnız tıklayıp
seçince ya da klavyeyle): bir değer seçtikten sonra odakta kalan liste, sayfa kaydırılırken değişmesin. Sayı kutusu
(QSpinBox) ve kaydırıcı (QSlider) tekerlekle yalnız odaktayken değişir (önce tıklanır ya da Tab ile gelinir).
Değişmediği her durumda tekerlek, denetimin içinde bulunduğu sayfayı kaydırır.

Uygulama geneline kurulan tek bir olay süzgecidir (install). Bu denetimlerin "tekerlekle odaklan" ilkesi (WheelFocus)
"tıkla ya da Tab" (StrongFocus) yapılır: yoksa Qt tekerlekte önce odağı verir, sonra değer değişirdi. Açılır listenin
kendi listesi, kaydırma çubukları ve diğer denetimler etkilenmez."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, \
    QScrollBar, QWidget

_GUARDED = (QComboBox, QAbstractSpinBox, QAbstractSlider)


def _guarded(obj: QObject) -> QWidget | None:
    """Tekerleği korunan denetim (düzenlenebilir seçim kutusunun iç yazı alanı da kutunun kendisi sayılır)."""
    if isinstance(obj, QScrollBar) or not isinstance(obj, QWidget):
        return None
    if isinstance(obj, _GUARDED):
        return obj
    parent = obj.parentWidget()
    if isinstance(parent, (QComboBox, QAbstractSpinBox)):
        return parent
    return None


def _scroll_area(w: QWidget) -> QAbstractScrollArea | None:
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            return p
        p = p.parentWidget()
    return None


class WheelGuard(QObject):
    """Bkz. modül açıklaması."""

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802 - Qt
        t = e.type()
        if t == QEvent.Type.Polish and isinstance(obj, _GUARDED) and not isinstance(obj, QScrollBar):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return False
        if t != QEvent.Type.Wheel:
            return False
        w = _guarded(obj)
        if w is None or (w.hasFocus() and not isinstance(w, QComboBox)):
            return False
        area = _scroll_area(w)
        if area is not None:  # sayfa kayar, değer değişmez
            bar = area.verticalScrollBar() if area.verticalScrollBar().isVisible() else area.horizontalScrollBar()
            QCoreApplication.sendEvent(bar, e)
        return True


_guard: WheelGuard | None = None


def install(app: QApplication) -> WheelGuard:
    """Süzgeci uygulamaya bir kez kurar (tekrar çağrılırsa aynı süzgeç döner)."""
    global _guard
    if _guard is None:
        _guard = WheelGuard(app)
        app.installEventFilter(_guard)
    return _guard
