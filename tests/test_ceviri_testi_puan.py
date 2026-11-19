"""tools/ceviri_testi.py çok anlamlı kelime puanlaması: Türkçe büyük/küçük harf, kök eşleşmesi ve puan kuralı, veri
seti denetimi, özet, rapor ve iki sonucun karşılaştırması. Ağ yok: çeviri ya sahte motorla ya da motorsuz ('none')
yapılır."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

KOK = Path(__file__).resolve().parents[1]


def _araci_yukle() -> Any:
    """Araç bir paket değil, betik: dosya yolundan yüklenir."""
    spec = importlib.util.spec_from_file_location("ceviri_testi_arac", KOK / "tools" / "ceviri_testi.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


ct = _araci_yukle()

MAGAZINE = {"kelime": "magazine", "anlam": "gundelik", "dogru": ["dergi"], "yanlis": ["şarjör", "mermi"]}


def _satir(tr: str | None, hata: str | None = None, en: str = "Toss me the magazine.", **alanlar: Any) -> dict:
    """Sonuç dosyasındaki gibi bir satır kaydı."""
    return {"no": 1, "en": en, "giden": en, "tr": tr, "ms": 100.0, "hata": hata, "beklenen": "",
            "profil_ornegi": False, **alanlar}


def _sahne(sid: str, *satirlar: dict) -> dict:
    """Sonuç dosyasındaki gibi bir sahne (sahneleri_cevir ad ve açıklamayı her zaman yazar)."""
    return {"id": sid, "ad": f"Sahne {sid}", "aciklama": "", "satirlar": list(satirlar)}


def _sonuc(sahneler: list[dict], model: str = "gemma4:12b") -> dict:
    """testi_calistir'ın yazdığı biçimde en küçük sonuç."""
    return {"bicim": ct.BICIM, "tarih": "2026-11-18T20:00:00", "surum": "1.2.1", "kod": "abc1234", "motor": "ollama",
            "model": model, "profil": "gta6", "istem_ozeti": "0123456789ab", "baglam_satiri": 8,
            "zaman_asimi_s": 20.0, "veri_seti": "tools/ceviri_testi_cokanlam.json", "hazirlik_s": 1.0,
            "isinma_ms": 300.0, "isinma_hatasi": None, "toplam_s": 5.0, "kesildi": False, "esikler": {},
            "ozet": ct.ozet_hesapla(sahneler), "puanlama": ct.puan_ozeti(sahneler), "sahneler": sahneler}


# --- Türkçe büyük/küçük harf -----------------------------------------------------------------------------------
def test_tr_kucuk_turkce_i_harfleri():
    assert ct.tr_kucuk("DERGİYİ OKU") == "dergiyi oku"
    assert ct.tr_kucuk("ISLAK IŞIK") == "ıslak ışık"
    assert ct.tr_kucuk("ŞARJÖRÜ ÇIKAR, ĞÜÖ") == "şarjörü çıkar, ğüö"
    # str.lower bunu yanlış yapar: 'İ' → 'i' + birleşik nokta, 'I' → 'i'
    assert "DERGİ".lower() != "dergi" and "ISIK".lower() == "isik"
    assert ct.tr_kucuk("ISIK") == "ısık"


def test_tr_kucuk_sapka_ve_ayrik_nokta():
    assert ct.tr_kucuk("Dükkân") == "dükkan"
    assert ct.tr_kucuk("KÂĞIT") == "kağıt"
    assert ct.tr_kucuk("İstanbul") == "istanbul"  # ayrık yazılmış İ (I + U+0307)
    assert ct.tr_kucuk("i̇ç") == "iç"


# --- kök eşleşmesi ve puan kuralı -----------------------------------------------------------------------------
def test_puan_kurallari():
    d, y = MAGAZINE["dogru"], MAGAZINE["yanlis"]
    assert ct.puanla("Dergiyi bitirince bana at.", d, y) == ("dogru", ["dergi"])
    assert ct.puanla("Şarjörü bitirince bana at.", d, y) == ("yanlis", ["şarjör"])
    assert ct.puanla("Onu bitirince bana at.", d, y) == ("belirsiz", [])
    # yanlış anlamın kökü geçiyorsa doğru kök de geçse yanlış
    assert ct.puanla("Dergiyi değil, şarjörü ver.", d, y) == ("yanlis", ["şarjör"])
    assert ct.puanla("Şarjör de mermi de bitti.", d, y) == ("yanlis", ["şarjör", "mermi"])
    # çeviri yoksa (motor hatası) puan 'hata'
    assert ct.puanla(None, d, y) == ("hata", [])
    assert ct.puanla("   ", d, y) == ("hata", [])


