"""Çeviri motorları (İngilizce → Türkçe).

* QuickMtTranslator — quickmt/quickmt-en-tr (CTranslate2, CC-BY-4.0). Yerel, ücretsiz,
  CPU'da ~20-60 ms. Varsayılan motor; ilk kullanımda ~400 MB model indirilir.
* OllamaTranslator — Ollama üzerinden yerel LLM (TranslateGemma, Gemma, Qwen, Hy-MT...).
  En iyi kalite; önceki satırları bağlam olarak kullanır, sözlüğe uyar.
* DeepLTranslator — DeepL API Free (ayda 500.000 karakter ücretsiz, kendi anahtarınızla).
* NoTranslator — çeviri yapmaz (yalnızca OCR testi için).
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
from pathlib import Path

from rapidfuzz import fuzz, process

from altyazitr.paths import MODEL_DIR
from altyazitr.textproc import split_sentences

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

    def protect(self, text: str, keep_only: bool = True) -> tuple[str, dict[str, str]]:
        """Terimleri yer tutucuyla değiştirir. keep_only=True iken yalnızca *çevrilmeyecek*
        terimler (ad, mekan) korunur: çevirisi olan sıradan kelimeleri yer tutucuya çevirmek
        NMT'nin dilbilgisini bozuyor (testte 'cop→polis' cümleyi bozdu)."""
        mapping: dict[str, str] = {}
        for i, ((en, tr), pat) in enumerate(zip(self.entries, self._pats)):
            if keep_only and tr and tr.casefold() != en.casefold():
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

    def get(self, key: str) -> str | None:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
            if len(key) < 12:
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
        if not getattr(self, "_dirty", False):
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
    is_fast = False  # hibrit modda "hızlı ön çeviri" olarak kullanılabilir mi

    def prepare(self, status: StatusCb) -> None:
        """Modeli indirir/yükler. Uzun sürebilir; çeviri iş parçacığında çağrılır."""

    def translate(self, text: str, context: Context = ()) -> str:  # pragma: no cover - arayüz
        raise NotImplementedError

    def close(self) -> None:
        pass


class NoTranslator(Translator):
    key = "none"
    label = "Çeviri yok (yalnızca İngilizce göster)"
    is_fast = True

    def translate(self, text: str, context: Context = ()) -> str:
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


def quickmt_downloaded(path=None) -> bool:
    """Gerekli tüm dosyalar var ve model.bin tam boyutlu mu? path verilmezse QUICKMT_DIR."""
    d = Path(path) if path is not None else QUICKMT_DIR
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


def download_quickmt(status: StatusCb, *, target_dir=None, progress: ProgressCb | None = None) -> None:
    """quickmt modelini indirir (varsayılan: QUICKMT_DIR). status: Türkçe durum metni,
    progress: 0..1 oran (bitene kadar en fazla 0,99). Hata → TranslatorError."""
    target = Path(target_dir) if target_dir is not None else QUICKMT_DIR
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
    if not quickmt_downloaded(target):
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
    label = "Yerel hızlı çeviri — quickmt (ücretsiz, önerilen)"
    is_fast = True

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
        assert self._tr is not None and self._src is not None and self._tgt is not None
        text = _sentence_case_if_shouting(text)
        text, mapping = self.glossary.protect(text)
        parts = split_sentences(text) or [text]
        batch = [self._src.encode(p, out_type=str) + ["</s>"] for p in parts]
        res = self._tr.translate_batch(batch, beam_size=2, max_decoding_length=256, repetition_penalty=1.05)
        out = " ".join(self._tgt.decode(r.hypotheses[0]) for r in res)
        return Glossary.restore(out, mapping).strip()

    def close(self) -> None:
        self._tr = None


# --- Ollama ---------------------------------------------------------------
TG_TEMPLATE = (
    "You are a professional English (en) to Turkish (tr) translator. Your goal is to accurately "
    "convey the meaning and nuances of the original English text while adhering to Turkish grammar, "
    "vocabulary, and cultural sensitivities.\n"
    "Produce only the Turkish translation, without any additional explanations or commentary. "
    "Please translate the following English text into Turkish:\n\n\n{text}"
)

