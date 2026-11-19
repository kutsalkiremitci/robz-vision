"""Çeviri profilleri: yapay zekâ çevirisine (Ollama, Gemini) kuralları, çevrilmeyecek adları, sözlüğü ve üslup
örneklerini veren JSON dosyaları. Uygulama yalnız genel profili (``genel.json``) kullanır: 1.3.0'da oyuna özel
profil (GTA 6) ve arayüzdeki profil seçimi kaldırıldı. Yükleyici ve şema, kuralların veri olarak tutulması ve
denetlenmesi için duruyor.

Her profil ``altyazitr/data/oyun_profilleri/`` altında bir JSON dosyasıdır; dosya adı (uzantısız) profilin
kimliğidir: ``genel.json`` → ``genel``. Bu modül dosyaları okur ve doğrular; istemi ``translate.py`` kurar.
Bozuk dosya uygulamayı durdurmaz: günlüğe uyarı yazılır, dosya atlanır. Bilinmeyen ya da boş kimlik (ör. eski
ayar dosyasındaki ``gta6``) genel profile düşer; genel profilin dosyası da okunamazsa içi boş bir genel profil
kullanılır. ``id`` dışındaki tüm alanlar zorunludur. Metinler boş olamaz; yalnız ``world`` boş bırakılabilir.

    Alan         Tür             İçerik
    id           metin           İsteğe bağlı; yazılırsa dosya adıyla aynı olmalı
    name         metin           Arayüzde görünen ad, ör. "GTA 6"
    description  metin           Arayüz ipucu: 1-2 sade Türkçe cümle
    world        metin           Model için İngilizce dünya ve ton özeti, tek paragraf (en fazla 90 kelime)
    rules        [metin]         Model için İngilizce çeviri kuralları (en fazla 12, her biri en fazla 25 kelime)
    keep         [metin]         Asla çevrilmeyecek adlar: kişi, yer, çete, marka, silah, araç (en fazla 60)
    glossary     [["en", "tr"]]  Tercih edilen karşılıklar, ör. ["stash", "zula"] (en fazla 60 çift)
    examples     [["en", "tr"]]  Üslup örnekleri: kısa ve ÖZGÜN satırlar (en fazla 14 çift)

Profil her çeviri isteğinde modele gider: kısa tutun, metnin toplamı (text_size) ~3500 karakteri (~1000-1200
token) geçmesin. Sınırları aşan profil uyarıyla yüklenir; HARD_MAX_CHARS'ı aşan yüklenmez. Örnekler oyunun
senaryosundan, altyazılarından ya da fragmanlarından alıntı olmamalı (telif): kendi cümlelerinizi yazın.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from altyazitr.paths import resource_dir

log = logging.getLogger(__name__)

GENERIC_PROFILE_ID = "genel"
# Uygulama oyuna özel profil kullanmaz: çeviri her oyunda genel kurallarla yapılır (1.3.0'da GTA 6 profili kalktı)
DEFAULT_PROFILE_ID = GENERIC_PROFILE_ID

# Önerilen boyut sınırları. Aşan profil yine yüklenir ama günlüğe uyarı yazılır (çeviri yavaşlar).
MAX_WORLD_WORDS = 90
MAX_RULES = 12
MAX_RULE_WORDS = 25
MAX_KEEP = 60
MAX_GLOSSARY = 60
MAX_EXAMPLES = 14
MAX_CHARS = 6000  # modele giden metnin toplamı (text_size)
# Bunu aşan profil yüklenmez: istem hem çok yavaşlar hem modelin bağlam penceresini (num_ctx) zorlar
HARD_MAX_CHARS = 12000

_FIELDS = ("name", "description", "world", "rules", "keep", "glossary", "examples")

T = TypeVar("T")


@dataclass(frozen=True)
class GameProfile:
    id: str  # dosya adı (uzantısız), ör. "gta6"
    name: str  # arayüzde görünen ad, ör. "GTA 6"
    description: str  # arayüz ipucu: 1-2 sade Türkçe cümle
    world: str  # MODEL İÇİN İngilizce dünya/ton özeti (tek paragraf)
    rules: tuple[str, ...]  # MODEL İÇİN İngilizce çeviri kuralları
    keep: tuple[str, ...]  # asla çevrilmeyecek özel adlar
    glossary: tuple[tuple[str, str], ...]  # (en, tr): tercih edilen karşılıklar
    examples: tuple[tuple[str, str], ...]  # (en, tr): üslup örnekleri


# genel.json okunamazsa: oyuna özel hiçbir şey eklemeyen genel profil (çeviri istemin ortak kurallarıyla sürer)
_EMPTY_GENERIC = GameProfile(GENERIC_PROFILE_ID, "Genel (tüm oyunlar)", "Herhangi bir oyun için genel çeviri.",
                             "", (), (), (), ())


class ProfileError(ValueError):
    """Profil dosyası okunamadı ya da şemaya uymuyor; mesaj Türkçedir."""


def profiles_dir() -> Path:
    """Paketle gelen profillerin klasörü (PyInstaller paketinde sys._MEIPASS altında)."""
    return resource_dir() / "data" / "oyun_profilleri"


def text_size(p: GameProfile) -> int:
    """Modele giden metnin karakter sayısı (dünya, kurallar, adlar, sözlük, örnekler): profil boyutunun
    ölçüsü. Gemma 4'te İngilizce-Türkçe karışık profil metninde ~3 karakter ≈ 1 token."""
    pairs = (*p.glossary, *p.examples)
    return len(p.world) + sum(map(len, p.rules)) + sum(map(len, p.keep)) + sum(len(a) + len(b) for a, b in pairs)


