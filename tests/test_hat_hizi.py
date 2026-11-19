"""tools/hat_hizi.py: senaryo ve zaman çizelgesi, sentetik kareler, sahte yakalayıcı (sahte saatle), sahte çeviri
motorları, ayarlar, ağaca takılan ölçüm noktaları, olayların satırlara bağlanması ve ölçüler, rapor ve karşılaştırma.

Gerçek zamana dayanan hiçbir şey yok: işlem hattı çalıştırılmaz, model yüklenmez, pencere açılmaz, Ollama'ya
gidilmez. Ölçümün kendisi (python tools/hat_hizi.py) elle çalıştırılır."""

from __future__ import annotations

import gc
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

KOK = Path(__file__).resolve().parents[1]
KUCUK = (730, 119)  # testlerde yarı boyutlu bölge: kareler hızlı üretilsin


def _araci_yukle() -> Any:
    """Araç bir paket değil, betik: dosya yolundan yüklenir."""
    spec = importlib.util.spec_from_file_location("hat_hizi_arac", KOK / "tools" / "hat_hizi.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul  # dataclass'lar modülünü sys.modules'ta arar
    spec.loader.exec_module(modul)
    return modul


hh = _araci_yukle()


class Saat:
    """Elle ilerleyen saat: sahte yakalayıcı gerçek zamanı beklemeden sınanır."""

    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def uyu(self, s: float) -> None:
        self.t += s


@pytest.fixture(scope="module")
def cizelge():
    return hh.cizelge_kur(1)


@pytest.fixture(scope="module")
def sahne():
    return hh.Sahne(KUCUK, 1)


def _kaynak(sahne, cizelge, **kw):
    saat = Saat()
    isinma = [hh.yazi_katmani(m, sahne.w, sahne.h) for m in hh.ISINMA_SATIRLARI]
    return hh.KareKaynagi(sahne, cizelge, isinma, saat=saat, uyu=saat.uyu, **kw), saat


# --- senaryo ve zaman çizelgesi ----------------------------------------------------------------------------------
def test_senaryo_istenen_durumlari_kapsar(cizelge):
    s = cizelge.satirlar
    assert 18 <= len(s) <= 24
    assert 45.0 <= cizelge.sure_s <= 60.0
    assert sum("\n" in x.metin for x in s) >= 3 and sum("\n" not in x.metin for x in s) >= 10  # tek ve iki satırlı
    assert any(x.duz == "No." for x in s) and max(len(x.duz) for x in s) >= 80  # çok kısa ve uzun
    assert sum(x.bosluk == 0 for x in s) >= 5  # hızlı konuşma: boşluksuz yer değiştirme
    assert sum(x.bosluk >= 0.7 for x in s) >= 8  # ardından boş ekran
    vurgulu = [x for x in s if x.vurgu]
    assert len(vurgulu) >= 3
    for x in vurgulu:  # vurgulanan, satırda tek bir kelime
        assert [k.strip(hh.NOKTALAMA) for k in x.duz.split()].count(x.vurgu) == 1 and x.renk in hh.RENKLER
    assert len({x.duz for x in s}) == len(s)  # önbellek ve takip karışmasın: her satır ayrı metin


def test_cizelge_satirlar_sirali_ve_ortusmez(cizelge):
    onceki = None
    for x in cizelge.satirlar:
        assert x.kare_off - x.kare_on >= 2
        if onceki is not None:
            assert x.kare_on >= onceki.kare_off
            if onceki.bosluk == 0:
                assert x.kare_on == onceki.kare_off  # hemen yerine geçer
            else:
                assert (x.kare_on - onceki.kare_off) / cizelge.fps >= 0.65
        onceki = x
    assert cizelge.kare_sayisi > cizelge.satirlar[-1].kare_off  # sonda boş ekran
    assert [z for z, _ in cizelge.bolumler] == list(hh.ZEMINLER)


def test_cizelge_ayni_tohumla_ayni_baska_tohumla_baska():
    a, b, c = hh.cizelge_kur(7), hh.cizelge_kur(7), hh.cizelge_kur(8)
    assert a.ozet() == b.ozet() != c.ozet()
    assert [x.metin for x in a.satirlar] == [x.metin for x in c.satirlar]  # yalnız zamanlama oynar
    kisa = hh.cizelge_kur(7, limit=3)
    assert len(kisa.satirlar) == 3 and kisa.sure_s < 15


def test_satir_at_ve_zemin_at(cizelge):
    for x in cizelge.satirlar:
        assert cizelge.satir_at(x.kare_on) is x and cizelge.satir_at(x.kare_off - 1) is x
        sonraki = cizelge.satir_at(x.kare_off)
        assert (sonraki is None) == (x.bosluk > 0)
        assert cizelge.zemin_at(x.kare_on) == x.zemin
    assert cizelge.satir_at(-1) is None and cizelge.satir_at(cizelge.kare_sayisi) is None
    assert cizelge.satir_at(0) is None  # baştaki yazısız süre


# --- sentetik kareler ---------------------------------------------------------------------------------------------
def test_kare_k_nin_saf_islevi(sahne):
    a, b = sahne.kare("gece", 40), sahne.kare("gece", 40)
    assert a.shape == (KUCUK[1], KUCUK[0], 3) and a.dtype == np.uint8
    assert a is not b and np.array_equal(a, b)
    assert not np.array_equal(a, sahne.kare("gece", 41))  # zemin kayar, kumlanma değişir
    assert np.array_equal(hh.Sahne(KUCUK, 1).kare("otoyol", 12), sahne.kare("otoyol", 12))
    assert not np.array_equal(hh.Sahne(KUCUK, 2).kare("otoyol", 12), sahne.kare("otoyol", 12))


@pytest.mark.parametrize("zemin", ["gece", "otoyol", "ic"])
def test_zemin_hareketli(sahne, zemin):
    """Değişim kapısı gerçekçi hareket görsün: art arda karelerin önemli bir kısmı farklı."""
    a, b = sahne.kare(zemin, 10).astype(np.int16), sahne.kare(zemin, 13).astype(np.int16)
    assert np.mean(np.abs(a - b) > 4) > 0.02


def test_yazi_alt_ortada_beyaz_ve_koyu_kenarli(sahne):
    katman = hh.yazi_katmani("Eyes on the road.", sahne.w, sahne.h)
    bos, dolu = sahne.kare("otoyol", 5), sahne.kare("otoyol", 5, katman)
    fark = np.argwhere(np.any(bos != dolu, axis=2))
    (y0, x0), (y1, x1) = fark.min(axis=0), fark.max(axis=0)
    assert y0 > sahne.h * 0.5 and y1 < sahne.h * 0.95  # altta
    assert abs((x0 + x1) / 2 - sahne.w / 2) < sahne.w * 0.03  # ortada
    kutu = dolu[y0:y1 + 1, x0:x1 + 1]
    assert np.mean(kutu.min(axis=2) >= 225) > 0.08  # beyaz harfler
    assert np.mean(kutu.max(axis=2) <= 40) > 0.08  # koyu kenar ve gölge (açık asfaltın üstünde)


def test_iki_satirli_altyazi_daha_yuksek(sahne):
    tek = hh.yazi_katmani("One line here.", sahne.w, sahne.h)
    iki = hh.yazi_katmani("One line here,\nand a second one.", sahne.w, sahne.h)
    assert iki.carpan.shape[0] > 1.6 * tek.carpan.shape[0]
    assert iki.y + iki.carpan.shape[0] == pytest.approx(tek.y + tek.carpan.shape[0], abs=2)  # alta yaslı


def test_vurgulu_kelime_renkli(sahne):
    def renkli(katman) -> int:
        b, g, r = (katman.eklenen[:, :, i].astype(np.int16) for i in range(3))
        return int(np.count_nonzero((r > 200) & (r - g > 60)))  # pembe: kırmızı yüksek, yeşil belirgin düşük

    duz = hh.yazi_katmani("Meet the courier at the harbor.", sahne.w, sahne.h)
    vurgulu = hh.yazi_katmani("Meet the courier at the harbor.", sahne.w, sahne.h, "harbor", "pembe")
    assert renkli(duz) == 0 and renkli(vurgulu) > 20


# --- sahte yakalayıcı ----------------------------------------------------------------------------------------------
def test_kaynak_satirin_ilk_karesinin_verildigi_ani_kaydeder(sahne, cizelge):
    kaynak, saat = _kaynak(sahne, cizelge)
    ilk = cizelge.satirlar[0]
    bos = kaynak.ver()
    assert kaynak.t0 is None and kaynak.ilk_kare == {}
    saat.t += 0.5
    t0 = kaynak.basla(0.1)
    assert t0 == kaynak.t0 and 0.1 - 1e-9 <= t0 - saat.t <= 0.1 + 2 / cizelge.fps
    assert round((t0 - kaynak.t_sifir) * cizelge.fps, 6) == kaynak.k0  # kare sınırında başlar
    saat.t = t0 + ilk.t_on - 0.004  # satırdan hemen önceki kare
    once = kaynak.ver()
    assert kaynak.ilk_kare == {}
    saat.t = t0 + ilk.t_on + 0.004
    kare = kaynak.ver()
    assert kaynak.ilk_kare == {1: saat.t}
    assert kaynak.belirme(ilk) == pytest.approx(t0 + ilk.t_on)
    assert not np.array_equal(kare, once) and not np.array_equal(once, bos)
    saat.t += 0.004  # aynı kaynak kare: aynı dizi, kayıt değişmez
    assert kaynak.ver() is kare and kaynak.verilen == 4 and kaynak.uretilen == 3
    saat.t = t0 + ilk.t_off + 0.004  # satır kalktı
    kaynak.ver()
    assert list(kaynak.ilk_kare) == [1]


def test_kaynak_boslusuz_satirda_sonraki_satiri_hemen_verir(sahne, cizelge):
    kaynak, saat = _kaynak(sahne, cizelge)
    t0 = kaynak.basla()
    a = next(x for x in cizelge.satirlar if x.bosluk == 0)
    b = cizelge.satirlar[a.no]  # no 1'den başlar: bir sonraki satır
    saat.t = t0 + a.t_off - 0.004
    kaynak.ver()
    saat.t = t0 + a.t_off + 0.004
    kaynak.ver()
    assert set(kaynak.ilk_kare) == {a.no, b.no}


def test_kaynak_isinma_satirini_acip_kapatir(sahne, cizelge):
    kaynak, saat = _kaynak(sahne, cizelge)
    bos = kaynak.ver()
    kaynak.goster(0)
    saat.t += 2 / cizelge.fps
    yazili = kaynak.ver()
    saat.t += 2 / cizelge.fps
    kaynak.goster(None)
    yine_bos = kaynak.ver()
    alt = slice(int(sahne.h * 0.6), int(sahne.h * 0.9))
    orta = slice(int(sahne.w * 0.3), int(sahne.w * 0.7))

    def beyaz(kare) -> float:
        return float(np.mean(kare[alt, orta].min(axis=2) >= 225))

    assert beyaz(yazili) > 0.05 > beyaz(bos) and beyaz(yine_bos) < 0.05
    assert kaynak.ilk_kare == {}  # ısınma satırı ölçülmez


def test_kaynak_okunan_kareyi_tanir(sahne, cizelge):
    kaynak, saat = _kaynak(sahne, cizelge)
    t0 = kaynak.basla()
    ilk = cizelge.satirlar[0]
    saat.t = t0 + ilk.t_on + 0.01
    kare = kaynak.ver()
    assert kaynak.kare_bilgisi(kare) == (saat.t, 1)
    assert kaynak.kare_bilgisi(kare[10:40, 20:300]) == (saat.t, 1)  # hattın kırptığı görünüm de aynı kare
    assert kaynak.kare_bilgisi(kare.copy()) is None and kaynak.kare_bilgisi("x") is None
    saat.t = t0 + ilk.t_off + 0.01
    bos = kaynak.ver()
    assert kaynak.kare_bilgisi(bos) == (saat.t, 0)
    del kare
    gc.collect()
    assert len(kaynak._kayit) == 1  # bırakılan karenin kaydı da gider (bellek birikmez)


def test_kaynak_pencere_tesliminde_son_teslim_edilen_kareyi_verir(sahne, cizelge):
    kaynak, saat = _kaynak(sahne, cizelge, teslim_s=0.05)
    bas = saat.t
    assert kaynak.kare_no() == 0
    saat.t = bas + 0.049
    assert kaynak.kare_no() == 0  # yeni kare daha teslim edilmedi
    saat.t = bas + 0.051
    assert kaynak.kare_no() == 1  # 0,05. saniyenin karesi
    kaynak.ver()
    assert kaynak.bekle(0.5) is True  # bir sonraki teslimi bekler
    assert bas + 0.10 <= saat.t < bas + 0.11
    kaynak.ver()
    assert kaynak.bekle(0.01) is False  # zaman aşımı: yeni kare yok
    assert saat.t < bas + 0.15


def test_teslim_araligi_agacin_kuralini_kullanir():
    assert hh.teslim_araligi(types.SimpleNamespace(), 10) == pytest.approx(0.05)  # yayımlanmış sürümün kuralı
    assert hh.teslim_araligi(types.SimpleNamespace(), 60) == pytest.approx(0.016)
    assert hh.teslim_araligi(types.SimpleNamespace(frame_interval_ms=lambda fps: 33), 10) == pytest.approx(0.033)


@pytest.mark.parametrize("tur, taban", [("screen", "RegionCapturer"), ("window", "WindowCapturer")])
def test_sahte_yakalayici_agacin_sinifindan_turer(sahne, cizelge, tur, taban):
    from altyazitr import capture
    from altyazitr.config import Settings

    kaynak, _saat = _kaynak(sahne, cizelge)
    cap = hh.yakalayici_sinifi(capture, kaynak, tur, Settings().capture)()
    assert isinstance(cap, getattr(capture, taban))  # hattaki isinstance denetimleri gerçek yoldan geçer
    cap.start()  # gerçek yakalama başlamaz
    assert cap.alive is True
    assert cap.grab_roi().shape == (sahne.h, sahne.w, 3) and kaynak.verilen == 1
    if tur == "screen":
        assert list(cap.rect) == [0, 0, sahne.w, sahne.h]
        cap.set_rect([1, 2, 30, 40])
        assert cap.rect == (1, 2, 30, 40)
    else:
        cap.set_region((0.1, 0.2, 0.3, 0.4))
        assert cap.region == (0.1, 0.2, 0.3, 0.4) and cap.hwnd == 0
        assert cap.wait_frame(0.001) in (True, False)
    cap.stop()
    assert cap.alive is True  # durdurulunca hat yeniden açmaya kalkmaz


# --- sahte çeviri motorları ---------------------------------------------------------------------------------------
def test_sahte_llm_agacin_arayuzunu_uygular():
    from altyazitr.translate import Translator, TranslatorError

    tr = hh.sahte_llm_sinifi(Translator, TranslatorError, 0.0, 6.0)()
    assert isinstance(tr, Translator) and tr.key == "ollama" and tr.label == hh.SAHTE_LLM_ETIKETI and tr.model
    durumlar: list[str] = []
    tr.prepare(durumlar.append)
    assert durumlar
    assert tr.translate("Get in the car.", [("Hey.", "Selam.")]) == "TR: Get in the car."
    assert tr.translate_marked("Go to the [[harbor]].") == "TR: Go to the [[harbor]]."  # işaretler çıktıda kalır
    tr.abort()
    tr.close()
    with pytest.raises(TranslatorError):
        tr.translate("After close.")


def test_sahte_llm_zaman_asimini_gercek_motor_gibi_bildirir():
    from altyazitr.translate import Translator, TranslatorError

    tr = hh.sahte_llm_sinifi(Translator, TranslatorError, 50.0, 0.01)()  # süre > zaman aşımı
    with pytest.raises(TranslatorError, match="zaman aşımı"):
        tr.translate("Too slow.")
    tr.durdur()
    with pytest.raises(TranslatorError, match="kesildi"):
        tr.translate("Stopped.")


def test_sahte_hizli_quickmt_gibi_kurulur():
    from altyazitr.translate import Translator

    tr = hh.sahte_hizli_sinifi(Translator, 0.0)("sozluk", "cpu")
    tr.prepare(lambda _m: None)
    assert isinstance(tr, Translator) and tr.key == "quickmt" and tr.active_device == "cpu"
    assert tr.translate("Run!") == "HZ: Run!" and tr.translate_marked("[[Run]]!") == "HZ: [[Run]]!"
    tr.close()


# --- ayarlar ------------------------------------------------------------------------------------------------------
def test_on_ayar_varsayilan_yalniz_motoru_degistirir():
    from altyazitr import config

    ayar, dokum = hh.ayarlari_kur(config, "varsayilan")
    ham = config.Settings()
    assert ayar.translate.engine == "ollama"  # yoksa asıl motor hiç devreye girmez
    assert ayar.ocr == ham.ocr and ayar.flow == ham.flow
    assert ayar.translate.hybrid == ham.translate.hybrid and ayar.capture.fps == ham.capture.fps
    assert ayar.capture.mode == "screen" and ayar.capture.rect == [0, 0, *hh.BOYUT] and ayar.capture.is_ready()
    assert dokum == {"on_ayar": "varsayilan", "uygulanan": ["translate.engine=ollama"], "atlanan": []}


def test_on_ayar_kullanici_agacin_yukleyicisinden_gecer_ve_tek_tek_ayarlar():
    """Kullanıcının 1.2.1 ayar dosyası bu ağacın load_settings'inden geçer: taşıma varsa (sürüm 5: 10 → 20 kare/sn,
    kararlılık 2 → 1) uygulanır, kullanıcının seçtiği kip ve motor korunur; --set en son uygulanır."""
    from altyazitr import config

    gercek_yol = config.SETTINGS_FILE
    ayar, dokum = hh.ayarlari_kur(config, "kullanici", ["ocr.interval_ms=250", "ocr.text_detector=hayır",
                                                         "translate.timeout_s=4,5", "olmayan.alan=1",
                                                         "ocr.olmayan_alan=3"], yakalama="window", boyut=(800, 120))
    assert config.SETTINGS_FILE == gercek_yol  # yükleyici geçici dosyayı okudu, yol geri alındı
    assert (ayar.ocr.detect_mode, ayar.ocr.interval_ms) == ("interval", 250)
    tasindi = getattr(config.Settings(), "settings_version", 4) >= 5
    assert (ayar.capture.fps, ayar.ocr.stable_frames) == ((config.CaptureCfg.fps, config.OcrCfg.stable_frames)
                                                          if tasindi else (10, 2))
    assert dokum["yukleyici"] == "load_settings" and dokum["dosya_surumu"] == 4
    assert bool(dokum["tasinan"]) == tasindi
    assert ayar.ocr.text_detector is False and ayar.ocr.preprocess == "auto"
    assert (ayar.translate.engine, ayar.translate.hybrid, ayar.translate.hybrid_wait_ms) == ("ollama", False, 1200)
    assert ayar.translate.timeout_s == 4.5 and ayar.flow.max_lines == 3
    assert ayar.capture.mode == "window" and ayar.capture.rect == [0, 0, 800, 120] and ayar.capture.is_ready()
    assert dokum["atlanan"] == ["olmayan.alan=1", "ocr.olmayan_alan=3"]  # öteki ağaçta olmayan alan atlanır
    assert "ocr.interval_ms=250" in dokum["uygulanan"]
    ayar, _ = hh.ayarlari_kur(config, "kullanici", ["capture.fps=10", "ocr.stable_frames=2"])
    assert (ayar.capture.fps, ayar.ocr.stable_frames) == (10, 2)  # taşıma --set ile geri alınabilir


def _sahte_config(tasi: bool = True, bozuk: bool = False) -> Any:
    """config modülü taklidi: load_settings SETTINGS_FILE'ı okur, 4'ten eski sürümü değil 5'ten eskiyi taşır."""
    from dataclasses import dataclass, field

    @dataclass
    class Capture:
        fps: int = 20
        mode: str = "screen"
        rect: list = field(default_factory=list)
        window_exe: str = ""

    @dataclass
    class Ocr:
        detect_mode: str = "smart"
        interval_ms: int = 500
        stable_frames: int = 1
        preprocess: str = "auto"
        text_detector: bool = True

    @dataclass
    class Translate:
        engine: str = "quickmt"
        hybrid: bool = True
        hybrid_wait_ms: int = 1200
        timeout_s: float = 6.0

    @dataclass
    class Flow:
        max_lines: int = 3

    @dataclass
    class Settings:
        capture: Capture = field(default_factory=Capture)
        ocr: Ocr = field(default_factory=Ocr)
        translate: Translate = field(default_factory=Translate)
        flow: Flow = field(default_factory=Flow)
        settings_version: int = 5

    mod = types.SimpleNamespace(Settings=Settings, SETTINGS_FILE=Path("KULLANICININ_DOSYASI_OKUNMAMALI.json"),
                                okunan=[])

    def load_settings():
        mod.okunan.append(mod.SETTINGS_FILE)
        s = Settings()
        if bozuk:
            return s  # dosyayı okuyamayan yükleyici: varsayılanlar
        data = json.loads(mod.SETTINGS_FILE.read_text(encoding="utf-8"))
        for bolum in ("capture", "ocr", "translate", "flow"):
            for ad, deger in data.get(bolum, {}).items():
                setattr(getattr(s, bolum), ad, deger)
        if tasi and data.get("settings_version", 1) < 5:
            if s.capture.fps == 10:
                s.capture.fps = 20
            if s.ocr.stable_frames == 2:
                s.ocr.stable_frames = 1
        return s

    mod.load_settings = load_settings
    return mod


def test_kullanici_dosyasi_taklit_yukleyiciyle():
    config = _sahte_config()
    ayar, dokum = hh.ayarlari_kur(config, "kullanici")
    assert len(config.okunan) == 1 and config.okunan[0].name == "ayarlar.json"  # geçici dosya
    assert not config.okunan[0].exists()  # geçici klasör silindi
    assert config.SETTINGS_FILE == Path("KULLANICININ_DOSYASI_OKUNMAMALI.json")
    assert (ayar.capture.fps, ayar.ocr.stable_frames, ayar.ocr.detect_mode) == (20, 1, "interval")
    assert dokum["tasinan"] == ["capture.fps: 10 → 20", "ocr.stable_frames: 2 → 1"]
    # taşıması olmayan (eski) ağaç: dosyadaki değerler aynen kalır
    ayar, dokum = hh.ayarlari_kur(_sahte_config(tasi=False), "kullanici")
    assert (ayar.capture.fps, ayar.ocr.stable_frames) == (10, 2) and dokum["tasinan"] == []
    # yükleyicisi olmayan ağaç: değerler doğrudan atanır
    yukleyicisiz = _sahte_config()
    del yukleyicisiz.load_settings
    ayar, dokum = hh.ayarlari_kur(yukleyicisiz, "kullanici")
    assert (ayar.capture.fps, ayar.ocr.detect_mode, dokum["yukleyici"]) == (10, "interval", "doğrudan")
    # dosyayı okuyamayan yükleyici sessizce varsayılana düşmez: ölçüm yapılmaz
    with pytest.raises(SystemExit, match="okuyamadı"):
        hh.ayarlari_kur(_sahte_config(bozuk=True), "kullanici")


@pytest.mark.parametrize("kotu", ["ocr.interval_ms=çok", "ocr.text_detector=belki", "esitsiz"])
def test_bozuk_ayar_anlasilir_hata_verir(kotu):
    from altyazitr import config

    with pytest.raises(SystemExit):
        hh.ayarlari_kur(config, "varsayilan", [kotu])


def test_ayar_dokumu_anahtar_yazmaz():
    from altyazitr import config

    ayar, _ = hh.ayarlari_kur(config, "kullanici")
    ayar.translate.gemini_key = ayar.translate.deepl_key = "GIZLI-ANAHTAR"
    dokum = hh.ayar_dokumu(ayar)
    assert "GIZLI-ANAHTAR" not in json.dumps(dokum)
    assert dokum["ocr"]["interval_ms"] == 500 and dokum["translate"]["hybrid"] is False
    assert "glossary" not in dokum["translate"] and set(dokum) == set(hh.AYAR_BOLUMLERI)


def test_agac_baska_yerden_yuklenmisse_olcum_yapilmaz(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(SystemExit, match="paketi yok"):
        hh.agaci_yukle(tmp_path)
    (tmp_path / "altyazitr").mkdir()
    (tmp_path / "altyazitr" / "__init__.py").write_text("__version__ = '0'\n", encoding="utf-8")
    import altyazitr  # noqa: F401 - testler depodan yüklüyor: başka ağaç istenirse karışmamalı

    with pytest.raises(SystemExit, match="başka yerden"):
        hh.agaci_yukle(tmp_path)


def test_agac_depodan_yuklenir(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    agac = hh.agaci_yukle(KOK)
    assert agac.yol == KOK and agac.pl.__name__ == "altyazitr.pipeline" and len(agac.kod_ozeti()) == 12


# --- ağaca takılan ölçüm noktaları (sahte ağaçla) ----------------------------------------------------------------
class _Motor:
    """OCR motoru taklidi: recognize dışarıdan, _ic içeriden çağrılır."""

    name = "sahte"

    def __init__(self) -> None:
        self.kapandi = False

    def recognize(self, img):
        return self._ic(img) + self.yardimci(img)

    def yardimci(self, img):
        return ["y"]

    def _ic(self, img):
        return ["x"]

    def close(self) -> None:
        self.kapandi = True


def _sahte_agac(hata=None):
    class Translator:
        key, label = "base", ""

        def prepare(self, status) -> None:
            if hata is not None:
                raise hata

    class Gercek(Translator):
        key, label = "ollama", "Gerçek motor"

        def translate(self, text, context=()):
            return "gerçek"

    class TextDetector:
        def boxes(self, img):
            return ["kutu"]

    pl = types.SimpleNamespace(REMOTE_ENGINES=("ollama", "gemini", "deepl"), QuickMtTranslator=object,
                               create_translator=lambda cfg, glossary: Gercek(), create_engine=lambda *a: _Motor(),
                               CACHE_FILE="kullanicinin_onbellegi")
    ceviri = types.SimpleNamespace(Translator=Translator, TranslatorError=RuntimeError,
                                   download_quickmt=lambda status: "indirildi")
    agac = types.SimpleNamespace(pl=pl, translate=ceviri, detect=types.SimpleNamespace(TextDetector=TextDetector))
    ayar = types.SimpleNamespace(translate=types.SimpleNamespace(engine="ollama", timeout_s=6.0))
    return agac, ayar, Gercek, TextDetector


def test_yamalar_sahte_motorlari_takar_ve_geri_alir(sahne, cizelge):
    agac, ayar, Gercek, TextDetector = _sahte_agac()
    ilk = (agac.pl.create_translator, agac.pl.create_engine, agac.pl.QuickMtTranslator,
           agac.translate.download_quickmt, TextDetector.boxes)
    kaynak, saat = _kaynak(sahne, cizelge)
    yama = hh.yamalari_kur(agac, ayar, kaynak, asil="fake", asil_ms=0.0, hizli="fake", hizli_ms=0.0)
    try:
        with pytest.raises(RuntimeError, match="indirmez"):
            agac.translate.download_quickmt(print)  # hiçbir koşulda model indirilmez
        assert agac.pl.QuickMtTranslator("s").translate("Hi.") == "HZ: Hi."
        tr = agac.pl.create_translator(ayar.translate, "sozluk")
        assert not isinstance(tr, Gercek) and isinstance(tr, agac.translate.Translator)
        assert yama.asil is tr and yama.asil_etiket == hh.SAHTE_LLM_ETIKETI and not yama.asil_bitti.is_set()
        tr.prepare(lambda _m: None)
        assert yama.asil_bitti.is_set() and yama.asil_hata is None
        assert tr.translate("Go.") == "TR: Go." and tr.translate_marked("[[Go]].") == "TR: [[Go]]."
        tr.durdur()
        with pytest.raises(RuntimeError):
            tr.translate("Late.")
        assert [(m, oldu) for _a, _b, m, oldu in yama.asil_cagrilari] == [("Go.", True), ("[[Go]].", True),
                                                                         ("Late.", False)]
        # hızlı çevirinin kendisi asıl motorsa (translate.engine = quickmt) o da sahtesiyle değişir
        ayar.translate.engine = "quickmt"
        assert agac.pl.create_translator(ayar.translate, "sozluk").label == hh.SAHTE_HIZLI_ETIKETI
        # OCR motoru: sınıfı aynı kalır, en dıştaki çağrı okunan kareyle birlikte kaydedilir
        assert not yama.ocr_hazir.is_set()
        motor = agac.pl.create_engine("rapidocr", "auto", "auto")
        assert isinstance(motor, _Motor) and yama.ocr_hazir.is_set()
        kare = kaynak.ver()
        assert motor.recognize(kare) == ["x", "y"]
        assert motor.recognize(np.zeros((4, 4, 3), np.uint8)) == ["x", "y"]
        assert [(ad, bilgi) for ad, _a, _b, bilgi in yama.ocr_cagrilari] == [("recognize", (saat.t, 0)),
                                                                             ("recognize", None)]
        motor.close()
        assert motor.kapandi
        assert TextDetector().boxes("img") == ["kutu"] and len(yama.bulucu_cagrilari) == 1
    finally:
        yama.geri_al()
    assert ilk == (agac.pl.create_translator, agac.pl.create_engine, agac.pl.QuickMtTranslator,
                   agac.translate.download_quickmt, TextDetector.boxes)


def test_yamalar_gercek_motoru_yalniz_izler(sahne, cizelge):
    """--main ollama: motor ağacın kendi create_translator'ından gelir; yalnız etiketi ve hazırlığı izlenir."""
    agac, ayar, Gercek, _ = _sahte_agac(hata=RuntimeError("Ollama kapalı"))
    yama = hh.yamalari_kur(agac, ayar, None, asil="ollama", hizli="real")
    try:
        assert agac.pl.QuickMtTranslator is object  # gerçek hızlı çeviriye dokunulmaz
        tr = agac.pl.create_translator(ayar.translate, "sozluk")
        assert isinstance(tr, Gercek) and yama.asil_etiket == "Gerçek motor"
        with pytest.raises(RuntimeError, match="Ollama kapalı"):
            tr.prepare(lambda _m: None)
        assert yama.asil_bitti.is_set() and str(yama.asil_hata) == "Ollama kapalı"  # ölçüm bunu görüp durur
        assert tr.translate("Go.") == "gerçek"  # çağrıları yine de sayılır
        assert [(m, oldu) for _a, _b, m, oldu in yama.asil_cagrilari] == [("Go.", True)]
    finally:
        yama.geri_al()


def test_yamalar_olcum_noktasi_olmayan_agacta_da_calisir():
    """OCR motorunun ve metin bulucunun yeri değişmiş bir ağaç: o ölçüler boş kalır, ölçüm durmaz."""
    agac, ayar, _, _ = _sahte_agac()
    del agac.pl.create_engine, agac.pl.QuickMtTranslator, agac.translate.download_quickmt
    agac.detect = types.SimpleNamespace()
    yama = hh.yamalari_kur(agac, ayar, None, asil="fake", hizli="fake")
    try:
        assert yama.ocr_hazir.is_set()  # beklenecek bir şey yok
        assert agac.pl.create_translator(ayar.translate, None).label == hh.SAHTE_LLM_ETIKETI
    finally:
        yama.geri_al()
    del agac.pl.create_translator
    with pytest.raises(SystemExit):
        hh.yamalari_kur(agac, ayar, None)


# --- olayların satırlara bağlanması ve ölçüler ---------------------------------------------------------------------
ASIL, HIZLI = "Asıl motor", "Hızlı motor"


def _s(no: int, metin: str, t_on: float, t_off: float, bosluk: float = 0.0, **ek: Any) -> dict:
    return {"no": no, "metin": metin, "t_on": t_on, "t_off": t_off, "bosluk": bosluk, "t_kare": t_on + 0.05, **ek}


def _alt(t: float, original: str, engine: str = ASIL, line: int = 1, **ek: Any) -> dict:
    return {"t": t, "tur": "subtitle", "original": original, "engine": engine, "line": line, "ocr_ms": 150.0,
            "tr_ms": 400.0, "latency_ms": None, "cached": False, "vurgu": False, **ek}


def _gone(t: float) -> dict:
    return {"t": t, "tur": "gone"}


SATIRLAR = [
    _s(1, "You said the warehouse would be empty tonight.", 1.0, 3.0, 1.0),
    _s(2, "Is that a patrol car behind us?", 4.0, 5.0),
    _s(3, "Eyes on the road.", 5.0, 6.0, 1.0),
    _s(4, "Meet the courier at the harbor before sunrise.", 7.0, 9.0, 1.0, vurgu="harbor"),
    _s(5, "No.", 10.0, 11.0, 1.0),
]


def test_ilk_ve_asil_gosterim_sureleri():
    olaylar = [
        _alt(1.6, "You said the warehouse would be empty tonight.", ASIL, 11), _gone(3.4),
        _alt(4.3, "Is that a patrol car behind us?", HIZLI, 12),
        _alt(5.4, "Eyes on the road.", HIZLI, 13),
        _alt(5.5, "Is that a patrol car behind us?", ASIL, 12),
        _alt(7.5, "Meet the courier at the harbor before sunrise.", ASIL, 14, vurgu=True), _gone(9.3),
    ]
    k, yetim = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL)
    assert yetim == []
    r1, r2, r3, r4, r5 = k
    assert (r1["first_ms"], r1["main_ms"]) == (pytest.approx(600), pytest.approx(600))
    assert (r1["ilk_motor"], r1["yukseltildi"]) == ("asıl", False)
    assert r1["kare_ms"] == pytest.approx(50) and r1["durum"] == "tamam" and r1["benzerlik"] == 100
    assert r1["gone_ms"] == pytest.approx(400) and not r1["gone_yok"]
    # önce hızlı çeviri gösterildi, asıl çeviri sonradan aynı satırı güncelledi
    assert (r2["first_ms"], r2["main_ms"], r2["ilk_motor"], r2["yukseltildi"]) == (pytest.approx(300),
                                                                                  pytest.approx(1500), "hızlı", True)
    assert r2["gone_ms"] is None and not r2["gone_yok"]  # ardından boşluk yok: işaret beklenmez
    # yalnız hızlı çeviriyle gösterildi; ekran boşaldı ama kayboldu işareti gelmedi
    assert (r3["first_ms"], r3["main_ms"], r3["ilk_motor"]) == (pytest.approx(400), None, "hızlı")
    assert r3["gone_yok"] and r3["gone_ms"] is None
    assert r4["vurgu_bulundu"] is True and r1["vurgu_bulundu"] is None
    assert r5["durum"] == "kayıp" and r5["first_ms"] is None and r5["olay"] == 0

    o = hh.ozet_hesapla(k, yetim)
    assert (o["satir"], o["gosterilen"], o["kayip"], o["kayip_no"]) == (5, 4, 1, [5])
    assert (o["once_hizli"], o["yukseltilen"], o["asilsiz"], o["yanlis"], o["yinelenen"]) == (2, 1, 1, 0, 0)
    assert o["first_ms"] == {"n": 4, "p50": 450.0, "p90": 570.0, "en_yuksek": 600.0, "ort": 450.0}
    assert o["main_ms"]["n"] == 3 and o["main_ms"]["en_yuksek"] == 1500.0
    assert o["first_kareden_ms"]["p50"] == 400.0  # yakalama beklemesi (50 ms) hariç
    assert o["gone_ms"]["n"] == 2 and o["gone_yok"] == 1 and o["vurgu"] == {"satir": 1, "bulunan": 1}


def test_yanlis_okuma_yineleme_ve_eslesmeyen_olay():
    olaylar = [
        _alt(1.5, "You said the warehouse would be", ASIL, 21),  # kesik okuma: yine de kendi satırına bağlanır
        _alt(2.0, "You said the warehouse would be empty tonight.", ASIL, 22),  # aynı satır yeni kimlikle
        _alt(2.2, "LIQUOR 24 HR", ASIL, 23),  # zemindeki yazı: hiçbir satıra benzemez
        _alt(7.6, "Meet the courier at the harbor before sunnse", ASIL, 24),
    ]
    k, yetim = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL)
    r1, r4 = k[0], k[3]
    assert r1["olay"] == 2 and r1["yineleme"] == 1 and r1["durum"] == "tamam"
    assert r1["ilk_benzerlik"] < 90 <= r1["benzerlik"] and r1["first_ms"] == pytest.approx(500)
    assert [e["original"] for e in yetim] == ["LIQUOR 24 HR"]
    assert r4["durum"] == "tamam" and r4["benzerlik"] < 100  # tek harf hatası eşiğin üstünde
    assert r4["vurgu_bulundu"] is False
    k2, _ = hh.satirlari_cozumle(SATIRLAR, [_alt(7.6, "Meet the courier at the")], ASIL)
    assert k2[3]["durum"] == "yanlış" and k2[3]["okunan"] == "Meet the courier at the"
    o = hh.ozet_hesapla(k, yetim)
    assert (o["yinelenen"], o["eslesmeyen"], o["kayip"]) == (1, 1, 3)
    # eşik değiştirilebilir
    k3, _ = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL, esik=99.5)
    assert k3[3]["durum"] == "yanlış"


