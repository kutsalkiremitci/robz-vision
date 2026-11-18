"""Çeviri sayfası: motor seçimi (yalnızca seçili motorun ayarları), oyun profili, sözlük, gelişmiş.

Buradaki küçük form yardımcıları (etiket sütunu, durum satırı, ilerleme çubuğu, bağlantı ve ikon düğmesi)
Ayarlar sayfasında da kullanılır; böylece iki sayfanın alanları aynı dikey çizgiden başlar.

"Önerilen kurulum" tek tıkla: Ollama çalışıyor mu bakar, önerilen model (RECOMMENDED_OLLAMA_MODEL) yüklü
değilse indirir (ilerleme çubuğu, iptal), sonra motoru, modeli ve varsayılan oyun profilini ayarlar. Düğme iki
yerde durur ve ikisi aynı işi yapar: motor seçiminin altındaki davette (motor Ollama ve önerilen model seçili
olana kadar görünür) ve Ollama bölümünde. Durum, düğmesine basılan yerde görünür.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from PySide6.QtCore import QCoreApplication, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from altyazitr.game_profiles import DEFAULT_PROFILE_ID, get_profile, list_profiles
from altyazitr.gui.common import Worker, _form, _tip
from altyazitr.gui.controls import Collapsible
from altyazitr.gui.icons import icon, pixmap
from altyazitr.gui.tokens import C, F, ICON, SPACE
from altyazitr.gui.widgets import Card, bind_check, bind_combo, bind_line, bind_spin, label, page, row
from altyazitr.translate import (ENGINE_LABELS, GEMINI_MODELS, OLLAMA_PRESETS, QUICKMT_DIR, RECOMMENDED_OLLAMA_MODEL,
                                 download_quickmt, ollama_list, ollama_pull, quickmt_downloaded)


# Önerilen kurulumun indireceği modellerin boyutu (Ollama kütüphanesindeki varsayılan etiketler)
MODEL_DOWNLOAD_SIZE = {"gemma4:12b": "8,0 GB", "gemma4:e4b": "6,6 GB", "gemma4:26b": "yaklaşık 17 GB"}


def confirm_download(parent: QWidget, model: str) -> bool:
    """Önerilen kurulumun büyük indirmesi için onay: boyutu söylenmeden hiçbir model indirilmez."""
    size = MODEL_DOWNLOAD_SIZE.get(model)
    text = (f"{model_title(model)} indirilecek" + (f" ({size})." if size else ".") +
            " İnternet hızınıza göre birkaç dakika ile bir saat arası sürebilir; bu sırada hızlı çeviri "
            "kullanılabilir.")
    box = QMessageBox(QMessageBox.Icon.Question, "Önerilen kurulum", text, parent=parent)
    ok = box.addButton("İndir", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Vazgeç", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is ok


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
    "ollama": "En doğal çeviri; konuşmanın gidişatını ve argoyu anlar. Ollama kurulu olmalı.",
    "gemini": "Bağlamı anlayan çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "deepl": "Çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "none": "Çeviri yapılmaz; okumanın doğruluğunu test etmek içindir.",
}

ROW_H = 34  # sözlük tablosu satır yüksekliği

GLOSSARY_TIP = ("Oyundaki adlar ve terimler. Türkçe karşılığı boş bırakılan terim çevrilmeden aynen kalır "
                "(Vice City, Lucia). İsterseniz karşılık da yazabilirsiniz: cop → polis, bro → kanka.")
CONTEXT_TIP = "Önceki satırlar yapay zekâya bağlam olarak gider; konuşmanın gidişatını anlar."
PROFILE_TIP = "Yapay zekâya oyunun dünyasını ve argosunu anlatır; oyundaki adlar her motorda çevrilmeden kalır"
EXTRA_TIP = "Profilin üstüne eklenen kendi notun. Ör: hangi görevdesin, kimlerle konuşuyorsun."

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
OLLAMA_DOWN_TIP = "Ollama'yı başlatın veya adresi kontrol edin"


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


def model_installed(model: str, names: Sequence[str]) -> bool:
    """Model Ollama'da yüklü mü? Etiketsiz ad ':latest' ile de eşleşir."""
    m = model.strip()
    return m in names or (":" not in m and f"{m}:latest" in names)


