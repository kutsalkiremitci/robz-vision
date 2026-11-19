r"""Güncelleme çekirdeği (Qt kullanmaz): GitHub'daki son sürümü denetler, kurulum dosyasını indirip doğrular, ayarları
yedekler ve kurulumu sessiz başlatır. Zamanlama, iş parçacıkları ve arayüz durumu altyazitr.update_service'te.

1. **Denetim** (UpdateChecker.check): GitHub API'sinin "son sürüm" kaydı (API_URL) kimlik doğrulamasız tek GET ile
   okunur. User-Agent "RobzVision/<sürüm>"; kişisel bilgi gönderilmez. Sistemin vekil sunucusu ayarı geçerlidir
   (requests: HTTPS_PROXY ortam değişkeni ya da Windows'un İnternet Seçenekleri ayarı). Bir önceki 200 yanıtının ETag'i
   If-None-Match ile gönderilir; 304'te gövde okunmaz. Taslak ve ön sürümler yok sayılır; sürüm "vX.Y.Z" etiketinden
   okunur ve __version__ ile sayı sayı karşılaştırılır (1.3.0 < 1.3.1 < 1.10.0). Hata, zaman aşımı, internet yok:
   sonuç "error" (yalnız günlük); 403/429 (oran sınırı): "limited", bekleme Retry-After / X-RateLimit-Reset'ten.
2. **İndirme** (download_setup): SHA256SUMS.txt ve RobzVision-Setup-X.Y.Z-x64.exe yeni, boş bir klasöre
   (UPDATE_DIR\X.Y.Z) iner. Dosya adresi API'nin verdiği browser_download_url'dir ve birebir bu deponun sürüm indirme
   adresi olmalıdır (<DOWNLOAD_PREFIX><etiket>/<ad>); yönlendirmeler elle izlenir, her adım HTTPS. Boyut API'deki size
   ile, SHA256 özeti SHA256SUMS.txt'deki (API dosya özeti, "digest", varsa onunla da) aynı olmalı; tutmazsa dosya
   silinir ve hiç çalıştırılmaz. Yarım indirme (.part) sürdürülmez: her deneme baştan alır.
3. **Kurulum** (launch_setup): yalnız bu kullanıcıya kurulu kopyada (install_kind() == "user": Inno Setup kaldırma
   kaydı bu exe'nin klasörünü gösteriyor). Ayarlar önce yedeklenir (backup_settings). Kurulum dosyası SETUP_FLAGS +
   /BEKLE=<pid> ile başlatılır (subprocess liste argümanlarıyla, kabuk yok) ve uygulama hemen kapanır. Kurulum
   (installer/RobzVision.iss [Code]) bu süreç bitene kadar bekler, dosyaları değiştirir ve /GUNCELLEME kipinde
   uygulamayı yeniden açar. Ayarlar (%APPDATA%\RobzVision) ve modeller (%LOCALAPPDATA%\RobzVision) kurulum klasörünün
   dışındadır; kurulum onlara dokunmaz.

Güncelleyicinin dayandığı adlar (.github/workflows/release.yml üretir; değişirse güncelleyici kırılır): etiket
"vX.Y.Z", SETUP_PATTERN, SUMS_NAME ("<sha256>  <dosya>" satırları). Hatalar UpdateError'dır: 1. satır kullanıcıya
gösterilecek kısa Türkçe metin, 2. satır "Ayrıntı: …".
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from altyazitr import APP_ID, __version__
from altyazitr.paths import DATA_DIR, LOG_DIR

if TYPE_CHECKING:
    import requests

log = logging.getLogger(__name__)

# --- adresler ve adlar ----------------------------------------------------------------------------------------------
REPO = "kutsalkiremitci/robz-vision"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
RELEASES_URL = f"https://github.com/{REPO}/releases"
SETUP_PATTERN = "RobzVision-Setup-{version}-x64.exe"  # release.yml / RobzVision.iss OutputBaseFilename
SUMS_NAME = "SHA256SUMS.txt"

# installer/RobzVision.iss: AppId={{20358419-…}; Inno Setup kaldırma kaydını "<AppId>_is1" adıyla yazar
INNO_APP_ID = "{20358419-F95F-4F60-86CE-3AFF9A70B062}"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INNO_APP_ID}_is1"

UPDATE_DIR = DATA_DIR / "guncelleme"  # indirilen kurulum dosyaları; açılışta temizlenir, kaldırmada silinir
SETUP_LOG = LOG_DIR / "guncelleme-kurulum.log"
# /SILENT: sihirbaz yok, yalnız ilerleme penceresi (kullanıcı uygulamanın kapanıp açılması arasında olanı görür).
# /GUNCELLEME ve /BEKLE=<pid> RobzVision.iss [Code]'undadır: uygulama kapanınca kurar, bitince yeniden açar.
SETUP_FLAGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/SP-", "/NORESTARTAPPLICATIONS",
               "/GUNCELLEME")
BACKUP_KEEP = 3  # ayarlar.yedek-<sürüm>.json: en çok bu kadar yedek kalır (en yeniler)

API_TIMEOUT = 5.0  # sn; denetim tek istektir, takılırsa bir sonraki denetime kalır
DOWNLOAD_TIMEOUT = (10.0, 30.0)  # (bağlanma, okuma) sn
MAX_REDIRECTS = 5
MAX_JSON_BYTES = 2 << 20  # sürüm kaydı (notlarla birlikte) birkaç on KB'dir
MAX_SUMS_BYTES = 64 << 10
MAX_NOTES_CHARS = 12000
MIN_RETRY_S, MAX_RETRY_S = 60.0, 24 * 3600.0  # oran sınırında bekleme sınırları
_CHUNK = 256 * 1024
_PROGRESS_EVERY = 0.2  # sn
_BLOCKED_WINERRORS = {5, 225, 226, 1260, 4551}  # erişim, antivirüs (225/226), AppLocker/SRP, WDAC

# Uygulamanın açılıştaki PATH'i: çeviri motoru çalışırken PATH'e CUDA klasörleri ekler (translate); kurulum ve yeniden
# açılan uygulama o eklemeleri devralmasın.
_START_PATH = os.environ.get("PATH", "")

_MSG_NETWORK = "Güncelleme indirilemedi: internet bağlantısı kesildi. Tekrar deneyin."
_MSG_DIGEST = "İndirilen güncelleme doğrulanamadı ve silindi. Tekrar deneyin."
_MSG_DOWNLOAD = "Güncelleme indirilemedi. Biraz sonra tekrar deneyin."
_MSG_MISSING = "Bu sürümün kurulum dosyası bulunamadı. Sürüm sayfasından indirebilirsiniz."
_MSG_CANCEL = "İptal edildi."
_MSG_LAUNCH = "Güncelleme başlatılamadı."
_MSG_BLOCKED = "Güncelleme başlatılamadı: antivirüs programı ya da şirket ilkesi engelledi."


class UpdateError(RuntimeError):
    """Kullanıcıya gösterilecek Türkçe hata. str(e): 1. satır kısa metin, 2. satır "Ayrıntı: …".

    kind: "cancel" iptal (hata sayılmaz); "network" ağ; "disk" yer yok; "digest" boyut ya da SHA256 tutmadı (dosya
    silindi); "missing" sürümde kurulum dosyası ya da özet yok; "download" diğer indirme hataları (HTTP 404, beklenmeyen
    adres); "blocked" antivirüs ya da ilke; "launch" kurulum başlatılamadı."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def short(self) -> str:
        return str(self).split("\n", 1)[0]

    @property
    def detail(self) -> str:
        rest = str(self).split("\n", 1)[1:]
        return rest[0].removeprefix("Ayrıntı: ") if rest else ""


