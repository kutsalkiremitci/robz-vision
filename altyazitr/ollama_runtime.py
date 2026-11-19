"""Ollama'yı uygulamanın içinden yönetmek: yoklama, gizli başlatma, GPU kapısı, model indirme ve hız testi.

* probe, candidate_urls, parse_version: Ollama açık mı, sürümü ne, hangi modeller yüklü; port başka bir
  programda mı (TCP bağlanıyor ama /api/version Ollama'nın yanıtı değil).
* start_hidden, wait_ready, ensure_running: kurulu ama kapalı Ollama'yı penceresiz başlatıp API'yi beklemek.
* inference_compute: %LOCALAPPDATA%\\Ollama\\server.log'daki "inference compute" satırları. Model indirmeden önce
  GPU kapısı: Ollama yalnız işlemciyi (library=cpu) ya da tümleşik kartı (kind=iGPU) görüyorsa model inmez.
* pull: /api/pull akışı; ilerleme katmanların digest'lerine göre toplanır, iptal edilebilir, hatalar Türkçe.
* gpu_share, speed_test: model ekran kartına sığdı mı, satır başına ne kadar sürüyor (uygulamanın gerçek
  çeviri motoruyla).

Qt kullanmaz. Uzun süren işler (wait_ready, ensure_running, pull, speed_test) çağıranın arka plan iş
parçacığında çalışmalıdır. Bu modülün kendi istekleri sistemin vekil sunucusunu (proxy) kullanmaz: Ollama bu
bilgisayardadır ve vekil sunucu 127.0.0.1 isteklerini yanlış yere götürebilir.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import queue
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    import requests

    from altyazitr.ollama_install import Installed

log = logging.getLogger(__name__)

DEFAULT_PORT = 11434
DEFAULT_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
MIN_VERSION = (0, 30, 5)  # bundan eski Ollama güncellenir
PROBE_TIMEOUT = 1.5  # tek yoklama isteğinin zaman aşımı (sn)
POLL_INTERVAL = 0.5  # wait_ready'nin iki yoklaması arası (sn)
PULL_TIMEOUT = (5.0, 600.0)  # /api/pull: bağlanma, satır bekleme (8 GB'lık katmanın özeti doğrulanırken satır gelmez)

# Hız testi kararı (satır başına ortanca süre). OK_MS karma modun 1,2 sn beklemesine sığar: daha yavaşsa satırların
# çoğunda önce hızlı çeviri görünür.
FAST_MS = 600
OK_MS = 1100
SPEED_LINE_TIMEOUT_S = 30.0  # ölçülen satırın zaman aşımı; aşılırsa ölçüm o satırda biter (sonuç "slow")
SPEED_BUDGET_S = 60.0  # ölçülen satırların toplam süresi bunu aşınca kalan satırlar atlanır
# Hız testi için özgün, oyun altyazısı tarzında satırlar (kısa ve uzun karışık)
SPEED_TEST_LINES = (
    "Keep the engine running, I'll be right back.",
    "Where did you get that kind of money?",
    "Cops are everywhere tonight, so we take the long way home.",
    "Don't worry about it. I know a guy.",
    "Hold the wheel steady while I deal with these idiots.",
    "We split it fifty-fifty, just like we agreed.",
    "You think this is a game? People got hurt back there.",
    "Meet me at the docks at midnight and come alone.",
)

# Windows süreç bayrakları (subprocess'te yalnız Windows'ta tanımlı olduklarından burada da yazılı)
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

_clock = time.perf_counter  # testler sahte saatle değiştirir
_START_LOCK = threading.Lock()  # iki iş parçacığı Ollama'yı aynı anda başlatmasın
_children: list[subprocess.Popen] = []  # başlatılan süreçlerin nesneleri: çöp toplanınca uyarı vermesinler

# _check sonuçları
_API = "api"  # Ollama yanıt verdi
_OTHER = "other"  # portta Ollama olmayan bir program var
_WAIT = "wait"  # TCP bağlandı ama yanıt gelmedi: Ollama açılıyor olabilir (ekran kartlarını tararken böyle)
_DOWN = "down"  # bağlantı reddedildi ya da kurulamadı


class OllamaError(RuntimeError):
    """Kullanıcıya gösterilecek Türkçe hata. str(e): 1. satır kısa metin, ayrıntı varsa 2. satır "Ayrıntı: …"
    (ollama_install.SetupError ile aynı biçim; short ve detail de aynı anlamda).

    kind, kurulum servisinin ne yapacağına karar vermesi içindir: "cancel" iptal; "network" ağ ya da Ollama
    bağlantısı (sonra yeniden denenebilir, indirme kaldığı yerden sürer); "disk" yer yok; "outdated" Ollama bu
    model için eski; "not_found" model yok; "start" Ollama açılmadı; "port" port başka programda;
    "" sınıflanmamış."""

    def __init__(self, short: str, kind: str = "", detail: str = "") -> None:
        super().__init__(f"{short}\nAyrıntı: {detail}" if detail else short)
        self.short = short  # arayüzde görünen kısa metin
        self.kind = kind
        self.detail = detail  # ipucu metni ("Ayrıntı: " öneki olmadan); yoksa boş


class PullError(OllamaError):
    """Model indirilemedi (pull)."""


@dataclass
class Probe:
    """Bir adresin yoklaması. api: Ollama yanıt verdi; version: (büyük, küçük, yama), okunamadıysa None;
    other_on_port: portta Ollama olmayan bir program var; models: yüklü modellerin adları;
    installed: bu bilgisayarda kurulu Ollama (ollama_install.find_installed), yoksa None."""

    url: str
    api: bool = False
    version: tuple[int, int, int] | None = None
    other_on_port: bool = False
    models: list[str] = field(default_factory=list)
    installed: Installed | None = None


@dataclass(frozen=True)
class Compute:
    """Ollama'nın açılışta bulduğu hesaplama aygıtı (server.log, msg="inference compute").
    library: "CUDA" | "ROCm" | "Vulkan" | "cpu" (bilinmeyen değer olduğu gibi); name: kartın adı;
    kind: "discrete" | "iGPU" | "" (işlemci ya da eski Ollama yazmıyor); total/available: bayt."""

    library: str
    name: str
    kind: str
    total: int
    available: int


@dataclass(frozen=True)
class Speed:
    """Hız testi sonucu: satır başına ortanca ve %90 süre (ms), modelin ekran kartındaki payı (0..1, bilinmiyorsa
    None)."""

    median_ms: int
    p90_ms: int
    gpu_share: float | None

    @property
    def verdict(self) -> str:
        """Ortanca süreye göre karar: "fast" (≤ 600 ms), "ok" (≤ 1100 ms) ya da "slow"."""
        if self.median_ms <= FAST_MS:
            return "fast"
        if self.median_ms <= OK_MS:
            return "ok"
        return "slow"


# --- sürüm ve adresler --------------------------------------------------------------------------------------
_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Sürüm metnini (büyük, küçük, yama) yapar: "0.40.16", "v0.12.3-rc1", "ollama version is 0.5.7". Yama yoksa
    0; sürüm bulunamazsa None."""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def candidate_urls(configured: str) -> list[str]:
    """Denenecek Ollama adresleri, tekrarsız: önce ayardaki adres, sonra OLLAMA_HOST (önce bu sürecin ortamı, sonra
    HKCU\\Environment kaydı: Robz Vision açıkken tanımlanan değer ortamda görünmez).

    Biçimler normalleştirilir: 0.0.0.0, ::, boş ve localhost → 127.0.0.1 (Ollama varsayılan olarak yalnız
    127.0.0.1'i dinler; Windows'ta "localhost" önce ::1'i dener ve her istekte ~2 sn kaybettirir). Port yoksa
    11434. Tek fark Ollama'nın kendi kuralından gelir: OLLAMA_HOST "http://" ya da "https://" ile yazılıp portsuz
    bırakılırsa Ollama 80/443'te dinler (envconfig.Host), aday da öyle olur. Uzak adresler de döner; yalnız
    127.0.0.1'e istek atmak çağıranın kuralıdır."""
    out: list[str] = []
    sources = [((configured or "").strip() or DEFAULT_URL, False), (os.environ.get("OLLAMA_HOST", ""), True),
               (_user_env("OLLAMA_HOST"), True)]
    for raw, server_env in sources:
        url = _normalize(raw, server_env=server_env)
        if url and url not in out:
            out.append(url)
    return out or [DEFAULT_URL]


