"""Altyazı akışı: son çeviriler alt alta (en yenisi altta), iki çıktıda da. Zamanlama SubtitleDisplay'de:
aynı "line" kimliği yerinde güncellenir, yeni kimlik alta eklenir, yığın dolunca en eski düşer, her satır en az
min_show_s kalır, en yeni satır ayrıca oyunu izler (on_gone/on_same). Oyun yazıyı göstermeye devam etse de her
satır en geç max_show_s sonra kalkar (üst sınır; 0 = sınır yok, eski davranış). "Tek satır" modu akıştan
önceki gibidir. Ayarlar'daki "Altyazı akışı" kartının en uzun kalma denetimi de burada sınanır.

Ekran gerektirmez: Qt 'offscreen' QApplication'dan hemen önce, fikstürün içinde ayarlanır (bkz. test_profiles).
Oluşturulan pencereler her testten sonra kapatılıp silinir; süreç kapanırken çökme olmasın."""

import asyncio
import json
import os
import socket
import time
from types import SimpleNamespace

import aiohttp
import numpy as np
import pytest

from altyazitr.config import Settings
from altyazitr.server import AsyncBridge, OverlayServer


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and (not isinstance(app, QApplication) or app.platformName() != "offscreen"):
        app.shutdown()  # başka bir test ekrana bağlı ya da yalnız QGuiApplication açmış olabilir
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # QApplication'dan hemen önce
        app = QApplication([])
    assert app.platformName() == "offscreen"
    return app


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Bridge:
    """AsyncBridge yerine: sunucuya giden eşyordamları kapatır (sunucu gerekmeyen testler)."""

    def submit(self, coro):
        coro.close()


class _Box:
    """Ekran kutusunun yerine: set_lines çağrılarını kaydeder."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def set_lines(self, lines):
        self.calls.append([dict(x) for x in lines])

    def clear(self):
        self.calls.append([])

    def update(self):
        pass

    @property
    def last(self) -> list[dict]:
        return self.calls[-1] if self.calls else []


def _settings(mode="flow", max_lines=3, min_show=3.0, max_show=7.0) -> Settings:
    s = Settings()
    s.flow.mode, s.flow.max_lines, s.flow.min_show_s, s.flow.max_show_s = mode, max_lines, min_show, max_show
    s.ocr.hide_delay_ms = 700
    s.capture.mode = "window"  # maske pencereye göre: sonuç monitörlerden bağımsız
    s.capture.region = [0.1, 0.7, 0.8, 0.2]
    return s


@pytest.fixture
def make_display(qapp):
    """Sahte saatli SubtitleDisplay (sunucusuz, sahte kutulu); zamanlayıcılar teardown'da durur."""
    from altyazitr.display import SubtitleDisplay

    made = []

    def make(**kw):
        box = _Box()
        d = SubtitleDisplay(_settings(**kw), _Bridge(), OverlayServer(_free_port()), box)
        clock = _Clock()
        d.clock = clock
        made.append(d)
        return d, box, clock

    try:
        yield make
    finally:
        for d in made:
            d._hide.stop()


def _ev(line, text, speaker=None, original=None):
    return {"line": line, "text": text, "speaker": speaker, "original": original if original is not None else f"en {line}"}


def _texts(box) -> list[str]:
    return [x["text"] for x in box.last]


def _advance(d, clock, sec: float) -> None:
    """Sahte saati ilerletir ve zamanlayıcının kurulduğu ana kadar geldiyse süre dolumunu çalıştırır."""
    clock.t += sec
    if d._hide.isActive():
        d._tick()


# --- satır ekleme / güncelleme ----------------------------------------------------------------
def test_yeni_kimlik_alta_eklenir_ayni_kimlik_yerinde_guncellenir(make_display):
    d, box, clock = make_display()
    d.on_subtitle(_ev(1, "Merhaba", "LUCIA"))
    clock.t += 0.5
    d.on_subtitle(_ev(2, "Nasılsın?"))
    assert _texts(box) == ["Merhaba", "Nasılsın?"]  # eskiden yeniye, en yeni altta
    born = d.lines[0]["born"]
    clock.t += 0.5
    d.on_subtitle(_ev(1, "Merhaba, Jason", "LUCIA"))  # 1. satırın daha iyi çevirisi
    assert _texts(box) == ["Merhaba, Jason", "Nasılsın?"]  # yeri değişmedi
    assert d.lines[0]["born"] == born  # süresi baştan başlamadı
    assert [x["line"] for x in box.last] == [1, 2]
    assert box.last[0]["speaker"] == "LUCIA" and box.last[0]["original"] == "en 1"