def _err(short: str, detail: str, kind: str) -> UpdateError:
    return UpdateError(f"{short}\nAyrıntı: {detail}" if detail else short, kind=kind)


def _exc_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".strip()


def user_agent() -> str:
    """Genel bir ad: uygulama ve sürümü; kişisel bilgi yok."""
    return f"{APP_ID}/{__version__}"


# --- sürüm ---------------------------------------------------------------------------------------------------------
_VERSION_RE = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,6})")


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """"v1.3.1" / "1.3.1" → (1, 3, 1). Ön sürüm ("1.4.0-beta", "1.4.0rc1") ve başka biçimler None (yok sayılır)."""
    m = _VERSION_RE.fullmatch(str(text or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(version: str, current: str = __version__) -> bool:
    """version, current'tan yeni mi (sayı sayı: 1.3.0 < 1.3.1 < 1.10.0)? Okunamayan sürüm yeni sayılmaz."""
    new, cur = parse_version(version), parse_version(current)
    return new is not None and cur is not None and new > cur


def setup_name(version: str) -> str:
    return SETUP_PATTERN.format(version=version)


# --- sürüm kaydı -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    """Sürümlerin okunduğu yer. Uygulama GITHUB'ı kullanır; testler ve deneme 127.0.0.1'deki sahte sunucuyu.

    download_prefix: sürüm dosyalarının adresi birebir "<önek><etiket>/<dosya adı>" olmalı. Bütün istekler (ve
    yönlendirmeler) önekin şemasıyla (GitHub'da HTTPS) yapılır."""

    api_url: str = API_URL
    download_prefix: str = DOWNLOAD_PREFIX
    releases_url: str = RELEASES_URL

    @property
    def scheme(self) -> str:
        return urlsplit(self.download_prefix).scheme


GITHUB = Source()


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    digest: str = ""  # API'nin verdiği SHA256 ("sha256:" öneki atılmış, küçük harf); yoksa ""


@dataclass(frozen=True)
class Release:
    version: str  # "1.3.1"
    tag: str  # "v1.3.1"
    page_url: str  # sürüm sayfası (Neler yeni?, taşınabilir sürümde İndir)
    notes: str  # Markdown (yalnız "Bu sürümde" bölümü)
    setup: Asset | None  # kurulum dosyası; yoksa ya da adresi beklenmedikse None
    sums: Asset | None  # SHA256SUMS.txt

    @property
    def installable(self) -> bool:
        """Kendini güncelleme için gereken iki dosya da var mı?"""
        return self.setup is not None and self.sums is not None


def release_notes(body: Any) -> str:
    """Sürüm sayfasının metninden (tools/surum_notlari.py: indirme rehberi + "## Bu sürümde") yalnız "Bu sürümde"
    bölümü; başlık yoksa tamamı. Satır sonları \\n, uzunluk sınırlı."""
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() == "## bu sürümde":
            text = "\n".join(lines[i + 1:])
            break
    text = text.strip()
    if len(text) > MAX_NOTES_CHARS:
        text = text[:MAX_NOTES_CHARS].rsplit("\n", 1)[0].rstrip() + "\n\n…"
    return text


def _asset(raw: Any, name: str, tag: str, source: Source) -> Asset | None:
    """API'deki dosya kaydı; adresi beklenen adres değilse ya da boyutu yoksa None (o dosya yok sayılır)."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("browser_download_url") or "")
    expected = f"{source.download_prefix}{tag}/{name}"
    if url != expected:
        log.warning("Güncelleme dosyasının adresi beklenmedik, yok sayıldı: %r (beklenen %r)", url, expected)
        return None
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return None
    digest = str(raw.get("digest") or "").strip().lower()
    digest = digest.removeprefix("sha256:") if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else ""
    return Asset(name, url, size, digest)


def parse_release(data: Any, source: Source = GITHUB) -> Release | None:
    """GitHub'ın sürüm kaydı → Release. Taslak, ön sürüm ya da sürümü okunamayan etiket → None."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "").strip()
    ver = parse_version(tag)
    if ver is None:
        return None
    version = "%d.%d.%d" % ver
    page = str(data.get("html_url") or "")
    if not page.startswith(source.releases_url + "/"):
        page = f"{source.releases_url}/tag/{tag}"
    wanted = {setup_name(version): None, SUMS_NAME: None}
    for raw in data.get("assets") or []:
        name = str(raw.get("name") or "") if isinstance(raw, dict) else ""
        if name in wanted and wanted[name] is None:
            wanted[name] = _asset(raw, name, tag, source)
    return Release(version, tag, page, release_notes(data.get("body")), wanted[setup_name(version)],
                   wanted[SUMS_NAME])


@dataclass(frozen=True)
class CheckResult:
    """status: "ok" (yanıt okundu; release None olabilir: yayımlanmış uygun sürüm yok), "same" (304: değişmedi,
    önceki release), "none" (depoda sürüm yok, 404), "limited" (403/429: oran sınırı; retry_after sn), "error"."""

    status: str
    release: Release | None = None
    retry_after: float | None = None
    error: str = ""


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def retry_after(headers: Mapping[str, str], now: float) -> float | None:
    """Oran sınırında bir sonraki denemeye kadar beklenecek süre (sn; MIN_RETRY_S…MAX_RETRY_S): Retry-After (sn ya da
    HTTP tarihi) ya da kalan hak 0 ise X-RateLimit-Reset (Unix zamanı). Başlık yoksa None."""
    secs: float | None = None
    ra = headers.get("Retry-After")
    if ra:
        secs = _to_float(ra)
        if secs is None:
            try:
                secs = parsedate_to_datetime(ra).timestamp() - now
            except (TypeError, ValueError, IndexError):
                secs = None
    if str(headers.get("X-RateLimit-Remaining", "")).strip() == "0":
        reset = _to_float(headers.get("X-RateLimit-Reset"))
        if reset:
            secs = max(secs or 0.0, reset - now)
    if secs is None:
        return None
    return min(max(secs, MIN_RETRY_S), MAX_RETRY_S)


def _same_scheme(url: str, source: Source) -> bool:
    return urlsplit(url).scheme == source.scheme


def _open(session: requests.Session, url: str, source: Source, headers: dict[str, str],
          timeout: float | tuple[float, float]) -> requests.Response:
    """GET (akış); yönlendirmeler elle izlenir ve her adım kaynağın şemasında (HTTPS) olmalı."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _same_scheme(url, source):
            raise _err(_MSG_DOWNLOAD, f"Güvensiz adres reddedildi: {url}", "download")
        r = session.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect:
            nxt = r.headers.get("Location", "")
            r.close()
            url = urljoin(url, nxt)
            continue
        return r
    raise _err(_MSG_DOWNLOAD, f"Çok fazla yönlendirme: {url}", "download")


def _read_capped(r: requests.Response, limit: int) -> bytes:
    buf = bytearray()
    for chunk in r.iter_content(chunk_size=64 * 1024):
        buf += chunk
        if len(buf) > limit:
            raise ValueError(f"yanıt {limit} bayttan uzun")
    return bytes(buf)


class UpdateChecker:
    """Son sürüm denetimi. ETag ve son okunan sürüm bellekte durur (uygulama açık kaldıkça); her açılışın ilk isteği
    tam yanıt alır, sonrakiler koşulludur."""

    def __init__(self, source: Source = GITHUB) -> None:
        self.source = source
        self.etag = ""
        self.release: Release | None = None  # son 200 yanıtındaki sürüm (304'te korunur)

    def check(self, session: requests.Session | None = None, now: Callable[[], float] = time.time) -> CheckResult:
        """Tek GET (API_TIMEOUT). Hata fırlatmaz: sonuç CheckResult'tadır."""
        import requests

        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": user_agent()}
        if self.etag:
            headers["If-None-Match"] = self.etag
        own = session is None
        s = requests.Session() if own else session
        try:
            if not _same_scheme(self.source.api_url, self.source):
                return CheckResult("error", self.release, error=f"güvensiz adres: {self.source.api_url}")
            r = _open(s, self.source.api_url, self.source, headers, API_TIMEOUT)
            try:
                code = r.status_code
                if code == 304:
                    return CheckResult("same", self.release)
                if code == 200:
                    data = json.loads(_read_capped(r, MAX_JSON_BYTES).decode("utf-8"))
                    self.release = parse_release(data, self.source)
                    self.etag = r.headers.get("ETag", "")
                    return CheckResult("ok", self.release)
                if code == 404:
                    self.etag, self.release = "", None
                    return CheckResult("none")
                if code in (403, 429):
                    return CheckResult("limited", self.release, retry_after(r.headers, now()), f"HTTP {code}")
                return CheckResult("error", self.release, error=f"HTTP {code}")
            finally:
                r.close()
        except UpdateError as e:
            return CheckResult("error", self.release, error=e.detail or e.short)
        except (requests.RequestException, OSError, ValueError) as e:  # ağ, zaman aşımı, bozuk JSON
            return CheckResult("error", self.release, error=_exc_text(e))
        finally:
            if own:
                s.close()


# --- indirme ve doğrulama ------------------------------------------------------------------------------------------
def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS.txt ("<64 onaltılık>  <dosya>" ya da "<özet> *<dosya>") → {dosya: küçük harf özet}."""
    out: dict[str, str] = {}
    for line in str(text or "").splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if m:
            out.setdefault(m[2].strip(), m[1].lower())
    return out


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Silinemedi: %s", path, exc_info=True)


def _is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and (exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112))


def _download_error(exc: BaseException) -> UpdateError:
    import requests

    if isinstance(exc, UpdateError):
        return exc
    if _is_disk_full(exc):
        return _err("Diskte yer kalmadı; güncelleme indirilemedi. Yer açıp tekrar deneyin.", _exc_text(exc), "disk")
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                        ConnectionError, TimeoutError)):
        return _err(_MSG_NETWORK, _exc_text(exc), "network")
    return _err(_MSG_DOWNLOAD, _exc_text(exc), "download")


def sha256_of(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verify_file(path: Path, size: int, sha256: str) -> None:
    """Boyut ve SHA256 tutmalı; tutmazsa dosya silinir ve UpdateError(kind="digest") fırlar."""
    path = Path(path)
    try:
        got_size = path.stat().st_size
    except OSError as e:
        raise _err(_MSG_DIGEST, f"Dosya bulunamadı: {_exc_text(e)}", "digest") from e
    if got_size != size:
        _unlink(path)
        raise _err(_MSG_DIGEST, f"Dosya {got_size} bayt, beklenen {size} bayt.", "digest")
    try:
        got = sha256_of(path)
    except OSError as e:
        raise _download_error(e) from e
    if got != sha256.lower():
        _unlink(path)
        raise _err(_MSG_DIGEST, f"SHA256 {got}, beklenen {sha256.lower()}.", "digest")


def _check_asset(asset: Asset, release: Release, source: Source) -> None:
    if asset.url != f"{source.download_prefix}{release.tag}/{asset.name}":
        raise _err(_MSG_DOWNLOAD, f"Beklenmeyen indirme adresi reddedildi: {asset.url}", "download")


def _fresh_dir(folder: Path) -> None:
    """Klasörü boşaltıp yeniden oluşturur (önceki deneme ya da yarım indirme kalmaz)."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)


def _fetch(session: requests.Session, asset: Asset, dest: Path, sha256: str, source: Source,
           progress: Callable[[int, int], None] | None, cancel: threading.Event) -> None:
    """asset'i dest'e indirir (akarken özet çıkarılır); boyut ya da özet tutmazsa dest silinir, UpdateError fırlar."""
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
    r = _open(session, asset.url, source, headers, DOWNLOAD_TIMEOUT)
    h = hashlib.sha256()
    have, last = 0, 0.0
    try:
        if r.status_code != 200:
            raise _err(_MSG_DOWNLOAD, f"Sunucu {r.status_code} yanıtı verdi: {asset.url}", "download")
        length = r.headers.get("Content-Length")
        if length is not None and _to_float(length) != asset.size:
            raise _err(_MSG_DIGEST, f"Sunucudaki dosya {length} bayt, beklenen {asset.size} bayt.", "digest")
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=_CHUNK):
                if cancel.is_set():
                    raise _err(_MSG_CANCEL, "", "cancel")
                if not chunk:
                    continue
                if have + len(chunk) > asset.size:
                    raise _err(_MSG_DIGEST, f"Sunucu {asset.size} bayttan uzun veri gönderdi.", "digest")
                f.write(chunk)
                h.update(chunk)
                have += len(chunk)
                now = time.monotonic()
                if progress is not None and now - last >= _PROGRESS_EVERY:
                    last = now
                    progress(have, asset.size)
    except BaseException:
        r.close()
        _unlink(dest)
        raise
    r.close()
    if have != asset.size:
        _unlink(dest)
        raise _err(_MSG_NETWORK if have < asset.size else _MSG_DIGEST,
                   f"{have} / {asset.size} bayt indi.", "network" if have < asset.size else "digest")
    if h.hexdigest() != sha256:
        _unlink(dest)
        raise _err(_MSG_DIGEST, f"SHA256 {h.hexdigest()}, beklenen {sha256}.", "digest")
    if progress is not None:
        progress(have, asset.size)


def download_setup(release: Release, progress: Callable[[int, int], None] | None = None,
                   cancel: threading.Event | None = None, *, folder: Path | None = None,
                   session: requests.Session | None = None, source: Source = GITHUB) -> tuple[Path, str]:
    """Kurulum dosyasını yeni, boş bir klasöre (folder; varsayılan UPDATE_DIR\\<sürüm>) indirir ve doğrular.

    Önce SHA256SUMS.txt (küçük; API özeti varsa onunla karşılaştırılır), sonra kurulum dosyası .part olarak iner;
    boyut (API size) ve SHA256 (SHA256SUMS.txt; API özeti varsa o da) tutarsa adı .exe olur. Döner: (dosya, sha256).
    Hata → UpdateError (network, disk, digest, missing, download, cancel); hatada yarım dosya kalmaz."""
    import requests

    cancel = cancel or threading.Event()
    if not release.installable:
        raise _err(_MSG_MISSING, f"{setup_name(release.version)} ya da {SUMS_NAME} sürümde yok.", "missing")
    setup, sums = release.setup, release.sums
    assert setup is not None and sums is not None
    for asset in (setup, sums):
        _check_asset(asset, release, source)
    folder = Path(folder) if folder is not None else UPDATE_DIR / release.version
    own = session is None
    s = requests.Session() if own else session
    try:
        _fresh_dir(folder)
        # 1) özet dosyası: yalnız bellekte okunur (boyutu küçük olmalı)
        sums_path = folder / SUMS_NAME
        if sums.size > MAX_SUMS_BYTES:
            raise _err(_MSG_DIGEST, f"{SUMS_NAME} beklenenden büyük ({sums.size} bayt).", "digest")
        if sums.digest:
            _fetch(s, sums, sums_path, sums.digest, source, None, cancel)
        else:
            _fetch_unverified(s, sums, sums_path, source, cancel)
        expected = parse_sums(sums_path.read_text(encoding="utf-8", errors="replace")).get(setup.name, "")
        if not expected:
            raise _err(_MSG_DIGEST, f"{SUMS_NAME} içinde {setup.name} için özet yok.", "digest")
        if setup.digest and setup.digest != expected:
            raise _err(_MSG_DIGEST, f"{SUMS_NAME} ile GitHub'ın dosya özeti uyuşmuyor ({expected} / {setup.digest}).",
                       "digest")
        # 2) kurulum dosyası
        target = folder / setup.name
        part = target.with_name(target.name + ".part")
        log.info("Güncelleme indiriliyor: %s (%d bayt)", setup.url, setup.size)
        _fetch(s, setup, part, expected, source, progress, cancel)
        os.replace(part, target)
        verify_file(target, setup.size, expected)
        log.info("Güncelleme doğrulandı: %s (SHA256 %s)", target, expected)
        return target, expected
    except UpdateError:
        raise
    except Exception as e:
        log.warning("Güncelleme indirilemedi", exc_info=True)
        raise _download_error(e) from e
    finally:
        if own:
            s.close()


def _fetch_unverified(session: requests.Session, asset: Asset, dest: Path, source: Source,
                      cancel: threading.Event) -> None:
    """Özeti bilinmeyen küçük dosya (API özeti vermediyse SHA256SUMS.txt): yalnız boyutu denetlenir."""
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
    r = _open(session, asset.url, source, headers, DOWNLOAD_TIMEOUT)
    try:
        if r.status_code != 200:
            raise _err(_MSG_DOWNLOAD, f"Sunucu {r.status_code} yanıtı verdi: {asset.url}", "download")
        if cancel.is_set():
            raise _err(_MSG_CANCEL, "", "cancel")
        data = _read_capped(r, MAX_SUMS_BYTES)
    except ValueError as e:
        raise _err(_MSG_DIGEST, _exc_text(e), "digest") from e
    finally:
        r.close()
    if len(data) != asset.size:
        raise _err(_MSG_DIGEST, f"{asset.name} {len(data)} bayt, beklenen {asset.size} bayt.", "digest")
    dest.write_bytes(data)


def cleanup_downloads(root: Path | None = None) -> None:
    """Önceki güncellemelerden kalan kurulum dosyalarını siler (açılıştan biraz sonra). Kullanımdaki dosya kalır."""
    root = Path(root) if root is not None else UPDATE_DIR
    if not root.is_dir():
        return
    for p in root.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            log.debug("Eski güncelleme dosyası silinemedi: %s", p, exc_info=True)


# --- kurulum türü --------------------------------------------------------------------------------------------------
def _registered_location(hive_name: str) -> str | None:
    """Robz Vision'ın Inno Setup kaldırma kaydındaki InstallLocation (yalnız okur); kayıt yoksa None."""
    if sys.platform != "win32":
        return None
    import winreg

    hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive_name]
    try:
        with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for name in ("InstallLocation", "Inno Setup: App Path"):
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if kind == winreg.REG_EXPAND_SZ:
                    value = winreg.ExpandEnvironmentStrings(value)
                if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and str(value).strip():
                    return str(value).strip().strip('"')
    except OSError:
        return None
    return None


def _same_dir(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(str(p))))  # noqa: E731
        return norm(a) == norm(b)


