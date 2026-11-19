"""tools/ocr_hiz.py: sentetik sahneler, değerlendirme ve kısa bir uçtan uca ölçüm."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ARAC = Path(__file__).resolve().parents[1] / "tools" / "ocr_hiz.py"


@pytest.fixture(scope="module")
def oh():
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location("ocr_hiz", ARAC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ocr_hiz"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sahne_secimi(oh):
    assert [s.ad for s in oh.sahne_sec("efekt")] == ["daktilo", "belirme"]
    assert [s.ad for s in oh.sahne_sec("hizli,bos_gece")] == ["hizli", "bos_gece"]
    assert len(oh.sahne_sec("hepsi")) == sum(len(g) for g in oh.GRUPLAR.values())
    with pytest.raises(SystemExit):
        oh.sahne_sec("olmayan")


def test_cizelge_anlari_ve_kareler(oh):
    """Altyazıların anları kare sınırlarında ve sahneler art arda; daktilo ve belirme satırı tamamı görününce 'tam'."""
    c = oh.Cizelge(oh.sahne_sec("hizli,daktilo"), boyut=(400, 60), fps=10)
    assert c.kare(0).shape == (60, 400, 3) and len(c.kareler) == round(c.sure * 10)
    hizli = [s for ad, s in c.satirlar if ad == "hizli"]
    assert [s.t_on for s in hizli[1:]] == [s.t_off for s in hizli[:-1]]  # boşluksuz art arda
    daktilo = [s for ad, s in c.satirlar if ad == "daktilo"]
    assert daktilo[0].t_on >= c.bolumler[0][2] and all(s.t_tam > s.t_on + 0.5 for s in daktilo)
    assert [ad for ad, _a, _b in c.bolumler] == ["hizli", "daktilo"]


def test_degerlendirme(oh):
    """Gecikme ilk doğru okumadan; yarım okuma "yanlış gönderilen", eşleşmeyen okuma "hayalet", okunmayan satır
    "kaçan" sayılır."""
    c = oh.Cizelge(oh.sahne_sec("hizli"), boyut=(400, 60), fps=10)
    s0, s1 = c.satirlar[0][1], c.satirlar[1][1]

    def sub(text):
        return ("sub", 0.0, {"text": text, "speaker": None})
    olcum = oh.Olcum(olaylar=[(s0.t_on + 0.08, sub("Go, go")), (s0.t_on + 0.12, sub("Go, go, go!")),
                              (s1.t_on + 0.1, sub("Which way?")), (s1.t_on + 0.2, sub("LIQUOR 24 HR MOTEL"))],
                     ocr_ms=[30.0, 40.0], islemci=[(0.0, 0.0), (c.sure, c.sure * 0.25)])
    r = oh.degerlendir(c, olcum)
    assert r["satir"] == 6 and r["kacan"] == 4 and r["hic_dogru_degil"] == 0
    assert r["yanlis_gonderilen"] == 1 and r["hayalet"] == 1
    assert r["gecikme_ortanca_ms"] == pytest.approx(110, abs=1) and r["islemci_yuzde"] == pytest.approx(25, abs=1)
    assert "kaçan 4" in oh.rapor_yaz(r)
    assert "gecikme ortanca (ms)" in oh.karsilastir(r, r)


def test_ayar_kur(oh):
    args = oh._argumanlar().parse_args(["--mod", "interval", "--set", "ocr.interval_ms=250",
                                        "--set", "ocr.text_detector=hayır", "--set", "capture.fps=10"])
    s = oh.ayar_kur(args)
    assert (s.ocr.detect_mode, s.ocr.interval_ms, s.ocr.text_detector, s.capture.fps) == ("interval", 250, False, 10)
    assert s.capture.mode == "window" and s.capture.is_ready()
    with pytest.raises(SystemExit):
        oh.ayar_kur(oh._argumanlar().parse_args(["--set", "ocr.olmayan=1"]))


def test_kisa_uctan_uca_olcum(oh):
    """Gerçek işlem hattı (yakalama + OCR iş parçacıkları, gerçek modeller) tek satırlık kısa bir sahnede: satır
    doğru okunur, gecikme ve işlemci ölçülür."""
    pytest.importorskip("rapidocr")
    from altyazitr import pipeline

    from rapidfuzz import fuzz

    sahne = oh.Sahne("deneme", "beton", "duz", [oh.Satir("We need to lose them before the bridge.", 1.2, 0.4)],
                     bas=0.4)
    c = oh.Cizelge([sahne])
    isinma = oh.birlestir(oh.zemin("gece", 179, 1213), *oh.yazi_katmani(["Warm up."], 1213, 179,
                                                                          oh.BICIMLER["kenarli"]))
    args = oh._argumanlar().parse_args([])
    cache = pipeline.CACHE_FILE
    sonuc = oh.degerlendir(c, oh.olc(c, oh.ayar_kur(args), pipeline, isinma))
    assert pipeline.CACHE_FILE == cache  # geçici değişiklik geri alındı
    assert sonuc["kacan"] == 0
    assert fuzz.ratio(sonuc["satirlar"][0]["son"], "We need to lose them before the bridge.") > 90
    assert sonuc["gecikme_ortanca_ms"] is not None and sonuc["gecikme_ortanca_ms"] < 1000
    assert sonuc["islemci_yuzde"] is not None and np.isfinite(sonuc["islemci_yuzde"])
