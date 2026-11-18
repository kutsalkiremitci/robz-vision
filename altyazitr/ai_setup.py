"""Yerel yapay zekâ kurulumu: Ollama + Gemma 4, tek arka plan işiyle.

Plan.build, Ollama'nın ve modelin durumuna bakıp yalnız gereken adımları seçer; indirme ve disk ihtiyacını ve
kurulumu engelleyen durumu (port dolu, Windows eski, disk yetmez) hesaplar. AiSetup bu planı tek bir QThread'de
(_Runner) sırayla yürütür:

    DOWNLOAD → INSTALL → START → CHECK (GPU kapısı) → PULL → [TEST: hız testi, Faz 2; varsayılan planda yok]

* Her adım tekrarlanabilir: başlarken ön koşuluna yeniden bakar (Ollama kurulu ve yeterince yeni mi, açık mı, model
  var mı). Duraklatmadan sürdürme, "Tekrar dene" ve uygulamanın sonraki açılışında yarım işin sürmesi aynı yoldan
  geçer; son ikisi planı da arka planda yeniden çıkarır (Ollama kapalıyken yoklama ~1,5 sn sürer, arayüz donmasın).
* Çeviri (işlem hattı) çalışırken iş duraklar (settings.ai.pause_while_running): süren istek kesilir, duraklama
  bitince aynı adım yeniden başlar. İndirme .part + Range ile, model çekme Ollama'nın kendi sürdürmesiyle kaldığı
  yerden sürer. "Ollama kuruluyor" adımı kesilmez: duraklatma ve iptal o adım bitince işler. force_resume()
  duraklatmayı yok sayar ("Yine de sürdür").
* Dış işlevler (hardware, ollama_install, ollama_runtime) `deps` nesnesinden çağrılır. Varsayılanı (Deps) modülleri
  ilk kullanımda içe aktarır; testler aynı adlı yöntemleri olan sahte bir nesne verir. Her çağrı ayrı bir yardımcı
  iş parçacığında yapılır ve aynı anda en çok bir çağrı çalışır. İş parçacığı iptalde ve kapanışta bu çağrıyı
  beklemeden döner; yarıda kalan kurulum süreci öldürülmez.
* Hatalar modüllerin Türkçe metniyle gelir (ilk satır kısa, ikincisi "Ayrıntı: …"); türleri (kind) arayüzün
  "Tekrar dene" ve "Elle kur" yollarını belirler (can_retry, manual_install).
* Kalıcı durum settings.ai'dedir: başlarken pending_model, hata olunca failed ve türü (failed_kind) yazılır; sonraki
  açılışta "Tekrar dene" / "Elle kur" bu türden hesaplanır. resume_pending() uygulama açılışında hatasız yarım işi
  sürdürür. Kullanıcının iptali (ya da hatadan sonra vazgeçmesi) pending_model'i ve indirilmiş Ollama kurulum
  dosyasını (yarım .part dahil) siler; duraklatma ve kapanış silmez (iş kaldığı yerden sürer). Ayarlar yalnız arayüz
  iş parçacığında değişir ve kaydedilir. Servis 127.0.0.1 dışına Ollama isteği atmaz.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, Signal, Slot

from altyazitr.game_profiles import DEFAULT_PROFILE_ID
from altyazitr.paths import LOG_DIR

if TYPE_CHECKING:
    from altyazitr.config import Settings
    from altyazitr.hardware import Advice
    from altyazitr.ollama_runtime import Probe, Speed

log = logging.getLogger(__name__)

GB = 1_000_000_000  # Ollama ve tasarım boyutları ondalık GB'tır
GIB = 1 << 30  # ekran kartı belleği ikili GiB'tır

# Kurulum dosyası ve sürümler: ollama_install.SETUP_BYTES / OLLAMA_VERSION ve ollama_runtime.MIN_VERSION ile aynı
# olmalı (plan, o modüller yüklenmeden de hesaplanabilsin diye burada da duruyorlar).
SETUP_BYTES = 1_857_663_944  # OllamaSetup.exe 0.40.16
OLLAMA_VERSION = (0, 40, 16)  # uygulamanın kurduğu sabit sürüm
MIN_OLLAMA_VERSION = (0, 30, 5)  # bundan eski Ollama yerinde güncellenir
OLLAMA_DISK_BYTES = 4_100_000_000  # kurulu Ollama (CUDA/ROCm/Vulkan kitaplıklarıyla), yaklaşık
DISK_MARGIN = GB  # ihtiyaç olan her sürücüde güvenlik payı
MIN_WINDOWS_BUILD = 19045  # Windows 10 22H2
DEFAULT_URL = "http://127.0.0.1:11434"
SETUP_LOG = LOG_DIR / "ollama-kurulum.log"

# Ağ hatasında model çekme bu aralıklarla yeniden denenir, sonra "Tekrar dene" (kurulum dosyasının indirmesi aynı
# beklemelerle kendi içinde yeniden dener: ollama_install.download_setup).
RETRY_DELAYS = (5.0, 15.0, 45.0)
EMIT_EVERY = 0.2  # ilerleme bildirimi en çok saniyede ~5 kez
ETA_WINDOW = 10.0  # kalan süre son ~10 sn'nin ortalama hızından
SAMPLE_EVERY = 0.25  # hız ölçümü için örnek aralığı
MIN_SPAN = 1.0  # bundan kısa ölçümle kalan süre söylenmez
SPEED_TEST = False  # hız testi adımı (Faz 2) varsayılan planda yok

LIGHT_MODEL = "gemma4:e4b"
RECOMMENDED_MODEL = "gemma4:12b"

# --- kullanıcıya gösterilen metinler (ilk satır kısa; ayrıntı sonraki satırda) ---------------------------------
TEXT_PREPARING = "Hazırlanıyor…"
TEXT_VERIFYING = "İmza ve bütünlük doğrulanıyor"  # INSTALL adımının ilk alt adımı (kurulum dosyası denetlenir)
TEXT_INSTALL_TIME = " (1–2 dk)"  # "Ollama kuruluyor" / "Ollama güncelleniyor" satırının sonu
TEXT_CANCELLING = "İptal ediliyor…"
TEXT_CANCELLED = "İptal edildi."
TEXT_PAUSED = "Duraklatıldı: çeviri çalışıyor"
TEXT_READY = "Yapay zekâ çevirisi hazır"
TEXT_READY_LATER = "Yapay zekâ hazır; durdurup başlatınca devreye girer"
TEXT_SLOW = "Yapay zekâ bu bilgisayarda yavaş"
TEXT_SLOW_TIP = "Satır başına ~{sec} sn{reason}. Hızlı çeviride kalabilir ya da hafif modeli kurabilirsiniz."
TEXT_MODEL_READY = "{model} indirildi."
TEXT_FAILED = "Yapay zekâ kurulamadı."
TEXT_NETWORK = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer."
TEXT_DISK_FULL = "Diskte yer kalmadı ({drive}). Yer açıp Tekrar dene'ye basın."
TEXT_DISK_LOW = "{drive} sürücüsünde yer yetmiyor: {free} GB boş, ~{need} GB gerekli"
TEXT_WINDOWS_OLD = "Yapay zekâ çevirisi için Windows 10 22H2 ya da daha yeni bir Windows gerekir."
TEXT_PORT = "{port} portunu başka bir program kullanıyor."
TEXT_OLD = "Ollama'nın sürümü bu model için eski."
TEXT_OLD_UNMANAGED = "Ollama'nın sürümü eski ({version}). Ollama'yı güncelleyip yeniden deneyin."
TEXT_START_FAILED = "Ollama başlatılamadı."
TEXT_INSTALL_FAILED = "Ollama kurulamadı."
TEXT_NO_GPU = "Ollama ekran kartınızı kullanamıyor."
TEXT_NO_GPU_TIP = "Model indirilmedi. Ekran kartı sürücüsünü güncelleyip yeniden deneyin."
TEXT_LIGHTER = "Ekran kartının belleği {vram} GB: daha hafif {model} kurulacak."

# Hata türleri (kind): ollama_install.SetupError (network, disk, digest, signature, blocked, install, download),
# ollama_runtime.OllamaError / PullError (network, disk, outdated, not_found, start, port) ve bu modülün kendi
# türleri (windows, port, disk, outdated, start, install, no_gpu). "cancel" hata sayılmaz. Arayüz, türe göre
# "Tekrar dene" (yeniden planlayıp başlatır) ve "Elle kur" (ollama.com/download) yollarını sunar.
NO_RETRY_KINDS = frozenset({"signature", "not_found", "windows"})  # yeniden denemek düzeltmez
MANUAL_KINDS = frozenset({"signature", "blocked", "install", "download", "outdated"})  # Ollama'yı elle kurmak çözer


class Step(Enum):
    """Kurulum adımları; planda bu sırayla yer alırlar."""

    DOWNLOAD = "download"  # Ollama kurulum dosyasını indir (.part + Range)
    INSTALL = "install"  # doğrula (boyut, SHA256, imza) ve sessizce kur; kesilmez, iptal edilmez
    START = "start"  # Ollama'yı başlat, hazır olmasını bekle
    CHECK = "check"  # GPU kapısı: Ollama ekran kartını kullanabiliyor mu, bellek azsa hafif model
    PULL = "pull"  # modeli çek (Ollama kaldığı yerden sürdürür)
    TEST = "test"  # hız testi (Faz 2)


STEP_TITLES = {
    Step.DOWNLOAD: "Ollama indiriliyor",
    Step.INSTALL: "Ollama kuruluyor",
    Step.START: "Ollama başlatılıyor",
    Step.CHECK: "Ekran kartı denetleniyor",
    Step.PULL: "{model} indiriliyor",
    Step.TEST: "Hız testi",
}


@dataclass(frozen=True)
class ModelInfo:
    title: str
    size: int  # indirme ve disk (bayt, Ollama'nın gösterdiği boyut)
    min_vram: int = 0  # ekran kartının belleği bundan azsa (bayt) yerine `light` kurulur; 0: sınır yok
    light: str = ""


MODEL_INFO: dict[str, ModelInfo] = {
    "gemma4:12b": ModelInfo("Gemma 4 12B", 8_000_000_000, int(10.5 * GIB), LIGHT_MODEL),
    "gemma4:e4b": ModelInfo("Gemma 4 E4B", 6_600_000_000),
    "gemma4:26b": ModelInfo("Gemma 4 26B", 17_000_000_000),
}


# --- küçük yardımcılar ----------------------------------------------------------------------------------------
def model_title(model: str) -> str:
    """Modelin okunur adı ("Gemma 4 12B"); tabloda yoksa adın kendisi."""
    info = MODEL_INFO.get(model.strip())
    return info.title if info else model.strip()


def model_size(model: str) -> int:
    """Modelin indirme boyutu (bayt); bilinmiyorsa 0."""
    info = MODEL_INFO.get(model.strip())
    return info.size if info else 0


def has_model(model: str, names: Iterable[str]) -> bool:
    """Model Ollama'nın listesinde mi? Etiketsiz ad ":latest" ile de eşleşir."""
    m = model.strip()
    wanted = {m} if ":" in m else {m, f"{m}:latest"}
    return any(n in wanted for n in names)