def _normalize(value: str, *, server_env: bool) -> str | None:
    """Adres ya da OLLAMA_HOST değerini "şema://ana_makine:port[/yol]" biçimine getirir; anlaşılmazsa None.
    server_env: değer OLLAMA_HOST'tan geliyor (Ollama'nın ayrıştırma kuralı geçerli)."""
    s = (value or "").strip().strip("\"'").strip()
    if not s:
        return None
    scheme, sep, rest = s.partition("://")
    if sep:
        scheme = scheme.lower()
        if scheme not in ("http", "https"):
            return None
        default_port = (80 if scheme == "http" else 443) if server_env else DEFAULT_PORT
    else:
        if server_env and s.lower() == "ollama.com":
            return None  # Ollama bunu bulut adresi sayar; bu bilgisayardaki sunucu değil
        scheme, rest, default_port = "http", s, DEFAULT_PORT
    hostport, _, path = rest.partition("/")
    split = _split_hostport(hostport, default_port)
    if split is None:
        return None
    host, port = split
    path = f"/{path}".rstrip("/") if path else ""
    return f"{scheme}://{_host_text(host)}:{port}{path}"


def _split_hostport(hostport: str, default_port: int) -> tuple[str, int] | None:
    """Go'nun net.SplitHostPort'u ve Ollama'nın yedekleri gibi: "[::1]:5", "::1", "host", "host:", ":5"."""
    hp = hostport.strip()
    host, port_text = hp, None
    if hp.startswith("["):
        end = hp.find("]")
        if end < 0:
            return None
        host, tail = hp[1:end], hp[end + 1:]
        if tail.startswith(":"):
            port_text = tail[1:]
        elif tail:
            return None
    elif hp.count(":") == 1:
        host, port_text = hp.split(":")
    # birden çok ":" köşeli ayraçsız IPv6'dır (ör. ::1): port yok
    try:
        port = int(port_text) if port_text is not None else default_port
    except ValueError:
        port = default_port  # Ollama da geçersiz portta varsayılana döner
    if not 0 < port <= 65535:
        port = default_port
    return _local_host(host), port