class PullStatus:
    """Ollama indirmesinin durumu: kısa durum satırı, indirirken ilerleme çubuğu + İptal, Ollama'ya ulaşılamazsa
    indirme bağlantısı. Ollama bölümünde ve önerilen kurulum davetinde aynı biçimde durur; iş, düğmesine basılan
    yerin durumunu kullanır. Öğeler gizliyken yer kaplamaz (boş alt düzen aralık almaz)."""

    def __init__(self, link_always: bool):
        self.line = StatusLine()
        self.bar = progress_bar()
        self.bar.setRange(0, 1000)
        self.cancel = with_icon(QPushButton("İptal"), "x")
        self.cancel.setToolTip("İndirmeyi durdurur")
        self.cancel.hide()
        self.link = link_button("Ollama'yı indir", OLLAMA_DOWNLOAD_URL)
        self.link_always = link_always
        self.link.setVisible(link_always)
        self.box = vbox(self.line, hbox(self.bar, self.cancel, None), hbox(self.link, None))

    def busy(self, on: bool) -> None:
        self.bar.setVisible(on)
        self.cancel.setVisible(on)
        self.cancel.setEnabled(on)
        if on:
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)

    def need_ollama(self, on: bool) -> None:
        self.link.setVisible(on or self.link_always)

    def clear(self) -> None:
        self.line.set("")
        self.need_ollama(False)


class TranslatePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.ctx = win.ctx
        t = self.ctx.s.translate
        sa, lay = page("Çeviri")
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)
        restart = self._restart
        self._workers: list[Worker] = []
        # süren Ollama indirmesi: (işçi, model, bitince çağrılacak iş, durumun gösterildiği yer); yoksa None
        self._pull: tuple[Worker, str, Callable[[], None] | None, PullStatus] | None = None
        rec_title = model_title(RECOMMENDED_OLLAMA_MODEL)
        rec_desc = dict(OLLAMA_PRESETS).get(RECOMMENDED_OLLAMA_MODEL, "")
        setup_tip = (f"Tek tıkla hazır: {rec_title} modeli (yüklü değilse indirilir) ve "
                     f"{get_profile(DEFAULT_PROFILE_ID).name} profili seçilir.")
        if rec_desc:
            setup_tip += f"\n{rec_desc}"

        # --- motor
        eng = Card("Çeviri motoru")
        f = form()
        self.engine = bind_combo(t, "engine", [(v, k) for k, v in ENGINE_LABELS.items()], self._engine_changed)
        self.engine.setMinimumWidth(FIELD_W)
        self.engine.setAccessibleName("Çeviri motoru")
        self.engine_desc = label("", "muted")
        add_row(f, "Motor", layout=vbox(self.engine, self.engine_desc, spacing=SPACE["xs"] + 2))
        # önerilen kurulum daveti: Ollama ve önerilen model seçili değilken motor seçiminin hemen altında durur
        self.hint_text = StatusLine(auto_hide=False)
        self.hint_text.set(f"Daha iyi çeviri: yerel yapay zekâ (Ollama + {rec_title}), ücretsiz ve internetsiz",
                           "info", tip=setup_tip)
        self.hint_setup = with_icon(QPushButton("Önerilen kurulum"), "check")
        self.hint_setup.setToolTip(setup_tip)
        self.hint_status = PullStatus(link_always=False)
        self.hint_setup.clicked.connect(lambda: self._recommended_setup(self.hint_status))
        self.hint_status.cancel.clicked.connect(self._cancel_pull)
        self.hint_box = QWidget()
        self.hint_box.setLayout(vbox(self.hint_text, hbox(self.hint_setup, None), self.hint_status.box))
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
        add_row(f, "Oyun profili", self.profile, tip=PROFILE_TIP)
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
        add_row(f, "Adres", bind_line(t, "ollama_url", restart, "http://127.0.0.1:11434"))
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
        self.ol_status = PullStatus(link_always=True)
        self.ol_line, self.ol_bar, self.ol_cancel, self.ol_get = (self.ol_status.line, self.ol_status.bar,
                                                                  self.ol_status.cancel, self.ol_status.link)
        self.ol_cancel.clicked.connect(self._cancel_pull)
        self.ol_setup = with_icon(QPushButton("Önerilen kurulum"), "check")
        self.ol_setup.setToolTip(setup_tip)
        self.ol_setup.clicked.connect(lambda: self._recommended_setup(self.ol_status))
        self.ol_pull = with_icon(QPushButton("Modeli indir"), "download")
        self.ol_pull.setToolTip("Seçili modeli Ollama'ya indirir")
        self.ol_pull.clicked.connect(self._ollama_pull)
        add_row(f, "", layout=vbox(hbox(self.ol_setup, self.ol_pull, None), self.ol_status.box))
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
        self._update_visibility()
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
        self.engine_form.setRowVisible(self.profile, e != "none")
        self.remote_form.setRowVisible(self.extra, e in ("ollama", "gemini"))  # DeepL istem kullanmaz
        self._update_setup_hint()

    def _update_setup_hint(self) -> None:
        """Önerilen kurulum daveti, motor Ollama olana kadar görünür (kartı küçük olduğu için bilerek başka bir
        Ollama modeli seçen kullanıcıya davet çıkmaz). Davetten başlayan indirme sürerken de görünür kalır
        (ilerlemesi orada)."""
        t = self.ctx.s.translate
        done = t.engine == "ollama"
        show = not done or (self._pull is not None and self._pull[3] is self.hint_status)
        self.engine_form.setRowVisible(self.hint_box, show)
        if not show:
            self.hint_status.clear()  # eski uyarı, davet yeniden göründüğünde kalmasın

    def _restart(self) -> None:
        self.ctx.save_later()
        self.ctx.request_restart()

    def _stop_workers(self) -> None:
        """Uygulama kapanırken süren indirmeyi durdurur: çalışırken yok edilen iş parçacığı süreci çökertir.
        Ollama indirmesi iptali bir sonraki ilerleme satırında (genelde 1 sn içinde) görür."""
        for w in self._workers:
            w.cancel.set()
        for w in self._workers:
            w.wait(3000)

    def _engine_changed(self) -> None:
        self._update_engine_desc()
        self._update_visibility()
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
        self._update_setup_hint()
        self._restart()

    def _ollama_refresh(self) -> None:
        names = ollama_list(self.ctx.s.translate.ollama_url)
        if names is None:
            self.ol_line.set("Ollama çalışmıyor", "warn", tip=OLLAMA_DOWN_TIP)
            return
        cur = self.ol_model.currentText()
        for n in names:
            if self.ol_model.findData(n) < 0:
                self.ol_model.addItem(n, n)
        self.ol_model.setCurrentText(cur)
        self.ol_line.set(f"{len(names)} model yüklü", "ok" if names else "info", tip=", ".join(names))

    def _ollama_pull(self) -> None:
        model = self.ol_model.currentText().strip()
        if not model or self._pull is not None:
            return
        if ollama_list(self.ctx.s.translate.ollama_url) is None:
            self.ol_line.set("Ollama çalışmıyor", "warn", tip=OLLAMA_DOWN_TIP)
            return
        self._start_pull(model, self.ol_status)

    def _start_pull(self, model: str, ui: PullStatus, then: Callable[[], None] | None = None) -> None:
        """Modeli arka planda Ollama'ya indirir; ilerleme ve İptal `ui`'da görünür. Başarıyla biterse `then`
        çağrılır; verilmediyse model listesi yenilenir ve işlem hattı yeniden başlar."""
        url = self.ctx.s.translate.ollama_url

        def job(progress, cancel):
            ollama_pull(url, model, progress, cancel)

        w = Worker(job, self)
        # sinyaller sayfanın yöntemlerine bağlanır: işçi iş parçacığından gelse de arayüz iş parçacığında çalışırlar
        w.progress.connect(self._pull_progress)
        w.done.connect(self._pull_done)
        self._pull = (w, model, then, ui)
        self._workers.append(w)
        self._set_pulling(ui, True)
        ui.line.set(f"{model} indiriliyor…", "busy", tip="")
        w.start()

    def _set_pulling(self, ui: PullStatus, on: bool) -> None:
        """Aynı anda tek indirme: süren indirmede iki 'Önerilen kurulum' ve 'Modeli indir' kapalıdır."""
        for b in (self.hint_setup, self.ol_setup, self.ol_pull):
            b.setEnabled(not on)
        ui.busy(on)

    def _pull_progress(self, msg: str, frac: float) -> None:
        if self._pull is None or self._pull[0].cancel.is_set():
            return  # iptal istendi: "İptal ediliyor…" yazısı kalsın
        ui = self._pull[3]
        if msg:
            ui.line.set(msg[:60], "busy")
        if frac >= 0:
            ui.bar.setRange(0, 1000)
            ui.bar.setValue(int(frac * 1000))
        else:
            ui.bar.setRange(0, 0)

    def _cancel_pull(self) -> None:
        if self._pull is None:
            return
        w, _model, _then, ui = self._pull
        w.cancel.set()  # ollama_pull bir sonraki ilerleme satırında durur
        ui.cancel.setEnabled(False)
        ui.line.set("İptal ediliyor…", "busy", tip="")

    def _pull_done(self, ok: bool, err: str) -> None:
        if self._pull is None:
            return
        w, model, then, ui = self._pull
        self._pull = None
        self._set_pulling(ui, False)
        if not ok:
            if w.cancel.is_set():
                ui.line.set("İndirme iptal edildi", "info", tip="")
            else:
                ui.line.set("İndirme başarısız", "error", tip=err)
            self._update_setup_hint()
            return
        if then is not None:
            then()
            return
        ui.line.set(f"{model} indirildi", "ok", tip="")
        self._ollama_refresh()
        self._restart()

    # --- önerilen kurulum ---------------------------------------------------------------
    def _recommended_setup(self, ui: PullStatus) -> None:
        """Tek tıkla önerilen kurulum (davetteki ve Ollama bölümündeki düğme): Ollama'ya ulaşılamazsa kurulum
        bağlantısını gösterir; önerilen model yüklü değilse indirir; sonra motoru, modeli ve varsayılan profili
        ayarlar. Durum, düğmesine basılan yerde (`ui`) görünür."""
        if self._pull is not None:
            return
        names = ollama_list(self.ctx.s.translate.ollama_url)
        ui.need_ollama(names is None)
        if names is None:
            ui.line.set("Önce Ollama'yı kurun", "warn",
                        tip="Ollama'ya ulaşılamadı. Kurup çalıştırın, sonra Önerilen kurulum'a yeniden basın.")
            return
        if model_installed(RECOMMENDED_OLLAMA_MODEL, names):
            self._apply_recommended()
        elif confirm_download(self, RECOMMENDED_OLLAMA_MODEL):
            self._start_pull(RECOMMENDED_OLLAMA_MODEL, ui, then=self._apply_recommended)

    def _apply_recommended(self) -> None:
        t = self.ctx.s.translate
        model = RECOMMENDED_OLLAMA_MODEL
        if (t.profile or "").strip().casefold() != DEFAULT_PROFILE_ID.casefold():
            self.ctx.pipeline.cache.clear()  # önceki profille yapılmış çeviriler yeniden çevrilsin
        t.engine, t.ollama_model, t.profile = "ollama", model, DEFAULT_PROFILE_ID
        # arayüzü ayarlarla eşle; sinyaller kapalı, yeniden başlatma aşağıda bir kez istenir
        for w in (self.engine, self.ol_model):
            w.blockSignals(True)
        self.engine.setCurrentIndex(max(0, self.engine.findData("ollama")))
        if self.ol_model.findData(model) < 0:
            self.ol_model.addItem(model, model)
        self.ol_model.setCurrentIndex(self.ol_model.findData(model))
        self.ol_model.setCurrentText(model)
        for w in (self.engine, self.ol_model):
            w.blockSignals(False)
        self._select_profile(DEFAULT_PROFILE_ID)
        self._update_engine_desc()
        self._update_visibility()  # davet gizlenir, Ollama bölümü görünür: onay orada
        self._restart()
        profile = self.profile.currentText() or DEFAULT_PROFILE_ID
        self.ol_line.set(f"Hazır: {model_title(model)} yerel çeviri", "ok",
                         tip=f"Motor: Ollama. Model: {model}. Oyun profili: {profile}.")

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
