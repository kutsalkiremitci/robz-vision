"""Çeviri sayfası: motor seçimi (yalnızca seçili motorun ayarları), oyun profili, sözlük, gelişmiş.

Buradaki küçük form yardımcıları (etiket sütunu, durum satırı, ilerleme çubuğu, bağlantı ve ikon düğmesi)
Ayarlar sayfasında da kullanılır; böylece iki sayfanın alanları aynı dikey çizgiden başlar.

Yerel yapay zekâyı (Ollama + Gemma 4) uygulamanın kurulum servisi kurar (`ctx.ai`, altyazitr.ai_setup.AiSetup).
"Önerilen kurulum" iki yerde durur ve ikisi aynı işi yapar: motor seçiminin altındaki davette ve Ollama bölümünde.
Basınca onay penceresi açılır (ne inecek, boyutu, disk; ai_setup_view.confirm_ai_setup); "Kur" denince iş arka
planda sürer. Davet, donanım önerisine göre (ai.advice(), arka planda okunur) yalnız uygun ekran kartında ve motor
Ollama değilken görünür; metnindeki model de öneriden gelir. İş sürerken ya da hatayla bittiyse yerinde kısa ilerleme
paneli durur (AiProgressPanel: İptal, Yine de sürdür, Tekrar dene). İlerleme düğmesine basılan yerde görünür; sayfa
dışından başlayan iş (ilk açılış, yarım kalan işin sürmesi) davette. "Modeli indir" yerel adreste aynı servise gider
(yalnız model iner, ayar değişmez); uzak sunucuda model eskisi gibi doğrudan o sunucuya iner. Kurulum bitince motor,
model ve adres seçimleri ayarlarla eşlenir; işlem hattı yeniden başlatılmaz (çeviri çalışıyorsa yapay zekâ bir sonraki
Başlat'ta devreye girer).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QCoreApplication, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from altyazitr import APP_NAME
from altyazitr.ai_setup import is_local_url
from altyazitr.game_profiles import DEFAULT_PROFILE_ID, list_profiles
from altyazitr.gui.ai_setup_view import AiProgressPanel, confirm_ai_setup, start_survey
from altyazitr.gui.common import Worker, _form, _tip
from altyazitr.gui.controls import Collapsible
from altyazitr.gui.icons import icon, pixmap
from altyazitr.gui.tokens import C, F, ICON, SPACE
from altyazitr.gui.widgets import Card, bind_check, bind_combo, bind_line, bind_spin, label, page, row
from altyazitr.translate import (ENGINE_LABELS, GEMINI_MODELS, OLLAMA_PRESETS, QUICKMT_DIR, RECOMMENDED_OLLAMA_MODEL,
                                 download_quickmt, ollama_list, ollama_pull, quickmt_downloaded)


# --- ortak form düzeni (Çeviri + Ayarlar) ---------------------------------------------
LABEL_W = 140   # etiket sütunu; iki sayfada aynı
FIELD_W = 340   # metin alanı / açılır menü genişliği (bind_line ile aynı)
_control_h: int | None = None


def control_height() -> int:
    """Temadaki tek satırlık giriş alanının yüksekliği (etiketleri ilk satırla hizalamak için)."""
    global _control_h
    if _control_h is None:
        _control_h = max(28, QLineEdit().sizeHint().height())
    return _control_h


def form(inside: Collapsible | None = None) -> QFormLayout:
    """Etiketler solda, sabit genişlikte sütunda; uzun alanlarda ilk satırla hizalı.

    `inside` verilirse form o katlanır bölümün girintili gövdesine konacak demektir: etiket sütunu
    girinti kadar daraltılır, böylece alanlar sayfadaki diğer alanlarla aynı dikey çizgide kalır."""
    f = _form()
    indent = inside.body_lay.contentsMargins().left() if inside is not None else 0
    f.setProperty("labelW", max(96, LABEL_W - indent))
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(SPACE["l"])
    f.setVerticalSpacing(SPACE["m"])
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    f.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return f


def field_label(text: str = "", tip: str = "", width: int = LABEL_W) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("field")
    lb.setFixedHeight(control_height())  # sabit yükseklik: metin, alanın ilk satırıyla aynı hizada
    lb.setStyleSheet(f"min-width: {width}px; max-width: {width}px;")  # temadaki #field genişliğini ezer
    lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if tip:
        lb.setToolTip(tip)
    return lb


def add_row(f: QFormLayout, text: str, w: QWidget | None = None, layout=None, tip: str = "") -> None:
    """Forma 'etiket | alan' satırı ekler. Boş etiketli satırlar alan sütununa hizalanır."""
    if isinstance(w, (QCheckBox, QLabel, StatusLine)):
        w.setMinimumHeight(control_height())  # alçak öğeler etiket metniyle aynı hizada dursun
    f.addRow(field_label(text, tip, int(f.property("labelW") or LABEL_W)), w if w is not None else layout)


def hbox(*items, spacing: int = SPACE["s"]) -> QHBoxLayout:
    """Yan yana öğeler; None = esnek boşluk."""
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for it in items:
        if it is None:
            h.addStretch(1)
        elif isinstance(it, QWidget):
            h.addWidget(it, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            h.addLayout(it)
    return h


def vbox(*items, spacing: int = SPACE["s"]) -> QVBoxLayout:
    v = QVBoxLayout()
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(spacing)
    for it in items:
        if isinstance(it, QWidget):
            v.addWidget(it)
        else:
            v.addLayout(it)
    return v


def with_icon(btn: QPushButton, name: str, color: str = C.TEXT_2) -> QPushButton:
    btn.setIcon(icon(name, color))
    btn.setIconSize(QSize(ICON["s"], ICON["s"]))
    return btn


def icon_button(name: str, tip: str) -> QPushButton:
    """Kare, yalnız ikonlu düğme (boyutu tema verir); ipucu ve erişilebilir ad zorunlu."""
    b = QPushButton()
    b.setObjectName("icon")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    with_icon(b, name)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    return b


def link_button(text: str, url: QUrl | str, tip: str = "", icon_name: str = "external") -> QPushButton:
    """Dış bağlantı: camgöbeği metin + 'dışarı aç' ikonu. Klavyeyle de açılır (odak + Enter/Boşluk)."""
    target = url if isinstance(url, QUrl) else QUrl(url)
    url = target.toLocalFile() if target.isLocalFile() else target.toString()
    b = QPushButton(text)
    b.setObjectName("ghost")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    with_icon(b, icon_name, C.CYAN)
    b.setStyleSheet(f"QPushButton {{ color: {C.CYAN}; padding-left: {SPACE['xs']}px; "
                    f"padding-right: {SPACE['s']}px; }}")
    b.setToolTip(tip or url)
    b.setAccessibleName(text)
    b.setAccessibleDescription(url)
    b.clicked.connect(lambda: QDesktopServices.openUrl(target))
    return b


def progress_bar() -> QProgressBar:
    """İnce ilerleme çubuğu (yazısız, temadaki 6 px çubuk); alan sütunu genişliğinde."""
    b = QProgressBar()
    b.setTextVisible(False)
    b.setFixedWidth(FIELD_W)
    b.hide()
    return b


class StatusLine(QWidget):
    """İkon + kısa durum metni. Anlamı renkli ikon taşır (turkuaz başarı, mercan uyarı, kırmızı hata,
    camgöbeği süren iş); metin sade kalır. Metin `text` QLabel'i olarak dışarıdan okunabilir."""

    KINDS = {
        "ok": ("circle-check", C.TURQUOISE),
        "warn": ("circle-alert", C.ORANGE),
        "error": ("circle-x", C.DANGER),
        "busy": ("download", C.CYAN),
        "pending": ("download", C.TEXT_3),
        "info": ("info", C.TEXT_3),
    }

    def __init__(self, auto_hide: bool = True, color: str = C.TEXT_2, parent: QWidget | None = None):
        super().__init__(parent)
        self.auto_hide = auto_hide
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SPACE["s"])
        self.icon = QLabel()
        self.icon.setFixedSize(ICON["s"], ICON["s"])
        self.text = QLabel()
        # Üst/alt boşluk şart: Bahnschrift'in dar dikey ölçüleri 'İ' noktasını kırpar. (Aynı nedenle metin
        # seçilebilir yapılmaz; seçilebilir QLabel boşluğa rağmen noktayı kırpıyor.)
        self.text.setStyleSheet(f"font-family: '{F.DISPLAY}'; font-size: {F.BODY_SIZE}px; color: {color};"
                                f" padding: 2px 0;")
        self.text.setMinimumWidth(1)  # dar pencerede sayfayı genişletmez; tam metin ipucunda kalır
        h.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.text, 1, Qt.AlignmentFlag.AlignVCenter)
        self.set("")

    def set(self, text: str, kind: str | None = None, tip: str | None = None) -> None:
        self.text.setText(text)
        spec = self.KINDS.get(kind or "")
        if spec:
            self.icon.setPixmap(pixmap(spec[0], ICON["s"], spec[1]))
        self.icon.setVisible(spec is not None)
        if tip is not None:
            self.text.setToolTip(tip)
        if self.auto_hide:
            self.setVisible(bool(text))


