"""Sayfaların ortak yardımcıları (arka plan işi, form düzeni, ipucu, stil yenileme, bağlantı)."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

log = logging.getLogger(__name__)


class Worker(QThread):
    """Uzun süren işleri (model indirme vb.) arayüzü dondurmadan çalıştırır."""

    progress = Signal(str, float)
    done = Signal(bool, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            self.fn(lambda s, f=-1.0: self.progress.emit(s, f), self.cancel)
            self.done.emit(True, "")
        except Exception as e:
            log.warning("Arka plan işi başarısız: %s", e)
            self.done.emit(False, str(e))


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    f.setHorizontalSpacing(18)
    f.setVerticalSpacing(10)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    return f


def _tip(w, text: str):
    w.setToolTip(text)
    return w


def _repolish(w: QWidget, name: str) -> None:
    w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


def link(text: str, url: str) -> QLabel:
    from altyazitr.gui.tokens import C

    lb = QLabel(f"<a href='{url}' style='color:{C.CYAN}; text-decoration:none'>{text}</a>")
    lb.setOpenExternalLinks(True)
    return lb
