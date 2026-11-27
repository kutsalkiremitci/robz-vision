"""Ayarlar sayfası: altyazı akışı, metin okuma (OCR), kısayollar, OBS, uygulama (güncellemeler, sürüm, lisanslar,
geliştiricinin Kick ve YouTube bağlantıları).

Güncellemeler (`ctx.updater`, altyazitr.update_service): "Yeni sürümleri denetle" (saatte bir; kapatılabilir), Şimdi
denetle ve son denetimin sonucu; yeni sürüm varken "Güncelle…" güncelleme penceresini açar.

Form düzeni (etiket sütunu, durum satırı, bağlantı düğmesi) Çeviri sayfasıyla ortaktır.

Altyazı akışı burada durur, Görünüm'de değil: Görünüm sayfasındaki her denetim yalnız üstte seçili görünümü
(Ekranımda ya da Yayınımda) düzenler; akış ise iki çıktıda ortaktır ve bir görünüm değil, davranıştır.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget

from altyazitr import APP_NAME, __version__
from altyazitr.gui import developer
from altyazitr.gui.common import _tip
from altyazitr.gui.controls import Collapsible, HotkeyEdit, Segmented
from altyazitr.gui.dialogs import show_obs_help
from altyazitr.gui.icons import icon
from altyazitr.gui.tokens import C, F, SPACE
from altyazitr.gui.translate_page import FIELD_W, LABEL_W, StatusLine, add_row, form, hbox, link_button, with_icon
from altyazitr.gui.update_dialog import settings_status
from altyazitr.gui.widgets import Card, bind_check, bind_combo, bind_spin, page, row
from altyazitr.ocr import ENGINES
from altyazitr.paths import CONFIG_DIR, LOG_DIR, MODEL_DIR, resource_dir

log = logging.getLogger(__name__)

HOTKEYS = (("toggle", "Başlat / durdur"), ("visibility", "Gizle / göster"), ("clear", "Temizle"))

FLOW_TIP = "Ekranınızdaki kutuda ve OBS'te aynı çalışır"
FLOW_MODE_TIPS = ("Son çeviriler alt alta kalır; hızlı konuşmada satır kaybolmaz",
                  "Yalnız en son çeviri görünür; yeni satır gelince öncekinin yerine geçer")
FLOW_LINES_TIP = ("Aynı anda en fazla kaç çeviri alt alta dursun. Yeni satır gelince en eskisi kalkar. "
                  "Ekranınızdaki kutu küçükse yalnızca en yeni satırlar sığar: Canlı → Konumu ayarla ile "
                  "kutuyu yukarı doğru büyütün.")
FLOW_MIN_TIP = "Her çeviri, oyun sonraki satıra geçse de en az bu kadar ekranda kalır. Yetişemiyorsanız artırın."
FLOW_MAX_TIP = ("Oyundaki yazı ekranda dursa bile çeviri en fazla bu kadar görünür. Uzun satırlara okuma süresi "
                "kadar pay tanınır.")
UPDATE_CHECK_TIP = ("Saatte bir GitHub'a kısa bir istek gönderir; yalnız son sürümün numarası okunur, kişisel bilgi "
                    "gönderilmez. Yeni sürüm yalnız siz Güncelle'ye basınca iner ve kurulur.")
MIN_SHOW_RANGE = (1.5, 6.0)  # en az gösterim (sn)
MAX_SHOW_RANGE = (3.0, 20.0)  # en uzun kalma (sn)


class FlowCard(Card):
    """Altyazı akışı (Settings.flow): Akış | Tek satır, en fazla satır, en az gösterim ve en uzun kalma süresi.
    Değişiklik kaydedilir ve hemen iki çıktıya uygulanır (display.push_style). En uzun kalma iki biçimde de
    geçerlidir; en az gösterim onu geçemez (iki sayı kutusunun aralığı birbirine bağlı)."""

    def __init__(self, ctx):
        super().__init__("Altyazı akışı")
        self.ctx = ctx
        fl = ctx.s.flow
        self.setToolTip(FLOW_TIP)
        f = form()
        self.mode = Segmented([("Akış", "flow"), ("Tek satır", "single")])
        for i, tip in enumerate(FLOW_MODE_TIPS):
            self.mode.group.button(i).setToolTip(tip)
        self.mode.setAccessibleName("Altyazı akışı")
        self.mode.setValue(fl.mode if fl.mode in ("flow", "single") else "flow")
        self.mode.changed.connect(self._set_mode)
        add_row(f, "Biçim", self.mode)
        self.lines = _tip(bind_spin(fl, "max_lines", 2, 4, self._changed, suffix=" satır"), FLOW_LINES_TIP)
        self.lines.setAccessibleName("En fazla satır")
        add_row(f, "En fazla satır", self.lines, tip=FLOW_LINES_TIP)
        self.min_show = _tip(bind_spin(fl, "min_show_s", *MIN_SHOW_RANGE, self._changed, step=0.5, suffix=" sn",
                                       decimals=1), FLOW_MIN_TIP)
        self.min_show.setAccessibleName("En az gösterim süresi")
        add_row(f, "En az gösterim", self.min_show, tip=FLOW_MIN_TIP)
        self.max_show = _tip(bind_spin(fl, "max_show_s", *MAX_SHOW_RANGE, self._changed, step=0.5, suffix=" sn",
                                       decimals=1), FLOW_MAX_TIP)
        self.max_show.setAccessibleName("En uzun kalma süresi")
        add_row(f, "En uzun kalma", self.max_show, tip=FLOW_MAX_TIP)
        spins = (self.lines, self.min_show, self.max_show)
        w = max(x.sizeHint().width() for x in spins)
        for x in spins:  # sayı kutuları aynı genişlikte
            x.setFixedWidth(w)
        for x in (self.min_show, self.max_show):
            x.valueChanged.connect(self._sync_limits)
        self._sync_limits()
        # en fazla satır ve en az gösterim yalnız akışta işe yarar; en uzun kalma iki biçimde de geçerli
        self._flow_only = [self.lines, self.min_show, f.labelForField(self.lines), f.labelForField(self.min_show)]
        self.add(layout=f)
        self._sync_enabled()

    def _sync_limits(self) -> None:
        """En az gösterim en uzun kalmayı geçemez: iki sayı kutusu birbirinin değerini sınır alır."""
        self.max_show.setMinimum(max(MAX_SHOW_RANGE[0], self.min_show.value()))
        self.min_show.setMaximum(min(MIN_SHOW_RANGE[1], self.max_show.value()))

    def _sync_enabled(self) -> None:
        on = self.ctx.s.flow.mode != "single"
        for w in self._flow_only:
            if w is not None:
                w.setEnabled(on)

    def _set_mode(self, mode) -> None:
        if mode == self.ctx.s.flow.mode:
            return
        self.ctx.s.flow.mode = mode
        self._sync_enabled()
        self._changed()

    def _changed(self) -> None:
        self.ctx.save_later()
        self.ctx.display.push_style()


class SettingsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = win.ctx
        s = self.ctx.s
        sa, lay = page("Ayarlar", "Kısayollar, altyazı akışı ve uygulama")
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)
        restart = self._restart

        # --- altyazı akışı: iki çıktıda ortak davranış (bkz. modül açıklaması)
        self.flow = FlowCard(self.ctx)
        lay.addWidget(self.flow)

        # --- OCR
        ocr = Card("Metin okuma")
        f = form()
        engine = _tip(bind_combo(s.ocr, "engine", [(v[0], k) for k, v in ENGINES.items()], restart),
                      "RapidOCR önerilir: daha doğru okur, tek satırı onlarca milisaniyede okur. Windows OCR için "
                      "Windows'ta İngilizce OCR dil paketi gerekir.")
        engine.setMinimumWidth(FIELD_W)
        engine.setAccessibleName("Metin okuma motoru")
        add_row(f, "Motor", engine)
        add_row(f, "", bind_check("Yalnız altyazıları oku", s.ocr, "subtitle_only", self.ctx.save_later,
                                  tip="Plaka, tabela, duvar yazısı gibi oyun içi yazıları atlar; beyaz, kenarlı, ortalı "
                                      "yazıyı okur. Altyazısı renkli ya da kenarsız bir oyunda kapatın."))
        ocr.add(layout=f)
        adv = Collapsible("Gelişmiş")
        f = form(inside=adv)
        add_row(f, "Ön işleme", _tip(bind_combo(s.ocr, "preprocess", [
            ("Otomatik (önerilen)", "auto"), ("Kenarlı yazıyı ayır", "outline"), ("Parlak metni ayır", "bright"),
            ("Yok", "none")], restart),
            "Otomatik: yazıyı birkaç yolla ayırıp okur, daha çok anlamlı kelime çıkanı seçer. "
            "Kenarlı yazıyı ayır: rengi ne olursa olsun koyu kenarlı ya da koyu zemindeki yazı; asfalt, çimen gibi "
            "kumlu arka planda da çalışır."))
        add_row(f, "Algılama", _tip(bind_combo(s.ocr, "detect_mode", [
            ("Akıllı (önerilen)", "smart"), ("Sabit aralık", "interval")], restart),
            "Akıllı: altyazı belirince ya da değişince hemen okur. Sabit aralık: bunun yanında yazı dururken de "
            "her aralıkta bir kez okur (işlemciyi biraz daha kullanır)."))
        add_row(f, "Sabit aralık", _tip(bind_spin(s.ocr, "interval_ms", 100, 3000, restart, step=50, suffix=" ms"),
                                        "Sabit aralık algılamasında iki okuma arasındaki en uzun süre"))
        add_row(f, "Kararlılık", _tip(bind_spin(s.ocr, "stable_frames", 1, 10, restart, suffix=" kare"),
                                      "Yazı belirdikten sonra kaç kare aynı kalınca okunsun. Belirme ve daktilo "
                                      "efektleri kendiliğinden beklenir; 1 önerilir."))
        add_row(f, "Parlaklık eşiği", _tip(bind_spin(s.ocr, "bright_threshold", 60, 250, restart),
                                           "Yalnız yapay zekâ metin bulucu kapalıyken: altyazı algılanmıyorsa "
                                           "düşürün; arka plan yüzünden sürekli tetikleniyorsa yükseltin"))
        add_row(f, "Büyütme", bind_combo(s.ocr, "upscale", [("Otomatik", "auto"), ("1x", "1"), ("1.5x", "1.5"),
                                                           ("2x", "2")], restart))
        add_row(f, "Tarama hızı", _tip(bind_spin(s.capture, "fps", 2, 30, restart, suffix=" kare/sn"),
                                       "20 kare/sn önerilir: yazı en geç bir kare sonra görülür. Zayıf "
                                       "bilgisayarda metin bulucu kendi yükünü sınırlar."))
        add_row(f, "En az harf", bind_spin(s.ocr, "min_letters", 1, 20, restart))
        add_row(f, "Gizleme gecikmesi", _tip(bind_spin(s.ocr, "hide_delay_ms", 0, 5000, self.ctx.save_later,
                                                       step=100, suffix=" ms"),
                                             "Altyazı oyundan kaybolduktan sonra çevirinin ekranda kalma süresi"))
        add_row(f, "", bind_check("Yapay zekâ metin bulucu", s.ocr, "text_detector", restart,
                                  "Yazının yerini harf şeklinden bulur: açık zeminde beyaz, gölgesiz yazı da. "
                                  "Altyazının belirdiği ve değiştiği kareyi yakalar; hiçbir altyazı kaçmasın ve "
                                  "hızlı okunsun diye açık önerilir. Kapalıysa kurallı algılama kullanılır."))
        add_row(f, "", bind_check("Yalnızca en alttaki satırları oku", s.ocr, "keep_lowest_block", restart,
                                  tip="Bölgedeki üst satırları (HUD, görev yazısı) yok sayar"))
        add_row(f, "", bind_check("Konuşmacı adını ayır", s.ocr, "detect_speaker", restart,
                                  tip="'LUCIA: …' gibi adlar çevrilmez ve renkli gösterilir"))
        adv.add(layout=f)
        ocr.add(adv)
        lay.addWidget(ocr)

        # --- kısayollar
        hk = s.hotkeys
        kc = Card("Kısayollar")
        kc.setToolTip("Oyun odaktayken de çalışır. Oyun yönetici olarak çalışıyorsa bu uygulamayı da "
                      "yönetici olarak açın.")
        f = form()
        self.edits: dict[str, HotkeyEdit] = {}
        for attr, title in HOTKEYS:
            e = HotkeyEdit(getattr(hk, attr))
            e.setMinimumWidth(240)
            e.setAccessibleName(f"{title} kısayolu")
            e.addAction(icon("keyboard", C.TEXT_3), QLineEdit.ActionPosition.LeadingPosition)
            self.edits[attr] = e
            add_row(f, title, e)
        apply = with_icon(QPushButton("Kaydet"), "check", C.INVERSE)
        apply.setObjectName("primary")
        apply.setToolTip("Kısayolları kaydeder ve hemen etkinleştirir")
        apply.clicked.connect(self._apply_hotkeys)
        self.hk_line = StatusLine()
        add_row(f, "", layout=hbox(apply, self.hk_line, None, spacing=SPACE["m"]))
        kc.add(layout=f)
        self._show_hotkey_errors(self.ctx.hotkey_errors)
        lay.addWidget(kc)

        # --- OBS
        oc = Card("OBS")
        f = form()
        port = _tip(bind_spin(s.server, "port", 1024, 65535, self.ctx.save_later),
                    "Değişiklik uygulama yeniden başlatılınca geçerli olur")
        port.setAccessibleName("OBS sunucu portu")
        helpb = with_icon(QPushButton("OBS'e nasıl eklenir?"), "help")
        helpb.clicked.connect(lambda: show_obs_help(self, self.ctx.server.url))
        add_row(f, "Port", layout=hbox(port, helpb, None, spacing=SPACE["m"]))
        oc.add(layout=f)
        if self.ctx.server_error:
            err = StatusLine(color=C.ORANGE)
            err.text.setWordWrap(True)
            err.layout().setAlignment(err.icon, Qt.AlignmentFlag.AlignTop)
            err.setContentsMargins(LABEL_W + SPACE["l"], 0, 0, 0)  # alan sütununa hizalı, tam genişlikte kırılır
            err.set(self.ctx.server_error.split("\n")[0], "warn", tip=self.ctx.server_error)
            oc.add(err)
        lay.addWidget(oc)

        # --- uygulama
        ac = Card("Uygulama")
        f = form()
        add_row(f, "Pencereyi kapatınca", bind_check("Sistem tepsisinde çalışmaya devam et", s, "minimize_to_tray",
                                                    self.ctx.save_later,
                                                    tip="İşaretli değilse pencereyi kapatmak uygulamadan çıkar"))

        # güncellemeler: saatte bir denetim (kapatılabilir), Şimdi denetle, son denetimin sonucu
        self._upd = getattr(self.ctx, "updater", None)  # araçlarda ve bazı testlerde yok
        self.update_check = bind_check("Yeni sürümleri denetle", s.update, "auto_check", self._update_auto_changed,
                                       tip=UPDATE_CHECK_TIP)
        self.update_check.setAccessibleName("Yeni sürümleri denetle")
        add_row(f, "Güncellemeler", self.update_check)
        self.update_now = with_icon(QPushButton("Şimdi denetle"), "refresh")
        self.update_now.setToolTip("GitHub'da yeni sürüm olup olmadığına şimdi bakar")
        self.update_now.clicked.connect(self._check_updates_now)
        self.update_open = with_icon(QPushButton("Güncelle…"), "download")
        self.update_open.setToolTip("Güncelleme penceresini açar: neler yeni, güncelle")
        self.update_open.clicked.connect(self._open_update)
        add_row(f, "", layout=hbox(self.update_now, self.update_open, None, spacing=SPACE["m"]))
        self.update_line = StatusLine(auto_hide=False)
        add_row(f, "", self.update_line)
        if self._upd is not None:
            self._upd.changed.connect(self._sync_update_status)
        self._sync_update_status()

        def opener(path):
            return lambda: (os.makedirs(path, exist_ok=True), QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))

        folders = []
        for text, path in (("Ayarlar", CONFIG_DIR), ("Modeller", MODEL_DIR), ("Günlükler", LOG_DIR)):
            b = with_icon(QPushButton(text), "folder")
            b.setToolTip(str(path))
            b.setAccessibleName(f"{text} klasörünü aç")
            b.clicked.connect(opener(path))
            folders.append(b)
        add_row(f, "Klasörler", layout=hbox(*folders, None))

        tour_btn = with_icon(QPushButton("Tanıtım turunu başlat"), "play-circle")
        tour_btn.setToolTip("Arayüzün bölümlerini adım adım gösterir")
        tour_btn.clicked.connect(self._start_tour)
        add_row(f, "Tanıtım", layout=hbox(tour_btn, None))

        ver = QLabel(f"{APP_NAME} <span style=\"font-family:'{F.DISPLAY}'\">{__version__}</span>")
        ver.setObjectName("muted")
        ver.setToolTip("MIT lisanslı açık kaynak yazılım. Bu proje Rockstar Games, Sony, OBS Project, Kick, "
                       "Google veya DeepL ile bağlantılı değildir.")
        notices = resource_dir().parent / "THIRD_PARTY_NOTICES.md"
        lic = link_button("Lisanslar", QUrl.fromLocalFile(str(notices)), icon_name="file-text",
                          tip="Açık kaynak ve üçüncü taraf lisansları")
        lic.setVisible(notices.exists())
        add_row(f, "Sürüm", layout=hbox(ver, lic, None, spacing=SPACE["m"]))

        # geliştirici: Kick ve YouTube kanalları (bkz. gui/developer.py); Kick yeşili yalnız logoda
        dev = QLabel(developer.DEVELOPER)
        dev.setObjectName("muted")
        self.kick_link = link_button("Kick'te takip et", developer.KICK_URL, tip=developer.FOLLOW_TIP)
        self.kick_link.setIcon(developer.kick_icon())
        self.kick_link.setAccessibleName(developer.FOLLOW_TEXT)
        self.youtube_link = link_button("YouTube", developer.YOUTUBE_URL, tip=developer.YOUTUBE_TIP)
        self.youtube_link.setIcon(developer.youtube_icon())
        self.youtube_link.setAccessibleName(developer.YOUTUBE_TIP)
        add_row(f, "Geliştirici", layout=hbox(dev, self.kick_link, self.youtube_link, None, spacing=SPACE["m"]))
        ac.add(layout=f)
        lay.addWidget(ac)
        lay.addStretch(1)

    def _restart(self) -> None:
        self.ctx.save_later()
        self.ctx.request_restart()

    # --- güncellemeler ------------------------------------------------------------------------------------------
    def _update_auto_changed(self) -> None:
        self.ctx.save_later()
        if self._upd is not None:
            self._upd.set_auto(self.ctx.s.update.auto_check)

    def _check_updates_now(self) -> None:
        if self._upd is not None:
            self._upd.check_now(manual=True)

    def _open_update(self) -> None:
        opener = getattr(self.win, "open_update_dialog", None)
        if callable(opener):
            opener()

    def _sync_update_status(self) -> None:
        upd = self._upd
        text, kind, tip = settings_status(upd, self.ctx.s)
        self.update_line.set(text, kind, tip=tip)
        self.update_now.setEnabled(upd is not None and not upd.checking)
        self.update_open.setVisible(upd is not None and upd.available is not None and upd.phase != "installing")

    def _start_tour(self) -> None:
        try:
            from altyazitr.gui import tour

            tour.start_tour(self.win)
        except Exception:
            log.exception("Tanıtım turu başlatılamadı")

    def _apply_hotkeys(self) -> None:
        hk = self.ctx.s.hotkeys
        for attr, e in self.edits.items():
            setattr(hk, attr, e.sequence())
        errs = self.ctx.register_hotkeys()
        self.ctx.save_later()
        self._show_hotkey_errors(errs, saved=True)

    def _show_hotkey_errors(self, errs: list[str], saved: bool = False) -> None:
        if errs:
            self.hk_line.set(f"{len(errs)} kısayol kullanılamadı", "warn",
                             tip="\n".join(errs) + "\n\nBaşka bir program aynı tuşları kullanıyor; "
                                 "farklı bir kombinasyon seçin.")
        else:
            self.hk_line.set("Kaydedildi" if saved else "", "ok", tip="")