# --- sayfa ------------------------------------------------------------------------------
ENGINE_DESC = {
    "quickmt": "Yerel ve ücretsiz, çok hızlı.",
    "ollama": "En doğal çeviri; konuşmanın gidişatını ve argoyu anlar. Ekran kartı ister.",
    "gemini": "Bağlamı anlayan çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "deepl": "Çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "none": "Çeviri yapılmaz; okumanın doğruluğunu test etmek içindir.",
}

ROW_H = 34  # sözlük tablosu satır yüksekliği

GLOSSARY_TIP = ("Oyundaki adlar ve terimler. Türkçe karşılığı boş bırakılan terim çevrilmeden aynen kalır "
                "(Vice City, Lucia). İsterseniz karşılık da yazabilirsiniz: cop → polis, bro → kanka.")
CONTEXT_TIP = "Önceki satırlar yapay zekâya bağlam olarak gider; konuşmanın gidişatını anlar."
PROFILE_TIP = "Yapay zekâya oyunun dünyasını ve argosunu anlatır; oyundaki adlar her motorda çevrilmeden kalır"
# "Oyun profili"nin yanındaki bilgi simgesinin ipucu: profilin ne olduğu ve neyi değiştirdiği
PROFILE_INFO = (
    "<b>Oyun profili</b> yapay zekâya hangi oyunu çevirdiğini anlatır; her satır bu bilgiyle çevrilir:"
    "<ul style='margin-left:-24px'>"
    "<li><b>Dünya ve ton:</b> oyunun geçtiği yer ve konusu (GTA 6: Leonida, Vice City, suç dünyası).</li>"
    "<li><b>Çevrilmeyecek adlar:</b> karakter, yer, marka ve silah adları (Lucia, Vice City, Ammu-Nation…); "
    "Türkçe ekleri doğru alır: Lucia'yı, Vice City'de.</li>"
    "<li><b>Argo sözlüğü:</b> heist → soygun, stash → zula, snitch → ispiyoncu…</li>"
    "<li><b>Üslup:</b> sokak ağzı, küfrün şiddeti, tuş adları (L2, R2) aynen kalır.</li>"
    "</ul>"
    "Yerel yapay zekâ ve Gemini profilin tamamını kullanır; hızlı çeviri ve DeepL yalnız adları korur. "
    "Başka bir oyun oynarken <b>Genel</b>'i seçin."
)
EXTRA_TIP = "Profilin üstüne eklenen kendi notun. Ör: hangi görevdesin, kimlerle konuşuyorsun."

OLLAMA_DOWN_TIP = "Ollama'yı başlatın veya adresi kontrol edin"

# Yapay zekâ daveti. Bu kademelerde (uygun ekran kartı yok; hardware.advise) davet gösterilmez.
NO_INVITE_TIERS = frozenset({"none", "weak"})
INVITE_TEXT = "Daha iyi çeviri: yerel yapay zekâ (Ollama + {model}), ücretsiz ve internetsiz"
AI_JOB_STATES = ("running", "paused", "failed")  # kısa ilerleme panelinin göründüğü servis durumları
AI_ACTIVE = ("running", "paused")
# Kısa ilerleme panelinin çubuğu alan sütunundan kısa: duraklatılmışken yanında "Yine de sürdür" ve "İptal" de durur;
# en küçük pencerede de (780 px) sayfa yana taşmasın.
AI_BAR_W = 240