def test_olay_henuz_belirmemis_satira_baglanmaz():
    satirlar = [_s(1, "Open the door.", 1.0, 2.0, 1.0), _s(2, "Open the door.", 5.0, 6.0, 1.0)]
    bagli, yetim = hh.olaylari_esle(satirlar, [_alt(1.4, "Open the door."), _alt(5.4, "Open the door.", line=2),
                                               _alt(0.5, "Open the door.", line=3)])
    assert [e["t"] for e in bagli[1]] == [1.4] and [e["t"] for e in bagli[2]] == [5.4]  # eşitlikte en son belirene
    assert [e["t"] for e in yetim] == [0.5]  # hiçbir satır belirmeden gelen olay


def test_iki_senaryo_satiri_ayni_kimlikle_gelirse_isaretlenir():
    olaylar = [_alt(4.3, "Is that a patrol car behind us?", ASIL, 5), _alt(5.4, "Eyes on the road.", ASIL, 5)]
    k, _ = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL)
    assert not k[1]["birlesti"] and k[2]["birlesti"]  # hat ikinci satırı ilkinin güncellemesi saydı
    assert hh.ozet_hesapla(k)["birlesen"] == 1


def test_erken_gelen_kayboldu_isareti_sayilir():
    olaylar = [_alt(1.4, "You said the warehouse would be empty tonight."), _gone(2.0), _gone(3.3)]
    k, _ = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL)
    assert k[0]["erken_gone"] == 1  # satır oyunda dururken çeviri gizlenirdi
    assert k[0]["gone_ms"] == pytest.approx(300)  # satır kalktıktan sonraki işaret
    assert hh.ozet_hesapla(k)["erken_gone"] == 1


