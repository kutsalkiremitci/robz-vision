"""Yerel yapay zekâ kurulumunun arayüz parçaları. İlk açılış penceresi (gui/setup_dialog.py) ve Çeviri sayfası
ortak kullanır. Görünüm DESIGN.md'den ("Ocean Drive gecesi"); akış ve metinler ilk açılış tasarım belgesinden (§3).

* `ChoiceCard`      Ekran 1'deki seçim kartı. Radyo düğmesi gibi davranır: oklarla gezilir ve seçilir, boşlukla
                    seçilir, ekran okuyucu adını ve açıklamasını okur. Seçili kart camgöbeği halka ve köşede onay
                    rozetiyle gösterilir (yalnız renge dayanmaz); klavye odağı ayrı bir iç halkadır.
* `StepRow`         Kurulum adımı satırı: durum işareti (bekliyor, sürüyor, bitti, hata), ad, sağda miktar ya da geçen
                    süre ve gerekirse altında kısa not (hata, ağ beklemesi, hafif modele geçiş).
* `AiConfirmPanel`  Ekran 2 (onay): model, indirilecekler ve boyutları, toplam, disk. "Kur"a basılmadan tek bayt
                    inmez; engel varsa ya da disk yetmezse "Kur" kapalıdır.
* `AiProgressPanel` Ekran 3 (ilerleme): her adımın satırı, toplam ilerleme (yüzde, indirme hızı, kalan süre),
                    duraklama ve hata satırları. `compact=True`: Çeviri sayfasındaki kısa biçim (süren adım, çubuk,
                    İptal; adımlar açılır listede).
* `confirm_ai_setup()`  Ekran 2'yi ayrı pencere olarak açar (Çeviri sayfasındaki "Önerilen kurulum").

Servisle bağ (altyazitr.ai_setup.AiSetup): `changed` ve `finished(bool, str)` sinyalleri; `state`, `step`, `substep`,
`done_bytes`/`total_bytes` (süren adımın baytları), `eta_s`, `message`, `error`, `model`, `note`, `speed`,
`active_plan`, `can_cancel`, `can_retry`, `manual_install` alanları; `start(plan)`, `retry()`, `cancel()`,
`force_resume()`. `advice()` ve `plan()` donanıma, diske ve Ollama'ya baktığı için arka planda (`SurveyThread`)
çağrılır; arayüz donmaz.

Baytsız adımlar (doğrulama, kurulum, başlatma, denetim) donmuş görünmez: satırda geçen süre saniyede bir ilerler,
toplam çubuk adımın beklenen süresine göre (hiç dolmadan) yürür. Hatada "Hızlı çeviride kal" her zaman, "Tekrar dene"
yalnız servis yeniden denemeyi anlamlı bulursa (`ai.can_retry`; ör. imza hatasında değil), Ollama'nın sitesine
bağlantı ("Ollama'yı elle kur") yalnız servis elle kurmayı önerirse (`ai.manual_install`) görünür.
"""

from __future__ import annotations

import logging
import math
import shutil
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from altyazitr import APP_NAME
from altyazitr import translate as T
from altyazitr.ai_setup import MODEL_INFO
from altyazitr.gui import icons
from altyazitr.gui.controls import Segmented
from altyazitr.gui.dialogs import brand_pixmap, font_css
from altyazitr.gui.tokens import ICON, RADIUS, SPACE, C, F
from altyazitr.gui.widgets import button_icon

if TYPE_CHECKING:
    from altyazitr.ai_setup import AiSetup, Plan
    from altyazitr.hardware import Advice

log = logging.getLogger(__name__)

# --- sabitler ve metinler -------------------------------------------------------------------------------------
HEAVY_MODEL = T.RECOMMENDED_OLLAMA_MODEL   # gemma4:12b
LIGHT_MODEL = T.LIGHT_OLLAMA_MODEL         # gemma4:e4b
MODELS = (HEAVY_MODEL, LIGHT_MODEL)
# Modellerin indirme boyutu (ondalık GB): model kataloğundan (ai_setup.MODEL_INFO)
MODEL_BYTES = {name: info.size for name, info in MODEL_INFO.items()}
MODEL_TIPS = {
    "gemma4:12b": "Daha doğru çeviri. 12 GB ekran kartı önerilir.",
    "gemma4:e4b": "Daha hızlı ve hafif, 6,6 GB. 8 GB kart ya da oyun da bu bilgisayardaysa.",
}
QUICK_BYTES = int(T.QUICKMT_TOTAL_MB * 1_000_000)
SLOW_MS = 1100            # hız testi: ortanca bundan uzunsa "yavaş" (≤600 hızlı, ≤1100 yeterli)
NOMINAL_RATE = 10_000_000  # bayt/sn: baytsız adımın toplam çubuktaki payı = beklenen süresi × bu hız
_SETUP_BYTES = 1_857_663_944   # ollama_install.SETUP_BYTES okunamazsa
_SETUP_VERSION = "0.40.16"     # ollama_install.OLLAMA_VERSION okunamazsa
_MANUAL_URL = "https://ollama.com/download"  # ollama_install.MANUAL_URL okunamazsa

CARD_AI_TITLE = "Yapay zekâ ile"
CARD_AI_TEXT = "Konuşmayı takip eder, argoyu anlar."
CARD_AI_TIP = ("Ollama (yapay zekâyı çalıştıran ücretsiz program) ve Gemma 4 modeli kurulur. İnternetsiz çalışır. "
               "Hızlı çeviri yedekte kalır: yapay zekâ gecikirse o görünür.")
CARD_QUICK_TITLE = "Yalnız hızlı çeviri"
CARD_QUICK_TEXT = "Her bilgisayarda çalışır, anında çevirir. Bazen düz ya da yanlış çevirir."
NO_GPU_TEXT = "Uygun ekran kartı yok"
NO_GPU_TIP = ("Yapay zekâ çevirisi için en az 8 GB belleği olan bir NVIDIA ya da AMD ekran kartı gerekir. "
              "Bu bilgisayarda hızlı çeviri daha iyi sonuç verir.")
INSPECTING = "Ekran kartı inceleniyor…"
RECOMMENDED = "Önerilen"
PAUSE_NOTE = "Çeviri çalışırken indirme duraklar."
PAUSE_TIP = ("Büyük indirme yayın bağlantınızı ve Wi-Fi'deki Remote Play görüntüsünü bozabilir. Durdurunca "
             "kaldığı yerden sürer.")
PAUSED = "Duraklatıldı: çeviri çalışıyor"
START_TIP = "Hızlı çeviriyle başlayın; yapay zekâ arka planda iner, hazır olunca devreye girer."
QUICK_WAIT = "Hızlı çeviri iniyor…"
CANCELLED = "İptal edildi."
INSTALL_NO_CANCEL = "Ollama kurulurken iptal edilemez; kurulum bitince durur."
MANUAL_TEXT = "Ollama'yı elle kur"
MANUAL_TIP = ("Otomatik kurulum yapılamadı. Kurulum dosyasını Ollama'nın sitesinden indirip kendiniz çalıştırın. "
              "Ollama kurulunca Önerilen kurulum yalnız modeli indirir.")
# Kurulum adımındaki hata doğrulamadan mı geldi (boyut, SHA256, imza)? Yalnız servis alt adımı (substep) bildirmezse.
_VERIFY_HINTS = ("imza", "bozuk", "özet", "sha256", "bütünlük")
_NET_HINT = "İnternet bağlantısı yok"

# Sözleşmedeki adım sırası (ai_setup.Step). Adımlar ada göre okunur.
STEP_ORDER = ("DOWNLOAD", "INSTALL", "START", "CHECK", "PULL", "TEST")
_BYTE_KEYS = ("DOWNLOAD", "PULL")

PAGE_MARGINS = (SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["l"] + 2)
DIALOG_W = 600
COMPACT_BAR_W = 340   # Çeviri sayfasının alan sütunu (translate_page.FIELD_W)
MARK = 48             # başlıktaki marka işareti