def hybrid_tip(wait_ms: int) -> str:
    """Karma mod ipucu; bekleme süresi ayardan gelir (1200 ms → '~1,2 sn')."""
    if wait_ms <= 0:
        return "Önce hızlı yerel çeviri görünür; yapay zekânın çevirisi gelince üstüne yazılır."
    sec = f"{wait_ms / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return (f"Yapay zekânın çevirisi ~{sec} sn içinde gelirse yalnız o görünür; gecikirse önce hızlı yerel çeviri "
            "görünür, sonra yapay zekânınki üstüne yazılır.")


def model_title(model: str, presets: Sequence[tuple[str, str]] = OLLAMA_PRESETS) -> str:
    """Modelin okunur adı: hazır listedeki açıklamanın '—' öncesi, parantezsiz (ör. 'Gemma 4 12B').
    Listede yoksa model adının kendisi."""
    for name, desc in presets:
        if name == model:
            return re.sub(r"\s*\([^)]*\)", "", desc.split("—")[0]).strip() or model
    return model


def setup_tip(model: str, advice: Any = None) -> str:
    """"Önerilen kurulum" ipucu: ne kurulacağı (model öneriden) ve varsa karta özgü not ve uyarı."""
    lines = [f"{APP_NAME}, Ollama'yı (yapay zekâyı çalıştıran ücretsiz program) ve {model_title(model)} modelini "
             "indirip kurar; kurmadan önce indirme boyutunu gösterir. İnternetsiz çalışır, hızlı çeviri yedekte kalır."]
    lines += [str(x) for x in (getattr(advice, "tip", ""), getattr(advice, "warn", "")) if x]
    return "\n".join(lines)


def invite_offered(advice: Any) -> bool:
    """Donanım önerisine göre davet edilir mi? Kademe none/weak ise (uygun ekran kartı yok) hayır. Öneri okunamadıysa
    (None) evet: onay penceresi ve kurulumdaki GPU kapısı yine korur."""
    if advice is None:
        return True
    return bool(getattr(advice, "offer", True)) and getattr(advice, "tier", "") not in NO_INVITE_TIERS


def _ignore_plan(*_args: Any) -> None:
    """start_survey'in plan sinyali için (sayfa yalnız öneriyi okur)."""


class PullStatus:
    """Uzak Ollama sunucusuna model indirmesinin durumu (Ollama bölümü): kısa durum satırı, indirirken ilerleme
    çubuğu + İptal. Öğeler gizliyken yer kaplamaz (boş alt düzen aralık almaz)."""

    def __init__(self) -> None:
        self.line = StatusLine()
        self.bar = progress_bar()
        self.bar.setRange(0, 1000)
        self.cancel = with_icon(QPushButton("İptal"), "x")
        self.cancel.setToolTip("İndirmeyi durdurur")
        self.cancel.hide()
        self.box = vbox(self.line, hbox(self.bar, self.cancel, None))

    def busy(self, on: bool) -> None:
        self.bar.setVisible(on)
        self.cancel.setVisible(on)
        self.cancel.setEnabled(on)
        if on:
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)


def has_model(model: str, names: Sequence[str]) -> bool:
    """Model Ollama'da yüklü mü. Etiketsiz ad ("llama3") Ollama'nın listesinde "llama3:latest" olarak görünür."""
    m = model.strip().casefold()
    want = {m} if ":" in m else {m, f"{m}:latest"}
    return bool(m) and any(n.strip().casefold() in want for n in names)