def test_asamalar_okumayi_yapan_ocr_cagrisindan_hesaplanir():
    satir = [_s(1, "Then drive!", 10.0, 11.0, 1.0)]  # t_kare = 10.05
    cagrilar = [
        {"t_bas": 10.10, "t_son": 10.22, "ms": 120.0, "satir": 0, "t_kare": 10.02},  # önceki (yazısız) kare
        {"t_bas": 10.40, "t_son": 10.55, "ms": 150.0, "satir": 1, "t_kare": 10.35},  # bu satırın okuması
        {"t_bas": 10.90, "t_son": 11.05, "ms": 150.2, "satir": 1, "t_kare": 10.85},  # olaydan sonra biten: sayılmaz
    ]
    k, _ = hh.satirlari_cozumle(satir, [_alt(11.0, "Then drive!", ocr_ms=150.1)], ASIL, cagrilar)
    r = k[0]
    assert r["first_ms"] == pytest.approx(1000)
    assert (r["kare_ms"], r["kapi_ms"], r["sira_ms"], r["ocr_ms"], r["ceviri_ms"]) == (
        pytest.approx(50), pytest.approx(300), pytest.approx(50), pytest.approx(150), pytest.approx(450))
    assert sum(r[f"{a}_ms"] for a in ("kare", "kapi", "sira", "ocr", "ceviri")) == pytest.approx(r["first_ms"])
    assert r["onay_ms"] is None  # okumanın çeviri kuyruğuna girdiği an bilinmiyor: OCR bitiminden sayılır
    # erken okunan kare geçidin onayını bekledi: okuma 10,55'te bitti, çeviri kuyruğuna 10,70'te girdi
    gonderimler = [{"t": 10.30, "tur": "sub", "metin": "Eyes on the road."},  # başka satır
                   {"t": 10.70, "tur": "sub", "metin": "Then drive!"}, {"t": 10.71, "tur": "gone", "metin": None}]
    k, _ = hh.satirlari_cozumle(satir, [_alt(11.0, "Then drive!", ocr_ms=150.1)], ASIL, cagrilar,
                                gonderimler=gonderimler)
    r = k[0]
    assert (r["onay_ms"], r["ceviri_ms"]) == (pytest.approx(150), pytest.approx(300))
    assert sum(r[f"{a}_ms"] for a in ("kare", "kapi", "sira", "ocr", "onay", "ceviri")) == pytest.approx(1000)
    assert hh.ozet_hesapla(k)["asamalar_ms"]["onay"] == 150.0
    # asıl motor önceki satırla meşguldü: bu satırın isteği okuma bittikten 200 ms sonra başlayabildi
    istekler = [{"t_bas": 9.0, "t_son": 9.4, "ms": 400.0, "metin": "Then drive!"},  # satır belirmeden: başka satır
                {"t_bas": 10.75, "t_son": 11.0, "ms": 250.0, "metin": "Then [[drive]]!"}]
    k, _ = hh.satirlari_cozumle(satir, [_alt(11.0, "Then drive!", ocr_ms=150.1)], ASIL, cagrilar,
                                asil_cagrilari=istekler)
    assert (k[0]["asil_sira_ms"], k[0]["asil_sure_ms"]) == (pytest.approx(200), 250.0)
    assert hh.ozet_hesapla(k)["asil_sira_ms"]["en_yuksek"] == 200.0
    k, _ = hh.satirlari_cozumle(satir, [_alt(11.0, "Then drive!", ocr_ms=150.1)], ASIL, cagrilar,
                                asil_cagrilari=istekler, gonderimler=gonderimler)
    assert k[0]["asil_sira_ms"] == pytest.approx(50)  # çeviri kuyruğuna girdiği andan sayılır
    # OCR çağrıları ölçülemediyse (ağaçta ölçüm noktası yok) hattın kendi `latency_ms` değerine düşülür
    k, _ = hh.satirlari_cozumle(satir, [_alt(11.0, "Then drive!", latency_ms=650.0)], ASIL)
    assert k[0]["kapi_ms"] == pytest.approx(300) and k[0]["sira_ms"] is None and k[0]["ocr_ms"] == 150.0
    assert hh.ozet_hesapla(k)["asamalar_ms"] == {"kare": 50.0, "kapi": 300.0, "sira": None, "ocr": 150.0,
                                                 "onay": None, "ceviri": None}