def model_for_vram(model: str, vram: int) -> str:
    """Ekran kartının belleğine (bayt) sığan model: sığmıyorsa MODEL_INFO'daki hafif seçeneğe iner."""
    seen: set[str] = set()
    while model not in seen:
        seen.add(model)
        info = MODEL_INFO.get(model)
        if info is None or not info.light or vram >= info.min_vram:
            break
        model = info.light
    return model


def step_title(step: Step, model: str = "", *, update: bool = False) -> str:
    """İlerleme listesindeki satır adı ("Gemma 4 12B indiriliyor")."""
    if step is Step.INSTALL and update:
        return "Ollama güncelleniyor"
    return STEP_TITLES[step].format(model=model_title(model) or "Model")


_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """"0.40.16", "v0.12.3-rc1" → (büyük, küçük, yama); yama yoksa 0 (ollama_runtime.parse_version gibi)."""
    m = _VERSION_RE.search(text or "")
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def windows_build() -> int | None:
    """Windows derleme numarası (Windows dışında None). Deps.windows_build daha doğrusunu okur (RtlGetVersion)."""
    getver = getattr(sys, "getwindowsversion", None)
    return getver().build if getver is not None else None


def local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def ollama_models_dir() -> Path:
    """Ollama'nın modelleri koyduğu klasör (OLLAMA_MODELS tanımlıysa o)."""
    env = os.environ.get("OLLAMA_MODELS", "").strip()
    return Path(os.path.expandvars(env)) if env else Path.home() / ".ollama" / "models"


def ollama_server_log() -> Path:
    return local_appdata() / "Ollama" / "server.log"


def _split_url(url: str):
    url = url.strip()
    return urlsplit(url if "://" in url else f"http://{url}")


