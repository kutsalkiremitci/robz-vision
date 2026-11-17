"""Tanıtım turu: ilk açılışta ana pencerenin üstünde adım adım ilerleyen spot ışığı (coach mark) katmanı.

Sabit API (main_window ve settings_page buna bağlanır):
    maybe_start_tour(win) -> bool   # settings.tour_seen False ise turu başlatır; başlattıysa True
    start_tour(win) -> None         # turu her koşulda 1. adımdan başlatır; açık tur varsa yeniden başlar

Nasıl çalışır
* `_TourOverlay` ana pencerenin tamamını kaplayan bir alt öğedir. Hedefin dışını BG %72 ile karartır,
  hedefin çevresinde yuvarlak bir delik bırakır ve deliğin dışına 2 px camgöbeği halka çizer.
* Delik `setMask` ile katmandan çıkarılır: deliğe yapılan tıklama alttaki gerçek öğeye gider, geri
  kalan her tıklama katmanda kalır.
* `_Bubble` (açıklama balonu) pencerenin ayrı bir alt öğesidir; katmanın maskesinden etkilenmez ve
  hedefin yanına (altına, üstüne, sağına, soluna; hangisi sığarsa) küçük bir okla yerleşir.
* Hedefler her adımda tembel olarak (lambda) bulunur; bulunamazsa ya da görünmüyorsa adım ortada,
  deliksiz gösterilir. Turdaki her hata kaydedilir ve tur kapanır; uygulama çökmez.
* Klavye: Sağ ok / Enter ileri, Sol ok geri, Esc "Daha sonra". Odak balonun düğmelerinde kalır.
* "Bitir" turu görüldü sayar (`tour_seen`); "Daha sonra" saymaz, tur bir sonraki açılışta yine gelir.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from altyazitr import APP_NAME
from altyazitr.gui.tokens import RADIUS, SPACE, C, F, motion_ms, qcolor

log = logging.getLogger(__name__)

# --- ölçüler -------------------------------------------------------------------------------------
DIM_ALPHA = 0.72          # karartma: BG %72
RING_PX = 2               # deliğin çevresindeki camgöbeği halka
HOLE_RADIUS = RADIUS["l"]
HOLE_PAD = SPACE["s"]     # hedefin çevresinde bırakılan boşluk
EDGE = SPACE["l"]         # balonun pencere kenarına en yakın mesafesi
GAP = SPACE["s"]          # delik kenarı → ok ucu
ARROW_H = 9               # ok derinliği
ARROW_W = 18              # ok tabanı
BUBBLE_W = 360            # balon genişliği (dar pencerede küçülür)
PAD_X, PAD_Y = SPACE["l"] + SPACE["xs"], SPACE["l"]   # balon iç boşluğu: 20 / 16
TITLE_PX = 17
TITLE_LIFT = 3            # Bahnschrift'te Ö/Ü/İ/Ğ noktaları yazı yüksekliğini 2–3 px aşar; kırpılmasın
SCROLL_MARGIN = 40

_ATTR = "_tour_overlay"   # pencerede açık turun tutulduğu öznitelik
_DEFAULT_PREFER = ("below", "above", "right", "left")


# --- adımlar -------------------------------------------------------------------------------------
Resolver = Callable[[object], object]


@dataclass(frozen=True)
class Step:
    """Turun bir adımı. `targets` sırayla denenen çözücülerdir: her biri pencereyi alır ve bir QWidget,
    (QWidget, o öğenin koordinatlarında QRect) ya da None döndürür. Hiçbiri sonuç vermezse adım ortada
    deliksiz gösterilir."""

    key: str
    title: str
    body: str | Callable[[object], str]
    targets: tuple[Resolver, ...] = ()
    page: str | None = None
    prefer: tuple[str, ...] = _DEFAULT_PREFER
    pad: int = HOLE_PAD

    def text(self, win) -> str:
        if callable(self.body):
            try:
                return str(self.body(win))
            except Exception:
                log.debug("Tur metni oluşturulamadı: %s", self.key, exc_info=True)
                return ""
        return self.body


def _dative(name: str) -> str:
    """Özel ada yönelme eki: "Robz Vision'a" (son ünlüye göre; ünlüyle bitiyorsa kaynaştırma y)."""
    vowels = [ch for ch in name.lower() if ch in "aıoueiöü"]
    suffix = "a" if not vowels or vowels[-1] in "aıou" else "e"
    if name and name[-1].lower() in "aıoueiöü":
        suffix = "y" + suffix
    return f"{name}'{suffix}"


def _home(attr: str) -> Resolver:
    return lambda w: getattr(w.pages["home"], attr)


def _section_of(widget) -> QWidget | None:
    """Öğenin içinde durduğu sayfa bölümü (QFrame#card); bulunamazsa None."""
    w = widget.parentWidget() if isinstance(widget, QWidget) else None
    for _ in range(8):
        if w is None or isinstance(w, QScrollArea):
            return None
        if w.objectName() == "card":
            return w
        w = w.parentWidget()
    return None


def _home_section(attr: str) -> Resolver:
    """Ana sayfadaki öğenin bölümü: görünen içeriği (başlık + denetimler); üstteki ayırıcı çizgi ve
    bölüm boşluğu deliğin dışında kalır."""

    def get(w):
        card = _section_of(getattr(w.pages["home"], attr))
        if card is None:
            return None
        r = card.childrenRect()
        return card, (r if r.isValid() and not r.isEmpty() else card.rect())

    return get


def _nav_item(title: str, index: int) -> Resolver:
    """Kenar çubuğundaki sayfa öğesi (önce adına, sonra sırasına göre)."""

    def get(w):
        nav = w.nav
        item = None
        for i in range(nav.count()):
            if nav.item(i).text() == title:
                item = nav.item(i)
                break
        if item is None:
            item = nav.item(index)
        if item is None:
            return None
        r = nav.visualItemRect(item)
        if r.isEmpty():
            return None
        inset = SPACE["xs"]
        return nav.viewport(), r.adjusted(inset, 0, -inset, 0)

    return get


def _toggle_hotkey(win) -> str:
    try:
        return str(win.ctx.s.hotkeys.toggle or "").strip()
    except Exception:
        return ""


def _start_body(win) -> str:
    hk = _toggle_hotkey(win)
    if hk:
        return f"Okumayı ve çeviriyi başlatır. Oyundayken {hk} ile de başlatıp durdurabilirsiniz."
    return "Okumayı ve çeviriyi başlatır. Tekrar bastığınızda durur."


NAV_PREFER = ("right", "below", "above", "left")

STEPS: tuple[Step, ...] = (
    Step("welcome", f"{_dative(APP_NAME)} hoş geldiniz",
         "Oyundaki İngilizce altyazıyı okur, Türkçesini ekranınızda ve yayında gösterir. "
         "Kısa bir turla nasıl kullanılacağını görün.",
         page="home"),
    Step("region", "Önce bölgeyi seçin",
         "Altyazının çıktığı alanı fareyle işaretleyin. Belirli bir pencereden okumak isterseniz "
         "yukarıdan Uygulama penceresi'ni seçin.",
         (_home("b_region"),), page="home"),
    Step("preview", "Canlı önizleme",
         "Seçtiğiniz alan burada görünür. Çalışırken metin olarak algılanan yerler yeşille işaretlenir.",
         (_home("preview"),), page="home"),
    Step("start", "Başlat", _start_body, (_home("btn"),), page="home"),
    Step("last", "Son çeviri",
         "Okunan İngilizce satır ve Türkçesi burada görünür. Çeviri tuhafsa önce satırın doğru okunup "
         "okunmadığına bakın.",
         (_home_section("tr"), _home("tr")), page="home"),
    Step("overlay", "Ekranınızdaki altyazı",
         "Konumu ayarla ile Türkçe altyazı kutusunu ekranda taşıyıp boyutlandırın. Bu kutu yayına girmez; "
         "girmesi için Yayında da görünsün'ü açın.",
         (_home_section("ov_edit"), _home("ov_edit")), page="home"),
    Step("obs", "Yayına ekleyin",
         "İzleyicilerin de görmesi için bu adresi OBS'e Tarayıcı Kaynağı olarak ekleyin. "
         "Nasıl ekleneceğini yandaki yardım düğmesi gösterir.",
         (_home_section("url"), _home("url")), page="home"),
    Step("style", "Görünüm",
         "Ekranınızdaki kutu ile yayındaki altyazı ayrı ayarlanır. Üstten Ekranımda ya da Yayınımda'yı "
         "seçip yazı tipini, boyutu ve rengi değiştirin.",
         (_nav_item("Görünüm", 1), lambda w: w.nav), page="style", prefer=NAV_PREFER, pad=0),
    Step("engine", "Çeviri motoru",
         "Varsayılan motor ücretsizdir ve tamamen bilgisayarınızda çalışır. Daha doğal çeviri için "
         "Ollama'yı veya Gemini'yi seçebilirsiniz.",
         (_nav_item("Çeviri", 2), lambda w: w.nav), page="translate", prefer=NAV_PREFER, pad=0),
    Step("done", "Hazırsınız",
         "Bölgeyi seçip Başlat'a basmanız yeterli. Bu turu Ayarlar'dan yeniden başlatabilirsiniz.",
         page="home"),
)


# --- yardımcılar ---------------------------------------------------------------------------------
def _alive(obj) -> bool:
    if obj is None:
        return False
    try:
        import shiboken6

        return shiboken6.isValid(obj)
    except Exception:
        return True


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if hi < lo else min(max(v, lo), hi)


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
                  a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t)


