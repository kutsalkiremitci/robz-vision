"""Ana sayfadaki "Son çeviriler" listesi: bu oturumda okunan satırlar, en yenisi üstte.

* Satır: solda saat (Bahnschrift, TEXT_3), sağında konuşmacı + Türkçe, altında küçük İngilizce orijinal.
  Üzerine gelince sağda kopyala ikonu belirir; sağ tık menüsü İngilizcesiyle birlikte de kopyalar.
* Aynı `line` kimliğiyle gelen olay (hibrit modda daha iyi çeviri, daha temiz okuma, daktilo uzaması)
  satırı yerinde günceller; kimlik yoksa metin değişince yeni satır açılır.
* En fazla HISTORY_MAX satır yalnız bellekte tutulur; diske yazılmaz (gizlilik).

En yenisi üstte: son satır, süreleriyle birlikte bölüm başlığının hemen altında, kaydırmadan görünür.
Eski satırları okumak için aşağı kaydırıldığında yeni gelen satır görüntüyü kaydırmaz (bkz. HistoryPanel.add).
Çizim QListView + kendi delegesiyle yapılır: yalnız görünen satırlar çizilir, satır yükseklikleri
genişliğe göre önbelleğe alınır; olay başına bir satır eklenir ya da güncellenir.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                           QPainterPath, QTextCharFormat, QTextLayout, QTextOption)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QLabel, QListView, QMenu, QPushButton,
                               QSizePolicy, QStyle, QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget)

from altyazitr.gui import icons
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F, qcolor
from altyazitr.gui.widgets import button_icon

HISTORY_MAX = 200
FILL_MIN_H = 160  # doldurma kipinde (bkz. HistoryView.set_fill) listenin en küçük yüksekliği
QWIDGETSIZE_MAX = 16777215
PRIVACY_TIP = (f"Son {HISTORY_MAX} çeviri yalnızca bu oturumda, bellekte tutulur; diske yazılmaz. "
               "Uygulamayı kapatınca silinir.")
COPY_TIP = "Türkçesini kopyala (sağ tık: İngilizcesiyle birlikte)"
EMPTY_TEXT = "Henüz çeviri yok"


# --- veri ----------------------------------------------------------------------------------------
@dataclass(eq=False)
class HistoryEntry:
    time: str  # "HH:MM:SS", satırın ilk göründüğü an
    text: str  # Türkçe
    original: str = ""  # İngilizce
    speaker: str = ""
    line: object = None  # işlem hattının satır kimliği (yoksa None)
    cache: object = field(default=None, repr=False, compare=False)  # delegenin düzen önbelleği (_Layout)


def entry_text(e: HistoryEntry, original: bool = False) -> str:
    """Panoya giden metin: "LUCIA: Türkçe"; original=True ise önce İngilizce satır, sonra Türkçe."""
    pre = f"{e.speaker}: " if e.speaker else ""
    tr = pre + e.text
    if original and e.original:
        return f"{pre}{e.original}\n{tr}"
    return tr


def transcript(entries: Iterable[HistoryEntry]) -> str:
    """'Tümünü kopyala' metni: verilen sırayla (eskiden yeniye) her satır için saat, İngilizce, Türkçe;
    satırlar arasında boş satır."""
    return "\n\n".join(f"{e.time}\n{entry_text(e, original=True)}" for e in entries)


class HistoryModel(QAbstractListModel):
    """Son çeviriler, en yenisi 0. satırda. Olay başına tek ekleme ya da tek satır güncellemesi."""

    def __init__(self, limit: int = HISTORY_MAX, parent=None):
        super().__init__(parent)
        self._items: list[HistoryEntry] = []
        self.limit = limit

    # Qt
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        e = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry_text(e)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{e.time}. {entry_text(e)}" + (f". İngilizcesi: {e.original}" if e.original else "")
        return None

    # erişim
    def entry(self, row: int) -> HistoryEntry:
        return self._items[row]

    def entries(self) -> list[HistoryEntry]:
        """En yenisi başta."""
        return list(self._items)

    # değişiklik
    def add(self, ev: dict, now: float | None = None) -> tuple[int, bool]:
        """Altyazı olayını ekler ya da aynı satırı günceller. Dönüş: (satır, yeni mi); boş metin: (-1, False)."""
        text = str(ev.get("text") or "").strip()
        if not text:
            return -1, False
        original = str(ev.get("original") or "").strip()
        speaker = str(ev.get("speaker") or "").strip()
        line = ev.get("line")
        hit = None
        if line is not None:
            hit = next((i for i, e in enumerate(self._items) if e.line is not None and e.line == line), None)
        elif self._items and self._items[0].line is None and self._items[0].text == text:
            hit = 0  # kimlik yok: metin aynıysa aynı satır
        if hit is not None:
            e = self._items[hit]
            if (e.text, e.original, e.speaker) != (text, original, speaker):
                e.text, e.original, e.speaker = text, original, speaker
                e.cache = None
                idx = self.index(hit)
                self.dataChanged.emit(idx, idx)
            return hit, False
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._items.insert(0, HistoryEntry(stamp, text, original, speaker, line))
        self.endInsertRows()
        n = len(self._items)
        if n > self.limit:
            self.beginRemoveRows(QModelIndex(), self.limit, n - 1)
            del self._items[self.limit:]
            self.endRemoveRows()
        return 0, True

    def forget_lines(self) -> None:
        """İşlem hattı yeniden başlayınca satır kimlikleri baştan sayılabilir: eski satırlar artık eşleşmesin."""
        for e in self._items:
            e.line = None

    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()


# --- çizim -----------------------------------------------------------------------------------------
@dataclass
class _Layout:
    width: int
    tr: QTextLayout
    tr_h: float
    ascent: float  # ilk Türkçe satırın taban çizgisi (saat aynı çizgiye oturur)
    line_h: float  # ilk Türkçe satırın yüksekliği (kopyala düğmesi ona ortalanır)
    en: QTextLayout | None
    height: int


class _Delegate(QStyledItemDelegate):
    """Bir geçmiş satırını çizer. Düzen (QTextLayout) satır ve genişlik başına önbelleğe alınır."""

    PAD_X = SPACE["m"]
    PAD_Y = SPACE["s"] + 2
    COL_GAP = SPACE["m"]  # saat sütunu ile metin arası
    LINE_GAP = 2  # Türkçe ile İngilizce arası
    BTN = 28  # kopyala düğmesinin tıklama alanı

    def __init__(self, view: HistoryView):
        super().__init__(view)
        self.view = view
        base = QFont(QApplication.font())
        self.f_tr = QFont(base)
        self.f_tr.setPixelSize(F.BODY_SIZE + 1)
        self.f_sp = QFont(self.f_tr)
        self.f_sp.setFamilies([F.BODY_STRONG, F.BODY_FALLBACK])
        self.f_sp.setWeight(QFont.Weight.DemiBold)
        self.f_en = QFont(base)
        self.f_en.setPixelSize(F.SMALL)
        self.f_time = QFont(F.DISPLAY)
        self.f_time.setPixelSize(F.SMALL)
        self.time_w = QFontMetrics(self.f_time).horizontalAdvance("00:00:00")
        self._sp_fmt = QTextCharFormat()
        self._sp_fmt.setFont(self.f_sp)
        self._sp_fmt.setForeground(QColor(C.TEXT_2))
        self._icons: dict[tuple[str, float], object] = {}

    # --- ölçüler
    def text_width(self) -> int:
        """Metin sütununun genişliği. Kaydırma çubuğu her zaman ayrılmış sayılır: çubuk belirip kaybolunca
        satırlar yeniden kırılmaz, yükseklik gidip gelmez."""
        v = self.view
        sb = max(12, v.verticalScrollBar().sizeHint().width())
        w = v.width() - 2 * v.frameWidth() - sb
        return max(80, w - 2 * self.PAD_X - self.time_w - self.COL_GAP - self.BTN - SPACE["xs"])

    @staticmethod
    def _wrap(lay: QTextLayout, width: int) -> float:
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        lay.setTextOption(opt)
        lay.beginLayout()
        y = 0.0
        while True:
            ln = lay.createLine()
            if not ln.isValid():
                break
            ln.setLineWidth(width)
            ln.setPosition(QPointF(0, y))
            y += ln.height()
        lay.endLayout()
        return y

    def layout(self, e: HistoryEntry, width: int | None = None) -> _Layout:
        w = self.text_width() if width is None else width
        c = e.cache
        if c is not None and c.width == w:
            return c
        pre = f"{e.speaker}: " if e.speaker else ""
        tr = QTextLayout(pre + e.text, self.f_tr)
        if pre:
            fr = QTextLayout.FormatRange()
            fr.start, fr.length, fr.format = 0, len(pre) - 1, self._sp_fmt
            tr.setFormats([fr])
        tr_h = self._wrap(tr, w)
        first = tr.lineAt(0)
        ascent, line_h = first.ascent(), first.height()
        en = None
        h = self.PAD_Y + tr_h
        if e.original:
            en = QTextLayout(e.original, self.f_en)
            h += self.LINE_GAP + self._wrap(en, w)
        h += self.PAD_Y
        e.cache = _Layout(w, tr, tr_h, ascent, line_h, en, int(h + 0.999))
        return e.cache

    def entry_height(self, e: HistoryEntry, width: int | None = None) -> int:
        return self.layout(e, width).height

    def button_rect(self, rect: QRect, lay: _Layout) -> QRect:
        """Kopyala düğmesi: satırın sağında, ilk Türkçe satıra dikey ortalı."""
        y = rect.top() + self.PAD_Y + int((lay.line_h - self.BTN) / 2)
        return QRect(rect.right() - self.PAD_X - self.BTN + SPACE["xs"], max(rect.top(), y), self.BTN, self.BTN)

    # --- Qt
    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        e = index.model().entry(index.row())
        return QSize(max(1, self.view.viewport().width()), self.entry_height(e))

    def _icon(self, color: str, dpr: float):
        key = (color, dpr)
        if key not in self._icons:
            self._icons[key] = icons.pixmap("copy", ICON["s"], color, dpr=max(1.0, dpr))
        return self._icons[key]

    def paint(self, p: QPainter, option, index) -> None:
        view = self.view
        model = index.model()
        row = index.row()
        e = model.entry(row)
        lay = self.layout(e)
        r = option.rect
        hovered = row == view.hover_row
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and view.hasFocus()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hovered or selected:
            bg = QPainterPath()
            bg.addRoundedRect(QRectF(r).adjusted(SPACE["xs"], 2, -SPACE["xs"], -2), RADIUS["s"], RADIUS["s"])
            p.fillPath(bg, qcolor(C.CYAN_SOFT) if selected else QColor(C.RAISED))
        if row < model.rowCount() - 1:  # satırlar arası ince çizgi
            p.fillRect(QRectF(r.left() + self.PAD_X, r.bottom(), r.width() - 2 * self.PAD_X, 1), QColor(C.LINE))
        x0 = r.left() + self.PAD_X
        top = r.top() + self.PAD_Y
        p.setFont(self.f_time)
        p.setPen(QColor(C.TEXT_3))
        p.drawText(QPointF(x0, top + lay.ascent), e.time)
        x1 = x0 + self.time_w + self.COL_GAP
        p.setPen(QColor(C.TEXT))
        lay.tr.draw(p, QPointF(x1, top))
        if lay.en is not None:
            p.setPen(QColor(C.TEXT_2))
            lay.en.draw(p, QPointF(x1, top + lay.tr_h + self.LINE_GAP))
        if hovered or selected:
            b = self.button_rect(r, lay)
            over = hovered and view.hover_button
            if over:
                hb = QPainterPath()
                hb.addRoundedRect(QRectF(b), RADIUS["m"], RADIUS["m"])
                p.fillPath(hb, QColor(C.HOVER))
            dev = p.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            s = ICON["s"]
            p.drawPixmap(QPoint(b.left() + (b.width() - s) // 2, b.top() + (b.height() - s) // 2),
                         self._icon(C.TEXT if over else C.TEXT_2, dpr))
        p.restore()

    def helpEvent(self, event, view, option, index) -> bool:  # noqa: N802
        """İpucu yalnız kopyala düğmesinin üstünde; satırın geri kalanı okurken ipucu açmaz."""
        if index.isValid() and event is not None:
            lay = self.layout(index.model().entry(index.row()))
            if self.button_rect(option.rect, lay).contains(event.pos()):
                QToolTip.showText(event.globalPos(), COPY_TIP, view)
                return True
        QToolTip.hideText()
        return True


class HistoryView(QListView):
    """Son çeviriler listesi. Yüksekliği içeriğe göre büyür, MAX_H'de durur ve kaydırılır."""

    copy_requested = Signal(int, bool)  # satır, İngilizcesiyle birlikte mi
    MAX_H = 300

    def __init__(self, model: HistoryModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("history")
        self.setModel(model)
        self._delegate = _Delegate(self)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Son çeviriler")
        self.setAccessibleDescription(PRIVACY_TIP)
        self.hover_row = -1
        self.hover_button = False
        self._press_row = -1
        model.dataChanged.connect(self._entry_changed)
        model.rowsInserted.connect(self._after_rows)
        model.rowsRemoved.connect(self._after_rows)
        model.modelReset.connect(self._after_rows)

    # --- ölçü
    def row_height(self, row: int) -> int:
        return self._delegate.entry_height(self.model().entry(row))

    def wanted_height(self) -> int:
        fw = 2 * self.frameWidth()
        total = fw
        w = self._delegate.text_width()
        for e in self.model()._items:
            total += self._delegate.entry_height(e, w)
            if total >= self.MAX_H:
                return self.MAX_H
        return total

    def set_fill(self, on: bool) -> None:
        """Doldurma kipi (Ana sayfanın geniş düzenindeki tam boy sütun): liste içeriğine göre büyümez, bulunduğu
        alanı doldurur ve kendi içinde kayar. Kapalıyken içeriğe göre büyür, MAX_H'de durur."""
        self._fill = bool(on)
        if on:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.setMinimumHeight(FILL_MIN_H)
            self.setMaximumHeight(QWIDGETSIZE_MAX)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.update_height()

    def update_height(self) -> None:
        if getattr(self, "_fill", False):
            return
        h = max(48, self.wanted_height())
        if self.minimumHeight() != h or self.maximumHeight() != h:
            self.setFixedHeight(h)

    def _entry_changed(self, *_a) -> None:
        self.scheduleDelayedItemsLayout()  # liste kipinde Qt satır yüksekliğini kendisi yeniden sormaz
        self.update_height()

    def _after_rows(self, *_a) -> None:
        self.update_height()
        self._refresh_hover()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.update_height()

    # --- fare
    def _button_hit(self, pos: QPoint) -> tuple[int, bool]:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return -1, False
        lay = self._delegate.layout(self.model().entry(idx.row()))
        return idx.row(), self._delegate.button_rect(self.visualRect(idx), lay).contains(pos)

    def _set_hover(self, pos: QPoint | None) -> None:
        row, btn = self._button_hit(pos) if pos is not None else (-1, False)
        if (row, btn) == (self.hover_row, self.hover_button):
            return
        old = self.hover_row
        self.hover_row, self.hover_button = row, btn
        m = self.model()
        for r in {old, row}:
            if 0 <= r < m.rowCount():
                self.update(m.index(r))
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if btn else Qt.CursorShape.ArrowCursor)

    def _refresh_hover(self) -> None:
        """Satır eklenip silinince imlecin altındaki satır değişir."""
        vp = self.viewport()
        self.hover_row = -1
        self._set_hover(vp.mapFromGlobal(QCursor.pos()) if vp.underMouse() else None)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        super().mouseMoveEvent(e)
        self._set_hover(e.position().toPoint())

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._set_hover(None)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        row, btn = self._button_hit(e.position().toPoint())
        self._press_row = row if btn and e.button() == Qt.MouseButton.LeftButton else -1
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        super().mouseReleaseEvent(e)
        row, btn = self._button_hit(e.position().toPoint())
        if btn and row == self._press_row and e.button() == Qt.MouseButton.LeftButton:
            self.copy_requested.emit(row, False)
        self._press_row = -1

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self.viewport().update()  # seçili satırın zemini yalnız odaktayken çizilir

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self.viewport().update()

    # --- klavye ve menü
    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.matches(QKeySequence.StandardKey.Copy) or (
                e.key() == Qt.Key.Key_C and e.modifiers() == (Qt.KeyboardModifier.ControlModifier
                                                              | Qt.KeyboardModifier.ShiftModifier)):
            row = self.currentIndex().row()
            if row >= 0:
                self.copy_requested.emit(row, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            e.accept()
            return
        super().keyPressEvent(e)

    def build_menu(self, row: int) -> QMenu:
        menu = QMenu(self)
        a_tr = menu.addAction(icons.icon("copy", C.TEXT_2), "Türkçesini kopyala")
        a_tr.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        a_tr.triggered.connect(lambda: self.copy_requested.emit(row, False))
        a_both = menu.addAction("İngilizce + Türkçe kopyala")
        a_both.setShortcut(QKeySequence("Ctrl+Shift+C"))
        a_both.setEnabled(bool(self.model().entry(row).original))
        a_both.triggered.connect(lambda: self.copy_requested.emit(row, True))
        return menu

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        idx = self.indexAt(e.pos())
        if not idx.isValid():
            return
        self.setCurrentIndex(idx)
        menu = self.build_menu(idx.row())
        menu.exec(e.globalPos())
        menu.deleteLater()


# --- bölüm içeriği ---------------------------------------------------------------------------------
def _ghost(text: str, icon_name: str, tip: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("ghost")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    ic, sz = button_icon(icon_name, C.TEXT_2, ICON["s"])
    b.setIcon(ic)
    b.setIconSize(sz)
    b.setToolTip(tip)
    return b


class HistoryPanel(QWidget):
    """Liste + boş durum + 'Tümünü kopyala' / 'Temizle'. Ana sayfanın "Son çeviriler" bölümüne konur."""

    def __init__(self, toast: Callable[[str, str], None] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._toast = toast
        self.model = HistoryModel(parent=self)
        self.view = HistoryView(self.model)
        self.view.copy_requested.connect(self.copy_row)

        self.empty = QLabel(EMPTY_TEXT)
        self.empty.setObjectName("muted")
        self.empty.setStyleSheet(f"color: {C.TEXT_3};")

        self.copy_all_btn = _ghost("Tümünü kopyala", "copy",
                                   "Listedeki tüm satırları eskiden yeniye, saatiyle, İngilizce ve Türkçe kopyalar")
        self.copy_all_btn.clicked.connect(self.copy_all)
        self.clear_btn = _ghost("Temizle", "trash", "Listeyi boşaltır; ekrandaki altyazıya dokunmaz")
        self.clear_btn.clicked.connect(self.clear)
        self.bar = QWidget()
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(SPACE["xs"])
        bl.addWidget(self.copy_all_btn)
        bl.addWidget(self.clear_btn)
        bl.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["s"])
        lay.addWidget(self.empty)
        lay.addWidget(self.view)
        lay.addWidget(self.bar)
        self._sync()

    def count(self) -> int:
        return self.model.rowCount()

    def add(self, ev: dict, now: float | None = None) -> None:
        sb = self.view.verticalScrollBar()
        anchored = sb.value() > 0  # eski satırları okuyor: yeni satır görüntüyü kaydırmasın
        old = sb.value()
        row, new = self.model.add(ev, now)
        if row < 0:
            return
        if new:
            if self.count() == 1:
                self._sync()
            if anchored:
                self.view.doItemsLayout()
                sb.setValue(old + self.view.row_height(0))

    def forget_lines(self) -> None:
        self.model.forget_lines()

    def clear(self) -> None:
        self.model.clear()
        self._sync()

    def set_fill(self, on: bool) -> None:
        """Liste bulunduğu sütunu doldursun (Ana sayfanın geniş düzeni); kapalıyken içeriğe göre büyür."""
        self.view.set_fill(on)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding if on else QSizePolicy.Policy.Preferred)

    def _sync(self) -> None:
        has = self.count() > 0
        self.empty.setVisible(not has)
        self.view.setVisible(has)
        self.bar.setVisible(has)
        self.view.update_height()

    # --- kopyalama
    def _notify(self, msg: str) -> None:
        if self._toast is not None:
            self._toast(msg, "info")

    def copy_row(self, row: int, original: bool = False) -> None:
        if not 0 <= row < self.count():
            return
        e = self.model.entry(row)
        QGuiApplication.clipboard().setText(entry_text(e, original))
        self._notify("İngilizce ve Türkçe kopyalandı" if original and e.original else "Çeviri kopyalandı")

    def copy_all(self) -> None:
        items = self.model.entries()
        if not items:
            return
        QGuiApplication.clipboard().setText(transcript(reversed(items)))
        self._notify(f"{len(items)} satır kopyalandı")