def test_eslesme_turkce_buyuk_kucuk_harf_duyarsiz():
    d, y = MAGAZINE["dogru"], MAGAZINE["yanlis"]
    assert ct.puanla("DERGİYİ VER!", d, y)[0] == "dogru"
    assert ct.puanla("ŞARJÖRÜ VER!", d, y)[0] == "yanlis"
    assert ct.puanla("dergi", ["DERGİ"], [])[0] == "dogru"  # kök de dönüştürülür
    # I küçülünce ı olur: 'IŞIK' kökü 'ışıklar'da var, 'isik' yazımında yok
    assert ct.puanla("Işıklar söndü.", ["IŞIK"], [])[0] == "dogru"
    assert ct.puanla("isikler sondu", ["IŞIK"], [])[0] == "belirsiz"
    assert ct.puanla("İÇERİ GİR", [" iç"], [])[0] == "dogru"
    assert ct.puanla("Dükkânın önünde", ["dükkan"], [])[0] == "dogru"


def test_kok_basindaki_ve_sonundaki_bosluk_kelime_siniri():
    assert ct.puanla("Bütün gömlekler buruşuk.", ["ütü"], [])[0] == "dogru"  # düz alt dize: 'bütün'de var
    assert ct.puanla("Bütün gömlekler buruşuk.", [" ütü"], [])[0] == "belirsiz"  # yalnız kelime başı
    assert ct.puanla("Ütüyü nereye koydun?", [" ütü"], [])[0] == "dogru"
    assert ct.puanla("Gömlek mi? Ütü!", [" ütü"], [])[0] == "dogru"  # noktalama boşluk sayılır
    assert ct.puanla("Kara gün.", [" kar "], [])[0] == "belirsiz"  # yalnız tam kelime
    assert ct.puanla("Kar yağıyor.", [" kar "], [])[0] == "dogru"
    assert ct.puanla("Her yer kar.", [" kar "], [])[0] == "dogru"  # metnin sonu da kelime sınırı
    assert ct.puanla("O zaman Kenny'nin işi bitti.", [" işi bitti"], [])[0] == "dogru"  # kesme işareti
    assert ct.eslesen_kokler("Kar yağıyor", [" kar ", " kar yağ", "yok"]) == [" kar ", " kar yağ"]


def test_satiri_puanla_kayittan_hesaplar():
    assert ct.satiri_puanla(_satir("Şarjörü at.", **MAGAZINE)) == ("yanlis", ["şarjör"])
    assert ct.satiri_puanla(_satir(None, hata="zaman aşımı", **MAGAZINE)) == ("hata", [])
    # hata alanı doluysa çeviri metni olsa da sayılmaz (motor hatası)
    assert ct.satiri_puanla(_satir("Dergiyi at.", hata="boş çeviri döndü", **MAGAZINE)) == ("hata", [])
    assert ct.satiri_puanla(_satir("Dergiyi at.")) is None  # kökü olmayan satır puanlanmaz


# --- veri seti denetimi -----------------------------------------------------------------------------------------
def _veri_seti(tmp_path: Path, satir: dict) -> Path:
    yol = tmp_path / "set.json"
    sahne = {"id": "deneme", "satirlar": [{"en": "Lights out in twenty."}, {"en": "Toss me the magazine.", **satir}]}
    yol.write_text(json.dumps({"sahneler": [sahne]}, ensure_ascii=False), encoding="utf-8")
    return yol


def test_veri_seti_gecerli_puan_alanlari(tmp_path):
    sahneler = ct.veri_setini_oku(_veri_seti(tmp_path, {**MAGAZINE, "zor": True}))
    assert sahneler[0]["satirlar"][1]["kelime"] == "magazine"
    # yanlış kökü olmayan satır da geçerli
    ct.veri_setini_oku(_veri_seti(tmp_path, {"kelime": "magazine", "dogru": ["dergi"]}))