def test_yigin_dolunca_en_eski_duser(make_display):
    d, box, clock = make_display(max_lines=3)
    for i in range(1, 6):
        d.on_subtitle(_ev(i, f"satır {i}"))
        clock.t += 0.2
    assert _texts(box) == ["satır 3", "satır 4", "satır 5"]
    d.s.flow.max_lines = 2  # ayar değişti: push_style yeterli
    d.push_style()
    assert _texts(box) == ["satır 4", "satır 5"]


def test_kimlik_yoksa_yazi_degisince_yeni_satir(make_display):
    d, box, clock = make_display()
    d.on_subtitle({"text": "Bir", "speaker": None, "original": "One"})
    d.on_subtitle({"text": "Bir", "speaker": "LUCIA", "original": "One"})  # aynı yazı: aynı satır
    assert _texts(box) == ["Bir"] and box.last[0]["speaker"] == "LUCIA"
    d.on_subtitle({"text": "Bir!", "speaker": None, "original": "One"})  # aynı İngilizce satırın daha iyi çevirisi
    assert _texts(box) == ["Bir!"]
    d.on_subtitle({"text": "İki", "speaker": None, "original": "Two"})
    assert _texts(box) == ["Bir!", "İki"]
    assert len({x["line"] for x in box.last}) == 2


def test_ekrandan_cikmis_satirin_gec_cevirisi_sirayi_bozmaz(make_display):
    d, box, clock = make_display(max_lines=2)
    for i in (1, 2, 3):
        d.on_subtitle(_ev(i, f"satır {i}"))
    assert _texts(box) == ["satır 2", "satır 3"]
    d.on_subtitle(_ev(1, "satır 1 (daha iyi)"))  # 1 çoktan düştü: alta eklenmez
    assert _texts(box) == ["satır 2", "satır 3"]
    d.on_subtitle(_ev(2, "satır 2 (daha iyi)"))  # 2 hâlâ ekranda: yerinde güncellenir
    assert _texts(box) == ["satır 2 (daha iyi)", "satır 3"]


# --- geç gelen çeviriler (karma mod) ve sıra ------------------------------------------------------
def test_gec_gelen_cevirisi_gizlemeyi_iptal_etmez(make_display):
    """Oyun satırı göstermeyi bıraktıktan sonra gelen daha iyi çeviri yalnız yazıyı değiştirir."""
    d, box, clock = make_display(min_show=1.0)
    d.on_subtitle(_ev(1, "Hızlı çeviri"))
    clock.t += 2.0
    d.on_gone()  # gizleme: 0,7 sn sonra
    clock.t += 0.3
    d.on_subtitle(_ev(1, "Asıl çeviri"))  # aynı orijinal: karma modun geç gelen çevirisi
    assert _texts(box) == ["Asıl çeviri"]
    _advance(d, clock, 0.45)  # kaybolmadan 0,75 sn sonra: süre baştan başlamadı
    assert _texts(box) == []


def test_suresi_dolmus_ya_da_temizlenmis_satir_geri_gelmez(make_display):
    d, box, clock = make_display(min_show=1.0)
    d.on_subtitle(_ev(1, "Hızlı çeviri"))
    clock.t += 2.0
    d.on_gone()
    _advance(d, clock, 0.75)
    assert _texts(box) == []
    d.on_subtitle(_ev(1, "Asıl çeviri"))  # satır gitti: geri getirilmez
    assert _texts(box) == [] and not d.lines
    d.on_subtitle(_ev(2, "İkinci"))
    d.clear()
    d.on_subtitle(_ev(2, "İkinci (daha iyi)"))  # temizlenen satır da geri gelmez
    assert box.last == [] and not d.lines
    d.on_subtitle(_ev(3, "Üçüncü"))  # yeni satır normal gösterilir
    assert _texts(box) == ["Üçüncü"]


def test_yeni_okuma_oyunun_satiri_gosterdigini_bildirir(make_display):
    """Aynı satırın yeni okuması (orijinal uzadı: daktilo) bekleyen gizlemeyi iptal eder."""
    d, box, clock = make_display(min_show=1.0, max_show=0)
    d.on_subtitle(_ev(1, "Arabaya", original="Get in"))
    clock.t += 2.0
    d.on_gone()
    clock.t += 0.3
    d.on_subtitle(_ev(1, "Arabaya bin", original="Get in the car"))
    _advance(d, clock, 5.0)
    assert _texts(box) == ["Arabaya bin"]  # oyun gösteriyor: süresiz (üst sınır kapalı)