def _local_host(host: str) -> str:
    """Her yeri dinleyen (0.0.0.0, ::), boş ve localhost adını 127.0.0.1 yapar; IP'yi kısa biçime getirir."""
    h = host.strip().lower()
    if h in ("", "localhost"):
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return h
    return "127.0.0.1" if ip.is_unspecified else ip.compressed


def _host_text(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _user_env(name: str) -> str:
    """Kullanıcının kalıcı ortam değişkeni (HKCU\\Environment); yoksa ya da okunamazsa boş."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    if not isinstance(value, str):
        return ""
    return winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value


def _clean_url(url: str) -> str:
    """Boşluk ve sondaki "/" atılır, şema yoksa http eklenir; localhost → 127.0.0.1 (bkz. candidate_urls)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return DEFAULT_URL
    if "://" not in u:
        u = f"http://{u}"
    parts = urlsplit(u)
    if (parts.hostname or "").lower() == "localhost":
        try:
            port = parts.port
        except ValueError:
            return u
        u = urlunsplit(parts._replace(netloc="127.0.0.1" + (f":{port}" if port else "")))
    return u


def _host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return parts.hostname or "127.0.0.1", port


def _is_local(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --- yoklama ------------------------------------------------------------------------------------------------
def _session() -> requests.Session:
    import requests

    s = requests.Session()
    s.trust_env = False  # sistem vekil sunucusu 127.0.0.1 isteklerini başka yere götürmesin
    return s


def _json(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _causes(exc: BaseException) -> Iterator[BaseException]:
    """Hatanın kendisi, nedenleri ve sarmaladıkları (__cause__, __context__, urllib3'ün reason'ı, args)."""
    seen: set[int] = set()
    todo: list[Any] = [exc]
    while todo:
        e = todo.pop()
        if not isinstance(e, BaseException) or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        todo += [e.__cause__, e.__context__, getattr(e, "reason", None), *e.args]


def _refused(exc: BaseException) -> bool:
    """Bağlantı hiç kurulamadı mı (reddedildi ya da adres yok)?"""
    return any(isinstance(e, ConnectionRefusedError) or type(e).__name__ == "NewConnectionError"
               for e in _causes(exc))


def _check(s: requests.Session, url: str, timeout: float) -> tuple[str, tuple[int, int, int] | None]:
    """Adresin durumu (_API, _OTHER, _WAIT, _DOWN) ve Ollama'nın sürümü.

    Önce yalnız TCP bağlantısı denenir: bağlanamıyorsa portta kimse yoktur. Bağlanıp /api/version'dan Ollama'nın
    yanıtı gelmezse portta başka bir program vardır; yanıt hiç gelmezse (zaman aşımı) Ollama açılıyor olabilir."""
    import requests

    host, port = _host_port(url)
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except OSError:
        return _DOWN, None
    try:
        r = s.get(f"{url}/api/version", timeout=timeout)
    except requests.Timeout:
        return _WAIT, None
    except requests.RequestException as e:  # bağlandı ama bağlantı yanıtsız kapandı ya da HTTP değil
        return (_DOWN if _refused(e) else _OTHER), None
    data = _json(r) if r.status_code == 200 else None
    if isinstance(data, dict) and isinstance(data.get("version"), str):
        return _API, parse_version(data["version"])
    try:  # /api/version'dan önceki çok eski Ollama: kök adres "Ollama is running" der
        root = s.get(f"{url}/", timeout=timeout)
        if root.status_code == 200 and "ollama is running" in root.text.lower():
            return _API, None
    except requests.RequestException:
        pass
    return _OTHER, None


def _models(s: requests.Session, url: str, timeout: float) -> list[str]:
    import requests

    try:
        r = s.get(f"{url}/api/tags", timeout=timeout)
    except requests.RequestException:
        return []
    data = _json(r) if r.status_code == 200 else None
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    for m in data.get("models") or []:
        if isinstance(m, dict) and (m.get("name") or m.get("model")):
            names.append(str(m.get("name") or m.get("model")))
    return names


def _find_installed() -> Installed | None:
    """Kurulu Ollama (ollama_install.find_installed, çalışma anında içe aktarılır); yoksa ya da aranamazsa None."""
    try:
        from altyazitr.ollama_install import find_installed
    except ImportError:
        log.debug("altyazitr.ollama_install yok; kurulu Ollama aranmadı")
        return None
    try:
        return find_installed()
    except Exception:
        log.warning("Kurulu Ollama aranırken hata", exc_info=True)
        return None


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> Probe:
    """Adresteki Ollama'yı yoklar. Hata fırlatmaz; açık Ollama'da birkaç ms sürer, kapalı portta en çok timeout sn
    (Windows reddedilen bağlantıyı ~2 sn'de bildirir).

    Üç durum: api=True (sürüm ve modeller dolu), other_on_port=True (TCP bağlanıyor ama yanıt Ollama'nın değil)
    ya da ikisi de False (bağlantı reddedildi, kimse dinlemiyor; ya da Ollama açılıyor ve henüz yanıt vermedi)."""
    url = _clean_url(url)
    s = _session()
    try:
        state, version = _check(s, url, timeout)
        models = _models(s, url, max(timeout, 3.0)) if state == _API else []
    finally:
        s.close()
    return Probe(url=url, api=state == _API, version=version, other_on_port=state == _OTHER, models=models,
                 installed=_find_installed())


# --- başlatma -----------------------------------------------------------------------------------------------
def start_hidden(app_exe: Path) -> None:
    """Ollama'yı penceresiz başlatır: "ollama app.exe" hidden --fast-startup. Beklemez (hazır olmasını wait_ready
    bekler). Dosya yoksa ya da başlatılamazsa OSError."""
    args = [str(app_exe), "hidden", "--fast-startup"]
    if sys.platform == "win32":
        proc = subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW, close_fds=True)
    else:  # pragma: no cover - uygulama yalnız Windows'ta çalışır
        proc = subprocess.Popen(args, start_new_session=True, close_fds=True)
    _children.append(proc)


def _sleep(seconds: float, cancel: threading.Event | None) -> bool:
    """seconds kadar bekler; iptal edildiyse True."""
    if cancel is None:
        time.sleep(seconds)
        return False
    return cancel.wait(seconds)


def wait_ready(url: str, timeout: float, cancel: threading.Event | None = None) -> tuple[int, int, int]:
    """/api/version yanıt verene kadar yoklar; Ollama'nın sürümünü döner (okunamazsa (0, 0, 0)).

    En çok timeout sn bekler. Olmazsa OllamaError: "Ollama başlatılamadı." (kind="start") ya da son yoklamada
    portta başka bir program varsa "… portunu başka bir program kullanıyor." (kind="port"). cancel kurulursa
    OllamaError("İptal edildi.", kind="cancel")."""
    url = _clean_url(url)
    deadline = time.monotonic() + max(0.0, timeout)
    last = _DOWN
    s = _session()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise OllamaError("İptal edildi.", kind="cancel")
            per_try = max(0.2, min(PROBE_TIMEOUT, deadline - time.monotonic()))
            last, version = _check(s, url, per_try)
            if last == _API:
                return version or (0, 0, 0)
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if _sleep(min(POLL_INTERVAL, left), cancel):
                raise OllamaError("İptal edildi.", kind="cancel")
    finally:
        s.close()
    port = _host_port(url)[1]
    if last == _OTHER:
        raise OllamaError(f"{port} portunu başka bir program kullanıyor.", kind="port",
                          detail=f"{url}/api/version Ollama'nın yanıtı değil.")
    raise OllamaError("Ollama başlatılamadı.", kind="start",
                      detail=f"{port} portu {timeout:g} sn içinde yanıt vermedi. Günlük: {server_log_path()}")


def ensure_running(url: str, timeout: float = 25.0) -> bool:
    """Ollama'nın API'si açık mı; değilse ve bu bilgisayarda kuruluysa gizlice başlatıp en çok timeout sn bekler.

    Ollama zaten açılıyorsa (bağlanıyor, henüz yanıt yok) yalnız bekler. Portta başka bir program varsa, adres bu
    bilgisayarda değilse ya da Ollama kurulu değilse başlatmaz. Hata fırlatmaz; API hazırsa True."""
    url = _clean_url(url)
    deadline = time.monotonic() + max(0.0, timeout)
    with _START_LOCK:
        s = _session()
        try:
            state, _ = _check(s, url, PROBE_TIMEOUT)
        finally:
            s.close()
        if state == _API:
            return True
        if state == _OTHER:
            log.warning("Ollama başlatılmadı: %s adresindeki port başka bir programda", url)
            return False
        if state == _DOWN:
            if not _is_local(url):
                return False
            installed = _find_installed()
            if installed is None:
                log.info("Ollama kurulu değil; başlatılamadı")
                return False
            try:
                start_hidden(installed.app_exe)
            except OSError as e:
                log.warning("Ollama başlatılamadı (%s): %s", installed.app_exe, e)
                return False
            log.info("Ollama gizlice başlatıldı: %s", installed.app_exe)
        try:
            wait_ready(url, max(0.0, deadline - time.monotonic()))
        except OllamaError as e:
            log.warning("Ollama hazır olmadı: %s", e)
            return False
        return True


# --- server.log: ekran kartı kapısı --------------------------------------------------------------------------
_COMPUTE_MARK = 'msg="inference compute"'
# Her sunucu açılışının ilk satırları; "inference compute" satırları bunlardan sonra gelir
_BOOT_MARKS = ('msg="server config"', 'msg="Listening on ')
_KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]i?B|B)\s*$", re.I)
_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
          "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}
