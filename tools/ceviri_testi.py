"""Çeviri kalite ve hız testi: hazır sahne metinlerini uygulamanın gerçek çeviri motoruyla çevirir.

Veri seti (varsayılan tools/ceviri_testi_seti.json) GTA 6 tarzında, bu proje için özgün yazılmış
sahnelerden oluşur. Her sahne uygulamadaki gibi sırayla çevrilir:

* Sözlük işlem hattındaki gibi kurulur (build_glossary: kullanıcı sözlüğü + oyun profilinin çevrilmeyecek
  adları; bu işlev olmayan eski sürümde yalnız kullanıcı sözlüğü).
* Satır önce işlem hattının OCR düzeltmesinden (textproc.fix_ocr) geçer. Metin değiştiyse raporda motora
  giden hali de görünür.
* Motora, aynı sahnede önceki satırların son N tanesi bağlam olarak gider: (İngilizce, aracın kendi Türkçe
  çıktısı). Her sahne boş bağlamla başlar.
* Çeviriden sonra uç noktalama korunur (restore_end_punctuation).
* Önbellek ve karma mod (önce hızlı çeviri) devre dışıdır; yalnız seçilen motor ölçülür.

Ölçümden önce sayılmayan bir ısınma çağrısı yapılır. Gecikme satır başına time.perf_counter ile ölçülür;
özet (p50, p90, en yüksek) yalnız başarılı satırlardan hesaplanır. Hata veren satır (zaman aşımı vb.)
raporda "HATA: ..." olarak görünür, bağlama işlem hattındaki gibi yalnız İngilizcesiyle girer ve test sürer.
Oyun profilinin üslup örneklerinde de geçen satırlar raporda işaretlenir: model bunların cevabını örnekten
alabileceği için sonuç olduğundan iyi görünür.

Çok anlamlı kelime puanlaması (ör. tools/ceviri_testi_cokanlam.json): veri setinde "dogru"/"yanlis" kök listesi
olan satır kendiliğinden puanlanır. Kökler çeviride Türkçe büyük/küçük harf kuralıyla (İ/i, I/ı) alt dize olarak
aranır, noktalama boşluk sayılır; kökün başındaki ya da sonundaki boşluk kelime sınırı ister (" ütü" "bütün"de
eşleşmez). Yanlış anlamın kökü geçiyorsa satır "yanlış", geçmiyorsa ve doğru anlamın kökü geçiyorsa "doğru", ikisi
de yoksa "belirsiz"dir (elle bakılır). Rapor toplam, anlama göre (gündelik / suç), zor örnekler ve kelime başına
doğruluğu verir, yanlış ve belirsiz satırları listeler; --compare iki sonucu kelime kelime karşılaştırır.

Kullanım:
  python tools/ceviri_testi.py                                  # Ollama + gemma4:12b, rapor ekrana
  python tools/ceviri_testi.py --engine ollama --model gemma4:12b --out gemma.md --json gemma.json
  python tools/ceviri_testi.py --model qwen3.5:9b --context 3 --json qwen.json
  python tools/ceviri_testi.py --engine quickmt --device cuda --out quickmt.md
  python tools/ceviri_testi.py --engine gemini --timeout 10 --pause 4   # ücretsiz kotaya takılmamak için
  python tools/ceviri_testi.py --scene soygun_plani,polis_kovalamacasi --limit 5
  python tools/ceviri_testi.py --dataset tools/ceviri_testi_cokanlam.json --out cokanlam.md --json cokanlam.json
  python tools/ceviri_testi.py --engine none                    # motorsuz kuru çalıştırma: veri seti ve rapor
  python tools/ceviri_testi.py --compare gemma.json qwen.json --out karsilastirma.md

Varsayılanlar: --engine ollama, --timeout 20. Model, profil ve bağlam satırı verilmezse uygulamanın
varsayılan çeviri ayarları (TranslateCfg) geçerlidir; kullanıcının ayar dosyası değiştirilmez. Gemini
anahtarı GEMINI_API_KEY, DeepL anahtarı DEEPL_AUTH_KEY ortam değişkeninden, yoksa uygulamanın kayıtlı
ayarlarından okunur ve hiçbir çıktıya yazılmaz. İlerleme stderr'e, rapor --out dosyasına (verilmezse
stdout'a) yazılır.

Veri setine satır eklerken: metin özgün olsun (oyun senaryosundan ya da başka eserlerden alıntı yok),
2-14 kelime, sahne içindeki sıra anlamlı. "beklenen", iyi bir çevirinin mutlaka taşıması gereken anlamı ya da
terimi kısaca söyler. Puanlanan satır ayrıca "kelime" (tek kelime ya da deyim), "dogru" (boş olamaz) ve "yanlis"
listelerini, isteğe bağlı olarak "anlam" ("gundelik": suç dışı anlam doğru, "suc": suç anlamı doğru) ve "zor"
(true: ipucu zayıf) alanlarını taşır.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERI_SETI = Path(__file__).resolve().with_name("ceviri_testi_seti.json")
MOTORLAR = ("ollama", "quickmt", "gemini", "deepl", "none")
# İşlem hattında karma mod (önce hızlı çeviri) ve istek zaman aşımı yalnız bu motorlarda geçerli
UZAK_MOTORLAR = ("ollama", "gemini", "deepl")
ISINMA_METNI = "Alright, let's get this over with."
# --json çıktısının biçim sürümü; alanlar değişirse artırılır. 2: istem özeti, çok anlamlı kelime puanlaması
# (puanlanan satırda kelime, anlam, zor, dogru, yanlis, puan, bulunan_kokler; sonuçta puanlama)
BICIM = 2
ORNEK_ISARETI = "[profil örneğinde var]"  # profilin üslup örneklerinde de geçen satırın rapordaki işareti
PUAN_ALANLARI = ("kelime", "anlam", "zor", "dogru", "yanlis")  # veri setinde puanlanan satırın alanları
ANLAMLAR = ("gundelik", "suc")  # "anlam": doğru olan suç dışı anlam mı, suç (silah, uyuşturucu, polis) anlamı mı
PUAN_ADI = {"dogru": "doğru", "yanlis": "yanlış", "belirsiz": "belirsiz", "hata": "hata"}
GRUP_ADI = {"gundelik": "Gündelik anlam", "suc": "Suç/argo anlamı", "zor": "Zor örnekler"}
_PUAN_SIRASI = {"yanlis": 0, "belirsiz": 1, "dogru": 2}  # karşılaştırmada düzelme ya da bozulma yönü

Kayit = dict[str, Any]  # JSON'a giden sözlükler: sonuç, sahne, satır
Baglam = list[tuple[str, str]]  # (İngilizce, Türkçe) çiftleri


def _bilgi(mesaj: str) -> None:
    """Durum ve ilerleme mesajı. stderr'e gider; rapor stdout'a yazılabilir."""
    print(mesaj, file=sys.stderr, flush=True)


def _uyar(mesaj: str) -> None:
    _bilgi(f"Uyarı: {mesaj}")


# --- veri seti -----------------------------------------------------------------
def veri_setini_oku(yol: Path) -> list[Kayit]:
    """Veri setini okur ve biçimini denetler; sorun varsa anlaşılır bir mesajla çıkar."""
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Veri seti bulunamadı: {yol}") from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"Veri seti geçerli JSON değil ({yol}): {e}") from None
    sahneler = veri.get("sahneler") if isinstance(veri, dict) else None
    if not isinstance(sahneler, list) or not sahneler:
        raise SystemExit(f"Veri setinde 'sahneler' listesi yok ya da boş: {yol}")
    gorulen: set[str] = set()
    for i, sahne in enumerate(sahneler, 1):
        sid = sahne.get("id") if isinstance(sahne, dict) else None
        if not isinstance(sid, str) or not sid.strip():
            raise SystemExit(f"Veri setinin {i}. sahnesinde 'id' yok.")
        if sid in gorulen:
            raise SystemExit(f"Sahne kimliği iki kez geçiyor: {sid}")
        gorulen.add(sid)
        satirlar = sahne.get("satirlar")
        if not isinstance(satirlar, list) or not satirlar:
            raise SystemExit(f"'{sid}' sahnesinde 'satirlar' listesi yok ya da boş.")
        for j, satir in enumerate(satirlar, 1):
            en = satir.get("en") if isinstance(satir, dict) else None
            if not isinstance(en, str) or not en.strip():
                raise SystemExit(f"'{sid}' sahnesinin {j}. satırında 'en' metni yok.")
            if any(alan in satir for alan in PUAN_ALANLARI):
                _puan_alanlarini_denetle(f"'{sid}' sahnesinin {j}. satırı", satir)
    return sahneler


def _puan_alanlarini_denetle(yer: str, satir: Kayit) -> None:
    """Puanlanan satırın alanları: tek bir kelime, boş olmayan doğru kökleri, yanlış kökleri (boş olabilir),
    isteğe bağlı anlam ve zor. Doğru kökün geçtiği her çeviride yanlış kök de geçecekse (yanlış kök doğru kökün
    içindeyse) o doğru kök hiç işe yaramaz: veri seti hatası sayılır."""
    kelime = satir.get("kelime")
    if not isinstance(kelime, str) or not kelime.strip():
        raise SystemExit(f"{yer}: puanlanan satırda 'kelime' yok (tek bir kelime ya da deyim, metin olarak).")
    kokler: dict[str, list[str]] = {}
    for alan in ("dogru", "yanlis"):
        liste = satir.get(alan, [])
        if not isinstance(liste, list) or not all(isinstance(k, str) and _kok_metni(k).strip() for k in liste):
            raise SystemExit(f"{yer}: '{alan}' harf içeren metinlerden oluşan bir liste olmalı.")
        kokler[alan] = liste
    if not kokler["dogru"]:
        raise SystemExit(f"{yer}: 'dogru' listesi boş; doğru anlamın en az bir kökü gerekli.")
    if "anlam" in satir and satir["anlam"] not in ANLAMLAR:
        raise SystemExit(f"{yer}: 'anlam' şunlardan biri olmalı: {', '.join(ANLAMLAR)}.")
    if "zor" in satir and not isinstance(satir["zor"], bool):
        raise SystemExit(f"{yer}: 'zor' true ya da false olmalı.")
    for d in kokler["dogru"]:
        tam_kelime = f" {_kok_metni(d).strip()} "  # doğru kök tek başına bir kelime olarak geçtiğinde
        for y in kokler["yanlis"]:
            if _kok_metni(y) in tam_kelime:
                raise SystemExit(f"{yer}: yanlış kök '{y}' doğru kök '{d}' içinde geçiyor; '{d}' bulunan her "
                                 "çeviri yanlış sayılır.")


# --- çok anlamlı kelime puanlaması -----------------------------------------------
_SAPKASIZ = str.maketrans("âîû", "aiu")
_AYIRAC = re.compile(r"[\W_]+")  # harf ve rakam dışındaki her şey: noktalama, kesme işareti, boşluk


def tr_kucuk(metin: str) -> str:
    """Türkçe küçük harf: İ → i, I → ı (str.lower 'I'yı 'i', 'İ'yi 'i' + birleşik nokta yapar). Şapkalı harf
    düzleşir (dükkân = dükkan); ayrık yazılmış birleşik nokta (i + U+0307) atılır."""
    metin = unicodedata.normalize("NFC", metin).replace("İ", "i").replace("I", "ı")
    return metin.lower().replace("̇", "").translate(_SAPKASIZ)


def _arama_metni(metin: str) -> str:
    """Köklerin arandığı biçim: Türkçe küçük harf, harf ve rakam dışındaki her şey tek boşluk, başta ve sonda birer
    boşluk. Kökün başındaki ya da sonundaki boşluk böylece kelime sınırı olur."""
    return f" {_AYIRAC.sub(' ', tr_kucuk(metin)).strip()} "


def _kok_metni(kok: str) -> str:
    """Kökün aranan biçimi: metinle aynı dönüşüm; baştaki ve sondaki boşluk (kelime sınırı) korunur."""
    return _AYIRAC.sub(" ", tr_kucuk(kok))


def eslesen_kokler(ceviri: str, kokler: Iterable[str]) -> list[str]:
    """Çeviride geçen kökler, veri setinde yazıldıkları gibi."""
    aranan = _arama_metni(ceviri)
    return [k for k in kokler if _kok_metni(k).strip() and _kok_metni(k) in aranan]


def puanla(ceviri: str | None, dogru: Iterable[str], yanlis: Iterable[str]) -> tuple[str, list[str]]:
    """Çok anlamlı kelimenin çevirideki anlamı: (puan, puanı belirleyen kökler). Yanlış anlamın kökü geçiyorsa
    'yanlis' (doğru kök de geçse); geçmiyorsa ve doğru anlamın kökü geçiyorsa 'dogru'; ikisi de yoksa 'belirsiz'
    (elle bakılmalı). Çeviri yoksa (motor hatası) 'hata'."""
    if ceviri is None or not ceviri.strip():
        return "hata", []
    if bulunan := eslesen_kokler(ceviri, yanlis):
        return "yanlis", bulunan
    if bulunan := eslesen_kokler(ceviri, dogru):
        return "dogru", bulunan
    return "belirsiz", []


def satiri_puanla(r: Kayit) -> tuple[str, list[str]] | None:
    """Sonuç satırının puanı; dogru/yanlis kökü olmayan satırda None. Satırdaki çeviri ve köklerden her seferinde
    yeniden hesaplanır: karşılaştırma iki sonucu, hangi sürümle kaydedildiklerine bakmadan aynı kuralla puanlar."""
    if not (r.get("dogru") or r.get("yanlis")):
        return None
    return puanla(None if r.get("hata") else r.get("tr"), r.get("dogru") or (), r.get("yanlis") or ())


def _puanli_satirlar(sahneler: Iterable[Kayit]) -> Iterable[tuple[Kayit, Kayit, str, list[str]]]:
    """Puanlanan satırlar: (sahne, satır, puan, belirleyen kökler)."""
    for sahne in sahneler:
        for r in sahne.get("satirlar", []):
            puan = satiri_puanla(r)
            if puan is not None:
                yield sahne, r, puan[0], puan[1]


def _puan_sayaci() -> Kayit:
    return {"satir": 0, "dogru": 0, "yanlis": 0, "belirsiz": 0, "hata": 0, "dogruluk": None}


def dogruluk(sayac: Kayit) -> float | None:
    """Doğru / (doğru + yanlış + belirsiz), yüzde, bir ondalık. Motor hatası veren satır yüzdeye girmez: modelin
    anlam seçimi değildir. Puanlanan satır yoksa None."""
    puanlanan = sayac["dogru"] + sayac["yanlis"] + sayac["belirsiz"]
    return round(100 * sayac["dogru"] / puanlanan, 1) if puanlanan else None


def puan_ozeti(sahneler: Iterable[Kayit]) -> Kayit | None:
    """Çok anlamlı kelime puanlarının özeti; puanlanan satır yoksa None. "toplam"; "gruplar": anlama göre (gundelik,
    suc) ve zor örnekler; "kelimeler": kelime başına, veri setindeki ilk geçiş sırasıyla."""
    toplam = _puan_sayaci()
    gruplar: dict[str, Kayit] = {}
    kelimeler: dict[str, Kayit] = {}
    for _sahne, r, puan, _kokler in _puanli_satirlar(sahneler):
        sayaclar = [toplam, kelimeler.setdefault(str(r.get("kelime") or "?"), _puan_sayaci())]
        if r.get("anlam") in ANLAMLAR:
            sayaclar.append(gruplar.setdefault(r["anlam"], _puan_sayaci()))
        if r.get("zor"):
            sayaclar.append(gruplar.setdefault("zor", _puan_sayaci()))
        for s in sayaclar:
            s["satir"] += 1
            s[puan] += 1
    if not toplam["satir"]:
        return None
    gruplar = {g: gruplar[g] for g in GRUP_ADI if g in gruplar}
    for s in (toplam, *gruplar.values(), *kelimeler.values()):
        s["dogruluk"] = dogruluk(s)
    return {"toplam": toplam, "gruplar": gruplar, "kelimeler": kelimeler}


def sahneleri_sec(sahneler: list[Kayit], secim: Sequence[str] | None, limit: int | None) -> list[Kayit]:
    """--scene (virgülle ya da tekrar ederek, verilen sırayla) ve --limit (sahne başına ilk N satır)."""
    if secim:
        istenen = list(dict.fromkeys(p.strip() for parca in secim for p in parca.split(",") if p.strip()))
        bilinen = {s["id"]: s for s in sahneler}
        eksik = [s for s in istenen if s not in bilinen]
        if eksik:
            raise SystemExit(f"Bilinmeyen sahne: {', '.join(eksik)}\nVeri setindeki sahneler: {', '.join(bilinen)}")
        sahneler = [bilinen[s] for s in istenen]
    if limit is not None:
        sahneler = [{**s, "satirlar": s["satirlar"][:limit]} for s in sahneler]
    return sahneler


# --- çeviri ayarları -----------------------------------------------------------
def _ata(cfg: Any, ad: str, deger: Any) -> None:
    """Ayar alanını atar. Alan bu sürümün TranslateCfg'sinde yoksa (ör. henüz eklenmemiş 'profile') uyarır ama
    yine de setattr ile atar: motor alanı getattr ile okuyorsa çalışır, okumuyorsa etkisizdir; araç çökmez."""
    if ad not in {f.name for f in fields(cfg)}:
        _uyar(f"TranslateCfg'de '{ad}' alanı yok; değer atandı ama çeviri motoru kullanmayabilir.")
    setattr(cfg, ad, deger)


def _anahtar(ortam: str, alan: str) -> str:
    """API anahtarı: önce ortam değişkeni, yoksa uygulamanın kayıtlı ayarları (dosya yalnızca okunur)."""
    deger = os.environ.get(ortam, "").strip()
    if deger:
        return deger
    try:
        from altyazitr.paths import SETTINGS_FILE

        ayar = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return str((ayar.get("translate") or {}).get(alan) or "").strip()
    except Exception:  # dosya yok ya da bozuk: anahtarsız devam, motor kendi hatasını verir
        return ""


def ayarlari_kur(args: argparse.Namespace) -> Any:
    """Uygulamanın varsayılan TranslateCfg'si üstüne komut satırı seçenekleri."""
    from altyazitr.config import TranslateCfg

    cfg = TranslateCfg()
    _ata(cfg, "engine", args.engine)
    if args.model:
        if args.engine in ("ollama", "gemini"):
            _ata(cfg, f"{args.engine}_model", args.model)
        else:
            _uyar(f"--model yalnız ollama ve gemini motorlarında kullanılır; '{args.engine}' için yok sayıldı.")
    if args.profile is not None:
        _ata(cfg, "profile", args.profile)
    if args.context is not None:
        _ata(cfg, "context_lines", args.context)
    _ata(cfg, "timeout_s", args.timeout)
    if args.ollama_url:
        _ata(cfg, "ollama_url", args.ollama_url)
    if args.device:
        _ata(cfg, "quickmt_device", args.device)
    if args.engine == "gemini" and not getattr(cfg, "gemini_key", ""):
        _ata(cfg, "gemini_key", _anahtar("GEMINI_API_KEY", "gemini_key"))
    if args.engine == "deepl" and not getattr(cfg, "deepl_key", ""):
        _ata(cfg, "deepl_key", _anahtar("DEEPL_AUTH_KEY", "deepl_key"))
    return cfg


