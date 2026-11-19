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
* Üst sınır (flow.max_show_s, iki modda da): oyundaki yazı ekranda dursa bile en yeni satır dahil her satır,
  ilk göründüğü andan en geç max_show_s sonra kalkar; yazısı yeni değiştiyse okuma süresi dolunca. Ekranda
  sabit kalan yazı (tuş ipucu, menü) ya da OCR'ın kaçırdığı kaybolma çeviriyi sonsuza dek tutmasın diye.
  max_show_s <= 0: sınır yok (eski davranış).
* Oyun gösterirken üst sınırla kalkan satırın orijinali, oyun yazıyı kaldırana (on_gone) dek hatırlanır: aynı
  yazı bu arada OCR titremesiyle yeni kimlikle gelse de gösterilmez (büyük/küçük harf, boşluk ve noktalama
  farkı sayılmaz). Oyun yazıyı kaldırıp yeniden gösterirse (yeniden söylenen cümle) çevirisi yine görünür.
* "Tek satır" modunda yığın tek satırdır ve akıştan önceki gibi çalışır (min_show_s kullanılmaz); üst sınır
  bu modda da geçerlidir.
* Süreler QTimer ile dolar (tek zamanlayıcı, her zaman bir sonraki süre dolumuna kurulur; yoklama yok).
* OBS maskesi oyundaki altyazıya bağlı kalır: en yeni satır oyunda göründükçe ve akıştan önceki gizleme
  süresi dolana kadar açıktır, en geç üst sınırda kapanır; ekranda yalnız eski satırlar kaldıysa da kapanır.

İki çıktının görünümü ayrıdır: OBS sayfası ve OBS maskesi "Yayınımda" ayarlarını (Settings.style),
ekrandaki kutu "Ekranımda" ayarlarını (Settings.screen_style) kullanır. Arayüz, iki görünümden herhangi
biri ya da akış ayarları (Settings.flow) değişince yalnızca push_style() çağırır.

Vurgulu kelimeler: olayın `spans` alanı (Türkçe metindeki aralıklar) satırla birlikte iki çıktıya da gider; her
çıktı onları kendi görünümünün vurgu rengiyle çizer (vurgu kapalıysa yazı renginde).