# --- biçim yardımcıları -------------------------------------------------------------------------------------------
def _num(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def fmt_size(n: float, approx: bool = False) -> str:
    """Bayt → "404 MB", "1,9 GB"; approx=True → tam sayı GB ("10 GB"). Birimler ondalıktır (MB = 10⁶)."""
    n = max(0.0, float(n or 0))
    if n < 1e9:
        return f"{n / 1e6:.0f} MB"
    gb = n / 1e9
    if approx or gb >= 100:
        return f"{int(gb + 0.5)} GB"
    return f"{_num(gb)} GB"


def fmt_pair(done: float, total: float) -> str:
    """İnen / toplam: "212 / 404 MB", "0,8 / 1,9 GB"."""
    done, total = max(0.0, float(done or 0)), max(0.0, float(total or 0))
    if total < 1e9:
        return f"{done / 1e6:.0f} / {total / 1e6:.0f} MB"
    return f"{_num(done / 1e9)} / {_num(total / 1e9)} GB"


def fmt_eta(seconds: float | None) -> str:
    """Kalan süre: "Kalan ~14 dk", "Kalan ~1 sa 5 dk"; bilinmiyorsa boş."""
    if seconds is None or seconds <= 0 or seconds != seconds:  # NaN dahil
        return ""
    if seconds < 60:
        return "Kalan 1 dk'dan az"
    minutes = int(seconds / 60 + 0.5)
    if minutes < 60:
        return f"Kalan ~{minutes} dk"
    hours, minutes = divmod(minutes, 60)
    return f"Kalan ~{hours} sa {minutes} dk" if minutes else f"Kalan ~{hours} sa"


def fmt_speed(rate: float | None) -> str:
    """İndirme hızı: "12,4 MB/sn", "850 KB/sn"; bilinmiyorsa boş."""
    if not rate or rate <= 0:
        return ""
    if rate >= 1e6:
        return f"{_num(rate / 1e6)} MB/sn"
    return f"{max(1, round(rate / 1e3))} KB/sn"


def fmt_elapsed(seconds: float) -> str:
    """Geçen süre: "0:42", "12:05", "1:02:09"."""
    s = max(0, int(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_percent(frac: float) -> str:
    return f"%{int(max(0.0, min(1.0, frac)) * 100)}"


def model_title(model: str) -> str:
    """Modelin okunur adı (translate.OLLAMA_PRESETS'teki açıklamanın '—' öncesi): "Gemma 4 12B"."""
    for name, desc in T.OLLAMA_PRESETS:
        if name == model:
            title = desc.split("—")[0]
            return title.split("(")[0].strip() or model
    return model


def _installer_info() -> tuple[int, str, str]:
    """Ollama kurulum dosyasının boyutu, sürümü ve elle kurma adresi (ollama_install yoksa sabitler)."""
    try:
        from altyazitr import ollama_install as oi

        return (int(getattr(oi, "SETUP_BYTES", _SETUP_BYTES)), str(getattr(oi, "OLLAMA_VERSION", _SETUP_VERSION)),
                str(getattr(oi, "MANUAL_URL", _MANUAL_URL)))
    except Exception:  # noqa: BLE001 - modül yüklenemedi: arayüz yine çalışsın
        return _SETUP_BYTES, _SETUP_VERSION, _MANUAL_URL


def ollama_tip() -> str:
    version = _installer_info()[1]
    return (f"Resmî kurulum dosyası (github.com/ollama/ollama, sürüm {version}). İmzası ve özeti doğrulanır. "
            "Ollama ayrı bir programdır: Windows açılınca arka planda başlar, kendini günceller; "
            f"{APP_NAME}'ı kaldırsanız da kalır.")


def step_name(step: Any) -> str:
    """Adım (ai_setup.Step, ad ya da None) → "DOWNLOAD" gibi büyük harfli ad; yoksa boş."""
    if step is None:
        return ""
    return str(getattr(step, "name", step)).split(".")[-1].upper()


def step_names(plan: Any) -> list[str]:
    return [step_name(s) for s in (getattr(plan, "steps", None) or [])]


def plan_bytes(plan: Any) -> tuple[int, int]:
    """(Ollama kurulum dosyası, model) baytları; planın adımlarından ve toplam indirmesinden çıkarılır."""
    names = step_names(plan)
    total = int(getattr(plan, "download_bytes", 0) or 0)
    ollama = min(_installer_info()[0], total) if "DOWNLOAD" in names else 0
    model = max(0, total - ollama) if "PULL" in names else 0
    if "PULL" in names and model <= 0:
        model = MODEL_BYTES.get(str(getattr(plan, "model", "") or ""), 0)
    return ollama, model


def nothing_to_download(plan: Any) -> bool:
    """Ollama da model de kurulu: Ekran 2 atlanır, yalnız ayar yapılır."""
    names = step_names(plan)
    return not int(getattr(plan, "download_bytes", 0) or 0) and "DOWNLOAD" not in names and "PULL" not in names


def error_note(error: str) -> str:
    """Hata satırının kısa metni: ilk satır; ikinci satır "Ayrıntı: …" değil de yönlendirmeyse o da ("Ollama ekran
    kartınızı kullanamıyor. Model indirilmedi. Ekran kartı sürücüsünü güncelleyip yeniden deneyin."). Ayrıntı
    ipucundadır."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    if not lines:
        return "Bilinmeyen hata."
    if len(lines) >= 2 and not lines[1].startswith("Ayrıntı"):
        return f"{lines[0]} {lines[1]}"
    return lines[0]


def can_retry(ai: Any) -> bool:
    """Hatada "Tekrar dene" sunulsun mu? Servis karar verir (ai.can_retry: ör. imza hatasında yeniden denemek
    düzeltmez); alan yoksa evet."""
    return bool(getattr(ai, "can_retry", True))


def manual_install(ai: Any) -> bool:
    """Hatada "Ollama'yı elle kur" (ollama.com) sunulsun mu? Yalnız servis elle kurmayı önerirse (ai.manual_install:
    imza, antivirüs/ilke engeli, kurulum hatası…)."""
    return bool(getattr(ai, "manual_install", False))


def disk_free(path: Any) -> int | None:
    """Yolun (yoksa var olan ilk üst klasörünün) sürücüsündeki boş alan; okunamazsa None."""
    if not path:
        return None
    try:
        p = Path(path).absolute()
        for candidate in (p, *p.parents):
            if candidate.exists():
                return int(shutil.disk_usage(candidate).free)
    except (OSError, ValueError):
        log.debug("Boş disk alanı okunamadı: %s", path, exc_info=True)
    return None


def _drive(path: Any) -> str:
    p = Path(path) if path else Path.home()
    return (p.drive or p.anchor or str(p)).upper()


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


@dataclass(frozen=True)
class Outcome:
    """Kurulumun sonucu: kind = ok | slow | failed."""

    kind: str
    title: str
    text: str
    tip: str = ""


def describe_result(ok: bool, message: str = "", speed: Any = None) -> Outcome:
    """finished(ok, message) + ai.speed → sonuç başlığı ve açıklaması (Ekran 4 ve kısa biçim).

    Hata iletisinin ilk satırı kısadır; ikinci satır "Ayrıntı: …" değilse başlık olarak ilk, açıklama olarak ikinci
    satır kullanılır ("Ollama ekran kartınızı kullanamıyor." / "Model indirilmedi. …"). Tamamı ipucundadır."""
    ms = int(getattr(speed, "median_ms", 0) or 0)
    if ok:
        if ms > SLOW_MS:
            share = getattr(speed, "gpu_share", None)
            why = ": ekran kartının belleği yetmiyor." if share is not None and share < 0.99 else "."
            return Outcome("slow", "Yapay zekâ bu bilgisayarda yavaş", f"Satır başına ~{_num(ms / 1000)} sn{why}")
        text = f"Satır başına ~{_num(ms / 1000)} sn. Hızlı çeviri yedekte." if ms else "Hızlı çeviri yedekte."
        return Outcome("ok", "Yapay zekâ hazır", text)
    lines = [ln.strip() for ln in (message or "").splitlines() if ln.strip()]
    if len(lines) >= 2 and not lines[1].startswith("Ayrıntı"):
        return Outcome("failed", lines[0].rstrip("."), lines[1], message)
    return Outcome("failed", "Yapay zekâ kurulamadı", lines[0] if lines else "Bilinmeyen hata.", message)


# --- arka planda donanım ve plan --------------------------------------------------------------------------------
class SurveyThread(QThread):
    """ai.advice() ve ai.plan(model) çağrılarını arayüzü dondurmadan yapar. Önce öneri, sonra önerilen modelin planı,
    en son öbür modelinki gelir (ilk ekran ilk ikisiyle hazır olur). Hata fırlatmaz: sorunlar sinyalle bildirilir."""

    advised = Signal(object)                    # Advice ya da None (okunamadı)
    planned = Signal(str, object, str, object)  # model, Plan ya da None, hata metni, boş disk alanı (int | None)

    def __init__(self, ai: AiSetup, models: Sequence[str] = MODELS, advice: bool = True):
        super().__init__()
        self._ai, self._models, self._want_advice = ai, tuple(models), advice

    def run(self) -> None:
        first = ""
        if self._want_advice:
            adv = None
            try:
                adv = self._ai.advice()
            except Exception:  # noqa: BLE001
                log.warning("Donanım önerisi alınamadı", exc_info=True)
            self.advised.emit(adv)
            first = str(getattr(adv, "model", "") or "")
        for model in sorted(self._models, key=lambda m: m != first):
            plan, err, free = None, "", None
            try:
                plan = self._ai.plan(model)
                free = getattr(plan, "disk_free", None)
                if free is None:
                    free = disk_free(getattr(plan, "disk_path", None))
            except Exception as e:  # noqa: BLE001
                log.warning("Kurulum planı hazırlanamadı (%s)", model, exc_info=True)
                err = str(e) or type(e).__name__
            self.planned.emit(model, plan, err, free)


# Çalışan iş parçacıkları burada tutulur: pencere kapansa da çalışırken yok edilmesinler.
_surveys: list[SurveyThread] = []


def start_survey(ai: AiSetup, on_advice: Callable[[Any], None] | None, on_plan: Callable[..., None],
                 models: Sequence[str] = MODELS) -> SurveyThread:
    """Öneri ve planları arka planda okur; sonuçlar arayüz iş parçacığında on_advice / on_plan'a gelir."""
    _surveys[:] = [t for t in _surveys if t.isRunning()]
    th = SurveyThread(ai, models, advice=on_advice is not None)
    if on_advice is not None:
        th.advised.connect(on_advice)
    th.planned.connect(on_plan)
    _surveys.append(th)
    th.start()
    return th


def surveys_running() -> bool:
    _surveys[:] = [t for t in _surveys if t.isRunning()]
    return bool(_surveys)


# --- küçük parçalar -------------------------------------------------------------------------------------------------
def _css(family: str, px: int, color: str, extra: str = "", disabled: str | None = C.TEXT_DISABLED) -> str:
    """Tek etikete yazı tipi ve renk; devre dışıyken soluk renk (etiketin kendi stili temadakini ezer).
    QSS iç boşluğu QLabel'e çerçeve kazandırır ve Qt metni yarım "x" kadar içeri iter: girinti sıfırlanır."""
    css = f"QLabel {{ {font_css(family, px, color)} background: transparent; qproperty-indent: 0; {extra} }}"
    if disabled:
        css += f" QLabel:disabled {{ color: {disabled}; }}"
    return css


def _text(text: str = "", color: str = C.TEXT_2, px: int = F.BODY_SIZE, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(_css(F.BODY, px, color))
    lb.setWordWrap(wrap)
    return lb


def _figure(text: str = "", color: str = C.TEXT_2, px: int = F.BODY_SIZE, semibold: bool = False) -> QLabel:
    """Sayılar ve durum yazısı: Bahnschrift. "İ" noktası için üstte pay; seçilebilir yapılmaz (DESIGN.md)."""
    lb = QLabel(text)
    lb.setStyleSheet(_css(F.DISPLAY_SEMIBOLD if semibold else F.DISPLAY, px, color, "padding-top: 2px;"))
    return lb


def rule() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {C.LINE}; border: none;")
    return line


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("primary")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def secondary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def link_button(text: str, url: str, tip: str = "") -> QPushButton:
    """Dış bağlantı: camgöbeği metin + "dışarı aç" ikonu; klavyeyle de açılır."""
    b = QPushButton(text)
    b.setObjectName("ghost")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    ic, sz = button_icon("external", C.CYAN)
    b.setIcon(ic)
    b.setIconSize(sz)
    b.setStyleSheet(f"QPushButton {{ color: {C.CYAN}; padding-left: {SPACE['xs']}px; "
                    f"padding-right: {SPACE['s']}px; }}")
    b.setToolTip(tip or url)
    b.setAccessibleName(text)
    b.setAccessibleDescription(url)
    b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    return b


def manual_link() -> QPushButton:
    """"Ollama'yı elle kur": yalnız otomatik kurulum imza ya da engel yüzünden yapılamazsa gösterilir."""
    b = link_button(MANUAL_TEXT, _installer_info()[2], MANUAL_TIP)
    b.setAutoDefault(False)
    return b


def _glyph(kind: str, color: str, size: int = ICON["s"]) -> QPixmap:
    """Adım işareti: "ring" (bekliyor), "ring-dot" (sürüyor) ya da gui/icons'tan bir ad. Halka, ikon setiyle aynı
    ölçüde çizilir (24 birim ızgara, r = 9.5, 1.75 çizgi); böylece bitince aynı yerde onaylı daireye dönüşür."""
    if kind not in ("ring", "ring-dot"):
        return icons.pixmap(kind, size, color)
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    p.setPen(QPen(QColor(color), icons.STROKE))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 12), icons.CIRCLE_R, icons.CIRCLE_R)
    if kind == "ring-dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(12, 12), 4.2, 4.2)
    p.end()
    return pm


class IconLine(QWidget):
    """İkon + kısa durum metni (Bahnschrift 13). Anlamı renkli ikon taşır, metin sade kalır; uzun metin kırılır."""

    KINDS = {
        "ok": ("circle-check", C.TURQUOISE),
        "warn": ("circle-alert", C.ORANGE),
        "error": ("circle-x", C.DANGER),
        "info": ("info", C.TEXT_3),
        "busy": ("download", C.CYAN),
        "work": ("ring-dot", C.CYAN),
        "wait": ("monitor", C.TEXT_3),
    }

    def __init__(self, color: str = C.TEXT_2, parent: QWidget | None = None, disabled: str = C.TEXT_DISABLED):
        super().__init__(parent)
        self._color, self._disabled = color, disabled
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["s"])
        self.icon = QLabel()
        self.icon.setFixedSize(ICON["s"], ICON["s"] + 3)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(1)
        self.text.setStyleSheet(_css(F.DISPLAY, F.BODY_SIZE, color, "padding-top: 2px;", disabled))
        h.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self.text, 1)
        self.kind = ""

    def set(self, text: str, kind: str | None = None, tip: str | None = None, color: str | None = None) -> None:
        self.text.setText(text)
        spec = self.KINDS.get(kind or "")
        if spec and (kind or "") != self.kind:  # ikon yalnız tür değişince çizilir (ilerleme sık güncellenir)
            self.icon.setPixmap(_glyph(spec[0], spec[1]))
        self.kind = kind or ""
        self.icon.setVisible(spec is not None)
        if color is not None and color != self._color:
            self._color = color
            self.text.setStyleSheet(_css(F.DISPLAY, F.BODY_SIZE, color, "padding-top: 2px;", self._disabled))
        if tip is not None:
            self.setToolTip(tip)
        self.setAccessibleName(text)