def _model_adi(cfg: Any, tr: Any) -> str:
    if cfg.engine == "ollama":
        return str(cfg.ollama_model)
    if cfg.engine == "gemini":
        return str(cfg.gemini_model)
    if cfg.engine == "quickmt":  # hazırlıktan sonra gerçekten kullanılan cihaz (CUDA olmazsa CPU'ya düşer)
        return f"quickmt-en-tr ({getattr(tr, 'active_device', None) or cfg.quickmt_device})"
    if cfg.engine == "deepl":
        return "DeepL API"
    return "-"


def _ocr_duzeltici() -> Callable[[str], str]:
    """İşlem hattının OCR metin düzeltmesi; bulunamazsa satırlar olduğu gibi gider."""
    try:
        from altyazitr.textproc import fix_ocr
    except ImportError:
        _uyar("altyazitr.textproc.fix_ocr bulunamadı; satırlar OCR düzeltmesi olmadan gidiyor.")
        return lambda metin: metin
    return fix_ocr


def _cumleler(metin: str) -> list[str]:
    """Karşılaştırma için cümleler: küçük harf, yalnız harf, rakam ve kesme işareti."""
    parcalar = (" ".join(re.sub(r"[^\w']+", " ", p.casefold()).split()) for p in re.split(r"(?<=[.!?])\s+", metin))
    return [p for p in parcalar if p]


