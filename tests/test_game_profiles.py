"""Oyun profilleri (altyazitr.game_profiles): paketle gelen JSON dosyaları şemaya ve boyut sınırlarına uyar,
bozuk dosyalar uygulamayı durdurmaz, bilinmeyen kimlik genel profile düşer. Ağ ve ekran gerekmez."""

import dataclasses
import json
import logging
import re
import sys
import tomllib
from pathlib import Path

import pytest

from altyazitr import game_profiles as gp
from altyazitr.config import TranslateCfg

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = sorted(gp.profiles_dir().glob("*.json"))
LOGGER = "altyazitr.game_profiles"


def _raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid(**kw) -> dict:
    data = {"name": "Deneme", "description": "Deneme profili.", "world": "A test world.", "rules": ["Be natural."],
            "keep": ["Vice City"], "glossary": [["heist", "soygun"]], "examples": [["It's done.", "İş tamam."]]}
    data.update(kw)
    return data


def _write(folder: Path, name: str, data) -> Path:
    path = folder / name
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def profiles_in(tmp_path, monkeypatch):
    """list_profiles/get_profile geçici klasörden okur; önbellek test öncesinde ve sonrasında temizlenir."""
    monkeypatch.setattr(gp, "profiles_dir", lambda: tmp_path)
    gp.load_profiles.cache_clear()
    yield tmp_path
    gp.load_profiles.cache_clear()


# --- paketle gelen profiller ------------------------------------------------------------------------------
def test_shipped_profiles_load_and_match_schema(caplog):
    assert {p.stem for p in SHIPPED} >= {gp.DEFAULT_PROFILE_ID, gp.GENERIC_PROFILE_ID}
    loaded = gp.load_profiles()
    assert len(loaded) == len(SHIPPED)  # hiçbiri atlanmadı
    for path in SHIPPED:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            p = gp.read_profile(path)
        assert not caplog.records, caplog.text  # tekrar, boyut ya da kimlik uyarısı yok
        assert loaded[p.id.casefold()] == p
        raw = _raw(path)
        assert set(raw) <= {"id", *gp._FIELDS}, "bilinmeyen alan (yazım hatası?)"
        assert raw.get("id", path.stem) == path.stem
        assert p.name and p.description
        assert all(isinstance(r, str) and r for r in (*p.rules, *p.keep))


def test_default_is_the_generic_profile():
    """Oyuna özel profil yok: çeviri her oyunda genel kurallarla yapılır (1.3.0'da GTA 6 profili kalktı)."""
    assert TranslateCfg().profile == gp.DEFAULT_PROFILE_ID == gp.GENERIC_PROFILE_ID == "genel"
    genel = gp.get_profile(gp.GENERIC_PROFILE_ID)
    assert genel.id == "genel" and genel.rules and genel.examples and not genel.world
    assert [p.id for p in gp.list_profiles()] == ["genel"]
    assert gp.get_profile("gta6") == genel  # eski ayar dosyasındaki kimlik genel profile düşer


@pytest.mark.parametrize("pid", ["yok_boyle_oyun", "", "   ", None])
def test_unknown_or_empty_id_falls_back_to_generic(pid):
    assert gp.get_profile(pid) == gp.get_profile(gp.GENERIC_PROFILE_ID)


def test_profile_id_is_case_insensitive():
    assert gp.get_profile(" GENEL ").id == "genel"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_size_limits(path):
    p = gp.read_profile(path)
    assert gp.size_warnings(p) == []
    assert len(p.world.split()) <= 90
    assert len(p.rules) <= 12 and all(len(r.split()) <= 25 for r in p.rules)
    assert len(p.keep) <= 60 and len(p.glossary) <= 60 and len(p.examples) <= 14
    assert gp.text_size(p) < 6000
    if p.id == "genel":
        assert 4 <= len(p.examples) <= 6


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_keep_unique_and_pairs_filled(path):
    raw = _raw(path)  # ham dosya: yükleyici tekrarları sessizce atmadan önce
    keep = [k.casefold() for k in raw["keep"]]
    assert len(keep) == len(set(keep)), "keep içinde tekrar var"
    terms = [en.casefold() for en, _tr in raw["glossary"]]
    assert len(terms) == len(set(terms)), "glossary içinde tekrar var"
    assert not set(keep) & set(terms), "aynı terim hem keep hem glossary'de"
    for pair in raw["glossary"] + raw["examples"]:
        assert isinstance(pair, list) and len(pair) == 2, pair
        assert all(isinstance(s, str) and s.strip() == s and s for s in pair), pair


def test_generic_profile_keeps_proper_names():
    """Özel isimler Türkçedeki kural gibi çevrilmez, ekleri kesme işaretiyle alır (Lucia'yı, Vice City'de)."""
    p = gp.get_profile("genel")
    names = next(r for r in p.rules if "proper names" in r)
    assert all(n in names for n in ("Vice City", "Lucia", "John", "Lucia'yı", "Vice City'de", "apostrophe"))
    # örnekler tuş adı uydurmaz: Türkçede geçen her tuş adı İngilizcesinde de vardır
    buttons = re.compile(r"\b(?:[LR][123]|X|Square|Triangle|Circle|Options|Touchpad)\b")
    for en, tr in p.examples:
        assert set(buttons.findall(tr)) <= set(buttons.findall(en)), en


