"""Ayarlar sayfası: metin okuma (OCR), kısayollar, OBS, uygulama.

Form düzeni (etiket sütunu, durum satırı, bağlantı düğmesi) Çeviri sayfasıyla ortaktır.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget

from altyazitr import APP_NAME, __version__
from altyazitr.gui.common import _tip
from altyazitr.gui.controls import Collapsible, HotkeyEdit
from altyazitr.gui.dialogs import show_obs_help
from altyazitr.gui.icons import icon
from altyazitr.gui.tokens import C, F, SPACE
from altyazitr.gui.translate_page import FIELD_W, LABEL_W, StatusLine, add_row, form, hbox, link_button, with_icon
from altyazitr.gui.widgets import Card, bind_check, bind_combo, bind_spin, page, row
from altyazitr.ocr import ENGINES
from altyazitr.paths import CONFIG_DIR, LOG_DIR, MODEL_DIR, resource_dir

log = logging.getLogger(__name__)

HOTKEYS = (("toggle", "Başlat / durdur"), ("visibility", "Gizle / göster"), ("clear", "Temizle"))


class SettingsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.ctx = win.ctx
        s = self.ctx.s
        sa, lay = page("Ayarlar")
        outer = row(sa)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)
        restart = self._restart

        # --- OCR
        ocr = Card("Metin okuma")
        f = form()
        engine = _tip(bind_combo(s.ocr, "engine", [(v[0], k) for k, v in ENGINES.items()], restart),
                      "Windows OCR çok hızlıdır ve çoğu oyunda yeterlidir. Süslü veya ince fontlarda "
                      "RapidOCR'ı deneyin.")
        engine.setMinimumWidth(FIELD_W)
        engine.setAccessibleName("Metin okuma motoru")
        add_row(f, "Motor", engine)
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
            "Akıllı: yalnızca altyazı değişince okur. Renkli veya koyu altyazılarda 'Sabit aralık'ı deneyin."))
        add_row(f, "Sabit aralık", bind_spin(s.ocr, "interval_ms", 100, 3000, restart, step=50, suffix=" ms"))
        add_row(f, "Kararlılık", _tip(bind_spin(s.ocr, "stable_frames", 1, 10, restart, suffix=" kare"),
                                      "Yazı kaç kare sabit kalınca okunsun (daktilo efektli altyazılarda artırın)"))
        add_row(f, "Parlaklık eşiği", _tip(bind_spin(s.ocr, "bright_threshold", 60, 250, restart),
                                           "Altyazı algılanmıyorsa düşürün; arka plan yüzünden sürekli "
                                           "tetikleniyorsa yükseltin"))
        add_row(f, "Büyütme", bind_combo(s.ocr, "upscale", [("Otomatik", "auto"), ("1x", "1"), ("1.5x", "1.5"),
                                                           ("2x", "2")], restart))
        add_row(f, "Tarama hızı", _tip(bind_spin(s.capture, "fps", 2, 30, restart, suffix=" kare/sn"),
                                       "10 kare/sn çoğu oyun için idealdir"))
        add_row(f, "En az harf", bind_spin(s.ocr, "min_letters", 1, 20, restart))
        add_row(f, "Gizleme gecikmesi", _tip(bind_spin(s.ocr, "hide_delay_ms", 0, 5000, self.ctx.save_later,
                                                       step=100, suffix=" ms"),
                                             "Altyazı oyundan kaybolduktan sonra çevirinin ekranda kalma süresi"))
        add_row(f, "", bind_check("Yapay zekâ metin bulucu", s.ocr, "text_detector", restart,
                                  "Açık zemindeki beyaz yazı, gölgesiz yazı gibi zor altyazıları da bulur; hiçbir "
                                  "altyazı kaçmasın diye açık önerilir. Saniyede 5 kez çalışır, işlemciyi biraz "
                                  "daha kullanır."))
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
        ver.setToolTip("MIT lisanslı açık kaynak yazılım. Bu proje Rockstar Games, Sony, OBS Project, "
                       "Google veya DeepL ile bağlantılı değildir.")
        notices = resource_dir().parent / "THIRD_PARTY_NOTICES.md"
        lic = link_button("Lisanslar", QUrl.fromLocalFile(str(notices)), icon_name="file-text",
                          tip="Açık kaynak ve üçüncü taraf lisansları")
        lic.setVisible(notices.exists())
        add_row(f, "Sürüm", layout=hbox(ver, lic, None, spacing=SPACE["m"]))
        ac.add(layout=f)
        lay.addWidget(ac)
        lay.addStretch(1)

    def _restart(self) -> None:
        self.ctx.save_later()
        self.ctx.request_restart()

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
