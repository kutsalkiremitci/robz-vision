"""Başla: televizyon uygulaması gibi açılış ekranı; kurulum adımları sihirbaz gibi.

Uzaktan bakılınca da okunan büyük yazılar; kumanda gibi ok tuşlarıyla gezilir (←/→/↑/↓ en yakın öğeye, Enter ya da
Boşluk açar), odaklanan öğe camgöbeği çerçeve alır.

* Üst bant: marka, tek cümle, büyük "Çeviriyi başlat" düğmesi (sayfa açılınca odak onda), "Test et" ve canlı durum.
* Kurulum adımları (sihirbaz): numaralı adım çizgisi (1 Oyunu göster, 2 Altyazıyı işaretle, 3 Çeviriyi seç,
  4 Görünümü ayarla, 5 Yayına ekle (isteğe bağlı)). Tamamlanan adımın çemberi dolu pembe ve onaylı, aradaki çizgi
  pembe; sıradaki adım pembe halkalı. Altındaki kutu sıradaki adımı anlatır ("Adım 2 / 4", ne yapılacağı, şu anki
  durum) ve "Bu adımı yap" ile açar; kutunun çentiği çizgide o adımı gösterir. Hepsi tamamsa "Her şey hazır".
  Adım, kurulum sihirbazını açar: ilgili sayfa açılır, denetim ekranda vurgulanır ve balonda ne yapılacağı yazar
  (bkz. tour.start_setup_wizard, MainWindow.start_guide; işlevler değişmez).
* Yardım: tanıtım turu, kullanım kılavuzu, Discord kurulum desteği, Kick kanalı.

Durum sinyallerle (çeviri başladı/durdu, OBS bağlandı, yapay zekâ kurulumu) ve sayfa görünürken saniyede bir
ayarlardan yenilenir (kaynak, bölge, çeviri motoru, yerleşim başka sayfalarda değişir). Renkler tokens'tan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from altyazitr import APP_NAME
from altyazitr.gui import developer
from altyazitr.gui.icons import pixmap
from altyazitr.gui.tokens import RADIUS, SPACE, C, F, motion_ms, qcolor
from altyazitr.gui.widgets import PAGE_MARGIN, page

log = logging.getLogger(__name__)

GUIDE_URL = "https://github.com/kutsalkiremitci/robz-vision/blob/main/docs/KULLANIM.md"
DISCORD_URL = "https://discord.gg/UyyEn7SHm4"

TITLE = "Başla"
TAGLINE = "Oyundaki İngilizce altyazıyı okur, anında Türkçeye çevirir."
START_TEXT, STOP_TEXT = "Çeviriyi başlat", "Durdur"
READY_TITLE, READY_TEXT = "Her şey hazır", "Oyunu açın ve Çeviriyi başlat'a basın."
RUNNING_TITLE, RUNNING_TEXT = "Çeviri çalışıyor", "Oyuna dönebilirsiniz; altyazılar ekranınızda belirir."
DO_IT = "Devam et  ›"
START_SETUP = "Kuruluma başla  ›"   # henüz hiçbir adım onaylanmadıysa
CHECK_SUFFIX = " · kontrol edin"  # hazır ama rehberde onaylanmamış adımın durumu

NODE_D = 34                # sihirbaz düğümünün çemberi
NODE_H = SPACE["m"] + NODE_D + SPACE["s"] + 22 + 34 + SPACE["s"]  # çember, başlık, iki satır durum
NODE_MIN_W = 120
NOTCH = 9                  # adım kutusunun, çizgideki adımı gösteren çentiği
HELP_W, HELP_H = 180, 56   # yardım karosu (en küçük genişlik)
GAP = SPACE["m"] + 2


@dataclass(frozen=True)
class StepState:
    """Bir adımın durumu. key: start sayfasının iç adı; done: tamam mı; optional: atlanabilir (OBS)."""
    key: str
    number: int
    title: str
    icon: str
    status: str
    done: bool
    optional: bool = False


def _engine_text(ctx: Any) -> tuple[str, bool]:
    """Çeviri motorunun kısa adı ve hazır olup olmadığı."""
    t = ctx.s.translate
    ai = getattr(ctx, "ai", None)
    if str(getattr(ai, "state", "") or "") == "running":
        return "Yapay zekâ kuruluyor…", True  # hızlı çeviri yedekte hazır
    engine = str(getattr(t, "engine", "quickmt") or "quickmt")
    if engine == "ollama":
        model = str(getattr(t, "ollama_model", "") or "").strip()
        return (f"Yapay zekâ · {model}" if model else "Yapay zekâ (yerel)"), True
    if engine == "gemini":
        return "Gemini (çevrimiçi)", True
    if engine == "deepl":
        return "DeepL (çevrimiçi)", True
    try:
        from altyazitr.translate import quickmt_downloaded

        ready = bool(quickmt_downloaded())
    except Exception:
        ready = True
    return ("Hızlı çeviri (yerel)" if ready else "Çeviri modeli inmedi"), ready


def _obs_count(ctx: Any) -> int:
    server = getattr(ctx, "server", None)
    try:
        return len(getattr(server, "obs_clients", ()) or ())
    except TypeError:
        return 0


def step_states(ctx: Any) -> list[StepState]:
    """Beş adımın durumu (sırasıyla). Ayarlardan ve uygulamanın durumundan okunur; hiçbir şey değiştirmez."""
    s = ctx.s
    c = s.capture
    window = c.mode == "window"
    if window:
        name = (c.window_title or c.window_exe or "").strip()
        src_done = bool(name)
        src = f"Pencere: {name}" if name else "Pencere seçilmedi"
    else:
        src_done, src = True, "Tüm ekran okunuyor"
    if window:
        reg_done = len(c.region) == 4 and src_done
        reg = "Pencerede bölge seçili" if reg_done else "Önce pencereyi seçin"
    else:
        reg_done = c.is_ready()
        reg = f"{c.rect[2]} × {c.rect[3]} bölge seçili" if reg_done else "Altyazının yeri seçilmedi"
    eng, eng_done = _engine_text(ctx)
    look = "Oyun altyazısının üzerine" if s.screen_style.cover_original else "Ayrı altyazı kutusu"
    n = _obs_count(ctx)
    obs = ("OBS bağlı" if n == 1 else f"OBS bağlı ({n})") if n else "İsteğe bağlı: yayına ekleyin"
    confirmed = set(getattr(s, "guide_done", ()) or ())

    def step(key: str, number: int, title: str, icon: str, status: str, ready: bool) -> StepState:
        # tamam: ayar hazır VE kullanıcı rehberde gördü (varsayılan değerler kendiliğinden "tamam" sayılmaz)
        if ready and key not in confirmed:
            status = f"{status}{CHECK_SUFFIX}"
        return StepState(key, number, title, icon, status, ready and key in confirmed)

    return [
        step("source", 1, "Oyunu göster", "monitor", src, src_done),
        step("region", 2, "Altyazıyı işaretle", "crop", reg, reg_done),
        step("translate", 3, "Çeviriyi seç", "languages", eng, eng_done),
        step("style", 4, "Görünümü ayarla", "palette", look, True),
        StepState("obs", 5, "Yayına ekle", "broadcast", obs, n > 0, optional=True),
    ]


# Kurulum adımları: her zorunlu adımın sayfası, adı ve Başla'daki kutuda ne yapılacağı (sihirbazın ayrıntılı
# açıklamaları tour.WIZARD_STEPS'te)
GUIDE_ORDER = ("source", "region", "translate", "style")
GUIDE = {
    "source": ("home", "Oyunu göster",
               "Oyunun nerede göründüğünü seçin: Ekran (çoğu oyun) ya da Uygulama penceresi (Robz Play). "
               "Önizlemede oyunun görüntüsü belirir."),
    "region": ("home", "Altyazıyı işaretle",
               "Önizlemedeki çerçeveyi oyunun altyazısının çıktığı yere taşıyın, köşelerinden boyutlandırın. "
               "İsterseniz Tam ekranda seç ile ekranın üstünde çizin."),
    "translate": ("translate", "Çeviriyi seç",
                  "Hızlı çeviri hemen çalışır. Daha iyi çeviri için Önerilen kurulum ile yerel yapay zekâyı "
                  "kurabilirsiniz; kurulum arka planda sürer."),
    "style": ("style", "Görünümü ayarla",
              "Türkçe altyazının görünümünü seçin. Test et ile ekranınızda 5 saniyelik örnek altyazı görürsünüz."),
}


def next_step(states: list[StepState]) -> StepState | None:
    """Eksik ilk zorunlu adım; hepsi tamamsa None."""
    return next((st for st in states if not st.done and not st.optional), None)


def _font(family: str, px: int, weight: QFont.Weight | None = None) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    if weight is not None:
        f.setWeight(weight)
    return f


def _css(family: str, px: int, color: str) -> str:
    return f"font-family: '{family}'; font-size: {px}px; color: {color}; background: transparent;"


# --- yardım karosu -------------------------------------------------------------------------------------------------
class Tile(QAbstractButton):
    """Yardım karosu: ikon, ad ve ok. Odakta camgöbeği 2 px çerçeve, hafifçe yukarı kalkar (hareket azaltılmışsa
    kalkmaz); üzerine gelince az opak pembe katman. Enter / Boşluk / tıklama `clicked` verir."""

    def __init__(self, title: str, icon: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.title, self.icon_name = title, icon
        self.custom_icon = None  # QPixmap (ör. Kick logosu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(HELP_W, HELP_H)
        self.setFixedHeight(HELP_H)
        self.setAccessibleName(title)
        self._lift = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0)
        self._anim.valueChanged.connect(self._on_lift)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(HELP_W + 30, HELP_H)

    def _on_lift(self, v) -> None:
        self._lift = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        ms = motion_ms("base")
        self._anim.stop()
        if ms <= 0:
            self._on_lift(1.0 if on else 0.0)
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._lift)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self._animate(True)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._animate(False)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.isAutoRepeat():
            self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lift = self._lift
        pad = 6.0
        r = QRectF(self.rect()).adjusted(pad, pad + 3 * (1 - lift), -pad, -pad - 3 * lift)
        radius = RADIUS["l"]
        hover = self.underMouse()
        focus = self.hasFocus()
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.fillPath(path, QColor(C.PRESSED if self.isDown() else (C.HOVER if focus else C.RAISED)))
        if hover or self.isDown():  # üzerine gelme: az opak pembe katman
            p.fillPath(path, qcolor(C.PINK, 0.20 if self.isDown() else 0.13))
        if focus:
            p.setPen(QPen(QColor(C.CYAN), 2))
        elif hover:
            p.setPen(QPen(qcolor(C.PINK, 0.55), 1.5))
        else:
            p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        dpr = self.devicePixelRatioF()
        hot = hover or focus
        x = r.left() + SPACE["l"]
        pm = self.custom_icon or pixmap(self.icon_name, 20, C.CYAN if hot else C.TEXT_2, dpr=max(2.0, dpr))
        p.drawPixmap(QPointF(x, r.center().y() - 10), pm)
        tx = x + 20 + SPACE["m"]
        p.setPen(QColor(C.TEXT))
        p.setFont(_font(F.DISPLAY_SEMIBOLD, 15))
        tr = QRectF(tx, r.top(), r.right() - tx - SPACE["l"] - 18, r.height())
        text = QFontMetrics(p.font()).elidedText(self.title, Qt.TextElideMode.ElideRight, int(tr.width()))
        p.drawText(tr, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        chev = pixmap("chevron-right", 16, C.CYAN if hot else C.TEXT_3, dpr=max(2.0, dpr))
        p.drawPixmap(QPointF(r.right() - SPACE["l"] - 16, r.center().y() - 8), chev)
        p.end()


# --- sihirbaz: adım çizgisi ------------------------------------------------------------------------------------------
class StepNode(QAbstractButton):
    """Sihirbaz çizgisinde bir adım. Numaralı çember: tamamsa dolu pembe ve onay işareti; sıradaki adımsa pembe
    halka; isteğe bağlıysa kesik gri halka; değilse gri halka. Çemberin altında başlık ve kısa durum. Üzerine
    gelince az opak pembe zemin, odakta camgöbeği çerçeve; Enter / Boşluk / tıklama `clicked` verir."""

    def __init__(self, key: str, number: int, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key, self.number, self.title = key, number, title
        self.status = ""
        self.done = False
        self.optional = False
        self.next = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(NODE_H)
        self.setAccessibleName(f"Adım {number}: {title}")

    def set_state(self, status: str = "", done: bool = False, optional: bool = False, nxt: bool = False) -> None:
        if (status, done, optional, nxt) == (self.status, self.done, self.optional, self.next):
            return
        self.status, self.done, self.optional, self.next = status, done, optional, nxt
        state = "sıradaki adım" if nxt else ("tamam" if done else ("isteğe bağlı" if optional else "yapılacak"))
        self.setAccessibleDescription(f"{status}; {state}" if status else state)
        self.setToolTip(status or self.title)
        self.update()
        parent = self.parentWidget()
        if parent is not None:
            parent.update()  # aradaki çizginin rengi

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, NODE_H)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(NODE_MIN_W, NODE_H)

    def circle(self) -> QRectF:
        """Çemberin düğüm içindeki yeri (aradaki çizgi ve adım kutusunun çentiği buna göre çizilir)."""
        return QRectF((self.width() - NODE_D) / 2, SPACE["m"], NODE_D, NODE_D)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.isAutoRepeat():
            self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = RADIUS["l"]
        if self.underMouse() or self.isDown():  # üzerine gelme: az opak pembe zemin
            path = QPainterPath()
            path.addRoundedRect(r, radius, radius)
            p.fillPath(path, qcolor(C.PINK, 0.20 if self.isDown() else 0.12))
        if self.hasFocus():
            p.setPen(QPen(QColor(C.CYAN), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)
        c = self.circle()
        current = self.next
        if self.done and not current:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.PINK))
            p.drawEllipse(c)
            s = round(c.width() * 0.6)
            p.drawPixmap(QPointF(c.center().x() - s / 2, c.center().y() - s / 2),
                         pixmap("check", s, C.INVERSE, dpr=max(2.0, self.devicePixelRatioF())))
        else:
            if current:
                p.setPen(QPen(QColor(C.PINK), 2))
                p.setBrush(qcolor(C.PINK, 0.16))
                num = C.PINK
            elif self.optional:
                p.setPen(QPen(QColor(C.TEXT_3), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(QColor(C.BG))
                num = C.TEXT_2
            else:
                p.setPen(QPen(QColor(C.LINE_STRONG), 1.5))
                p.setBrush(QColor(C.BG))
                num = C.TEXT_2
            p.drawEllipse(c.adjusted(1, 1, -1, -1))
            p.setPen(QColor(num))
            p.setFont(_font(F.DISPLAY_SEMIBOLD, 15))
            p.drawText(c, int(Qt.AlignmentFlag.AlignCenter), str(self.number))
        m = SPACE["s"]
        w = r.width() - 2 * m
        top = c.bottom() + SPACE["s"]
        p.setPen(QColor(C.TEXT if (current or self.done) else C.TEXT_2))
        p.setFont(_font(F.DISPLAY_SEMIBOLD, 15))
        title = QFontMetrics(p.font()).elidedText(self.title, Qt.TextElideMode.ElideRight, int(w))
        p.drawText(QRectF(r.left() + m, top, w, 22), int(Qt.AlignmentFlag.AlignCenter), title)
        p.setPen(QColor(C.TEXT_2 if current else C.TEXT_3))
        p.setFont(_font(F.BODY_FALLBACK, 12))
        p.drawText(QRectF(r.left() + m, top + 23, w, 34),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                   self.status)
        p.end()


class StepTrack(QWidget):
    """Sihirbazın adım çizgisi: düğümler yan yana, aralarında ince çizgi. Önceki adım tamamsa çizgi pembe, sonraki
    adım isteğe bağlıysa kesik."""

    def __init__(self, nodes: list[StepNode], parent: QWidget | None = None):
        super().__init__(parent)
        self.nodes = nodes
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["xs"])
        for n in nodes:
            h.addWidget(n, 1)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for a, b in zip(self.nodes, self.nodes[1:]):
            if a.isHidden() or b.isHidden():
                continue
            ca = a.circle().translated(QPointF(a.pos()))
            cb = b.circle().translated(QPointF(b.pos()))
            x1 = ca.right() + 6
            x2 = cb.left() - 6
            if x2 <= x1:
                continue
            pen = QPen(QColor(C.PINK if a.done else C.LINE_STRONG), 2,
                       Qt.PenStyle.DashLine if b.optional else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            y = ca.center().y()
            p.drawLine(QPointF(x1, y), QPointF(x2, y))
        p.end()


class _StatusCard(QWidget):
    """Üst bandın sağındaki canlı durum: büyük nokta + "Hazır" / "Çalışıyor" ve OBS bağlantısı."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.running = False
        self.obs = 0
        self.setMinimumSize(210, 88)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_state(self, running: bool, obs: int) -> None:
        if (running, obs) != (self.running, self.obs):
            self.running, self.obs = running, obs
            self.setAccessibleName(("Çalışıyor" if running else "Hazır") + (", OBS bağlı" if obs else ""))
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS["l"], RADIUS["l"])
        p.fillPath(path, QColor(C.PANEL))
        p.setPen(QPen(QColor(C.LINE_STRONG), 1))
        p.drawPath(path)
        m = SPACE["l"]
        color = QColor(C.LIVE if self.running else C.TEXT_3)
        dot = QRectF(r.left() + m, r.top() + m + 5, 13, 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(dot)
        p.setPen(QColor(C.TEXT))
        p.setFont(_font(F.DISPLAY_SEMIBOLD, 21))
        p.drawText(QRectF(dot.right() + SPACE["s"] + 2, r.top() + m - 3, r.width(), 30),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "Çalışıyor" if self.running else "Hazır")
        p.setPen(QColor(C.TEXT_2))
        p.setFont(_font(F.BODY_FALLBACK, 12))
        obs = "OBS bağlı" if self.obs else "OBS bağlı değil"
        p.drawText(QRectF(r.left() + m, r.top() + m + 34, r.width() - 2 * m, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), obs)
        p.end()


class _StepPanel(QWidget):
    """Sihirbazın adım kutusu: sıradaki adımın numarası ("Adım 2 / 4"), adı, ne yapılacağı, şu anki durumu ve
    "Bu adımı yap". Kurulum tamamsa "Her şey hazır" (çeviri sürüyorsa "Çeviri çalışıyor"). Üst kenardaki çentik,
    adım çizgisinde bu adımın düğümünü gösterir (anchor)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = "ready"
        self.anchor: StepNode | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE["xl"], NOTCH + SPACE["l"], SPACE["xl"], SPACE["l"])
        v.setSpacing(SPACE["xs"])
        self.eyebrow = QLabel()
        self.eyebrow.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, 13, C.PINK))
        self.title = QLabel()
        self.title.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, 21, C.TEXT))
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet(_css(F.BODY_FALLBACK, 14, C.TEXT_2))
        v.addWidget(self.eyebrow)
        v.addWidget(self.title)
        v.addWidget(self.text)
        v.addSpacing(SPACE["s"])
        row = QHBoxLayout()
        row.setSpacing(SPACE["s"])
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.status = QLabel()
        self.status.setStyleSheet(_css(F.BODY_FALLBACK, 12, C.TEXT_3))
        self.button = QPushButton(DO_IT)
        self.button.setObjectName("primary")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet("QPushButton#primary { font-size: 14px; min-height: 34px; padding: 0 20px; }"
                                  f"QPushButton#primary:focus {{ border: 2px solid {C.CYAN}; }}")
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.status, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.button)
        v.addLayout(row)

    def show_next(self, st: StepState | None, running: bool, anchor: StepNode | None = None,
                  started: bool = True) -> None:
        """started: kullanıcı en az bir adımı onayladı mı (değilse düğme "Kuruluma başla")."""
        total = len(GUIDE_ORDER)
        self.anchor = anchor if st is not None else None
        if st is None:
            self.kind = "running" if running else "ready"
            self.eyebrow.setText(f"{total} / {total} adım tamam")
            self.title.setText(RUNNING_TITLE if running else READY_TITLE)
            self.text.setText(RUNNING_TEXT if running else READY_TEXT)
            self.icon.setPixmap(pixmap("circle-check", 16, C.PINK, dpr=max(2.0, self.devicePixelRatioF())))
            self.status.setText("Adımları yeniden görmek için çizgideki adıma tıklayın.")
            self.button.hide()
        else:
            self.kind = "next"
            self.eyebrow.setText(f"Adım {st.number} / {total}")
            self.title.setText(st.title)
            self.text.setText(GUIDE[st.key][2] if st.key in GUIDE else st.status)
            self.icon.setPixmap(pixmap("info", 16, C.TEXT_3, dpr=max(2.0, self.devicePixelRatioF())))
            self.status.setText(f"Şu an: {st.status.removesuffix(CHECK_SUFFIX)}")
            self.button.setText(DO_IT if started else START_SETUP)
            self.button.setToolTip("Sihirbaz bu adımın sayfasını açar ve ne yapılacağını ekranda gösterir")
            self.button.show()
        self.setAccessibleName(f"{self.eyebrow.text()}: {self.title.text()}. {self.text.text()}")
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, NOTCH + 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS["l"], RADIUS["l"])
        node = self.anchor
        if node is not None and node.isVisible():
            top = self.window()
            x = self.mapFrom(top, node.mapTo(top, node.circle().center().toPoint())).x()
            x = min(max(x, r.left() + RADIUS["l"] + NOTCH), r.right() - RADIUS["l"] - NOTCH)
            notch = QPainterPath()
            notch.moveTo(x - NOTCH, r.top() + 1)
            notch.lineTo(x, r.top() - NOTCH + 1)
            notch.lineTo(x + NOTCH, r.top() + 1)
            notch.closeSubpath()
            path = path.united(notch)
        p.fillPath(path, QColor(C.PANEL))
        p.setPen(QPen(qcolor(C.PINK, 0.45), 1))
        p.drawPath(path)
        p.end()


# --- sayfa ----------------------------------------------------------------------------------------------------------
class StartPage(QWidget):
    """Başla sayfası (bkz. modül açıklaması). win: ana pencere (goto, select_region, start_tour)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = ctx = win.ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._sa, lay = page(None)
        m = PAGE_MARGIN
        lay.setContentsMargins(m, m - 8, m, m)
        lay.setSpacing(SPACE["l"] + 4)
        root.addWidget(self._sa)

        # --- üst bant: marka, cümle, büyük düğmeler; sağda canlı durum
        hero = QHBoxLayout()
        hero.setSpacing(SPACE["xl"])
        left = QVBoxLayout()
        left.setSpacing(SPACE["s"])
        title = QLabel(APP_NAME)
        title.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, 30, C.TEXT))
        title.setAccessibleName(APP_NAME)
        tag = QLabel(TAGLINE)
        tag.setStyleSheet(_css(F.DISPLAY, 15, C.TEXT_2))
        tag.setWordWrap(True)
        left.addWidget(title)
        left.addWidget(tag)
        left.addSpacing(SPACE["m"])
        btns = QHBoxLayout()
        btns.setSpacing(SPACE["m"])
        self.start = QPushButton(START_TEXT)
        self.start.setObjectName("primary")
        self.start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start.setIconSize(QSize(18, 18))
        self.start.setStyleSheet("QPushButton#primary { font-size: 16px; min-height: 42px; padding: 0 24px;"
                                 " border-radius: 8px; }"
                                 f"QPushButton#primary:focus {{ border: 3px solid {C.CYAN}; }}")
        self.start.clicked.connect(ctx.toggle_running)
        self.test = QPushButton("Test et")
        self.test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test.setIconSize(QSize(16, 16))
        self.test.setIcon(_icon("captions", C.TEXT_2))
        self.test.setToolTip("Ekranınızda ve OBS'te 5 saniyelik örnek altyazı gösterir")
        self.test.setStyleSheet("QPushButton { font-size: 14px; min-height: 42px; padding: 0 18px;"
                                " border-radius: 8px; }"
                                f"QPushButton:focus {{ border: 2px solid {C.CYAN}; }}")
        display = getattr(ctx, "display", None)
        if display is not None and hasattr(display, "show_test"):
            self.test.clicked.connect(display.show_test)
        btns.addWidget(self.start)
        btns.addWidget(self.test)
        btns.addStretch(1)
        left.addLayout(btns)
        hero.addLayout(left, 1)
        self.status_card = _StatusCard()
        hero.addWidget(self.status_card, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(hero)

        # --- kurulum adımları (sihirbaz): adım çizgisi ve altında sıradaki adımın kutusu
        heading = self._heading("Kurulum adımları", " ")
        self.progress = heading.findChild(QLabel, "sub")
        lay.addWidget(heading)
        wizard = QVBoxLayout()
        wizard.setSpacing(SPACE["xs"])
        self.tiles: dict[str, StepNode] = {}
        for st in step_states(ctx):
            node = StepNode(st.key, st.number, st.title)
            node.clicked.connect(lambda _=False, k=st.key: self.open_step(k))
            self.tiles[st.key] = node
        self.track = StepTrack(list(self.tiles.values()))
        wizard.addWidget(self.track)
        self.banner = _StepPanel()
        self.banner.button.clicked.connect(self._do_next)
        wizard.addWidget(self.banner)
        lay.addLayout(wizard)

        # --- yardım
        lay.addWidget(self._heading("Yardım", None))
        self._help_box = QWidget()
        self._help_grid = QGridLayout(self._help_box)
        self._help_grid.setContentsMargins(0, 0, 0, 0)
        self._help_grid.setSpacing(GAP)
        self.help_tiles: list[Tile] = []
        for text, icon, fn in (("Tanıtım turu", "play-circle", self._tour),
                               ("Kullanım kılavuzu", "file-text", lambda: _open(GUIDE_URL)),
                               ("Discord desteği", "help", lambda: _open(DISCORD_URL)),
                               ("Kick'te takip et", "broadcast", developer.open_kick)):
            t = Tile(text, icon)
            if "Kick" in text:
                try:
                    t.custom_icon = developer.kick_pixmap(24, 2.0, developer.ICON_SCALE)
                except Exception:
                    t.custom_icon = None
            t.clicked.connect(fn)
            self.help_tiles.append(t)
        lay.addWidget(self._help_box)
        lay.addStretch(1)

        self._cols = 0
        self._relayout(force=True)
        for w in self._focusables():
            w.installEventFilter(self)

        self._timer = QTimer(self, interval=1000, timeout=self.refresh)
        pipe = getattr(ctx, "pipeline", None)
        if pipe is not None and hasattr(pipe, "running_changed"):
            pipe.running_changed.connect(lambda _on: self.refresh())
        sig = getattr(ctx, "obs_clients", None)
        if sig is not None and hasattr(sig, "connect"):
            sig.connect(lambda _n: self.refresh())
        ai = getattr(ctx, "ai", None)
        if ai is not None and hasattr(ai, "changed"):
            ai.changed.connect(self.refresh)
        self.refresh()

    # --- yardımcılar -------------------------------------------------------------------------------------------
    @staticmethod
    def _heading(text: str, sub: str | None) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, SPACE["s"], 0, 0)
        v.setSpacing(2)
        t = QLabel(text)
        t.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, 19, C.TEXT))
        v.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("sub")
            s.setStyleSheet(_css(F.BODY_FALLBACK, 12, C.TEXT_3))
            v.addWidget(s)
        return w

    def _focusables(self) -> list[QWidget]:
        return [self.start, self.test, *self.tiles.values(), self.banner.button, *self.help_tiles]

    # --- durum ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Adımların durumu, sıradaki adım, Başlat düğmesi ve canlı durum (ayarlardan ve uygulamadan okunur)."""
        ctx = self.ctx
        try:
            states = step_states(ctx)
        except Exception:
            log.debug("Adım durumu okunamadı", exc_info=True)
            return
        nxt = next_step(states)
        for st in states:  # çizgide kısa durum ("kontrol edin" yok; sıradaki adımı kutu anlatır)
            self.tiles[st.key].set_state(st.status.removesuffix(CHECK_SUFFIX), st.done, st.optional,
                                         nxt is not None and st.key == nxt.key)
        running = bool(getattr(getattr(ctx, "pipeline", None), "running", False))
        done = sum(1 for st in states if st.done and not st.optional)
        self.banner.show_next(nxt, running, self.tiles[nxt.key] if nxt is not None else None, started=done > 0)
        if self.progress is not None:
            self.progress.setText(f"Tamamlanan adım: {done} / {len(GUIDE_ORDER)}. Adıma tıklayın ya da ok "
                                  "tuşlarıyla gezinip Enter'a basın.")
        self.status_card.set_state(running, _obs_count(ctx))
        self.start.setText(STOP_TEXT if running else START_TEXT)
        self.start.setIcon(_icon("stop" if running else "play", C.INVERSE))
        hk = getattr(getattr(ctx.s, "hotkeys", None), "toggle", "")
        self.start.setToolTip(f"Çeviriyi {'durdur' if running else 'başlat'}" + (f" ({hk})" if hk else ""))

    def _do_next(self) -> None:
        nxt = next_step(step_states(self.ctx))
        if nxt is not None:
            self.open_step(nxt.key)

    def open_step(self, key: str) -> None:
        """Adımın karosu: ilgili sayfayı ya da pencereyi açar (işlevin kendisi o sayfada)."""
        win = self.win
        start_guide = getattr(win, "start_guide", None)
        if key in GUIDE and callable(start_guide):  # kurulum sihirbazı: sayfa açılır, denetim ekranda vurgulanır
            start_guide(key)
            return
        if key == "source":
            win.goto("home")
        elif key == "region":
            if self.ctx.s.capture.mode == "window" and not (self.ctx.s.capture.window_title
                                                            or self.ctx.s.capture.window_exe):
                win.goto("home")  # önce pencere seçilmeli
            else:
                win.select_region()
        elif key == "translate":
            win.goto("translate")
        elif key == "style":
            win.goto("style")
        elif key == "obs":
            from altyazitr.gui.dialogs import show_obs_help

            server = getattr(self.ctx, "server", None)
            show_obs_help(self, getattr(server, "url", "http://127.0.0.1:8765/"))

    def _tour(self) -> None:
        start = getattr(self.win, "start_tour", None)
        if callable(start):
            start()

    # --- düzen ve gezinme -----------------------------------------------------------------------------------
    def _relayout(self, force: bool = False) -> None:
        """Yardım karoları: genişse dört, darsa iki sütun (adım çizgisi her genişlikte tek sıra)."""
        w = max(1, self._sa.viewport().width() - 2 * PAGE_MARGIN)
        cols = 4 if w >= 4 * HELP_W + 3 * GAP else 2
        if cols == self._cols and not force:
            return
        self._cols = cols
        grid = self._help_grid
        for it in self.help_tiles:
            grid.removeWidget(it)
        for i, it in enumerate(self.help_tiles):
            grid.addWidget(it, i // cols, i % cols)
        for c in range(4):
            grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.refresh()
        self._relayout()
        self._timer.start()
        QTimer.singleShot(0, lambda: self.start.setFocus(Qt.FocusReason.OtherFocusReason))

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._timer.stop()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        """Kumanda gibi gezinme: ok tuşları en yakın odaklanabilir öğeye, Enter düğmeye basar."""
        if e.type() == QEvent.Type.KeyPress and isinstance(obj, QWidget):
            key = e.key()
            dirs = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1),
                    Qt.Key.Key_Down: (0, 1)}
            if key in dirs:
                target = self._neighbour(obj, *dirs[key])
                if target is not None:
                    target.setFocus(Qt.FocusReason.TabFocusReason)
                    self._sa.ensureWidgetVisible(target, 24, 48)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and isinstance(obj, QPushButton):
                obj.click()
                return True
        return super().eventFilter(obj, e)

    def _neighbour(self, src: QWidget, dx: int, dy: int) -> QWidget | None:
        """Yönde en yakın görünür öğe (merkezler arası; yana sapma ağır sayılır)."""
        def center(w: QWidget) -> QPointF:
            g = w.mapTo(self, w.rect().center())
            return QPointF(g.x(), g.y())

        c0 = center(src)
        best, best_d = None, None
        for w in self._focusables():
            if w is src or not w.isVisible() or not w.isEnabled():
                continue
            c = center(w)
            ddx, ddy = c.x() - c0.x(), c.y() - c0.y()
            along = ddx * dx + ddy * dy
            if along <= 4:
                continue
            across = abs(ddy if dx else ddx)
            d = along + 2.5 * across
            if best_d is None or d < best_d:
                best, best_d = w, d
        return best


def _icon(name: str, color: str):
    from altyazitr.gui.icons import icon

    return icon(name, color)


def _open(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))