def install_kind(exe: str | Path | None = None, frozen: bool | None = None,
                 location: Callable[[str], str | None] = _registered_location) -> str:
    """Bu kopya kendini güncelleyebilir mi?

    "user": bu kullanıcıya kurulu (Inno Setup kaydı HKCU'da ve kurulum klasörü bu exe'nin klasörü): kendini günceller.
    "all_users": tüm kullanıcılar için kurulu (HKLM; yönetici gerekir): yeni sürüm elle kurulur.
    "portable": kayıt yok ya da başka bir klasörü gösteriyor (taşınabilir sürüm): yeni sürüm elle indirilir.
    "source": kaynaktan çalışıyor (sys.frozen yok): denetim yapılır, kurulum asla başlatılmaz."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return "source"
    folder = Path(exe if exe is not None else sys.executable).parent
    for hive, kind in (("HKCU", "user"), ("HKLM", "all_users")):
        loc = location(hive)
        if loc and _same_dir(loc, folder):
            return kind
    return "portable"


# --- ayar yedeği ---------------------------------------------------------------------------------------------------
def backup_settings(version: str = __version__, settings_file: Path | None = None,
                    keep: int = BACKUP_KEEP) -> Path | None:
    """ayarlar.json → ayarlar.yedek-<sürüm>.json (aynı klasörde); en yeni `keep` yedek kalır. Dosya yoksa None.
    Yedeklenemezse günlüğe yazar ve None döner (güncellemeyi durdurmaz: kurulum ayarlara zaten dokunmaz)."""
    if settings_file is None:
        from altyazitr import config  # testler config.SETTINGS_FILE'ı değiştirir

        settings_file = config.SETTINGS_FILE
    src = Path(settings_file)
    if not src.is_file():
        return None
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(version)) or "eski"
    dst = src.with_name(f"{src.stem}.yedek-{safe}.json")
    try:
        shutil.copyfile(src, dst)
        os.utime(dst)  # sıralama yedeğin alındığı ana göre
        backups = sorted(src.parent.glob(f"{src.stem}.yedek-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[max(1, keep):]:
            _unlink(old)
    except OSError:
        log.warning("Ayarlar yedeklenemedi: %s", dst, exc_info=True)
        return None
    log.info("Ayarlar yedeklendi: %s", dst)
    return dst


# --- kurulumu başlatma -------------------------------------------------------------------------------------------
def setup_command(setup: Path, pid: int, log_file: Path | None = None) -> list[str]:
    """Kurulum dosyasının komut satırı (liste: kabuk yok, her argüman ayrı). Günlük varsayılanı SETUP_LOG."""
    return [str(setup), *SETUP_FLAGS, f"/BEKLE={int(pid)}", f"/LOG={log_file or SETUP_LOG}"]


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Kuruluma (ve onun yeniden açacağı uygulamaya) geçecek ortam: PyInstaller'ın iç değişkenleri atılır, PATH
    açılıştaki hâline döner."""
    out = {k: v for k, v in (env if env is not None else os.environ).items()
           if not k.upper().startswith(("_PYI_", "_MEIPASS"))}
    out["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if _START_PATH:
        out["PATH"] = _START_PATH
    return out


def launch_setup(setup: Path, pid: int | None = None, log_file: Path | None = None,
                 popen: Callable[..., Any] | None = None) -> None:
    """Doğrulanmış kurulum dosyasını ayrık bir süreç olarak başlatır (beklemez). Başlatılamazsa UpdateError.

    Süreç bu uygulamadan bağımsız yaşar (DETACHED_PROCESS, yeni süreç grubu; izin varsa iş nesnesinden de kopar),
    çalışma klasörü kurulum dosyasının klasörüdür (kurulum klasörünü tutmasın). Günlük varsayılanı SETUP_LOG."""
    setup = Path(setup)
    log_file = Path(log_file or SETUP_LOG)
    popen = popen or subprocess.Popen
    args = setup_command(setup, os.getpid() if pid is None else pid, log_file)
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Kurulum günlüğü klasörü oluşturulamadı: %s", log_file, exc_info=True)
    base = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
              cwd=str(setup.parent), env=child_env())
    log.info("Güncelleme kuruluyor: %s", subprocess.list2cmdline(args))
    try:
        try:
            popen(args, creationflags=base | breakaway, **kw)
        except OSError:
            if not breakaway:
                raise
            # iş nesnesi kopmaya izin vermiyor (ERROR_ACCESS_DENIED): ayrık süreç yine yaşar
            popen(args, creationflags=base, **kw)
    except OSError as e:
        log.warning("Güncelleme kurulumu başlatılamadı", exc_info=True)
        code = getattr(e, "winerror", None)
        if code in _BLOCKED_WINERRORS:
            raise _err(_MSG_BLOCKED, f"{_exc_text(e)}. Kurulum dosyası: {setup}", "blocked") from e
        raise _err(_MSG_LAUNCH, _exc_text(e), "launch") from e