class Header(QWidget):
    """Pencere başlığı: marka işareti + başlık (Bahnschrift SemiBold 20) + isteğe bağlı tek satır açıklama.
    Sonuç ekranında başlığın solunda durum ikonu da durabilir."""

    def __init__(self, title: str, subtitle: str = "", mark: int = MARK, parent: QWidget | None = None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["l"])
        self.mark = QLabel()
        self.mark.setPixmap(brand_pixmap(mark))
        self.mark.setFixedSize(mark, mark)
        self.mark.setAccessibleName(APP_NAME)
        h.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE["xs"])
        col.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(SPACE["s"])
        self.icon = QLabel()
        self.icon.setFixedSize(ICON["m"], ICON["m"])
        self.icon.hide()
        self.title = QLabel(title)
        self.title.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, 20, C.TEXT, "padding-top: 3px;"))
        self.title.setWordWrap(True)
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title, 1)
        col.addLayout(top)
        self.sub = _text(subtitle, C.TEXT_2)
        col.addWidget(self.sub)
        col.addStretch(1)
        h.addLayout(col, 1)
        self.set(title, subtitle)

    def set(self, title: str, subtitle: str = "", icon: str | None = None, color: str = C.TEXT_2,
            tip: str = "") -> None:
        self.title.setText(title)
        self.sub.setText(subtitle)
        self.sub.setVisible(bool(subtitle))
        self.sub.setToolTip(tip)
        if icon:
            self.icon.setPixmap(icons.pixmap(icon, ICON["m"], color))
        self.icon.setVisible(bool(icon))


def page_layout(w: QWidget) -> QVBoxLayout:
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*PAGE_MARGINS)
    lay.setSpacing(0)
    return lay


def dialog_width(parent: QWidget | None = None, preferred: int = DIALOG_W) -> int:
    """~600 px; dar ekranda ekrana sığacak kadar daralır (metinler kırılır, taşma olmaz)."""
    screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
    avail = screen.availableGeometry().width() if screen is not None else preferred + 64
    return max(320, min(preferred, avail - 32))


def fit_dialog(dlg: QWidget, page: QWidget, width: int) -> None:
    """Pencereyi sayfanın içeriğine oturtur: genişlik sabit, yükseklik kırılan satırlara göre (boşluk kalmaz)."""
    lay = page.layout()
    if lay is None:
        return
    lay.activate()
    h = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
    h = max(h, lay.totalMinimumSize().height())
    if h > 0:
        dlg.setFixedSize(width, h)