# --- yükleyici ----------------------------------------------------------------------------------------------
def test_broken_files_do_not_crash(tmp_path, caplog):
    _write(tmp_path, "iyi.json", _valid())
    _write(tmp_path, "bozuk.json", '{"name": "Yarım", ')  # geçersiz JSON
    _write(tmp_path, "liste.json", [1, 2, 3])  # en üstte nesne yok
    _write(tmp_path, "eksik.json", {k: v for k, v in _valid().items() if k != "rules"})
    _write(tmp_path, "tip.json", _valid(rules="Be natural."))  # liste yerine metin
    _write(tmp_path, "bos_ad.json", _valid(keep=["Vice City", "  "]))  # boş girdi
    _write(tmp_path, "cift.json", _valid(glossary=[["heist"]]))  # çift değil
    _write(tmp_path, "bos_tr.json", _valid(glossary=[["heist", ""]]))  # boş karşılık
    _write(tmp_path, "ad_yok.json", _valid(name=""))
    _write(tmp_path, "sayi.json", _valid(examples=[["It's done.", 5]]))
    (tmp_path / "utf16.json").write_text(json.dumps(_valid()), encoding="utf-16")  # UTF-8 değil
    (tmp_path / "klasor.json").mkdir()  # dosya değil: sessizce geçilir
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        profiles = gp.load_profiles(tmp_path)
    assert list(profiles) == ["iyi"]
    for stem in ("bozuk", "liste", "eksik", "tip", "bos_ad", "cift", "bos_tr", "ad_yok", "sayi", "utf16"):
        assert any(f"{stem}.json" in r.getMessage() for r in caplog.records), stem


def test_missing_folder_gives_no_profiles(tmp_path):
    assert dict(gp.load_profiles(tmp_path / "yok")) == {}


def test_bom_whitespace_and_duplicates_are_tolerated(tmp_path, caplog):
    data = _valid(rules=["Be\n   natural."], keep=["Vice City", "vice city", "Leonida"],
                  glossary=[["heist", "soygun"], ["Heist", "vurgun"]], world="")
    path = tmp_path / "bom.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")  # Not Defteri BOM ekler
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        p = gp.read_profile(path)
    assert p.rules == ("Be natural.",)  # satır sonu istemin madde listesini bozmasın
    assert p.keep == ("Vice City", "Leonida") and p.glossary == (("heist", "soygun"),)
    assert p.world == "" and "tekrar" in caplog.text


def test_id_comes_from_file_name(tmp_path, caplog):
    path = _write(tmp_path, "dosya_adi.json", _valid(id="baska"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert gp.read_profile(path).id == "dosya_adi"
    assert "dosya adıyla aynı değil" in caplog.text


def test_size_limits_warn_or_reject(tmp_path, caplog):
    _write(tmp_path, "uzun.json", _valid(rules=[f"Rule number {i}." for i in range(gp.MAX_RULES + 3)]))
    _write(tmp_path, "dev.json", _valid(world="word " * (gp.HARD_MAX_CHARS // 5 + 10)))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        profiles = gp.load_profiles(tmp_path)
    assert list(profiles) == ["uzun"]  # sınırı aşan uyarıyla yüklenir, aşırı büyük olan yüklenmez
    assert "önerilen boyutu aşıyor" in caplog.text and "çok uzun" in caplog.text


def test_list_order_and_generic_fallback(profiles_in):
    # klasör boş ya da okunamıyor: (içi boş) genel profil yine listede, her kimlik ona düşer
    assert [p.id for p in gp.list_profiles()] == ["genel"]
    assert gp.get_profile("gta6").id == "genel" and gp.get_profile(None).rules == ()
    gp.load_profiles.cache_clear()
    for stem, name in (("zz", "Zebra"), ("genel", "Genel"), ("aa", "Mafia"), ("gta6", "GTA 6"), ("bb", "Araba")):
        _write(profiles_in, f"{stem}.json", _valid(name=name))
    assert [p.id for p in gp.list_profiles()] == ["genel", "bb", "gta6", "aa", "zz"]


def test_cached_profiles_cannot_be_modified():
    profiles = gp.load_profiles()
    assert gp.load_profiles() is profiles  # önbellek
    with pytest.raises(TypeError):
        profiles["x"] = None  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        gp.get_profile("genel").name = "x"  # type: ignore[misc]


# --- paketleme ----------------------------------------------------------------------------------------------
def test_profiles_are_packaged():
    """pip paketi ("data/*" alt klasörleri kapsamaz) ve PyInstaller paketi profilleri içerir."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "data/oyun_profilleri/*" in cfg["tool"]["setuptools"]["package-data"]["altyazitr"]
    spec = (ROOT / "RobzVision.spec").read_text(encoding="utf-8")
    assert '("altyazitr/data", "altyazitr/data")' in spec  # PyInstaller klasörü alt klasörleriyle kopyalar
    assert gp.profiles_dir() == ROOT / "altyazitr" / "data" / "oyun_profilleri"


def test_profiles_dir_in_pyinstaller_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert gp.profiles_dir() == tmp_path / "altyazitr" / "data" / "oyun_profilleri"