def test_sureleri_ayni_iki_okumadan_gonderilen_secilir():
    """Aynı satırın iki okuması neredeyse aynı sürdü: olayı üreten, çeviri kuyruğuna girmeden hemen önce biten
    okumadır (gönderim anı yoksa satırın ilk tutan okuması), sonraki yeniden okuma değil."""
    satir = [_s(11, "I am not paying for another windshield.", 22.97, 24.53)]  # t_kare = 23.02
    cagrilar = [{"t_bas": 23.037, "t_son": 23.159, "ms": 122.224, "satir": 11, "t_kare": 23.019},
                {"t_bas": 24.114, "t_son": 24.236, "ms": 122.245, "satir": 11, "t_kare": 24.112}]
    olay = [_alt(26.064, "I am not paying for another windshield.", ocr_ms=122.236)]
    gonderim = [{"t": 23.163, "tur": "sub", "metin": "I am not paying for another windshield."}]
    for g in (gonderim, ()):
        r = hh.satirlari_cozumle(satir, olay, ASIL, cagrilar, gonderimler=g)[0][0]
        assert r["kapi_ms"] == pytest.approx(0, abs=1) and r["sira_ms"] == pytest.approx(18)
    r = hh.satirlari_cozumle(satir, olay, ASIL, cagrilar, gonderimler=gonderim)[0][0]
    assert r["onay_ms"] == pytest.approx(4) and r["ceviri_ms"] == pytest.approx(2901)