LLM_SYSTEM = (
    "You are a professional video game localizer. Translate English game subtitles into natural, "
    "fluent Turkish for a Turkish live-stream audience.\n"
    "Rules:\n"
    "- Translate ONLY the last user message. Earlier messages are previous subtitle lines, given only as context.\n"
    "- Keep the tone: casual dialogue stays casual (use 'sen'), keep slang, jokes and swearing natural in Turkish.\n"
    "- Do not translate names of people, places, gangs, brands or vehicles.\n"
    "- If a glossary term appears, use the glossary translation exactly.\n"
    "- Output ONLY the Turkish translation: no quotes, no notes, no explanations, no English.\n"
    "- Keep it short and easy to read as a subtitle."
)

OLLAMA_PRESETS = [
    ("translategemma:4b", "TranslateGemma 4B — hızlı, ~3.3 GB (önerilen başlangıç)"),
    ("translategemma:12b", "TranslateGemma 12B — en iyi kalite, ~8.1 GB VRAM"),
    ("gemma4:12b", "Gemma 4 12B — bağlam + sözlük desteği güçlü"),
    ("qwen3.5:9b", "Qwen 3.5 9B — genel amaçlı, hızlı"),
    ("kaelri/hy-mt2:7b-q4_K_M", "Tencent Hy-MT2 7B — çeviriye özel (Apache-2.0)"),
]


def _clean_llm_output(s: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    lines = [ln.strip() for ln in s.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    lines = lines[:2]
    out = " ".join(lines)
    out = re.sub(r"^(türkçe|turkish|çeviri|translation)\s*:\s*", "", out, flags=re.I)
    return out.strip().strip('"“”«»').strip()


class OllamaTranslator(Translator):
    key = "ollama"
    label = "Ollama yerel LLM (en iyi kalite, ücretsiz)"

    def __init__(self, url: str, model: str, glossary: Glossary, game_context: str = "",
                 timeout: float = 6.0):
        import requests

        self.url = url.rstrip("/")
        self.model = model.strip()
        self.glossary = glossary
        self.game_context = game_context.strip()
        self.timeout = timeout
        self._s = requests.Session()
        self._think_ok = True
        self.is_tg = self.model.lower().startswith("translategemma")

    def _system(self) -> str:
        s = LLM_SYSTEM
        if self.game_context:
            s += f"\nGame / scene context: {self.game_context}"
        if self.glossary:
            s += "\nGlossary:\n" + self.glossary.prompt_lines()
        return s

    def prepare(self, status: StatusCb) -> None:
        status(f"Ollama'ya bağlanılıyor ({self.url})…")
        names = ollama_list(self.url)
        if names is None:
            raise TranslatorError(
                "Ollama'ya ulaşılamadı. Ollama'yı https://ollama.com adresinden kurup çalıştırın "
                "veya çeviri motorunu 'Yerel hızlı çeviri' olarak değiştirin.")
        if not _model_in(self.model, names):
            raise TranslatorError(
                f"'{self.model}' modeli Ollama'da yüklü değil. Çeviri sekmesindeki "
                "'Modeli indir' düğmesiyle indirebilirsiniz.")
        status(f"{self.model} belleğe yükleniyor (ilk seferde biraz sürebilir)…")
        try:
            self._s.post(f"{self.url}/api/generate", json={"model": self.model, "prompt": "",
                                                            "keep_alive": "30m"}, timeout=180)
        except Exception as e:
            log.warning("Ollama ön yükleme başarısız: %s", e)

    def translate(self, text: str, context: Context = ()) -> str:
        options = {"temperature": 0.0 if self.is_tg else 0.2, "num_predict": 200, "num_ctx": 2048}
        if self.is_tg:
            prot, mapping = self.glossary.protect(text)
            messages = [{"role": "user", "content": TG_TEMPLATE.format(text=prot)}]
        else:
            mapping = {}
            messages = [{"role": "system", "content": self._system()}]
            for en, tr in context:
                messages += [{"role": "user", "content": en}, {"role": "assistant", "content": tr}]
            messages.append({"role": "user", "content": text})
        body = {"model": self.model, "messages": messages, "stream": False, "keep_alive": "30m",
                "options": options}
        if self._think_ok and not self.is_tg:
            body["think"] = False
        r = self._s.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
            self._think_ok = False
            body.pop("think")
            r = self._s.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        if r.status_code != 200:
            raise TranslatorError(f"Ollama hatası ({r.status_code}): {r.text[:200]}")
        out = _clean_llm_output(r.json().get("message", {}).get("content", ""))
        return Glossary.restore(out, mapping) if mapping else out

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


def ollama_list(url: str) -> list[str] | None:
    """Yüklü modellerin adları; Ollama çalışmıyorsa None."""
    import requests

    try:
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2.5)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return None


def ollama_pull(url: str, model: str, progress: Callable[[str, float], None],
                cancel: threading.Event) -> None:
    """Modeli indirir; progress(durum_metni, oran 0..1 veya -1)."""
    import requests

    with requests.post(f"{url.rstrip('/')}/api/pull", json={"model": model, "stream": True},
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
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite — hızlı, çeviri için önerilen"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash — daha güçlü, daha düşük ücretsiz limit"),
]