def is_local_url(url: str) -> bool:
    try:
        host = (_split_url(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def local_url(url: str) -> str:
    """Servisin kullanacağı adres: ayardaki adres bu bilgisayardaysa onun portuyla 127.0.0.1, değilse varsayılan."""
    if not is_local_url(url):
        return DEFAULT_URL
    try:
        port = _split_url(url).port or 11434
    except ValueError:
        return DEFAULT_URL
    return f"http://127.0.0.1:{port}"


def url_port(url: str) -> int:
    try:
        return _split_url(url).port or 11434
    except ValueError:
        return 11434


def ollama_status(probe: Probe) -> tuple[bool, tuple[int, int, int] | None]:
    """(Ollama bu bilgisayarda var mı, sürümü). Sürüm açıksa API'den, kapalıysa kayıttaki DisplayVersion'dan."""
    if probe.api:
        return True, (tuple(probe.version) if probe.version else None)  # type: ignore[return-value]
    installed = getattr(probe, "installed", None)
    if installed is not None:
        return True, parse_version(getattr(installed, "version", ""))
    return False, None


def _gb(n: int) -> str:
    """Ondalık GB, Türkçe yazımla: 9 → "9", 1,86 → "1,9", 14,96 → "15"."""
    x = n / GB
    s = (f"{x:.0f}" if x >= 10 else f"{x:.1f}").replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def _first_line(text: str) -> str:
    text = text.strip()
    return text.splitlines()[0] if text else ""


def _existing(path: Path) -> Path:
    """Yolun var olan en yakın üst klasörü (boş yer ölçümü için; sürücü yoksa kökü)."""
    p = path
    try:
        while not p.exists() and p.parent != p:
            p = p.parent
    except OSError:
        return Path(path.anchor or path)
    return p


def _drive(path: Path) -> str:
    return (path.drive or path.anchor or str(path)).upper()


# --- plan --------------------------------------------------------------------------------------------------------
@dataclass
class Plan:
    """Yapılacak iş: yalnız gereken adımlar, indirme ve disk ihtiyacı, varsa engel.

    blocker doluysa kurulum başlatılamaz (arayüzde "Kur" kapalı); blocker_kind türüdür (windows, port, outdated,
    disk). disk_path / disk_bytes en çok yer gereken sürücüdür (geçici kurulum dosyası ve güvenlik payı dahil);
    model klasörü ile %LOCALAPPDATA% farklı sürücülerdeyse engel ikisine de bakar. apply=False: yalnız model
    indirilir, ayarlar değişmez, GPU kapısı yoktur (Çeviri sayfasındaki "Modeli indir")."""

    model: str
    steps: list[Step]
    download_bytes: int
    disk_bytes: int
    disk_path: Path
    blocker: str = ""
    apply: bool = True
    url: str = DEFAULT_URL  # kullanılacak yerel Ollama adresi
    disk_free: int | None = None  # disk_path sürücüsündeki boş yer (bilinmiyorsa None)
    ollama_version: str = ""  # bulunan Ollama'nın sürümü ("0.12.3"); yoksa ""
    blocker_kind: str = ""

    @property
    def updating(self) -> bool:
        """Var olan Ollama yerinde güncellenecek mi?"""
        return Step.INSTALL in self.steps and bool(self.ollama_version)

    @classmethod
    def build(cls, probe: Probe, model: str, apply: bool = True, *,
              free_space: Callable[[Path], int | None] | None = None, win_build: int | None = None,
              test: bool = SPEED_TEST, update: bool = False, models_dir: Path | None = None,
              app_dir: Path | None = None) -> Plan:
        """probe: ollama_runtime.probe sonucu (url, api, version, other_on_port, models, installed).
        free_space verilmezse disk denetlenmez; win_build verilmezse bu bilgisayarınki okunur (0: bilinmiyor).
        update: Ollama sürümü yetse de resmî kurulumu uygulamanın sürümüne güncelle (model "sürüm eski" dediyse)."""
        model = model.strip()
        present, version = ollama_status(probe)
        installed = getattr(probe, "installed", None)
        new_enough = version is None or version >= MIN_OLLAMA_VERSION
        need_install = not present or not new_enough
        if update and installed is not None and (version is None or version < OLLAMA_VERSION):
            need_install = True
        need_pull = not (probe.api and has_model(model, probe.models or ()))
        need_check = apply and need_pull
        steps: list[Step] = []
        if need_install:
            steps += [Step.DOWNLOAD, Step.INSTALL]
        if (need_check or need_pull or test) and (need_install or not probe.api):
            steps.append(Step.START)
        if need_check:
            steps.append(Step.CHECK)
        if need_pull:
            steps.append(Step.PULL)
        if test:
            steps.append(Step.TEST)
        if version is not None:
            ollama_version = ".".join(map(str, version))
        else:
            ollama_version = str(getattr(installed, "version", "") or "") if present else ""

        download = (SETUP_BYTES if need_install else 0) + (model_size(model) if need_pull else 0)
        models_dir = models_dir or ollama_models_dir()
        app_dir = app_dir or local_appdata()
        need: dict[str, list[Any]] = {}  # sürücü → [yol, bayt]

        def add(path: Path, n: int) -> None:
            if n > 0:
                need.setdefault(_drive(path), [path, 0])[1] += n

        if need_pull:
            add(models_dir, model_size(model))
        if need_install:  # geçici kurulum dosyası + (yeni kurulumsa) kurulu Ollama
            add(app_dir, SETUP_BYTES + (0 if present else OLLAMA_DISK_BYTES))
        for slot in need.values():
            slot[1] += DISK_MARGIN

        disk_path, disk_bytes = models_dir, 0
        if need:
            disk_path, disk_bytes = max(need.values(), key=lambda slot: slot[1])
        disk_free: int | None = None
        disk_blocker = ""
        if free_space is not None:
            disk_free = free_space(_existing(disk_path))
            for drive, (path, n) in need.items():
                free = disk_free if path is disk_path else free_space(_existing(path))
                if free is not None and free < n and not disk_blocker:
                    disk_blocker = TEXT_DISK_LOW.format(drive=drive, free=_gb(free), need=_gb(n))

        if win_build is None:
            win_build = windows_build()
        blocker, kind = "", ""
        if win_build and win_build < MIN_WINDOWS_BUILD:  # 0 / None: bilinmiyor, engel sayılmaz
            blocker, kind = TEXT_WINDOWS_OLD, "windows"
        elif probe.other_on_port:
            blocker, kind = TEXT_PORT.format(port=url_port(probe.url)), "port"
        elif present and not new_enough and installed is None:
            # Docker, WSL ya da zip: resmî kurulumla güncellenemez (ikinci bir Ollama aynı portu ister)
            blocker, kind = TEXT_OLD_UNMANAGED.format(version=ollama_version), "outdated"
        elif disk_blocker:
            blocker, kind = disk_blocker, "disk"
        return cls(model=model, steps=steps, download_bytes=download, disk_bytes=disk_bytes, disk_path=disk_path,
                   blocker=blocker, apply=apply, url=probe.url, disk_free=disk_free, ollama_version=ollama_version,
                   blocker_kind=kind)


# --- hatalar -------------------------------------------------------------------------------------------------------
class AiSetupError(RuntimeError):
    """Kullanıcıya gösterilecek hata: ilk satır kısa Türkçe, sonraki satırlar ayrıntı. kind: hata türü
    (bkz. NO_RETRY_KINDS, MANUAL_KINDS)."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind


class _Aborted(Exception):
    """İş iptal edildi ya da uygulama kapanıyor."""


def _exc_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)


def is_network_error(exc: BaseException) -> bool:
    """Bağlantı / zaman aşımı hatası mı? Modüllerin hatasında türüne (kind="network") bakılır."""
    from altyazitr.translate import is_network_error as net

    if getattr(exc, "kind", "") == "network":
        return True
    return net(exc) or any(str(e).startswith("İnternet bağlantısı yok") for e in _exc_chain(exc))


def is_disk_full(exc: BaseException) -> bool:
    if getattr(exc, "kind", "") == "disk":
        return True
    for e in _exc_chain(exc):
        if isinstance(e, OSError) and (e.errno == 28 or getattr(e, "winerror", None) in (39, 112)):
            return True
        if "no space left" in str(e).lower():
            return True
    return False


def _is_user_error(exc: BaseException) -> bool:
    """Bizim modüllerimizin Türkçe iletili hatası mı (SetupError, OllamaError, AiSetupError, TranslatorError)?"""
    cls = type(exc)
    return isinstance(exc, RuntimeError) and cls is not RuntimeError and cls.__module__.startswith("altyazitr")


def error_info(exc: BaseException, drive: str = "") -> tuple[str, str]:
    """(kullanıcı metni, tür). Modüllerin Türkçe hatası metni ve türüyle olduğu gibi geçer; ötekiler sınıflanır."""
    if _is_user_error(exc) and str(exc).strip():
        return str(exc).strip(), str(getattr(exc, "kind", "") or "")
    detail = f"{type(exc).__name__}: {exc}".strip()
    if is_disk_full(exc):
        return f"{TEXT_DISK_FULL.format(drive=drive or 'C:')}\nAyrıntı: {detail}", "disk"
    if is_network_error(exc):
        return f"{TEXT_NETWORK}\nAyrıntı: {detail}", "network"
    return f"{TEXT_FAILED}\nAyrıntı: {detail}", ""


def error_text(exc: BaseException, drive: str = "") -> str:
    """Hatanın kullanıcı metni: ilk satır kısa Türkçe, ikinci satır ayrıntı."""
    return error_info(exc, drive)[0]


# --- dış işlevler ----------------------------------------------------------------------------------------------
class Deps:
    """Servisin dış işlevleri. Gerçek modüller (hardware, ollama_install, ollama_runtime) ilk kullanımda içe aktarılır;
    servis oluşturulurken hiçbiri yüklenmez. Testler aynı adlı yöntemleri olan sahte bir nesne verir.

    Zorunlu: download_setup, verify_setup, run_setup, find_installed, mark_installed_by_us, probe, ensure_running,
    inference_compute, pull, free_space, advise, list_gpus. İsteğe bağlı (nesnede yoksa atlanır): candidate_urls,
    system_ram, windows_build, speed_test, discard_download."""

    # ollama_install
    def download_setup(self, progress: Callable[[int, int], None], cancel: threading.Event) -> Path:
        from altyazitr import ollama_install

        return ollama_install.download_setup(progress, cancel)

    def discard_download(self) -> None:
        from altyazitr import ollama_install

        ollama_install.discard_download()

    def verify_setup(self, path: Path) -> None:
        from altyazitr import ollama_install

        ollama_install.verify_setup(path)

    def run_setup(self, path: Path, log_file: Path) -> None:
        from altyazitr import ollama_install

        ollama_install.run_setup(path, log_file)

    def find_installed(self) -> Any:
        from altyazitr import ollama_install

        return ollama_install.find_installed()

    def mark_installed_by_us(self) -> None:
        from altyazitr import ollama_install

        ollama_install.mark_installed_by_us()

    # ollama_runtime
    def probe(self, url: str) -> Any:
        from altyazitr import ollama_runtime

        return ollama_runtime.probe(url)

    def candidate_urls(self, configured: str) -> list[str]:
        from altyazitr import ollama_runtime

        return ollama_runtime.candidate_urls(configured)

    def ensure_running(self, url: str, timeout: float = 25.0) -> bool:
        from altyazitr import ollama_runtime

        return ollama_runtime.ensure_running(url, timeout=timeout)

    def inference_compute(self, log_file: Path) -> list[Any]:
        from altyazitr import ollama_runtime

        return ollama_runtime.inference_compute(log_file)

    def pull(self, url: str, model: str, progress: Callable[[int, int, str], None], cancel: threading.Event) -> None:
        from altyazitr import ollama_runtime

        ollama_runtime.pull(url, model, progress, cancel)

    def speed_test(self, url: str, model: str, profile_id: str, lines: Sequence[str] | None = None) -> Any:
        from altyazitr import ollama_runtime

        return ollama_runtime.speed_test(url, model, profile_id, lines or ollama_runtime.SPEED_TEST_LINES)

    # hardware
    def free_space(self, path: Path) -> int | None:
        from altyazitr import hardware

        return hardware.free_space(path)

    def list_gpus(self) -> list[Any]:
        from altyazitr import hardware

        return hardware.list_gpus()

    def advise(self, gpus: Any, *, ram: int | None, win_build: int) -> Any:
        from altyazitr import hardware

        return hardware.advise(gpus, ram=ram, win_build=win_build)

    def system_ram(self) -> int | None:
        from altyazitr import hardware

        return hardware.system_ram()

    def windows_build(self) -> int:
        from altyazitr import hardware

        return hardware.windows_build()  # 0: bilinmiyor


# --- ilerleme ----------------------------------------------------------------------------------------------------
class _Progress:
    """Planın indirme baytları: süren adımın ve bütün işin ilerlemesi, son ~10 sn'nin ortalama hızıyla kalan süre ve
    bildirim sınırı (saniyede en çok ~5; adım bitince her zaman). Yalnız bayt adımları (DOWNLOAD, PULL) sayılır.

    Model çekmede Ollama'nın bildirdiği toplam katmanlar göründükçe büyür (önce ~0,1 GB, sonra 7,4 GB): adımın
    boyutu max(bildirilen, beklenen) alınır, çubuk geri düşmez."""

    def __init__(self, sizes: dict[Step, int], clock: Callable[[], float]) -> None:
        self.expected = dict(sizes)  # tablodaki boyutlar (MODEL_INFO, SETUP_BYTES)
        self.sizes = dict(sizes)  # henüz bitmemiş bayt adımlarının boyutu
        self.finished = 0  # biten bayt adımlarının toplamı
        self.clock = clock
        self.step: Step | None = None
        self.done = 0
        self.total = 0
        self.samples: deque[tuple[float, int]] = deque()
        self.last_emit = -math.inf

    @property
    def all_done(self) -> int:
        return self.finished + (self.done if self.step in self.sizes else 0)

    @property
    def all_total(self) -> int:
        return self.finished + sum(self.sizes.values())

    def begin(self, step: Step) -> None:
        self.step, self.done, self.total = step, 0, self.sizes.get(step, 0)
        self.last_emit = -math.inf

    def set_size(self, step: Step, size: int) -> None:
        """Beklenen boyut değişti (GPU kapısı hafif modele geçti)."""
        if step in self.sizes:
            self.expected[step] = self.sizes[step] = size
            if step is self.step:
                self.total = size

    def skip(self, step: Step) -> None:
        """Adım gerekmedi (ön koşulu zaten sağlanmış): baytları toplamdan çıkar."""
        self.sizes.pop(step, None)
        if step is self.step:
            self.done = self.total = 0

    def finish(self, step: Step) -> None:
        if step in self.sizes:
            self.finished += max(self.sizes.pop(step), self.done if step is self.step else 0)
        self.step, self.done, self.total = None, 0, 0

    def pause(self) -> None:
        """Duraklama: eski hız ölçümü sürdürmede kullanılmaz."""
        self.samples.clear()

    def update(self, done: int, total: int) -> tuple[int, int, int, int, int | None] | None:
        """İndirme ilerledi; bildirilecekse anlık görüntüyü döndürür."""
        now = self.clock()
        step = self.step
        if total > 0:
            self.total = max(total, self.expected.get(step, 0))  # type: ignore[arg-type]
            if step in self.sizes:
                self.sizes[step] = self.total  # type: ignore[index]
        self.done = max(0, done)
        cur = self.all_done
        if self.samples and cur < self.samples[-1][1]:
            self.samples.clear()  # istek baştan başladı
        if not self.samples or now - self.samples[-1][0] >= SAMPLE_EVERY:
            self.samples.append((now, cur))
            while len(self.samples) > 2 and now - self.samples[1][0] >= ETA_WINDOW:
                self.samples.popleft()
        complete = total > 0 and self.done >= total
        if not complete and now - self.last_emit < EMIT_EVERY:
            return None
        self.last_emit = now
        return self.snapshot()

    def eta(self) -> int | None:
        total = self.all_total
        if total <= 0 or len(self.samples) < 2:
            return None
        (t0, d0), (t1, d1) = self.samples[0], self.samples[-1]
        if t1 - t0 < MIN_SPAN or d1 <= d0:
            return None
        rate = (d1 - d0) / (t1 - t0)
        return math.ceil(max(0, total - self.all_done) / rate)

    def snapshot(self) -> tuple[int, int, int, int, int | None]:
        return self.done, self.total, self.all_done, self.all_total, self.eta()


# --- iş ve arka plan iş parçacığı --------------------------------------------------------------------------------
@dataclass(eq=False)
class _Job:
    """Bir kurulum işinin denetim durumu. Alanlar AiSetup._cv kilidi altında okunup yazılır."""

    plan: Plan | None  # None: plan arka planda çıkarılacak (resume_pending, retry)
    model: str
    apply: bool
    update: bool = False  # planı çıkarırken Ollama'yı güncellemeye zorla (bkz. Plan.build)
    abort: str = ""  # "" | "cancel" | "shutdown"
    forced: bool = False  # "Yine de sürdür": çeviri çalışsa da duraklama
    step: Step | None = None  # süren ya da başlamayı bekleyen adım
    in_step: bool = False  # adımın gövdesi çalışıyor (duraklatma kapısı geçildi)
    token: threading.Event | None = None  # süren isteğin iptal işareti (duraklatma ve iptal kurar)
    installed_now: bool = False  # Ollama bu işte kuruldu (açılışı daha uzun beklenir)
    setup_path: Path | None = None


class _Runner(QThread):
    """Tek arka plan işi: planın adımlarını sırayla yürütür, olayları arayüz iş parçacığına kuyrukla gönderir."""

    event = Signal(object, str, object)  # (iş, olay, veri)

    def __init__(self, svc: AiSetup, job: _Job) -> None:
        super().__init__()
        self.setObjectName("yz-kurulum")
        self.svc, self.job = svc, job
        self.deps = svc._deps
        self.url = DEFAULT_URL
        self.progress = _Progress({}, svc._clock)
        self.seen_bytes = False
        self.handlers: dict[Step, Callable[[threading.Event], None]] = {
            Step.DOWNLOAD: self._download, Step.INSTALL: self._install, Step.START: self._start,
            Step.CHECK: self._check, Step.PULL: self._pull, Step.TEST: self._test,
        }

    def _emit(self, kind: str, data: Any = None) -> None:
        self.event.emit(self.job, kind, data)

    def run(self) -> None:
        result = ("fail", TEXT_FAILED, "")
        try:
            result = self._work()
        finally:  # iş nasıl biterse bitsin arayüz sonucu alır (servis "running"de kalmaz)
            self._emit("end", result)

    def _work(self) -> tuple[str, str, str]:
        svc, job = self.svc, self.job
        try:
            plan = job.plan
            if plan is None:  # yoklama Ollama kapalıyken ~1,5 sn sürer: arayüz iş parçacığında yapılmaz
                plan = self._call(svc.plan, job.model, job.apply, update=job.update)
                with svc._cv:
                    job.plan = plan
                self._emit("plan", plan)
            self.url = plan.url
            sizes = {}
            if Step.DOWNLOAD in plan.steps:
                sizes[Step.DOWNLOAD] = SETUP_BYTES
            if Step.PULL in plan.steps:
                sizes[Step.PULL] = model_size(job.model)
            self.progress = _Progress(sizes, svc._clock)
            if plan.blocker:
                raise AiSetupError(plan.blocker, plan.blocker_kind)
            for step in plan.steps:
                self._step(step)
            with svc._cv:
                if svc._aborted(job):  # son adım sürerken istenen iptal: iş uygulanmaz
                    raise _Aborted
            return "ok", "", ""
        except _Aborted:
            log.info("Yapay zekâ kurulumu durdu (%s)", job.abort or "cancel")
            return job.abort or "cancel", TEXT_CANCELLED, ""
        except Exception as e:
            try:
                text, kind = error_info(e, self._drive(job.step))
            except Exception:
                text, kind = f"{TEXT_FAILED}\nAyrıntı: {type(e).__name__}: {e}", ""
            if _is_user_error(e):
                log.warning("Yapay zekâ kurulumu başarısız (%s, %s): %s", job.step, kind or "?",
                            text.replace("\n", " | "))
            else:
                log.exception("Yapay zekâ kurulumu başarısız (%s)", job.step)
            return "fail", text, kind

    # --- adım yürütme ---------------------------------------------------------------------------------------
    def _step(self, step: Step) -> None:
        svc, job = self.svc, self.job
        with svc._cv:
            job.step, job.in_step = step, False
        self.progress.begin(step)
        self._emit("step", (step, self.progress.snapshot()))
        log.info("Yapay zekâ kurulumu: %s", step.value)
        attempt = 0
        while True:
            self._gate()
            with svc._cv:
                if svc._aborted(job):
                    raise _Aborted
                if svc._pause_wanted(job):
                    continue  # kapı ile buradaki kilit arasında çeviri başladı: önce duraklat
                token = threading.Event()
                job.token, job.in_step = token, True
            if step is Step.INSTALL:
                self._emit("locked", True)
            self.seen_bytes = False
            try:
                self.handlers[step](token)
                break
            except _Aborted:
                raise
            except Exception as e:
                with svc._cv:
                    job.in_step = False
                    aborted, interrupted = svc._aborted(job), token.is_set()
                if aborted:
                    raise _Aborted from e
                if interrupted:
                    # duraklatma isteği kesti: duraklama bitince adım baştan (indirme kaldığı yerden) başlar
                    log.info("Yapay zekâ kurulumu duraklatıldı (%s)", step.value)
                    continue
                # Kurulum dosyasının indirmesi ağ hatasında kendi içinde yeniden dener; model çekme denemez.
                if step is Step.PULL and is_network_error(e):
                    if self.seen_bytes:
                        attempt = 0  # bu denemede ilerledi: kısa kopmalar hakkı tüketmesin
                    if attempt < len(svc._retry_delays):
                        log.info("Ağ hatası (%s), %d. yeniden deneme: %s", step.value, attempt + 1, e)
                        self._emit("retry", TEXT_NETWORK)
                        self._sleep(svc._retry_delays[attempt])
                        attempt += 1
                        continue
                raise
            finally:
                with svc._cv:
                    job.token, job.in_step = None, False
                if step is Step.INSTALL:
                    self._emit("locked", False)
        self.progress.finish(step)

    def _gate(self) -> None:
        """Çeviri çalışırken (ve duraklatma açıksa) bekler; iş iptal edildiyse _Aborted fırlatır."""
        svc, job = self.svc, self.job
        paused = False
        while True:
            with svc._cv:
                if svc._aborted(job):
                    raise _Aborted
                if not svc._pause_wanted(job):
                    break
                if paused:
                    svc._cv.wait(0.5)  # ayar arayüzden değişebilir: yarım saniyede bir yeniden bakılır
                    continue
            paused = True
            self.progress.pause()
            self._emit("paused")
        if paused:
            self._emit("resumed")

    def _sleep(self, seconds: float) -> None:
        """Yeniden denemeden önce bekler; iptalde _Aborted, duraklatma istenirse hemen döner."""
        svc, job = self.svc, self.job
        deadline = time.monotonic() + seconds
        with svc._cv:
            while True:
                if svc._aborted(job):
                    raise _Aborted
                left = deadline - time.monotonic()
                if left <= 0 or svc._pause_wanted(job):
                    return
                svc._cv.wait(left)

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Dış işlevi yardımcı iş parçacığında çalıştırır ve bitmesini bekler. Önceki çağrı (iptal edilmiş bir
        indirme) hâlâ sürüyorsa önce o biter: aynı dosyaya iki istek yazmaz. İş iptal edilir ya da uygulama kapanırsa
        beklemeden _Aborted fırlatır; çağrı kendi kendine biter (kurulum süreci öldürülmez)."""
        svc, job = self.svc, self.job
        with svc._cv:
            while not svc._helper_done.is_set():
                if svc._aborted(job):
                    raise _Aborted
                svc._cv.wait(0.5)
            if svc._aborted(job):
                raise _Aborted
            done = threading.Event()
            svc._helper_done = done
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = fn(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001 - hata yürütücü iş parçacığında yeniden fırlatılır
                box["error"] = e
            finally:
                with svc._cv:
                    done.set()
                    svc._cv.notify_all()

        name = getattr(fn, "__name__", "çağrı")
        threading.Thread(target=target, name=f"yz-kurulum-{name}", daemon=True).start()
        with svc._cv:
            while not done.is_set():
                if svc._aborted(job):
                    raise _Aborted
                svc._cv.wait(0.5)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _on_bytes(self, token: threading.Event, done: int, total: int) -> None:
        """İndirme ilerlemesi (yardımcı iş parçacığında çağrılır); kesilmiş isteğin bildirimi yok sayılır."""
        if token.is_set():
            return
        self.seen_bytes = True
        snap = self.progress.update(int(done or 0), int(total or 0))
        if snap is not None:
            self._emit("bytes", snap)

    def _skip(self) -> None:
        step = self.job.step
        log.info("Yapay zekâ kurulumu: %s gerekmedi", step.value if step else "?")
        if step is not None:
            self.progress.skip(step)

    def _drive(self, step: Step | None) -> str:
        path = local_appdata() if step in (Step.DOWNLOAD, Step.INSTALL) else ollama_models_dir()
        return path.drive or "C:"

    # --- Ollama -------------------------------------------------------------------------------------------------
    def _probe(self) -> Any:
        return self._call(self.deps.probe, self.url)

    def _api(self, timeout: float = 25.0) -> Any:
        """Ollama açık değilse başlatır; açık yoklamayı döndürür."""
        p = self._probe()
        if p.api:
            return p
        if p.other_on_port:
            raise AiSetupError(TEXT_PORT.format(port=url_port(self.url)), "port")
        self._call(self.deps.ensure_running, self.url, timeout=timeout)
        p = self._probe()
        if p.api:
            return p
        if p.other_on_port:
            raise AiSetupError(TEXT_PORT.format(port=url_port(self.url)), "port")
        raise AiSetupError(f"{TEXT_START_FAILED}\nAyrıntı: {self.url} yanıt vermiyor. Günlük: {ollama_server_log()}",
                           "start")

    # --- adımlar --------------------------------------------------------------------------------------------------
    def _needs_install(self) -> bool:
        """İndirme ve kurulumun ön koşulu (Plan.build ile aynı kural): Ollama yok ya da en düşük sürümden eski; plan
        güncelleme içeriyorsa resmî kurulum hâlâ uygulamanın sürümünden eski."""
        p = self._probe()
        present, version = ollama_status(p)
        if not present or (version is not None and version < MIN_OLLAMA_VERSION):
            return True
        plan = self.job.plan
        return bool(plan is not None and plan.updating and getattr(p, "installed", None) is not None
                    and (version is None or version < OLLAMA_VERSION))

    def _download(self, token: threading.Event) -> None:
        if not self._needs_install():
            self._skip()
            return
        path = self._call(self.deps.download_setup, lambda d, t: self._on_bytes(token, d, t), token)
        self.job.setup_path = Path(path)

    def _install(self, token: threading.Event) -> None:
        job = self.job
        if not self._needs_install():
            self._skip()
            return
        path = job.setup_path
        if path is None:  # planda indirme yoktu: yeni planla yeniden denensin
            raise AiSetupError(f"{TEXT_INSTALL_FAILED}\nAyrıntı: kurulum dosyası indirilmedi.", "install")
        self._emit("substep", "verify")  # arayüzde "İmza ve bütünlük doğrulanıyor" satırı
        self._call(self.deps.verify_setup, path)  # indirmeden bu yana beklediyse: bozuksa siler, Türkçe hata
        if self._call(self.deps.find_installed) is None:
            # Yeni kurulum: kaldırıcı Ollama'yı da kaldırmayı sorabilsin. İşaret kurulumdan ÖNCE yazılır: uygulama
            # kurulum sürerken kapanırsa kurulum süreci kendi biter (öldürülmez), sonraki açılışta Ollama kurulu
            # bulunur ve bu adım hiç çalışmaz.
            self._call(self.deps.mark_installed_by_us)
            self._emit("installed", True)
        self._emit("substep", "install")  # "Ollama kuruluyor (1–2 dk)"
        self._call(self.deps.run_setup, path, SETUP_LOG)  # başarıda kurulum dosyasını kendisi siler
        if self._call(self.deps.find_installed) is None:
            raise AiSetupError(f"{TEXT_INSTALL_FAILED}\nAyrıntı: kurulumdan sonra Ollama bulunamadı. "
                               f"Günlük: {SETUP_LOG}", "install")
        job.installed_now = True

    def _start(self, token: threading.Event) -> None:
        p = self._api(timeout=60.0 if self.job.installed_now else 25.0)
        version = tuple(p.version) if p.version else None
        if version is not None and version < MIN_OLLAMA_VERSION:
            have = ".".join(map(str, version))
            need = ".".join(map(str, MIN_OLLAMA_VERSION))
            raise AiSetupError(f"{TEXT_OLD}\nAyrıntı: çalışan sürüm {have}, en az {need} gerekli.", "outdated")

    def _check(self, token: threading.Event) -> None:
        """GPU kapısı: Ollama'nın günlüğünde yalnız işlemci ya da tümleşik kart varsa model indirilmez. Kartın
        belleği modele yetmiyorsa hafif modele geçilir. Günlük okunamazsa kapı sessizce geçilir."""
        job = self.job
        if has_model(job.model, self._api().models or ()):
            self._skip()  # model zaten var: korunacak indirme yok
            return
        computes = list(self._call(self.deps.inference_compute, ollama_server_log()) or ())
        if not computes:
            log.info("GPU kapısı: Ollama günlüğü okunamadı, atlandı")
            return
        usable = [c for c in computes
                  if str(c.library).lower() != "cpu" and str(c.kind).lower() not in ("igpu", "integrated")]
        if not usable:
            raise AiSetupError(f"{TEXT_NO_GPU}\n{TEXT_NO_GPU_TIP}", "no_gpu")
        vram = max(int(c.total) for c in usable)
        model = model_for_vram(job.model, vram)
        if model != job.model:
            note = TEXT_LIGHTER.format(vram=f"{vram / GIB:.0f}", model=model_title(model))
            log.info("GPU kapısı: %s yerine %s (%.1f GiB)", job.model, model, vram / GIB)
            with self.svc._cv:
                job.model = model
            self.progress.set_size(Step.PULL, model_size(model))
            self._emit("model", (model, note, self.progress.snapshot()))

    def _pull(self, token: threading.Event) -> None:
        model = self.job.model
        if has_model(model, self._api().models or ()):
            self._skip()
            return
        self._call(self.deps.pull, self.url, model, lambda d, t, s="": self._on_bytes(token, d, t), token)

    def _test(self, token: threading.Event) -> None:
        """Hız testi (Faz 2). Karar yalnız Speed.median_ms / Speed.verdict'ten verilir (gpu_share güvenilmez)."""
        fn = getattr(self.deps, "speed_test", None)
        if fn is None:
            self._skip()
            return
        self._api()
        profile = getattr(self.svc.settings.translate, "profile", "") or DEFAULT_PROFILE_ID
        self._emit("speed", self._call(fn, self.url, self.job.model, profile))


# Kapanışta beklenip bitmeyen iş parçacıkları burada tutulur: çalışırken yok edilmesinler.
_abandoned: list[QThread] = []


def _keep(th: QThread) -> None:
    _abandoned[:] = [t for t in _abandoned if t.isRunning()]
    _abandoned.append(th)


def _sec(ms: int) -> str:
    return f"{ms / 1000:.1f}".replace(".", ",")


# --- servis --------------------------------------------------------------------------------------------------------
class AiSetup(QObject):
    """Yerel yapay zekâ kurulum servisi. Yöntemler arayüz iş parçacığından çağrılır; arayüz `changed` ile
    güncellenir (ilerleme bildirimi saniyede en çok ~5 kez, durum değişiklikleri hemen) ve `finished(başarılı,
    ileti)` ile sonucu alır (iptalde finished(False, "İptal edildi."); kapanışta yayılmaz).

    Durum öznitelikleri:
      state: idle | running | paused | done | failed
      step: süren (ya da duraklamada sırası gelen) adım; hatada hatalı adım; bitince None
      substep: INSTALL adımının alt adımı: "verify" (adım bununla başlar: imza ve bütünlük doğrulanıyor) | "install"
        (Ollama kuruluyor / güncelleniyor); başka adımlarda "" (hatada hatalı alt adım kalır)
      done_bytes, total_bytes: süren adımın baytları (bayt adımı değilse 0)
      all_done_bytes, all_total_bytes, fraction: bütün indirme (adımlar boyunca)
      eta_s: bütün indirmenin kalan süresi (sn), son ~10 sn'nin ortalama hızından; bilinmiyorsa None
      message: kısa durum satırı; error: hata metni (ilk satır kısa, sonrası ayrıntı); error_kind: hata türü
      can_retry / manual_install: hatada "Tekrar dene" / "Elle kur" (ollama.com/download) sunulsun mu
      model: kurulan model (GPU kapısı hafif modele geçirebilir; note geçişi açıklar)
      speed, speed_verdict: hız testi sonucu (Faz 2; "fast" | "ok" | "slow", yoksa ""); active_plan: süren ya da
      son plan (resume_pending / retry'da plan arka planda çıkana kadar None)
    """

    changed = Signal()
    finished = Signal(bool, str)

    def __init__(self, settings: Settings, save: Callable[[], None], deps: Any = None, *,
                 retry_delays: Iterable[float] = RETRY_DELAYS, clock: Callable[[], float] = time.monotonic,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._save_cb = save
        self._deps = deps if deps is not None else Deps()
        self._retry_delays = tuple(retry_delays)
        self._clock = clock
        self._cv = threading.Condition()
        self._pipeline_running = False
        self._job: _Job | None = None
        self._thread: _Runner | None = None
        self._helper_done = threading.Event()  # son dış çağrı bitti mi (aynı anda tek çağrı)
        self._helper_done.set()
        self._closed = False
        self._locked = False  # "Ollama kuruluyor": iptal ve duraklatma kapalı
        self._advice: Advice | None = None

        self.state = "idle"
        self.step: Step | None = None
        self.substep = ""
        self.done_bytes = self.total_bytes = 0
        self.all_done_bytes = self.all_total_bytes = 0
        self.eta_s: int | None = None
        self.message = ""
        self.error = ""
        self.error_kind = ""
        self.model = ""
        self.note = ""
        self.speed: Speed | None = None
        self.active_plan: Plan | None = None
        ai = settings.ai
        if ai.pending_model and ai.failed:  # önceki açılışta hatayla bitmiş iş: "Tekrar dene" beklenir
            self.state, self.error, self.model = "failed", ai.failed, ai.pending_model
            self.error_kind = str(getattr(ai, "failed_kind", "") or "")  # can_retry / manual_install bundan
            self.message = _first_line(ai.failed)

    # --- durum ----------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state in ("running", "paused")

    @property
    def can_cancel(self) -> bool:
        """İptal düğmesi açık mı ("Ollama kuruluyor" adımında kapalı)?"""
        return self.active and not self._locked

    @property
    def can_retry(self) -> bool:
        """Hatada "Tekrar dene" sunulsun mu (ör. imza doğrulanamadıysa hayır)?"""
        return self.state == "failed" and self.error_kind not in NO_RETRY_KINDS

    @property
    def manual_install(self) -> bool:
        """Hatada "Elle kur" bağlantısı (ollama.com/download) sunulsun mu? Ollama'nın kurulamadığı ya da
        güncellenmesi gereken hatalarda evet."""
        if self.state != "failed":
            return False
        return self.error_kind in MANUAL_KINDS or (not self.error_kind and self.step in (Step.DOWNLOAD, Step.INSTALL))

    @property
    def speed_verdict(self) -> str:
        return str(getattr(self.speed, "verdict", "") or "") if self.speed is not None else ""

    @property
    def fraction(self) -> float:
        """Bütün indirmenin oranı (0..1)."""
        if self.state == "done":
            return 1.0
        if self.all_total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.all_done_bytes / self.all_total_bytes))

    @property
    def url(self) -> str:
        return self.active_plan.url if self.active_plan is not None else local_url(self.settings.translate.ollama_url)

    # --- donanım ve plan --------------------------------------------------------------------------------------
    def advice(self, refresh: bool = False) -> Advice:
        """Donanım önerisi (kademe, model…). İlk çağrı ekran kartını okur (≤ ~1 sn; arka planda çağrılabilir),
        sonrakiler aynı sonucu döndürür."""
        if self._advice is None or refresh:
            d = self._deps
            ram_fn = getattr(d, "system_ram", None)
            ram = ram_fn() if ram_fn is not None else None
            self._advice = d.advise(d.list_gpus(), ram=ram, win_build=self._win_build() or 0)
        return self._advice

    def plan(self, model: str | None = None, apply: bool = True, *, test: bool = SPEED_TEST,
             update: bool = False) -> Plan:
        """Bugünkü duruma göre plan; model verilmezse donanım önerisindeki model. Ollama'yı yoklar: Ollama kapalıysa
        ~1,5 sn sürebilir (arayüz donmasın diye arka planda çağrılabilir; ayar yazmaz, Qt kullanmaz)."""
        model = (model or "").strip() or self._advised_model()
        return Plan.build(self._probe(), model, apply, free_space=self._deps.free_space,
                          win_build=self._win_build(), test=test, update=update)

    def _advised_model(self) -> str:
        return getattr(self.advice(), "model", None) or RECOMMENDED_MODEL

    def _win_build(self) -> int | None:
        fn = getattr(self._deps, "windows_build", None)
        return fn() if fn is not None else windows_build()

    def _probe(self) -> Probe:
        """Ayardaki adres (yerelse) ve OLLAMA_HOST adayları; ilk açık olan, yoksa ayardakinin yoklaması."""
        configured = self.settings.translate.ollama_url
        urls = [local_url(configured)]
        candidates = getattr(self._deps, "candidate_urls", None)
        if candidates is not None:
            try:
                for u in candidates(configured):
                    if is_local_url(u) and local_url(u) not in urls:
                        urls.append(local_url(u))
            except Exception:
                log.debug("Ollama aday adresleri okunamadı", exc_info=True)
        first = None
        for u in urls:
            p = self._deps.probe(u)
            if p.api:
                return p
            first = first or p
        return first

    # --- iş -----------------------------------------------------------------------------------------------------
    def start(self, plan: Plan) -> None:
        """Planı arka planda yürütür. Engel varsa iş hemen hatayla biter (finished(False, engel))."""
        self._launch(plan, plan.model, plan.apply)

    def retry(self) -> None:
        """"Tekrar dene": işi bugünkü duruma göre arka planda yeniden planlayıp başlatır (yapılmış adımlar atlanır).
        Model "sürüm eski" dediyse resmî Ollama kurulumu uygulamanın sürümüne güncellenir."""
        if self.active:
            return
        model = self.model or self.settings.ai.pending_model
        if not model:
            return
        apply = self.active_plan.apply if self.active_plan is not None else True
        self._launch(None, model, apply, update=self.error_kind == "outdated")

    def _launch(self, plan: Plan | None, model: str, apply: bool, update: bool = False) -> None:
        if self._closed:
            return
        if self.active and self._job is not None and not self._job.abort:
            log.warning("Yapay zekâ kurulumu zaten sürüyor; yeni iş başlatılmadı")
            return
        old = self._thread
        if old is not None and old.isRunning():
            _keep(old)  # iptal edilmiş eski iş bitiyor; olayları artık yok sayılır
        job = _Job(plan=plan, model=model, apply=apply, update=update)
        with self._cv:
            self._job = job
        self.active_plan = plan
        self.model, self.note, self.speed = model, "", None
        self.state, self.step, self.substep, self.error, self.error_kind = "running", None, "", "", ""
        self.message = TEXT_PREPARING
        self.done_bytes = self.total_bytes = self.all_done_bytes = 0
        self.all_total_bytes = plan.download_bytes if plan is not None else 0
        self.eta_s = None
        self._locked = False
        if apply:
            ai = self.settings.ai
            ai.pending_model, ai.failed, ai.failed_kind = model, "", ""
            self._save()
        steps = [s.value for s in plan.steps] if plan is not None else "(planlanacak)"
        log.info("Yapay zekâ kurulumu başlıyor: %s, adımlar %s", model, steps)
        runner = _Runner(self, job)
        runner.event.connect(self._on_event)
        self._thread = runner
        runner.start()
        self.changed.emit()

    def cancel(self) -> None:
        """Süren işi iptal eder ve pending_model'i siler ("Ollama kuruluyor" adımındaysa o adım bitince durur); iş
        durunca indirilmiş Ollama kurulum dosyası da silinir. İş yoksa hatalı / yarım işten vazgeçer ("Hızlı çeviride
        kal"): pending_model, failed ve kurulum dosyası silinir."""
        job = self._job
        if not self.active or job is None:
            ai = self.settings.ai
            if ai.pending_model or ai.failed or self.state == "failed":
                ai.pending_model, ai.failed, ai.failed_kind = "", "", ""
                self.state, self.error, self.error_kind, self.message, self.step = "idle", "", "", "", None
                self.substep = ""
                self._save()
                self._discard_setup(job)
                self.changed.emit()
            return
        with self._cv:
            if job.abort:
                return
            job.abort = "cancel"
            if job.token is not None and not (job.in_step and job.step is Step.INSTALL):
                job.token.set()
            self._cv.notify_all()
        log.info("Yapay zekâ kurulumu iptal ediliyor")
        if job.apply:
            self.settings.ai.pending_model = ""
            self._save()
        self.message = TEXT_CANCELLING
        self.changed.emit()

    def resume_pending(self) -> None:
        """Uygulama açılışında: önceki açılışta yarım kalmış iş (pending_model, hata yok) kaldığı yerden sürer. Plan
        arka planda çıkarılır (arayüz donmaz)."""
        ai = self.settings.ai
        if self._closed or self.active or not ai.pending_model or ai.failed:
            return
        log.info("Yarım kalan yapay zekâ kurulumu sürüyor: %s", ai.pending_model)
        self._launch(None, ai.pending_model, True)

    def on_pipeline_running(self, running: bool) -> None:
        """İşlem hattı başladı / durdu. Çalışırken (pause_while_running açıksa) süren istek kesilir ve iş duraklar;
        "Ollama kuruluyor" adımı kesilmez, iş o adım bitince durur. Durunca kaldığı yerden sürer."""
        with self._cv:
            self._pipeline_running = bool(running)
            job = self._job
            if (job is not None and running and job.token is not None and self._pause_wanted(job)
                    and not (job.in_step and job.step is Step.INSTALL)):
                job.token.set()
            self._cv.notify_all()

    def force_resume(self) -> None:
        """"Yine de sürdür": bu iş, çeviri çalışsa da duraklamaz."""
        with self._cv:
            if self._job is not None:
                self._job.forced = True
            self._cv.notify_all()

    def shutdown(self, wait_ms: int = 3000) -> bool:
        """Uygulama kapanıyor: işi durdurur, en çok wait_ms bekler. Süren kurulum süreci öldürülmez ve pending_model
        silinmez: iş sonraki açılışta sürer. İş parçacığı durduysa True."""
        self._closed = True
        with self._cv:
            job = self._job
            if job is not None:
                job.abort = "shutdown"
                if job.token is not None:
                    job.token.set()
            self._cv.notify_all()
        th = self._thread
        if th is None or th.wait(max(0, int(wait_ms))):
            return True
        _keep(th)
        log.warning("Yapay zekâ kurulumu %d ms içinde durmadı", wait_ms)
        return False

    # --- iş parçacıklarının ortak kararları (self._cv kilidi altında çağrılır) -------------------------------------
    def _pause_wanted(self, job: _Job) -> bool:
        if not self._pipeline_running or job.forced:
            return False
        if getattr(self.settings.ai, "pause_while_running", True):
            return True
        # var olan Ollama'yı güncellemek onu kapatır: çeviri çalışırken başlanmaz
        return job.step is Step.INSTALL and job.plan is not None and job.plan.updating and not job.in_step

    @staticmethod
    def _aborted(job: _Job) -> bool:
        if job.abort == "shutdown":
            return True
        return job.abort == "cancel" and not (job.in_step and job.step is Step.INSTALL)

    # --- arka plan işinin olayları (arayüz iş parçacığında) ------------------------------------------------------
    @Slot(object, str, object)
    def _on_event(self, job: _Job, kind: str, data: Any) -> None:
        if job is not self._job:
            return  # yerine yenisi başlatılmış eski işin olayı
        if kind == "end":
            self._end(job, *data)
            return
        if kind == "plan":
            self.active_plan = data
            self.all_total_bytes = data.download_bytes
        elif kind == "step":
            step, snap = data
            # INSTALL önce kurulum dosyasını doğrular: satır baştan "İmza ve bütünlük doğrulanıyor" olur (kurulumun
            # gerekip gerekmediğine bakan yoklama Ollama kapalıyken ~2 sn sürer; arada "kuruluyor" görünmesin)
            self.step, self.substep = step, ("verify" if step is Step.INSTALL else "")
            self._set_bytes(snap)
            self.message = self._progress_message()
        elif kind == "substep":  # INSTALL: önce doğrulama, sonra kurulum
            self.substep = data
            self.message = self._progress_message()
        elif kind == "bytes":
            self._set_bytes(data)
        elif kind == "paused":
            self.state, self.message, self.eta_s = "paused", TEXT_PAUSED, None
        elif kind == "resumed":
            self.state = "running"
            self.message = self._progress_message()
        elif kind == "retry":
            self.message = data
        elif kind == "locked":
            self._locked = bool(data)
        elif kind == "model":
            self.model, self.note, snap = data
            self.message = self.note
            self._set_bytes(snap)
            if job.apply:
                self.settings.ai.pending_model = self.model
                self._save()
        elif kind == "installed":
            self.settings.ai.installed_ollama = True
            self._save()
        elif kind == "speed":
            self.speed = data
            ms = int(getattr(data, "median_ms", 0) or 0)
            if ms:
                self.settings.ai.speed_ms = ms
        self.changed.emit()

    def _end(self, job: _Job, kind: str, text: str, error_kind: str) -> None:
        if kind == "shutdown":
            return  # uygulama kapanıyor: ayarlar olduğu gibi kalır, iş sonraki açılışta sürer
        ai = self.settings.ai
        self._locked = False
        self.eta_s = None
        if kind == "ok":
            text = self._apply(job) if job.apply else TEXT_MODEL_READY.format(model=model_title(self.model))
            self.state, self.step, self.substep, self.error, self.error_kind = "done", None, "", "", ""
            self.message = _first_line(text)
            self.all_done_bytes = self.all_total_bytes
            self.eta_s = 0
            log.info("Yapay zekâ kurulumu bitti: %s", self.model)
            ok = True
        elif kind == "cancel":
            if job.apply:
                ai.pending_model = ""
            self.state, self.step, self.substep, self.error, self.error_kind = "idle", None, "", "", ""
            self.message = TEXT_CANCELLED
            ok = False
            # Kullanıcı yapay zekâ kurulumundan vazgeçti: 1,9 GB'lık kurulum dosyası (yarım .part dahil) kalmasın.
            # Yalnız model indiren iş (apply=False) kurulum dosyası indirmediyse ona dokunmaz.
            if job.apply or (job.plan is not None and Step.DOWNLOAD in job.plan.steps):
                self._discard_setup(job)
        else:
            if job.apply:
                # pending_model kalır: "Tekrar dene" modeli bilir, kendiliğinden sürmez. Tür, sonraki açılışta da
                # "Tekrar dene" / "Elle kur" kararı için saklanır.
                ai.failed, ai.failed_kind = text, error_kind
            self.state, self.error, self.error_kind, self.message = "failed", text, error_kind, _first_line(text)
            ok = False
        self._save()
        self.finished.emit(ok, text)
        self.changed.emit()

    def _apply(self, job: _Job) -> str:
        """Başarı: motor yapay zekâ olur (çeviri çalışıyorsa yeniden başlatılmaz, sonraki Başlat'ta devreye girer).
        Hız testi "yavaş" dediyse motor değişmez: kullanıcı hızlı çeviride kalmayı ya da hafif modeli seçer."""
        ai, t = self.settings.ai, self.settings.translate
        ai.pending_model, ai.failed, ai.failed_kind, ai.invite_pending = "", "", "", False
        if self.speed_verdict == "slow":
            share = getattr(self.speed, "gpu_share", None)  # yalnız açıklama: Ollama'nın bu değeri güvenilmez
            reason = ": ekran kartının belleği yetmiyor" if share is not None and share < 0.99 else ""
            tip = TEXT_SLOW_TIP.format(sec=_sec(int(getattr(self.speed, "median_ms", 0))), reason=reason)
            return f"{TEXT_SLOW}\n{tip}"
        t.engine, t.ollama_model = "ollama", self.model
        if not (t.profile or "").strip():
            t.profile = DEFAULT_PROFILE_ID
        if job.plan is not None and (not is_local_url(t.ollama_url) or local_url(t.ollama_url) != job.plan.url):
            t.ollama_url = job.plan.url  # kurulan Ollama bu adreste
        return TEXT_READY_LATER if self._pipeline_running else TEXT_READY

    def _discard_setup(self, job: _Job | None) -> None:
        """Kullanıcı yapay zekâ kurulumundan vazgeçti: indirilen Ollama kurulum dosyası ve yarım .part silinir
        (deps.discard_download; nesnede yoksa atlanır). Duraklatmada ve kapanışta çağrılmaz.

        Kesilen indirme isteği dosyayı hâlâ açık tutabilir (Windows açık dosyayı silmez): silme, süren dış çağrı
        bitince ve sonraki dış çağrılardan önce yapılır (dış çağrı sırasına girer, bkz. _Runner._call). O arada yeni
        bir iş başlatıldıysa dosya artık onundur (kaldığı yerden sürer): silinmez."""
        fn = getattr(self._deps, "discard_download", None)
        if fn is None:
            return
        with self._cv:
            prev, done = self._helper_done, threading.Event()
            self._helper_done = done

        def target() -> None:
            try:
                prev.wait()
                with self._cv:
                    current = self._job is job
                if current:
                    fn()
                    log.info("Yapay zekâ kurulumu iptal edildi; Ollama kurulum dosyası silindi")
            except Exception:
                log.warning("Ollama kurulum dosyası silinemedi", exc_info=True)
            finally:
                with self._cv:
                    done.set()
                    self._cv.notify_all()

        threading.Thread(target=target, name="yz-kurulum-sil", daemon=True).start()

    def _set_bytes(self, snap: tuple[int, int, int, int, int | None]) -> None:
        self.done_bytes, self.total_bytes, self.all_done_bytes, self.all_total_bytes, self.eta_s = snap

    def _step_message(self, step: Step) -> str:
        plan = self.active_plan
        text = step_title(step, self.model, update=plan is not None and plan.updating)
        return f"{text}{TEXT_INSTALL_TIME}" if step is Step.INSTALL else text

    def _progress_message(self) -> str:
        """Süren adımın satırı; INSTALL'da alt adımınki ("İmza ve bütünlük doğrulanıyor" / "Ollama kuruluyor")."""
        if self.step is None:
            return TEXT_PREPARING
        if self.step is Step.INSTALL and self.substep == "verify":
            return TEXT_VERIFYING
        return self._step_message(self.step)

    def _save(self) -> None:
        try:
            self._save_cb()
        except Exception:
            log.exception("Ayarlar kaydedilemedi")
