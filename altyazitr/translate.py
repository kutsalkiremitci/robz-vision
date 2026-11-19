"""Çeviri motorları (İngilizce → Türkçe).

* QuickMtTranslator — quickmt/quickmt-en-tr (CTranslate2, CC-BY-4.0). Yerel, ücretsiz,
  CPU'da ~20-60 ms. Varsayılan motor ve LLM'lerin yedeği; ilk kullanımda ~400 MB model indirilir.
  Bağlam kullanmaz.
* OllamaTranslator — Ollama üzerinden yerel LLM (önerilen: Gemma 4). En iyi kalite: oyun profilini
  (dünya, ton, çevrilmeyecek adlar, argo sözlüğü, üslup örnekleri), önceki satırları ve sözlüğü kullanır;
  OCR'ın yanlış okuduğu harfleri düzeltir. İnternetsiz ve ücretsiz çalışır.
* GeminiTranslator — Google Gemini API (ücretsiz katman, kendi anahtarınızla); istemi Ollama ile aynı.
* DeepLTranslator — DeepL API Free (ayda 500.000 karakter ücretsiz, kendi anahtarınızla).
* NoTranslator — çeviri yapmaz (yalnızca OCR testi için).

Bağlam (Context): önceki satırların (en, tr) çiftleri, eskiden yeniye. tr boşsa o satırın güvenilir bir
Türkçesi yoktur (ör. yalnız hızlı yedek çeviri gösterildi); LLM onu yalnız İngilizce bağlam olarak görür.

Vurgulu kelimeler (oyunun renkli yazdığı yer, kişi, eşya adları) translate_marked ile çevrilir: girdi
"Drive to the [[radar station]]" gibi işaretlidir, çıktıda vurguların Türkçe karşılığı işaretlenir. LLM'ler
işareti kendisi taşır (kullanıcı mesajına kısa bir kural eklenir; sistem istemi değişmez), quickmt vurgulu
ifadeyi ayrıca çevirip tam çeviride arar, DeepL ve TranslateGemma vurgusuz çevirir. Bağlam her zaman işaretsizdir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from rapidfuzz import fuzz, process

# Yerel model kataloğu (ad, başlık, boyut, ekran kartı eşiği) tek kaynakta: ai_setup.MODEL_INFO. ai_setup bu modülü
# yalnız işlev içinde içe aktarır (is_network_error): içe aktarma döngüsü yok.
from altyazitr.ai_setup import LIGHT_MODEL, MODEL_INFO, RECOMMENDED_MODEL, model_title
from altyazitr.paths import MODEL_DIR
from altyazitr.textproc import Spans, clean_marks, mark, snap_spans, split_sentences, unmark

if TYPE_CHECKING:
    from altyazitr.game_profiles import GameProfile

log = logging.getLogger(__name__)

StatusCb = Callable[[str], None]
Context = Sequence[tuple[str, str]]


class TranslatorError(RuntimeError):
    """Kullanıcıya gösterilecek Türkçe hata mesajı içerir."""


# --- sözlük ---------------------------------------------------------------
class Glossary:
    """Kullanıcı sözlüğü. NMT/DeepL için terimler yer tutucularla korunur, LLM için
    talimat olarak eklenir. Türkçe karşılık boşsa terim olduğu gibi korunur."""

    TOKEN = "ZQX{}"

    def __init__(self, pairs: Sequence[Sequence[str]] = ()):
        entries = []
        for p in pairs:
            if not p:
                continue
            en = (p[0] or "").strip()
            tr = (p[1] if len(p) > 1 else "").strip()
            if en:
                entries.append((en, tr))
        entries.sort(key=lambda e: -len(e[0]))
        self.entries = entries
        self._pats = [re.compile(r"(?<![\w])" + re.escape(en) + r"(?![\w])", re.IGNORECASE) for en, _ in entries]

    def __bool__(self) -> bool:
        return bool(self.entries)

    def protect(self, text: str) -> tuple[str, dict[str, str]]:
        """Yalnızca *çevrilmeyecek* terimleri (ad, mekan) yer tutucuyla değiştirir: çevirisi olan
        sıradan kelimeleri yer tutucuya çevirmek NMT'nin dilbilgisini bozuyor (testte 'cop→polis'
        cümleyi bozdu)."""
        mapping: dict[str, str] = {}
        for i, ((en, tr), pat) in enumerate(zip(self.entries, self._pats)):
            if tr and tr.casefold() != en.casefold():
                continue
            if pat.search(text):
                tok = self.TOKEN.format(i)
                text = pat.sub(tok, text)
                mapping[tok] = tr or en
        return text, mapping

    @staticmethod
    def restore(text: str, mapping: dict[str, str]) -> str:
        for tok, rep in mapping.items():
            num = tok[3:]
            text = re.sub(r"Z\s*Q\s*X\s*" + num + r"(?!\d)", lambda _m, r=rep: r, text, flags=re.IGNORECASE)
        return text

    def prompt_lines(self) -> str:
        out = []
        for en, tr in self.entries:
            out.append(f"- {en} = {en} (do not translate)" if not tr or tr == en else f"- {en} = {tr}")
        return "\n".join(out)


def _sentence_case_if_shouting(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.85:
        low = text.lower()
        low = re.sub(r"\bi\b", "I", low)
        return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), low)
    return text


# --- önbellek ---------------------------------------------------------------
class TranslationCache:
    """LRU önbellek + son girdiler üzerinde bulanık (fuzzy) arama."""

    def __init__(self, size: int = 20000):
        self._d: OrderedDict[str, str] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()
        self._dirty = False

    def get(self, key: str, fuzzy: bool = True) -> str | None:
        """Birebir anahtar; yoksa (fuzzy ise) son girdilerde çok benzer anahtar (OCR'ın tek harf farkı)."""
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
            if not fuzzy or len(key) < 12:
                return None
            recent = list(self._d.keys())[-96:]
            hit = process.extractOne(key, recent, scorer=fuzz.ratio, score_cutoff=94)
            return self._d[hit[0]] if hit else None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._size:
                self._d.popitem(last=False)
            self._dirty = True

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._dirty = True

    # --- kalıcı önbellek: tekrar eden satırlar sonraki oturumlarda da anında çevrilir
    VERSION = 1

    def load(self, path) -> None:
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("v") != self.VERSION:
                return
            with self._lock:
                for k, v in data.get("items", [])[-self._size:]:
                    self._d[k] = v
            log.info("Çeviri önbelleği yüklendi: %d satır", len(self._d))
        except FileNotFoundError:
            pass
        except Exception:
            log.warning("Çeviri önbelleği okunamadı", exc_info=True)
        self._dirty = False

    def save(self, path) -> None:
        if not self._dirty:
            return
        with self._lock:
            items = list(self._d.items())
            self._dirty = False
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump({"v": self.VERSION, "items": items}, fp, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            log.warning("Çeviri önbelleği kaydedilemedi", exc_info=True)


# --- temel sınıf ----------------------------------------------------------
class Translator:
    key = "base"
    label = ""

    def prepare(self, status: StatusCb) -> None:
        """Modeli indirir/yükler. Uzun sürebilir; arayüz iş parçacığında çağrılmaz. Karma modda asıl motor bunu
        uzak çeviri iş parçacığında yapar; o sırada hızlı yerel çeviri satırları göstermeye devam eder."""

    def translate(self, text: str, context: Context = ()) -> str:  # pragma: no cover - arayüz
        raise NotImplementedError

    def translate_marked(self, text: str, context: Context = ()) -> str:
        """Vurgulu kelimeleri [[...]] ile işaretli satırı çevirir. Çıktıda vurguların Türkçe karşılığı işaretlidir;
        karşılığı bulunamayan vurgu işaretsiz kalır. Varsayılan: vurgusuz çeviri."""
        return self.translate(unmark(text)[0], context)

    def abort(self) -> None:
        """Süren ya da başlayacak hazırlığı (prepare) bırakır: hat durdurulurken başka iş parçacığından çağrılır.
        Kalıcıdır: motor her çalıştırmada yeniden oluşturulur, durdurulan motor bir daha hazırlanmaz (hazırlık
        başlamadan gelen Durdur da kaybolmasın). Uzun süren hazırlığı olan motor (yerel LLM) bunu uygular;
        ötekilerde bir şey yapmaz."""

    def healthy(self) -> bool:
        """Art arda hata veren motor gerçekten çalışabilir durumda mı (hat buna göre motoru yeniden hazırlar).
        Yerel LLM: model Ollama'nın belleğinde mi. Ötekiler: hep True (hatalar geçicidir: ağ, kota)."""
        return True

    def keep_warm(self) -> None:
        """Hat çalışırken uzun süre çeviri istenmediyse çağrılır (oyun duraklatıldı, konuşma yok). Modelini bellekte
        tutan motor (yerel LLM) kalma süresini yeniler; ötekilerde bir şey yapmaz."""

    def close(self) -> None:
        pass


class NoTranslator(Translator):
    key = "none"
    label = "Çeviri yok (yalnızca İngilizce göster)"

    def translate(self, text: str, context: Context = ()) -> str:
        return text

    def translate_marked(self, text: str, context: Context = ()) -> str:
        return text


# --- quickmt (CTranslate2) ------------------------------------------------
QUICKMT_REPO = "quickmt/quickmt-en-tr"
QUICKMT_FILES = ["model.bin", "config.json", "source_vocabulary.json", "target_vocabulary.json",
                 "src.spm.model", "tgt.spm.model", "README.md"]
QUICKMT_REQUIRED = [f for f in QUICKMT_FILES if f != "README.md"]
QUICKMT_DIR = MODEL_DIR / "quickmt-en-tr"
QUICKMT_TOTAL_MB = 404.0  # 7 dosyanın toplamı ≈ 403,6 MB (model.bin = 401.699.775 bayt)
QUICKMT_MODEL_MIN_BYTES = 300_000_000  # bundan küçük model.bin yarım/bozuk sayılır

ProgressCb = Callable[[float], None]


def quickmt_downloaded() -> bool:
    """QUICKMT_DIR'de gerekli tüm dosyalar var ve model.bin tam boyutlu mu?"""
    d = QUICKMT_DIR
    try:
        if not all((d / f).is_file() for f in QUICKMT_REQUIRED):
            return False
        return (d / "model.bin").stat().st_size > QUICKMT_MODEL_MIN_BYTES
    except OSError:
        return False


def _dir_size_mb(path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1_000_000


def download_fraction(done_mb: float, total_mb: float = QUICKMT_TOTAL_MB) -> float:
    """İndirilen MB → 0..0,99 oranı. %100'ü yalnızca indirme gerçekten bitince bildiririz."""
    if total_mb <= 0:
        return 0.0
    return max(0.0, min(0.99, done_mb / total_mb))


# Ağ hatası sayılan sınıf adları (requests/httpx/httpx2/httpcore/urllib3/aiohttp)
_NET_LIBS = {"requests", "httpx", "httpx2", "httpcore", "urllib3", "aiohttp"}
_NET_NAMES = {"ConnectionError", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
              "Timeout", "TimeoutException", "TimeoutError", "ConnectTimeoutError", "ReadTimeoutError",
              "NetworkError", "TransportError", "ProxyError", "ReadError", "WriteError", "RemoteProtocolError",
              "NewConnectionError", "NameResolutionError", "MaxRetryError", "SSLError",
              "ClientConnectorError", "ServerDisconnectedError"}


def _exc_chain(exc: BaseException):
    """Hatanın kendisi ve nedenleri (__cause__, gizlenmemişse __context__)."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)


def is_network_error(exc: BaseException) -> bool:
    """Gerçek bağlantı/zaman aşımı hatası mı (huggingface_hub'ın sarmaladıkları dahil)?"""
    import socket

    for e in _exc_chain(exc):
        if isinstance(e, (ConnectionError, TimeoutError, socket.gaierror)):
            return True
        for cls in type(e).__mro__:
            if cls.__module__.split(".")[0] in _NET_LIBS and cls.__name__ in _NET_NAMES:
                return True
    return False


def download_error_message(exc: BaseException) -> str:
    """İlk satır kısa Türkçe özet, ikinci satır ayrıntı."""
    detail = f"{type(exc).__name__}: {exc}".strip()
    if is_network_error(exc):
        head = "Çeviri modeli indirilemedi. İnternet bağlantınızı kontrol edin."
    elif any(isinstance(e, OSError) and (e.errno == 28 or getattr(e, "winerror", None) in (39, 112))
             for e in _exc_chain(exc)):
        head = "Çeviri modeli indirilemedi: diskte yeterli boş alan yok (~400 MB gerekli)."
    else:
        head = f"Çeviri modeli indirilemedi ({type(exc).__name__})."
    return f"{head}\nAyrıntı: {detail}"


def _snapshot_download():
    # İlerleme çubukları (tqdm) sys.stderr'e yazar; pythonw/--windowed altında stderr None olduğundan
    # "'NoneType' object has no attribute 'write'" hatası veriyordu. İlerlemeyi klasör boyutundan izliyoruz.
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import disable_progress_bars

    disable_progress_bars()
    return snapshot_download


def download_quickmt(status: StatusCb, *, progress: ProgressCb | None = None) -> None:
    """quickmt modelini QUICKMT_DIR'e indirir. status: Türkçe durum metni,
    progress: 0..1 oran (bitene kadar en fazla 0,99). Hata → TranslatorError."""
    target = QUICKMT_DIR
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download = _snapshot_download()
    done = threading.Event()

    def report() -> None:
        mb = _dir_size_mb(target)
        status(f"Çeviri modeli indiriliyor… {mb:.0f} / {QUICKMT_TOTAL_MB:.0f} MB")
        if progress is not None:
            progress(download_fraction(mb))

    def watch() -> None:
        while not done.wait(0.5):
            try:
                report()
            except Exception:  # geri çağırma hatası indirmeyi bozmasın
                log.debug("İndirme ilerlemesi bildirilemedi", exc_info=True)

    t = threading.Thread(target=watch, name="indirme-izleyici", daemon=True)
    t.start()
    try:
        snapshot_download(QUICKMT_REPO, local_dir=str(target), allow_patterns=QUICKMT_FILES)
    except Exception as e:
        log.warning("quickmt indirilemedi", exc_info=True)
        raise TranslatorError(download_error_message(e)) from e
    finally:
        done.set()
        t.join(2.0)
    if not quickmt_downloaded():
        raise TranslatorError("Çeviri modeli eksik indirildi. Lütfen tekrar deneyin.\n"
                              f"Ayrıntı: {target} klasöründe gerekli dosyalar eksik.")
    if progress is not None:
        progress(1.0)
    status("Çeviri modeli indirildi.")


def _add_cuda_dll_dirs() -> None:
    """pip ile kurulan nvidia-cublas-cu12 vb. DLL klasörlerini arama yoluna ekler."""
    import site

    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    if getattr(sys, "_MEIPASS", None):
        roots.append(sys._MEIPASS)  # type: ignore[attr-defined]
    for root in roots:
        nv = os.path.join(root, "nvidia")
        if not os.path.isdir(nv):
            continue
        for sub in os.listdir(nv):
            b = os.path.join(nv, sub, "bin")
            if os.path.isdir(b):
                try:
                    os.add_dll_directory(b)
                except OSError:
                    pass
                os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")


# Yüklenmiş quickmt modelleri (istenen cihaza göre): işlem hattı her yeniden başlatmada (Başlat, ayar değişikliği)
# yeni bir QuickMtTranslator kurar; model diskten yeniden yüklenmesin (~0,5–1 sn, bu sürede altyazı çıkmaz).
_quickmt_models: dict[str, tuple[object, object, object, str]] = {}
_quickmt_lock = threading.Lock()


class QuickMtTranslator(Translator):
    key = "quickmt"
    label = "Yerel hızlı çeviri — quickmt (ücretsiz, en hafif, bağlamsız)"

    def __init__(self, glossary: Glossary, device: str = "cpu"):
        self.glossary = glossary
        self.device = device
        self._tr = None
        self._src = None
        self._tgt = None
        self.active_device = "cpu"

    def prepare(self, status: StatusCb) -> None:
        if self._tr is not None:
            return
        with _quickmt_lock:
            cached = _quickmt_models.get(self.device)
        if cached is not None:  # önceki çalıştırmada yüklendi
            self._tr, self._src, self._tgt, self.active_device = cached
            return
        if not quickmt_downloaded():
            status("Çeviri modeli ilk kez indiriliyor (~400 MB, bir kereye mahsus)…")
            download_quickmt(status)
        import ctranslate2
        import sentencepiece as spm

        status("Çeviri modeli yükleniyor…")
        d = str(QUICKMT_DIR)
        tr = None
        if self.device == "cuda":
            try:
                _add_cuda_dll_dirs()
                if ctranslate2.get_cuda_device_count() > 0:
                    tr = ctranslate2.Translator(d, device="cuda", compute_type="int8_float16")
                    tr.translate_batch([["▁Hello", "</s>"]], max_decoding_length=4)  # cuBLAS denemesi
                    self.active_device = "cuda"
            except Exception as e:
                log.warning("GPU (CUDA) ile yüklenemedi, CPU kullanılacak: %s", e)
                tr = None
        if tr is None:
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            tr = ctranslate2.Translator(d, device="cpu", compute_type="int8", intra_threads=threads)
            self.active_device = "cpu"
        self._tr = tr
        self._src = spm.SentencePieceProcessor(model_file=os.path.join(d, "src.spm.model"))
        self._tgt = spm.SentencePieceProcessor(model_file=os.path.join(d, "tgt.spm.model"))
        with _quickmt_lock:
            _quickmt_models[self.device] = (self._tr, self._src, self._tgt, self.active_device)
        log.info("quickmt yüklendi (%s)", self.active_device)

    def translate(self, text: str, context: Context = ()) -> str:
        return self._translate_all([text])[0]

    def translate_marked(self, text: str, context: Context = ()) -> str:
        """En iyi çaba: vurgulu ifadeler aynı toplu çağrıda ayrıca çevrilir ve tam çeviride aranır (Türkçe ekleri
        tolere edilir: "radar istasyonu" ↔ "Radar istasyonuna"). Bulunamayan ifade vurgusuz kalır. Çevrilmeyecek ad
        (Lucia, Ammu-Nation) çevrilmez, olduğu gibi aranır. Diğer ifadeler "The … ." kalıbında çevrilir: quickmt
        tek başına verilen ifadeyi eksik çevirebiliyor ("radar station" → "Radar")."""
        plain, spans = unmark(text)
        phrases = [plain[s:e] for s, e in spans]
        names = [self._kept_name(p) for p in phrases]
        full, *outs = self._translate_all([plain, *(f"The {p}." for p, n in zip(phrases, names) if n is None)])
        found = iter(outs)
        return mark(full, locate_phrases(full, [n if n is not None else next(found) for n in names]))

    def _kept_name(self, phrase: str) -> str | None:
        """İfade bütünüyle çevrilmeyecek bir adsa (sözlük, oyun profili) o ad; değilse None."""
        prot, mapping = self.glossary.protect(phrase)
        return mapping.get(prot.strip())

    def _translate_all(self, texts: Sequence[str]) -> list[str]:
        """Metinleri tek translate_batch çağrısıyla çevirir (cümlelere bölünür; çevrilmeyecek adlar korunur)."""
        assert self._tr is not None and self._src is not None and self._tgt is not None
        batch, jobs = [], []
        for text in texts:
            text, mapping = self.glossary.protect(_sentence_case_if_shouting(text))
            parts = split_sentences(text) or [text]
            batch += [self._src.encode(p, out_type=str) + ["</s>"] for p in parts]
            jobs.append((len(parts), mapping))
        res = self._tr.translate_batch(batch, beam_size=2, max_decoding_length=256, repetition_penalty=1.05)
        decoded = [self._tgt.decode(r.hypotheses[0]) for r in res]
        out, i = [], 0
        for n, mapping in jobs:
            out.append(Glossary.restore(" ".join(decoded[i:i + n]), mapping).strip())
            i += n
        return out

    def close(self) -> None:
        """Bu motoru kapatır; model bellekte kalır (sonraki çalıştırma kullanır, bkz. _quickmt_models)."""
        self._tr = None


_TR_WORD_RE = re.compile(r"\w+(?:['’-]\w+)*")


def _tr_fold(s: str) -> str:
    """Türkçe büyük/küçük harf duyarsız karşılaştırma: İ → i, I → ı."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def _stem_match(p: str, w: str, short_exact: bool = True) -> float:
    """İfadenin bir kelimesi (p) çeviride w olarak geçiyor mu (0..1). Türkçe ekler sona gelir, kök sonu da
    yumuşayabilir (kitap → kitabı): ortak önek p'nin boyuna oranlanır. Tek kelimelik ifadede kısa kelime (ev) tam
    eşleşmeli ("evet" ev değildir); çok kelimeli ifadede komşu kelimeler de tuttuğu için eki kabul edilir (eve)."""
    if (short_exact and len(p) <= 2) or len(w) < len(p) - 1:
        return 1.0 if p == w else 0.0
    n = 0
    for a, b in zip(p, w):
        if a != b:
            break
        n += 1
    return n / len(p)


def locate_phrases(text: str, phrases: Sequence[str], min_score: float = 0.75) -> Spans:
    """Ayrıca çevrilmiş ifadelerin tam çevirideki yerleri (bulunanlar). Her ifade, kelime sayısı kadar ardışık
    kelimeyle karşılaştırılır; her kelime en az `min_score` tutmalı, en iyi tutan pencere seçilir. Tutmazsa ifadenin
    son kelimesi tek başına aranır: Türkçe tamlamada asıl kelime sondadır ve tam çeviri tamlamayı tek kelimeyle
    söyleyebilir ("Polis karakolu" ↔ "Karakola"). Bulunan aralıklar birbiriyle çakışmaz."""
    words = [(m.start(), m.end(), _tr_fold(m.group())) for m in _TR_WORD_RE.finditer(text)]
    taken: Spans = []

    def best_window(pw: list[str]) -> tuple[int, int] | None:
        k = len(pw)
        best: tuple[float, int] | None = None
        for i in range(len(words) - k + 1 if k else 0):
            s, e = words[i][0], words[i + k - 1][1]
            if any(s < te and ts < e for ts, te in taken):
                continue
            scores = [_stem_match(p, words[i + j][2], short_exact=k == 1) for j, p in enumerate(pw)]
            if min(scores) >= min_score and (best is None or sum(scores) > best[0]):
                best = (sum(scores), i)
        return None if best is None else (words[best[1]][0], words[best[1] + k - 1][1])

    for phrase in phrases:
        pw = [_tr_fold(m.group()) for m in _TR_WORD_RE.finditer(phrase)]
        found = best_window(pw) or (best_window(pw[-1:]) if len(pw) > 1 else None)
        if found is not None:
            taken.append(found)
    return snap_spans(text, taken, whole_words=True)


# --- Ollama ---------------------------------------------------------------
TG_TEMPLATE = (
    "You are a professional English (en) to Turkish (tr) translator. Your goal is to accurately "
    "convey the meaning and nuances of the original English text while adhering to Turkish grammar, "
    "vocabulary, and cultural sensitivities.\n"
    "Produce only the Turkish translation, without any additional explanations or commentary. "
    "Please translate the following English text into Turkish:\n\n\n{text}"
)

# Önerilen yerel model ve hazır listeler (ilk öğe önerilen). Ölçümler: RTX 4070, Ollama 0.40, GTA 6 profili. Model
# adları, başlıkları ve boyutları model kataloğundan (MODEL_INFO) gelir: ilk açılış ve Çeviri sayfası aynı bilgiyi
# gösterir.
RECOMMENDED_OLLAMA_MODEL = RECOMMENDED_MODEL  # Gemma 4 12B: 12 GB+ ekran kartı
LIGHT_OLLAMA_MODEL = LIGHT_MODEL  # Gemma 4 E4B: 8 GB ekran kartı ya da oyun da bu bilgisayarda


def _size_text(n: int) -> str:
    """Ondalık GB, Türkçe yazımla: 8_000_000_000 → "8 GB", 6_600_000_000 → "6,6 GB"."""
    return f"{n / 1e9:.1f}".replace(".", ",").removesuffix(",0") + " GB"


def _preset(model: str, note: str) -> tuple[str, str]:
    """Hazır listedeki satır: başlık ve boyut model kataloğundan; note'taki "{size}" boyutla dolar."""
    info = MODEL_INFO[model]
    return model, f"{info.title} — {note.format(size=_size_text(info.size))}"


OLLAMA_PRESETS = [
    _preset(RECOMMENDED_OLLAMA_MODEL, "önerilen: en iyi bağlam ve Türkçe, ~{size}, 12 GB+ ekran kartı"),
    _preset(LIGHT_OLLAMA_MODEL, "en hızlı, ~{size}, 8 GB ekran kartı ya da yayın + oyun aynı kartta"),
    _preset("gemma4:26b", "en yüksek kalite, ~{size}, 24 GB ekran kartı"),
    ("translategemma:12b", "TranslateGemma 12B — yalnız çeviri modeli; bağlam ve profil kullanmaz"),
]

# Yerel yapay zekânın hazırlık hataları (TranslatorError): 1. satır kısa ve ne yapılacağını söyler, 2. satır ayrıntı.
TEXT_OLLAMA_DOWN = "Ollama çalışmıyor ve başlatılamadı. Çeviri sayfasındaki Önerilen kurulum'a basın."


def missing_model_text(model: str) -> str:
    """Model Ollama'da yok. Önerilen kurulumun kurduğu modellerde (Gemma 4 12B / E4B) o düğme, ötekilerde "Modeli
    indir" söylenir; bilinen modelin adı kataloğdan gelir ("Gemma 4 12B")."""
    m = model.strip()
    if m in (RECOMMENDED_OLLAMA_MODEL, LIGHT_OLLAMA_MODEL):
        head = f"{model_title(m)} indirilmedi; Çeviri sayfasındaki Önerilen kurulum'a basın."
    else:
        name = model_title(m) if m in MODEL_INFO else f"'{m}' modeli"
        head = f"{name} indirilmedi; Çeviri sayfasındaki Modeli indir'e basın."
    return f"{head}\nAyrıntı: '{m}' modeli Ollama'da yüklü değil."

# Her oyun profilinin kurallarına eklenen, istemin biçimine bağlı ortak kurallar
LLM_BASE_RULES = (
    "The input is read from the screen by OCR: silently fix obvious misreadings (wrong or missing letters, "
    "missing apostrophes, ALL CAPS) and translate what was meant.",
    "Never invent words or controller buttons that are not in the input; if a button icon is missing "
    "(\"Hold to interact\"), leave the button out.",
    "Translate only the last EN line. Earlier lines are context: use them to understand who is talking, what "
    "is happening and what short replies refer to. Never translate or repeat them.",
    "Output only the Turkish subtitle: no labels, quotes, notes, alternatives, explanations or English.",
)
LLM_INTRO = "You translate English video game subtitles into Turkish, live, for a Turkish stream audience."
# Vurgulu satırda kullanıcı mesajına eklenen kural. Sistem istemi her istekte aynı kalır (Ollama istemin ortak
# başını önbellekten kullanır); işaretsiz satırın istemi hiç değişmez.
LLM_MARK_RULE = ("The words in [[ ]] are highlighted in the game. In your Turkish line, put [[ ]] around their "
                 "translation, suffixes included.")

# Ollama istek ayarları. num_ctx her istekte aynı kalmalı: değişirse Ollama modeli yeniden yükler.
OLLAMA_NUM_CTX = 4096
# Model bellekte ne kadar kalır. Çeviri sürerken her istek süreyi yeniler. Hat durunca model bellekten ATILMAZ
# (yeniden Başlat ve ayar değişikliği modeli baştan yüklemesin: 12B'de 5-100 sn), yalnız süresi kısalır; uygulama
# kapanırken ya da başka modele geçilirken hemen boşaltılır (release_ollama_models).
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_IDLE_KEEP_ALIVE = "10m"
OLLAMA_LOAD_TIMEOUT_S = 600.0  # ilk yükleme: 8 GB'lık model yavaş diskten dakikalar sürebilir
# Çıktı sınırı: bir altyazı satırının çevirisi kısadır. Model sapıtıp uzun yazarsa zayıf makinede saniyeler gider;
# sınır satırın uzunluğuna göre konur (kelime başına ~4 token + pay), en çok OLLAMA_MAX_PREDICT.
OLLAMA_MAX_PREDICT = 160
# Model aynı belirteci üst üste üretmeye girerse Ollama isteği keser ("token repeat limit"; gerçek oturumda seyrek
# görüldü). O satır bu seçeneklerle bir kez daha istenir; yine olmazsa hata (hat hızlı çeviriyle sürer).
OLLAMA_REPEAT_RETRY = {"repeat_penalty": 1.2, "temperature": 0.4}
# Isınma cümleleri: ilk istek modeli belleğe yükler ve sistem istemini işler (asıl bekleme budur); ikincisi küçük
# eklemeli isteğin ilk çalıştırmasını da yayından önceye alır.
WARMUP_LINES = ("Hey.", "Get in the car, we're late.")


_LOCALHOST_RE = re.compile(r"^(http://)localhost(?=[:/]|$)", re.I)


def ollama_base(url: str) -> str:
    """Ollama adresinin kullanılan hali: boşluk ve sondaki "/" atılır, `localhost` 127.0.0.1 yapılır.

    Windows `localhost`u önce IPv6'ya (::1) çözer; Ollama yalnız IPv4'te (127.0.0.1) dinler, bu yüzden `localhost`a
    açılan her yeni bağlantı önce ~2 sn boşuna bekler (ölçüm: 2050 ms'ye karşı 15 ms). Varsayılan adres zaten
    127.0.0.1'dir; bu, adresi elle `localhost` yazan kullanıcıyı korur (ollama_runtime._clean_url ile aynı kural).
    https adresine dokunulmaz: sertifika `localhost` adına düzenlenmiş olabilir."""
    return _LOCALHOST_RE.sub(r"\g<1>127.0.0.1", (url or "").strip().rstrip("/"))


def predict_limit(text: str) -> int:
    """Satır için üretilecek en çok token sayısı (bkz. OLLAMA_MAX_PREDICT)."""
    return min(OLLAMA_MAX_PREDICT, 32 + 4 * len(text.split()))


class ContextWindow:
    """Yerel LLM'e giden bağlam penceresi: istemin başı istekler arasında aynı kalsın diye yalnız sona eklenir.

    İşlem hattı her çeviride son N satırı verir (kayan pencere). Kayan pencere her yeni satırda istemin bağlam
    kısmını baştan değiştirir; model de o kısmı her satırda yeniden işler (ölçüm, Gemma 4 12B: satır başına ~245
    token; yalnız işlemcide çalışan zayıf makinede bu saniyeler eder). Bu sınıf pencereyi büyüterek taşır: yeni
    satırlar sona eklenir, pencere `limit`i aşınca son `keep` satıra iner. İstemin başı çoğu istekte bir öncekiyle
    aynıdır, Ollama onu önbellekten kullanır ve yalnız yeni satırı işler (aynı ölçümde ~58 token).

    N satırlık ayar için pencere N/2 ile N + N/2 satır arasında gezer (8 → 4..12)."""

    def __init__(self, lines: int):
        n = max(0, int(lines))
        self.keep = (n + 1) // 2
        self.limit = n + max(2, n // 2) if n else 0
        self._win: list[tuple[str, str]] = []

    def reset(self) -> None:
        self._win = []

    def update(self, context: Context) -> list[tuple[str, str]]:
        """Hattın verdiği son satırlardan (context) bu isteğin bağlamını üretir. Bağlam boşsa (yeni sahne) pencere
        de boşalır. Önceki pencereyle hizalanamayan bağlam (hat bağlamı baştan kurdu) pencerenin yerini alır."""
        ctx = [(str(en), str(tr or "")) for en, tr in context]
        if not ctx or self.limit <= 0:
            self._win = []
            return []
        merged: list[tuple[str, str]] | None = None
        # Aynı satırın yeni okuması gelince hat eskisini bağlamdan çıkarır: penceremizin sonundaki en çok iki eski
        # okuma bırakılarak hizalama denenir (istemin yalnız sonu değişir).
        for drop in range(min(2, len(self._win)) + 1):
            base = self._win[:len(self._win) - drop]
            new = self._new_lines(base, ctx)
            if new is not None:
                merged = base + new
                break
        if merged is None:
            merged = ctx
        if len(merged) > self.limit:
            merged = merged[-self.keep:] if self.keep else []
        self._win = merged
        return list(merged)

    @staticmethod
    def _new_lines(base: list[tuple[str, str]], ctx: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
        """ctx'in, base'in sonuyla örtüşen kısmından sonraki (yeni) satırları; örtüşme yoksa None. Satırlar
        İngilizce metinleriyle eşleşir (Türkçesi noktalama düzeltmesiyle değişmiş olabilir)."""
        if not base:
            return list(ctx)
        have = [en for en, _ in base]
        ens = [en for en, _ in ctx]
        for fresh in range(len(ctx) + 1):
            k = min(len(have), len(ctx) - fresh)  # örtüşen satır sayısı
            if k <= 0:
                break
            if have[len(have) - k:] == ens[len(ctx) - fresh - k:len(ctx) - fresh]:
                return list(ctx[len(ctx) - fresh:])
        return None


def _merge_terms(profile: GameProfile | None, glossary: Glossary) -> tuple[list[str], list[tuple[str, str]]]:
    """İstem için çevrilmeyecek adlar ve tercih edilen karşılıklar: profil + kullanıcı sözlüğü.
    Kullanıcının girdisi aynı İngilizce terim için profili ezer; büyük/küçük harf fark etmez."""
    keep: dict[str, str] = {}
    terms: dict[str, tuple[str, str]] = {}
    if profile is not None:
        for name in profile.keep:
            keep.setdefault(name.casefold(), name)
        for en, tr in profile.glossary:
            terms[en.casefold()] = (en, tr)
    for en, tr in glossary.entries:
        k = en.casefold()
        if not tr or tr.casefold() == k:
            keep.setdefault(k, en)
            terms.pop(k, None)
        else:
            terms[k] = (en, tr)
            keep.pop(k, None)
    return list(keep.values()), list(terms.values())


def build_system_prompt(profile: GameProfile | None, glossary: Glossary, extra: str = "") -> str:
    """LLM'in sistem istemi. Her istekte aynıdır (oyun ve ayarlar değişmedikçe); satıra bağlı her şey
    kullanıcı mesajındadır (build_user_prompt)."""
    parts = [LLM_INTRO]
    if profile is not None and profile.world:
        parts.append(f"Game: {profile.name}. {profile.world}")
    rules = [*(profile.rules if profile is not None else ()), *LLM_BASE_RULES]
    parts.append("Rules:\n" + "\n".join(f"- {r}" for r in rules))
    keep, terms = _merge_terms(profile, glossary)
    if keep:
        parts.append("Never translate these names: " + ", ".join(keep) + ".")
    if terms:
        parts.append("Preferred terms: " + "; ".join(f"{en} = {tr}" for en, tr in terms) + ".")
    extra = extra.strip()
    if extra:
        parts.append(f"Extra context from the player: {extra}")
    if profile is not None and profile.examples:
        parts.append("Examples:\n" + "\n".join(f"EN: {en}\nTR: {tr}" for en, tr in profile.examples))
    return "\n\n".join(parts)


def build_user_prompt(text: str, context: Context = ()) -> str:
    """Önceki satırlar (bağlam) ve çevrilecek satır. Türkçesi boş bağlam satırı yalnız İngilizce gider. Satırda
    vurgu işareti ([[...]]) varsa satırdan hemen önce işaret kuralı eklenir; yoksa istem hiç değişmez."""
    parts = []
    if context:
        parts.append("Previous lines (context only):")
        parts += [f"EN: {en}\nTR: {tr}" if tr else f"EN: {en}" for en, tr in context]
        parts.append("Line to translate:")
    if unmark(text)[1]:
        parts.append(LLM_MARK_RULE)
    parts.append(f"EN: {text}\nTR:")
    return "\n".join(parts)


_LABEL_RE = re.compile(r"^(?:tr|türkçe|turkish|çeviri|translation|translate|line to translate)\s*:\s*", re.I)


def _clean_llm_output(s: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    lines = [ln.strip() for ln in s.strip().splitlines() if ln.strip()]
    # model istemin biçimini tekrarlarsa ("EN: …" + "TR: …") yalnız Türkçe satır kalır
    if any(re.match(r"^tr\s*:", ln, re.I) for ln in lines):
        lines = [ln for ln in lines if not re.match(r"^en\s*:", ln, re.I)]
    if not lines:
        return ""
    out = " ".join(lines[:2])
    for _ in range(2):  # "Translate: TR: …" gibi iç içe etiketler
        out = _LABEL_RE.sub("", out)
    return out.strip().strip('"“”«»').strip()


class OllamaTranslator(Translator):
    key = "ollama"
    label = "Yerel yapay zekâ — Ollama (önerilen kalite, ücretsiz, internetsiz)"

    def __init__(self, url: str, model: str, glossary: Glossary, game_context: str = "",
                 timeout: float = 6.0, profile: GameProfile | None = None, context_lines: int = 8):
        self.url = ollama_base(url)
        self.model = model.strip()
        self.glossary = glossary
        self.game_context = game_context.strip()
        self.timeout = timeout
        self.profile = profile
        self._s = _ollama_session()
        self._think_ok = True
        self.is_tg = self.model.lower().startswith("translategemma")
        self.system = build_system_prompt(profile, glossary, self.game_context)
        self._window = ContextWindow(context_lines)  # istem önbelleği için yalnız sona eklenen bağlam
        self._abort = threading.Event()

    def prepare(self, status: StatusCb) -> None:
        """Ollama açık mı ve model yüklü mü; kurulu ama kapalı Ollama'yı başlatır, sonra modeli belleğe yükleyip
        ısıtır. Ollama'ya ulaşılamazsa ya da model yoksa TranslatorError (1. satır kısa ve ne yapılacağını söyler):
        hat hızlı çeviriyle sürer, kullanıcıya pencere açmayan bildirim gider (Pipeline.notice).

        Adımlar ayrı bir iş parçacığında yürür (Ollama'nın açılması ~25 sn, modelin belleğe yüklenmesi dakikayı
        bulabilir); bu yöntem durumu geçen süreyle bildirir ve abort() çağrılınca hemen döner: Durdur ve yeniden
        Başlat hazırlığı beklemez. Yarıda bırakılan yükleme Ollama'da sürer, bir sonraki Başlat hazır modeli bulur.
        Model zaten bellekteyse (önceki çalıştırmadan) ısınma ~1 sn'dir."""
        if self._abort.is_set():  # hazırlık başlamadan Durdur'a basıldı
            raise TranslatorError("Yapay zekâ hazırlığı durduruldu.")
        t0 = time.monotonic()
        last = [""]  # son durum iletisi: uzun süren adımda geçen süreyle yenilenir
        found: list[bool] = []
        errors: list[Exception] = []
        done = threading.Event()

        def say(msg: str) -> None:
            last[0] = msg
            if not self._abort.is_set():  # bırakılan hazırlık arayüze yazmaz
                status(msg)

        def work() -> None:
            try:
                found.append(self._prepare_steps(say))
            except Exception as e:
                errors.append(e)
            finally:
                done.set()

        threading.Thread(target=work, name="ollama-hazirlik", daemon=True).start()
        shown = 0
        while not done.wait(0.25):
            if self._abort.is_set():
                raise TranslatorError("Yapay zekâ hazırlığı durduruldu.")
            sec = int(time.monotonic() - t0)
            if last[0] and sec >= 2 and sec != shown:  # uzun adımda geçen süre görünsün (takılmış sanılmasın)
                shown = sec
                status(f"{last[0]} {sec} sn")
        if errors:
            raise errors[0]
        log.info("Ollama hazır: %s, %.1f sn (%s)", self.model, time.monotonic() - t0,
                 "bellekteydi" if found and found[0] else "belleğe yüklendi")

    def _prepare_steps(self, say: StatusCb) -> bool:
        """Hazırlığın adımları (kendi iş parçacığında): bağlan, gerekirse Ollama'yı başlat, modeli belleğe yükle ve
        ısıt. Model zaten bellekteydiyse True. Bağlantı ve model hataları TranslatorError olarak çıkar; ısınma
        isteğinin hatası yutulur (çeviri yine denensin, hata orada görünür)."""
        say("Ollama'ya bağlanılıyor…")
        names = ollama_list(self.url)
        if names is None:  # kurulu ama kapalı olabilir (Windows açılışında başlamadı ya da kapatıldı)
            names = self._start_ollama(say)
        if names is None:
            raise TranslatorError(f"{TEXT_OLLAMA_DOWN}\nAyrıntı: {self.url} yanıt vermiyor.")
        if not _model_in(self.model, names):
            raise TranslatorError(missing_model_text(self.model))
        if self._abort.is_set():  # hazırlık bırakıldı: model boşuna belleğe yüklenmesin
            return False
        release_ollama_models(keep=(self.url, self.model))  # önceki model ekran kartı belleğini tutmasın
        in_memory = ollama_loaded(self.url, self.model)
        title = model_title(self.model) or self.model
        say(f"{title} {'ısınıyor' if in_memory else 'belleğe yükleniyor'}…")
        try:
            self._warm_up()
        except Exception as e:
            log.warning("Ollama ısınması başarısız: %s", e)
        return in_memory

    def _warm_up(self) -> None:
        """Modeli belleğe yükler ve ısıtır: ilk çeviriyle aynı sistem istemi ve aynı seçeneklerle (Ollama sistem
        istemini önbelleğe alır; num_ctx aynı olduğu için model sonradan yeniden yüklenmez)."""
        ctx: list[tuple[str, str]] = []
        for i, line in enumerate(WARMUP_LINES):
            out = self._request(line, ctx, timeout=OLLAMA_LOAD_TIMEOUT_S if i == 0 else 60.0)
            if self._abort.is_set():  # hazırlık bırakıldı: model yüklendi, gerisi bir sonraki Başlat'ta
                _settle_abandoned(self.url, self.model)
                return
            ctx.append((line, out))

    def abort(self) -> None:
        self._abort.set()

    def healthy(self) -> bool:
        return ollama_loaded(self.url, self.model)

    def keep_warm(self) -> None:
        """Modelin bellekte kalma süresini yeniler. Süre dolup model boşalırsa uzun bir sessizlikten sonraki ilk
        satır onun yeniden yüklenmesini beklerdi. Boş istek modele iş yaptırmaz, istem önbelleğine dokunmaz."""
        if _is_loaded_by_us(self.url, self.model):
            self._s.post(f"{self.url}/api/generate", timeout=5, json={
                "model": self.model, "keep_alive": OLLAMA_KEEP_ALIVE, "options": {"num_ctx": OLLAMA_NUM_CTX}})

    def _start_ollama(self, status: StatusCb) -> list[str] | None:
        """Kurulu ama kapalı Ollama'yı penceresiz başlatır (ollama_runtime.ensure_running; açılışı en çok ~25 sn
        beklenir) ve yüklü modelleri yeniden okur. Ollama kurulu değilse, adres bu bilgisayarda değilse, portta başka
        bir program varsa ya da Ollama açılmazsa None."""
        status("Ollama başlatılıyor…")
        try:
            from altyazitr import ollama_runtime  # tembel: yalnız Ollama kapalıyken gerekir

            if not ollama_runtime.ensure_running(self.url):
                return None
        except Exception:  # ensure_running hata fırlatmaz; yine de hazırlık bilinen hatayla (TranslatorError) bitsin
            log.warning("Ollama başlatılamadı", exc_info=True)
            return None
        return ollama_list(self.url)

    def _body(self, text: str, context: Context) -> dict:
        limit = predict_limit(text)
        if self.is_tg:  # TranslateGemma yalnız kendi şablonuyla çalışır: bağlam ve profil gitmez
            options = {"temperature": 0.0, "num_predict": limit, "num_ctx": OLLAMA_NUM_CTX}
            messages = [{"role": "user", "content": TG_TEMPLATE.format(text=text)}]
        else:
            options = {"temperature": 0.15, "top_p": 0.9, "num_predict": limit, "num_ctx": OLLAMA_NUM_CTX,
                       "stop": ["\nEN:"]}
            messages = [{"role": "system", "content": self.system},
                        {"role": "user", "content": build_user_prompt(text, context)}]
        body = {"model": self.model, "messages": messages, "stream": False, "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": options}
        if self._think_ok and not self.is_tg:
            body["think"] = False
        return body

    def _request(self, text: str, context: Context, timeout: float | None = None) -> str:
        body = self._body(text, context)
        timeout = self.timeout if timeout is None else timeout
        data = self._post(body, timeout)
        if data.get("done_reason") == "length":
            # Çeviri çıktı sınırına takıldı. Sınır gerçek çevirilerin en az ~2 katıdır (ölçüm: 598 çeviride en çok
            # %43'ü kullanıldı): bu neredeyse her zaman modelin kendini tekrarlamaya girmesidir. Yarım / bozuk satır
            # gösterilmez; tekrar cezasıyla bir kez daha istenir, yine sınıra takılırsa hata (hızlı çeviri gösterilir).
            limit = min(OLLAMA_MAX_PREDICT, 2 * body["options"]["num_predict"])
            log.info("Çeviri çıktı sınırına takıldı; tekrar cezasıyla yeniden isteniyor: %r", text)
            body = {**body, "options": {**body["options"], **OLLAMA_REPEAT_RETRY, "num_predict": limit}}
            data = self._post(body, timeout)
            if data.get("done_reason") == "length":
                raise TranslatorError("Yapay zekâ çevirisi çıktı sınırını aştı (tekrara girdi)")
        _note_loaded(self.url, self.model)  # yanıt geldi: model bellekte (ısınma başarısız olsa da kayda girsin)
        return _clean_llm_output(data.get("message", {}).get("content", ""))

    def _post(self, body: dict, timeout: float) -> dict:
        r = self._s.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
            self._think_ok = False
            body.pop("think")
            r = self._s.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        if r.status_code == 500 and "repeat limit" in r.text.lower():
            log.info("Ollama tekrar sınırına takıldı; satır yeniden isteniyor")
            body = {**body, "options": {**body["options"], **OLLAMA_REPEAT_RETRY}}
            r = self._s.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        if r.status_code != 200:
            raise TranslatorError(f"Ollama hatası ({r.status_code}): {r.text[:200]}")
        return r.json()

    def translate(self, text: str, context: Context = ()) -> str:
        if self.is_tg:
            prot, mapping = self.glossary.protect(text)
            return Glossary.restore(self._request(prot, ()), mapping)
        return self._request(text, self._window.update(context))

    def translate_marked(self, text: str, context: Context = ()) -> str:
        """İşaretli satır modele işaretli gider; işaretler bozuk, eksik ya da fazlaysa vurgusuz düz metin döner.
        TranslateGemma kendi şablonu dışında talimat almaz: vurgusuz çevirir."""
        if self.is_tg:
            return super().translate_marked(text, context)
        return clean_marks(self._request(text, self._window.update(context)), len(unmark(text)[1]))

    def close(self) -> None:
        """Oturumu kapatır. Model bellekten atılmaz: hat yeniden başlayınca (Başlat, ayar değişikliği) hazır
        olsun diye bellekte kalır, yalnız kalma süresi kısalır. Uygulama kapanırken release_ollama_models boşaltır."""
        self._abort.set()
        # Model bellekte değilse (süresi dolmuş, Ollama kapanmış) süre isteği onu boşuna yeniden yükletirdi
        if _is_loaded_by_us(self.url, self.model) and ollama_loaded(self.url, self.model):
            try:  # aynı num_ctx: farklı seçenek modeli yeniden yükletirdi
                self._s.post(f"{self.url}/api/generate", timeout=2, json={
                    "model": self.model, "keep_alive": OLLAMA_IDLE_KEEP_ALIVE,
                    "options": {"num_ctx": OLLAMA_NUM_CTX}})
            except Exception:
                pass
        self._s.close()


def _model_in(model: str, names: Sequence[str]) -> bool:
    m = model.strip()
    cands = {m, m + ":latest"} if ":" not in m else {m}
    return any(n in cands for n in names)


# Bu uygulamanın çeviri için kullandığı (belleğe yüklettiği ya da bellekte bulup kullandığı) (adres, model)
# çiftleri: başka modele geçerken ve uygulama kapanırken boşaltılır. Uygulamanın hiç kullanmadığı modellere
# dokunulmaz.
_loaded_models: set[tuple[str, str]] = set()
_loaded_lock = threading.Lock()
# Şu an istenen model (son hazırlığın): yarıda bırakılan bir yükleme bittiğinde, model artık istenmiyorsa boşaltılır.
_wanted: tuple[str, str] | None = None


def _note_loaded(url: str, model: str) -> None:
    with _loaded_lock:
        _loaded_models.add((ollama_base(url), model.strip()))


def _is_loaded_by_us(url: str, model: str) -> bool:
    with _loaded_lock:
        return (ollama_base(url), model.strip()) in _loaded_models


def _settle_abandoned(url: str, model: str) -> None:
    """Durdurulan hazırlığın yüklemesi bitti. O model artık istenmiyorsa (bu arada başka modele ya da başka motora
    geçildi) hemen boşaltılır; hâlâ isteniyorsa yeni çalıştırma onu kullanır."""
    with _loaded_lock:
        wanted = _wanted
    if wanted != (ollama_base(url), model.strip()):
        release_ollama_models(keep=wanted, want=False)


def ollama_loaded(url: str, model: str) -> bool:
    """Model şu an Ollama'nın belleğinde mi (/api/ps). Ollama'ya ulaşılamazsa False. Bağlanma süresi kısa tutulur:
    Windows'ta kapalı porta bağlanmak ~2 sn sürer, bu denetim Durdur'u bekletmemeli."""
    try:
        with _ollama_session() as s:
            r = s.get(f"{ollama_base(url)}/api/ps", timeout=(1.0, 2.5))
            r.raise_for_status()
            return _model_in(model, [m.get("name", "") for m in r.json().get("models", [])])
    except Exception:
        return False


def release_ollama_models(keep: tuple[str, str] | None = None, *, want: bool = True) -> None:
    """Bu uygulamanın kullandığı modelleri bellekten çıkarır (ekran kartı belleği boşalır). keep: bellekte kalacak
    (adres, model); başka modele geçerken yenisi verilir. Uygulama kapanırken ve yerel yapay zekâdan başka motora
    geçilince keep'siz çağrılır. want: keep artık istenen model olur (yarıda kalan yüklemeler buna göre boşaltılır).
    Hata fırlatmaz."""
    global _wanted
    kept = (ollama_base(keep[0]), keep[1].strip()) if keep else None
    with _loaded_lock:
        if want:
            _wanted = kept
        drop = [m for m in _loaded_models if m != kept]
        _loaded_models.difference_update(drop)
    for url, model in drop:
        try:
            with _ollama_session() as s:
                s.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=3)
            log.info("Ollama modeli bellekten çıkarıldı: %s", model)
        except Exception:
            log.debug("Ollama modeli bellekten çıkarılamadı: %s", model, exc_info=True)


def _ollama_session():
    """Ollama istekleri için oturum. Sistemin vekil sunucusu (proxy: HTTP_PROXY vb.) kullanılmaz: Ollama bu
    bilgisayardadır (ya da yerel ağda) ve vekil sunucu 127.0.0.1 isteklerini yanlış yere götürebilir."""
    import requests

    s = requests.Session()
    s.trust_env = False
    return s


def ollama_list(url: str) -> list[str] | None:
    """Yüklü modellerin adları; Ollama çalışmıyorsa None."""
    try:
        with _ollama_session() as s:
            r = s.get(f"{ollama_base(url)}/api/tags", timeout=2.5)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return None


def ollama_pull(url: str, model: str, progress: Callable[[str, float], None],
                cancel: threading.Event) -> None:
    """Modeli indirir; progress(durum_metni, oran 0..1 veya -1)."""
    with _ollama_session() as s, s.post(f"{ollama_base(url)}/api/pull", json={"model": model, "stream": True},
                                        stream=True, timeout=(5, 600)) as r:
        if r.status_code != 200:
            raise TranslatorError(f"Model indirilemedi ({r.status_code}): {r.text[:200]}")
        for raw in r.iter_lines():
            if cancel.is_set():
                raise TranslatorError("İndirme iptal edildi.")
            if not raw:
                continue
            msg = json.loads(raw)
            if "error" in msg:
                raise TranslatorError(f"Ollama: {msg['error']}")
            total, done = msg.get("total"), msg.get("completed")
            frac = (done / total) if total and done else -1.0
            st = msg.get("status", "")
            if total and done:
                st = f"{st} — {done / 1e9:.2f} / {total / 1e9:.2f} GB"
            progress(st, frac)


# --- DeepL ----------------------------------------------------------------
class DeepLTranslator(Translator):
    key = "deepl"
    label = "DeepL API Free (çevrimiçi, ayda 500 bin karakter ücretsiz)"

    def __init__(self, api_key: str, glossary: Glossary, timeout: float = 6.0):
        import requests

        self.api_key = api_key.strip()
        self.glossary = glossary
        self.timeout = timeout
        host = "api-free.deepl.com" if self.api_key.endswith(":fx") else "api.deepl.com"
        self.endpoint = f"https://{host}/v2/translate"
        self._s = requests.Session()

    def prepare(self, status: StatusCb) -> None:
        if not self.api_key:
            raise TranslatorError("DeepL API anahtarı girilmemiş (Çeviri sekmesi).")
        status("DeepL hazır.")

    def translate(self, text: str, context: Context = ()) -> str:
        prot, mapping = self.glossary.protect(text)
        body = {"text": [prot], "source_lang": "EN", "target_lang": "TR"}
        if context:
            body["context"] = " ".join(en for en, _ in context)
        r = self._s.post(self.endpoint, json=body, timeout=self.timeout,
                         headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"})
        if r.status_code == 403:
            raise TranslatorError("DeepL API anahtarı geçersiz.")
        if r.status_code == 456:
            raise TranslatorError("DeepL aylık ücretsiz kotası doldu.")
        if r.status_code == 429:
            raise TranslatorError("DeepL çok fazla istek (429), biraz bekleyin.")
        r.raise_for_status()
        out = r.json()["translations"][0]["text"]
        return Glossary.restore(out, mapping)

    def close(self) -> None:
        self._s.close()


# --- Google Gemini ------------------------------------------------------------
GEMINI_MODELS = [
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite — önerilen: hızlı, ücretsiz kotası en geniş"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash — daha güçlü ama ücretsiz kotası canlı yayında dakikalar içinde dolar"),
]


class GeminiTranslator(Translator):
    """Google Gemini API (ücretsiz katman, kendi API anahtarınızla). İstem Ollama ile aynıdır (oyun profili,
    bağlam, sözlük). Kota (429) dolunca sunucunun bildirdiği süre kadar bekler; bu sırada hat yerel çeviriye
    düşer."""

    key = "gemini"
    label = "Google Gemini API (çevrimiçi, ücretsiz kotası sınırlı)"

    def __init__(self, api_key: str, model: str, glossary: Glossary, game_context: str = "",
                 timeout: float = 6.0, profile: GameProfile | None = None):
        import requests

        self.api_key = api_key.strip()
        self.model = (model or GEMINI_MODELS[0][0]).strip()
        self.glossary = glossary
        self.game_context = game_context.strip()
        self.timeout = timeout
        self.profile = profile
        self.system = build_system_prompt(profile, glossary, self.game_context)
        self._s = requests.Session()
        self._thinking_ok = True
        self._cool_until = 0.0

    def prepare(self, status: StatusCb) -> None:
        if not self.api_key:
            raise TranslatorError("Gemini API anahtarı girilmemiş (Çeviri sekmesi → Google Gemini).")
        status(f"Gemini hazır ({self.model}).")

    def translate(self, text: str, context: Context = ()) -> str:
        now = time.monotonic()
        if now < self._cool_until:
            raise TranslatorError(f"Gemini kota beklemesi ({self._cool_until - now:.0f} sn)")
        contents = [{"role": "user", "parts": [{"text": build_user_prompt(text, context)}]}]
        gen = {"temperature": 0.15, "maxOutputTokens": 256, "stopSequences": ["\nEN:"]}
        if self._thinking_ok:
            gen["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
        body = {"systemInstruction": {"parts": [{"text": self.system}]}, "contents": contents,
                "generationConfig": gen}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        r = self._s.post(url, json=body, headers=headers, timeout=self.timeout)
        if r.status_code == 400 and "thinking" in r.text.lower() and self._thinking_ok:
            self._thinking_ok = False
            gen.pop("thinkingConfig", None)
            r = self._s.post(url, json=body, headers=headers, timeout=self.timeout)
        if r.status_code == 429:
            delay = 20.0
            try:
                for d in r.json().get("error", {}).get("details", []):
                    rd = d.get("retryDelay")
                    if rd:
                        delay = min(120.0, float(str(rd).rstrip("s")))
                    for v in d.get("violations", []) or []:
                        if "PerDay" in str(v.get("quotaId", "")):
                            delay = 3600.0
            except Exception:
                pass
            self._cool_until = time.monotonic() + delay
            raise TranslatorError("Gemini ücretsiz kota sınırı doldu (429); geçici olarak yerel çeviri kullanılıyor.")
        if r.status_code in (401, 403):
            raise TranslatorError("Gemini API anahtarı geçersiz veya yetkisiz.")
        if r.status_code != 200:
            raise TranslatorError(f"Gemini hatası ({r.status_code}): {r.text[:200]}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as e:
            raise TranslatorError("Gemini boş yanıt döndürdü (içerik filtresi olabilir).") from e
        return _clean_llm_output("".join(p.get("text", "") for p in parts if not p.get("thought")))

    def translate_marked(self, text: str, context: Context = ()) -> str:
        """İstem Ollama ile aynı: işaretler bozuk, eksik ya da fazlaysa vurgusuz düz metin döner."""
        return clean_marks(self.translate(text, context), len(unmark(text)[1]))

    def close(self) -> None:
        self._s.close()


# --- Türkçe son düzeltmeler ----------------------------------------------------
def restore_end_punctuation(src: str, out: str) -> str:
    """Kaynak '?', '!' veya '...' ile bitiyorsa çeviride de aynı bitişi korur
    (NMT bazen '!' veya '?' işaretini '.' yapıyor)."""
    s, o = src.rstrip(), out.rstrip()
    if not s or not o:
        return out
    for end in ("?!", "!?", "...", "?", "!"):
        if s.endswith(end):
            if o.endswith(end) or (end == "..." and o.endswith("…")):
                return o
            if o[-1] in ".!?…":
                o = o.rstrip(".!?…")
            return o + end
    return o


ENGINE_LABELS = {
    "quickmt": QuickMtTranslator.label,
    "ollama": OllamaTranslator.label,
    "gemini": GeminiTranslator.label,
    "deepl": DeepLTranslator.label,
    "none": NoTranslator.label,
}


def build_glossary(cfg) -> Glossary:
    """Kullanıcı sözlüğü + seçili oyun profilinin çevrilmeyecek adları. Yer tutucuyla ad koruyan motorlar
    (quickmt, DeepL, TranslateGemma) profilin adlarını da korusun diye; LLM istemi profili ayrıca kullanır."""
    from altyazitr.game_profiles import get_profile

    pairs = [list(p) for p in cfg.glossary]
    known = {(p[0] or "").strip().casefold() for p in pairs if p}
    for name in get_profile(getattr(cfg, "profile", "")).keep:
        if name.casefold() not in known:
            pairs.append([name, ""])
            known.add(name.casefold())
    return Glossary(pairs)


def create_translator(cfg, glossary: Glossary) -> Translator:
    if cfg.engine in ("ollama", "gemini"):
        from altyazitr.game_profiles import get_profile

        profile = get_profile(getattr(cfg, "profile", ""))
        if cfg.engine == "ollama":
            return OllamaTranslator(cfg.ollama_url, cfg.ollama_model, glossary, timeout=cfg.timeout_s,
                                    profile=profile, context_lines=int(getattr(cfg, "context_lines", 8)))
        return GeminiTranslator(cfg.gemini_key, cfg.gemini_model, glossary, timeout=cfg.timeout_s, profile=profile)
    if cfg.engine == "deepl":
        return DeepLTranslator(cfg.deepl_key, glossary, cfg.timeout_s)
    if cfg.engine == "none":
        return NoTranslator()
    return QuickMtTranslator(glossary, cfg.quickmt_device)