_LIBRARIES = {"cuda": "CUDA", "rocm": "ROCm", "vulkan": "Vulkan", "cpu": "cpu", "metal": "Metal",
              "oneapi": "oneAPI"}


def server_log_path() -> Path:
    """Ollama sunucusunun günlüğü: %LOCALAPPDATA%\\Ollama\\server.log (her açılışta yenisi başlar)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Ollama" / "server.log"


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return str(json.loads(value))  # Go'nun strconv.Quote kaçışları çoğunlukla JSON ile aynı
        except ValueError:
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _parse_size(text: str) -> int | None:
    """Boyut metnini bayta çevirir: "12.0 GiB", "457.0 MiB", "0 B" (Ollama'nın HumanBytes2'si ikili birim yazar)."""
    m = _SIZE_RE.match(text)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).lower()])


def _parse_compute(line: str) -> tuple[tuple[str, str, str], Compute] | None:
    rest = line[line.index(_COMPUTE_MARK) + len(_COMPUTE_MARK):]
    kv = {k: _unquote(v) for k, v in _KV_RE.findall(rest)}
    library = kv.get("library", "").strip()
    total = _parse_size(kv.get("total", ""))
    if not library or total is None:
        return None
    # Ollama 0.12+: name=CUDA0 description="NVIDIA GeForce RTX 4070"; daha eskisi: name="NVIDIA GeForce …"
    name = (kv.get("description") or kv.get("name") or "").strip()
    kind = kv.get("type", "").strip()
    kind = {"igpu": "iGPU", "discrete": "discrete"}.get(kind.lower(), kind)
    comp = Compute(library=_LIBRARIES.get(library.lower(), library), name=name, kind=kind, total=total,
                   available=_parse_size(kv.get("available", "")) or 0)
    return (library.lower(), kv.get("id", ""), name), comp


def inference_compute(log: Path) -> list[Compute]:
    """server.log'daki SON sunucu açılışının "inference compute" satırları (Ollama'nın kullanacağı aygıtlar).

    Ekran kartı yoksa ya da kullanılamıyorsa Ollama tek bir library=cpu satırı yazar; tümleşik kart açıksa
    kind="iGPU" olur. GPU kapısı: liste boş değilse ve hepsi cpu ya da iGPU ise model indirilmez. Dosya
    okunamazsa ya da biçim tanınmazsa boş liste döner (kapı sessizce atlanır); hata fırlatmaz."""
    found: dict[tuple[str, str, str], Compute] = {}
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if _COMPUTE_MARK in line:
                    item = _parse_compute(line)
                    if item is not None:
                        found[item[0]] = item[1]
                elif any(mark in line for mark in _BOOT_MARKS):
                    found = {}  # yeni açılış: öncekinin aygıtları geçersiz
    except (OSError, ValueError):  # parametre adı "log" modülün log'unu gölgeliyor
        logging.getLogger(__name__).debug("Ollama günlüğü okunamadı: %s", log, exc_info=True)
        return []
    return list(found.values())


# --- model indirme ------------------------------------------------------------------------------------------
_OUTDATED_RE = re.compile(r"\b412\b|newer version|requires ollama version", re.I)
_DISK_RE = re.compile(r"no space left|not enough space|disk (?:is )?full|insufficient (?:disk|storage)|"
                      r"yeterli (?:boş )?alan|disk dolu", re.I)
_DRIVE_RE = re.compile(r"\b([A-Za-z]):[\\/]")
_NOT_FOUND_RE = re.compile(r"file does not exist|manifest unknown", re.I)
_NET_RE = re.compile(r"dial tcp|no such host|timeout|timed out|connection (?:refused|reset|aborted)|forcibly closed|"
                     r"network is unreachable|context deadline exceeded|max retries exceeded|\beof\b|wsarecv|"
                     r"wsasend|proxyconnect|name resolution", re.I)
_STATUS_TR = (("pulling manifest", "Hazırlanıyor"), ("pulling", "İndiriliyor"),
              ("verifying", "Doğrulanıyor"), ("writing manifest", "Kaydediliyor"),
              ("removing", "Eski parçalar siliniyor"), ("success", "Tamamlandı"))


def _status_tr(status: str) -> str:
    s = status.strip().lower()
    for prefix, text in _STATUS_TR:
        if s.startswith(prefix):
            return text
    return status


def _detail(text: str) -> str:
    return " ".join(str(text).split())[:300]


def _pull_error(text: str, model: str, status: int | None = None) -> PullError:
    """Ollama'nın hata metnini Türkçe PullError'a çevirir."""
    detail = _detail(text)
    if status == 412 or _OUTDATED_RE.search(text):
        return PullError("Ollama'nın sürümü bu model için eski.", kind="outdated", detail=detail)
    if _DISK_RE.search(text):
        m = _DRIVE_RE.search(text)
        where = f" ({m.group(1).upper()}:)" if m else ""
        return PullError(f"Diskte yer kalmadı{where}. Yer açıp Tekrar dene'ye basın.", kind="disk", detail=detail)
    if _NOT_FOUND_RE.search(text):
        return PullError(f"'{model}' modeli bulunamadı.", kind="not_found", detail=detail)
    if _NET_RE.search(text):
        return PullError("İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer.", kind="network",
                         detail=detail)
    return PullError("Model indirilemedi.", detail=detail)


def _hang_up(r: requests.Response) -> None:
    """Bağlantıyı yarıda bırakır: soket kapatılmaz, yalnız FIN gönderilir (shutdown). Ollama'nın Go sunucusu bunu
    görünce isteği iptal eder (indirme durur, yarım parçalar kalır, sonraki pull oradan sürer) ve bağlantıyı
    kapatır; okuyan iş parçacığı da böylece çıkar. Windows'ta shutdown bekleyen recv'i kendisi uyandırmaz,
    close() ise okuyanın tamponunu kilitlediği için burada çağrılmaz."""
    sock = _response_socket(r)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:  # bu arada kapanmış olabilir
            pass


def _response_socket(r: requests.Response) -> socket.socket | None:
    """Akışın soket nesnesi (dosya numarası değil: kapanmışsa yeniden kullanılan bir numaraya dokunulmasın).
    Bağlantı açık kalacaksa urllib3 bağlantısında; sunucu "Connection: close" dediyse http.client soketi yanıtın
    dosya nesnesine devreder."""
    raw = getattr(r, "raw", None)
    sock = getattr(getattr(raw, "connection", None), "sock", None)
    if sock is None:
        fp = getattr(getattr(raw, "_fp", None), "fp", None)  # http.client.HTTPResponse.fp → BufferedReader
        sock = getattr(getattr(fp, "raw", None), "_sock", None)  # → socket.SocketIO
    return sock if isinstance(sock, socket.socket) else None


_END = object()  # akış bitti


def _pump(r: requests.Response, q: queue.SimpleQueue) -> None:
    """Akışı okur (ayrı iş parçacığında): her JSON satırını kuyruğa koyar, sonda _END ya da okuma hatası."""
    try:
        for raw in r.iter_lines():
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                q.put(msg)
        q.put(_END)
    except Exception as e:
        q.put(e)
    finally:
        try:
            r.close()
        except Exception:
            pass


def _error_text(r: requests.Response) -> str:
    data = _json(r)
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return f"HTTP {r.status_code}: {r.text[:200]}"


def pull(url: str, model: str, progress: Callable[[int, int, str], None], cancel: threading.Event) -> None:
    """Modeli Ollama'ya indirtir (/api/pull). Ollama yarım kalan indirmeyi kendisi sürdürür.

    progress(yapılan_bayt, toplam_bayt, durum): katmanlar digest'e göre toplanır (toplam = görülen katmanların
    total'ı, yapılan = completed'ları). Ollama katmanları sırayla bildirdiği için toplam yeni katman göründükçe
    büyür. durum Türkçedir: "Hazırlanıyor", "İndiriliyor", "Doğrulanıyor", "Kaydediliyor", "Tamamlandı".
    cancel kurulunca en geç ~0,1 sn'de PullError("İptal edildi.") fırlar ve bağlantı yarıda bırakılır (Ollama
    indirmeyi durdurur). Hatalar PullError'dır (kind: cancel, network, disk, outdated, not_found ya da "")."""
    import requests

    url = _clean_url(url)
    if cancel.is_set():
        raise PullError("İptal edildi.", kind="cancel")
    s = _session()
    try:
        try:
            r = s.post(f"{url}/api/pull", json={"model": model, "stream": True}, stream=True, timeout=PULL_TIMEOUT)
        except requests.RequestException as e:
            detail = _detail(f"{type(e).__name__}: {e}")
            raise PullError("Ollama'ya bağlanılamadı.", kind="network", detail=detail) from e
        if r.status_code != 200:
            with r:
                raise _pull_error(_error_text(r), model, r.status_code)
        # Okuma ayrı iş parçacığında: bekleyen okuma Windows'ta dışarıdan uyandırılamıyor, iptal ise hemen etki
        # etmeli (çeviri başlayınca indirme duraklar).
        q: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=_pump, args=(r, q), daemon=True, name="ollama-pull").start()
        finished = False
        try:
            _read_pull(q, model, progress, cancel)
            finished = True
        finally:
            if not finished:  # iptal, Ollama'nın hata satırı ya da progress'in hatası: akış hâlâ açık olabilir
                _hang_up(r)
    finally:
        s.close()