def size_warnings(p: GameProfile) -> list[str]:
    """Aşılan önerilen boyut sınırları (Türkçe açıklamalar); boş liste: profil sınırların içinde."""
    out = []
    words = len(p.world.split())
    if words > MAX_WORLD_WORDS:
        out.append(f"world {words} kelime (en fazla {MAX_WORLD_WORDS})")
    for name, items, cap in (("rules", p.rules, MAX_RULES), ("keep", p.keep, MAX_KEEP),
                             ("glossary", p.glossary, MAX_GLOSSARY), ("examples", p.examples, MAX_EXAMPLES)):
        if len(items) > cap:
            out.append(f"{name} {len(items)} öğe (en fazla {cap})")
    long_rules = [str(i) for i, r in enumerate(p.rules, 1) if len(r.split()) > MAX_RULE_WORDS]
    if long_rules:
        out.append(f"{MAX_RULE_WORDS} kelimeden uzun kural: {', '.join(long_rules)}. madde")
    size = text_size(p)
    if size > MAX_CHARS:
        out.append(f"toplam {size} karakter (en fazla {MAX_CHARS})")
    return out


# --- doğrulama ----------------------------------------------------------------------------------------------
def _clean(s: str) -> str:
    """Boşlukları tekleştirir: satır sonu istemin biçimini (madde listesi, EN:/TR: satırları) bozmasın."""
    return " ".join(s.split())


def _text(data: dict, key: str, *, allow_empty: bool = False) -> str:
    v = data[key]
    if not isinstance(v, str):
        raise ProfileError(f'"{key}" metin olmalı')
    v = _clean(v)
    if not v and not allow_empty:
        raise ProfileError(f'"{key}" boş olamaz')
    return v


def _texts(data: dict, key: str) -> tuple[str, ...]:
    items = data[key]
    if not isinstance(items, list):
        raise ProfileError(f'"{key}" bir liste olmalı: ["…", "…"]')
    out = []
    for i, v in enumerate(items, 1):
        if not isinstance(v, str) or not _clean(v):
            raise ProfileError(f'"{key}" listesinin {i}. öğesi boş olmayan bir metin olmalı')
        out.append(_clean(v))
    return tuple(out)


def _pairs(data: dict, key: str) -> tuple[tuple[str, str], ...]:
    items = data[key]
    if not isinstance(items, list):
        raise ProfileError(f'"{key}" bir liste olmalı: [["en", "tr"], …]')
    out = []
    for i, v in enumerate(items, 1):
        if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) and _clean(x) for x in v)):
            raise ProfileError(f'"{key}" listesinin {i}. öğesi iki dolu metin olmalı: ["en", "tr"]')
        out.append((_clean(v[0]), _clean(v[1])))
    return tuple(out)


def _unique(items: tuple[T, ...], term: Callable[[T], str], field: str, source: str) -> tuple[T, ...]:
    """Tekrar edenleri (büyük/küçük harf fark etmez) ilk geçeni bırakarak atar."""
    seen: set[str] = set()
    out: list[T] = []
    dups: list[str] = []
    for it in items:
        k = term(it).casefold()
        if k in seen:
            dups.append(term(it))
        else:
            seen.add(k)
            out.append(it)
    if dups:
        log.warning("Oyun profili %s: %s içinde tekrar edenler atlandı: %s", source, field, ", ".join(dups))
    return tuple(out)