@pytest.mark.parametrize(("alanlar", "mesaj"), [
    ({"dogru": ["dergi"], "yanlis": ["şarjör"]}, "'kelime' yok"),
    ({"kelime": ["magazine", "clip"], "dogru": ["dergi"]}, "'kelime' yok"),
    ({"kelime": "magazine", "yanlis": ["şarjör"]}, "'dogru' listesi boş"),
    ({"kelime": "magazine", "dogru": "dergi"}, "'dogru' harf içeren"),
    ({"kelime": "magazine", "dogru": ["dergi", "  "]}, "'dogru' harf içeren"),
    ({"kelime": "magazine", "dogru": ["dergi"], "yanlis": [3]}, "'yanlis' harf içeren"),
    ({**MAGAZINE, "anlam": "gunluk"}, "'anlam' şunlardan biri"),
    ({**MAGAZINE, "zor": "evet"}, "'zor' true ya da false"),
    # doğru kök geçen her çeviride yanlış kök de geçer: veri seti hatası
    ({"kelime": "snow", "dogru": [" kar yağ"], "yanlis": [" kar "]}, "yanlış kök ' kar '"),
    ({"kelime": "magazine", "dogru": ["dergi"], "yanlis": ["DERGİ"]}, "yanlış kök 'DERGİ'"),
])
def test_veri_seti_hatali_puan_alanlari(tmp_path, alanlar, mesaj):
    with pytest.raises(SystemExit, match=mesaj):
        ct.veri_setini_oku(_veri_seti(tmp_path, alanlar))


def test_yanlis_kok_dogru_kokun_uzantisi_olabilir(tmp_path):
    """Yanlış kök doğru kökü içeriyorsa sorun yok: 'kov' doğru, 'kovala' yanlış ('kovaladı' yanlış sayılır)."""
    ct.veri_setini_oku(_veri_seti(tmp_path, {"kelime": "fire", "dogru": [" kov"], "yanlis": [" kovala"]}))
    assert ct.puanla("Beni kovacak.", [" kov"], [" kovala"])[0] == "dogru"
    assert ct.puanla("Beni kovalayacak.", [" kov"], [" kovala"])[0] == "yanlis"


def test_mevcut_veri_seti_bozulmadi():
    sahneler = ct.veri_setini_oku(KOK / "tools" / "ceviri_testi_seti.json")
    assert sahneler and ct.puan_ozeti(sahneler) is None  # puanlanan satırı yok


def test_cok_anlamli_veri_seti():
    sahneler = ct.veri_setini_oku(KOK / "tools" / "ceviri_testi_cokanlam.json")
    anlamlar: defaultdict[str, set[str]] = defaultdict(set)
    satirlar: Counter[str] = Counter()
    for sahne in sahneler:
        assert 2 <= len(sahne["satirlar"]) <= 4, sahne["id"]
        puanli = [r for r in sahne["satirlar"] if r.get("dogru")]
        assert len(puanli) == 1, sahne["id"]  # sahne başına bir kelime sınanır
        for r in sahne["satirlar"]:
            assert 2 <= len(r["en"].split()) <= 14, r["en"]
            satirlar[r["en"]] += 1
        anlamlar[puanli[0]["kelime"]].add(puanli[0]["anlam"])
    assert len(anlamlar) >= 30
    assert all(a == {"gundelik", "suc"} for a in anlamlar.values()), anlamlar  # her kelime iki bağlamda
    assert not [en for en, n in satirlar.items() if n > 1]  # karşılaştırma satırları metinle eşleştirir


# --- özet --------------------------------------------------------------------------------------------------------
def _ornek_sahneler() -> list[dict]:
    clip = {"kelime": "clip", "anlam": "suc", "dogru": ["şarjör"], "yanlis": [" klip "]}
    return [
        _sahne("a", _satir("Işıklar söner.", en="Lights out."),
               _satir("Dergiyi at.", en="Toss me the magazine.", **MAGAZINE)),
        _sahne("b", _satir("Şarjörü at.", en="Throw me the magazine.", zor=True, **MAGAZINE)),
        _sahne("c", _satir("Onu at.", en="Pass the magazine.", **MAGAZINE),
               _satir(None, hata="zaman aşımı", en="Last clip.", **clip),
               _satir("Son şarjör.", en="One clip left.", **clip)),
    ]