def test_tek_satir_modunda_eski_kimlik_yok_sayilir(make_display):
    d, box, clock = make_display(mode="single")
    d.on_subtitle(_ev(5, "Beşinci"))
    d.on_subtitle(_ev(4, "Dördüncü (geç)"))  # hiç gösterilmemiş olsa da eski: gösterilmez
    assert _texts(box) == ["Beşinci"]
    d.on_subtitle(_ev(5, "Beşinci (daha iyi)"))
    assert _texts(box) == ["Beşinci (daha iyi)"]


def test_akista_gec_gelen_eski_satir_sirasina_girer(make_display):
    d, box, clock = make_display(max_lines=3, min_show=3.0)
    d.on_subtitle(_ev(1, "Bir"))
    d.on_subtitle(_ev(3, "Üç"))  # 2'nin hızlı çevirisi çıkmadı; asıl çevirisi geç gelecek
    d.on_subtitle(_ev(2, "İki"))
    assert _texts(box) == ["Bir", "İki", "Üç"]  # kimlik sırasıyla
    assert d._live["line"] == 3  # oyunun gösterdiği hâlâ 3
    d.on_subtitle(_ev(4, "Dört"))
    d.on_subtitle(_ev(5, "Beş"))
    assert _texts(box) == ["Üç", "Dört", "Beş"]
    d.on_subtitle(_ev(2, "İki (daha iyi)"))  # düşmüş satır geri gelmez
    assert _texts(box) == ["Üç", "Dört", "Beş"]
    d2, box2, _ = make_display(max_lines=2)
    d2.on_subtitle(_ev(1, "Bir"))
    d2.on_subtitle(_ev(5, "Beş"))
    d2.on_subtitle(_ev(2, "İki"))  # son iki satırdan değil: gösterilmez
    assert _texts(box2) == ["Bir", "Beş"]


# --- süreler ---------------------------------------------------------------------------------------
def test_eski_satir_en_az_min_show_kalir_sonra_duser(make_display):
    d, box, clock = make_display(min_show=3.0, max_show=0)
    d.on_subtitle(_ev(1, "Kısa"))
    clock.t += 0.4
    d.on_subtitle(_ev(2, "Sonraki"))  # oyun geçti ama 1 okunabilsin
    assert _texts(box) == ["Kısa", "Sonraki"]
    assert d._hide.isActive() and 2500 <= d._hide.remainingTime() <= 2700  # 1'in süresine kurulu (yoklama yok)
    _advance(d, clock, 2.0)
    assert _texts(box) == ["Kısa", "Sonraki"]
    _advance(d, clock, 0.7)  # 1 doğalı 3,1 sn
    assert _texts(box) == ["Sonraki"]
    # en yeni satır oyunda durdukça süresiz kalır (üst sınır kapalı)
    _advance(d, clock, 30)
    assert _texts(box) == ["Sonraki"] and not d._hide.isActive()


LONG = "Bu satır epey uzun, okunması için birkaç saniye gerekir ve erken kaybolmamalıdır."  # okuma süresi ~4,9 sn


def test_uzun_satir_okuma_suresi_kadar_kalir(make_display):
    d, box, clock = make_display(min_show=1.0)
    d.on_subtitle(_ev(1, LONG))
    d.on_subtitle(_ev(2, "Tamam"))
    _advance(d, clock, 3.0)  # min_show (1 sn) geçti ama okuma süresi (~4,9 sn) geçmedi
    assert _texts(box) == [LONG, "Tamam"]
    _advance(d, clock, 2.0)
    assert _texts(box) == ["Tamam"]


def test_en_yeni_satir_oyunu_izler(make_display):
    d, box, clock = make_display(min_show=3.0, max_show=0)
    d.on_subtitle(_ev(1, "Merhaba"))
    clock.t += 5
    d.on_gone()  # oyundaki altyazı kayboldu: hide_delay (0,7 sn) sonra gider
    assert _texts(box) == ["Merhaba"]
    _advance(d, clock, 0.5)
    d.on_same()  # geri geldi: gizleme iptal
    _advance(d, clock, 10)
    assert _texts(box) == ["Merhaba"]
    d.on_gone()
    _advance(d, clock, 0.75)
    assert _texts(box) == [] and not d.lines


