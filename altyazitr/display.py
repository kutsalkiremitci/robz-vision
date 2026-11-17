"""Altyazının ne zaman gösterilip gizleneceğini yönetir ve OBS sayfasına + ekran
overlay'ine iletir (tek yerde zamanlama → iki çıktı her zaman senkron).

İki çıktının görünümü ayrıdır: OBS sayfası ve OBS maskesi "Yayınımda" ayarlarını (Settings.style),
ekrandaki kutu "Ekranımda" ayarlarını (Settings.screen_style) kullanır. Arayüz, iki görünümden
herhangi biri değişince yalnızca push_style() çağırır."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from altyazitr import APP_NAME
from altyazitr.capture import monitor_fraction
from altyazitr.config import Settings
from altyazitr.overlay import ScreenOverlay
from altyazitr.server import AsyncBridge, OverlayServer, style_vars

log = logging.getLogger(__name__)

CHARS_PER_SEC = 17.0  # okuma hızı (Netflix yetişkin standardı)


class SubtitleDisplay(QObject):
    visibility_changed = Signal(bool)

    def __init__(self, settings: Settings, bridge: AsyncBridge, server: OverlayServer,
                 overlay: ScreenOverlay):
        super().__init__()
        self.s = settings
        self.bridge = bridge
        self.server = server
        self.overlay = overlay
        self.visible = True
        self.current: dict | None = None
        self.shown_at = 0.0
        self._hide = QTimer(self, singleShot=True, timeout=self._do_hide)
        self._test_active = False
        self._mask_key: tuple | None = None
        self._mask_frac: tuple[float, float, float, float] | None = None

    # --- işlem hattından gelenler ------------------------------------------
    def on_subtitle(self, ev: dict) -> None:
        self._test_active = False
        self._hide.stop()
        same_text = self.current is not None and self.current.get("text") == ev["text"]
        self.current = ev
        if not same_text:
            self.shown_at = time.monotonic()
        self._push()

    def on_gone(self) -> None:
        if self.current is None or self._test_active:
            return
        elapsed = time.monotonic() - self.shown_at
        min_disp = max(1.2, min(6.0, len(self.current.get("text", "")) / CHARS_PER_SEC))
        delay = max(self.s.ocr.hide_delay_ms / 1000.0, min_disp - elapsed)
        self._hide.start(int(delay * 1000))

    def on_same(self) -> None:
        if self.current is not None and self._hide.isActive():
            self._hide.stop()

    # --- kullanıcı eylemleri ------------------------------------------------
    def clear(self) -> None:
        self._hide.stop()
        self._do_hide()

    def toggle_visible(self) -> bool:
        self.visible = not self.visible
        self._push()
        self.visibility_changed.emit(self.visible)
        return self.visible

    def show_test(self) -> None:
        """Örnek altyazıyı 5 sn iki çıktıda birden gösterir; her biri kendi görünümüyle çizilir
        (OBS sayfası yayın görünümüyle, ekrandaki kutu ekran görünümüyle)."""
        self._test_active = True
        self._hide.stop()
        self.current = {"text": "Bu bir test altyazısıdır — ğüşıöç ĞÜŞİÖÇ. Ayarları buradan kontrol edin!",
                        "speaker": APP_NAME, "original": "This is a test subtitle. Check your settings here!"}
        self.shown_at = time.monotonic()
        self._push()
        self._hide.start(5000)

    def push_style(self) -> None:
        """Görünüm ayarlarından biri değişince çağrılır (hangisi olduğu fark etmez):
        * yayın görünümü (s.style) → OBS sayfasına CSS değişkenleri; maske de yeniden hesaplanır,
        * ekran görünümü (s.screen_style) → kutu yeniden çizilir. Kutu görünümü (geçiş süresi dahil)
          her çizimde/geçişte ayarlardan okur; önbellekte bir şey tutmaz."""
        self.bridge.submit(self.server.set_style(style_vars(self.s.style)))
        self.overlay.update()
        self._push()

    # --- iç ------------------------------------------------------------------
    def _mask_rect(self) -> list[float] | None:
        """OBS'teki maske: bölgenin yayın karesindeki oransal konumu. Ekran modunda bölgenin
        bulunduğu monitöre, pencere modunda pencerenin istemci alanına göre hesaplanır.
        Maske yalnızca OBS'te olduğu için yayın görünümünü (s.style) kullanır."""
        st = self.s.style
        c = self.s.capture
        if c.mode == "window":
            frac = tuple(c.region)
        else:
            key = tuple(c.rect)
            if key != self._mask_key:
                self._mask_key, self._mask_frac = key, monitor_fraction(c.rect)
            frac = self._mask_frac
        if not frac:
            return None
        x, y, w, h = frac
        pad = st.mask_pad_percent / 100.0
        x0, y0 = max(0.0, x - pad), max(0.0, y - pad)
        x1, y1 = min(1.0, x + w + pad), min(1.0, y + h + pad)
        return [x0, y0, x1 - x0, y1 - y0]

    def _push(self) -> None:
        cur = self.current if self.visible else None
        if cur:
            text, speaker, original = cur.get("text", ""), cur.get("speaker"), cur.get("original", "")
        else:
            text, speaker, original = "", None, ""
        self.bridge.submit(self.server.set_subtitle(text, speaker, original))
        mask = self._mask_rect() if (cur and self.s.style.mask_original) else None  # yayın görünümü
        self.bridge.submit(self.server.set_mask(mask))
        if text:
            self.overlay.set_subtitle(text, speaker, original)
        else:
            self.overlay.clear()

    def _do_hide(self) -> None:
        self._test_active = False
        self.current = None
        self._push()