def read_profile(path: Path) -> GameProfile:
    """Tek bir profil dosyasını okur ve doğrular. Sorun varsa ProfileError (Türkçe mesaj)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: Not Defteri'nin eklediği BOM sorun olmasın
    except OSError as e:
        raise ProfileError(f"dosya okunamadı: {e}") from e
    except UnicodeDecodeError as e:
        raise ProfileError("dosya UTF-8 olarak kaydedilmemiş") from e
    except json.JSONDecodeError as e:
        raise ProfileError(f"geçersiz JSON (satır {e.lineno}, sütun {e.colno}): {e.msg}") from e
    if not isinstance(data, dict):
        raise ProfileError("dosyanın en üstünde bir JSON nesnesi ({…}) olmalı")
    missing = [k for k in _FIELDS if k not in data]
    if missing:
        raise ProfileError("eksik alan: " + ", ".join(missing))
    pid = path.stem
    if "id" in data and data["id"] != pid:
        log.warning('Oyun profili %s: "id" (%r) dosya adıyla aynı değil; kimlik olarak dosya adı kullanılıyor',
                    path.name, data["id"])
    p = GameProfile(
        id=pid,
        name=_text(data, "name"),
        description=_text(data, "description"),
        world=_text(data, "world", allow_empty=True),
        rules=_texts(data, "rules"),
        keep=_unique(_texts(data, "keep"), lambda s: s, "keep", path.name),
        glossary=_unique(_pairs(data, "glossary"), lambda pair: pair[0], "glossary", path.name),
        examples=_pairs(data, "examples"),
    )
    size = text_size(p)
    if size > HARD_MAX_CHARS:
        raise ProfileError(f"profil çok uzun: {size} karakter (en fazla {HARD_MAX_CHARS}); çeviri çok yavaşlar")
    problems = size_warnings(p)
    if problems:
        log.warning("Oyun profili %s önerilen boyutu aşıyor, çeviri yavaşlayabilir: %s", path.name,
                    "; ".join(problems))
    return p


# --- yükleme ------------------------------------------------------------------------------------------------
@lru_cache(maxsize=8)
def load_profiles(folder: str | Path | None = None) -> Mapping[str, GameProfile]:
    """Klasördeki geçerli profiller (varsayılan: paketle gelenler); anahtar küçük harfe çevrilmiş kimliktir.
    Bozuk dosyalar uyarıyla atlanır, hiçbir hata yukarı çıkmaz. Sonuç önbellektedir: dosyalar değişirse
    load_profiles.cache_clear()."""
    d = Path(folder) if folder is not None else profiles_dir()
    try:
        paths = sorted(p for p in d.glob("*.json") if p.is_file())
    except OSError as e:
        log.warning("Oyun profilleri klasörü okunamadı (%s): %s", d, e)
        paths = []
    out: dict[str, GameProfile] = {}
    for path in paths:
        try:
            p = read_profile(path)
        except ProfileError as e:
            log.warning("Oyun profili atlandı (%s): %s", path.name, e)
            continue
        except Exception:  # beklenmeyen hata da uygulamayı durdurmasın
            log.warning("Oyun profili atlandı (%s)", path.name, exc_info=True)
            continue
        key = p.id.casefold()
        if key in out:
            log.warning("Oyun profili atlandı (%s): aynı kimlikte başka bir profil var", path.name)
            continue
        out[key] = p
    if out:
        log.info("Oyun profilleri yüklendi (%s): %s", d, ", ".join(out))
    else:
        log.warning("Oyun profili bulunamadı: %s", d)
    return MappingProxyType(out)


def list_profiles() -> list[GameProfile]:
    """Sıra: önce genel, sonra diğerleri ada göre. Genel profil her zaman listededir."""
    rest = dict(load_profiles())
    head = rest.pop(GENERIC_PROFILE_ID, _EMPTY_GENERIC)
    return [head, *sorted(rest.values(), key=lambda p: (p.name.casefold(), p.id.casefold()))]


def get_profile(pid: str | None) -> GameProfile:
    """Kimliği verilen profil (büyük/küçük harf fark etmez). Bilinmeyen ya da boş kimlikte genel profil."""
    profiles = load_profiles()
    key = (pid or "").strip().casefold()
    if key in profiles:
        return profiles[key]
    if key and key != GENERIC_PROFILE_ID:
        log.warning("Oyun profili bulunamadı: %r; genel profil kullanılıyor", pid)
    return profiles.get(GENERIC_PROFILE_ID, _EMPTY_GENERIC)
