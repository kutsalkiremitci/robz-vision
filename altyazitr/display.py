"""Altyazının ne zaman gösterilip gizleneceğini yönetir ve OBS sayfasına + ekran
overlay'ine iletir (tek yerde zamanlama → iki çıktı her zaman senkron).

Akış (Settings.flow): hızlı konuşmada hiçbir çeviri okunmadan kaybolmasın diye son satırlar alt alta
durur, en yenisi en altta. Kurallar:
* İşlem hattından gelen olayın "line" kimliği vardır (hep artar, işlem hattı yeniden başlasa da). Yeni
  kimlik → yığının altına yeni satır; ekrandaki bir satırın kimliği → o satır yerinde güncellenir, süreleri
  baştan başlamaz. Aynı okumanın daha iyi çevirisi (karma mod; oyun geçtikten sonra da gelebilir) yalnız
  yazıyı değiştirir; oyundaki satırın yeni bir okuması (uzayan metin, daha temiz okuma) ayrıca oyunun onu
  hâlâ gösterdiğini bildirir. Ekrandan çıkmış (süresi dolmuş, düşmüş, temizlenmiş) satır geri gelmez.
  Daha yenisinden sonra ilk kez gelen eski satır akışta sırasına girer (son satırlardansa), tek satır
  modunda gösterilmez. Kimlik yoksa (eski işlem hattı) yazı ya da orijinal değişince yeni satır sayılır.
* Yığın doluysa (flow.max_lines) yeni satır gelince en eskisi düşer.
* Her satır, doğduğu andan itibaren en az max(flow.min_show_s, okuma süresi) ekranda kalır; oyun sonraki
  satıra geçse bile. Satırın yazısı değişirse okuma süresi o andan yeniden sayılır.
* En yeni satır ayrıca akıştan önceki kuralı izler: oyundaki altyazı durdukça kalır; kaybolunca (on_gone)
  en az ocr.hide_delay_ms ve en az okuma süresi kadar daha görünür.
* "Tek satır" modunda yığın tek satırdır ve her şey akıştan önceki gibidir (min_show_s kullanılmaz).
* Süreler QTimer ile dolar (tek zamanlayıcı, her zaman bir sonraki süre dolumuna kurulur; yoklama yok).
* OBS maskesi oyundaki altyazıya bağlı kalır: en yeni satır oyunda göründükçe ve akıştan önceki gizleme
  süresi dolana kadar açıktır; ekranda yalnız eski satırlar kaldıysa kapanır.

İki çıktının görünümü ayrıdır: OBS sayfası ve OBS maskesi "Yayınımda" ayarlarını (Settings.style),
ekrandaki kutu "Ekranımda" ayarlarını (Settings.screen_style) kullanır. Arayüz, iki görünümden herhangi
biri ya da akış ayarları (Settings.flow) değişince yalnızca push_style() çağırır."""

from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from altyazitr import APP_NAME
from altyazitr.capture import monitor_fraction
from altyazitr.config import Settings
from altyazitr.overlay import ScreenOverlay
from altyazitr.render import stack_limit
from altyazitr.server import AsyncBridge, OverlayServer, style_vars

log = logging.getLogger(__name__)

CHARS_PER_SEC = 17.0  # okuma hızı (Netflix yetişkin standardı)
TEST_S = 5.0  # Test düğmesinin örnek altyazısı bu kadar görünür
_EPS = 0.005  # zamanlayıcı birkaç ms erken uyanırsa da süresi dolan satır gitsin
_TEST_ID = -1_000_000  # örnek satırların kimlikleri (işlem hattınınkilerle ve kendi kimliklerimizle karışmaz)
_LEFT_MAX = 32  # ekrandan çıkan satırlardan bu kadarı hatırlanır (geç gelen çeviriler için)

# Test düğmesi, eskiden yeniye. Akışta ayardaki satır sayısı kadar en yenisi (varsayılan 3), tek satır
# modunda yalnızca sonuncusu gösterilir.
TEST_LINES = (
    ("Çabuk ol, polisler peşimizde!", "JASON", "Hurry up, the cops are on our tail!"),
    ("Arabaya bin, Vice City'ye gidiyoruz.", "LUCIA", "Get in the car, we're going to Vice City."),
    ("Köprüyü geçince sola dön, sonra güneye ilerle.", None, "Turn left after the bridge, then head south."),
    ("Bu bir test altyazısıdır — ğüşıöç ĞÜŞİÖÇ. Ayarları buradan kontrol edin!", APP_NAME,
     "This is a test subtitle. Check your settings here!"),
)