def test_en_yeni_satir_kaybolsa_da_min_show_kadar_kalir(make_display):
    d, box, clock = make_display(min_show=3.0)
    d.on_subtitle(_ev(1, "Hı?"))
    clock.t += 0.3
    d.on_gone()  # çok kısa bir satır: oyunda 0,3 sn göründü
    _advance(d, clock, 1.5)
    assert _texts(box) == ["Hı?"]
    _advance(d, clock, 1.3)  # doğalı 3,1 sn
    assert _texts(box) == []


def test_gercek_zamanlayici_suresi_dolunca_satiri_kaldirir(make_display, qapp, monkeypatch):
    """Sahte saat olmadan: QTimer satırı kendisi kaldırır (olay döngüsü çalışırken)."""
    import altyazitr.display as dmod

    monkeypatch.setattr(dmod, "reading_time", lambda text: 0.1)  # okuma süresi kısa olsun
    d, box, _ = make_display(min_show=0.15)
    d.clock = time.monotonic
    d.s.ocr.hide_delay_ms = 0
    d.on_subtitle(_ev(1, "bir"))
    d.on_subtitle(_ev(2, "iki"))
    d.on_gone()
    assert len(box.last) == 2
    end = time.monotonic() + 3
    while d.lines and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert not d.lines and box.last == []


# --- tek satır modu, görünürlük, temizle, test, maske -------------------------------------------------
def test_tek_satir_modu_eskisi_gibi(make_display):
    d, box, clock = make_display(mode="single", min_show=10.0)
    d.on_subtitle(_ev(1, "Bir"))
    clock.t += 0.2
    d.on_subtitle(_ev(2, "İki"))
    assert _texts(box) == ["İki"]  # yenisi anında yerini alır
    clock.t += 2.0
    d.on_gone()
    # akıştan önceki kural: max(hide_delay, okuma süresi - geçen) → 0,7 sn; min_show_s kullanılmaz
    _advance(d, clock, 0.75)
    assert _texts(box) == []
    d.show_test()
    assert len(box.last) == 1 and "test mesajıdır" in box.last[0]["text"]


def test_gorunurluk_temizle_ve_test(make_display):
    d, box, clock = make_display()
    d.on_subtitle(_ev(1, "Bir"))
    d.on_subtitle(_ev(2, "İki"))
    d.toggle_visible()
    assert box.last == [] and len(d.lines) == 2  # gizli: çıktılar boş, satırlar ve süreleri sürer
    d.toggle_visible()
    assert _texts(box) == ["Bir", "İki"]
    d.clear()
    assert box.last == [] and not d.lines and not d._hide.isActive()

    d.show_test()  # akışta 3 örnek satır: iki konuşmacılı + bir düz satır, en yenisi test cümlesi
    lines = box.last
    assert len(lines) == 3 and "test mesajıdır" in lines[-1]["text"] and "kick.com/robzsogood" in lines[-1]["text"]
    assert sum(1 for x in lines if x["speaker"]) == 2 and any(x["speaker"] is None for x in lines)
    assert any(ch in "".join(x["text"] for x in lines) for ch in "ğüşıöçĞÜŞİÖÇ")
    d.on_gone()  # örnek gösterilirken oyun olayları onu kaldırmaz
    _advance(d, clock, 4.9)
    assert len(box.last) == 3
    _advance(d, clock, 0.2)
    assert box.last == []

    d.show_test()
    d.on_subtitle(_ev(7, "Gerçek satır"))  # gerçek altyazı gelince örnekler hemen gider
    assert _texts(box) == ["Gerçek satır"]


def _record_masks(d) -> list:
    """Maskeyi açar ve OBS'e giden maskeleri (sunucu yerine) sırayla kaydeder; None = maske kapalı."""
    d.s.style.mask_original = True
    sent = []
    d.server = type("S", (), {})()

    async def set_lines(lines):
        pass

    async def set_mask(rect):
        sent.append(rect)

    d.server.set_lines, d.server.set_mask = set_lines, set_mask
    d.bridge = type("B", (), {"submit": staticmethod(lambda c: asyncio.run(c))})()
    return sent


def test_maske_oyundaki_altyaziya_bagli(make_display):
    d, box, clock = make_display(min_show=3.0)
    sent = _record_masks(d)
    d.on_subtitle(_ev(1, "Bir"))
    assert sent[-1] is not None  # oyunda altyazı var: maske açık
    clock.t += 2.0
    d.on_gone()
    _advance(d, clock, 0.75)  # akıştan önceki gizleme süresi doldu: maske kapanır …
    assert sent[-1] is None
    assert _texts(box) == ["Bir"]  # … satır ise min_show_s dolana kadar durur
    _advance(d, clock, 0.5)
    assert _texts(box) == []