def test_kayitli_sonuc_yeniden_cozumlenir(tmp_path, capsys):
    sonuc = _sonuc("eski/kullanici/400")
    sonuc["ocr_cagrilari"], sonuc["asil_cagrilari"], sonuc["gonderimler"] = [], [], []
    for r in sonuc["satirlar"]:  # eski çözümlemenin hatalı alanları: yeniden hesaplanır
        r["first_ms"], r["durum"] = 1.0, "yanlış"
    sonuc["ozet"]["first_ms"] = {"p50": 1.0}
    yol = tmp_path / "s.json"
    yol.write_text(json.dumps(sonuc), encoding="utf-8")
    assert hh.main(["--reanalyze", str(yol)]) == 0
    assert "ilk gösterim   p50 450" in capsys.readouterr().out
    yeni = json.loads(yol.read_text(encoding="utf-8"))
    assert yeni["ozet"]["first_ms"]["p50"] == 450.0 and yeni["ozet"]["cpu_yuzde"] == 50.0  # ölçüm değeri korunur
    assert [r["durum"] for r in yeni["satirlar"]] == ["tamam", "tamam", "tamam", "tamam", "kayıp"]


def test_yuzdelik_ve_benzerlik():
    assert hh.yuzdelik([], 50) is None
    assert hh.yuzdelik([10.0], 90) == 10.0
    assert hh.yuzdelik([4.0, 1.0, 3.0, 2.0], 50) == 2.5
    assert hh.yuzdelik(list(range(1, 11)), 90) == pytest.approx(9.1)
    assert hh.benzerlik("EYES on the road!", "Eyes on the road.") == 100.0  # noktalama ve büyük harf sayılmaz
    assert hh.benzerlik("Eyes on the toad", "Eyes on the road.") < 100.0