def _read_pull(q: queue.SimpleQueue, model: str, progress: Callable[[int, int, str], None],
               cancel: threading.Event) -> None:
    totals: dict[str, int] = {}
    completed: dict[str, int] = {}
    success = False
    while True:
        if cancel.is_set():
            raise PullError("İptal edildi.", kind="cancel")
        try:
            msg = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if msg is _END:
            break
        if isinstance(msg, BaseException):  # okuma hatası: Ollama kapandı ya da bağlantı koptu
            raise PullError("Ollama'yla bağlantı koptu.", kind="network",
                            detail=_detail(f"{type(msg).__name__}: {msg}")) from msg
        if msg.get("error"):
            raise _pull_error(str(msg["error"]), model)
        digest = msg.get("digest")
        total = msg.get("total")
        if digest and isinstance(total, int) and total > 0:
            totals[digest] = total
            done = msg.get("completed")
            if isinstance(done, int):
                completed[digest] = max(0, min(done, total))
        status = str(msg.get("status") or "")
        progress(sum(completed.values()), sum(totals.values()), _status_tr(status))
        if status == "success":
            success = True
    if cancel.is_set():
        raise PullError("İptal edildi.", kind="cancel")
    if not success:
        raise PullError("Ollama'yla bağlantı koptu.", kind="network",
                        detail="Ollama indirmeyi bitirmeden bağlantıyı kapattı.")