# --- üst sınır: oyunda duran yazının çevirisi sonsuza dek kalmaz (flow.max_show_s) -----------------------
HINT = "Hold L2 to interact with Andrew"  # oyunda ekranda sabit kalan tuş ipucu
HINT_TR = "Andrew ile konuşmak için L2'yi basılı tut"


def test_oyunda_duran_satir_en_fazla_max_show_kalir(make_display):
    d, box, clock = make_display(max_show=7.0)
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))
    assert d._hide.isActive() and 6900 <= d._hide.remainingTime() <= 7000  # süresiz değil: üst sınıra kurulu
    _advance(d, clock, 5.0)
    d.on_gone()  # OCR yazıyı bir an kaçırdı …
    _advance(d, clock, 0.3)
    d.on_same()  # … oyun hâlâ gösteriyor: bekleyen gizleme iptal, üst sınır yine geçerli
    _advance(d, clock, 1.6)
    assert _texts(box) == [HINT_TR]
    _advance(d, clock, 0.2)  # doğalı 7,1 sn
    assert box.last == [] and not d.lines and not d._hide.isActive()


def test_uzun_satir_ust_sinira_ragmen_okuma_suresi_kadar_kalir(make_display):
    """Üst sınır okuma süresini kısaltmaz; oyun yazıyı gösterdikçe maske de o süre boyunca açık kalır."""
    d, box, clock = make_display(min_show=1.5, max_show=3.0)
    masks = _record_masks(d)
    d.on_subtitle(_ev(1, LONG))
    _advance(d, clock, 4.0)  # üst sınır (3 sn) geçti ama okuma süresi (~4,9 sn) geçmedi
    assert _texts(box) == [LONG] and masks[-1] is not None
    _advance(d, clock, 1.0)
    assert box.last == [] and masks[-1] is None
    # yazısı yeni değişen satır (daktilo: orijinal uzadı) yeni yazının okuma süresi kadar görünür
    d.on_subtitle(_ev(2, "Bu satır epey uzun,", original="This line is quite long,"))
    _advance(d, clock, 2.5)
    d.on_subtitle(_ev(2, LONG, original="This line is quite long, it takes a few seconds to read."))
    _advance(d, clock, 4.0)  # doğalı 6,5 sn
    assert _texts(box) == [LONG] and masks[-1] is not None
    _advance(d, clock, 1.0)
    assert box.last == [] and masks[-1] is None


def test_maske_ust_sinirda_kapanir(make_display):
    """Üst sınırla kalkan en yeni satırla maske de kapanır; ekranda yalnız eski satır kalsa da kapalı kalır."""
    d, box, clock = make_display(min_show=1.5, max_show=3.0)
    masks = _record_masks(d)
    d.on_subtitle(_ev(1, LONG))
    clock.t += 0.5
    d.on_subtitle(_ev(2, "Tamam"))  # oyun bunu göstermeye devam ediyor
    _advance(d, clock, 2.9)
    assert _texts(box) == [LONG, "Tamam"] and masks[-1] is not None
    _advance(d, clock, 0.2)  # 2'nin üst sınırı (doğalı 3 sn) doldu
    assert _texts(box) == [LONG] and masks[-1] is None  # eski satır hâlâ okunuyor; maske kapalı
    _advance(d, clock, 1.4)
    assert box.last == []


def test_ust_sinirla_kalkan_satir_geri_gelmez(make_display):
    """Kalkan satırın kimliği ekrandan çıkmışlara yazılır: oyun yazıyı göstermeyi sürdürse de sonradan kaldırsa
    da o satırın yeni okumaları ve geç gelen çevirileri onu geri getirmez."""
    d, box, clock = make_display(max_show=7.0)
    d.on_subtitle(_ev(1, "Andrevv ile konuşmak için L2'yi basılı tut", original="Hold L2 to interact with Andrevv"))
    _advance(d, clock, 7.1)
    assert box.last == [] and d._live is None
    d.on_same()  # oyun yazıyı hâlâ gösteriyor
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))  # aynı satırın daha temiz okuması
    d.on_subtitle(_ev(1, "L2'yi basılı tutup Andrew'la konuş", original=HINT))  # karma mod: asıl çeviri geç geldi
    assert box.last == [] and not d.lines
    d.on_gone()  # oyun yazıyı kaldırdı; kimlik yine de hatırlanır
    d.on_subtitle(_ev(1, "L2'yi basılı tutup Andrew'la konuş", original=HINT))
    _advance(d, clock, 1.0)
    assert box.last == [] and not d.lines