# --- rapor, karşılaştırma, komut satırı ---------------------------------------------------------------------------
def _sonuc(etiket: str, kayma_ms: float = 0.0, **kosul: Any) -> dict:
    """olcumu_calistir'ın yazdığı biçimde en küçük sonuç."""
    d = kayma_ms / 1000.0
    olaylar = [_alt(1.6 + d, "You said the warehouse would be empty tonight.", ASIL, 1), _gone(3.4),
               _alt(4.3 + d, "Is that a patrol car behind us?", HIZLI, 2),
               _alt(5.5 + d, "Is that a patrol car behind us?", ASIL, 2),
               _alt(5.4 + d, "Eyes on the road.", ASIL, 3), _gone(6.5),
               _alt(7.5 + d, "Meet the courier at the harbor before sunrise.", ASIL, 4, vurgu=True), _gone(9.4)]
    kayitlar, yetim = hh.satirlari_cozumle(SATIRLAR, olaylar, ASIL)
    ozet = hh.ozet_hesapla(kayitlar, yetim)
    ozet.update(sure_s=12.0, cpu_s=6.0, cpu_yuzde=50.0, sentez_s=0.1, yakalama_hz=10.0, ocr_cagri=24,
                ocr_cagri_ort_ms=150.0, ocr_toplam_s=3.6, ocr_olay_ort_ms=150.0, bulucu_cagri=0, bulucu_toplam_s=0.0,
                asil_cagri=4, asil_hata=0, asil_ort_ms=400.0, isaret={"gone": 3, "same": 0}, kuyruk_atilan=None,
                uyari=0)
    return {
        "bicim": hh.BICIM, "arac": "hat_hizi", "etiket": etiket, "tarih": "2026-11-19T11:00:00", "agac": "C:/agac",
        "surum": "1.2.1", "kod": "abcdef012345",
        "kosul": {"asil": "fake", "asil_ms": 400.0, "hizli": "real", "hizli_ms": None, "yakalama": "screen",
                  "teslim_ms": None, "tohum": 1, "boyut": [1460, 238], "kaynak_fps": 30, "senaryo": "0123456789ab",
                  "senaryo_s": 12.0, "onizleme": True, "benzerlik_esigi": 90.0, "asil_etiket": ASIL, **kosul},
        "ayar_dokumu": {"on_ayar": "kullanici", "uygulanan": [], "atlanan": []},
        "ayarlar": {"capture": {"fps": 10}, "ocr": {"detect_mode": "interval", "interval_ms": 500},
                    "translate": {"hybrid": False, "hybrid_wait_ms": 1200}, "flow": {"max_lines": 3}},
        "toplam_s": 25.0, "ozet": ozet, "satirlar": kayitlar, "eslesmeyen": [],
        "olaylar": [e for e in olaylar], "uyarilar": [], "bildirimler": [],
    }