"Oyun altyazısının üzerine" (StyleCfg.cover_original, iki görünümde ayrı): o görünüm akışı göstermez; oyunun şu an
gösterdiği satırın çevirisi, oyunun İngilizce altyazısının tam yerine yazılır. Opak bir kutu İngilizceyi örter, Türkçe
içinde ortalanır. Kutunun yeri okumanın `box` alanından gelir (bkz. textproc.subtitle_box): altyazının bölgedeki
dikdörtgeni + pay (cover_area); yazı boyu oyunun satır yüksekliğinden (cover_fonts). Kutu OBS maskesiyle aynı kurala
bağlıdır: oyun altyazıyı kaldırınca gizleme süresi sonunda, en geç üst sınırda kalkar; yeni satırın çevirisi gelince
yeni yerine geçer. İki çıktıya aynı hesap gider: OBS'e yayın karesine oransal (set_cover), ekrandaki kutuya mantıksal
piksel (ScreenOverlay.set_cover). Türkçenin kutuya sığdırılması (gerekirse küçültme, sonra satır kaydırıp yukarı
büyütme) çizen tarafta aynı kuralla yapılır (render.cover_size, web/overlay.html)."""

from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict, deque
from collections.abc import Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from altyazitr import APP_NAME
from altyazitr.capture import monitor_fraction, region_screen_rect
from altyazitr.config import Settings
from altyazitr.overlay import ScreenOverlay, screen_area
from altyazitr.render import stack_limit
from altyazitr.server import AsyncBridge, OverlayServer, clean_spans, style_vars

log = logging.getLogger(__name__)

CHARS_PER_SEC = 17.0  # okuma hızı (Netflix yetişkin standardı)
TEST_S = 5.0  # Test düğmesinin örnek altyazısı bu kadar görünür
_EPS = 0.005  # zamanlayıcı birkaç ms erken uyanırsa da süresi dolan satır gitsin
_TEST_ID = -1_000_000  # örnek satırların kimlikleri (işlem hattınınkilerle ve kendi kimliklerimizle karışmaz)
_LEFT_MAX = 32  # ekrandan çıkan satırlardan bu kadarı hatırlanır (geç gelen çeviriler için)
_STUCK_MAX = 8  # üst sınırla kalkan yazılardan bu kadarı hatırlanır (oyun yazıyı kaldırana dek)

# "Oyun altyazısının üzerine": ölçüler OCR'ın satır kutusu yüksekliğine (L) oranla. Ölçüm (GTA 6 altyazı kırpıntıları,
# RapidOCR, 1x-3x büyütme): satır kutusu harflerin her yanından 0,1-0,3 L taşar; harfleri oyununkiyle aynı yükseklikte
# olan Segoe UI Bold ~0,65 L boyundadır ve genişliği de oyununkiyle aynıdır (%96-99).
COVER_PAD = (0.25, 0.12)  # kutunun OCR kutusundan taşma payı (yatay, dikey): gölge ve okumalar arası oynama da örtülür
COVER_FONT = 0.65  # yazı boyu / L: Türkçe oyunun altyazısıyla aynı boyda görünür
COVER_SHRINK = 0.75  # Türkçe sığmazsa yazı en çok bu orana küçülür, sonra satır kaydırılır
COVER_MIN_PX = 16.0  # okunur alt sınır (1080p'ye göre piksel)
_TEST_COVER = (0.15, 0.88, 0.7)  # örnek altyazının kapağı: bölgenin solundan, alt kenarı ve genişliği (oransal)

# Test düğmesi, eskiden yeniye: (çeviri, konuşmacı, orijinal, vurgulu ifade). Akışta ayardaki satır sayısı kadar
# en yenisi (varsayılan 3), tek satır modunda yalnızca sonuncusu gösterilir. Vurgulu ifade vurgu renginde çizilir.
TEST_LINES = (
    ("Çabuk ol, polisler peşimizde!", "JASON", "Hurry up, the cops are on our tail!", None),
    ("Arabaya bin, Vice City'ye gidiyoruz.", "LUCIA", "Get in the car, we're going to Vice City.", "Vice City'ye"),
    ("Köprüyü geçince sola dön, sonra güneye ilerle.", None, "Turn left after the bridge, then head south.", None),
    ("Bu bir test altyazısıdır — ğüşıöç ĞÜŞİÖÇ. Ayarları buradan kontrol edin!", APP_NAME,
     "This is a test subtitle. Check your settings here!", "test altyazısıdır"),
)


def _phrase_spans(text: str, phrase: str | None) -> list[list[int]]:
    """Metindeki ifadenin vurgu aralığı (örnek satırlar için)."""
    at = text.find(phrase) if phrase else -1
    return [[at, at + len(phrase)]] if at >= 0 else []


def reading_time(text: str) -> float:
    """Bir satırı okumak için gereken süre (sn): uzunluğa göre 1,2 ile 6 sn arası."""
    return max(1.2, min(6.0, len(text) / CHARS_PER_SEC))


def _text_key(text: str) -> str:
    """Orijinal yazıların karşılaştırma anahtarı: büyük/küçük harf, boşluk ve noktalama farkı sayılmaz."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def cover_area(box: dict | None) -> tuple[tuple[float, float, float, float], int, float | None]:
    """"Üzerine yerleştir" kutusunun bölgeye (ROI) oransal yeri: oyunun altyazısının kutusu + COVER_PAD (bölgenin
    dışına taşabilir), oyundaki satır sayısı ve satır yüksekliği (bölge yüksekliğine oransal). Yatay pay bölgenin
    piksel oranıyla hesaplanır (pay her yanda aynı piksel). Altyazının yeri bilinmiyorsa bölgenin tamamı örtülür ve
    yazı boyu görünümünkü olur (satır yüksekliği None)."""
    whole = ((0.0, 0.0, 1.0, 1.0), 1, None)
    if not isinstance(box, dict):
        return whole
    try:
        x, y, w, h = (float(v) for v in box["rect"])
        rows = max(1, int(box.get("rows") or 1))
        lh = float(box.get("line_h") or 0.0) or h / rows
        rw, rh = (float(v) for v in (box.get("roi") or (1, 1)))
    except (KeyError, TypeError, ValueError):
        return whole
    if not (w > 0 and h > 0 and lh > 0 and rw > 0 and rh > 0) or not all(map(math.isfinite, (x, y, w, h, lh))):
        return whole
    px, py = COVER_PAD[0] * lh * rh / rw, COVER_PAD[1] * lh
    return (x - px, y - py, w + 2 * px, h + 2 * py), rows, lh