def test_takili_yazi_yeni_kimlikle_gelse_de_gosterilmez(make_display):
    """Oyun yazıyı kaldırmadıkça üst sınırla kalkan yazı, OCR titremesiyle yeni kimlikle gelse de gösterilmez."""
    d, box, clock = make_display(max_show=7.0)
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))
    _advance(d, clock, 7.1)
    d.on_subtitle(_ev(2, HINT_TR, original="hold L2 to interact with  Andrew!"))  # harf, boşluk, noktalama farkı
    assert box.last == [] and not d.lines
    d.on_subtitle(_ev(3, "Hadi gidelim.", original="Let's go."))  # başka yazı her zamanki gibi gösterilir
    assert _texts(box) == ["Hadi gidelim."]
    d.on_subtitle(_ev(4, HINT_TR, original=HINT))  # altyazı bitti, ipucu yine okundu: oyun onu hiç kaldırmadı
    assert _texts(box) == ["Hadi gidelim."]


def test_oyun_yaziyi_kaldirinca_ayni_yazi_yeniden_gosterilir(make_display):
    """Oyun yazıyı kaldırınca (on_gone; ekranda satır yokken de) takılı yazı unutulur: yeniden gösterilen yazının
    çevirisi yine görünür. Engellenen okumanın geç çevirisi ise gelmez; o okuma hiç gösterilmedi."""
    d, box, clock = make_display(max_show=7.0)
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))
    _advance(d, clock, 7.1)
    d.on_subtitle(_ev(2, HINT_TR, original=HINT))  # engellenir
    assert d._live is None
    d.on_gone()  # oyun yazıyı kaldırdı
    d.on_subtitle(_ev(2, "L2'yi basılı tutup Andrew'la konuş", original=HINT))  # 2'nin karma mod çevirisi
    assert box.last == [] and not d.lines
    d.on_subtitle(_ev(3, HINT_TR, original=HINT))  # oyun yazıyı yeniden gösterdi
    assert _texts(box) == [HINT_TR]


def test_tek_satir_modunda_da_ust_sinir(make_display):
    d, box, clock = make_display(mode="single", max_show=7.0)
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))
    _advance(d, clock, 6.9)
    assert _texts(box) == [HINT_TR]
    _advance(d, clock, 0.2)
    assert box.last == [] and not d.lines
    d.on_subtitle(_ev(2, HINT_TR, original=HINT))  # oyun kaldırmadan yeni kimlik: gösterilmez
    assert box.last == []
    d.on_gone()
    d.on_subtitle(_ev(3, HINT_TR, original=HINT))
    assert _texts(box) == [HINT_TR]


@pytest.mark.parametrize("max_show", [0, -1.0])
def test_max_show_sifir_ya_da_eksiyse_ust_sinir_yok(make_display, max_show):
    """Eski davranış: en yeni satır oyunda durdukça süresiz kalır; oyun yazıyı kaldırınca gizlenir."""
    d, box, clock = make_display(max_show=max_show)
    d.on_subtitle(_ev(1, HINT_TR, original=HINT))
    _advance(d, clock, 60)
    assert _texts(box) == [HINT_TR] and not d._hide.isActive()  # süre dolumu kurulmaz (yoklama da yok)
    d.on_gone()
    _advance(d, clock, 0.75)
    assert box.last == []