def test_puan_ozeti_toplam_gruplar_kelimeler():
    po = ct.puan_ozeti(_ornek_sahneler())
    assert po["toplam"] == {"satir": 5, "dogru": 2, "yanlis": 1, "belirsiz": 1, "hata": 1, "dogruluk": 50.0}
    assert list(po["gruplar"]) == ["gundelik", "suc", "zor"]
    assert po["gruplar"]["gundelik"]["dogruluk"] == pytest.approx(33.3)
    # hata veren satır yüzdeye girmez: clip 1 doğru / 1 puanlanan
    assert po["gruplar"]["suc"] == {"satir": 2, "dogru": 1, "yanlis": 0, "belirsiz": 0, "hata": 1, "dogruluk": 100.0}
    assert po["gruplar"]["zor"]["yanlis"] == 1
    assert list(po["kelimeler"]) == ["magazine", "clip"]  # ilk geçiş sırası
    assert po["kelimeler"]["magazine"]["dogruluk"] == pytest.approx(33.3)
    assert ct.puan_ozeti([_sahne("x", _satir("Selam."))]) is None


def test_yuzde_ve_fark_bicimi():
    assert ct._yuzde(77.78) == "%77,8" and ct._yuzde(100.0) == "%100" and ct._yuzde(None) == "-"
    a = {"dogru": 1, "yanlis": 2, "belirsiz": 0, "dogruluk": 33.3}
    b = {"dogru": 3, "yanlis": 0, "belirsiz": 0, "dogruluk": 100.0}
    assert ct._oran(a) == "%33,3 (1/3)"
    assert ct._fark(a, b) == "+66,7" and ct._fark(b, a) == "-66,7" and ct._fark(a, a) == "0"
    assert ct._fark(None, b) == "-"


# --- çeviri döngüsü ve rapor -------------------------------------------------------------------------------------
class SahteMotor:
    def __init__(self, ceviriler: dict[str, str]) -> None:
        self.ceviriler = ceviriler

    def translate(self, metin: str, baglam=()) -> str:
        return self.ceviriler[metin]


def test_sahneleri_cevir_puanlanan_satira_alan_ekler():
    kaynak = [{"id": "kogus", "satirlar": [{"en": "Lights out.", "beklenen": "ışıklar söner"},
                                           {"en": "Toss me the magazine.", "beklenen": "dergi", **MAGAZINE}]}]
    motor = SahteMotor({"Lights out.": "Işıklar söner.", "Toss me the magazine.": "Şarjörü bana at."})
    sonuc, kesildi = ct.sahneleri_cevir(motor, kaynak, 8, lambda m: m, lambda _en, tr: tr)
    assert not kesildi
    baglam, puanli = sonuc[0]["satirlar"]
    # puanlanmayan satırın kaydı önceki biçimle aynı
    assert set(baglam) == {"no", "en", "giden", "tr", "ms", "hata", "beklenen", "profil_ornegi"}
    assert puanli["kelime"] == "magazine" and puanli["dogru"] == ["dergi"]
    assert puanli["puan"] == "yanlis" and puanli["bulunan_kokler"] == ["şarjör"]


def test_rapor_puan_bolumu_ve_isaretleri():
    rapor = ct.rapor_md(_sonuc(_ornek_sahneler()))
    assert "## Çok anlamlı kelimeler" in rapor
    assert "| **Toplam** | 5 | 2 | 1 | 1 | 1 | %50 |" in rapor
    assert "| Gündelik anlam | 3 | 1 | 1 | 1 | 0 | %33,3 |" in rapor
    # kelime başına: en düşük doğruluk önce
    assert rapor.index("| magazine | 3 |") < rapor.index("| clip | 2 |")
    assert "### Yanlış satırlar (1)" in rapor
    assert "| magazine | `b` | Throw me the magazine. | Şarjörü at. |  | şarjör |" in rapor
    assert "### Belirsiz satırlar (1)" in rapor and "### Hata veren satırlar (1)" in rapor
    assert "[yanlış: şarjör]" in rapor and "[doğru]" in rapor and "[belirsiz]" in rapor
    assert "- **İstem özeti:** `0123456789ab`" in rapor


def test_rapor_puansiz_veri_setinde_bolum_yok():
    rapor = ct.rapor_md(_sonuc([_sahne("a", _satir("Işıklar söner.", en="Lights out."))]))
    assert "Çok anlamlı" not in rapor and "[doğru]" not in rapor