def cover_fonts(match: float, top: float, floor: float) -> tuple[float, float]:
    """Kapak yazısının hedef ve en küçük boyu (aynı birimde): oyunun yazısıyla aynı boy (match) ama görünümün yazı
    boyunu (top) geçmez; Türkçe sığmazsa hedefin COVER_SHRINK katına kadar küçülür, okunur alt sınırın (floor) altına
    inmez (hedef zaten ondan küçükse hedefte kalır)."""
    font = max(1e-6, min(match, top))
    return font, min(font, max(font * COVER_SHRINK, floor))


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
        # için anlamlı), until (yalnız örnek satırlar: kesin bitiş), box (oyundaki altyazının yeri; bilinmiyorsa None).
        self.lines: list[dict] = []
        self._live: dict | None = None  # oyunun şu an gösterdiği satır (yığının en yenisi); gidince None
        self._hide = QTimer(self, singleShot=True, timeout=self._tick)  # bir sonraki süre dolumu
        self._hide.setTimerType(Qt.TimerType.PreciseTimer)
        self._test_active = False
        self._max_line: int | None = None  # gösterilen en yeni işlem hattı kimliği
        self._auto_id = 0  # olayda "line" yoksa kendi kimliklerimiz (negatif)
        self._left: OrderedDict[int, None] = OrderedDict()  # ekrandan çıkan satırların kimlikleri (son birkaçı)
        # oyun hâlâ gösterirken üst sınırla kalkan yazıların anahtarları (_text_key); oyun yazıyı kaldırınca silinir
        self._stuck: deque[str] = deque(maxlen=_STUCK_MAX)
        self._pushed: tuple | None = None
        self._mask_key: tuple | None = None
        self._mask_frac: tuple[float, float, float, float] | None = None
        # "Üzerine yerleştir": OBS'e ve ekrandaki kutuya en son gönderilen kapak (yalnız değişince gönderilir);
        # ekrandaki kutunun akışta satır gösterip göstermediği (kapağa geçerken bir kez temizlenir)
        self._cover_sent: dict | None = None
        self._screen_cover_sent: dict | None = None
        self._screen_flow_shown = False
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
        if ln is None and _text_key(original) in self._stuck:
            self._mark_left(lid)  # oyunun hiç kaldırmadığı yazı yeni kimlikle geldi: bu okuma hiç gösterilmez
            return
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
        """Oyundaki altyazı kayboldu: en yeni satır gizleme süresini saymaya başlar. Üst sınırla kalkan yazılar
        da unutulur (ekranda satır olmasa da): oyun aynı yazıyı yeniden gösterirse çevirisi yine görünür."""
        self._stuck.clear()
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
        kadar örnek satır alt alta görünür (varsayılan 3), tek satır modunda bir satır. "Oyun altyazısının üzerine"
        açık görünümde en yeni örnek, seçilen bölgenin altında kapak kutusuyla gösterilir (bkz. _cover)."""
        now = self.clock()
        n = min(stack_limit(self.s.flow), len(TEST_LINES))
        first = len(TEST_LINES) - n
        self._test_active = True
        self._live = None
        self.lines = []
        for j in range(first, len(TEST_LINES)):
            text, speaker, original, phrase = TEST_LINES[j]
            self.lines.append({"line": _TEST_ID - j, "text": text, "speaker": speaker, "original": original,
                               "spans": _phrase_spans(text, phrase), "born": now, "changed": now, "gone": None,
                               "until": now + TEST_S, "box": None})
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
                "spans": clean_spans(ev.get("spans"), len(text)), "born": now, "changed": now, "gone": None,
                "until": None, "box": ev.get("box")}

    def _update(self, ln: dict, ev: dict, text: str, original: str, now: float) -> None:
        """Ekrandaki satırın güncellemesi: yeri ve süreleri korunur. Aynı okumanın daha iyi çevirisi (karma mod;
        oyun geçtikten sonra da gelebilir) yalnız yazıyı değiştirir. Oyundaki satırın yeni bir okuması (orijinal
        uzadı ya da düzeldi) oyunun onu hâlâ gösterdiğini bildirir: bekleyen gizleme iptal olur, okuma süresi
        yeni yazıdan sayılır (akıştan önceki gizleme kuralı). Okumanın altyazı kutusu da güncellenir (uzayan
        altyazı büyür)."""
        if original != ln["original"] and ln is self._live:
            if text != ln["text"]:
                ln["changed"] = now
            ln["gone"] = None
        ln.update(text=text, speaker=ev.get("speaker"), original=original,
                  spans=clean_spans(ev.get("spans"), len(text)))
        if ev.get("box"):
            ln["box"] = ev["box"]

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
            self._mark_left(ln["line"])

    def _mark_left(self, lid: int) -> None:
        """Kimliği ekrandan çıkmışlara yazar: o satırın sonraki okumaları ve çevirileri ekrana gelmez."""
        self._left[lid] = None
        self._left.move_to_end(lid)
        while len(self._left) > _LEFT_MAX:
            self._left.popitem(last=False)

    def _remember_stuck(self, original: str) -> None:
        """Üst sınırla kalkan yazıyı oyun kaldırana (on_gone) dek hatırlar: yeni kimlikle gelse de gösterilmez."""
        key = _text_key(original)
        if key and key not in self._stuck:
            self._stuck.append(key)

    def _cap(self, ln: dict) -> float:
        """Satırın üst sınırı: oyun yazıyı göstermeye devam etse de en geç bu anda kalkar. İlk göründüğü andan
        flow.max_show_s sonra; yazısı yeni değiştiyse okuma süresi dolunca. max_show_s <= 0: sınır yok."""
        limit = float(self.s.flow.max_show_s)
        if not limit > 0:  # 0, eksi (ya da NaN): sınır yok
            return math.inf
        return max(ln["born"] + limit, ln["changed"] + reading_time(ln["text"]))

    def _game_end(self, ln: dict) -> float:
        """En yeni satırın akıştan önceki kurala göre gizleneceği an: oyunda kaybolduktan en az
        ocr.hide_delay_ms sonra ve yazı en az okuma süresi kadar göründükten sonra; oyunda duruyorsa sonsuz.
        Her durumda en geç üst sınırda (bkz. _cap)."""
        end = math.inf
        if ln["gone"] is not None:
            end = max(ln["gone"] + self.s.ocr.hide_delay_ms / 1000.0, ln["changed"] + reading_time(ln["text"]))
        return min(end, self._cap(ln))

    def _deadline(self, ln: dict) -> float:
        """Satırın ekrandan kalkacağı an (sonsuz: oyun hâlâ gösteriyor ve üst sınır kapalı)."""
        if ln["until"] is not None:
            return ln["until"]
        keep = ln["changed"] + reading_time(ln["text"])
        if self.s.flow.mode != "single":
            keep = max(keep, ln["born"] + max(0.0, float(self.s.flow.min_show_s)))
        if ln is self._live:
            return max(keep, self._game_end(ln))
        return keep

    def _mask_on(self, now: float) -> bool:
        """OBS maskesi açık mı: oyundaki altyazıya bağlı (yayın görünümünün ayarı), en geç üst sınırda kapanır.
        "Oyun altyazısının üzerine" açıkken maske kullanılmaz: kapak altyazıyı zaten örter."""
        if not (self.visible and self.s.style.mask_original and self.lines) or self.s.style.cover_original:
            return False
        if self._test_active:
            return True
        return self._live is not None and now < self._game_end(self._live)

    def _cover_line(self, now: float) -> dict | None:
        """"Üzerine yerleştir" kutusunda gösterilecek satır: oyunun şu an gösterdiği satır (en yeni), maskeyle aynı
        kuralla (oyun kaldırınca gizleme süresi sonunda, en geç üst sınırda biter; bkz. _game_end); örnek gösterilirken
        en yeni örnek satır."""
        if not self.visible or not self.lines:
            return None
        if self._test_active:
            return self.lines[-1]
        ln = self._live
        return ln if ln is not None and now < self._game_end(ln) else None

    def _refresh(self, force: bool = False) -> None:
        """Süresi dolan satırları çıkarır, iki çıktıyı günceller, zamanlayıcıyı bir sonraki süre dolumuna
        (ya da maskenin kapanacağı ana) kurar."""
        now = self.clock()
        limit = stack_limit(self.s.flow)
        while len(self.lines) > limit:  # akış ayarı değişmiş olabilir
            self._remove(self.lines[0])
        for ln in [x for x in self.lines if self._deadline(x) <= now + _EPS]:
            if ln is self._live and ln["gone"] is None:  # oyun hâlâ gösterirken üst sınırla kalktı
                self._remember_stuck(ln["original"])
            self._remove(ln)
        if self._test_active and not self.lines:
            self._test_active = False
        self._push(now, force)
        nxt = min((self._deadline(x) for x in self.lines), default=math.inf)
        covering = self.s.style.cover_original or self.s.screen_style.cover_original
        if self._live is not None and (self._mask_on(now) or (covering and self._cover_line(now) is self._live)):
            nxt = min(nxt, self._game_end(self._live))  # maske / kapak oyundaki altyazıyla kalkar
        if math.isinf(nxt):
            self._hide.stop()
        else:
            self._hide.start(max(0, math.ceil((nxt - now) * 1000)))

    def _tick(self) -> None:
        self._refresh()

    def _region_frac(self) -> tuple[float, float, float, float] | None:
        """Bölgenin yayın karesindeki oransal konumu: ekran modunda bölgenin bulunduğu monitöre, pencere modunda
        pencerenin istemci alanına göre (OBS tuvalinin oyun görüntüsüyle aynı olduğu kabul edilir)."""
        c = self.s.capture
        if c.mode == "window":
            return tuple(c.region) if len(c.region) == 4 else None
        key = tuple(c.rect)
        if key != self._mask_key:
            self._mask_key, self._mask_frac = key, monitor_fraction(c.rect)
        return self._mask_frac

    def _mask_rect(self) -> list[float] | None:
        """OBS'teki maske: bölgenin yayın karesindeki oransal konumu (bkz. _region_frac) + pay.
        Maske yalnızca OBS'te olduğu için yayın görünümünü (s.style) kullanır."""
        frac = self._region_frac()
        if not frac:
            return None
        x, y, w, h = frac
        pad = self.s.style.mask_pad_percent / 100.0
        x0, y0 = max(0.0, x - pad), max(0.0, y - pad)
        x1, y1 = min(1.0, x + w + pad), min(1.0, y + h + pad)
        return [x0, y0, x1 - x0, y1 - y0]

    @staticmethod
    def _item(x: dict) -> dict:
        """Çıktılara giden satır: {"line", "text", "speaker", "original"}, vurgu varsa "spans" (yoksa alan hiç yazılmaz:
        eski tüketiciler için olay aynı kalır)."""
        item = {"line": x["line"], "text": x["text"], "speaker": x["speaker"], "original": x["original"]}
        if x.get("spans"):
            item["spans"] = [list(sp) for sp in x["spans"]]
        return item

    def _cover(self, ln: dict, roi: Sequence[float], top: float, floor: float) -> dict:
        """Kapak kutusunun bir çıktıdaki yeri ve yazı boyları (iki çıktıda aynı kural). roi: bölgenin o çıktıdaki yeri
        (x, y, genişlik, yükseklik); top: görünümün yazı boyu, floor: okunur alt sınır (yükseklik biriminde). Kutu
        oyunun altyazısı + pay (cover_area); yazı oyunun satırı boyunda (cover_fonts). Örnek satırın oyunda yeri
        olmadığından kutusu bölgenin altında, görünümün yazı boyunda tek satırdır (_TEST_COVER)."""
        x, y, w, h = (float(v) for v in roi)
        if ln["until"] is not None:
            left, bottom, width = _TEST_COVER
            lh = min(bottom, top / COVER_FONT / h) if h > 0 else bottom
            rect, rows = (left, bottom - lh, width, lh), 1
        else:
            rect, rows, lh = cover_area(ln.get("box"))
        font, low = cover_fonts(COVER_FONT * lh * h if lh else top, top, floor)
        line = self._item(ln)
        line.pop("original")  # örtülen yazının kendisi: kapakta gösterilmez
        return {"rect": [x + rect[0] * w, y + rect[1] * h, rect[2] * w, rect[3] * h], "rows": rows,
                "font": font, "min": low, "line": line}

    def _stream_cover(self, ln: dict) -> dict | None:
        """OBS'teki kapak: yayın karesine oransal (bkz. _region_frac); yazı boyları kare yüksekliğine oransal."""
        frac = self._region_frac()
        if not frac:
            return None
        c = self._cover(ln, frac, float(self.s.style.font_size) / 1080.0, COVER_MIN_PX / 1080.0)
        c["rect"] = [round(v, 5) for v in c["rect"]]
        c["font"], c["min"] = round(c["font"], 5), round(c["min"], 5)
        return c

    def _screen_cover(self, ln: dict) -> dict | None:
        """Ekrandaki kapak, mantıksal piksel: bölgenin ekrandaki yeri okumayla gelir (pencere modunda pencere taşınsa
        da doğru), örnekte ayardan bulunur; Windows ölçeği ve çok ekran için screen_area. Yazının üst sınırı kutunun
        normal boyutudur (görünümün boyu × ekran yüksekliği / 1080 × ölçek); bilinmiyorsa None."""
        box = ln.get("box") if ln["until"] is None else None
        phys = (box.get("screen") if isinstance(box, dict) else None) or region_screen_rect(self.s.capture)
        area = screen_area(phys) if phys and len(phys) == 4 else None
        if area is None:
            return None
        roi, bounds = area
        k = bounds[3] / 1080.0
        try:
            scale = max(0.1, float(self.s.overlay.scale))
        except (TypeError, ValueError):
            scale = 1.0
        c = self._cover(ln, roi, float(self.s.screen_style.font_size) * k * scale, COVER_MIN_PX * k)
        c["bounds"] = list(bounds)
        return c

    def _push(self, now: float | None = None, force: bool = False) -> None:
        """Görünen satırları (eskiden yeniye), maskeyi ve "üzerine yerleştir" kutularını iki çıktıya gönderir;
        değişiklik yoksa göndermez. Kapak açık görünüm akışı göstermez (ekranda örneğin yeri bilinmiyorsa normal
        kutuda gösterilir); kapak yalnız değişince gönderilir."""
        if now is None:
            now = self.clock()
        out = [self._item(x) for x in (self.lines if self.visible else [])]
        st, sc = self.s.style, self.s.screen_style
        cover_ln = self._cover_line(now) if (st.cover_original or sc.cover_original) else None
        stream_cover = self._stream_cover(cover_ln) if st.cover_original and cover_ln else None
        screen_cover = (self._screen_cover(cover_ln)
                        if sc.cover_original and cover_ln and self.s.overlay.enabled else None)
        screen_flow = not sc.cover_original or (self._test_active and screen_cover is None)
        stream_lines, screen_lines = ([] if st.cover_original else out), (out if screen_flow else [])
        mask = self._mask_rect() if self._mask_on(now) else None  # yayın görünümü
        state = (stream_lines, mask, stream_cover, screen_lines, screen_flow, screen_cover)
        if not force and state == self._pushed:
            return
        self._pushed = state
        self.bridge.submit(self.server.set_lines(stream_lines))
        self.bridge.submit(self.server.set_mask(mask))
        if stream_cover != self._cover_sent or (force and st.cover_original):
            self._cover_sent = stream_cover
            self.bridge.submit(self.server.set_cover(stream_cover))
        if screen_flow:
            if self._screen_cover_sent is not None:
                self._screen_cover_sent = None
                self._set_screen_cover(None)
            self._show_on_screen(screen_lines)
            self._screen_flow_shown = bool(screen_lines)
            return
        if self._screen_flow_shown:  # akıştan kapağa geçildi: kutudaki satırlar kalkar
            self._screen_flow_shown = False
            self._show_on_screen([])
        if force or screen_cover != self._screen_cover_sent:
            self._screen_cover_sent = screen_cover
            self._set_screen_cover(screen_cover)

    def _set_screen_cover(self, cover: dict | None) -> None:
        put = getattr(self.overlay, "set_cover", None)  # testlerdeki sahte kutularda olmayabilir
        if put is not None:
            put(cover)

    def _show_on_screen(self, lines: list[dict]) -> None:
        show = getattr(self.overlay, "set_lines", None)
        if show is not None:
            show(lines)
        elif lines:  # yalnız tek satır gösterebilen kutu (ör. testlerdeki sahte kutu): en yeni satır
            new = lines[-1]
            self.overlay.set_subtitle(new["text"], new["speaker"], new["original"])
        else:
            self.overlay.clear()
