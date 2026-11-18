"""İlk açılış penceresi: çeviri yolunun seçimi ve zorunlu hızlı çeviri modelinin (quickmt) indirilmesi.

Hızlı çeviri modeli yoksa uygulama ana pencereyi açmadan önce bu pencereyi gösterir. Model, pencere açılır açılmaz
arka planda (QThread) iner; arayüz donmaz. Yerel yapay zekâ servisi (`AiSetup`) verildiyse pencere dört ekranlıdır
(QStackedWidget; parçalar gui/ai_setup_view.py'de):

1. Seçim: "Yapay zekâ ile" (uygun ekran kartında önceden seçili) ya da "Yalnız hızlı çeviri". Donanım arka planda
   okunur ("Ekran kartı inceleniyor…"); kullanıcı o arada bir karta tıkladıysa seçimi ezilmez.
2. Onay: model, indirilecekler ve boyutları, disk. Ollama ve model zaten kuruluysa atlanır (yalnız ayar yapılır).
3. İlerleme: her adım kendi satırında (Ollama'yı da uygulama indirip kurar), toplam ilerleme, hız ve kalan süre.
   Yapay zekâ indirmesi hızlı çeviri modeli inince başlar (bant bölünmesin). "Şimdi başla" hızlı çeviri inene dek
   kapalıdır; basınca pencere kapanır, yapay zekâ arka planda sürer. Hata olursa hatalı satır ve "Tekrar dene" burada.
4. Sonuç: yapay zekâ hazır ya da bu bilgisayarda yavaş.

Servis verilmediyse (ai=None) pencere eskisi gibidir: yalnız hızlı çeviri modeli iner, bitince kendiliğinden kapanır.

Kapatma: "Çıkış" uygulamayı açmadan çıkar. X ve Esc de öyledir; ancak yapay zekâ kurulumu başladıysa (Ekran 3-4) ve
hızlı çeviri hazırsa pencereyi kapatmak "Şimdi başla" gibidir. Ekran 2'de Esc bir önceki ekrana döner. Uygulama
kapanırken indirme sürüyorsa iş parçacığı `_abandoned`'da tutulur; app.py `download_running()` ile bunu görüp süreci
`os._exit` ile bitirir (snapshot_download yarıda durdurulamıyor; yarım dosyalar sonraki açılışta tamamlanır).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QStackedWidget, QVBoxLayout, QWidget

from altyazitr import APP_NAME
from altyazitr import translate as T
from altyazitr.gui import icons
from altyazitr.gui.ai_setup_view import (CARD_AI_TEXT, CARD_AI_TIP, CARD_AI_TITLE, CARD_QUICK_TEXT, CARD_QUICK_TITLE,
                                         INSPECTING, LIGHT_MODEL, MODEL_BYTES, NO_GPU_TEXT, NO_GPU_TIP, QUICK_BYTES,
                                         QUICK_WAIT, RECOMMENDED, AiConfirmPanel, AiProgressPanel, ChoiceCard, Header,
                                         Outcome, SurveyThread, _figure, _installer_info, describe_result,
                                         dialog_width, fit_dialog, fmt_pair, fmt_size, nothing_to_download,
                                         page_layout, primary_button, rule, secondary_button, start_survey,
                                         step_names)
from altyazitr.gui.dialogs import AlertLabel, font_css
from altyazitr.gui.theme import app_icon
from altyazitr.gui.tokens import ICON, SPACE, C, F

if TYPE_CHECKING:
    from altyazitr.ai_setup import AiSetup, Plan
    from altyazitr.config import Settings

log = logging.getLogger(__name__)

LEGACY_W = 460   # yapay zekâsız (eski) pencerenin genişliği
START_HINT = "Hızlı çeviri modeli inince açılır."

# Kullanıcı indirme sürerken çıktıysa iş parçacığı burada tutulur (çalışırken yok edilmesin).
_abandoned: list[QThread] = []


def download_running() -> bool:
    """Yarıda bırakılmış ve hâlâ süren bir indirme var mı?"""
    _abandoned[:] = [t for t in _abandoned if t.isRunning()]
    return bool(_abandoned)


class _DownloadThread(QThread):
    progressed = Signal(float)
    failed = Signal(str)

    def run(self) -> None:
        try:
            # Modül üzerinden çağrılır: testlerde T.download_quickmt / T.QUICKMT_DIR değiştirilebilir.
            T.download_quickmt(lambda _m: None, progress=self.progressed.emit)
        except Exception as e:  # TranslatorError dahil
            log.warning("İlk kurulum indirmesi başarısız: %s", e)
            self.failed.emit(str(e) or type(e).__name__)


def _ai_size_text(plan: Plan) -> str:
    """Kart A'daki indirme satırı (Ekran 2'deki toplamla aynı sayı)."""
    if nothing_to_download(plan):
        return "Kurulu, indirme yok"
    total = int(getattr(plan, "download_bytes", 0) or 0)
    names = step_names(plan)
    if "DOWNLOAD" not in names and "INSTALL" not in names:
        return f"İndirme: {fmt_size(total, approx=True)} (Ollama kurulu)"
    return f"İndirme: yaklaşık {fmt_size(total, approx=True)}"


def _estimate_text(model: str) -> str:
    """Plan gelmeden önce: Ollama + modelin bilinen boyutu."""
    return f"İndirme: yaklaşık {fmt_size(_installer_info()[0] + MODEL_BYTES.get(model, 8_000_000_000), True)}"


class _QuickFooter(QWidget):
    """Ekran 1'in altındaki hızlı çeviri modeli durumu: ikon + yazı, altında ince çubuk ve miktar. İnemezse çubuğun
    yerinde "Tekrar dene" durur; hatanın tamamı ipucundadır."""

    retry = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE["xs"])
        top = QHBoxLayout()
        top.setSpacing(SPACE["s"])
        self.icon = QLabel()
        self.icon.setFixedSize(ICON["s"], ICON["s"])
        self.text = _figure("", C.TEXT_2)
        self.text.setMinimumWidth(1)  # dar pencerede düğmeleri itmez
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.text, 1, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(top)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(ICON["s"] + SPACE["s"], 0, 0, 0)
        bottom.setSpacing(SPACE["m"])
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setMaximumWidth(220)
        self.bar.setAccessibleName("Hızlı çeviri modeli indirmesi")
        self.amount = _figure("", C.TEXT_3)
        self.retry_btn = secondary_button("Tekrar dene")
        self.retry_btn.setAutoDefault(False)
        self.retry_btn.clicked.connect(self.retry)
        self.retry_btn.hide()
        bottom.addWidget(self.bar, 1, Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.amount, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.retry_btn)
        bottom.addStretch(0)
        v.addLayout(bottom)
        self.state = ""

    def set(self, state: str, frac: float, error: str = "") -> None:
        if state != self.state:
            name, color = {"done": ("circle-check", C.TURQUOISE), "failed": ("circle-alert", C.ORANGE)}.get(
                state, ("download", C.CYAN))
            self.icon.setPixmap(icons.pixmap(name, ICON["s"], color))
        self.state = state
        self.text.setText({"done": "Hızlı çeviri modeli hazır", "failed": "Hızlı çeviri modeli inemedi"}.get(
            state, "Hızlı çeviri modeli iniyor"))
        self.amount.setText(fmt_pair(frac * QUICK_BYTES, QUICK_BYTES) if state == "active" else "")
        self.bar.setValue(round(max(0.0, min(1.0, frac)) * 1000))
        self.bar.setVisible(state == "active")
        self.amount.setVisible(state == "active")
        self.retry_btn.setVisible(state == "failed")
        self.setToolTip(error if state == "failed" else "")
        self.setAccessibleName(f"{self.text.text()} {self.amount.text()}".strip())


class FirstRunDialog(QDialog):
    """İlk açılış penceresi (bkz. modül açıklaması). `choice`: kullanıcının seçimi ("ai" | "quickmt" | "")."""

    def __init__(self, settings: Settings | None = None, ai: AiSetup | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} kurulumu")
        self.setWindowIcon(app_icon())
        self.setModal(True)
        self.settings, self.ai = settings, ai
        self.choice = ""
        self._thread: _DownloadThread | None = None
        self._quick = "done" if T.quickmt_downloaded() else "active"
        self._quick_frac = 1.0 if self._quick == "done" else 0.0
        self._quick_error = ""
        self._auto_close = False   # "Yalnız hızlı çeviri" seçildi: model inince pencere kapanır
        self._started = False      # pencere bir kez gösterildi (indirme ve donanım okuması başladı)
        self._survey: SurveyThread | None = None
        self._advice: Any = None
        self._user_picked = False
        self._outcome: Outcome | None = None
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        if ai is None:
            self._width = LEGACY_W
            self.legacy = self._build_legacy()
            self.stack.addWidget(self.legacy)
        else:
            self._width = dialog_width(parent)
            self.choice_page = self._build_choice()
            self.confirm = AiConfirmPanel(back_text="Geri")
            self.confirm.back.connect(lambda: self._go(self.choice_page))
            self.confirm.install.connect(self._install)
            self.confirm.relayout.connect(self._schedule_fit)
            self.confirm.models.changed.connect(lambda _m: self._update_ai_card())
            self.progress = AiProgressPanel(ai)
            self.progress.start_clicked.connect(self._finish)
            self.progress.back_clicked.connect(self._back_to_choice)
            self.progress.retry_quick.connect(self._start_quick)
            self.progress.stay_quick.connect(self._stay_quick)
            self.progress.cancelled.connect(lambda: self._set_choice("quickmt"))
            self.progress.relayout.connect(self._schedule_fit)
            self.result_page = self._build_result()
            for page in (self.choice_page, self.confirm, self.progress, self.result_page):
                self.stack.addWidget(page)
            ai.finished.connect(self._on_ai_finished)
        self._fit_timer = QTimer(self, singleShot=True, interval=0, timeout=self._fit)
        self._sync_quick()
        self._go(self.stack.widget(0))

    # --- sayfalar ---------------------------------------------------------------------------------------------------
    def _build_legacy(self) -> QWidget:
        """Yapay zekâsız pencere (bugünkü görünüm): başlık, ince çubuk, miktar, hata, Çıkış / Tekrar dene."""
        page = QWidget()
        page.setObjectName("page")
        lay = page_layout(page)
        self.header = Header("Ücretsiz çeviri modeli indiriliyor", "Yaklaşık 400 MB, yalnızca bir kez indirilir",
                             mark=52)
        lay.addWidget(self.header)
        lay.addSpacing(SPACE["xl"])
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setAccessibleName("İndirme ilerlemesi")  # görünüm temadan (QProgressBar)
        lay.addWidget(self.bar)
        lay.addSpacing(SPACE["s"])
        self.amount = QLabel("")
        self.amount.setStyleSheet(font_css(F.DISPLAY, F.BODY_SIZE, C.TEXT_2))
        self.amount.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.amount)
        self.error = AlertLabel("", C.ORANGE)  # uyarı ikonu + kısa ileti (tamamı ipucunda)
        self.error.hide()
        lay.addSpacing(SPACE["s"])
        lay.addWidget(self.error)
        lay.addSpacing(SPACE["xl"])
        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        btns.addStretch(1)
        self.quit_btn = secondary_button("Çıkış")
        self.quit_btn.setAutoDefault(False)
        self.quit_btn.clicked.connect(self._quit)
        self.retry_btn = primary_button("Tekrar dene")
        self.retry_btn.setIcon(icons.icon("refresh", C.INVERSE))
        self.retry_btn.setIconSize(QSize(ICON["s"], ICON["s"]))
        self.retry_btn.clicked.connect(self._start_quick)
        self.retry_btn.hide()
        btns.addWidget(self.quit_btn)
        btns.addWidget(self.retry_btn)
        lay.addLayout(btns)
        return page

    def _build_choice(self) -> QWidget:
        """Ekran 1: iki seçim kartı; altta hızlı çeviri modelinin indirmesi, Çıkış ve Devam."""
        page = QWidget()
        page.setObjectName("page")
        lay = page_layout(page)
        lay.addWidget(Header("Çeviri nasıl yapılsın?", "Sonra Çeviri sayfasından değiştirebilirsiniz."))
        lay.addSpacing(SPACE["xl"])
        cards = QWidget()  # kartlar aynı kapta: tek seçim (autoExclusive) ve oklarla gezinme
        cl = QVBoxLayout(cards)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SPACE["m"])
        self.card_ai = ChoiceCard(CARD_AI_TITLE, CARD_AI_TEXT, _estimate_text(T.RECOMMENDED_OLLAMA_MODEL))
        self.card_ai.setToolTip(CARD_AI_TIP)
        self.card_ai.set_info(INSPECTING, "wait")
        self.card_quick = ChoiceCard(CARD_QUICK_TITLE, CARD_QUICK_TEXT, f"İndirme: {fmt_size(QUICK_BYTES)}")
        self.card_quick.setToolTip("Yerel ve ücretsiz; çeviri bu bilgisayarda yapılır.")
        for card in (self.card_ai, self.card_quick):
            card.clicked.connect(self._card_clicked)
            card.toggled.connect(lambda _on: self._update_next())
            cl.addWidget(card)
        lay.addWidget(cards)
        lay.addSpacing(SPACE["xl"])
        lay.addWidget(rule())
        lay.addSpacing(SPACE["l"])
        foot = QHBoxLayout()
        foot.setSpacing(SPACE["s"])
        self.quick_footer = _QuickFooter()
        self.quick_footer.retry.connect(self._start_quick)
        self.quit_btn = secondary_button("Çıkış")
        self.quit_btn.setAutoDefault(False)
        self.quit_btn.clicked.connect(self._quit)
        self.next_btn = primary_button("Devam")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._next)
        foot.addWidget(self.quick_footer, 1)
        foot.addSpacing(SPACE["l"])
        foot.addWidget(self.quit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(self.next_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(foot)
        return page

    def _build_result(self) -> QWidget:
        """Ekran 4: sonuç başlığı ve açıklaması; bir ya da iki düğme."""
        page = QWidget()
        page.setObjectName("page")
        lay = page_layout(page)
        self.result_header = Header("Yapay zekâ hazır")
        lay.addWidget(self.result_header)
        lay.addSpacing(SPACE["xl"])
        btns = QHBoxLayout()
        btns.setSpacing(SPACE["s"])
        btns.addStretch(1)
        self.res_secondary = secondary_button("Hızlı çeviride kal")
        self.res_secondary.setAutoDefault(False)
        self.res_secondary.clicked.connect(self._stay_quick)
        self.res_primary = primary_button("Başla")
        self.res_primary.clicked.connect(self._result_primary)
        btns.addWidget(self.res_secondary)
        btns.addWidget(self.res_primary)
        lay.addLayout(btns)
        return page

    # --- gezinme ----------------------------------------------------------------------------------------------------
    def _primary(self, page: QWidget) -> Any:
        """Sayfanın görünen birincil (pembe) düğmesi: Enter buna gider."""
        if page is self.choice_page:
            return self.next_btn
        if page is self.confirm:
            return self.confirm.install_btn
        if page is self.progress:
            p = self.progress
            # Tekrar dene yoksa (ör. imza hatası) Enter görünmeyen "Şimdi başla"ya değil "Hızlı çeviride kal"a gitsin
            return next((b for b in (p.retry_quick_btn, p.retry_btn, p.start_btn, p.stay_btn) if b.isVisibleTo(p)),
                        p.start_btn)
        if page is self.result_page:
            return self.res_primary
        return None

    def _go(self, page: QWidget) -> None:
        self.stack.setCurrentWidget(page)
        if self.ai is not None:
            primary = self._primary(page)
            if primary is not None:
                primary.setDefault(True)
            if page is self.choice_page:
                card = self.card_ai if self.card_ai.isChecked() or (
                    self.card_ai.isEnabled() and not self.card_quick.isChecked()) else self.card_quick
                card.setFocus()
            elif primary is not None and primary.isEnabled():
                primary.setFocus()
        self._fit()

    def _back_to_choice(self) -> None:
        self._auto_close = False
        self._go(self.choice_page)

    def _schedule_fit(self) -> None:
        self._fit_timer.start()

    def _fit(self) -> None:
        page = self.stack.currentWidget()
        if page is not None:
            fit_dialog(self, page, self._width)

    # --- Ekran 1 ---------------------------------------------------------------------------------------------------
    def _card_clicked(self) -> None:
        self._user_picked = True
        self._update_next()

    def _on_advice(self, advice: Any) -> None:
        """Donanım okundu: Kart A'yı kademeye göre hazırlar ve (kullanıcı henüz seçmediyse) önceden seçer."""
        self._advice = advice
        self.confirm.set_advice(advice)
        tier = str(getattr(advice, "tier", "unknown") or "unknown")
        offered = advice is None or (bool(getattr(advice, "offer", False)) and tier not in ("none", "weak"))
        preselect = offered and bool(getattr(advice, "preselect", False))
        line = str(getattr(advice, "line", "") or "") or ("Ekran kartı belirlenemedi" if offered else NO_GPU_TEXT)
        tip = str(getattr(advice, "tip", "") or "")
        card = self.card_ai
        if offered:
            card.set_info(line, "ok" if tier in ("edge", "mid", "good", "strong") else "info")
            card.set_warn(str(getattr(advice, "warn", "") or ""))
            card.set_badge(RECOMMENDED if preselect else "")
            card.setToolTip(f"{CARD_AI_TIP}\n\n{tip}" if tip else CARD_AI_TIP)
            card.setEnabled(True)
            self._update_ai_card()
        else:
            card.set_info(line, "info")
            card.set_warn("")
            card.set_badge("")
            card.set_size("")
            card.setToolTip(tip or NO_GPU_TIP)
            if card.isChecked():
                self.card_quick.setChecked(True)  # seçilemeyen kart seçili kalamaz
            card.setEnabled(False)
        if not self._user_picked:
            (card if preselect else self.card_quick).setChecked(True)
            # odak kartlardaysa seçili karta geçer (Çıkış/Devam'a gelmiş odağa dokunulmaz)
            if self.stack.currentWidget() is self.choice_page and self.focusWidget() in (None, card, self.card_quick):
                (card if card.isChecked() else self.card_quick).setFocus()
        self._update_next()
        self._schedule_fit()

    def _on_plan(self, model: str, plan: Any, error: str, free: Any) -> None:
        self.confirm.set_plan(model, plan, error, free)
        self._update_ai_card()
        self._update_next()

    def _update_ai_card(self) -> None:
        if not self.card_ai.isEnabled():
            return
        plan = self.confirm.plan
        self.card_ai.set_size(_ai_size_text(plan) if plan is not None else _estimate_text(self.confirm.model))
        self._schedule_fit()

    def _update_next(self) -> None:
        if self.ai is None:
            return
        if self.card_quick.isChecked():
            ok = True
        elif self.card_ai.isChecked():
            ok = self.confirm.has_plan()
        else:
            ok = False
        self.next_btn.setEnabled(ok)
        self.next_btn.setToolTip("" if ok or not self.card_ai.isChecked() else "Kurulum hazırlanıyor…")

    def _next(self) -> None:
        """Devam: B → hızlı çeviri hazırsa pencere kapanır, değilse Ekran 3 (yalnız hızlı çeviri satırı) ve inince
        kendiliğinden kapanır. A → Ekran 2; indirilecek bir şey yoksa Ekran 2 atlanır."""
        if self.card_quick.isChecked():
            self._set_choice("quickmt")
            if self._quick == "done":
                self.accept()
                return
            self._auto_close = True
            self.progress.show_quick_only(back=True)
            self._go(self.progress)
        elif self.card_ai.isChecked() and self.confirm.has_plan():
            plan = self.confirm.plan
            if plan is not None and nothing_to_download(plan) and not getattr(plan, "blocker", ""):
                self._install(plan)
            else:
                self._go(self.confirm)

    # --- Ekran 2-3 ---------------------------------------------------------------------------------------------------
    def _install(self, plan: Plan) -> None:
        """Kur (ya da indirilecek bir şey yoksa doğrudan): iş hızlı çeviri inince başlar (bant bölünmesin)."""
        self._set_choice("ai")
        self._auto_close = False
        wait = self._quick != "done" and not nothing_to_download(plan)
        self.progress.start(plan, wait=wait)
        self._go(self.progress)

    def _on_ai_finished(self, ok: bool, message: str) -> None:
        """Kurulum bitti: başarıda Ekran 4. Hata ve iptal Ekran 3'te kalır (hatalı satır, Tekrar dene)."""
        if not self.isVisible() or self.progress.plan is None or self.progress.queued:
            return
        if ok:
            self._show_result()
        else:
            if self.stack.currentWidget() is self.progress:
                self._primary(self.progress).setDefault(True)
            self._schedule_fit()

    # --- Ekran 4 ---------------------------------------------------------------------------------------------------
    def _show_result(self) -> None:
        out = describe_result(True, "", getattr(self.ai, "speed", None))
        self._outcome = out
        icon, color = ("circle-check", C.TURQUOISE) if out.kind == "ok" else ("circle-alert", C.ORANGE)
        self.result_header.set(out.title, out.text, icon, color, tip=out.tip)
        self._sync_result_buttons()
        self._go(self.result_page)

    def _light_available(self) -> bool:
        model = str(getattr(self.ai, "model", "") or getattr(self.progress.plan, "model", "") or "")
        return bool(model) and model != LIGHT_MODEL

    def _sync_result_buttons(self) -> None:
        out = self._outcome
        if out is None or self.ai is None:
            return
        ready = self._quick == "done"
        sec, pri = self.res_secondary, self.res_primary
        if out.kind == "slow" and self._light_available():
            sec.show()
            pri.setText(f"Hafif modeli kur ({fmt_size(MODEL_BYTES.get(LIGHT_MODEL, 6_600_000_000))})")
            pri.setEnabled(True)
        elif out.kind == "slow":
            sec.hide()
            pri.setText("Hızlı çeviride kal")
            pri.setEnabled(True)
        else:
            sec.hide()
            pri.setText("Başla" if ready else QUICK_WAIT)
            pri.setEnabled(ready)
        pri.setToolTip(START_HINT if out.kind == "ok" and not ready else "")

    def _result_primary(self) -> None:
        out = self._outcome
        if out is None:
            return
        if out.kind == "slow" and self._light_available():
            self._install_light()
        elif out.kind == "slow":
            self._stay_quick()
        else:
            self._finish()

    def _install_light(self) -> None:
        """Yavaş sonuçtan sonra hafif model: planı yeniden okunur (Ollama artık kurulu), onay ekranında "Kur"."""
        self.confirm.forget(LIGHT_MODEL)
        self.confirm.select_model(LIGHT_MODEL)
        self._survey = start_survey(self.ai, None, self._on_plan, models=(LIGHT_MODEL,))
        self._go(self.confirm)

    def _stay_quick(self) -> None:
        """"Hızlı çeviride kal": hatalı iş unutulur (panel servise bildirdi); yavaş çıkan yapay zekâ kurulduysa motor
        yine hızlı çeviriye döner. Hızlı çeviri hazırsa pencere kapanır, değilse inince kapanır."""
        self._set_choice("quickmt")
        out = self._outcome
        if out is not None and out.kind == "slow" and self.settings is not None:
            self.settings.translate.engine = "quickmt"
        if self._quick == "done":
            self.accept()
        else:
            self._auto_close = True
            self.progress.show_quick_only(back=False)
            self._go(self.progress)

    def _finish(self) -> None:
        """"Şimdi başla" / "Başla": hızlı çeviriyle ana pencere açılır; yapay zekâ (sürüyorsa) arka planda sürer."""
        if self._quick == "done":
            self.accept()

    def _set_choice(self, choice: str) -> None:
        self.choice = choice
        cfg = getattr(self.settings, "ai", None)
        if cfg is not None and hasattr(cfg, "choice"):
            cfg.choice = choice

    # --- hızlı çeviri modeli (quickmt) --------------------------------------------------------------------------------
    def _start_quick(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._quick, self._quick_frac, self._quick_error = "active", 0.0, ""
        if self.ai is None:
            self.header.set("Ücretsiz çeviri modeli indiriliyor", "Yaklaşık 400 MB, yalnızca bir kez indirilir")
        self._sync_quick()
        th = _DownloadThread()  # ebeveynsiz: pencere kapanınca çalışan iş parçacığı yok edilmesin
        th.progressed.connect(self._quick_progress)
        th.failed.connect(self._quick_failed)
        th.finished.connect(self._quick_finished)
        self._thread = th
        th.start()

    def _quick_progress(self, frac: float) -> None:
        if self._quick != "active":
            return
        self._quick_frac = max(0.0, min(1.0, frac))
        self._sync_quick(fit=False)

    def _quick_failed(self, msg: str) -> None:
        self._quick, self._quick_error = "failed", msg or "Bilinmeyen hata."
        self._sync_quick()

    def _quick_finished(self) -> None:
        if self._thread is not None:
            self._thread.wait(2000)  # run() döndü; QThread tamamen bitsin (yok edilirken çalışıyor olmasın)
        if self._quick == "failed" or not self.isVisible():
            return
        if T.quickmt_downloaded():
            self._quick, self._quick_frac = "done", 1.0
            log.info("Çeviri modeli hazır.")
            self._sync_quick()
            if self.ai is None or self._auto_close:
                self.accept()
        else:
            self._quick_failed("Model dosyaları eksik indirildi. Lütfen tekrar deneyin.\n"
                               f"Ayrıntı: {T.QUICKMT_DIR}")

    def _sync_quick(self, fit: bool = True) -> None:
        state, frac, err = self._quick, self._quick_frac, self._quick_error
        if self.ai is None:
            self.bar.setValue(round(frac * 1000))
            self.amount.setText(f"{frac * T.QUICKMT_TOTAL_MB:.0f} / {T.QUICKMT_TOTAL_MB:.0f} MB")
            failed = state == "failed"
            if failed:
                self.header.set("Kurulum tamamlanamadı", "Yaklaşık 400 MB, yalnızca bir kez indirilir")
                self.error.setText(err.splitlines()[0] if err else "Bilinmeyen hata.")
                self.error.setToolTip(err)
            self.error.setVisible(failed)
            self.retry_btn.setVisible(failed)
            if failed:
                self.retry_btn.setDefault(True)
                self.retry_btn.setFocus()
        else:
            self.quick_footer.set(state, frac, err)
            self.progress.set_quickmt(state, frac, err)
            if self.stack.currentWidget() is self.progress:
                self._primary(self.progress).setDefault(True)
            self._sync_result_buttons()
        if fit:
            self._schedule_fit()

    # --- açılış ve kapanış -----------------------------------------------------------------------------------------
    def showEvent(self, e) -> None:  # noqa: N802 - Qt
        super().showEvent(e)
        if self._started:
            return
        self._started = True
        if self._quick != "done":
            self._start_quick()
        if self.ai is not None:
            self._survey = start_survey(self.ai, self._on_advice, self._on_plan)

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt
        if self.ai is not None and e.key() == Qt.Key.Key_Escape and self.stack.currentWidget() is self.confirm:
            self._go(self.choice_page)  # Ekran 2'de Esc: bir önceki ekran
            e.accept()
            return
        super().keyPressEvent(e)

    def reject(self) -> None:
        """X ve Esc. Yapay zekâ kurulumu başladıysa (Ekran 3-4) ve hızlı çeviri hazırsa pencereyi kapatmak "Şimdi
        başla" gibidir: ana pencere açılır, yapay zekâ arka planda sürer. Aksi hâlde Çıkış."""
        if (self.ai is not None and self._quick == "done"
                and self.stack.currentWidget() in (self.progress, self.result_page)):
            self.accept()
            return
        self._quit()

    def _quit(self) -> None:
        """Çıkış: uygulama açılmaz. İndirme sürüyorsa iş parçacığı bırakılır (bkz. download_running)."""
        th = self._thread
        if th is not None and th.isRunning():
            log.info("İlk kurulum indirme sürerken kapatıldı.")
            _abandoned.append(th)
        super().reject()


def run_first_setup(settings: Settings | None, ai: AiSetup | None = None, parent: QWidget | None = None) -> bool:
    """İlk açılış. Hızlı çeviri modeli kuruluysa hemen True (pencere açılmaz, hiçbir şey inmez). Değilse pencereyi
    gösterir: model indi ve kullanıcı devam ettiyse True, çıktıysa False (o zaman ana pencere açılmamalı; indirme
    sürüyorsa çağıran `download_running()` ile süreci kapatır). `ai` verilmezse yalnız hızlı çeviri modeli iner."""
    if T.quickmt_downloaded():
        return True
    dlg = FirstRunDialog(settings, ai, parent)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    return accepted and T.quickmt_downloaded()


def ensure_translation_model(parent: QWidget | None = None) -> bool:
    """Eski giriş (app.py bugün bunu çağırır): yapay zekâsız ilk açılış, yalnız hızlı çeviri modeli."""
    return run_first_setup(None, None, parent)