def test_rapor_satirlari_ve_ozeti_yazar():
    metin = hh.rapor(_sonuc("eski/kullanici/400"), ayrinti=True)
    assert "eski/kullanici/400" in metin and "sabit aralık 500 ms, karma kapalı" in metin
    assert "asıl motor: sahte 400 ms" in metin and "hızlı: gerçek quickmt" in metin
    satir2 = next(ln for ln in metin.splitlines() if "Is that a patrol car" in ln and ln.lstrip().startswith("2"))
    assert satir2.split()[3:6] == ["300", "1500", "hızlı"]
    assert "asıl çeviri +1200 ms sonra" in metin and "KAYIP (hiç gösterilmedi)" in metin
    assert "ilk gösterim   p50 450" in metin and "kayıp 1 (no 5)" in metin
    assert "Olaylar (sn, senaryonun başından):" in metin and "gone" in metin
    assert "Olaylar" not in hh.rapor(_sonuc("x"))


def test_karsilastirma_once_sonra_tablosu(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_sonuc("eski/kullanici/400")), encoding="utf-8")
    b.write_text(json.dumps(_sonuc("yeni/kullanici/400", kayma_ms=-200.0)), encoding="utf-8")
    metin = hh.karsilastirma_raporu(a, b)
    assert "A: eski/kullanici/400" in metin and "B: yeni/kullanici/400" in metin and "UYARI" not in metin
    p50 = next(ln for ln in metin.splitlines() if ln.startswith("ilk gösterim p50"))
    assert p50.split()[-4:] == ["450", "250", "-200", "(%-44)"]
    satir1 = next(ln for ln in metin.splitlines() if ln.lstrip().startswith("1 "))
    assert satir1.split()[1:7] == ["600", "400", "-200", "600", "400", "-200"]
    assert "KAYIP" in next(ln for ln in metin.splitlines() if ln.rstrip().endswith("No."))
    # koşullar farklıysa uyarır
    b.write_text(json.dumps(_sonuc("yeni/kullanici/1500", asil_ms=1500.0, senaryo="baska")), encoding="utf-8")
    metin = hh.karsilastirma_raporu(a, b)
    assert "UYARI: senaryo farklı" in metin and "UYARI: asıl motor süresi farklı" in metin
    # komut satırından
    assert hh.main(["--compare", str(a), str(b)]) == 0
    assert "Karşılaştırma  A → B" in capsys.readouterr().out
    assert hh.main(["--summary", str(a), str(b)]) == 0
    cikti = capsys.readouterr().out.splitlines()
    assert len(cikti) == 3 and cikti[1].startswith("eski/kullanici/400") and "450" in cikti[1]