def _profil_cumleleri(tr: Any) -> set[str]:
    """Motorun istemine giren oyun profili üslup örneklerinin cümleleri. Profil kullanmayan motorda boş."""
    profil = getattr(tr, "profile", None)
    if profil is None or getattr(tr, "is_tg", False):  # TranslateGemma kendi şablonunu kullanır, profil gitmez
        return set()
    try:
        return {c for en, _tr in getattr(profil, "examples", ()) for c in _cumleler(en)}
    except (TypeError, ValueError):  # beklenmeyen biçim: denetim atlanır, test sürer
        return set()


def _istem_ozeti(tr: Any) -> str:
    """Motorun sistem isteminin (oyun profili, kurallar, sözlük, örnekler) SHA-256 özetinin ilk 12 hanesi. Profil ya
    da istem değişince değişir: iki sonucun aynı istemle alınıp alınmadığı buradan görülür. İstem kullanmayan
    motorda (quickmt, DeepL, TranslateGemma) boş."""
    istem = getattr(tr, "system", "")
    if not isinstance(istem, str) or not istem or getattr(tr, "is_tg", False):
        return ""
    return hashlib.sha256(istem.encode("utf-8")).hexdigest()[:12]


def _kod_surumu() -> str:
    """Kısa commit kimliği; altyazitr/ altında kaydedilmemiş değişiklik varsa sonuna '+' eklenir.
    Yalnızca okur: --no-optional-locks ile 'git status' dizin dosyasını da güncellemez."""

    def git(*arg: str) -> str:
        r = subprocess.run(["git", "--no-optional-locks", *arg], cwd=ROOT, capture_output=True, check=True,
                           timeout=10, encoding="utf-8", errors="replace")
        return r.stdout.strip()

    try:
        kimlik = git("rev-parse", "--short", "HEAD")
        degisik = git("status", "--porcelain", "--", "altyazitr")
    except (OSError, subprocess.SubprocessError):
        return ""
    return kimlik + ("+" if degisik else "")