class TranslatePage(QWidget):
    advice_ready = Signal(object)  # donanım önerisi okundu (hardware.Advice; okunamadıysa None)
    ai_progress = Signal(float)  # yapay zekâ kurulumunun toplam ilerlemesi (0..1; bkz. ai_fraction) değişti
    _ollama_models_read = Signal(object)  # arka planda okunan yüklü Ollama modelleri (list; Ollama kapalıysa None)

    def __init__(self, win):
        super().__init__()
        self.ctx = win.ctx
        self.ai = self.ctx.ai  # yerel yapay zekâ kurulum servisi (altyazitr.ai_setup.AiSetup)
        t = self.ctx.s.translate
        sa, lay = page("Çeviri")
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)
        restart = self._restart
        self._workers: list[Worker] = []
        self._pull: tuple[Worker, str] | None = None  # uzak sunucuya süren model indirmesi (işçi, model)
        self._planning: tuple[Worker, dict[str, Any]] | None = None  # "Modeli indir": servisin planı çıkarılıyor
        self._advice: Any = None
        self._advice_known = False
        # Ollama'daki yüklü modeller: arka planda okunur (Ollama kapalıyken yanıt ~2,5 sn sürer; arayüz donmasın).
        # None = bilinmiyor; o zaman "Modeli indir" görünür kalır.
        self._ol_installed: list[str] | None = None
        self._ol_reading = False
        self._ol_report = False  # okuma bitince sonuç Ollama bölümünün durum satırına yazılsın mı (yenile düğmesi)
        self._ollama_models_read.connect(self._on_ollama_models)
        # Yapay zekâ işinin ilerlemesi düğmesine basılan yerde görünür: "hint" (davet) ya da "ollama" (Ollama
        # bölümü). Sayfa dışından başlayan iş davette görünür; "Tekrar dene" yerini korur.
        self._ai_state = str(getattr(self.ai, "state", "") or "idle")
        self._ai_origin = "hint"
        self._next_origin = ""

        # --- motor
        eng = Card("Çeviri motoru")
        f = form()
        self.engine = bind_combo(t, "engine", [(v, k) for k, v in ENGINE_LABELS.items()], self._engine_changed)
        self.engine.setMinimumWidth(FIELD_W)
        self.engine.setAccessibleName("Çeviri motoru")
        self.engine_desc = label("", "muted")
        add_row(f, "Motor", layout=vbox(self.engine, self.engine_desc, spacing=SPACE["xs"] + 2))
        # yapay zekâ daveti motor seçiminin hemen altında; iş sürerken yerinde kısa ilerleme paneli
        self.hint_text = StatusLine(auto_hide=False)
        self.hint_setup = with_icon(QPushButton("Önerilen kurulum"), "check")
        self.hint_setup.clicked.connect(lambda: self._recommended_setup("hint"))
        self.hint_invite = QWidget()
        self.hint_invite.setLayout(vbox(self.hint_text, hbox(self.hint_setup, None)))
        self.hint_progress = self._ai_panel()
        self.hint_progress.bar.valueChanged.connect(self._emit_ai_progress)
        self.hint_box = QWidget()
        self.hint_box.setLayout(vbox(self.hint_invite, self.hint_progress))
        add_row(f, "", self.hint_box)
        # oyun profili: ad gösterilir, veri olarak id tutulur; ipucu profilin açıklaması. Yapay zekâ istemini
        # belirler, profilin adlarını ise her motor korur; bu yüzden yerel hızlı çeviride de görünür.
        self.profile = QComboBox()
        self.profile.setMinimumWidth(FIELD_W)
        self.profile.setAccessibleName("Oyun profili")
        for p in list_profiles():
            self.profile.addItem(p.name, p.id)
            self.profile.setItemData(self.profile.count() - 1, p.description, Qt.ItemDataRole.ToolTipRole)
        shown = self._select_profile(t.profile)
        if shown and shown.casefold() != (t.profile or "").strip().casefold():
            # kayıtlı profil listede yok: get_profile genel profile düşerdi; çeviri ekrandaki profille yapılsın
            t.profile = shown
        self.profile.currentIndexChanged.connect(self._profile_changed)
        # yanında bilgi simgesi: üzerine gelince profilin ne olduğu ve neyi değiştirdiği görünür
        self.profile_info = QLabel()
        self.profile_info.setPixmap(pixmap("info", ICON["s"], C.TEXT_2))
        self.profile_info.setToolTip(PROFILE_INFO)
        self.profile_info.setAccessibleName("Oyun profili nedir")
        self.profile_info.setAccessibleDescription(re.sub(r"<[^>]+>", " ", PROFILE_INFO))
        self.profile_info.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.profile_row = QWidget()
        self.profile_row.setLayout(hbox(self.profile, self.profile_info, None))
        add_row(f, "Oyun profili", self.profile_row, tip=PROFILE_INFO)
        self.engine_form = f
        eng.add(layout=f)
        self.remote_box = QWidget()
        rf = form()
        self.remote_box.setLayout(rf)
        self.remote_form = rf
        # ek bağlam profilin hemen altında: kullanıcının notu profile eklenir (yalnız yapay zekâ istemine gider)
        self.extra = _tip(bind_line(t, "game_context", restart, "Ör: Lucia ile soygun planı"), EXTRA_TIP)
        add_row(rf, "Ek bağlam", self.extra)
        self.hybrid = bind_check("Yapay zekâ gecikirse hızlı çeviriyi göster", t, "hybrid", restart,
                                 tip=hybrid_tip(t.hybrid_wait_ms))
        add_row(rf, "Hibrit mod", self.hybrid)
        self.context = _tip(bind_spin(t, "context_lines", 0, 12, restart, suffix=" satır"), CONTEXT_TIP)
        add_row(rf, "Bağlam", self.context)
        eng.add(self.remote_box)
        lay.addWidget(eng)

        # --- quickmt
        qm = Card("Yerel çeviri modeli")
        f = form()
        self.qm_line = StatusLine(auto_hide=False)
        self.qm_status = self.qm_line.text
        self.qm_status.setToolTip(f"Model klasörü: {QUICKMT_DIR}")
        self.qm_btn = with_icon(QPushButton("Modeli indir"), "download")
        self.qm_btn.setToolTip("Yaklaşık 400 MB; bir kez indirilir")
        self.qm_btn.clicked.connect(self._download_quickmt)
        self.qm_bar = progress_bar()
        self.qm_bar.setRange(0, 0)
        add_row(f, "Durum", layout=vbox(hbox(self.qm_line, self.qm_btn, None, spacing=SPACE["m"]), self.qm_bar))
        add_row(f, "İşlemci", _tip(bind_combo(t, "quickmt_device", [("CPU (önerilen)", "cpu"),
                                                                    ("NVIDIA GPU (CUDA)", "cuda")], restart),
                                   "CPU zaten çok hızlıdır (~20-60 ms). GPU için ayrıca "
                                   "'pip install nvidia-cublas-cu12' gerekir."))
        qm.add(layout=f)
        lay.addWidget(qm)
        self.card_qm = qm

        # --- ollama
        ol = Card("Ollama")
        f = form()
        self.ol_url = bind_line(t, "ollama_url", restart, "http://127.0.0.1:11434")
        add_row(f, "Adres", self.ol_url)
        self.ol_model = QComboBox()
        self.ol_model.setEditable(True)
        self.ol_model.setMinimumWidth(FIELD_W - 32 - SPACE["s"])
        self.ol_model.setAccessibleName("Ollama modeli")
        for name, desc in OLLAMA_PRESETS:
            self.ol_model.addItem(name, name)
            self.ol_model.setItemData(self.ol_model.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        self.ol_model.setCurrentText(t.ollama_model)
        self.ol_model.setToolTip(f"Önerilen: {RECOMMENDED_OLLAMA_MODEL}. Listede olmayan bir modelin adını "
                                 "da yazabilirsiniz.")
        self.ol_model.currentTextChanged.connect(self._ollama_model_changed)
        b_list = icon_button("refresh", "Yüklü modelleri yenile")
        b_list.clicked.connect(self._ollama_refresh)
        add_row(f, "Model", layout=hbox(self.ol_model, b_list, None))
        self.ol_status = PullStatus()
        self.ol_line, self.ol_bar, self.ol_cancel = self.ol_status.line, self.ol_status.bar, self.ol_status.cancel
        self.ol_cancel.clicked.connect(self._cancel_pull)
        self.ol_setup = with_icon(QPushButton("Önerilen kurulum"), "check")
        self.ol_setup.clicked.connect(lambda: self._recommended_setup("ollama"))
        self.ol_pull = with_icon(QPushButton("Modeli indir"), "download")
        self.ol_pull.setToolTip("Seçili modeli Ollama'ya indirir")
        self.ol_pull.clicked.connect(self._ollama_pull)
        # buradan başlayan işin ilerlemesi; motor zaten Ollama olduğu için hatada "Hızlı çeviride kal" yerine "Vazgeç"
        self.ol_progress = self._ai_panel()
        self.ol_progress.stay_btn.setText("Vazgeç")
        self.ol_progress.stay_btn.setToolTip("Yarım kalan işi bırakır")
        add_row(f, "", layout=vbox(hbox(self.ol_setup, self.ol_pull, None), self.ol_status.box, self.ol_progress))
        ol.add(layout=f)
        lay.addWidget(ol)
        self.card_ol = ol

        # --- gemini
        gm = Card("Google Gemini")
        f = form()
        add_row(f, "API anahtarı", layout=vbox(
            bind_line(t, "gemini_key", restart, "AIza…", password=True),
            hbox(link_button("Ücretsiz anahtar al", "https://aistudio.google.com/apikey"), None),
            spacing=SPACE["xs"]))
        self.gm_model = QComboBox()
        self.gm_model.setEditable(True)
        self.gm_model.setMinimumWidth(FIELD_W)
        self.gm_model.setAccessibleName("Gemini modeli")
        for name, desc in GEMINI_MODELS:
            self.gm_model.addItem(name, name)
            self.gm_model.setItemData(self.gm_model.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        self.gm_model.setCurrentText(t.gemini_model)
        self.gm_model.currentTextChanged.connect(
            lambda txt: (setattr(t, "gemini_model", txt.strip()), self._restart()))
        add_row(f, "Model", self.gm_model)
        privacy = StatusLine(auto_hide=False)
        privacy.set("Gizlilik", "info", tip="Google, ücretsiz katmanda gönderilen metinleri ürünlerini "
                                            "geliştirmek için kullanabilir. Yalnızca altyazı metni gönderilir. "
                                            "Kota dolarsa yerel çeviri devreye girer.")
        privacy.setToolTip(privacy.text.toolTip())
        add_row(f, "", privacy)
        gm.add(layout=f)
        lay.addWidget(gm)
        self.card_gm = gm

        # --- deepl
        dl = Card("DeepL")
        f = form()
        add_row(f, "API anahtarı", layout=vbox(
            bind_line(t, "deepl_key", restart, "xxxxxxxx-…:fx", password=True),
            hbox(link_button("Ücretsiz anahtar al", "https://www.deepl.com/pro-api",
                             tip="Ücretsiz plan: ayda 500 bin karakter"), None),
            spacing=SPACE["xs"]))
        dl.add(layout=f)
        lay.addWidget(dl)
        self.card_dl = dl

        # --- sözlük
        gl = Card("Sözlük")
        gl.setToolTip(GLOSSARY_TIP)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["İngilizce", "Türkçe (boşsa çevrilmez)"])
        self.table.horizontalHeaderItem(1).setToolTip("Boş bırakılırsa terim çevrilmeden aynen gösterilir")
        self.table.setToolTip(GLOSSARY_TIP)
        self.table.setAccessibleName("Sözlük")
        self._style_table()
        self.gl_empty = label("Sözlük boş. Çevrilmemesi gereken bir ad eklemek için Ekle'ye basın.", "muted")
        add = with_icon(QPushButton("Ekle"), "plus")
        add.setToolTip("Sözlüğe yeni satır ekler")
        add.clicked.connect(self._add_new_row)
        self.gl_remove = with_icon(QPushButton("Sil"), "trash")
        self.gl_remove.setToolTip("Seçili satırları siler")
        self.gl_remove.clicked.connect(self._remove_rows)
        for en, tr in t.glossary:
            self._add_row(en, tr)
        self.table.itemChanged.connect(self._glossary_changed)
        self.table.itemSelectionChanged.connect(self._update_glossary_ui)
        gl.add(self.table)
        gl.add(self.gl_empty)
        gl.add(layout=hbox(add, self.gl_remove, None))
        lay.addWidget(gl)
        self._update_glossary_ui()

        # --- gelişmiş
        adv_card = Card()
        adv = Collapsible("Gelişmiş")
        f = form(inside=adv)
        add_row(f, "Zaman aşımı", _tip(bind_spin(t, "timeout_s", 1, 30, restart, step=0.5, suffix=" sn",
                                                 decimals=1),
                                       "Çevrimiçi veya LLM yanıtı bu süreyi aşarsa yerel çeviri gösterilir"))
        clr = with_icon(QPushButton("Çeviri önbelleğini temizle"), "trash")
        clr.setToolTip("Daha önce çevrilmiş satırlar yeniden çevrilir")

        def clear_cache() -> None:
            self.ctx.pipeline.cache.clear()
            clr.setText("Önbellek temizlendi")
            with_icon(clr, "check", C.TURQUOISE)

        clr.clicked.connect(clear_cache)
        add_row(f, "Önbellek", layout=hbox(clr, None))
        adv.add(layout=f)
        adv_card.add(adv)
        lay.addWidget(adv_card)
        lay.addStretch(1)

        self._update_engine_desc()
        self._update_qm_status()
        self._apply_advice()
        self._update_visibility()
        self.ai.changed.connect(self._on_ai_changed)
        self.ai.finished.connect(self._on_ai_finished)
        # donanım önerisi (ekran kartı okuması ≤ ~1 sn) arka planda: gelene dek davet gizli
        self._survey = start_survey(self.ai, self._on_advice, _ignore_plan, models=())
        if t.engine == "ollama":
            self._read_ollama_models()  # "Modeli indir" yalnız seçili model yüklü değilse görünsün
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_workers)

    # --- görünürlük ---------------------------------------------------------------------
    def _update_visibility(self) -> None:
        e = self.ctx.s.translate.engine
        self.card_qm.setVisible(e == "quickmt")
        self.card_ol.setVisible(e == "ollama")
        self.card_gm.setVisible(e == "gemini")
        self.card_dl.setVisible(e == "deepl")
        self.remote_box.setVisible(e in ("ollama", "gemini", "deepl"))
        self.engine_form.setRowVisible(self.profile_row, e != "none")
        self.remote_form.setRowVisible(self.extra, e in ("ollama", "gemini"))  # DeepL istem kullanmaz
        self._update_setup_hint()

    def _update_setup_hint(self) -> None:
        """Davet alanı ve yapay zekâ işinin ilerlemesi. İş sürerken ya da hatayla bittiyse kısa ilerleme paneli
        düğmesine basılan yerde görünür (Ollama bölümü gizliyse davette); davet metni ve düğmesi o sırada gizlidir.
        İş yokken davet, kart uygunsa (öneri okunduysa ve kademe none/weak değilse) ve motor Ollama değilken görünür
        (kartı küçük olduğu için bilerek başka bir Ollama modeli seçen kullanıcıya davet çıkmaz)."""
        engine = self.ctx.s.translate.engine
        job = self._ai_state in AI_JOB_STATES
        in_ollama = job and self._ai_origin == "ollama" and engine == "ollama"
        in_hint = job and not in_ollama
        invite = engine != "ollama" and self._advice_known and invite_offered(self._advice)
        self.hint_invite.setVisible(not in_hint)
        self.hint_progress.setVisible(in_hint)
        self.ol_progress.setVisible(in_ollama)
        self.engine_form.setRowVisible(self.hint_box, in_hint or invite)
        self._sync_buttons()

    def _busy(self) -> bool:
        """Süren indirme var mı: yapay zekâ işi, "Modeli indir"in plan hazırlığı ya da uzak sunucuya indirme."""
        return self._ai_state in AI_ACTIVE or self._planning is not None or self._pull is not None

    def _sync_buttons(self) -> None:
        """Aynı anda tek indirme: süren indirmede iki "Önerilen kurulum" ve "Modeli indir" kapalıdır."""
        on = not self._busy()
        for b in (self.hint_setup, self.ol_setup, self.ol_pull):
            b.setEnabled(on)

    def _restart(self) -> None:
        self.ctx.save_later()
        self.ctx.request_restart()

    def _stop_workers(self) -> None:
        """Uygulama kapanırken süren indirmeyi durdurur: çalışırken yok edilen iş parçacığı süreci çökertir.
        Ollama indirmesi iptali bir sonraki ilerleme satırında (genelde 1 sn içinde) görür. Donanım okuması
        (nvidia-smi en çok 4 sn) da beklenir."""
        for w in self._workers:
            w.cancel.set()
        for w in self._workers:
            w.wait(3000)
        self._survey.wait(5000)

    def _engine_changed(self) -> None:
        self._update_engine_desc()
        self._update_visibility()
        if self.ctx.s.translate.engine == "ollama":
            self._read_ollama_models()
        self._restart()

    def _update_engine_desc(self) -> None:
        self.engine_desc.setText(ENGINE_DESC.get(self.ctx.s.translate.engine, ""))

    # --- oyun profili -------------------------------------------------------------------
    def _select_profile(self, pid: str) -> str:
        """Profili sinyal göndermeden seçer (büyük/küçük harf fark etmez, get_profile gibi); kayıtlı id listede
        yoksa varsayılan profil seçilir. Seçilen profilin id'sini döndürür."""
        ids = [str(self.profile.itemData(i) or "").casefold() for i in range(self.profile.count())]
        key = (pid or "").strip().casefold()
        i = ids.index(key) if key in ids else self.profile.findData(DEFAULT_PROFILE_ID)
        self.profile.blockSignals(True)
        self.profile.setCurrentIndex(max(i, 0))
        self.profile.blockSignals(False)
        self.profile.setToolTip(self.profile.itemData(self.profile.currentIndex(), Qt.ItemDataRole.ToolTipRole)
                                or "")
        return str(self.profile.currentData() or "")

    def _profile_changed(self, i: int) -> None:
        pid = self.profile.itemData(i)
        if not pid:
            return
        self.ctx.s.translate.profile = pid
        self.profile.setToolTip(self.profile.itemData(i, Qt.ItemDataRole.ToolTipRole) or "")
        self.ctx.pipeline.cache.clear()  # önceki profille yapılmış çeviriler yeniden çevrilsin
        self._restart()

    # --- quickmt ------------------------------------------------------------------------
    def _update_qm_status(self) -> None:
        ok = quickmt_downloaded()
        if ok:
            self.qm_line.set("Hazır", "ok")
        else:
            self.qm_line.set("İlk başlatmada otomatik indirilir (~400 MB)", "pending")
        self.qm_btn.setVisible(not ok)

    def _download_quickmt(self) -> None:
        self.qm_btn.setEnabled(False)
        self.qm_bar.setRange(0, 0)
        self.qm_bar.show()
        self.qm_line.set("İndirme başlıyor…", "busy")

        def job(progress, _cancel):
            download_quickmt(lambda m: progress(m, -1.0), progress=lambda fr: progress("", fr))

        w = Worker(job, self)
        w.progress.connect(self._qm_progress)
        w.done.connect(self._qm_done)
        self._workers.append(w)
        w.start()

    def _qm_progress(self, msg: str, frac: float) -> None:
        if msg:
            self.qm_line.set(msg, "busy")
        if frac >= 0:
            self.qm_bar.setRange(0, 1000)
            self.qm_bar.setValue(int(frac * 1000))

    def _qm_done(self, ok: bool, err: str) -> None:
        self.qm_bar.hide()
        self.qm_btn.setEnabled(True)
        self._update_qm_status()
        if not ok:
            self.qm_line.set("İndirme başarısız", "error", tip=err)

    # --- ollama -------------------------------------------------------------------------
    def _ollama_model_changed(self, text: str) -> None:
        self.ctx.s.translate.ollama_model = text.strip()
        self._update_pull_button()
        self._restart()

    def _ollama_refresh(self) -> None:
        """Yenile düğmesi: yüklü modeller yeniden okunur, sonuç durum satırında yazar."""
        self._read_ollama_models(report=True)

    def _read_ollama_models(self, report: bool = False) -> None:
        """Yüklü Ollama modellerini arka planda okur; sonuç _on_ollama_models'e gelir. Okuma sürerken yeni istek
        yalnız "sonucu yaz" isteğini ekler (ikinci okuma başlamaz)."""
        self._ol_report = self._ol_report or report
        if self._ol_reading:
            return
        self._ol_reading = True
        url = self.ctx.s.translate.ollama_url

        def job() -> None:
            names = None
            try:
                names = ollama_list(url)
            finally:
                try:
                    self._ollama_models_read.emit(names)
                except RuntimeError:  # sayfa kapanırken silindi
                    pass

        threading.Thread(target=job, name="ollama-modelleri", daemon=True).start()

    def _on_ollama_models(self, names: list[str] | None) -> None:
        """Yüklü modeller okundu: listede olmayanlar seçime eklenir, "Modeli indir" seçili modele göre gizlenir."""
        self._ol_reading = False
        report, self._ol_report = self._ol_report, False
        self._ol_installed = None if names is None else list(names)
        if names is not None:
            cur = self.ol_model.currentText()
            for n in names:
                if self.ol_model.findData(n) < 0:
                    self.ol_model.addItem(n, n)
            self.ol_model.setCurrentText(cur)
        if report:
            if names is None:
                self.ol_line.set("Ollama çalışmıyor", "warn", tip=OLLAMA_DOWN_TIP)
            else:
                self.ol_line.set(f"{len(names)} model yüklü", "ok" if names else "info", tip=", ".join(names))
        self._update_pull_button()

    def _update_pull_button(self) -> None:
        """"Modeli indir" yalnız seçili model Ollama'da yüklü değilse görünür. Yüklü modeller okunamadıysa (Ollama
        kapalı ya da henüz okunmadı) düğme görünür kalır."""
        installed = self._ol_installed is not None and has_model(self.ol_model.currentText(), self._ol_installed)
        self.ol_pull.setVisible(not installed)
        self.ol_model.setToolTip(
            (f"{self.ol_model.currentText().strip()} yüklü. " if installed else "")
            + f"Önerilen: {RECOMMENDED_OLLAMA_MODEL}. Listede olmayan bir modelin adını da yazabilirsiniz.")

    def _ollama_pull(self) -> None:
        """"Modeli indir": yerel Ollama'da kurulum servisine gider (yalnız model iner, ayar değişmez; ilerleme Ollama
        bölümünde). Uzak sunucuda model eskisi gibi doğrudan o sunucuya indirilir."""
        model = self.ol_model.currentText().strip()
        if not model or self._busy():
            return
        url = self.ctx.s.translate.ollama_url
        if not url.strip() or is_local_url(url):  # boş adres: servis varsayılan yerel adresi kullanır
            self._plan_model(model)
            return
        if ollama_list(url) is None:
            self.ol_line.set("Ollama çalışmıyor", "warn", tip=OLLAMA_DOWN_TIP)
            return
        self._start_pull(model)

    def _plan_model(self, model: str) -> None:
        """ai.start(ai.plan(model, apply=False)). Plan arka planda çıkarılır: Ollama'yı yoklar, Ollama kapalıyken
        ~1,5 sn sürebilir (arayüz donmasın)."""
        ai = self.ai
        box: dict[str, Any] = {}

        def job(_progress, _cancel) -> None:
            box["plan"] = ai.plan(model, apply=False)

        w = Worker(job, self)
        w.done.connect(self._planned)  # sayfanın yöntemi: sonuç arayüz iş parçacığında işlenir
        self._planning = (w, box)
        self._workers.append(w)
        self.ol_line.set("Hazırlanıyor…", "busy", tip="")
        self._sync_buttons()
        w.start()

    def _planned(self, ok: bool, err: str) -> None:
        if self._planning is None:
            return
        plan = self._planning[1].get("plan")
        self._planning = None
        if not ok or plan is None:
            self.ol_line.set("İndirme başlatılamadı", "error", tip=err)
        elif self._ai_state in AI_ACTIVE:  # o arada başka bir kurulum başladı (ör. yarım kalan iş sürdü)
            self.ol_line.set("Başka bir kurulum sürüyor", "info", tip="")
        else:
            self.ol_line.set("")
            self._next_origin = "ollama"
            try:
                self.ai.start(plan)
            finally:
                self._next_origin = ""
        self._sync_buttons()

    def _start_pull(self, model: str) -> None:
        """Modeli uzak Ollama sunucusuna arka planda indirir; ilerleme ve İptal Ollama bölümünde. Bitince model
        listesi yenilenir ve işlem hattı yeniden başlar."""
        url = self.ctx.s.translate.ollama_url

        def job(progress, cancel):
            ollama_pull(url, model, progress, cancel)

        w = Worker(job, self)
        # sinyaller sayfanın yöntemlerine bağlanır: işçi iş parçacığından gelse de arayüz iş parçacığında çalışırlar
        w.progress.connect(self._pull_progress)
        w.done.connect(self._pull_done)
        self._pull = (w, model)
        self._workers.append(w)
        self.ol_status.busy(True)
        self._sync_buttons()
        self.ol_line.set(f"{model} indiriliyor…", "busy", tip="")
        w.start()

    def _pull_progress(self, msg: str, frac: float) -> None:
        if self._pull is None or self._pull[0].cancel.is_set():
            return  # iptal istendi: "İptal ediliyor…" yazısı kalsın
        if msg:
            self.ol_line.set(msg[:60], "busy")
        if frac >= 0:
            self.ol_bar.setRange(0, 1000)
            self.ol_bar.setValue(int(frac * 1000))
        else:
            self.ol_bar.setRange(0, 0)

    def _cancel_pull(self) -> None:
        if self._pull is None:
            return
        self._pull[0].cancel.set()  # ollama_pull bir sonraki ilerleme satırında durur
        self.ol_cancel.setEnabled(False)
        self.ol_line.set("İptal ediliyor…", "busy", tip="")

    def _pull_done(self, ok: bool, err: str) -> None:
        if self._pull is None:
            return
        w, model = self._pull
        self._pull = None
        self.ol_status.busy(False)
        self._sync_buttons()
        if not ok:
            if w.cancel.is_set():
                self.ol_line.set("İndirme iptal edildi", "info", tip="")
            else:
                self.ol_line.set("İndirme başarısız", "error", tip=err)
            return
        self.ol_line.set(f"{model} indirildi", "ok", tip="")
        self._read_ollama_models()  # artık yüklü: "Modeli indir" gizlenir
        self._restart()

    # --- yapay zekâ kurulumu (önerilen kurulum, servis) --------------------------------------
    @property
    def advice(self) -> Any:
        """Donanım önerisi (hardware.Advice); okunmadıysa ya da okunamadıysa None (bkz. advice_known)."""
        return self._advice

    @property
    def advice_known(self) -> bool:
        return self._advice_known

    @property
    def ai_fraction(self) -> float:
        """Yapay zekâ kurulumunun toplam ilerlemesi (0..1): kısa ilerleme panelinin çubuğu. Baytsız adımlar
        (kurulum, başlatma) beklenen süreleriyle sayılır; kenar çubuğundaki yüzde panelinkiyle aynı kalsın diye."""
        bar = self.hint_progress.bar
        return bar.value() / max(1, bar.maximum())

    def _emit_ai_progress(self, _value: int) -> None:
        self.ai_progress.emit(self.ai_fraction)

    def _ai_panel(self) -> AiProgressPanel:
        """Kısa ilerleme paneli: süren adım, çubuk, İptal / Yine de sürdür; hatada Tekrar dene ve vazgeçme."""
        panel = AiProgressPanel(self.ai, compact=True)
        panel.bar.setFixedWidth(AI_BAR_W)
        return panel

    def _on_advice(self, advice: Any) -> None:
        """Donanım önerisi arka planda okundu: davetin metni ve modeli buna göre, kart uygunsa davet görünür."""
        self._advice, self._advice_known = advice, True
        self._apply_advice()
        self._update_setup_hint()
        self.advice_ready.emit(advice)

    def _apply_advice(self) -> None:
        """Davet metni ve iki "Önerilen kurulum" düğmesinin ipucu: model öneriden (gelene dek önerilen model)."""
        model = str(getattr(self._advice, "model", "") or RECOMMENDED_OLLAMA_MODEL)
        tip = setup_tip(model, self._advice)
        self.hint_text.set(INVITE_TEXT.format(model=model_title(model)), "info", tip=tip)
        for b in (self.hint_setup, self.ol_setup):
            b.setToolTip(tip)

    def _recommended_setup(self, origin: str) -> None:
        """"Önerilen kurulum" (davette ve Ollama bölümünde): onay penceresi açılır (ne inecek, boyutu, disk); "Kur"
        denince iş arka planda başlar, ilerlemesi düğmesine basılan yerde (`origin`) görünür. Ollama ve model zaten
        kuruluysa pencere kendiliğinden kapanır, yalnız ayar yapılır. Siteye yönlendirme yok: Ollama'yı servis kurar."""
        if self._busy():
            return
        self.ol_line.set("")
        self._next_origin = origin
        try:
            confirm_ai_setup(self, self.ai)
        finally:
            self._next_origin = ""

    def _on_ai_changed(self) -> None:
        state = str(getattr(self.ai, "state", "") or "idle")
        prev, self._ai_state = self._ai_state, state
        if state in AI_ACTIVE and prev not in AI_ACTIVE:
            # yeni iş: ilerleme düğmesine basılan yerde; sayfa dışından başlayan davette; "Tekrar dene" yerinde kalır
            if self._next_origin:
                self._ai_origin = self._next_origin
            elif prev != "failed":
                self._ai_origin = "hint"
            self.ol_line.set("")  # önceki işin sonucu kalmasın
        self._update_setup_hint()

    def _on_ai_finished(self, ok: bool, message: str) -> None:
        """İş bitti. Başarıda servis ayarları yazdı: motor, model, adres ve profil seçimleri sinyal göndermeden
        eşlenir (yeniden başlatma istenmez; çeviri çalışıyorsa yapay zekâ bir sonraki Başlat'ta devreye girer) ve
        sonuç Ollama bölümünde yazar. Kullanıcının iptalinde (iş Ollama bölümünden başladıysa) orada not düşülür."""
        self._ai_state = str(getattr(self.ai, "state", "") or "idle")
        if ok:
            self._sync_settings()
            self._show_result()
            self._read_ollama_models()  # model indi: "Modeli indir" seçili modele göre gizlenir
        elif self._ai_state != "failed" and self._ai_origin == "ollama":
            self.ol_line.set("İptal edildi", "info", tip="")
        self._update_setup_hint()

    def _sync_settings(self) -> None:
        """Arayüzü ayarlarla eşler; sinyaller kapalı (ayar yazılmaz, işlem hattı yeniden başlatılmaz)."""
        t = self.ctx.s.translate
        widgets = (self.engine, self.ol_model, self.ol_url)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.engine.setCurrentIndex(max(0, self.engine.findData(t.engine)))
            model = t.ollama_model.strip()
            if model and self.ol_model.findData(model) < 0:
                self.ol_model.addItem(model, model)
            if self.ol_model.findData(model) >= 0:
                self.ol_model.setCurrentIndex(self.ol_model.findData(model))
            self.ol_model.setCurrentText(model)
            if self.ol_url.text() != t.ollama_url:
                self.ol_url.setText(t.ollama_url)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._select_profile(t.profile)
        self._update_engine_desc()
        self._update_visibility()  # motor Ollama olduysa davet gizlenir, Ollama bölümü görünür: sonuç orada

    def _show_result(self) -> None:
        t = self.ctx.s.translate
        plan = getattr(self.ai, "active_plan", None)
        model = str(getattr(self.ai, "model", "") or "").strip()
        if plan is not None and not getattr(plan, "apply", True):  # "Modeli indir": yalnız model indi
            if model and self.ol_model.findData(model) < 0:
                self.ol_model.blockSignals(True)
                self.ol_model.addItem(model, model)
                self.ol_model.blockSignals(False)
            self.ol_line.set(f"{model_title(model)} indirildi", "ok", tip="")
        elif t.engine == "ollama":
            profile = self.profile.currentText() or DEFAULT_PROFILE_ID
            self.ol_line.set(f"Hazır: {model_title(t.ollama_model)} yerel çeviri", "ok",
                             tip=f"Motor: Ollama. Model: {t.ollama_model}. Oyun profili: {profile}.")

    # --- sözlük -------------------------------------------------------------------------
    def _style_table(self) -> None:
        tb = self.table
        hh = tb.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setFixedHeight(ROW_H)
        tb.verticalHeader().setVisible(False)
        tb.verticalHeader().setDefaultSectionSize(ROW_H)
        tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tb.setShowGrid(False)
        tb.setAlternatingRowColors(True)
        tb.setWordWrap(False)
        tb.setMaximumWidth(720)

    def _add_row(self, en: str, tr: str) -> None:
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(en))
        self.table.setItem(r, 1, QTableWidgetItem(tr))
        self.table.blockSignals(False)
        self._update_glossary_ui()

    def _add_new_row(self) -> None:
        self._add_row("", "")
        self.table.scrollToBottom()
        r = self.table.rowCount() - 1
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))

    def _remove_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._glossary_changed()
        self._update_glossary_ui()

    def _update_glossary_ui(self) -> None:
        n = self.table.rowCount()
        self.gl_empty.setVisible(n == 0)
        self.gl_remove.setEnabled(bool(self.table.selectedIndexes()))
        # tablo içeriği kadar uzar (en az 2, en çok 8 satır); fazlası tablo içinde kaydırılır
        self.table.setFixedHeight(ROW_H * (1 + min(max(n, 2), 8)) + 2)

    def _glossary_changed(self, *_a) -> None:
        out = []
        for r in range(self.table.rowCount()):
            en = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            tr = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if en:
                out.append([en, tr])
        self.ctx.s.translate.glossary = out
        self.ctx.pipeline.cache.clear()
        self._restart()