def _guard(fn):
    """Tur hatası uygulamayı bozmasın: hatayı kaydet, turu kapat."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if getattr(self, "_closed", False):
            return None
        try:
            return fn(self, *args, **kwargs)
        except Exception:
            log.exception("Tanıtım turu hatası; tur kapatılıyor")
            self._fail()
            return None

    return wrapper


# --- balon ---------------------------------------------------------------------------------------
class _Bubble(QWidget):
    """Açıklama balonu: başlık, kısa metin, adım sayacı ve düğmeler. Hedefe bakan kenarında küçük bir ok
    çizer; ok için gereken boşluk iç kenar boşluğuna eklenir."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("tourBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.side: str | None = None   # balonun hedefe göre konumu: below / above / right / left
        self.arrow_at = 0.0             # okun kenar boyunca yeri (balon koordinatı)
        self.setStyleSheet(
            f"QLabel#tourTitle {{ font-family: '{F.DISPLAY_SEMIBOLD}', '{F.DISPLAY}', '{F.BODY_FALLBACK}';"
            f" font-size: {TITLE_PX}px; color: {C.TEXT}; background: transparent; }}"
            f"QLabel#tourBody {{ font-family: '{F.BODY}', '{F.BODY_FALLBACK}'; font-size: {F.BODY_SIZE}px;"
            f" color: {C.TEXT_2}; background: transparent; }}"
            f"QLabel#tourCount {{ font-family: '{F.DISPLAY}', '{F.BODY_FALLBACK}'; font-size: {F.BODY_SIZE}px;"
            f" color: {C.TEXT_3}; background: transparent; }}")

        self.title = QLabel()
        self.title.setObjectName("tourTitle")
        self.title.setWordWrap(True)
        self.title.setContentsMargins(0, TITLE_LIFT, 0, 0)
        self.body = QLabel()
        self.body.setObjectName("tourBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.count = QLabel()
        self.count.setObjectName("tourCount")
        self.count.setTextFormat(Qt.TextFormat.RichText)

        self.later_btn = QPushButton("Daha sonra")
        self.later_btn.setObjectName("ghost")
        self.later_btn.setToolTip("Turu kapatır; bir sonraki açılışta yeniden gösterilir (Esc)")
        self.back_btn = QPushButton("Geri")
        self.back_btn.setToolTip("Önceki adım (Sol ok)")
        self.next_btn = QPushButton("İleri")
        self.next_btn.setObjectName("primary")
        self.next_btn.setToolTip("Sonraki adım (Sağ ok veya Enter)")
        self.buttons = (self.later_btn, self.back_btn, self.next_btn)   # görsel ve Tab sırası
        for b in self.buttons:
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoDefault(False)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.back_btn.setMinimumWidth(64)
        self.next_btn.setMinimumWidth(76)

        v = QVBoxLayout(self)
        v.setSpacing(0)
        v.addWidget(self.title)
        v.addSpacing(SPACE["s"] - 2)
        v.addWidget(self.body)
        v.addSpacing(SPACE["l"] + SPACE["xs"])
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["s"])
        row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.later_btn)
        row.addWidget(self.back_btn)
        row.addWidget(self.next_btn)
        v.addLayout(row)
        self.set_side(None)

    # --- içerik --------------------------------------------------------------------------------
    def set_step(self, title: str, body: str, num: int, total: int) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.count.setText(f"<span style=\"color: {C.TEXT_2};\">{num}</span>/{total}")
        self.count.setAccessibleName(f"Adım {num} / {total}")
        self.back_btn.setVisible(num > 1)
        last = num >= total
        self.later_btn.setVisible(not last)   # son adımda ertelenecek bir şey kalmaz
        self.next_btn.setText("Bitir" if last else "İleri")
        self.next_btn.setToolTip("Turu bitirir (Enter)" if last else "Sonraki adım (Sağ ok veya Enter)")
        self.setAccessibleName(f"Tanıtım turu: {title}")
        self.setAccessibleDescription(body)
        if self.focusWidget() is not None and not self.focusWidget().isVisibleTo(self):
            self.next_btn.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_side(self, side: str | None) -> None:
        self.side = side
        m = [PAD_X, PAD_Y - TITLE_LIFT, PAD_X, PAD_Y]   # sol, üst, sağ, alt
        if side == "below":
            m[1] += ARROW_H
        elif side == "above":
            m[3] += ARROW_H
        elif side == "right":
            m[0] += ARROW_H
        elif side == "left":
            m[2] += ARROW_H
        self.layout().setContentsMargins(*m)

    def body_height(self, width: int) -> int:
        """Oksuz balonun verilen genişlikteki yüksekliği."""
        for w in (self, self.title, self.body, self.count, *self.buttons):
            w.ensurePolished()
        side = self.side
        self.set_side(None)
        try:
            h = self.layout().totalHeightForWidth(width)
        except Exception:
            h = -1
        if h <= 0:
            h = self.heightForWidth(width)
        if h <= 0:
            h = self.sizeHint().height()
        self.set_side(side)
        return int(h)

    def set_arrow_at(self, v: float) -> None:
        if abs(v - self.arrow_at) > 0.01:
            self.arrow_at = v
            self.update()

    # --- odak: Tab balonun düğmeleri arasında döner ---------------------------------------------
    def focusNextPrevChild(self, next_: bool) -> bool:  # noqa: N802 - Qt
        btns = [b for b in self.buttons if b.isVisibleTo(self) and b.isEnabled()]
        if not btns:
            return False
        cur = QApplication.focusWidget() or self.focusWidget()
        i = btns.index(cur) if cur in btns else (-1 if next_ else 0)
        target = btns[(i + (1 if next_ else -1)) % len(btns)]
        target.setFocus(Qt.FocusReason.TabFocusReason if next_ else Qt.FocusReason.BacktabFocusReason)
        return True

    # --- çizim ---------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        try:
            self._paint()
        except Exception:
            log.debug("Tur balonu çizilemedi", exc_info=True)

    def _paint(self) -> None:
        r = QRectF(self.rect())
        side = self.side
        body = QRectF(r)
        if side == "below":
            body.setTop(r.top() + ARROW_H)
        elif side == "above":
            body.setBottom(r.bottom() - ARROW_H)
        elif side == "right":
            body.setLeft(r.left() + ARROW_H)
        elif side == "left":
            body.setRight(r.right() - ARROW_H)
        body = body.adjusted(0.5, 0.5, -0.5, -0.5)
        rad = float(RADIUS["l"])
        path = QPainterPath()
        path.addRoundedRect(body, rad, rad)
        if side is not None:
            half = ARROW_W / 2
            if side in ("below", "above"):
                at = _clamp(self.arrow_at, body.left() + rad + half + 2, body.right() - rad - half - 2)
                base_y, tip_y = (body.top() + 1, r.top() + 0.5) if side == "below" else \
                    (body.bottom() - 1, r.bottom() - 0.5)
                pts = (QPointF(at - half, base_y), QPointF(at, tip_y), QPointF(at + half, base_y))
            else:
                at = _clamp(self.arrow_at, body.top() + rad + half + 2, body.bottom() - rad - half - 2)
                base_x, tip_x = (body.left() + 1, r.left() + 0.5) if side == "right" else \
                    (body.right() - 1, r.right() - 0.5)
                pts = (QPointF(base_x, at - half), QPointF(tip_x, at), QPointF(base_x, at + half))
            tri = QPainterPath(pts[0])
            tri.lineTo(pts[1])
            tri.lineTo(pts[2])
            tri.closeSubpath()
            path = path.united(tri)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(path, qcolor(C.RAISED))
        p.setPen(QPen(qcolor(C.LINE_STRONG), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


# --- katman ve tur denetimi ------------------------------------------------------------------------
class _TourOverlay(QWidget):
    """Pencereyi kaplayan karartma katmanı; turun durumunu ve adımlar arası geçişi de yönetir."""

    def __init__(self, win, steps: tuple[Step, ...] = STEPS):
        super().__init__(win)
        self.win = win
        self.steps = tuple(steps)
        self.index = 0
        self._closed = False
        self._gen = 0
        self._hole: QRectF | None = None          # şu an çizilen delik
        self._pos = QPointF()                      # balonun şu anki sol üst köşesi
        self._from: tuple[QRectF | None, QPointF] | None = None
        self._to: tuple[QRectF | None, QPointF] | None = None
        self._target: QWidget | None = None
        self._scroll_bars: list = []
        self._prev_focus = None

        self.setObjectName("tourOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setAccessibleName("Tanıtım turu")
        self.hide()

        self.bubble = _Bubble(win)
        self.bubble.hide()
        self.bubble.later_btn.clicked.connect(lambda: self.later())
        self.bubble.back_btn.clicked.connect(lambda: self.back())
        self.bubble.next_btn.clicked.connect(lambda: self.next())
        for obj in (self.bubble, *self.bubble.buttons):
            obj.installEventFilter(self)

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self._tick(float(v)))
        self._anim.finished.connect(lambda: self._tick(1.0))

        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(0)
        self._relayout.timeout.connect(lambda: self._place(animate=False))

    # --- genel API -------------------------------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self.steps)

    @property
    def closed(self) -> bool:
        return self._closed

    @_guard
    def start(self) -> None:
        self._prev_focus = QApplication.focusWidget()
        self.win.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self.setGeometry(self.win.rect())
        self.show()
        self.raise_()
        self.bubble.show()
        self.bubble.raise_()
        self._go(0, animate=False)
        self.bubble.next_btn.setFocus(Qt.FocusReason.OtherFocusReason)

    def next(self, *_args) -> None:
        if self.index >= self.count - 1:
            self.finish()
        else:
            self.go(self.index + 1)

    def back(self, *_args) -> None:
        if self.index > 0:
            self.go(self.index - 1)

    def later(self, *_args) -> None:
        """'Daha sonra' / Esc: kapatır; tur görülmüş sayılmaz."""
        self.close_tour(finished=False)

    def finish(self, *_args) -> None:
        """'Bitir': turu görüldü olarak kaydeder ve kapatır."""
        self.close_tour(finished=True)

    @_guard
    def go(self, index: int) -> None:
        self._go(max(0, min(index, self.count - 1)), animate=True)

    def close_tour(self, finished: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if finished:
            try:
                self.win.ctx.s.tour_seen = True
                self.win.ctx.save_later()
            except Exception:
                log.exception("Tur durumu kaydedilemedi")
        for fn in (self._anim.stop, self._relayout.stop, self._unbind_target):
            try:
                fn()
            except Exception:
                log.debug("Tur kapatılırken", exc_info=True)
        try:
            app = QApplication.instance()
            if app is not None:
                app.focusChanged.disconnect(self._on_focus_changed)
        except Exception:
            pass
        try:
            self.win.removeEventFilter(self)
        except Exception:
            pass
        try:
            self.bubble.hide()
            self.hide()
        except Exception:
            pass
        try:
            if getattr(self.win, _ATTR, None) is self:
                setattr(self.win, _ATTR, None)
        except Exception:
            pass
        self._restore_focus()
        for w in (self.bubble, self):
            try:
                w.deleteLater()
            except Exception:
                pass

    # --- adım geçişi -------------------------------------------------------------------------
    def _go(self, index: int, animate: bool) -> None:
        self.index = index
        self._gen += 1
        gen = self._gen
        step = self.steps[index]
        self.bubble.set_step(step.title, step.text(self.win), index + 1, self.count)
        if step.page:
            try:
                self.win.goto(step.page)
            except Exception:
                log.debug("Tur sayfaya geçemedi: %s", step.page, exc_info=True)
        self._flush_layouts()
        found = self._find(step)
        if found is not None:
            self._scroll_into_view(*found)
            self._flush_layouts()
        self._place(animate=animate)
        # düzen yerleşince (yeni açılan sayfa, kaydırma) deliği yeniden ölç
        QTimer.singleShot(0, self, lambda: self._settle(gen))
        QTimer.singleShot(50, self, lambda: self._settle(gen))

    @_guard
    def _settle(self, gen: int) -> None:
        if gen != self._gen:
            return
        found = self._find(self.steps[self.index])
        if found is not None:
            self._scroll_into_view(*found)
        self._place(animate=False)

    @staticmethod
    def _flush_layouts() -> None:
        try:
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
        except Exception:
            pass

    # --- hedef -------------------------------------------------------------------------------
    def _find(self, step: Step) -> tuple[QWidget, QRect] | None:
        """Adımın hedefi: (öğe, öğe koordinatlarında dikdörtgen). Kaydırılıp görünmez olmuş olabilir."""
        for get in step.targets:
            try:
                res = get(self.win)
                if res is None:
                    continue
                widget, rect = res if isinstance(res, tuple) else (res, None)
                if not isinstance(widget, QWidget) or not _alive(widget):
                    continue
                if widget.window() is not self.win.window() or not widget.isVisibleTo(self.win):
                    continue
                local = QRect(rect) if rect is not None else widget.rect()
                if local.width() > 0 and local.height() > 0:
                    return widget, local
            except Exception:
                log.debug("Tur hedefi bulunamadı: %s", step.key, exc_info=True)
        return None

    def _visible_rect(self, widget: QWidget, rect: QRect) -> QRect | None:
        """Öğe koordinatlarındaki dikdörtgenin, üst öğelerin kırptığı görünen kısmı (pencere koordinatı)."""
        r = QRect(rect)
        w = widget
        while w is not None and w is not self.win:
            r = r.intersected(w.rect())
            if r.isEmpty():
                return None
            r.translate(w.pos())
            w = w.parentWidget()
        if w is None:
            return None
        r = r.intersected(self.win.rect())
        return None if r.width() < 2 or r.height() < 2 else r

    def _scroll_into_view(self, widget: QWidget, rect: QRect) -> None:
        """Hedefi içeren kaydırma alanında hedefi görünür yapar; kenara yapışmışsa 40 px pay bırakır."""
        w = widget.parentWidget()
        while w is not None and w is not self.win:
            if isinstance(w, QScrollArea):
                inner = w.widget()
                if inner is not None and (inner is widget or inner.isAncestorOf(widget)):
                    w.ensureWidgetVisible(widget, SCROLL_MARGIN, SCROLL_MARGIN)
                    self._keep_margin(w, inner, widget, rect)
                return
            w = w.parentWidget()

    @staticmethod
    def _keep_margin(sa: QScrollArea, inner: QWidget, widget: QWidget, rect: QRect) -> None:
        # ensureWidgetVisible öğe zaten görünüyorsa hiç kaydırmaz; delik ve halka kenarda kırpılmasın
        bar = sa.verticalScrollBar()
        r = QRect(widget.mapTo(inner, rect.topLeft()), rect.size())
        view_top, vh, m = -inner.y(), sa.viewport().height(), SCROLL_MARGIN
        if r.height() + 2 * m >= vh:
            return
        if r.top() - m < view_top:
            delta = r.top() - m - view_top
        elif r.bottom() + m > view_top + vh:
            delta = r.bottom() + m - (view_top + vh)
        else:
            return
        bar.setValue(bar.value() + delta)

    def _bind_target(self, widget: QWidget | None) -> None:
        """Hedef taşınır/boyutlanır ya da sayfa kaydırılırsa delik onu izlesin."""
        if widget is self._target:
            return
        self._unbind_target()
        if widget is None:
            return
        self._target = widget
        widget.installEventFilter(self)
        w = widget.parentWidget()
        while w is not None and w is not self.win:
            if isinstance(w, QScrollArea):
                for bar in (w.verticalScrollBar(), w.horizontalScrollBar()):
                    bar.valueChanged.connect(self._schedule)
                    self._scroll_bars.append(bar)
            w = w.parentWidget()

    def _unbind_target(self) -> None:
        if self._target is not None and _alive(self._target):
            self._target.removeEventFilter(self)
        self._target = None
        for bar in self._scroll_bars:
            try:
                if _alive(bar):
                    bar.valueChanged.disconnect(self._schedule)
            except Exception:
                pass
        self._scroll_bars = []

    def _schedule(self, *_args) -> None:
        """Deliği ve balonu bir sonraki olay döngüsünde yeniden ölç (art arda gelen istekler birleşir).
        Not: sinyali doğrudan QTimer.start'a bağlamayın; start(int) değeri aralık sanar."""
        if not self._closed:
            self._relayout.start(0)

    # --- yerleşim ----------------------------------------------------------------------------
    @_guard
    def _place(self, animate: bool) -> None:
        if not _alive(self.win):
            return
        if self.geometry() != self.win.rect():
            self.setGeometry(self.win.rect())
        step = self.steps[self.index]
        found = self._find(step)
        # kaydırılıp görünmez olsa da hedefi izle: geri kaydırılınca delik yeniden belirsin
        self._bind_target(found[0] if found is not None else None)
        vis = self._visible_rect(*found) if found is not None else None
        hole = None
        if vis is not None:
            pad = step.pad
            r = QRectF(vis).adjusted(-pad, -pad, pad, pad)
            inset = RING_PX + 1
            r = r.intersected(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset))
            if r.width() >= 8 and r.height() >= 8:
                hole = r
        side, rect = self._bubble_rect(hole, step.prefer)
        self.bubble.set_side(side)
        self.bubble.setFixedSize(round(rect.width()), round(rect.height()))
        pos = QPointF(round(rect.x()), round(rect.y()))

        ms = motion_ms("base") if animate else 0
        if ms > 0 and self.bubble.isVisible() and self._from_state_ready():
            self._from = (QRectF(self._hole) if self._hole is not None else None, QPointF(self._pos))
            self._to = (hole, pos)
            self._anim.stop()
            self._anim.setDuration(ms)
            self._anim.start()
        elif self._anim.state() == QVariantAnimation.State.Running and self._from is not None:
            self._to = (hole, pos)   # süren geçiş yeni yere doğru devam eder
        else:
            self._anim.stop()
            self._from = self._to = None
            self._apply(hole, pos)

    def _from_state_ready(self) -> bool:
        return not self._pos.isNull() or self._hole is not None

    def _tick(self, t: float) -> None:
        if self._closed or self._to is None:
            return
        try:
            th, tp = self._to
            if self._from is None or t >= 1.0:
                self._apply(th, tp)
                if t >= 1.0:
                    self._from = self._to = None
                return
            fh, fp = self._from
            hole = _lerp_rect(fh, th, t) if (fh is not None and th is not None) else th
            pos = QPointF(fp.x() + (tp.x() - fp.x()) * t, fp.y() + (tp.y() - fp.y()) * t)
            self._apply(hole, pos)
        except Exception:
            log.exception("Tanıtım turu hatası; tur kapatılıyor")
            self._fail()

    def _apply(self, hole: QRectF | None, pos: QPointF) -> None:
        self._hole = QRectF(hole) if hole is not None else None
        self._pos = QPointF(pos)
        self.bubble.move(round(pos.x()), round(pos.y()))
        side = self.bubble.side
        if hole is not None and side is not None:
            if side in ("below", "above"):
                self.bubble.set_arrow_at(hole.center().x() - pos.x())
            else:
                self.bubble.set_arrow_at(hole.center().y() - pos.y())
        if hole is None:
            self.clearMask()
        else:
            path = QPainterPath()
            path.addRoundedRect(hole, HOLE_RADIUS, HOLE_RADIUS)
            cut = QRegion(path.toFillPolygon().toPolygon())
            self.setMask(QRegion(self.rect()).subtracted(cut))
        self.update()
        self.bubble.update()

    def _bubble_rect(self, hole: QRectF | None, prefer: tuple[str, ...]) -> tuple[str | None, QRectF]:
        """Balonun yeri: tercih sırasıyla hedefin altı/üstü/sağı/solu; hiçbiri sığmazsa en az taşan yer."""
        area = QRectF(self.rect()).adjusted(EDGE, EDGE, -EDGE, -EDGE)
        cw = float(min(BUBBLE_W, max(200.0, area.width())))
        bh = float(self.bubble.body_height(round(cw)))
        if hole is None:
            c = area.center()
            return None, QRectF(c.x() - cw / 2, max(area.top(), c.y() - bh / 2), cw, bh)
        cx, cy = hole.center().x(), hole.center().y()
        x_h = _clamp(cx - cw / 2, area.left(), area.right() - cw)
        y_v = _clamp(cy - bh / 2, area.top(), area.bottom() - bh)
        options: list[tuple[float, int, str, QRectF]] = []
        for rank, side in enumerate(prefer):
            if side == "below":
                rect = QRectF(x_h, hole.bottom() + GAP, cw, bh + ARROW_H)
                over = rect.bottom() - area.bottom()
            elif side == "above":
                rect = QRectF(x_h, hole.top() - GAP - ARROW_H - bh, cw, bh + ARROW_H)
                over = area.top() - rect.top()
            elif side == "right":
                rect = QRectF(hole.right() + GAP, y_v, cw + ARROW_H, bh)
                over = rect.right() - area.right()
            elif side == "left":
                rect = QRectF(hole.left() - GAP - ARROW_H - cw, y_v, cw + ARROW_H, bh)
                over = area.left() - rect.left()
            else:
                continue
            if over <= 0:
                return side, rect
            options.append((over, rank, side, rect))
        if not options:
            return None, QRectF(area.center().x() - cw / 2, area.center().y() - bh / 2, cw, bh)
        # hiçbir yana sığmıyor: en az taşan yana koy, pencerenin içine it
        _over, _rank, side, rect = min(options, key=lambda o: (o[0], o[1]))
        rect.moveLeft(_clamp(rect.left(), area.left(), area.right() - rect.width()))
        rect.moveTop(_clamp(rect.top(), area.top(), area.bottom() - rect.height()))
        if rect.intersects(hole):   # hedefin üstüne binecekse ok anlamsız: oksuz, pencerenin altına
            rect = QRectF(x_h, area.bottom() - bh, cw, bh)
            side = None
        return side, rect

    # --- olaylar -------------------------------------------------------------------------------
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 - Qt
        if self._closed:
            return False
        try:
            t = ev.type()
            if obj is self.win:
                if t in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange,
                         QEvent.Type.LayoutRequest):
                    if t == QEvent.Type.Resize:
                        self.setGeometry(self.win.rect())
                    self._schedule()
                return False
            if obj is self._target:
                if t in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
                    self._schedule()
                return False
            if t in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
                return self._key(obj, ev, override=t == QEvent.Type.ShortcutOverride)
        except Exception:
            log.exception("Tanıtım turu hatası; tur kapatılıyor")
            self._fail()
        return False

    _RIGHT, _LEFT, _ESC = Qt.Key.Key_Right.value, Qt.Key.Key_Left.value, Qt.Key.Key_Escape.value
    _ENTER = (Qt.Key.Key_Return.value, Qt.Key.Key_Enter.value)
    _MODS_OK = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)

    def _key(self, obj, ev, override: bool) -> bool:
        k = int(ev.key())
        if k not in (self._RIGHT, self._LEFT, self._ESC, *self._ENTER) or ev.modifiers() not in self._MODS_OK:
            return False
        if override:   # pencere kısayolları bu tuşları turdan çalmasın
            ev.accept()
            return True
        if ev.isAutoRepeat():
            return True
        if k == self._RIGHT:
            self.next()
        elif k == self._LEFT:
            self.back()
        elif k == self._ESC:
            self.later()
        else:   # Enter: odaktaki tur düğmesine basar (tuş olayı odaktaki öğeye gelir); değilse ileri
            if obj in self.bubble.buttons and obj.isVisible():
                obj.click()
            else:
                self.next()
        return True

    def _on_focus_changed(self, _old, new) -> None:
        """Odak balonda kalsın (yalnızca vurgulanan hedefin içine gidebilir)."""
        if self._closed or new is None:
            return
        try:
            if new.window() is not self.win.window():
                return
            if new is self.bubble or self.bubble.isAncestorOf(new):
                return
            tgt = self._target
            if tgt is not None and _alive(tgt) and (new is tgt or tgt.isAncestorOf(new)):
                return
            QTimer.singleShot(0, self, self._focus_bubble)
        except RuntimeError:
            pass

    def _focus_bubble(self) -> None:
        if self._closed:
            return
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self.bubble or self.bubble.isAncestorOf(fw)):
            return
        self.bubble.next_btn.setFocus(Qt.FocusReason.OtherFocusReason)

    def _restore_focus(self) -> None:
        try:
            prev = self._prev_focus
            if prev is not None and _alive(prev) and prev.isVisible() and prev.window() is self.win.window():
                prev.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self.win.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            log.debug("Odak geri verilemedi", exc_info=True)

    # tıklamalar katmanda kalır (deliğin içi maske dışında olduğu için alttaki öğeye gider)
    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def wheelEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def contextMenuEvent(self, e) -> None:  # noqa: N802 - Qt
        e.accept()

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            path.addRect(QRectF(self.rect()))
            hole = self._hole
            if hole is not None:
                path.addRoundedRect(hole, HOLE_RADIUS, HOLE_RADIUS)
            p.fillPath(path, qcolor(C.BG, DIM_ALPHA))
            if hole is not None:
                h = RING_PX / 2
                p.setPen(QPen(qcolor(C.CYAN), RING_PX))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(hole.adjusted(-h, -h, h, h), HOLE_RADIUS + h, HOLE_RADIUS + h)
            p.end()
        except Exception:
            log.exception("Tanıtım turu çizilemedi; tur kapatılıyor")
            QTimer.singleShot(0, self, self._fail)

    def _fail(self) -> None:
        try:
            self.close_tour(finished=False)
        except Exception:
            try:
                self._closed = True
                self.bubble.hide()
                self.hide()
            except Exception:
                pass


# --- herkese açık API ----------------------------------------------------------------------------
def active_tour(win) -> _TourOverlay | None:
    """Pencerede açık olan tur (yoksa None)."""
    t = getattr(win, _ATTR, None)
    if t is None or not _alive(t) or t.closed:
        return None
    return t


def start_tour(win) -> None:
    """Turu 1. adımdan başlatır; açık bir tur varsa kapatıp yeniden başlatır. Hata uygulamayı bozmaz."""
    try:
        old = active_tour(win)
        if old is not None:
            old.close_tour(finished=False)
        tour = _TourOverlay(win)
        setattr(win, _ATTR, tour)
        tour.start()
    except Exception:
        log.exception("Tanıtım turu başlatılamadı")
        t = getattr(win, _ATTR, None)
        if t is not None and _alive(t):
            t._fail()


def maybe_start_tour(win) -> bool:
    """Tur daha önce bitirilmediyse (`tour_seen` False) başlatır; başlattıysa True döndürür."""
    try:
        if getattr(win.ctx.s, "tour_seen", True):
            return False
    except Exception:
        return False
    start_tour(win)
    return active_tour(win) is not None