class GeminiTranslator(Translator):
    """Google Gemini API (ücretsiz katman, kendi API anahtarınızla). Kota (429) dolunca
    sunucunun bildirdiği süre kadar bekler; bu sırada hat yerel çeviriye düşer."""

    key = "gemini"
    label = "Google Gemini API (çevrimiçi, ücretsiz katman)"

    def __init__(self, api_key: str, model: str, glossary: Glossary, game_context: str = "",
                 timeout: float = 6.0):
        import requests

        self.api_key = api_key.strip()
        self.model = (model or GEMINI_MODELS[0][0]).strip()
        self.glossary = glossary
        self.game_context = game_context.strip()
        self.timeout = timeout
        self._s = requests.Session()
        self._thinking_ok = True
        self._cool_until = 0.0

    def _system(self) -> str:
        s = LLM_SYSTEM
        if self.game_context:
            s += f"\nGame / scene context: {self.game_context}"
        if self.glossary:
            s += "\nGlossary:\n" + self.glossary.prompt_lines()
        return s

    def prepare(self, status: StatusCb) -> None:
        if not self.api_key:
            raise TranslatorError("Gemini API anahtarı girilmemiş (Çeviri sekmesi → Google Gemini).")
        status(f"Gemini hazır ({self.model}).")

    def translate(self, text: str, context: Context = ()) -> str:
        now = time.monotonic()
        if now < self._cool_until:
            raise TranslatorError(f"Gemini kota beklemesi ({self._cool_until - now:.0f} sn)")
        contents = []
        for en, tr in context:
            contents += [{"role": "user", "parts": [{"text": en}]}, {"role": "model", "parts": [{"text": tr}]}]
        contents.append({"role": "user", "parts": [{"text": text}]})
        gen = {"temperature": 0.2, "maxOutputTokens": 256}
        if self._thinking_ok:
            gen["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
        body = {"systemInstruction": {"parts": [{"text": self._system()}]}, "contents": contents,
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


def create_translator(cfg, glossary: Glossary) -> Translator:
    if cfg.engine == "ollama":
        return OllamaTranslator(cfg.ollama_url, cfg.ollama_model, glossary, cfg.game_context, cfg.timeout_s)
    if cfg.engine == "gemini":
        return GeminiTranslator(cfg.gemini_key, cfg.gemini_model, glossary, cfg.game_context, cfg.timeout_s)
    if cfg.engine == "deepl":
        return DeepLTranslator(cfg.deepl_key, glossary, cfg.timeout_s)
    if cfg.engine == "none":
        return NoTranslator()
    return QuickMtTranslator(glossary, cfg.quickmt_device)


def measure(fn: Callable[[], str]) -> tuple[str, float]:
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000