def test_yinelenen_olcumler_birlestirilir(tmp_path):
    """Aynı koşulun başka tohumlu yinelemeleri: satırlar havuzlanır, ölçüm başına değerler ortalanır."""
    yollar = []
    for ad, kayma, tohum, cpu in (("a1", 0.0, 1, 40.0), ("a2", 100.0, 2, 60.0), ("b1", -200.0, 1, 30.0),
                                  ("b2", -100.0, 2, 30.0)):
        sonuc = _sonuc(f"{ad[0]}/kullanici/400", kayma_ms=kayma, tohum=tohum, senaryo=f"senaryo{tohum}")
        sonuc["ozet"]["cpu_yuzde"] = cpu
        yol = tmp_path / f"{ad}.json"
        yol.write_text(json.dumps(sonuc), encoding="utf-8")
        yollar.append(yol)
    a1, a2, b1, b2 = (json.loads(y.read_text(encoding="utf-8")) for y in yollar)
    a = hh.sonuclari_birlestir([a1, a2])
    assert a["etiket"] == "a/kullanici/400 x2" and a["kosul"]["tohum"] == [1, 2]
    assert a["kosul"]["senaryo"] == "senaryo1+senaryo2"
    assert (a["ozet"]["satir"], a["ozet"]["gosterilen"], a["ozet"]["kayip"]) == (10, 8, 2)  # sayımlar toplanır
    assert a["ozet"]["first_ms"]["n"] == 8 and a["ozet"]["first_ms"]["p50"] == 500.0  # 300…600 ve 400…700 havuzu
    assert a["ozet"]["cpu_yuzde"] == 50.0 and a["ozet"]["ocr_cagri"] == 24  # ölçüm başına değerler ortalanır
    metin = hh.karsilastirma_raporu(f"{yollar[0]},{yollar[1]}", f"{yollar[2]}, {yollar[3]}")
    assert "A: a/kullanici/400 x2" in metin and "B: b/kullanici/400 x2" in metin and "UYARI" not in metin
    p50 = next(ln for ln in metin.splitlines() if ln.startswith("ilk gösterim p50"))
    assert p50.split()[-4:-1] == ["500", "300", "-200"]
    satir1 = next(ln for ln in metin.splitlines() if ln.lstrip().startswith("1 "))
    assert satir1.split()[1:4] == ["650", "450", "-200"]  # satır başına ortalama
    islemci = next(ln for ln in metin.splitlines() if ln.startswith("işlemci"))
    assert islemci.split()[-4:-2] == ["50", "30"]


def test_sonuc_olmayan_dosya_reddedilir(tmp_path):
    yol = tmp_path / "x.json"
    with pytest.raises(SystemExit, match="bulunamadı"):
        hh.karsilastirma_raporu(yol, yol)
    yol.write_text("{bozuk", encoding="utf-8")
    with pytest.raises(SystemExit, match="geçerli JSON değil"):
        hh.ozet_tablosu([yol])
    yol.write_text(json.dumps({"arac": "ceviri_testi", "ozet": {}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="hat_hizi sonucu değil"):
        hh.ozet_tablosu([yol])


def test_olay_kaydi_hattin_olayini_duzlestirir():
    ev = {"text": "Çeviri", "original": "Line", "speaker": None, "cached": False, "engine": ASIL, "ocr_ms": 12.5,
          "tr_ms": 30.0, "line": 7, "latency_ms": 480.0, "spans": [[0, 3]]}
    r = hh._olay_kaydi(2.5, "subtitle", ev)
    assert r == {"t": 2.5, "tur": "subtitle", "original": "Line", "text": "Çeviri", "engine": ASIL, "line": 7,
                 "ocr_ms": 12.5, "tr_ms": 30.0, "latency_ms": 480.0, "cached": False, "vurgu": True}
    assert hh._olay_kaydi(3.0, "gone", None) == {"t": 3.0, "tur": "gone"}
    assert hh._olay_kaydi(3.0, "subtitle", {})["original"] is None  # alanı olmayan (ileride değişmiş) olay da geçer


def _ollamali_agac():
    """Gerçek Ollama motorunun ve yardımcılarının taklidi: her çağrı `cagrilar` listesine yazılır."""
    cagrilar: list[str] = []
    agac, ayar, _, _ = _sahte_agac()

    class OllamaTranslator(agac.translate.Translator):
        key, label = "ollama", "Ollama"

        def prepare(self, status) -> None:
            cagrilar.append("prepare")

        def translate(self, text, context=()):
            cagrilar.append("translate")
            return "x"

        def _post(self, body, timeout):
            cagrilar.append("_post")

        def keep_warm(self) -> None:
            cagrilar.append("keep_warm")

        def close(self) -> None:
            cagrilar.append("close")

    agac.translate.OllamaTranslator = OllamaTranslator
    agac.translate.create_translator = lambda cfg, glossary: OllamaTranslator()
    agac.translate.ollama_list = lambda url: cagrilar.append("ollama_list") or ["m"]
    agac.translate.release_ollama_models = lambda keep=None: cagrilar.append("release")
    agac.ollama_runtime = types.SimpleNamespace(ensure_running=lambda url, timeout=25.0: cagrilar.append("start"))
    return agac, ayar, OllamaTranslator, cagrilar


def test_sahte_modda_gercek_ollamaya_hicbir_yoldan_gidilmez():
    agac, ayar, Ollama, cagrilar = _ollamali_agac()
    ilk = (Ollama.prepare, agac.translate.ollama_list, agac.ollama_runtime.ensure_running)
    yama = hh.yamalari_kur(agac, ayar, None, asil="fake", hizli="fake")
    try:
        tr = Ollama()  # hat motoru bilinmeyen bir yoldan kursa bile
        with pytest.raises(RuntimeError, match="gerçek Ollama"):
            tr.prepare(print)
        with pytest.raises(RuntimeError):
            tr.translate("Hi.")
        with pytest.raises(RuntimeError):
            tr._post({}, 1.0)
        tr.keep_warm()
        tr.close()
        assert agac.translate.ollama_list("u") is None and agac.translate.release_ollama_models() is None
        assert not agac.ollama_runtime.ensure_running("u")
        assert cagrilar == []
        # motor translate modülünün create_translator'ından kurulsa da sahtesi gelir
        assert agac.translate.create_translator(ayar.translate, None).label == hh.SAHTE_LLM_ETIKETI
    finally:
        yama.geri_al()
    assert (Ollama.prepare, agac.translate.ollama_list, agac.ollama_runtime.ensure_running) == ilk
    yama = hh.yamalari_kur(agac, ayar, None, asil="ollama", hizli="real")  # gerçek modda dokunulmaz
    try:
        assert Ollama.prepare is ilk[0] and agac.ollama_runtime.ensure_running is ilk[2]
    finally:
        yama.geri_al()


def test_ocr_motoru_ocr_modulunden_kurulsa_da_izlenir(sahne, cizelge):
    agac, ayar, _, _ = _sahte_agac()
    del agac.pl.create_engine
    agac.ocr = types.SimpleNamespace(create_engine=lambda *a: _Motor())
    kaynak, _saat = _kaynak(sahne, cizelge)
    yama = hh.yamalari_kur(agac, ayar, kaynak, asil="fake", hizli="fake")
    try:
        assert not yama.ocr_hazir.is_set()
        motor = agac.ocr.create_engine("rapidocr")
        assert yama.ocr_hazir.is_set()
        motor.recognize(kaynak.ver())
        assert [ad for ad, *_ in yama.ocr_cagrilari] == ["recognize"]
    finally:
        yama.geri_al()


def test_ekran_yakalayicida_wait_frame_yalniz_gercek_sinifta_varsa(sahne, cizelge):
    class RegionCapturer:
        def __init__(self, rect) -> None:
            self.rect = tuple(rect)

    class BekleyenRegionCapturer(RegionCapturer):
        def wait_frame(self, timeout: float) -> bool:
            raise AssertionError("gerçek yakalayıcıya gidilmemeli")

    kaynak, saat = _kaynak(sahne, cizelge)
    ayar = types.SimpleNamespace(region=[0.12, 0.74, 0.76, 0.22], fps=10)
    duz = hh.yakalayici_sinifi(types.SimpleNamespace(RegionCapturer=RegionCapturer), kaynak, "screen", ayar)()
    assert not hasattr(duz, "wait_frame") and duz.grab_roi(copy=False).shape[:2] == (sahne.h, sahne.w)
    bekleyen = hh.yakalayici_sinifi(types.SimpleNamespace(RegionCapturer=BekleyenRegionCapturer), kaynak, "screen",
                                    ayar)()
    once = saat.t
    assert bekleyen.wait_frame(1.0) is True and saat.t - once == pytest.approx(1 / cizelge.fps)