# --- ölçüm ---------------------------------------------------------------------
def _hata_metni(e: BaseException) -> str:
    """Tek satırlık kısa hata metni. Motorun kendi Türkçe mesajı (TranslatorError) olduğu gibi kalır."""
    ad = type(e).__name__
    mesaj = " ".join(str(e).split())
    if "timeout" in ad.lower():
        return f"zaman aşımı ({ad})"
    if ad == "TranslatorError" and mesaj:
        return mesaj[:200]
    return f"{ad}: {mesaj}"[:200] if mesaj else ad


def satiri_cevir(tr: Any, metin: str, baglam: Baglam,
                 son_duzelt: Callable[[str, str], str]) -> tuple[str | None, float, str | None]:
    """Tek satırı çevirir: (çeviri ya da None, ms, hata ya da None). Ctrl+C dışında hata fırlatmaz."""
    t0 = time.perf_counter()
    try:
        cikti = tr.translate(metin, baglam)
    except Exception as e:
        return None, (time.perf_counter() - t0) * 1000, _hata_metni(e)
    ms = (time.perf_counter() - t0) * 1000
    if not cikti or not cikti.strip():
        return None, ms, "boş çeviri döndü"  # işlem hattı da boş çeviriyi hata sayar
    return son_duzelt(metin, cikti), ms, None


def sahneleri_cevir(tr: Any, sahneler: Sequence[Kayit], baglam_n: int, duzelt: Callable[[str], str],
                    son_duzelt: Callable[[str, str], str], ara_s: float = 0.0,
                    ornek_cumleler: set[str] | frozenset[str] = frozenset()) -> tuple[list[Kayit], bool]:
    """Sahneleri işlem hattındaki bağlam akışıyla çevirir; (sahne sonuçları, Ctrl+C ile kesildi mi).
    ornek_cumleler: profil örneklerinin cümleleri; bunlardan birini içeren satır işaretlenir."""
    toplam = sum(len(s["satirlar"]) for s in sahneler)
    genislik = len(str(toplam))
    sonuc: list[Kayit] = []
    sira = 0
    try:
        for sahne in sahneler:
            # işlem hattındaki TrContext.hist gibi: son N satır, Türkçesi motorun kendi önceki çıktısı
            gecmis: deque[tuple[str, str]] = deque(maxlen=max(1, baglam_n))
            satirlar: list[Kayit] = []
            sonuc.append({"id": sahne["id"], "ad": sahne.get("ad") or sahne["id"],
                          "aciklama": sahne.get("aciklama", ""), "satirlar": satirlar})
            for no, kaynak in enumerate(sahne["satirlar"], 1):
                if sira and ara_s > 0:
                    time.sleep(ara_s)
                en = kaynak["en"].strip()
                giden = duzelt(en) or en
                ceviri, ms, hata = satiri_cevir(tr, giden, list(gecmis) if baglam_n > 0 else [], son_duzelt)
                if baglam_n > 0:
                    # işlem hattı gibi: asıl motorun çeviremediği satır bağlama yalnız İngilizcesiyle girer
                    gecmis.append((giden, ceviri or ""))
                kayit: Kayit = {"no": no, "en": en, "giden": giden, "tr": ceviri, "ms": round(ms, 1),
                                "hata": hata, "beklenen": kaynak.get("beklenen", ""),
                                "profil_ornegi": bool(ornek_cumleler.intersection(_cumleler(giden)))}
                kayit.update({alan: kaynak[alan] for alan in PUAN_ALANLARI if alan in kaynak})
                puan = satiri_puanla(kayit)
                if puan is not None:  # puanlanmayan satırın kaydı önceki biçimle aynı kalır
                    kayit["puan"], kayit["bulunan_kokler"] = puan
                satirlar.append(kayit)
                sira += 1
                gorunen = f"HATA: {hata}" if hata else ceviri or ""
                ek = f"  [{PUAN_ADI[puan[0]]}]" if puan is not None else ""
                _bilgi(f"[{sira:>{genislik}}/{toplam}] {sahne['id']} #{no:<2} {_ms(ms):>6} ms  "
                       f"{_kisalt(gorunen, 90)}{ek}")
    except KeyboardInterrupt:
        _uyar("Test Ctrl+C ile kesildi; tamamlanan satırlar raporlanıyor.")
        return [s for s in sonuc if s["satirlar"]], True
    return sonuc, False


def yuzdelik(degerler: Sequence[float], p: float) -> float | None:
    """Doğrusal aradeğerlemeli yüzdelik (numpy'nin varsayılanı gibi); boş listede None."""
    if not degerler:
        return None
    s = sorted(degerler)
    k = (len(s) - 1) * p / 100
    alt, ust = math.floor(k), math.ceil(k)
    return s[alt] + (s[ust] - s[alt]) * (k - alt)