# --- ekran kartı payı ve hız testi ----------------------------------------------------------------------------
def _model_key(name: str) -> str:
    n = (name or "").strip().lower()
    return n if ":" in n.rsplit("/", 1)[-1] else f"{n}:latest"


def gpu_share(url: str, model: str) -> float | None:
    """Belleğe yüklü modelin ekran kartındaki payı (/api/ps: size_vram / size, 0..1). 1,0 = tamamen ekran kartında.
    Model yüklü değilse ya da Ollama yanıt vermezse None."""
    import requests

    url = _clean_url(url)
    s = _session()
    try:
        r = s.get(f"{url}/api/ps", timeout=max(PROBE_TIMEOUT, 3.0))
        data = _json(r) if r.status_code == 200 else None
    except requests.RequestException:
        return None
    finally:
        s.close()
    if not isinstance(data, dict):
        return None
    want = _model_key(model)
    for m in data.get("models") or []:
        if not isinstance(m, dict) or want not in (_model_key(str(m.get("name") or "")),
                                                    _model_key(str(m.get("model") or ""))):
            continue
        size, vram = m.get("size"), m.get("size_vram")
        if isinstance(size, (int, float)) and size > 0 and isinstance(vram, (int, float)):
            return max(0.0, min(1.0, vram / size))
        return None
    return None