# --- karşılaştırma -----------------------------------------------------------------------------------------------
def _yaz(yol: Path, sonuc: dict) -> Path:
    yol.write_text(json.dumps(sonuc, ensure_ascii=False), encoding="utf-8")
    return yol


def test_karsilastirma_kelime_kelime(tmp_path):
    a = _ornek_sahneler()
    b = _ornek_sahneler()
    b[1]["satirlar"][0]["tr"] = "Dergiyi bana at."  # yanlış → doğru
    b[2]["satirlar"][2]["tr"] = "Son klip."  # doğru → yanlış
    b[2]["satirlar"][1].update(tr="Son şarjör.", hata=None)  # hata → doğru
    rapor = ct.karsilastirma_raporu(_yaz(tmp_path / "a.json", _sonuc(a)), _yaz(tmp_path / "b.json", _sonuc(b)))
    assert "## Çok anlamlı kelimeler" in rapor
    assert "| **Toplam** | %50 (2/4) | %60 (3/5) | +10 |" in rapor
    assert "| magazine | %33,3 (1/3) | %66,7 (2/3) | +33,3 |" in rapor
    assert "| clip | %100 (1/1) | %50 (1/2) | -50 |" in rapor
    assert "### Puanı değişen satırlar (3)" in rapor
    assert "B'de düzelen 1, bozulan 1, motor hatası yüzünden değişen 1." in rapor
    assert "| yanlış: Şarjörü at. | doğru: Dergiyi bana at. | düzeldi |" in rapor
    assert "| doğru: Son şarjör. | yanlış: Son klip. | bozuldu |" in rapor
    assert "[A: yanlış, B: doğru]" in rapor  # sahne tablosunda iki puan


def test_karsilastirma_eski_sonuclarla_bozulmadi(tmp_path):
    """Puanlanan satırı olmayan (ör. eski biçimli) iki sonuç: puan bölümü yok, önceki rapor aynen."""
    eski = _sonuc([_sahne("a", _satir("Işıklar söner.", en="Lights out."))])
    for alan in ("puanlama", "istem_ozeti"):
        eski.pop(alan)
    eski["bicim"] = 1
    rapor = ct.karsilastirma_raporu(_yaz(tmp_path / "a.json", eski), _yaz(tmp_path / "b.json", eski))
    assert "Çok anlamlı" not in rapor and "İstem özeti" not in rapor
    assert "Ortak satır: 1, ikisinde aynı çeviri: 1." in rapor


def test_karsilastirma_puanlanmayan_sonucla(tmp_path):
    a = _sonuc([_sahne("a", _satir("Işıklar söner.", en="Lights out."))])
    rapor = ct.karsilastirma_raporu(_yaz(tmp_path / "a.json", a), _yaz(tmp_path / "b.json", _sonuc(_ornek_sahneler())))
    assert "| **Toplam** | - | %50 (2/4) | - |" in rapor
    assert "Puanı değişen satır yok." in rapor


# --- uçtan uca, motorsuz ----------------------------------------------------------------------------------------
def test_motorsuz_calistirma_json_ve_rapor(tmp_path, monkeypatch):
    """--engine none çeviri yapmaz (İngilizceyi döndürür): puanlama hattı ağsız, uçtan uca çalışır."""
    monkeypatch.setattr(ct, "_kod_surumu", lambda: "test")
    veri = _veri_seti(tmp_path, {**MAGAZINE, "dogru": ["dergi", "magazine"]})  # İngilizce metinde 'magazine' var
    args = ct._argumanlar().parse_args(["--engine", "none", "--dataset", str(veri), "--out", str(tmp_path / "r.md"),
                                        "--json", str(tmp_path / "r.json")])
    assert ct.testi_calistir(args) == 0
    sonuc = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert sonuc["bicim"] == 2 and sonuc["istem_ozeti"] == ""
    assert sonuc["puanlama"]["toplam"]["dogru"] == 1
    puanli = sonuc["sahneler"][0]["satirlar"][1]
    assert puanli["puan"] == "dogru" and puanli["bulunan_kokler"] == ["magazine"]
    assert "## Çok anlamlı kelimeler" in (tmp_path / "r.md").read_text(encoding="utf-8")