def reading_time(text: str) -> float:
    """Bir satırı okumak için gereken süre (sn): uzunluğa göre 1,2 ile 6 sn arası."""
    return max(1.2, min(6.0, len(text) / CHARS_PER_SEC))


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
        # Ekrandaki satırlar, eskiden yeniye. Her biri: line (kimlik), text, speaker, original, born (ilk
        # göründüğü an), changed (yazının son değiştiği an), gone (oyunda kaybolduğu an; yalnız en yeni satır
        # için anlamlı), until (yalnız örnek satırlar: kesin bitiş).
        self.lines: list[dict] = []
        self._live: dict | None = None  # oyunun şu an gösterdiği satır (yığının en yenisi); gidince None
        self._hide = QTimer(self, singleShot=True, timeout=self._tick)  # bir sonraki süre dolumu
        self._hide.setTimerType(Qt.TimerType.PreciseTimer)
        self._test_active = False
        self._max_line: int | None = None  # gösterilen en yeni işlem hattı kimliği
        self._auto_id = 0  # olayda "line" yoksa kendi kimliklerimiz (negatif)
        self._left: OrderedDict[int, None] = OrderedDict()  # ekrandan çıkan satırların kimlikleri (son birkaçı)
        self._pushed: tuple | None = None
        self._mask_key: tuple | None = None
        self._mask_frac: tuple[float, float, float, float] | None = None
        self.clock = time.monotonic  # testlerde sahte saatle değiştirilebilir

    @property
    def current(self) -> dict | None:
        """En yeni (en alttaki) satır; yoksa None."""
        return self.lines[-1] if self.lines else None

    # --- işlem hattından gelenler ------------------------------------------
    def on_subtitle(self, ev: dict) -> None:
        text = str(ev.get("text") or "")
        if not text:
            return
        now = self.clock()
        original = str(ev.get("original") or "")
        lid, known = self._line_id(ev, text, original)
        ln = next((x for x in self.lines if x["line"] == lid and x["until"] is None), None)
        late = ln is None and known and self._max_line is not None and lid < self._max_line
        if ln is None and (lid in self._left or (late and not self._late_fits(lid))):
            return  # ekrandan çıkmış satır geri getirilmez; eski satır ancak son satırlardansa gösterilir
        if self._test_active:  # örnek satırlar gerçeklerle karışmasın
            self._test_active = False
            self.lines = []
            self._live = None
        if ln is not None:
            self._update(ln, ev, text, original, now)
        elif late:
            self._insert_late(lid, ev, text, original, now)
        else:
            limit = stack_limit(self.s.flow)
            while len(self.lines) >= limit:  # yığın dolu: en eski düşer
                self._remove(self.lines[0])
            ln = self._new_line(lid, ev, text, original, now)
            self.lines.append(ln)
            self._live = ln
            if known:
                self._max_line = lid
        self._refresh()

    def on_gone(self) -> None:
        """Oyundaki altyazı kayboldu: en yeni satır gizleme süresini saymaya başlar."""
        if self._test_active or self._live is None:
            return
        self._live["gone"] = self.clock()
        self._refresh()

    def on_same(self) -> None:
        """Oyundaki altyazı hâlâ aynı (ya da geri geldi): bekleyen gizleme iptal."""
        if self._test_active or self._live is None or self._live["gone"] is None:
            return
        self._live["gone"] = None
        self._refresh()

    # --- kullanıcı eylemleri ------------------------------------------------
    def clear(self) -> None:
        """Ekrandaki satırları kaldırır; geç gelen çeviriler onları geri getirmez."""
        self._test_active = False
        for ln in list(self.lines):
            self._remove(ln)
        self._refresh()

    def toggle_visible(self) -> bool:
        self.visible = not self.visible
        self._push()
        self.visibility_changed.emit(self.visible)
        return self.visible

    def show_test(self) -> None:
        """Örnek altyazıyı 5 sn iki çıktıda birden gösterir; her biri kendi görünümüyle çizilir
        (OBS sayfası yayın görünümüyle, ekrandaki kutu ekran görünümüyle). Akışta ayardaki satır sayısı
        kadar örnek satır alt alta görünür (varsayılan 3), tek satır modunda bir satır."""
        now = self.clock()
        n = min(stack_limit(self.s.flow), len(TEST_LINES))
        first = len(TEST_LINES) - n
        self._test_active = True
        self._live = None
        self.lines = []
        for j in range(first, len(TEST_LINES)):
            text, speaker, original = TEST_LINES[j]
            self.lines.append({"line": _TEST_ID - j, "text": text, "speaker": speaker, "original": original,
                               "born": now, "changed": now, "gone": None, "until": now + TEST_S})
        self._refresh()

    def push_style(self) -> None:
        """Görünüm ya da akış ayarlarından biri değişince çağrılır (hangisi olduğu fark etmez):
        * yayın görünümü (s.style) → OBS sayfasına CSS değişkenleri; maske de yeniden hesaplanır,
        * ekran görünümü (s.screen_style) → kutu yeniden çizilir. Kutu görünümü (geçiş süresi dahil)
          her çizimde/geçişte ayarlardan okur; önbellekte bir şey tutmaz,
        * akış (s.flow) → satır sayısı azaldıysa en eskiler düşer, süreler yeni ayarlarla yeniden kurulur."""
        self.bridge.submit(self.server.set_style(style_vars(self.s.style)))
        self.overlay.update()
        self._refresh(force=True)

    # --- iç ------------------------------------------------------------------
    def _line_id(self, ev: dict, text: str, original: str) -> tuple[int, bool]:
        """Olayın satır kimliği ve kimliğin işlem hattından gelip gelmediği."""
        lid = ev.get("line")
        if lid is not None:
            try:
                return int(lid), True
            except (TypeError, ValueError):
                pass
        # Kimlik yok: en yeni satırla aynı yazı ya da aynı (veya uzayan) orijinal → aynı satır; değilse yeni satır
        new = self.current
        if new is not None and new["until"] is None and (
                new["text"] == text or (new["original"] and original.startswith(new["original"]))):
            return new["line"], False
        self._auto_id -= 1
        return self._auto_id, False

    @staticmethod
    def _new_line(lid: int, ev: dict, text: str, original: str, now: float) -> dict:
        return {"line": lid, "text": text, "speaker": ev.get("speaker"), "original": original,
                "born": now, "changed": now, "gone": None, "until": None}

    def _update(self, ln: dict, ev: dict, text: str, original: str, now: float) -> None:
        """Ekrandaki satırın güncellemesi: yeri ve süreleri korunur. Aynı okumanın daha iyi çevirisi (karma mod;
        oyun geçtikten sonra da gelebilir) yalnız yazıyı değiştirir. Oyundaki satırın yeni bir okuması (orijinal
        uzadı ya da düzeldi) oyunun onu hâlâ gösterdiğini bildirir: bekleyen gizleme iptal olur, okuma süresi
        yeni yazıdan sayılır (akıştan önceki gibi)."""
        if original != ln["original"] and ln is self._live:
            if text != ln["text"]:
                ln["changed"] = now
            ln["gone"] = None
        ln.update(text=text, speaker=ev.get("speaker"), original=original)

    def _late_fits(self, lid: int) -> bool:
        """Daha yenisinden sonra ilk kez gelen eski satır gösterilebilir mi: tek satır modunda hayır; akışta
        son satırlardansa (ekranda olabilecek kadar yeni) ve yığının en eskisi olmayacaksa evet."""
        limit = stack_limit(self.s.flow)
        if self.s.flow.mode == "single" or self._max_line is None or lid <= self._max_line - limit:
            return False
        real = [x for x in self.lines if x["until"] is None]
        return len(real) < limit or any(x["line"] < lid for x in real)

    def _insert_late(self, lid: int, ev: dict, text: str, original: str, now: float) -> None:
        """Eski satırı (ör. hızlı çevirisi çıkmamış satırın asıl çevirisi) kimlik sırasına yerleştirir; oyun onu
        göstermediği için en yeni sayılmaz ve yalnız kendi süresi kadar kalır."""
        limit = stack_limit(self.s.flow)
        i = len(self.lines)
        while i > 0 and self.lines[i - 1]["line"] > lid:
            i -= 1
        self.lines.insert(i, self._new_line(lid, ev, text, original, now))
        while len(self.lines) > limit:  # yığın dolu: en eski düşer (yeni satır en eski olamaz, bkz. _late_fits)
            self._remove(self.lines[0])

    def _remove(self, ln: dict) -> None:
        self.lines = [x for x in self.lines if x is not ln]
        if ln is self._live:
            self._live = None
        if ln["until"] is None:
            self._left[ln["line"]] = None
            self._left.move_to_end(ln["line"])
            while len(self._left) > _LEFT_MAX:
                self._left.popitem(last=False)

    def _game_end(self, ln: dict) -> float:
        """En yeni satırın akıştan önceki kurala göre gizleneceği an: oyunda kaybolduktan en az
        ocr.hide_delay_ms sonra ve yazı en az okuma süresi kadar göründükten sonra (oyunda duruyorsa sonsuz)."""
        if ln["gone"] is None:
            return math.inf
        return max(ln["gone"] + self.s.ocr.hide_delay_ms / 1000.0, ln["changed"] + reading_time(ln["text"]))

    def _deadline(self, ln: dict) -> float:
        """Satırın ekrandan kalkacağı an (sonsuz: oyun hâlâ gösteriyor)."""
        if ln["until"] is not None:
            return ln["until"]
        keep = ln["changed"] + reading_time(ln["text"])
        if self.s.flow.mode != "single":
            keep = max(keep, ln["born"] + max(0.0, float(self.s.flow.min_show_s)))
        if ln is self._live:
            return max(keep, self._game_end(ln))
        return keep

    def _mask_on(self, now: float) -> bool:
        """OBS maskesi açık mı: oyundaki altyazıya bağlı (yayın görünümünün ayarı)."""
        if not (self.visible and self.s.style.mask_original and self.lines):
            return False
        if self._test_active:
            return True
        return self._live is not None and now < self._game_end(self._live)

    def _refresh(self, force: bool = False) -> None:
        """Süresi dolan satırları çıkarır, iki çıktıyı günceller, zamanlayıcıyı bir sonraki süre dolumuna
        (ya da maskenin kapanacağı ana) kurar."""
        now = self.clock()
        limit = stack_limit(self.s.flow)
        while len(self.lines) > limit:  # akış ayarı değişmiş olabilir
            self._remove(self.lines[0])
        for ln in [x for x in self.lines if self._deadline(x) <= now + _EPS]:
            self._remove(ln)
        if self._test_active and not self.lines:
            self._test_active = False
        self._push(now, force)
        nxt = min((self._deadline(x) for x in self.lines), default=math.inf)
        if self._live is not None and self._mask_on(now):
            nxt = min(nxt, self._game_end(self._live))
        if math.isinf(nxt):
            self._hide.stop()
        else:
            self._hide.start(max(0, math.ceil((nxt - now) * 1000)))

    def _tick(self) -> None:
        self._refresh()

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

    def _push(self, now: float | None = None, force: bool = False) -> None:
        """Görünen satırları (eskiden yeniye) ve maskeyi iki çıktıya gönderir; değişiklik yoksa göndermez."""
        if now is None:
            now = self.clock()
        shown = self.lines if self.visible else []
        out = [{"line": x["line"], "text": x["text"], "speaker": x["speaker"], "original": x["original"]}
               for x in shown]
        mask = self._mask_rect() if self._mask_on(now) else None  # yayın görünümü
        if not force and (out, mask) == self._pushed:
            return
        self._pushed = (out, mask)
        self.bridge.submit(self.server.set_lines(out))
        self.bridge.submit(self.server.set_mask(mask))
        self._show_on_screen(out)

    def _show_on_screen(self, lines: list[dict]) -> None:
        show = getattr(self.overlay, "set_lines", None)
        if show is not None:
            show(lines)
        elif lines:  # yalnız tek satır gösterebilen kutu (ör. testlerdeki sahte kutu): en yeni satır
            new = lines[-1]
            self.overlay.set_subtitle(new["text"], new["speaker"], new["original"])
        else:
            self.overlay.clear()