def _yuvarla(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def ozet_hesapla(sahneler: Iterable[Kayit], esikler: Kayit | None = None) -> Kayit:
    """Satır ve hata sayısı, gecikme özeti (yalnız başarılı satırlar) ve uygulama eşikleriyle karşılaştırma."""
    satirlar = [r for sahne in sahneler for r in sahne.get("satirlar", [])]
    ms = [float(r["ms"]) for r in satirlar if not r.get("hata") and r.get("ms") is not None]
    ozet: Kayit = {
        "satir": len(satirlar), "basarili": len(ms), "hata": len(satirlar) - len(ms),
        "p50_ms": _yuvarla(yuzdelik(ms, 50)), "p90_ms": _yuvarla(yuzdelik(ms, 90)),
        "en_yuksek_ms": _yuvarla(max(ms, default=None)),
        "ortalama_ms": _yuvarla(statistics.fmean(ms) if ms else None),
        "profil_ornegi": sum(1 for r in satirlar if r.get("profil_ornegi")),
    }
    esikler = esikler or {}
    if esikler.get("karma_bekleme_ms"):
        ozet["karma_bekleme_icinde"] = sum(v <= esikler["karma_bekleme_ms"] for v in ms)
    if esikler.get("uygulama_zaman_asimi_s"):
        ozet["uygulama_zaman_asimini_asan"] = sum(v > esikler["uygulama_zaman_asimi_s"] * 1000 for v in ms)
    return ozet


def _uygulama_esikleri(motor: str) -> Kayit:
    """Uygulamanın varsayılan karma bekleme süresi ve zaman aşımı: ölçülen gecikmeyi bunlarla kıyaslamak için.
    Yalnız uzak motorlarda anlamlı; alan bu sürümde yoksa o eşik atlanır."""
    if motor not in UZAK_MOTORLAR:
        return {}
    from altyazitr.config import TranslateCfg

    varsayilan = TranslateCfg()
    esikler: Kayit = {}
    if getattr(varsayilan, "hybrid", False) and getattr(varsayilan, "hybrid_wait_ms", 0):
        esikler["karma_bekleme_ms"] = int(varsayilan.hybrid_wait_ms)
    if getattr(varsayilan, "timeout_s", 0):
        esikler["uygulama_zaman_asimi_s"] = float(varsayilan.timeout_s)
    return esikler


def _goreli(yol: Path) -> str:
    try:
        return yol.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(yol)


def testi_calistir(args: argparse.Namespace) -> int:
    veri_yolu = Path(args.dataset)
    sahneler = sahneleri_sec(veri_setini_oku(veri_yolu), args.scene, args.limit)
    toplam = sum(len(s["satirlar"]) for s in sahneler)

    from altyazitr import __version__
    from altyazitr import translate as ceviri

    baslangic = datetime.now()
    cfg = ayarlari_kur(args)
    baglam_n = max(0, int(getattr(cfg, "context_lines", 0) or 0))
    try:
        # işlem hattı gibi: kullanıcı sözlüğü + profilin çevrilmeyecek adları (yer tutucuyla ad koruyan motorlar için)
        sozluk_kur = getattr(ceviri, "build_glossary", None)
        sozluk = sozluk_kur(cfg) if sozluk_kur is not None else ceviri.Glossary(cfg.glossary)
        tr = ceviri.create_translator(cfg, sozluk)
    except Exception as e:
        _bilgi(f"Çeviri motoru oluşturulamadı: {e}")
        return 2
    try:
        t0 = time.perf_counter()
        try:
            tr.prepare(_bilgi)
        except Exception as e:
            _bilgi(f"Çeviri motoru hazırlanamadı: {e}")
            if cfg.engine == "ollama":
                _bilgi(f"İpucu: Ollama açık mı ({cfg.ollama_url})? Model yüklü değilse: ollama pull {cfg.ollama_model}")
            return 2
        hazirlik_s = time.perf_counter() - t0
        model = _model_adi(cfg, tr)
        istem_ozeti = _istem_ozeti(tr)

        t0 = time.perf_counter()
        isinma_hatasi = None
        try:
            tr.translate(ISINMA_METNI, [])
        except Exception as e:
            isinma_hatasi = _hata_metni(e)
            _uyar(f"Isınma çağrısı başarısız: {isinma_hatasi}")
        isinma_ms = (time.perf_counter() - t0) * 1000
        _bilgi(f"{cfg.engine} / {model}: hazırlık {_sn(hazirlik_s)} sn, ısınma {_ms(isinma_ms)} ms. "
               f"{len(sahneler)} sahne, {toplam} satır çevriliyor (bağlam {baglam_n} satır).")

        t0 = time.perf_counter()
        sonuc_sahneleri, kesildi = sahneleri_cevir(tr, sahneler, baglam_n, _ocr_duzeltici(),
                                                   ceviri.restore_end_punctuation, args.pause, _profil_cumleleri(tr))
        toplam_s = time.perf_counter() - t0
    finally:
        try:
            tr.close()
            serbest_birak = getattr(ceviri, 'release_ollama_models', None)
            if serbest_birak is not None:  # yeni sürümde close() modeli bellekte bırakır: ekran kartı boşalsın
                serbest_birak()
        except Exception as e:
            _uyar(f"Çeviri motoru kapatılamadı: {e}")

    esikler = _uygulama_esikleri(cfg.engine)
    sonuc: Kayit = {
        "bicim": BICIM,
        "tarih": baslangic.isoformat(timespec="seconds"),
        "surum": __version__,
        "kod": _kod_surumu(),
        "motor": cfg.engine,
        "model": model,
        "profil": str(getattr(cfg, "profile", "") or ""),
        "istem_ozeti": istem_ozeti,
        "baglam_satiri": baglam_n,
        "zaman_asimi_s": float(cfg.timeout_s),
        "veri_seti": _goreli(veri_yolu),
        "hazirlik_s": round(hazirlik_s, 2),
        "isinma_ms": round(isinma_ms, 1),
        "isinma_hatasi": isinma_hatasi,
        "toplam_s": round(toplam_s, 2),
        "kesildi": kesildi,
        "esikler": esikler,
        "ozet": ozet_hesapla(sonuc_sahneleri, esikler),
        "puanlama": puan_ozeti(sonuc_sahneleri),
        "sahneler": sonuc_sahneleri,
    }
    if sonuc["puanlama"] is not None:
        t = sonuc["puanlama"]["toplam"]
        _bilgi(f"Çok anlamlı kelimeler: doğruluk {_oran(t)}, yanlış {t['yanlis']}, belirsiz {t['belirsiz']}, "
               f"hata {t['hata']}.")
    _cikti_yaz(rapor_md(sonuc), args.out, "Rapor")
    if args.json:
        _cikti_yaz(json.dumps(sonuc, ensure_ascii=False, indent=2) + "\n", args.json, "JSON sonucu")
    return 130 if kesildi else 0


# --- rapor ---------------------------------------------------------------------
def _ms(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.1f}".replace(".", ",") if v < 10 else f"{v:.0f}"


def _sn(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _sayi(v: Any) -> str:
    return f"{v:g}".replace(".", ",") if isinstance(v, (int, float)) else str(v)


def _tarih(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(iso or "-")


def _kisalt(metin: str, n: int) -> str:
    return metin if len(metin) <= n else metin[: n - 1] + "…"


def _hucre(metin: Any) -> str:
    """Markdown tablo hücresi: satır sonları boşluğa döner, '|' kaçışlanır."""
    return " ".join(str("" if metin is None else metin).split()).replace("|", "\\|")


def _ingilizce(r: Kayit) -> str:
    giden = r.get("giden") or r["en"]
    return r["en"] if giden == r["en"] else f"{r['en']} (giden: {giden})"


def _turkce(r: Kayit | None) -> str:
    if r is None:
        return "(yok)"
    if r.get("hata"):
        return f"HATA: {r['hata']}"
    return r.get("tr") or ""


def _yuzde(v: float | None) -> str:
    """Türkçe yüzde: %77,8, %100; değer yoksa '-'."""
    return "-" if v is None else "%" + f"{v:.1f}".replace(".", ",").removesuffix(",0")


def _oran(s: Kayit | None) -> str:
    """Doğruluk ve doğru / puanlanan satır: '%66,7 (2/3)'."""
    if not s:
        return "-"
    return f"{_yuzde(s['dogruluk'])} ({s['dogru']}/{s['dogru'] + s['yanlis'] + s['belirsiz']})"


def _fark(a: Kayit | None, b: Kayit | None) -> str:
    """B'nin doğruluğu eksi A'nınki, yüzde puanı: '+12,5', '-3', '0'. Yuvarlanmış yüzdelerden değil sayılardan
    hesaplanır (%66,7 - %33,3 = 33,3; 33,4 değil)."""
    pa = a["dogru"] + a["yanlis"] + a["belirsiz"] if a else 0
    pb = b["dogru"] + b["yanlis"] + b["belirsiz"] if b else 0
    if not a or not b or not pa or not pb:
        return "-"
    d = round(100 * (b["dogru"] / pb - a["dogru"] / pa), 1)
    return "0" if d == 0 else f"{d:+.1f}".replace(".", ",").removesuffix(",0")


def _kokler(kokler: Iterable[str]) -> str:
    return ", ".join(k.strip() for k in kokler)


def _puan_notu(r: Kayit) -> str:
    """Sahne tablosunun Not sütunu için puan: '[doğru]', '[yanlış: şarjör]', '[belirsiz]'; puanlanmayan satırda boş."""
    puan = satiri_puanla(r)
    if puan is None:
        return ""
    return f"[{PUAN_ADI[puan[0]]}" + (f": {_kokler(puan[1])}" if puan[0] == "yanlis" else "") + "]"


def _sayac_satiri(ad: str, s: Kayit) -> str:
    return (f"| {ad} | {s['satir']} | {s['dogru']} | {s['yanlis']} | {s['belirsiz']} | {s['hata']} | "
            f"{_yuzde(s['dogruluk'])} |")


def _puan_bolumu(s: Kayit) -> list[str]:
    """Raporun çok anlamlı kelime bölümü: toplam ve gruplar, kelime başına doğruluk (en düşük önce), yanlış,
    belirsiz ve hata veren satırların listesi. Puanlanan satır yoksa boş."""
    po = puan_ozeti(s["sahneler"])
    if po is None:
        return []
    baslik = "| | Satır | Doğru | Yanlış | Belirsiz | Hata | Doğruluk |"
    hiza = "|---|---:|---:|---:|---:|---:|---:|"
    out = ["", "## Çok anlamlı kelimeler", "",
           "Çeviride yanlış anlamın kökü geçiyorsa **yanlış**; geçmiyorsa ve doğru anlamın kökü geçiyorsa **doğru**; "
           "ikisi de yoksa **belirsiz** (elle bakılmalı). Doğruluk = doğru / (doğru + yanlış + belirsiz); motor "
           "hatası veren satır yüzdeye girmez.", "", baslik, hiza, _sayac_satiri("**Toplam**", po["toplam"])]
    out += [_sayac_satiri(GRUP_ADI[g], v) for g, v in po["gruplar"].items()]
    sirali = sorted(po["kelimeler"].items(),
                    key=lambda kv: (101.0 if kv[1]["dogruluk"] is None else kv[1]["dogruluk"], -kv[1]["yanlis"]))
    out += ["", "### Kelime başına", "", "En düşük doğruluk önce.", "", baslik.replace("| |", "| Kelime |", 1), hiza]
    out += [_sayac_satiri(_hucre(k), v) for k, v in sirali]
    for puan, ad in (("yanlis", "Yanlış satırlar"), ("belirsiz", "Belirsiz satırlar"), ("hata", "Hata veren satırlar")):
        liste = [(sahne, r, kokler) for sahne, r, p, kokler in _puanli_satirlar(s["sahneler"]) if p == puan]
        if not liste:
            continue
        kok_sutunu = puan == "yanlis"
        out += ["", f"### {ad} ({len(liste)})", "",
                "| Kelime | Sahne | İngilizce | Türkçe | Beklenen |" + (" Yanlış kök |" if kok_sutunu else ""),
                "|---|---|---|---|---|" + ("---|" if kok_sutunu else "")]
        for sahne, r, kokler in liste:
            out.append(f"| {_hucre(r.get('kelime'))} | `{sahne.get('id', '?')}` | {_hucre(_ingilizce(r))} | "
                       f"{_hucre(_turkce(r))} | {_hucre(r.get('beklenen'))} |"
                       + (f" {_hucre(_kokler(kokler))} |" if kok_sutunu else ""))
    return out


def rapor_md(s: Kayit) -> str:
    o = s["ozet"]
    e = s.get("esikler") or {}
    out = [f"# Çeviri testi: {s['motor']} / {s['model']}", "",
           f"- **Motor:** {s['motor']}",
           f"- **Model:** {s['model']}",
           f"- **Profil:** {s.get('profil') or '-'}"]
    if s.get("istem_ozeti"):
        out.append(f"- **İstem özeti:** `{s['istem_ozeti']}` (sistem istemi değişince değişir)")
    out += [f"- **Bağlam satırı:** {s['baglam_satiri']}",
            f"- **Tarih:** {_tarih(s['tarih'])}"]
    if s["motor"] in UZAK_MOTORLAR:
        out.append(f"- **Zaman aşımı:** {_sayi(s['zaman_asimi_s'])} sn")
    out.append(f"- **Kod:** Robz Vision {s['surum']}" + (f" ({s['kod']})" if s.get("kod") else ""))
    out.append(f"- **Veri seti:** `{s['veri_seti']}` ({len(s['sahneler'])} sahne, {o['satir']} satır)")
    if s.get("kesildi"):
        out += ["", "> Test Ctrl+C ile yarıda kesildi; yalnız tamamlanan satırlar var."]
    out += ["", "## Özet", "",
            "| Satır | Hata | p50 ms | p90 ms | En yüksek ms | Ortalama ms |",
            "|---:|---:|---:|---:|---:|---:|",
            f"| {o['satir']} | {o['hata']} | {_ms(o['p50_ms'])} | {_ms(o['p90_ms'])} | {_ms(o['en_yuksek_ms'])} | "
            f"{_ms(o['ortalama_ms'])} |", ""]
    isinma = f"ısınma çağrısı ({_ms(s['isinma_ms'])} ms"
    isinma += f", hata: {s['isinma_hatasi']})" if s.get("isinma_hatasi") else ")"
    out.append(f"- Gecikme yalnız başarılı satırlardan hesaplanır; {isinma} sayılmaz.")
    if "karma_bekleme_icinde" in o:
        out.append(f"- Karma modun bekleme süresi ({e['karma_bekleme_ms']} ms) içinde biten: "
                   f"{o['karma_bekleme_icinde']}/{o['basarili']} satır. Daha uzun sürenlerde uygulama önce hızlı "
                   "çeviriyi gösterir.")
    if "uygulama_zaman_asimini_asan" in o:
        out.append(f"- Uygulamanın zaman aşımını ({_sayi(e['uygulama_zaman_asimi_s'])} sn) aşan: "
                   f"{o['uygulama_zaman_asimini_asan']} satır.")
    if o.get("profil_ornegi"):
        out.append(f"- Oyun profilinin üslup örneklerinde de geçen satır: {o['profil_ornegi']}. Model bunların "
                   f"cevabını örnekten alabilir, sonuç olduğundan iyi görünür; tabloda {ORNEK_ISARETI} ile işaretli.")
    out.append(f"- Hazırlık {_sn(s['hazirlik_s'])} sn, çeviriler toplam {_sn(s['toplam_s'])} sn.")
    out += _puan_bolumu(s)
    for i, sahne in enumerate(s["sahneler"], 1):
        so = ozet_hesapla([sahne])
        out += ["", f"## {i}. {sahne['ad']} (`{sahne['id']}`)", ""]
        if sahne.get("aciklama"):
            out += [sahne["aciklama"], ""]
        out += [f"p50 {_ms(so['p50_ms'])} ms, en yüksek {_ms(so['en_yuksek_ms'])} ms, hata {so['hata']}.", "",
                "| # | İngilizce | Türkçe | ms | Not |",
                "|---:|---|---|---:|---|"]
        for r in sahne["satirlar"]:
            isaretler = [r.get("beklenen") or "", _puan_notu(r), ORNEK_ISARETI if r.get("profil_ornegi") else ""]
            not_ = " ".join(p for p in isaretler if p)
            out.append(f"| {r['no']} | {_hucre(_ingilizce(r))} | {_hucre(_turkce(r))} | {_ms(r.get('ms'))} | "
                       f"{_hucre(not_)} |")
    return "\n".join(out) + "\n"


# --- karşılaştırma -------------------------------------------------------------
def _sonuc_oku(yol: Path) -> Kayit:
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Sonuç dosyası okunamadı ({yol}): {e}") from None
    if not isinstance(veri, dict) or not isinstance(veri.get("sahneler"), list):
        raise SystemExit(f"{yol} bu aracın --json çıktısı değil ('sahneler' yok).")
    return veri


def _satir_haritasi(sonuc: Kayit) -> dict[str, tuple[Kayit, dict[tuple[str, int], Kayit]]]:
    """Sahne kimliği → (sahne, {(İngilizce, kaçıncı tekrar): satır}). İki sonuç satır numarasıyla değil metinle
    eşleşir: veri seti iki çalıştırma arasında değiştiyse de satırlar kaymaz."""
    harita: dict[str, tuple[Kayit, dict[tuple[str, int], Kayit]]] = {}
    for sahne in sonuc["sahneler"]:
        tekrar: Counter[str] = Counter()
        satirlar: dict[tuple[str, int], Kayit] = {}
        for r in sahne.get("satirlar", []):
            en = r.get("en", "")
            satirlar[(en, tekrar[en])] = r
            tekrar[en] += 1
        harita[sahne.get("id", "?")] = (sahne, satirlar)
    return harita


def karsilastirma_raporu(a_yol: Path, b_yol: Path) -> str:
    """İki --json sonucunu yan yana koyar: üst bilgi, gecikme özetleri, sahne sahne iki çeviri."""
    a, b = _sonuc_oku(a_yol), _sonuc_oku(b_yol)
    oa, ob = ozet_hesapla(a["sahneler"], a.get("esikler")), ozet_hesapla(b["sahneler"], b.get("esikler"))
    ha, hb = _satir_haritasi(a), _satir_haritasi(b)

    govde: list[str] = []
    ortak = ayni = 0
    for n, sid in enumerate(list(ha) + [x for x in hb if x not in ha], 1):
        sahne_a, ra = ha.get(sid, ({}, {}))
        sahne_b, rb = hb.get(sid, ({}, {}))
        sahne = sahne_a or sahne_b
        govde += ["", f"## {n}. {sahne.get('ad') or sid} (`{sid}`)", "",
                  f"| # | İngilizce | A: {_hucre(a.get('model'))} | B: {_hucre(b.get('model'))} | A ms | B ms | Not |",
                  "|---:|---|---|---|---:|---:|---|"]
        for i, k in enumerate(list(ra) + [k for k in rb if k not in ra], 1):
            x, y = ra.get(k), rb.get(k)
            if x is not None and y is not None:
                ortak += 1
                ayni += (not x.get("hata") and not y.get("hata")
                         and (x.get("tr") or "").strip() == (y.get("tr") or "").strip())
            r = x or y
            kimde = [ad for ad, s in (("A", x), ("B", y)) if s and s.get("profil_ornegi")]
            not_ = (r.get("beklenen") or "") + (f" [profil örneği: {', '.join(kimde)}]" if kimde else "")
            puanlar = [f"{ad}: {PUAN_ADI[p[0]]}" for ad, s in (("A", x), ("B", y))
                       if s is not None and (p := satiri_puanla(s)) is not None]
            if puanlar:
                not_ += f" [{', '.join(puanlar)}]"
            govde.append(f"| {i} | {_hucre(_ingilizce(r))} | {_hucre(_turkce(x))} | {_hucre(_turkce(y))} | "
                         f"{_ms(x.get('ms') if x else None)} | {_ms(y.get('ms') if y else None)} | {_hucre(not_)} |")
    yalniz_a = sum(len(r) for _, r in ha.values()) - ortak
    yalniz_b = sum(len(r) for _, r in hb.values()) - ortak

    def satir(etiket: str, va: Any, vb: Any) -> str:
        return f"| {etiket} | {_hucre(va)} | {_hucre(vb)} |"

    def zaman_asimi(s: Kayit) -> str:
        return f"{_sayi(s['zaman_asimi_s'])} sn" if s.get("motor") in UZAK_MOTORLAR else "-"

    bas = [f"# Çeviri karşılaştırması: {a.get('model', '?')} / {b.get('model', '?')}", "",
           "| | A | B |", "|---|---|---|",
           satir("Dosya", a_yol.name, b_yol.name),
           satir("Motor", a.get("motor", "-"), b.get("motor", "-")),
           satir("Model", a.get("model", "-"), b.get("model", "-")),
           satir("Profil", a.get("profil") or "-", b.get("profil") or "-")]
    if a.get("istem_ozeti") or b.get("istem_ozeti"):
        bas.append(satir("İstem özeti", a.get("istem_ozeti") or "-", b.get("istem_ozeti") or "-"))
    bas += [satir("Bağlam satırı", a.get("baglam_satiri", "-"), b.get("baglam_satiri", "-")),
            satir("Zaman aşımı", zaman_asimi(a), zaman_asimi(b)),
            satir("Tarih", _tarih(a.get("tarih")), _tarih(b.get("tarih"))),
            satir("Kod", f"{a.get('surum', '?')} {a.get('kod') or ''}".strip(),
                  f"{b.get('surum', '?')} {b.get('kod') or ''}".strip())]
    if a.get("kesildi") or b.get("kesildi"):
        bas.append(satir("Durum", "yarıda kesildi" if a.get("kesildi") else "tam",
                         "yarıda kesildi" if b.get("kesildi") else "tam"))
    bas += ["", "## Gecikme", "",
            "| | A | B |", "|---|---:|---:|",
            satir("Satır", oa["satir"], ob["satir"]),
            satir("Hata", oa["hata"], ob["hata"]),
            satir("p50 ms", _ms(oa["p50_ms"]), _ms(ob["p50_ms"])),
            satir("p90 ms", _ms(oa["p90_ms"]), _ms(ob["p90_ms"])),
            satir("En yüksek ms", _ms(oa["en_yuksek_ms"]), _ms(ob["en_yuksek_ms"])),
            satir("Ortalama ms", _ms(oa["ortalama_ms"]), _ms(ob["ortalama_ms"])),
            satir("Isınma ms", _ms(a.get("isinma_ms")), _ms(b.get("isinma_ms")))]
    if "karma_bekleme_icinde" in oa or "karma_bekleme_icinde" in ob:
        bas.append(satir("Karma bekleme içinde",
                         f"{oa['karma_bekleme_icinde']}/{oa['basarili']}" if "karma_bekleme_icinde" in oa else "-",
                         f"{ob['karma_bekleme_icinde']}/{ob['basarili']}" if "karma_bekleme_icinde" in ob else "-"))
    if oa.get("profil_ornegi") or ob.get("profil_ornegi"):
        bas.append(satir("Profil örneğiyle örtüşen", oa.get("profil_ornegi", 0), ob.get("profil_ornegi", 0)))
    ozet = f"Ortak satır: {ortak}, ikisinde aynı çeviri: {ayni}."
    if yalniz_a or yalniz_b:
        ozet += f" Yalnız A'da {yalniz_a}, yalnız B'de {yalniz_b} satır var (farklı veri seti ya da --scene/--limit)."
    bas += ["", ozet]
    return "\n".join(bas + _puan_karsilastirmasi(a, b, ha, hb) + govde) + "\n"


def _puan_karsilastirmasi(a: Kayit, b: Kayit, ha: dict[str, tuple[Kayit, dict[tuple[str, int], Kayit]]],
                          hb: dict[str, tuple[Kayit, dict[tuple[str, int], Kayit]]]) -> list[str]:
    """Karşılaştırmanın çok anlamlı kelime bölümü: toplam ve gruplar, kelime kelime doğruluk ve fark, puanı değişen
    satırlar. İkisinde de puanlanan satır yoksa boş. Puanlar her iki sonuçta kayıtlı köklerden yeniden hesaplanır."""
    pa, pb = puan_ozeti(a["sahneler"]), puan_ozeti(b["sahneler"])
    if pa is None and pb is None:
        return []
    bos: Kayit = {"toplam": None, "gruplar": {}, "kelimeler": {}}
    pa, pb = pa or bos, pb or bos

    def oran_satiri(ad: str, va: Kayit | None, vb: Kayit | None) -> str:
        return f"| {ad} | {_oran(va)} | {_oran(vb)} | {_fark(va, vb)} |"

    out = ["", "## Çok anlamlı kelimeler", "",
           "Doğruluk = doğru / (doğru + yanlış + belirsiz); fark = B - A, yüzde puanı.", "",
           "| | A | B | Fark |", "|---|---:|---:|---:|", oran_satiri("**Toplam**", pa["toplam"], pb["toplam"])]
    out += [oran_satiri(ad, pa["gruplar"].get(g), pb["gruplar"].get(g))
            for g, ad in GRUP_ADI.items() if g in pa["gruplar"] or g in pb["gruplar"]]
    kelimeler = list(pa["kelimeler"]) + [k for k in pb["kelimeler"] if k not in pa["kelimeler"]]
    out += ["", "### Kelime başına", "", "| Kelime | A | B | Fark |", "|---|---:|---:|---:|"]
    out += [oran_satiri(_hucre(k), pa["kelimeler"].get(k), pb["kelimeler"].get(k)) for k in kelimeler]

    degisen: list[tuple[str, Kayit, Kayit, str, str, str]] = []
    for sid, (_sahne, ra) in ha.items():
        rb = hb.get(sid, ({}, {}))[1]
        for k, x in ra.items():
            y = rb.get(k)
            px, py = satiri_puanla(x), (satiri_puanla(y) if y is not None else None)
            if y is None or px is None or py is None or px[0] == py[0]:
                continue
            if px[0] in _PUAN_SIRASI and py[0] in _PUAN_SIRASI:
                yon = "düzeldi" if _PUAN_SIRASI[py[0]] > _PUAN_SIRASI[px[0]] else "bozuldu"
            else:  # biri motor hatası: anlam seçimi karşılaştırılamaz
                yon = "değişti"
            degisen.append((sid, x, y, px[0], py[0], yon))
    if not degisen:
        return out + ["", "Puanı değişen satır yok."]
    sayi = Counter(d[5] for d in degisen)
    out += ["", f"### Puanı değişen satırlar ({len(degisen)})", "",
            f"B'de düzelen {sayi['düzeldi']}, bozulan {sayi['bozuldu']}"
            + (f", motor hatası yüzünden değişen {sayi['değişti']}" if sayi["değişti"] else "") + ".", "",
            "| Kelime | Sahne | İngilizce | A | B | Yön |", "|---|---|---|---|---|---|"]
    for sid, x, y, px, py, yon in degisen:
        out.append(f"| {_hucre(x.get('kelime') or y.get('kelime'))} | `{sid}` | {_hucre(_ingilizce(x))} | "
                   f"{_hucre(PUAN_ADI[px] + ': ' + _turkce(x))} | {_hucre(PUAN_ADI[py] + ': ' + _turkce(y))} | {yon} |")
    return out


# --- komut satırı --------------------------------------------------------------
def _cikti_yaz(metin: str, yol: str | None, ne: str) -> None:
    if not yol:
        sys.stdout.write(metin)
        sys.stdout.flush()
        return
    hedef = Path(yol)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    hedef.write_text(metin, encoding="utf-8", newline="\n")
    _bilgi(f"{ne} yazıldı: {hedef}")


def _tam_sayi(en_az: int) -> Callable[[str], int]:
    def donustur(metin: str) -> int:
        try:
            deger = int(metin)
        except ValueError:
            raise argparse.ArgumentTypeError(f"tam sayı olmalı: {metin}") from None
        if deger < en_az:
            raise argparse.ArgumentTypeError(f"en az {en_az} olmalı: {metin}")
        return deger

    return donustur


def _saniye(sifir_olabilir: bool) -> Callable[[str], float]:
    """Saniye değeri; '2,5' gibi ondalık virgül de kabul edilir."""

    def donustur(metin: str) -> float:
        try:
            deger = float(metin.replace(",", "."))
        except ValueError:
            raise argparse.ArgumentTypeError(f"saniye olmalı: {metin}") from None
        if not math.isfinite(deger) or deger < 0 or (deger == 0 and not sifir_olabilir):
            sinir = "0 ya da daha büyük" if sifir_olabilir else "0'dan büyük"
            raise argparse.ArgumentTypeError(f"{sinir} olmalı: {metin}")
        return deger

    return donustur


def _argumanlar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python tools/ceviri_testi.py", description=__doc__, add_help=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="help", help="bu yardımı gösterir")
    p.add_argument("--engine", choices=MOTORLAR, default="ollama", help="çeviri motoru (varsayılan: ollama)")
    p.add_argument("--model", help="Ollama ya da Gemini model adı (varsayılan: uygulamanınki)")
    p.add_argument("--profile", help="oyun profili (varsayılan: uygulamanınki)")
    p.add_argument("--context", type=_tam_sayi(0), metavar="N",
                   help="bağlam olarak giden önceki satır sayısı, 0 = bağlamsız (varsayılan: uygulamanınki)")
    p.add_argument("--timeout", type=_saniye(False), default=20.0, metavar="SN",
                   help="satır başına istek zaman aşımı, saniye (varsayılan: 20)")
    p.add_argument("--scene", action="append", metavar="ID",
                   help="yalnız bu sahneler; virgülle ayrılarak ya da seçenek tekrarlanarak")
    p.add_argument("--limit", type=_tam_sayi(1), metavar="N", help="her sahneden yalnız ilk N satır")
    p.add_argument("--out", metavar="RAPOR.md", help="Markdown raporun yazılacağı dosya (verilmezse stdout)")
    p.add_argument("--json", metavar="SONUC.json", help="makine tarafından okunur sonucun yazılacağı dosya")
    p.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                   help="iki --json sonucunu yan yana karşılaştırır (çeviri yapmaz)")
    p.add_argument("--dataset", default=str(VERI_SETI), metavar="SET.json",
                   help="veri seti (varsayılan: tools/ceviri_testi_seti.json; çok anlamlı kelimeler için "
                        "tools/ceviri_testi_cokanlam.json)")
    p.add_argument("--ollama-url", metavar="URL", help="Ollama adresi (varsayılan: uygulamanınki)")
    p.add_argument("--device", choices=("cpu", "cuda"), help="quickmt cihazı (varsayılan: uygulamanınki)")
    p.add_argument("--pause", type=_saniye(True), default=0.0, metavar="SN",
                   help="satırlar arasında bekleme, saniye (ör. Gemini ücretsiz kota sınırı için)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    for akis in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe harfler bozulmasın
        if hasattr(akis, "reconfigure"):
            akis.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _argumanlar().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.compare:
            metin = karsilastirma_raporu(Path(args.compare[0]), Path(args.compare[1]))
            _cikti_yaz(metin, args.out, "Karşılaştırma")
            return 0
        return testi_calistir(args)
    except KeyboardInterrupt:
        _bilgi("İptal edildi.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