# --- seçim kartı ---------------------------------------------------------------------------------------------------
class ChoiceCard(QAbstractButton):
    """Ekran 1'deki seçim kartı: başlık (+ "Önerilen" rozeti), açıklama, indirme boyutu, donanım ve uyarı satırları.

    Radyo düğmesi gibidir (checkable + autoExclusive; aynı kaptaki kartlar tek seçim): oklar seçimi taşır, boşluk
    seçer, Enter pencerenin varsayılan düğmesine (Devam) gider. Ekran okuyucu rolü RadioButton'dır; adı başlık,
    açıklaması görünen satırlardır. Devre dışı kart soluk çizilir ve seçilemez; gerekçe satırı okunur kalır.
    İç etiketler fareye saydamdır: tıklama ve ipucu kartın kendisine gelir."""

    BADGE_R = 9

    def __init__(self, title: str, text: str, size: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["l"] + 2, SPACE["l"] - 2, SPACE["l"] + 2, SPACE["l"])
        lay.setSpacing(SPACE["xs"])
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE["s"])
        self.title_label = QLabel(title)  # Bahnschrift SemiBold 16 (#section gibi); devre dışıyken soluk
        self.title_label.setStyleSheet(_css(F.DISPLAY_SEMIBOLD, F.SECTION, C.TEXT, "padding-top: 2px;"))
        self.badge = QLabel()
        self.badge.setObjectName("pill")
        self.badge.hide()
        top.addWidget(self.title_label)
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        top.addSpacing(2 * self.BADGE_R + SPACE["xs"])  # seçili kartın onay rozeti burada çizilir
        lay.addLayout(top)
        self.text_label = _text(text, C.TEXT_2)
        lay.addWidget(self.text_label)
        self.size_label = _figure(size, C.TEXT_2)
        self.size_label.setVisible(bool(size))
        lay.addWidget(self.size_label)
        self.info = IconLine(C.TEXT_2, disabled=C.TEXT_3)  # seçilemeyen kartta gerekçe okunur kalır
        self.info.hide()
        self.warn = IconLine(C.TEXT)
        self.warn.hide()
        self._gap = QWidget()
        self._gap.setFixedHeight(SPACE["xs"])
        self._gap.hide()
        lay.addWidget(self._gap)
        lay.addWidget(self.info)
        lay.addWidget(self.warn)
        for w in (self.title_label, self.badge, self.text_label, self.size_label, self.info, self.warn, self._gap):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName(title)
        self._sync()

    # --- içerik ---------------------------------------------------------------------------------------------------
    def set_badge(self, text: str) -> None:
        self.badge.setText(text)
        self.badge.setVisible(bool(text))
        self._sync()

    def set_size(self, text: str) -> None:
        self.size_label.setText(text)
        self.size_label.setVisible(bool(text))
        self._sync()

    def set_info(self, text: str, kind: str = "info") -> None:
        self.info.set(text, kind)
        self.info.setVisible(bool(text))
        self._sync()

    def set_warn(self, text: str) -> None:
        self.warn.set(text, "warn")
        self.warn.setVisible(bool(text))
        self._sync()

    def _sync(self) -> None:
        self._gap.setVisible(self.info.isVisibleTo(self) or self.warn.isVisibleTo(self))
        parts = [self.badge.text() if self.badge.isVisibleTo(self) else "", self.text_label.text(),
                 self.size_label.text() if self.size_label.isVisibleTo(self) else "",
                 self.info.text.text() if self.info.isVisibleTo(self) else "",
                 self.warn.text.text() if self.warn.isVisibleTo(self) else ""]
        self.setAccessibleDescription(". ".join(p.replace("\n", ". ").rstrip(".") for p in parts if p) + ".")
        self.update()

    # --- Qt ------------------------------------------------------------------------------------------------------
    def changeEvent(self, e) -> None:  # noqa: N802 - Qt
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = RADIUS["l"]
        on, enabled = self.isChecked(), self.isEnabled()
        hover = enabled and self.underMouse()
        if not enabled:
            fill = None  # soluk: yüzey yok, yalnız ince kenar
        elif self.isDown():
            fill = C.PRESSED
        elif hover:
            fill = C.RAISED
        else:
            fill = C.PANEL
        width = 2.0 if on else 1.0
        half = width / 2
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(half, half, -half, -half), radius - half, radius - half)
        if fill:
            p.fillPath(path, QColor(fill))
        border = C.CYAN if on else (C.LINE_STRONG if hover else C.LINE)
        p.setPen(QPen(QColor(border), width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if on:  # seçili: camgöbeği halka + köşede onay rozeti (yalnız renge dayanmasın)
            cy = self.title_label.geometry().center().y() + 1
            c = QPointF(rect.right() - SPACE["l"] - 2 - self.BADGE_R, cy)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.CYAN))
            p.drawEllipse(c, self.BADGE_R, self.BADGE_R)
            s = 12
            p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2, s, s).toRect(), icons.pixmap("check", s, C.INVERSE))
        if self.hasFocus() and bool(self.property("kbfocus")):  # klavye odağı: içte ayrı halka
            p.setPen(QPen(QColor(C.CYAN), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(4.5, 4.5, -4.5, -4.5), radius - 4, radius - 4)
        p.end()


# --- adım satırı ---------------------------------------------------------------------------------------------------
class StepRow(QWidget):
    """Kurulum adımı: durum işareti + ad + sağda miktar ya da geçen süre; gerekirse altında kısa not.

    Durumlar: pending (boş halka), active (camgöbeği; indirme satırında indirme ikonu), paused, done (turkuaz onay),
    failed (kırmızı çarpı), cancelled. Bitince ad geçmiş zamana döner ("Ollama indirildi"). Not satırı hata metnini,
    ağ beklemesini ya da hafif modele geçişi kısa yazar; ayrıntı ipucundadır."""

    _LOOK = {  # durum → (işaret, işaret rengi, ad rengi, miktar rengi)
        "pending": ("ring", C.TEXT_DISABLED, C.TEXT_3, C.TEXT_3),
        "active": ("ring-dot", C.CYAN, C.TEXT, C.TEXT_2),
        "paused": ("ring-dot", C.TEXT_3, C.TEXT_2, C.TEXT_3),
        "done": ("circle-check", C.TURQUOISE, C.TEXT_2, C.TEXT_3),
        "failed": ("circle-x", C.DANGER, C.TEXT, C.TEXT_2),
        "cancelled": ("ring", C.TEXT_DISABLED, C.TEXT_3, C.TEXT_3),
    }
    _SPOKEN = {"pending": "bekliyor", "active": "sürüyor", "paused": "duraklatıldı", "done": "bitti",
               "failed": "hata", "cancelled": "iptal edildi"}

    def __init__(self, title: str, done_title: str | None = None, download: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._title, self._done_title, self._download = title, done_title or title, download
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["m"])
        self.icon = QLabel()
        self.icon.setFixedSize(ICON["s"], ICON["s"])
        self.label = QLabel(title)
        self.label.setMinimumWidth(1)
        self.detail = QLabel()
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(h)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setContentsMargins(ICON["s"] + SPACE["m"], 0, 0, 0)  # adın hizasında
        self.note.hide()
        self._note_color = ""
        v.addWidget(self.note)
        self.state = ""
        self.set_state("pending")

    def set_title(self, title: str, done_title: str | None = None) -> None:
        self._title, self._done_title = title, done_title or title
        self.label.setText(self._done_title if self.state == "done" else self._title)

    def set_state(self, state: str, detail: str | None = None) -> None:
        if state not in self._LOOK:
            state = "pending"
        if state != self.state:
            self.state = state
            glyph, gcol, tcol, dcol = self._LOOK[state]
            if state in ("active", "paused") and self._download:
                glyph = "download"
            self.icon.setPixmap(_glyph(glyph, gcol))
            self.label.setStyleSheet(_css(F.BODY, F.BODY_SIZE, tcol))
            self.detail.setStyleSheet(_css(F.DISPLAY, F.BODY_SIZE, dcol, "padding-top: 2px;"))
            self.label.setText(self._done_title if state == "done" else self._title)
        if detail is not None:
            self.detail.setText(detail)
        self._speak()

    def set_note(self, text: str, color: str = C.TEXT_2, tip: str = "") -> bool:
        """Adın altındaki kısa not. Görünürlüğü değiştiyse True (pencere yeniden oturtulsun)."""
        if color != self._note_color:
            self._note_color = color
            self.note.setStyleSheet(_css(F.BODY, F.SMALL, color))
        changed = self.note.isVisibleTo(self) != bool(text) or (text and self.note.text() != text)
        self.note.setText(text)
        self.note.setToolTip(tip)
        self.note.setVisible(bool(text))
        self.setToolTip(tip)
        self._speak()
        return bool(changed)

    def _speak(self) -> None:
        parts = [self.label.text(), self._SPOKEN.get(self.state, ""), self.detail.text(), self.note.text()]
        self.setAccessibleName(", ".join(p for p in parts if p))


# --- Ekran 2: onay ---------------------------------------------------------------------------------------------------
class AiConfirmPanel(QWidget):
    """Ekran 2: yapay zekâ kurulumunun onayı. Model segmenti (kademeye göre seçili), Ollama ve model satırları
    boyutlarıyla, toplam indirme, disk satırı ve Ollama bilgisi. Boyutlar `ai.plan(model)` sonucundan gelir
    (`set_plan`). Engel varsa ya da diskte yer yetmiyorsa "Kur" kapalıdır. "Kur" → `install(plan)`."""

    install = Signal(object)  # Plan
    back = Signal()
    relayout = Signal()       # içerik yüksekliği değişti (pencere yeniden oturtulsun)

    def __init__(self, back_text: str = "Geri", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")
        self._model = HEAVY_MODEL
        self._model_picked = False
        self._plans: dict[str, tuple[Any, str, int | None]] = {}
        lay = page_layout(self)
        self.header = Header("Yapay zekâ kurulumu",
                             f"{APP_NAME} bunları indirip kurar; sizin bir şey yapmanız gerekmez.")
        lay.addWidget(self.header)
        lay.addSpacing(SPACE["xl"])

        # model seçimi
        mrow = QHBoxLayout()
        mrow.setSpacing(SPACE["m"])
        mlabel = _text("Model", C.TEXT_2, wrap=False)
        self.models = Segmented([(model_title(m), m) for m in MODELS])
        for i, m in enumerate(MODELS):
            self.models.group.button(i).setToolTip(MODEL_TIPS.get(m, ""))
        self.models.setAccessibleName("Model")
        self.models.changed.connect(self._model_clicked)
        mrow.addWidget(mlabel, 0, Qt.AlignmentFlag.AlignVCenter)
        mrow.addWidget(self.models, 1)
        lay.addLayout(mrow)
        lay.addSpacing(SPACE["l"] + 2)

        # indirilecekler: ikon | ad | açıklama | boyut
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE["m"])
        grid.setVerticalSpacing(SPACE["m"])
        grid.setColumnStretch(2, 1)
        self.ollama_row = self._item(grid, 0, "Ollama", ollama_tip())
        self.model_row = self._item(grid, 1, model_title(self._model), MODEL_TIPS.get(self._model, ""))
        grid.addWidget(rule(), 2, 1, 1, 3)
        self.total_label = _text("Toplam indirme", C.TEXT_2, wrap=False)
        self.total = _figure("", C.TEXT, F.BUTTON, semibold=True)
        self.total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.total_label, 3, 1, 1, 2)
        grid.addWidget(self.total, 3, 3)
        lay.addLayout(grid)
        lay.addSpacing(SPACE["l"] + 2)

        # disk, Ollama bilgisi, engel
        self.disk = IconLine()
        self.info = IconLine()
        self.info.set("Ollama ayrı bir programdır: Windows açılınca arka planda başlar, kendini günceller.", "info",
                      tip=ollama_tip())
        self.blocker = IconLine(C.TEXT)
        self.blocker.hide()
        notes = QVBoxLayout()
        notes.setSpacing(SPACE["s"])
        for w in (self.blocker, self.disk, self.info):
            notes.addWidget(w)
        lay.addLayout(notes)
        lay.addSpacing(SPACE["xl"])

        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        btns.addStretch(1)
        self.back_btn = secondary_button(back_text)
        self.back_btn.setAutoDefault(False)
        self.back_btn.clicked.connect(self.back)
        self.install_btn = primary_button("Kur")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._install)
        btns.addWidget(self.back_btn)
        btns.addWidget(self.install_btn)
        lay.addLayout(btns)
        self.models.setValue(self._model)
        self._refresh()

    @staticmethod
    def _item(grid: QGridLayout, row: int, name: str, tip: str) -> dict[str, QLabel]:
        icon = QLabel()
        icon.setFixedSize(ICON["s"], ICON["s"])
        title = _text(name, C.TEXT, wrap=False)
        desc = _text("", C.TEXT_2)
        size = _figure("", C.TEXT_2, F.BUTTON)
        size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for w in (title, desc, size):
            w.setToolTip(tip)
        grid.addWidget(icon, row, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(title, row, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(desc, row, 2, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(size, row, 3, Qt.AlignmentFlag.AlignVCenter)
        return {"icon": icon, "title": title, "desc": desc, "size": size}

    # --- veri ---------------------------------------------------------------------------------------------------
    @property
    def model(self) -> str:
        return self._model

    @property
    def plan(self) -> Plan | None:
        entry = self._plans.get(self._model)
        return entry[0] if entry else None

    def has_plan(self, model: str | None = None) -> bool:
        """Modelin planı (ya da planlama hatası) geldi mi?"""
        return (model or self._model) in self._plans

    def plan_for(self, model: str) -> Plan | None:
        entry = self._plans.get(model)
        return entry[0] if entry else None

    def forget(self, model: str) -> None:
        """Modelin planını unutur (yeniden okunacak); gelene dek "Kur" kapalıdır."""
        self._plans.pop(model, None)
        if model == self._model:
            self._refresh()

    def set_advice(self, advice: Advice | None) -> None:
        """Kademeye göre model seçer (kullanıcı segmente dokunduysa seçimi ezilmez)."""
        model = getattr(advice, "model", None)
        if model in MODELS and not self._model_picked:
            self.select_model(model, user=False)

    def set_plan(self, model: str, plan: Plan | None, error: str = "", free: int | None = None) -> None:
        self._plans[model] = (plan, error, free)
        if model == self._model:
            self._refresh()

    def select_model(self, model: str, user: bool = True) -> None:
        if model not in MODELS:
            return
        self._model_picked |= user
        self._model = model
        self.models.setValue(model)
        self._refresh()

    def _model_clicked(self, model: object) -> None:
        self.select_model(str(model), user=True)

    def ready(self) -> bool:
        return self.install_btn.isEnabled()

    def _install(self) -> None:
        plan = self.plan
        if plan is not None and self.ready():
            self.install.emit(plan)

    # --- görünüm ------------------------------------------------------------------------------------------------
    def _refresh(self) -> None:
        before = (self.blocker.isVisibleTo(self), self.info.isVisibleTo(self), self.disk.isVisibleTo(self),
                  self.blocker.text.text(), self.disk.text.text())
        m = self._model
        o, mr = self.ollama_row, self.model_row
        mr["title"].setText(model_title(m))
        for w in (mr["title"], mr["desc"], mr["size"]):
            w.setToolTip(MODEL_TIPS.get(m, ""))
        entry = self._plans.get(m)
        blocker = ""
        ok_disk = False
        if entry is None:  # plan henüz gelmedi
            for row, desc in ((o, "Yapay zekâyı bilgisayarınızda çalıştırır"), (mr, "Çeviriyi yapan model")):
                row["icon"].setPixmap(icons.pixmap("download", ICON["s"], C.TEXT_3))
                row["desc"].setText(desc)
                row["size"].setText("")
            self.total.setText("")
            self.disk.set("Disk alanına bakılıyor…", None, color=C.TEXT_3)
            self.disk.show()
            self.info.show()
        else:
            plan, error, free = entry
            if plan is None:
                blocker = "Kurulum hazırlanamadı."
                self.blocker.set(blocker, "warn", tip=error)
                for row in (o, mr):
                    row["size"].setText("")
                self.total.setText("")
                self.disk.hide()
                self.info.hide()
            else:
                names = step_names(plan)
                ollama_b, model_b = plan_bytes(plan)
                install_ollama = "DOWNLOAD" in names or "INSTALL" in names
                old = str(getattr(plan, "ollama_version", "") or "") if install_ollama else ""
                if install_ollama:
                    o["icon"].setPixmap(icons.pixmap("download", ICON["s"], C.TEXT_2))
                    o["desc"].setText(f"{old} kurulu, güncellenecek" if old
                                      else "Yapay zekâyı bilgisayarınızda çalıştırır")
                    o["size"].setText(fmt_size(ollama_b) if ollama_b else "")
                else:
                    o["icon"].setPixmap(icons.pixmap("circle-check", ICON["s"], C.TURQUOISE))
                    o["desc"].setText("Yapay zekâyı bilgisayarınızda çalıştırır")
                    o["size"].setText("Kurulu")
                if "PULL" in names:
                    mr["icon"].setPixmap(icons.pixmap("download", ICON["s"], C.TEXT_2))
                    mr["size"].setText(fmt_size(model_b))
                else:
                    mr["icon"].setPixmap(icons.pixmap("circle-check", ICON["s"], C.TURQUOISE))
                    mr["size"].setText("Kurulu")
                mr["desc"].setText("Çeviriyi yapan model")
                total = int(getattr(plan, "download_bytes", 0) or 0)
                self.total.setText(f"~{fmt_size(total, approx=True)}" if total else "İndirme yok")
                self.info.setVisible(install_ollama)
                blocker = str(getattr(plan, "blocker", "") or "")
                need = int(getattr(plan, "disk_bytes", 0) or 0)
                drive = _drive(getattr(plan, "disk_path", None))
                short = free is not None and need > 0 and free < need
                if free is None or not need:
                    self.disk.hide()
                    ok_disk = True
                elif short:
                    self.disk.set(f"{drive} sürücüsünde yer yetmiyor: {fmt_size(free, approx=True)} boş, "
                                  f"~{fmt_size(need, approx=True)} gerekli", "warn", color=C.TEXT,
                                  tip=f"Kurulum için {drive} sürücüsünde yer açın.")
                    self.disk.show()
                else:
                    self.disk.set(f"{drive} sürücüsünde {fmt_size(free, approx=True)} boş "
                                  f"(~{fmt_size(need, approx=True)} gerekli)", "ok", color=C.TEXT_2,
                                  tip=str(getattr(plan, "disk_path", "") or ""))
                    self.disk.show()
                    ok_disk = True
                if blocker:
                    self.blocker.set(_first_line(blocker), "warn", tip=blocker)
                # planın disk engeli disk satırında zaten yazıyor: iki kez gösterilmez
                show_blocker = bool(blocker) and not (short and f"{drive} sürücüsünde yer yetmiyor" in blocker)
                self.blocker.setVisible(show_blocker)
        if entry is None or entry[0] is None:
            self.blocker.setVisible(bool(blocker))
        self.install_btn.setEnabled(entry is not None and entry[0] is not None and not blocker and ok_disk)
        after = (self.blocker.isVisibleTo(self), self.info.isVisibleTo(self), self.disk.isVisibleTo(self),
                 self.blocker.text.text(), self.disk.text.text())
        if after != before:
            self.relayout.emit()


# --- Ekran 3: ilerleme ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RowDef:
    """İlerleme listesindeki bir satır. `step` satırı yürüten servis adımıdır (VERIFY ve INSTALL ikisi de INSTALL
    adımının parçasıdır; READY sondaki "Hazır" satırıdır). `expect_s` baytsız adımın beklenen süresidir: satır
    sürerken toplam çubuk buna göre ilerler ve kalan süreye eklenir."""

    key: str
    step: str
    active: str
    done: str
    expect_s: float = 0.0


def row_defs(steps: Sequence[str], model: str = "", updating: bool = False) -> list[RowDef]:
    """Planın adımlarından ilerleme satırları (sonda "Hazır")."""
    title = model_title(model) or "Model"
    out: list[RowDef] = []
    for s in steps:
        if s == "DOWNLOAD":
            out.append(RowDef("DOWNLOAD", s, "Ollama indiriliyor", "Ollama indirildi"))
        elif s == "INSTALL":
            out.append(RowDef("VERIFY", s, "İmza ve bütünlük doğrulanıyor", "İmza ve bütünlük doğrulandı", 10))
            if updating:
                out.append(RowDef("INSTALL", s, "Ollama güncelleniyor (1–2 dk)", "Ollama güncellendi", 90))
            else:
                out.append(RowDef("INSTALL", s, "Ollama kuruluyor (1–2 dk)", "Ollama kuruldu", 90))
        elif s == "START":
            out.append(RowDef("START", s, "Ollama başlatılıyor", "Ollama başlatıldı", 15))
        elif s == "CHECK":
            out.append(RowDef("CHECK", s, "Ekran kartı denetleniyor", "Ekran kartı denetlendi", 5))
        elif s == "PULL":
            out.append(RowDef("PULL", s, f"{title} indiriliyor", f"{title} indirildi"))
        elif s == "TEST":
            out.append(RowDef("TEST", s, "Hız testi", "Hız testi yapıldı", 20))
    if out:
        out.append(RowDef("READY", "", "Hazır", "Hazır"))
    return out


@dataclass
class _Row:
    d: RowDef
    widget: StepRow
    size: int = 0            # indirilecek bayt (indirme satırı değilse 0)
    done: int = 0            # şu ana dek inen bayt
    state: str = "pending"
    title: str = ""          # şu anki adı ("Ollama indiriliyor" / "Ollama indirildi")
    detail: str = ""


class _Rate:
    """Son 10 sn'nin ortalama indirme hızı (bayt/sn)."""

    WINDOW = 10.0

    def __init__(self) -> None:
        self._s: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self._s.clear()

    def add(self, done: float, now: float) -> None:
        if self._s and done < self._s[-1][1]:
            self._s.clear()  # baştan başladı (iptal, tekrar)
        if self._s and now - self._s[-1][0] < 0.2:
            self._s[-1] = (self._s[-1][0], done)  # sık bildirim: son örneği güncelle
        else:
            self._s.append((now, done))
        while len(self._s) > 2 and now - self._s[1][0] >= self.WINDOW:
            self._s.popleft()

    def speed(self) -> float | None:
        if len(self._s) < 2:
            return None
        (t0, b0), (t1, b1) = self._s[0], self._s[-1]
        if t1 - t0 < 1.0 or b1 <= b0:
            return None
        return (b1 - b0) / (t1 - t0)


class AiProgressPanel(QWidget):
    """Ekran 3: kurulumun her adımı kendi satırında, toplam ilerleme (yüzde, indirme hızı, kalan süre), duraklama ve
    hata satırları; `compact=True` → Çeviri sayfasındaki kısa biçim (süren adım, çubuk, İptal; adımlar açılır).

    Panel işin arayüz tarafını da yönetir: `start(plan, wait=True)` işi hızlı çeviri modeli inene dek bekletir (bant
    bölünmesin), `set_quickmt("done")` gelince `ai.start(plan)` çağrılır. İptal: bekleyen iş hiç başlamaz, süren iş
    `ai.cancel()` ile durur ("Ollama kuruluyor" adımında kapalı). Hata: hatalı satır kırmızı, altında kısa metin
    (ayrıntı ipucunda), her zaman "Hızlı çeviride kal"; "Tekrar dene" (`ai.retry()`) yalnız `ai.can_retry` ise,
    "Ollama'yı elle kur" yalnız `ai.manual_install` ise. Sinyaller: `start_clicked` ("Şimdi başla"/"Başla"; hızlı
    çeviri inene dek kapalı), `back_clicked` (yalnız hızlı çeviri inerken "Geri"), `retry_quick` (hızlı çeviri modeli
    inemedi), `stay_quick` ("Hızlı çeviride kal"), `cancelled`, `relayout` (içerik yüksekliği değişti)."""

    start_clicked = Signal()
    back_clicked = Signal()
    retry_quick = Signal()
    stay_quick = Signal()
    cancelled = Signal()
    relayout = Signal()

    def __init__(self, ai: AiSetup, compact: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.ai = ai
        self.compact = compact
        self._clock: Callable[[], float] = time.monotonic  # testler sahte saat verebilir
        self._plan: Any = None
        self._steps: list[str] = []      # gösterilen adımlar (Tekrar dene'de yapılmış olanlar da kalır)
        self._seen_plan: Any = None      # satırların dayandığı son plan (servisin active_plan'ı)
        self._merge_next = False         # Tekrar dene: yeni planın adımları eskilerle birleşir
        self._row_sig: tuple = ()
        self._queued = False
        self._cancelling = False
        self._user_cancelled = False
        self._cur = 0
        self._quick = "hidden"
        self._quick_frac = 0.0
        self._quick_error = ""
        self._back = False
        self._rows: list[_Row] = []
        self._since: dict[str, float] = {}   # satır → sürmeye başladığı an (geçen süre)
        self._rate = _Rate()
        self._last_rate: float | None = None
        if compact:
            self._build_compact()
        else:
            self.setObjectName("page")
            self._build_full()
        self._ticker = QTimer(self, interval=1000, timeout=self._refresh)  # geçen süre ve çubuk saniyede bir
        ai.changed.connect(self._refresh)
        ai.finished.connect(self._on_finished)
        self._refresh()

    # --- kurulum --------------------------------------------------------------------------------------------------
    def _build_full(self) -> None:
        lay = page_layout(self)
        self.header = Header("Kuruluyor")
        lay.addWidget(self.header)
        lay.addSpacing(SPACE["xl"] - 2)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(SPACE["s"] + 2)
        lay.addLayout(self.rows_box)
        lay.addSpacing(SPACE["xl"] - 2)
        self.bar = self._make_bar("Kurulum ilerlemesi")
        lay.addWidget(self.bar)
        lay.addSpacing(SPACE["s"])
        self.pct, self.speed, self.eta = (_figure("", C.TEXT_2) for _ in range(3))
        meta = QHBoxLayout()
        meta.setSpacing(SPACE["l"])
        meta.addWidget(self.pct)
        meta.addStretch(1)
        meta.addWidget(self.speed)
        meta.addWidget(self.eta)
        lay.addLayout(meta)
        lay.addSpacing(SPACE["m"])
        note = QHBoxLayout()
        note.setContentsMargins(0, 0, 0, 0)
        note.setSpacing(SPACE["s"])
        self.note = IconLine()
        self.resume_btn = secondary_button("Yine de sürdür")
        self.resume_btn.setObjectName("ghost")
        self.resume_btn.setAutoDefault(False)
        self.resume_btn.setToolTip("İndirme çeviri çalışırken de sürer")
        self.resume_btn.clicked.connect(self._force_resume)
        note.addWidget(self.note, 1)
        note.addWidget(self.resume_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.note_box = QWidget()
        self.note_box.setLayout(note)
        lay.addWidget(self.note_box)
        # "Ollama'yı elle kur": yalnız imza ya da antivirüs/ilke engelinde (otomatik kurulumun yedeği)
        self.link_btn = manual_link()
        link = QHBoxLayout()
        link.setContentsMargins(0, 0, 0, 0)
        link.addWidget(self.link_btn)
        link.addStretch(1)
        self.link_box = QWidget()
        self.link_box.setLayout(link)
        lay.addWidget(self.link_box)
        lay.addSpacing(SPACE["xl"])
        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        self.cancel_btn = secondary_button("Yapay zekâyı iptal et")
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.stay_btn = secondary_button("Hızlı çeviride kal")
        self.stay_btn.setAutoDefault(False)
        self.stay_btn.clicked.connect(self._stay)
        self.back_btn = secondary_button("Geri")
        self.back_btn.setAutoDefault(False)
        self.back_btn.clicked.connect(self.back_clicked)
        self.wait_label = _figure(QUICK_WAIT, C.TEXT_3)  # "Şimdi başla" neden kapalı
        self.retry_quick_btn = primary_button("Tekrar dene")
        self.retry_quick_btn.clicked.connect(self.retry_quick)
        self.retry_btn = primary_button("Tekrar dene")
        self.retry_btn.clicked.connect(self.retry)
        self.start_btn = primary_button("Şimdi başla")
        self.start_btn.clicked.connect(self.start_clicked)
        for w in (self.cancel_btn, self.stay_btn, self.back_btn):
            btns.addWidget(w)
        btns.addStretch(1)
        btns.addWidget(self.wait_label, 0, Qt.AlignmentFlag.AlignVCenter)
        btns.addSpacing(SPACE["xs"])
        for w in (self.retry_quick_btn, self.retry_btn, self.start_btn):
            btns.addWidget(w)
        lay.addLayout(btns)

    def _build_compact(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["s"])
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE["s"])
        self.line_icon = QLabel()
        self.line_icon.setFixedSize(ICON["s"], ICON["s"])
        self.line_text = QLabel()
        self.line_text.setMinimumWidth(1)  # dar sayfada kırpılır; tamamı ipucunda
        self.line_text.setStyleSheet(_css(F.DISPLAY, F.BODY_SIZE, C.TEXT_2, "padding-top: 2px;"))
        self.line_detail = _figure("", C.TEXT_3)
        head.addWidget(self.line_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.line_text, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(SPACE["xs"])
        head.addWidget(self.line_detail, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._line_kind = ""
        lay.addLayout(head)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["s"])
        self.bar = self._make_bar("Yapay zekâ kurulumu")
        self.bar.setFixedWidth(COMPACT_BAR_W)
        self.resume_btn = secondary_button("Yine de sürdür")
        self.resume_btn.setToolTip("İndirme çeviri çalışırken de sürer")
        self.resume_btn.clicked.connect(self._force_resume)
        self.cancel_btn = secondary_button("İptal")
        ic, sz = button_icon("x", C.TEXT_2)
        self.cancel_btn.setIcon(ic)
        self.cancel_btn.setIconSize(sz)
        self.cancel_btn.clicked.connect(self.cancel)
        self.retry_btn = secondary_button("Tekrar dene")
        ic, sz = button_icon("refresh", C.TEXT_2)
        self.retry_btn.setIcon(ic)
        self.retry_btn.setIconSize(sz)
        self.retry_btn.clicked.connect(self.retry)
        self.stay_btn = secondary_button("Hızlı çeviride kal")
        self.stay_btn.setObjectName("ghost")
        self.stay_btn.clicked.connect(self._stay)
        self.link_btn = manual_link()
        row.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        for w in (self.resume_btn, self.cancel_btn, self.retry_btn, self.stay_btn, self.link_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(SPACE["l"])
        self.pct, self.speed, self.eta = (_figure("", C.TEXT_3, F.SMALL) for _ in range(3))
        for w in (self.pct, self.speed, self.eta):
            meta.addWidget(w)
        meta.addStretch(1)
        self.steps_btn = QPushButton("Adımlar")
        self.steps_btn.setObjectName("collapse")
        self.steps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.steps_btn.setCheckable(True)
        self.steps_btn.toggled.connect(self._toggle_steps)
        meta.addWidget(self.steps_btn)
        self.meta_box = QWidget()
        self.meta_box.setLayout(meta)
        lay.addWidget(self.meta_box)
        self.steps_box = QWidget()
        self.rows_box = QVBoxLayout(self.steps_box)
        self.rows_box.setContentsMargins(0, SPACE["xs"], 0, 0)
        self.rows_box.setSpacing(SPACE["s"])
        self.steps_box.hide()
        lay.addWidget(self.steps_box)
        self._toggle_steps(False)

    @staticmethod
    def _make_bar(name: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setAccessibleName(name)
        return bar

    def _toggle_steps(self, on: bool) -> None:
        ic, sz = button_icon("chevron-down" if on else "chevron-right", C.TEXT_2, ICON["s"], gap=2)
        self.steps_btn.setIcon(ic)
        self.steps_btn.setIconSize(sz)
        self.steps_btn.setAccessibleDescription("Açık" if on else "Kapalı")
        self.steps_box.setVisible(on and bool(self._rows))
        self.relayout.emit()

    # --- dış arayüz -------------------------------------------------------------------------------------------------
    @property
    def plan(self) -> Plan | None:
        return self._plan

    @property
    def queued(self) -> bool:
        return self._queued

    @property
    def user_cancelled(self) -> bool:
        return self._user_cancelled

    def rows(self) -> list[_Row]:
        """Görünen satırlar (sırayla; testler ve ekran görüntüsü araçları için)."""
        return list(self._rows)

    def _display_plan(self) -> Any:
        return self._plan if self._plan is not None else getattr(self.ai, "active_plan", None)

    def phase(self) -> str:
        """quick (yalnız hızlı çeviri) | queued | running | paused | done | failed | cancelled | idle"""
        if self._queued:
            return "queued"
        if self._plan is None and not self.compact:
            return "quick"
        state = str(getattr(self.ai, "state", "") or "")
        if state in ("running", "paused", "done", "failed"):
            return state
        if self._user_cancelled or str(getattr(self.ai, "message", "") or "") == CANCELLED:
            return "cancelled"
        return "idle"

    def start(self, plan: Plan, wait: bool = False) -> None:
        """İşi başlatır; wait=True ise hızlı çeviri modeli inene dek bekletir."""
        self._plan = plan
        self._steps = step_names(plan)
        self._seen_plan = None
        self._merge_next = False
        self._queued = bool(wait)
        self._reset_run()
        if not self._queued:
            self.ai.start(plan)
        self._refresh()

    def retry(self) -> None:
        """"Tekrar dene": servis işi bugünkü duruma göre yeniden planlar; yapılmış adımlar atlanır (satırları
        "bitti" kalır). Hatalı satır yeniden "sürüyor"a döner."""
        fn = getattr(self.ai, "retry", None)
        plan = self._display_plan()
        self._reset_run(keep_row=True)
        self._merge_next = True
        if callable(fn):
            fn()
        elif plan is not None:
            self.ai.start(plan)
        self._refresh()

    def show_quick_only(self, back: bool = True) -> None:
        """Yalnız hızlı çeviri modeli satırı (Ekran 1'de "Yalnız hızlı çeviri" seçildi, model henüz inmedi)."""
        self._plan = None
        self._steps = []
        self._queued = False
        self._back = back
        self._reset_run()
        self._refresh()

    def set_quickmt(self, state: str, frac: float = 0.0, error: str = "") -> None:
        """Hızlı çeviri modelinin durumu: hidden | active | done | failed. "done" gelince bekleyen iş başlar."""
        self._quick, self._quick_frac, self._quick_error = state, max(0.0, min(1.0, frac)), error
        if state == "done" and self._queued and self._plan is not None:
            self._queued = False
            self.ai.start(self._plan)
        self._refresh()

    def cancel(self) -> None:
        """"Yapay zekâyı iptal et" / "İptal"."""
        if self._queued:
            self._queued = False
            self._user_cancelled = True
        elif self.phase() in ("running", "paused"):
            self._user_cancelled = True
            self._cancelling = True
            self.ai.cancel()
        else:
            return
        self.cancelled.emit()
        self._refresh()

    def _stay(self) -> None:
        """"Hızlı çeviride kal": hatalı işten vazgeçilir (servis yarım işi ve hatayı unutur)."""
        if self.phase() == "failed":
            self.ai.cancel()
        self.stay_quick.emit()
        self._refresh()

    def _force_resume(self) -> None:
        fn = getattr(self.ai, "force_resume", None)
        if callable(fn):
            fn()

    def _on_finished(self, ok: bool, message: str) -> None:
        self._cancelling = False
        self._refresh()

    def _reset_run(self, keep_row: bool = False) -> None:
        self._cancelling = False
        self._user_cancelled = False
        if not keep_row:
            self._cur = 0
        self._since.clear()
        self._rate.reset()

    # --- satırlar ---------------------------------------------------------------------------------------------------
    def _sync_rows(self) -> None:
        """Satırları gösterilecek adımlara göre kurar (değiştiyse). Tekrar dene'de servis yeni plan yapar ve yapılmış
        adımları atlar; o satırlar listede "bitti" olarak kalır. Kısa biçimde başka bir iş başlarsa liste yenilenir."""
        use_ai = self.compact or self._plan is not None
        active = getattr(self.ai, "active_plan", None) if use_ai and not self._queued else None
        plan = active if active is not None else (self._plan if use_ai else None)
        if not use_ai:
            self._steps = []
        elif plan is not None and plan is not self._seen_plan:
            if self._merge_next or plan is self._plan or not self._steps:
                known = set(self._steps) | set(step_names(plan))
                self._steps = [s for s in STEP_ORDER if s in known]
            else:
                self._steps = step_names(plan)
            self._seen_plan = plan
            self._merge_next = False
        updating = bool(getattr(plan, "updating", False))
        quick = self._quick != "hidden" and not self.compact
        sig = (quick, tuple(self._steps), updating)
        if sig == self._row_sig:
            return
        self._row_sig = sig
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        rows: list[_Row] = []
        if quick:
            rows.append(_Row(RowDef("QUICK", "QUICK", "Hızlı çeviri modeli", "Hızlı çeviri modeli"),
                             StepRow("Hızlı çeviri modeli", download=True), size=QUICK_BYTES,
                             title="Hızlı çeviri modeli"))
        model = str(getattr(self.ai, "model", "") or getattr(plan, "model", "") or "")
        ollama_b, model_b = plan_bytes(plan) if plan is not None else (0, 0)
        for d in row_defs(self._steps, model, updating):
            size = ollama_b if d.key == "DOWNLOAD" else (model_b if d.key == "PULL" else 0)
            if d.key == "DOWNLOAD" and not size:
                size = _installer_info()[0]
            rows.append(_Row(d, StepRow(d.active, d.done, download=d.key in _BYTE_KEYS), size=size, title=d.active))
        self._rows = rows
        for r in rows:
            self.rows_box.addWidget(r.widget)
        self.relayout.emit()

    def _ai_rows(self) -> list[_Row]:
        return [r for r in self._rows if r.d.key != "QUICK"]

    def _verifying(self) -> bool:
        """Kurulum adımında doğrulama mı sürüyor? Servis bunu `substep` ya da "İmza…" iletisiyle bildirebilir;
        bildirmezse kurulum adımı başlayınca doğrulama bitmiş sayılır."""
        sub = str(getattr(self.ai, "substep", "") or "").lower()
        if sub:
            return sub.startswith(("verif", "doğrula"))
        return str(getattr(self.ai, "message", "") or "").startswith("İmza")

    def _row_key(self, step: str, phase: str) -> str:
        """INSTALL adımının satırı: doğrulama mı kurulum mu. Servisin alt adımı (`substep`) hatada da korunur; servis
        bildirmediyse hata metninden çıkarılır."""
        if step != "INSTALL":
            return step
        if phase == "failed" and not str(getattr(self.ai, "substep", "") or ""):
            err = str(getattr(self.ai, "error", "") or "").lower()
            return "VERIFY" if any(h in _first_line(err) for h in _VERIFY_HINTS) else "INSTALL"
        return "VERIFY" if self._verifying() else "INSTALL"

    def _snapshot(self, phase: str, now: float) -> None:
        """Satırların durumu, adı, miktarı ve notu."""
        rows = self._ai_rows()
        step = step_name(getattr(self.ai, "step", None))
        if phase in ("running", "paused", "failed") and step:
            key = self._row_key(step, phase)
            idx = next((i for i, r in enumerate(rows) if r.d.key == key), None)
            if idx is not None:
                self._cur = idx
        model = str(getattr(self.ai, "model", "") or getattr(self._display_plan(), "model", "") or "")
        title = model_title(model) or "Model"
        cur_total = int(getattr(self.ai, "total_bytes", 0) or 0)
        cur_done = int(getattr(self.ai, "done_bytes", 0) or 0)
        message = str(getattr(self.ai, "message", "") or "")
        error = str(getattr(self.ai, "error", "") or "")
        note_text = str(getattr(self.ai, "note", "") or "")
        relayout = False
        for i, r in enumerate(rows):
            if r.d.key == "READY":
                state = "done" if phase == "done" else ("cancelled" if phase == "cancelled" else "pending")
            elif phase == "done":
                state = "done"
            elif phase in ("queued", "idle"):
                state = "pending"
            elif phase == "failed":
                state = "done" if i < self._cur else ("failed" if i == self._cur else "pending")
            elif phase == "cancelled":
                state = "done" if i < self._cur else "cancelled"
            else:
                state = "done" if i < self._cur else ((phase if phase == "paused" else "active")
                                                       if i == self._cur else "pending")
            r.state = state
            if r.d.key == "PULL":
                active, done = f"{title} indiriliyor", f"{title} indirildi"
                if state in ("active", "paused") and cur_total > 0:
                    r.size = cur_total  # hafif modele geçildiyse gerçek boyut
                elif not r.size:
                    r.size = MODEL_BYTES.get(model, 0)
            else:
                active, done = r.d.active, r.d.done
            r.title = done if state == "done" else active
            if r.size:  # indirme satırı
                if state == "done":
                    r.done = r.size
                elif state in ("active", "paused") and step == r.d.step:
                    r.done = max(0, min(cur_done, r.size))
                else:
                    r.done = 0
                r.detail = fmt_pair(r.done, r.size) if state in ("active", "paused") else fmt_size(r.size)
            elif state == "active":
                r.detail = fmt_elapsed(now - self._since.setdefault(r.d.key, now))
            else:
                r.detail = ""
            if state == "paused":
                r.detail = r.detail if r.size else "Duraklatıldı"
            # not: hata, ağ beklemesi, hafif modele geçiş
            note, ncolor, ntip = "", C.TEXT_2, ""
            if state == "failed":
                note, ncolor, ntip = error_note(error), C.TEXT, error
            elif state == "active" and message.startswith(_NET_HINT):
                note, ntip = message, message
            elif r.d.key == "CHECK" and note_text and state in ("active", "done"):
                note = note_text
            if r.widget is not None:
                r.widget.set_title(active, done)
                r.widget.set_state(state, r.detail)
                relayout |= r.widget.set_note(note, ncolor, ntip)
        if relayout:
            self.relayout.emit()

    def _quick_row(self) -> _Row | None:
        return next((r for r in self._rows if r.d.key == "QUICK"), None)

    def _progress(self, now: float) -> tuple[float, float, float, float]:
        """(oran, inen bayt, kalan bayt, kalan baytsız süre). Baytsız adımlar beklenen süreleri kadar pay alır;
        süren baytsız adımın payı geçen süreyle dolar ama bitmeden %95'i geçmez."""
        total = done = 0.0
        byte_done = byte_left = 0.0
        time_left = 0.0
        for r in self._rows:
            if r.size:
                total += r.size
                done += r.done
                byte_done += r.done
                if r.state not in ("done", "cancelled"):
                    byte_left += max(0, r.size - r.done)
            elif r.d.expect_s:
                share = r.d.expect_s * NOMINAL_RATE
                total += share
                if r.state == "done":
                    done += share
                elif r.state == "active":
                    elapsed = now - self._since.get(r.d.key, now)
                    done += share * min(0.95, 1 - math.exp(-elapsed / r.d.expect_s))
                    time_left += max(5.0, r.d.expect_s - elapsed)
                elif r.state == "pending":
                    time_left += r.d.expect_s
        frac = done / total if total else 0.0
        return frac, byte_done, byte_left, time_left

    # --- görünüm ------------------------------------------------------------------------------------------------
    def _refresh(self) -> None:
        now = self._clock()
        phase = self.phase()
        self._sync_rows()
        quick = self._quick_row()
        if quick is not None:
            qstate = {"active": "active", "done": "done", "failed": "failed"}.get(self._quick, "pending")
            quick.state = qstate
            quick.done = QUICK_BYTES if qstate == "done" else int(QUICK_BYTES * self._quick_frac)
            quick.detail = fmt_pair(quick.done, QUICK_BYTES) if qstate == "active" else (
                "" if qstate == "failed" else fmt_size(QUICK_BYTES))
            quick.widget.set_state(qstate, quick.detail)
            if quick.widget.set_note(_first_line(self._quick_error) if qstate == "failed" else "", C.TEXT,
                                     self._quick_error if qstate == "failed" else ""):
                self.relayout.emit()
        self._snapshot(phase, now)
        frac, byte_done, byte_left, time_left = self._progress(now)
        if phase == "done":
            frac = 1.0
        # hız ve kalan süre: bir şey inerken ölçülür; baytsız adımda son hızla tahmin edilir
        cur = self._current_row()
        waiting_net = str(getattr(self.ai, "message", "") or "").startswith(_NET_HINT) and phase == "running"
        downloading = cur is not None and cur.size > 0 and cur.state == "active" and not waiting_net
        busy = phase in ("running", "queued") or self._quick == "active"
        if busy:
            self._rate.add(byte_done, now)
        rate = self._rate.speed() if downloading else None
        if rate:
            self._last_rate = rate
        eta = None
        if busy and phase != "paused":
            use = rate or self._last_rate
            if byte_left <= 0:
                eta = time_left or None
            elif use:
                eta = byte_left / use + time_left
            elif downloading and phase == "running" and getattr(self.ai, "eta_s", None):
                eta = float(getattr(self.ai, "eta_s") or 0) + time_left
        self.bar.setValue(round(max(0.0, min(1.0, frac)) * 1000))
        for label, text in ((self.pct, fmt_percent(frac) if phase != "idle" else ""),
                            (self.speed, fmt_speed(rate) if downloading else ""), (self.eta, fmt_eta(eta))):
            label.setText(text)
            label.setVisible(bool(text))  # boş etiket aralık bırakmasın
        self.speed.setToolTip("Son 10 saniyenin ortalama indirme hızı")
        if busy and not self._ticker.isActive():
            self._ticker.start()
        elif not busy and self._ticker.isActive():
            self._ticker.stop()
        if self.compact:
            self._refresh_compact(phase, cur)
        else:
            self._refresh_full(phase)

    def _current_row(self) -> _Row | None:
        for r in self._rows:
            if r.state in ("active", "paused", "failed"):
                return r
        return None

    def _quick_ready(self) -> bool:
        return self._quick in ("done", "hidden")

    def _can_cancel(self) -> bool:
        if self._queued:
            return True
        if self._cancelling:
            return False
        flag = getattr(self.ai, "can_cancel", None)
        if isinstance(flag, bool):
            return flag
        return step_name(getattr(self.ai, "step", None)) != "INSTALL"

    def _refresh_full(self, phase: str) -> None:
        error = str(getattr(self.ai, "error", "") or "")
        has_ai = self._plan is not None
        busy = phase in ("queued", "running", "paused")
        failed = phase == "failed"
        # başlık
        if failed:
            self.header.set("Yapay zekâ kurulamadı", "Hızlı çeviri hazır; onunla başlayabilirsiniz."
                            if self._quick_ready() else "")
        elif phase == "cancelled":
            self.header.set("Kuruluyor", "Yapay zekâ kurulumu iptal edildi.")
        else:
            self.header.set("Kuruluyor")
        # not satırı (duraklama) ve elle kurma bağlantısı
        self.note_box.setVisible(has_ai and busy)
        if phase == "paused":
            self.note.set(PAUSED, "info", tip=PAUSE_TIP)
        else:
            self.note.set(PAUSE_NOTE, "info", tip=PAUSE_TIP)
        self.resume_btn.setVisible(phase == "paused")
        self.link_box.setVisible(failed and manual_install(self.ai))
        # düğmeler (ekranda tek pembe birincil düğme). Hatada "Hızlı çeviride kal" her zaman, "Tekrar dene" yalnız
        # servis yeniden denemeyi anlamlı bulursa.
        failed_quick = self._quick == "failed"
        self.cancel_btn.setVisible(has_ai and busy)
        self.cancel_btn.setEnabled(self._can_cancel())
        self.cancel_btn.setToolTip("Hızlı çeviri kalır" if self.cancel_btn.isEnabled() or self._cancelling
                                   else INSTALL_NO_CANCEL)
        self.stay_btn.setVisible(failed and not failed_quick)
        self.back_btn.setVisible(phase == "quick" and self._back)
        self.retry_quick_btn.setVisible(failed_quick)
        self.retry_quick_btn.setToolTip(self._quick_error)
        self.retry_btn.setVisible(failed and not failed_quick and can_retry(self.ai))
        self.retry_btn.setToolTip(error)
        ready = self._quick_ready()
        self.start_btn.setVisible(not failed_quick and not failed)
        self.start_btn.setEnabled(ready)
        self.wait_label.setVisible(not ready and not failed_quick)
        # yapay zekâ sürerken "Şimdi başla" (o arka planda sürer), her şey bittiyse "Başla"
        self.start_btn.setText("Şimdi başla" if busy else "Başla")
        self.start_btn.setToolTip(START_TIP if busy else "")

    def _refresh_compact(self, phase: str, cur: _Row | None) -> None:
        error = str(getattr(self.ai, "error", "") or "")
        busy = phase in ("running", "paused")
        failed = phase == "failed"
        detail, tip = "", ""
        if phase == "paused":
            kind, text, tip = "info", PAUSED, PAUSE_TIP
        elif phase == "running":
            kind = "busy" if cur is not None and cur.size else "work"
            text = cur.title if cur is not None else (str(getattr(self.ai, "message", "") or "") or "Hazırlanıyor…")
            detail = cur.detail if cur is not None else ""
        elif phase == "done":
            kind, text = "ok", "Yapay zekâ hazır"
        elif failed:
            kind, text, tip = "error", _first_line(error) or "Yapay zekâ kurulamadı.", error
            detail = ""
        elif phase == "cancelled":
            kind, text = "info", CANCELLED
        else:
            kind, text = "", ""
        if kind != self._line_kind:
            self._line_kind = kind
            spec = IconLine.KINDS.get(kind)
            if spec:
                self.line_icon.setPixmap(_glyph(spec[0], spec[1]))
        self.line_icon.setVisible(bool(kind))
        self.line_text.setText(text)
        self.line_detail.setText(detail)
        self.line_text.setToolTip(tip)
        self.setAccessibleName(" ".join(p for p in (text, detail) if p))
        visible = phase != "idle"
        for w in (self.line_icon, self.line_text, self.line_detail):
            w.setVisible(visible and (w is not self.line_icon or bool(kind)))
        self.bar.setVisible(busy)
        self.meta_box.setVisible(visible and bool(self._rows))
        self.steps_box.setVisible(visible and bool(self._rows) and self.steps_btn.isChecked())
        self.resume_btn.setVisible(phase == "paused")
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(self._can_cancel())
        self.cancel_btn.setToolTip("Yapay zekâ kurulumunu durdurur; hızlı çeviri kalır"
                                   if self.cancel_btn.isEnabled() or self._cancelling else INSTALL_NO_CANCEL)
        self.retry_btn.setVisible(failed and can_retry(self.ai))
        self.retry_btn.setToolTip(error)
        self.stay_btn.setVisible(failed)
        self.link_btn.setVisible(failed and manual_install(self.ai))


# --- Ekran 2'nin ayrı pencere biçimi ----------------------------------------------------------------------------
class AiConfirmDialog(QDialog):
    """Çeviri sayfasındaki "Önerilen kurulum": Ekran 2 ayrı pencerede. "Kur" → ai.start(plan), pencere kabul edilir.
    Ollama ve model zaten kuruluysa pencere kendiliğinden kapanır (yalnız ayar yapılır). Gösterilmeden döner
    (ekran görüntüsü araçları için); confirm_ai_setup() bunu açıp bekler."""

    def __init__(self, ai: AiSetup, parent: QWidget | None = None):
        super().__init__(parent)
        from altyazitr.gui.theme import app_icon

        self.setWindowTitle("Yapay zekâ kurulumu")
        self.setWindowIcon(app_icon())
        self.setModal(True)
        self.ai = ai
        self.started: Any = None
        self.panel = AiConfirmPanel(back_text="Vazgeç")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.panel)
        self.panel.back.connect(self.reject)
        self.panel.install.connect(self._install)
        self.panel.relayout.connect(self._schedule_fit)
        self._width = dialog_width(parent)
        self._fit_timer = QTimer(self, singleShot=True, interval=0, timeout=self._fit)
        self._survey: SurveyThread | None = None
        self.panel.install_btn.setDefault(True)
        self._fit()

    def showEvent(self, e) -> None:  # noqa: N802 - Qt
        super().showEvent(e)
        if self._survey is None:
            self._survey = start_survey(self.ai, self._on_advice, self._on_plan)

    def _on_advice(self, advice: Any) -> None:
        self.panel.set_advice(advice)

    def _on_plan(self, model: str, plan: Any, error: str, free: Any) -> None:
        self.panel.set_plan(model, plan, error, free)
        if (model == self.panel.model and plan is not None and nothing_to_download(plan)
                and not getattr(plan, "blocker", "") and self.isVisible()):
            self._install(plan)  # her şey kurulu: Ekran 2 atlanır, yalnız ayar yapılır

    def _install(self, plan: Any) -> None:
        if self.started is not None:
            return
        self.started = plan
        self.ai.start(plan)
        self.accept()

    def _schedule_fit(self) -> None:
        self._fit_timer.start()

    def _fit(self) -> None:
        fit_dialog(self, self.panel, self._width)


def confirm_ai_setup(parent: QWidget | None, ai: AiSetup) -> bool:
    """Ekran 2'yi (onay) ayrı pencere olarak açar. "Kur" → `ai.start(plan)` ve True; Vazgeç/kapatma → False.
    İş zaten sürüyorsa hiçbir şey açmaz ve False döner (ilerleme Çeviri sayfasında görünür)."""
    if str(getattr(ai, "state", "") or "") in ("running", "paused"):
        return False
    dlg = AiConfirmDialog(ai, parent)
    return dlg.exec() == QDialog.DialogCode.Accepted and dlg.started is not None