# --- Ayarlar: "Altyazı akışı" kartında en uzun kalma ------------------------------------------------------
@pytest.fixture
def flow_card(qapp):
    """Ayarlar'daki akış kartı (gösterilmez); kayıt ve push_style çağrıları sırayla listeye yazılır. Kart
    testten sonra kapatılıp Qt tarafında silinir."""
    from PySide6.QtCore import QCoreApplication, QEvent

    from altyazitr.gui.settings_page import FlowCard

    made = []

    def make(**flow):
        s = Settings()
        for k, v in flow.items():
            setattr(s.flow, k, v)
        calls: list[str] = []
        ctx = SimpleNamespace(s=s, save_later=lambda: calls.append("save"),
                              display=SimpleNamespace(push_style=lambda: calls.append("push")))
        made.append(FlowCard(ctx))
        return made[-1], s, calls

    try:
        yield make
    finally:
        for card in made:
            card.close()
            card.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_ayarlarda_en_uzun_kalma(flow_card):
    card, s, calls = flow_card()
    sp = card.max_show
    assert sp.value() == 7.0 and (sp.minimum(), sp.maximum(), sp.singleStep()) == (3.0, 20.0, 0.5)
    assert sp.suffix() == " sn" and sp.toolTip().startswith("Oyundaki yazı ekranda dursa bile")
    sp.setValue(10.5)
    assert s.flow.max_show_s == 10.5 and calls == ["save", "push"]  # öbür denetimler gibi: kaydet, uygula
    # en az gösterim en uzun kalmayı geçemez: iki sayı kutusunun aralığı birbirine bağlı
    sp.setValue(4.0)
    assert card.min_show.maximum() == 4.0
    card.min_show.setValue(4.0)
    assert sp.minimum() == 4.0
    sp.setValue(3.0)  # en az gösterimin altına inmez
    assert s.flow.max_show_s == 4.0 == s.flow.min_show_s
    card.mode.group.button(1).click()  # Tek satır: üst sınır bu biçimde de geçerli
    assert sp.isEnabled() and not card.min_show.isEnabled()

    card, _, calls = flow_card(mode="single", min_show_s=5.5, max_show_s=12.0)  # kayıtlı ayarlarla açılış
    assert card.max_show.value() == 12.0 and card.max_show.isEnabled()
    assert (card.max_show.minimum(), card.min_show.maximum()) == (5.5, 6.0)
    assert calls == []  # açılırken hiçbir şey kaydedilmez


# --- OBS: WebSocket "lines" mesajı -----------------------------------------------------------------
@pytest.fixture
def live(qapp):
    from altyazitr.display import SubtitleDisplay

    bridge = AsyncBridge()
    bridge.start()
    server = OverlayServer(_free_port())
    bridge.submit(server.start()).result(5)
    box = _Box()
    display = SubtitleDisplay(_settings(), bridge, server, box)
    try:
        yield server, display, box
    finally:
        display._hide.stop()
        bridge.submit(server.stop()).result(5)
        bridge.stop()


async def _next(ws, kind: str) -> dict:
    while True:
        m = json.loads((await ws.receive(timeout=5)).data)
        if m["type"] == kind:
            return m


def test_obs_lines_mesaji_sirali_gelir(live):
    server, display, _ = live
    display.on_subtitle(_ev(10, "Eski satır", "LUCIA"))  # bağlanmadan önce: son durumda gelmeli
    end = time.monotonic() + 5
    while not server.state["lines"]["lines"] and time.monotonic() < end:  # sunucu iş parçacığında işlenir
        time.sleep(0.01)

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"ws://127.0.0.1:{server.port}/ws") as ws:
                first = await _next(ws, "lines")
                display.on_subtitle(_ev(11, "Orta satır"))
                display.on_subtitle(_ev(12, "Yeni satır", "JASON"))
                await _next(ws, "lines")
                last = await _next(ws, "lines")
                sub = await _next(ws, "sub")  # eski sayfa kopyaları için: en yeni satır
                display.clear()
                empty = await _next(ws, "lines")
                return first, last, sub, empty

    first, last, sub, empty = asyncio.run(senaryo())
    assert first["lines"] == [{"line": 10, "text": "Eski satır", "speaker": "LUCIA", "original": "en 10"}]
    assert [x["line"] for x in last["lines"]] == [10, 11, 12]  # eskiden yeniye
    assert [x["text"] for x in last["lines"]] == ["Eski satır", "Orta satır", "Yeni satır"]
    assert last["lines"][2]["speaker"] == "JASON" and last["lines"][1]["speaker"] is None
    assert sub["text"] == "Yeni satır"
    assert empty["lines"] == []


def test_sayfa_lines_mesajini_ciziyor(live):
    server, _, _ = live

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.get(server.url) as r:
                return await r.text()

    page = asyncio.run(senaryo())
    assert "m.type === 'lines'" in page and "prefers-reduced-motion" in page
    assert "navigator.serviceWorker.register('/sw.js')" in page


