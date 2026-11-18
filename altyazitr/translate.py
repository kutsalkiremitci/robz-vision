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
OLLAMA_KEEP_ALIVE = "60m"  # uygulama açıkken model bellekte kalsın; kapanınca close() boşaltır
# Isınma cümleleri: ekran kartı farklı uzunluktaki istemler için çekirdekleri ilk kez derlerken birkaç saniye
# takılıyor; bunu yayın başlamadan, hazırlıkta yaşarız.
WARMUP_LINES = ("Hey.", "Get in the car, we're late.",
                "Hold L2 to talk to the clerk. We need two pistols and a lot of ammo before tonight.")


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
                 timeout: float = 6.0, profile: GameProfile | None = None):
        self.url = url.rstrip("/")
        self.model = model.strip()
        self.glossary = glossary
        self.game_context = game_context.strip()
        self.timeout = timeout
        self.profile = profile
        self._s = _ollama_session()
        self._think_ok = True
        self.is_tg = self.model.lower().startswith("translategemma")
        self.system = build_system_prompt(profile, glossary, self.game_context)

    def prepare(self, status: StatusCb) -> None:
        """Ollama açık mı ve model yüklü mü; kurulu ama kapalı Ollama'yı başlatır, sonra modeli belleğe yükleyip
        ısıtır. Ollama'ya ulaşılamazsa ya da model yoksa TranslatorError (1. satır kısa ve ne yapılacağını söyler):
        karma modda hat hızlı çeviriyle sürer, kullanıcıya pencere açmayan bildirim gider (Pipeline.notice)."""
        status(f"Ollama'ya bağlanılıyor ({self.url})…")
        names = ollama_list(self.url)
        if names is None:  # kurulu ama kapalı olabilir (Windows açılışında başlamadı ya da kapatıldı)
            names = self._start_ollama(status)
        if names is None:
            raise TranslatorError(f"{TEXT_OLLAMA_DOWN}\nAyrıntı: {self.url} yanıt vermiyor.")
        if not _model_in(self.model, names):
            raise TranslatorError(missing_model_text(self.model))
        status(f"{self.model} belleğe yükleniyor ve ısınıyor (ilk seferde biraz sürebilir)…")
        ctx: list[tuple[str, str]] = []
        for i, line in enumerate(WARMUP_LINES):
            try:
                out = self._request(line, ctx, timeout=180.0 if i == 0 else 60.0)
                ctx.append((line, out))
            except Exception as e:  # ısınma başarısız olsa da çeviri denensin; hata orada görünür
                log.warning("Ollama ısınması başarısız: %s", e)
                break

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
        if self.is_tg:  # TranslateGemma yalnız kendi şablonuyla çalışır: bağlam ve profil gitmez
            options = {"temperature": 0.0, "num_predict": 160, "num_ctx": OLLAMA_NUM_CTX}
            messages = [{"role": "user", "content": TG_TEMPLATE.format(text=text)}]
        else:
            options = {"temperature": 0.15, "top_p": 0.9, "num_predict": 160, "num_ctx": OLLAMA_NUM_CTX,
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
        r = self._s.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
            self._think_ok = False
            body.pop("think")
            r = self._s.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        if r.status_code != 200:
            raise TranslatorError(f"Ollama hatası ({r.status_code}): {r.text[:200]}")
        return _clean_llm_output(r.json().get("message", {}).get("content", ""))

    def translate(self, text: str, context: Context = ()) -> str:
        if self.is_tg:
            prot, mapping = self.glossary.protect(text)
            return Glossary.restore(self._request(prot, ()), mapping)
        return self._request(text, context)

    def translate_marked(self, text: str, context: Context = ()) -> str:
        """İşaretli satır modele işaretli gider; işaretler bozuk, eksik ya da fazlaysa vurgusuz düz metin döner.
        TranslateGemma kendi şablonu dışında talimat almaz: vurgusuz çevirir."""
        if self.is_tg:
            return super().translate_marked(text, context)
        return clean_marks(self._request(text, context), len(unmark(text)[1]))

    def close(self) -> None:
        # VRAM'i boşalt
        try:
            self._s.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=2)
        except Exception:
            pass
        self._s.close()


def _model_in(model: str, names: Sequence[str]) -> bool:
    m = model.strip()
    cands = {m, m + ":latest"} if ":" not in m else {m}
    return any(n in cands for n in names)


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
            r = s.get(f"{url.rstrip('/')}/api/tags", timeout=2.5)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return None


def ollama_pull(url: str, model: str, progress: Callable[[str, float], None],
                cancel: threading.Event) -> None:
    """Modeli indirir; progress(durum_metni, oran 0..1 veya -1)."""
    with _ollama_session() as s, s.post(f"{url.rstrip('/')}/api/pull", json={"model": model, "stream": True},
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
            return OllamaTranslator(cfg.ollama_url, cfg.ollama_model, glossary, cfg.game_context, cfg.timeout_s,
                                    profile=profile)
        return GeminiTranslator(cfg.gemini_key, cfg.gemini_model, glossary, cfg.game_context, cfg.timeout_s,
                                profile=profile)
    if cfg.engine == "deepl":
        return DeepLTranslator(cfg.deepl_key, glossary, cfg.timeout_s)
    if cfg.engine == "none":
        return NoTranslator()
    return QuickMtTranslator(glossary, cfg.quickmt_device)
