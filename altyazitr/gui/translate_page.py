"""Çeviri sayfası: motor seçimi (yalnızca seçili motorun ayarları), sözlük, gelişmiş.

Buradaki küçük form yardımcıları (etiket sütunu, durum satırı, ilerleme çubuğu, bağlantı ve ikon düğmesi)
Ayarlar sayfasında da kullanılır; böylece iki sayfanın alanları aynı dikey çizgiden başlar.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from altyazitr.gui.common import Worker, _form, _tip
from altyazitr.gui.controls import Collapsible
from altyazitr.gui.icons import icon, pixmap
from altyazitr.gui.tokens import C, F, ICON, SPACE
from altyazitr.gui.widgets import Card, bind_check, bind_combo, bind_line, bind_spin, label, page, row
from altyazitr.translate import (ENGINE_LABELS, GEMINI_MODELS, OLLAMA_PRESETS, QUICKMT_DIR, download_quickmt,
                                 ollama_list, ollama_pull, quickmt_downloaded)

log = logging.getLogger(__name__)


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
    "ollama": "Yerel LLM ile en doğal çeviri. Ollama kurulu olmalı.",
    "gemini": "Bağlamı anlayan çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "deepl": "Çevrimiçi çeviri. Ücretsiz API anahtarı gerekir.",
    "none": "Çeviri yapılmaz; okumanın doğruluğunu test etmek içindir.",
}

ROW_H = 34  # sözlük tablosu satır yüksekliği

GLOSSARY_TIP = ("Oyundaki adlar ve terimler. Türkçe karşılığı boş bırakılan terim çevrilmeden aynen kalır "
                "(Vice City, Lucia). İsterseniz karşılık da yazabilirsiniz: cop → polis, bro → kanka.")


class TranslatePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = win.ctx
        t = self.ctx.s.translate
        sa, lay = page("Çeviri")
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)
        restart = self._restart
        self._workers: list[Worker] = []

        # --- motor
        eng = Card("Çeviri motoru")
        f = form()
        self.engine = bind_combo(t, "engine", [(v, k) for k, v in ENGINE_LABELS.items()], self._engine_changed)
        self.engine.setMinimumWidth(FIELD_W)
        self.engine.setAccessibleName("Çeviri motoru")
        self.engine_desc = label("", "muted")
        add_row(f, "Motor", layout=vbox(self.engine, self.engine_desc, spacing=SPACE["xs"] + 2))
        eng.add(layout=f)
        self.remote_box = QWidget()
        rf = form()
        self.remote_box.setLayout(rf)
        add_row(rf, "Hibrit mod", bind_check(
            "Önce hızlı yerel çeviriyi göster", t, "hybrid", restart,
            tip="Yerel çeviri anında görünür; daha kaliteli sonuç gelince yerine geçer."))
        add_row(rf, "Bağlam", _tip(bind_spin(t, "context_lines", 0, 6, restart, suffix=" satır"),
                                   "Daha doğal çeviri için önceki kaç satır modele gönderilsin"))
        add_row(rf, "Oyun bağlamı", _tip(bind_line(t, "game_context", restart, "Ör: GTA VI, suç dünyası, argo"),
                                         "Oyunun türü ve teması; modelin üslubu tutturmasına yardım eder"))
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
        self.ol_model.setToolTip("translategemma:4b hızlıdır; translategemma:12b en iyi kaliteyi verir "
                                 "(~8 GB VRAM). Bağlam ve argo için gemma4 veya qwen3.5 deneyin.")
        self.ol_model.currentTextChanged.connect(self._ollama_model_changed)
        b_list = icon_button("refresh", "Yüklü modelleri yenile")
        b_list.clicked.connect(self._ollama_refresh)
        add_row(f, "Model", layout=hbox(self.ol_model, b_list, None))
        self.ol_pull = with_icon(QPushButton("Modeli indir"), "download")
        self.ol_pull.setToolTip("Seçili modeli Ollama'ya indirir")
        self.ol_line = StatusLine()
        self.ol_status = self.ol_line.text
        self.ol_pull.clicked.connect(self._ollama_pull)
        self.ol_bar = progress_bar()
        self.ol_bar.setRange(0, 1000)
        add_row(f, "", layout=vbox(hbox(self.ol_pull, self.ol_line, None, spacing=SPACE["m"]), self.ol_bar,
                                   hbox(link_button("Ollama'yı indir", "https://ollama.com/download"), None)))
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

    # --- görünürlük ---------------------------------------------------------------------
    def _update_visibility(self) -> None:
        e = self.ctx.s.translate.engine
        self.card_qm.setVisible(e == "quickmt")
        self.card_ol.setVisible(e == "ollama")
        self.card_gm.setVisible(e == "gemini")
        self.card_dl.setVisible(e == "deepl")
        self.remote_box.setVisible(e in ("ollama", "gemini", "deepl"))

    def _restart(self) -> None:
        self.ctx.save_later()
        self.ctx.request_restart()

    def _engine_changed(self) -> None:
        self._update_engine_desc()
        self._update_visibility()
        self._restart()

    def _update_engine_desc(self) -> None:
        self.engine_desc.setText(ENGINE_DESC.get(self.ctx.s.translate.engine, ""))

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
        self._restart()

    def _ollama_refresh(self) -> None:
        names = ollama_list(self.ctx.s.translate.ollama_url)
        if names is None:
            self.ol_line.set("Ollama çalışmıyor", "warn", tip="Ollama'yı başlatın veya adresi kontrol edin")
            return
        cur = self.ol_model.currentText()
        for n in names:
            if self.ol_model.findData(n) < 0:
                self.ol_model.addItem(n, n)
        self.ol_model.setCurrentText(cur)
        self.ol_line.set(f"{len(names)} model yüklü", "ok" if names else "info", tip=", ".join(names))

    def _ollama_pull(self) -> None:
        url, model = self.ctx.s.translate.ollama_url, self.ol_model.currentText().strip()
        if not model:
            return
        if ollama_list(url) is None:
            self.ol_line.set("Ollama çalışmıyor", "warn", tip="Ollama'yı başlatın veya adresi kontrol edin")
            return
        self.ol_pull.setEnabled(False)
        self.ol_bar.show()
        self.ol_bar.setValue(0)
        self.ol_line.set(f"{model} indiriliyor…", "busy", tip="")

        def job(progress, cancel):
            ollama_pull(url, model, progress, cancel)

        w = Worker(job, self)

        def prog(msg: str, frac: float) -> None:
            self.ol_line.set(msg[:60], "busy")
            if frac >= 0:
                self.ol_bar.setRange(0, 1000)
                self.ol_bar.setValue(int(frac * 1000))
            else:
                self.ol_bar.setRange(0, 0)

        w.progress.connect(prog)
        w.done.connect(lambda ok, err: self._pull_done(ok, err, model))
        self._workers.append(w)
        w.start()

    def _pull_done(self, ok: bool, err: str, model: str) -> None:
        self.ol_pull.setEnabled(True)
        self.ol_bar.hide()
        if ok:
            self.ol_line.set(f"{model} indirildi", "ok", tip="")
        else:
            self.ol_line.set("İndirme başarısız", "error", tip=err)
        if ok:
            self._ollama_refresh()
            self._restart()

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