def _percentile(values: Sequence[float], p: float) -> float:
    """Doğrusal aradeğerlemeli yüzdelik (numpy'nin varsayılanı; tools/ceviri_testi.py ile aynı)."""
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def speed_test(url: str, model: str, profile_id: str, lines: Sequence[str]) -> Speed:
    """Uygulamanın gerçek çeviri motoruyla (OllamaTranslator: oyun profili, sözlük, bağlam) satır başına süreyi
    ölçer. Önce motorun kendi hazırlığı çalışır (model yüklü mü, belleğe yükleme, ısınma; sayılmaz), sonra
    satırlar işlem hattındaki gibi önceki satırlar bağlam olarak giderek sırayla çevrilir. Bitince model
    bellekten boşaltılır (yayında ekran kartını tutmasın).

    Satır SPEED_LINE_TIMEOUT_S'yi aşarsa ölçüm orada biter ve o satır geçen süreyle sayılır; ölçülen süre
    SPEED_BUDGET_S'yi aşınca kalan satırlar atlanır. Hazırlık hataları (Ollama yok, model yok) TranslatorError
    olarak fırlar. Karar için Speed.verdict."""
    import requests

    from altyazitr.config import TranslateCfg
    from altyazitr.translate import build_glossary, create_translator

    texts = [t.strip() for t in lines if t and t.strip()]
    if not texts:
        raise ValueError("speed_test: ölçülecek satır yok")
    url = _clean_url(url)
    cfg = TranslateCfg(engine="ollama", ollama_url=url, ollama_model=model, profile=profile_id,
                       timeout_s=SPEED_LINE_TIMEOUT_S)
    tr = create_translator(cfg, build_glossary(cfg))
    times: list[float] = []
    try:
        tr.prepare(lambda _msg: None)
        hist: deque[tuple[str, str]] = deque(maxlen=max(1, cfg.context_lines))
        started = _clock()
        for text in texts:
            t0 = _clock()
            try:
                out = tr.translate(text, list(hist))
            except requests.Timeout:
                times.append((_clock() - t0) * 1000)
                log.warning("Hız testi: satır %.0f sn'de çevrilemedi; ölçüm bitti", SPEED_LINE_TIMEOUT_S)
                break
            times.append((_clock() - t0) * 1000)
            hist.append((text, out))
            if _clock() - started > SPEED_BUDGET_S:
                break
        share = gpu_share(url, model)
    finally:
        tr.close()  # model bellekte kalır (hat yeniden başlayınca hazır olsun); ölçümden sonra boşaltılır:
        try:
            with _session() as s:
                s.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=3)
        except requests.RequestException:
            log.debug("Hız testi: model bellekten çıkarılamadı", exc_info=True)
    speed = Speed(median_ms=round(statistics.median(times)), p90_ms=round(_percentile(times, 90)), gpu_share=share)
    log.info("Hız testi %s: ortanca %d ms, %%90 %d ms, ekran kartı payı %s (%d satır)", model, speed.median_ms,
             speed.p90_ms, share, len(times))
    return speed