# --- ekran kutusu: yığın çizimi ----------------------------------------------------------------------
@pytest.fixture
def overlay(qapp):
    from altyazitr.overlay import ScreenOverlay

    s = Settings()
    s.overlay.enabled = False  # kutu hiç gösterilmez (show() yok); çizim render() ile
    sc = s.screen_style
    sc.text_color = sc.speaker_color = "#FF0000"
    sc.outline_width, sc.shadow, sc.bg_opacity, sc.font_size, sc.show_original = 0.0, False, 0.0, 30, False
    ov = ScreenOverlay(s)
    ov.resize(1500, 400)  # geniş: örnek satırlar tek satıra sığsın
    try:
        yield ov
    finally:
        ov._anim.stop()
        ov._shift.stop()
        ov.close()
        ov.deleteLater()


def _render(ov) -> np.ndarray:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    ov._anim.stop()
    ov._shift.stop()
    ov._opacity, ov._progress = 1.0, 1.0  # geçişleri beklemeden
    img = QImage(ov.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    ov.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def _bands(px: np.ndarray) -> list[tuple[int, int, int]]:
    """Yazı olan satır bantları (üstten alta): (başlangıç, bitiş, banttaki en yüksek alfa)."""
    rows = (px[..., 3] > 20).any(axis=1)
    out, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            out.append((start, y, int(px[start:y, :, 3].max())))
            start = None
    return out


THREE = [{"line": 1, "text": "Arabaya bin, Vice City'ye gidiyoruz", "speaker": "LUCIA", "original": "a"},
         {"line": 2, "text": "Köprüyü geçince sola dön", "speaker": None, "original": "b"},
         {"line": 3, "text": "Çabuk ol, polisler geliyor", "speaker": "JASON", "original": "c"}]


def test_ekran_kutusu_uc_satiri_alt_alta_cizer(overlay):
    overlay.set_lines(THREE)
    assert overlay.text == "Çabuk ol, polisler geliyor"  # tek satırlık arayüz en yeniyi gösterir
    bands = _bands(_render(overlay))
    assert len(bands) == 3
    (_, _, top_a), (_, _, mid_a), (_, bot_end, bot_a) = bands
    assert bot_a >= 250  # en yeni (altta) tam görünür
    assert 90 <= top_a <= 140 and 140 <= mid_a <= 190  # üsttekiler soluk: ~0,45 ve ~0,65
    assert bot_end > overlay.height() * 0.85  # yığın kutunun altına yaslı


def test_kisa_kutuda_sigan_en_yeni_satirlar_cizilir(overlay):
    overlay.resize(1500, 95)  # iki satırlık yer
    overlay.set_lines(THREE)
    bands = _bands(_render(overlay))
    assert len(bands) == 2 and bands[-1][2] >= 250
    overlay.resize(1500, 40)  # en yeni satır her zaman çizilir
    assert len(_bands(_render(overlay))) == 1


def test_ekran_kutusu_tek_satir_ve_temizle(overlay):
    overlay.set_lines(THREE[-1:])
    assert len(_bands(_render(overlay))) == 1
    overlay.set_lines([])
    overlay._anim.stop()
    overlay._on_anim(0.0)
    overlay._on_anim_done()
    assert overlay.lines == [] and overlay.text == ""


def test_ayar_modunda_akis_ornegi(overlay):
    overlay.editing = True  # set_editing pencereyi gösterirdi
    assert len(_bands_text(_render(overlay))) >= 3  # akış: örnek yığın (3 satır)
    overlay.s.flow.mode = "single"
    assert len(overlay.sample_lines()) == 1
    overlay.editing = False


def _bands_text(px: np.ndarray) -> list[tuple[int, int, int]]:
    """Ayar modunda karartma ve çerçeve de çizilir: yalnız kırmızı yazı piksellerine bak."""
    b, g, r, a = (px[..., i].astype(int) for i in range(4))
    red = (r - np.maximum(g, b) > 60) & (a > 20)
    masked = px.copy()
    masked[..., 3] = np.where(red, px[..., 3], 0)
    return _bands(masked)


def test_yeni_satir_eklenince_gecis_baslar_tek_satirda_baslamaz(overlay):
    from PySide6.QtCore import QAbstractAnimation

    running = QAbstractAnimation.State.Running
    overlay._opacity = 1.0
    overlay.set_lines(THREE[:1])
    assert overlay._shift.state() != running  # tek satır: anında
    overlay.set_lines(THREE[1:2])  # tek satır yerine tek satır: yazı anında değişir (akıştan önceki gibi)
    assert overlay._shift.state() != running and overlay._progress == 1.0
    overlay.set_lines(THREE[1:3])  # yığına yeni satır: belirir, eskiler kayar
    assert overlay._shift.state() == running and overlay._progress < 1.0
