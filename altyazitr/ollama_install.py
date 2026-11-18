r"""Ollama'nın Robz Vision içinden kurulması: resmî kurulum dosyasını indirir, doğrular ve sessizce kurar.

Ollama'nın kendi Windows betiğiyle aynı yol izlenir (github.com/ollama/ollama, scripts/install.ps1): OllamaSetup.exe
indirilir; Authenticode imzası geçerli olmalı ve imzalayan sertifikada O=Ollama Inc. yazmalıdır. Kurulumdan önce
%LOCALAPPDATA%\Ollama\upgraded işareti bırakılır; Ollama kurulumdan sonra bu işareti görür ve penceresini açmadan
gizli başlar. Kurulum /VERYSILENT /NORESTART /SUPPRESSMSGBOXES ile çalışır. Betikten farkı: sürüm sabittir, dosyanın
boyutu ve SHA256 özeti GitHub'daki sürüm kaydıyla karşılaştırılır, imza PowerShell yerine ctypes ile denetlenir.

Yönetici hakkı gerekmez (ollama.iss: PrivilegesRequired=lowest, kurulum %LOCALAPPDATA%\Programs\Ollama). Modül Qt
kullanmaz; uzun işler (indirme, doğrulama, kurulum) çağıranın arka plan iş parçacığında çalışır.

Hatalar SetupError'dır: 1. satır kullanıcıya gösterilecek kısa Türkçe metin, 2. satır "Ayrıntı: …".
"""

from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from altyazitr.paths import DATA_DIR, LOG_DIR

if TYPE_CHECKING:
    import requests

log = logging.getLogger(__name__)

# --- sabit sürüm --------------------------------------------------------------------------------------------------
# Her Robz Vision sürümünde güncellenir. Değerler GitHub'ın sürüm kaydından okunur (dosya inmez):
#   gh api repos/ollama/ollama/releases/tags/vX.Y.Z --jq '.assets[] | select(.name=="OllamaSetup.exe") | .digest, .size'
OLLAMA_VERSION = "0.40.16"
SETUP_URL = f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/OllamaSetup.exe"
SETUP_SHA256 = "84db4f756ddc9e091142a85cdc0f7ae5db68bc9b3e075f114388b326f8ed912d"
SETUP_BYTES = 1_857_663_944
SIGNER_ORG = "Ollama Inc."  # imzalayan sertifikanın O= değeri (install.ps1 ile aynı denetim)

# ollama.iss: AppId={{44E83376-CE68-45EB-8FC1-393500EB558C}; Inno Setup kaldırma kaydını "<AppId>_is1" adıyla yazar.
APP_GUID = "{44E83376-CE68-45EB-8FC1-393500EB558C}"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_GUID}_is1"
APP_EXE = "ollama app.exe"  # Ollama'nın tepsi uygulaması; sunucuyu o başlatır
DOWNLOAD_DIR = DATA_DIR / "indirilenler"  # Robz Vision kaldırılınca silinir (RobzVision.iss [UninstallDelete])
SETUP_LOG = LOG_DIR / "ollama-kurulum.log"  # run_setup'a verilecek varsayılan günlük
MANUAL_URL = "https://ollama.com/download"  # "Elle kur" bağlantısı
SETUP_FLAGS = ("/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES")
OUR_KEY = r"Software\RobzVision"  # HKCU altında; Robz Vision'ın kaldırıcısı Ollama'yı biz kurduysak sorar
INSTALLED_BY_US_VALUE = "OllamaKuruldu"
RETRY_DELAYS = (5.0, 15.0, 45.0)  # ağ hatasında bekleme (sn); ilk deneme + 3 yeniden deneme

_UNINSTALL_LOCATIONS = (  # aranma sırası (install.ps1 ile aynı)
    ("HKCU", UNINSTALL_KEY),
    ("HKLM", UNINSTALL_KEY),
    ("HKLM", UNINSTALL_KEY.replace("Software\\", "Software\\WOW6432Node\\", 1)),
)
_CHUNK = 256 * 1024  # küçük parça: iptal (duraklatma) yavaş bağlantıda da çabuk etki eder
_TIMEOUT = (15.0, 30.0)  # (bağlanma, okuma) sn; takılan bağlantı ağ hatası sayılır ve yeniden denenir
_PROGRESS_EVERY = 0.25  # ilerleme en çok bu aralıkla bildirilir (sn)
_RESET_AFTER = 1 << 20  # kesilen denemede en az bu kadar bayt indiyse deneme hakları yenilenir
_REPLACE_TRIES = 5  # .part → .exe adlandırması antivirüs taraması sürerken birkaç kez denenir
# Antivirüs ya da ilke engeli: virüslü/istenmeyen yazılım (225, 226), AppLocker/SRP (1260), WDAC (4551), erişim (5)
_BLOCKED_WINERRORS = {5, 225, 226, 1260, 4551}

_MSG_NETWORK = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer."
_MSG_DIGEST = "İndirilen Ollama dosyası bozuk çıktı ve silindi. Tekrar dene'ye basın."
_MSG_SIGNATURE = "Ollama dosyasının imzası doğrulanamadı; kurulmadı."
_MSG_DOWNLOAD = "Ollama indirilemedi."
_MSG_INSTALL = "Ollama kurulamadı."
_MSG_BLOCKED = "Ollama kurulumu engellendi."
_MSG_CANCEL = "İptal edildi."


class SetupError(RuntimeError):
    """Kullanıcıya gösterilecek Türkçe hata. str(e): 1. satır kısa metin, 2. satır "Ayrıntı: …".

    kind, arayüzün ve kurulum servisinin ne yapacağına karar vermesi içindir:
    "cancel" iptal (hata sayılmaz; .part kalır); "network" ağ (Tekrar dene kaldığı yerden sürer); "disk" yer yok;
    "digest" boyut ya da SHA256 tutmadı, dosya silindi; "signature" imza doğrulanamadı (elle kur);
    "blocked" antivirüs ya da şirket ilkesi (elle kur); "install" kurulum başarısız (çıkış kodu, zaman aşımı);
    "download" diğer indirme hataları (ör. HTTP 404); "" sınıflanmamış."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def short(self) -> str:
        """İlk satır: arayüzde görünen kısa metin."""
        return str(self).split("\n", 1)[0]

    @property
    def detail(self) -> str:
        """İkinci satır "Ayrıntı: " öneki olmadan (ipucu metni); yoksa boş."""
        rest = str(self).split("\n", 1)[1:]
        return rest[0].removeprefix("Ayrıntı: ") if rest else ""


@dataclass(frozen=True)
class Installed:
    """Kurulu Ollama. version kayıtta yoksa "", uninstall kaldırıcı bulunamazsa "" (kayıttaki gibi tırnaklı)."""

    app_exe: Path
    version: str
    uninstall: str


def _err(short: str, detail: str, kind: str) -> SetupError:
    return SetupError(f"{short}\nAyrıntı: {detail}", kind=kind)


def _exc_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".strip()


def _n(n: int) -> str:
    """1857663944 → "1.857.663.944"."""
    return f"{n:,}".replace(",", ".")


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f}".replace(".", ",") + " GB"


def _duration(seconds: float) -> str:
    return f"{seconds / 60:.0f} dakika" if seconds >= 120 else f"{seconds:.0f} saniye"


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Silinemedi: %s", path, exc_info=True)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# --- hata eşleme ---------------------------------------------------------------------------------------------
def error_from_os(exc: OSError) -> SetupError:
    """Kurulum dosyası çalıştırılırken (ya da açılırken) gelen OSError → Türkçe SetupError.

    WinError 225/226 (antivirüs), 1260 (AppLocker), 4551 (WDAC) ve 5 (erişim engellendi) "engellendi" metnini verir;
    ötekiler genel kurulum hatasıdır."""
    code = getattr(exc, "winerror", None)
    if code in _BLOCKED_WINERRORS:
        if code in (225, 226):
            why = "Antivirüs programınız kurulum dosyasını engelledi."
        elif code in (1260, 4551):
            why = "Bilgisayardaki şirket ilkesi (AppLocker, WDAC) kurulum dosyasının çalışmasına izin vermedi."
        else:
            why = "Erişim engellendi: antivirüs programınız ya da şirket ilkesi kurulum dosyasını durdurmuş olabilir."
        return _err(_MSG_BLOCKED, f"{why} Ollama'yı {MANUAL_URL} adresinden elle kurabilirsiniz. "
                                  f"(WinError {code}: {exc.strerror or exc})", "blocked")
    return _err(_MSG_INSTALL, _exc_text(exc), "install")


def _is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and (exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112))


def _download_error(exc: BaseException) -> SetupError:
    """İndirme ve doğrulama sırasındaki kalıcı (yeniden denemeye değmeyen) hata → SetupError."""
    if _is_disk_full(exc):
        drive = DOWNLOAD_DIR.drive
        head = f"Diskte yer kalmadı ({drive})." if drive else "Diskte yer kalmadı."
        return _err(f"{head} Yer açıp Tekrar dene'ye basın.",
                    f"Ollama kurulum dosyası için yaklaşık {_gb(SETUP_BYTES)} boş yer gerekir. {_exc_text(exc)}",
                    "disk")
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in _BLOCKED_WINERRORS:
        return error_from_os(exc)
    return _err(_MSG_DOWNLOAD, _exc_text(exc), "download")


def _cancelled() -> SetupError:
    return _err(_MSG_CANCEL, "Ollama indirmesi durduruldu; yeniden başlayınca kaldığı yerden sürer.", "cancel")


# --- indirme ---------------------------------------------------------------------------------------------------
class _Retry(Exception):
    """Geçici sorun (sunucu hatası, eksik akış): beklenip yeniden denenir."""


def _setup_file() -> Path:
    return DOWNLOAD_DIR / f"OllamaSetup-{OLLAMA_VERSION}.exe"


def _part_file(target: Path) -> Path:
    return target.with_name(target.name + ".part")


def _is_transient(exc: BaseException) -> bool:
    """Bekleyip yeniden denemeye değer mi: bağlantı, zaman aşımı, yarıda kopan akış, sunucu hatası."""
    import requests

    if isinstance(exc, _Retry):
        return True
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError, ConnectionError, TimeoutError, socket.gaierror))


def _content_range(value: str) -> tuple[int | None, int | None]:
    """"bytes 100-199/1000" → (100, 1000); toplam "*" ise (100, None); çözülemezse (None, None)."""
    m = re.fullmatch(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)\s*", value or "")
    if not m:
        return None, None
    return int(m.group(1)), (None if m.group(2) == "*" else int(m.group(2)))


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _reporter(progress: Callable[[int, int], None]) -> Callable[..., None]:
    """progress(done, total) çağrılarını seyreltir; geri çağırmanın hatası indirmeyi bozmaz."""
    last = -1e9

    def report(done: int, force: bool = False) -> None:
        nonlocal last
        now = time.monotonic()
        if not force and now - last < _PROGRESS_EVERY:
            return
        last = now
        try:
            progress(done, SETUP_BYTES)
        except Exception:
            log.debug("İndirme ilerlemesi bildirilemedi", exc_info=True)

    return report


def _fetch_once(session: requests.Session, part: Path, report: Callable[..., None], cancel: threading.Event) -> None:
    """Tek HTTP isteğiyle .part'ı tamamlamaya çalışır (varsa kaldığı yerden, Range ile).

    Geçici sorunlarda _Retry ya da requests'in ağ hatası, kalıcı sorunlarda SetupError fırlatır."""
    have = _size(part)
    if have > SETUP_BYTES:  # olmaması gereken bozuk parça
        _unlink(part)
        have = 0
    if have == SETUP_BYTES:  # önceki oturumda tamamlanmış, adı değiştirilmemiş
        return
    # Sıkıştırma istenmez: bayt konumları dosyadakiyle aynı kalmalı (Range ile sürdürme)
    headers = {"Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    r = session.get(SETUP_URL, headers=headers, stream=True, timeout=_TIMEOUT)
    try:
        code = r.status_code
        if code == 206:  # kısmi içerik: parça tam istediğimiz yerden başlamalı
            start, total = _content_range(r.headers.get("Content-Range", ""))
            if total is not None and total != SETUP_BYTES:
                _unlink(part)
                raise _err(_MSG_DIGEST, f"Sunucudaki dosya {_n(total)} bayt, beklenen {_n(SETUP_BYTES)} bayt.",
                           "digest")
            if start != have:
                _unlink(part)
                raise _Retry(f"beklenmeyen aralık {r.headers.get('Content-Range')!r} (istenen {have}-)")
            mode = "ab"
        elif code == 200:  # sunucu Range'i yok saydı ya da ilk istek: baştan yazılır
            length = _to_int(r.headers.get("Content-Length"))
            if length is not None and length != SETUP_BYTES:
                _unlink(part)
                raise _err(_MSG_DIGEST, f"Sunucudaki dosya {_n(length)} bayt, beklenen {_n(SETUP_BYTES)} bayt.",
                           "digest")
            have, mode = 0, "wb"
        elif code == 416:  # istenen aralık dosyanın dışında: parça güvenilmez
            _unlink(part)
            raise _Retry("HTTP 416")
        elif code in (408, 425, 429) or code >= 500:
            raise _Retry(f"HTTP {code}")
        else:
            raise _err(_MSG_DOWNLOAD, f"Sunucu {code} yanıtı verdi: {SETUP_URL}", "download")
        if cancel.is_set():
            raise _cancelled()
        report(have, True)
        too_long = False
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=_CHUNK):
                if cancel.is_set():
                    raise _cancelled()
                if not chunk:
                    continue
                if have + len(chunk) > SETUP_BYTES:
                    too_long = True
                    break
                f.write(chunk)
                have += len(chunk)
                report(have)
    finally:
        r.close()
    if too_long:
        _unlink(part)
        raise _err(_MSG_DIGEST, f"Sunucu {_n(SETUP_BYTES)} bayttan uzun veri gönderdi.", "digest")
    if have < SETUP_BYTES:
        raise _Retry(f"bağlantı erken kapandı: {_n(have)} / {_n(SETUP_BYTES)} bayt")


def _download_loop(session: requests.Session, part: Path, report: Callable[..., None],
                   cancel: threading.Event) -> None:
    """_fetch_once'ı ağ hatalarında RETRY_DELAYS aralıklarıyla yeniden dener (her deneme kaldığı yerden)."""
    failures = 0
    while True:
        if cancel.is_set():
            raise _cancelled()
        before = _size(part)
        try:
            _fetch_once(session, part, report, cancel)
            return
        except SetupError:
            raise
        except Exception as e:
            if not _is_transient(e):
                log.warning("Ollama kurulum dosyası indirilemedi", exc_info=True)
                raise _download_error(e) from e
            if _size(part) - before >= _RESET_AFTER:
                failures = 0  # bağlantı gidip geliyor ama indirme ilerliyor
            if failures >= len(RETRY_DELAYS):
                log.warning("Ollama kurulum dosyası indirilemedi; denemeler bitti", exc_info=True)
                raise _err(_MSG_NETWORK, f"Son hata: {_exc_text(e)}", "network") from e
            delay = RETRY_DELAYS[failures]
            failures += 1
            log.info("Ollama indirmesi kesildi (%s); %.0f sn sonra yeniden denenecek (%d/%d)",
                     _exc_text(e), delay, failures, len(RETRY_DELAYS))
            if cancel.wait(delay):
                raise _cancelled() from None


def _replace(src: Path, dst: Path) -> None:
    """os.replace; antivirüs yeni .exe'yi tararken kısa süre kilitleyebildiği için birkaç kez dener."""
    for i in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.4)


def _remove_stale(target: Path) -> None:
    """Önceki Robz Vision sürümlerinin indirdiği başka Ollama sürümlerini (ve yarım .part'larını) siler."""
    keep = {target.name, _part_file(target).name}
    for p in DOWNLOAD_DIR.glob("OllamaSetup*"):
        if p.name not in keep and p.is_file():
            log.info("Eski Ollama kurulum dosyası siliniyor: %s", p)
            _unlink(p)


def download_setup(progress: Callable[[int, int], None], cancel: threading.Event,
                   session: requests.Session | None = None) -> Path:
    """OllamaSetup.exe'yi DOWNLOAD_DIR'e indirir, doğrular (verify_setup) ve yolunu döndürür.

    * Önceki indirme doğrulanmış duruyorsa yeniden inmez. Yarım indirme `.part` dosyasında kalır ve HTTP Range ile
      kaldığı yerden sürer (206); sunucu Range'i yok sayarsa (200) baştan yazılır.
    * progress(done, total): bayt; total = SETUP_BYTES. Başta (varsa .part boyutuyla) ve sonda mutlaka çağrılır.
    * cancel kurulunca SetupError(kind="cancel") fırlar, .part silinmez (duraklatma = iptal + yeniden başlatma).
      İptal bir sonraki 256 KB'lık parçada etki eder; bağlantı takılmışsa okuma zaman aşımına (30 sn) kadar sürebilir.
    * Ağ hatasında RETRY_DELAYS (5, 15, 45 sn) arayla 3 kez yeniden dener; indirme ilerledikçe haklar yenilenir.
    * session verilmezse kendi requests.Session'ını açıp kapatır; verilen oturum kapatılmaz.
    * Hata → SetupError (kind: network, disk, digest, signature, blocked, download, cancel)."""
    import requests

    target = _setup_file()
    part = _part_file(target)
    report = _reporter(progress)
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _download_error(e) from e
    _remove_stale(target)
    if target.is_file():
        try:
            verify_setup(target)
        except SetupError as e:
            if e.kind != "digest":  # imza ya da erişim sorunu yeniden indirmekle düzelmez
                raise
            log.warning("Önceki Ollama kurulum dosyası bozuk çıktı; yeniden indiriliyor")
        else:
            report(SETUP_BYTES, True)
            return target
    report(_size(part), True)
    log.info("Ollama %s kurulum dosyası indiriliyor (%s / %s bayt hazır)", OLLAMA_VERSION, _n(_size(part)),
             _n(SETUP_BYTES))
    own = session is None
    s = requests.Session() if own else session
    try:
        _download_loop(s, part, report, cancel)
    finally:
        if own:
            s.close()
    try:
        _replace(part, target)
    except OSError as e:
        raise _download_error(e) from e
    report(SETUP_BYTES, True)
    verify_setup(target)
    return target


def discard_download() -> None:
    """İndirilen kurulum dosyasını ve yarım .part'ı siler (ör. kullanıcı yapay zekâ kurulumunu iptal edince).
    İndirme sürerken çağrılmamalı: Windows açık .part'ı silmez (günlüğe not düşülür)."""
    target = _setup_file()
    for p in (target, _part_file(target)):
        _unlink(p)


# --- doğrulama -------------------------------------------------------------------------------------------------
def verify_setup(path: Path) -> None:
    """Boyut + SHA256 + Authenticode (imzalayan O=Ollama Inc.). Tutmazsa dosyayı siler ve SetupError fırlatır.

    Boyut ya da özet tutmazsa kind="digest", imza tutmazsa kind="signature". SHA256 1,9 GB'ı okur (birkaç sn)."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as e:
        raise _err("İndirilen Ollama dosyası bulunamadı. Tekrar dene'ye basın.", _exc_text(e), "digest") from e
    if size != SETUP_BYTES:
        _unlink(path)
        raise _err(_MSG_DIGEST, f"Dosya {_n(size)} bayt, beklenen {_n(SETUP_BYTES)} bayt.", "digest")
    try:
        with open(path, "rb") as f:
            got = hashlib.file_digest(f, "sha256").hexdigest()
    except OSError as e:
        raise _download_error(e) from e
    if got != SETUP_SHA256.lower():
        _unlink(path)
        raise _err(_MSG_DIGEST, f"SHA256 {got}, beklenen {SETUP_SHA256}.", "digest")
    org = signer_org(path)
    if org != SIGNER_ORG:
        _unlink(path)
        why = f"İmzalayan: {org} (beklenen: {SIGNER_ORG})." if org else "Geçerli bir Authenticode imzası bulunamadı."
        raise _err(_MSG_SIGNATURE, f"{why} Ollama'yı {MANUAL_URL} adresinden elle kurabilirsiniz.", "signature")
    log.info("Ollama kurulum dosyası doğrulandı: %s (SHA256 ve imza: %s)", path, org)


# --- Authenticode (ctypes; PowerShell çağrılmaz) ----------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR), ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(_GUID))]


class _WintrustData(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WintrustFileInfo)),  # birleşimin WTD_CHOICE_FILE üyesi
                ("dwStateAction", wintypes.DWORD), ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPWSTR), ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD), ("pSignatureSettings", ctypes.c_void_p)]


_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0  # Get-AuthenticodeSignature (install.ps1) ile aynı: iptal denetimi Windows ilkesine kalır
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2
_CERT_QUERY_OBJECT_FILE = 1
_CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
_CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
_CMSG_SIGNER_CERT_INFO_PARAM = 7  # imzalayanın Issuer + SerialNumber'ı (CERT_INFO)
_X509_PKCS7_ENCODING = 0x00000001 | 0x00010000
_CERT_FIND_SUBJECT_CERT = 11 << 16
_CERT_NAME_ATTR_TYPE = 3
_OID_ORGANIZATION = b"2.5.4.10"  # szOID_ORGANIZATION_NAME (O=)


def _verify_v2_guid() -> _GUID:
    """WINTRUST_ACTION_GENERIC_VERIFY_V2 = {00AAC56B-CD44-11D0-8CC2-00C04FC295EE} (Authenticode)."""
    return _GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


@functools.cache
def _crypt_api():
    """wintrust.dll ve crypt32.dll işlevleri, tip bilgileriyle (yalnız bu modülün kopyası)."""
    wintrust = ctypes.WinDLL("wintrust")
    crypt32 = ctypes.WinDLL("crypt32")
    vp, dw = ctypes.c_void_p, wintypes.DWORD
    wintrust.WinVerifyTrust.argtypes = (wintypes.HWND, ctypes.POINTER(_GUID), vp)
    wintrust.WinVerifyTrust.restype = wintypes.LONG
    crypt32.CryptQueryObject.argtypes = (dw, wintypes.LPCWSTR, dw, dw, dw, ctypes.POINTER(dw), ctypes.POINTER(dw),
                                         ctypes.POINTER(dw), ctypes.POINTER(vp), ctypes.POINTER(vp), vp)
    crypt32.CryptQueryObject.restype = wintypes.BOOL
    crypt32.CryptMsgGetParam.argtypes = (vp, dw, dw, vp, ctypes.POINTER(dw))
    crypt32.CryptMsgGetParam.restype = wintypes.BOOL
    crypt32.CertFindCertificateInStore.argtypes = (vp, dw, dw, dw, vp, vp)
    crypt32.CertFindCertificateInStore.restype = vp
    crypt32.CertGetNameStringW.argtypes = (vp, dw, dw, ctypes.c_char_p, wintypes.LPWSTR, dw)
    crypt32.CertGetNameStringW.restype = dw
    crypt32.CertFreeCertificateContext.argtypes = (vp,)
    crypt32.CertFreeCertificateContext.restype = wintypes.BOOL
    crypt32.CertCloseStore.argtypes = (vp, dw)
    crypt32.CertCloseStore.restype = wintypes.BOOL
    crypt32.CryptMsgClose.argtypes = (vp,)
    crypt32.CryptMsgClose.restype = wintypes.BOOL
    return wintrust, crypt32


def _trusted(wintrust, path: str) -> bool:
    """WinVerifyTrust: dosyanın gömülü Authenticode imzası geçerli ve güvenilir bir köke bağlı mı?"""
    info = _WintrustFileInfo(ctypes.sizeof(_WintrustFileInfo), path, None, None)
    data = _WintrustData()
    data.cbStruct = ctypes.sizeof(_WintrustData)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_NONE
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(info)
    data.dwStateAction = _WTD_STATEACTION_VERIFY
    action = _verify_v2_guid()
    no_user = wintypes.HWND(-1)  # INVALID_HANDLE_VALUE: etkileşimli kullanıcı yok, asla pencere açılmaz
    res = wintrust.WinVerifyTrust(no_user, ctypes.byref(action), ctypes.byref(data))
    data.dwStateAction = _WTD_STATEACTION_CLOSE  # doğrulamanın ayırdığı durumu bırak
    wintrust.WinVerifyTrust(no_user, ctypes.byref(action), ctypes.byref(data))
    if res != 0:
        log.info("Authenticode imzası geçersiz ya da yok: %s (WinVerifyTrust 0x%08X)", path, res & 0xFFFFFFFF)
        return False
    return True


def _signer_organization(crypt32, path: str) -> str | None:
    """Gömülü imzanın imzalayan sertifikasındaki O= değeri (CryptQueryObject → CryptMsgGetParam →
    CertFindCertificateInStore → CertGetNameStringW)."""
    store, msg = ctypes.c_void_p(), ctypes.c_void_p()
    enc, content, fmt = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not crypt32.CryptQueryObject(_CERT_QUERY_OBJECT_FILE, path, _CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
                                    _CERT_QUERY_FORMAT_FLAG_BINARY, 0, ctypes.byref(enc), ctypes.byref(content),
                                    ctypes.byref(fmt), ctypes.byref(store), ctypes.byref(msg), None):
        return None
    cert = None
    try:
        size = wintypes.DWORD(0)
        ok = crypt32.CryptMsgGetParam(msg, _CMSG_SIGNER_CERT_INFO_PARAM, 0, None, ctypes.byref(size))
        if not ok or not size.value:
            return None
        info = ctypes.create_string_buffer(size.value)  # CERT_INFO (yalnız Issuer ve SerialNumber dolu)
        if not crypt32.CryptMsgGetParam(msg, _CMSG_SIGNER_CERT_INFO_PARAM, 0, info, ctypes.byref(size)):
            return None
        cert = crypt32.CertFindCertificateInStore(store, _X509_PKCS7_ENCODING, 0, _CERT_FIND_SUBJECT_CERT, info, None)
        if not cert:
            return None
        n = crypt32.CertGetNameStringW(cert, _CERT_NAME_ATTR_TYPE, 0, _OID_ORGANIZATION, None, 0)
        if n <= 1:  # yalnız sondaki NUL: sertifikada O= yok
            return None
        buf = ctypes.create_unicode_buffer(n)
        crypt32.CertGetNameStringW(cert, _CERT_NAME_ATTR_TYPE, 0, _OID_ORGANIZATION, buf, n)
        return buf.value or None
    finally:
        if cert:
            crypt32.CertFreeCertificateContext(cert)
        if store:
            crypt32.CertCloseStore(store, 0)
        if msg:
            crypt32.CryptMsgClose(msg)


def signer_org(path: Path) -> str | None:
    """Dosyanın Authenticode imzası geçerliyse imzalayan sertifikadaki O= değeri; imzasız, geçersiz ya da
    okunamıyorsa None. Hata fırlatmaz. Ağ erişimi olursa Windows sertifika iptal listesine de bakabilir."""
    if sys.platform != "win32":
        return None
    p = str(Path(path).absolute())
    try:
        wintrust, crypt32 = _crypt_api()
        if not _trusted(wintrust, p):
            return None
        return _signer_organization(crypt32, p)
    except Exception:  # ctypes/OS hatası doğrulamayı geçirmez
        log.warning("İmza denetlenemedi: %s", p, exc_info=True)
        return None


# --- kurulum ---------------------------------------------------------------------------------------------------
def _make_upgrade_marker() -> None:
    """%LOCALAPPDATA%\\Ollama\\upgraded: Ollama kurulumdan sonra ilk açılışta penceresini göstermez, işareti siler
    (app/cmd/app/app.go). Oluşturulamazsa kurulum yine sürer; yalnız Ollama penceresi açılabilir."""
    marker = _local_appdata() / "Ollama" / "upgraded"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
    except OSError:
        log.warning("Ollama işaret dosyası oluşturulamadı: %s", marker, exc_info=True)


def run_setup(path: Path, log_file: Path, timeout: float = 900.0) -> None:
    """Doğrulanmış kurulum dosyasını sessiz çalıştırır ve yalnız kurulum sürecini bekler.

    Önce %LOCALAPPDATA%\\Ollama\\upgraded işaretini bırakır. Inno Setup bitince `ollama app.exe`'yi kendisi başlatır;
    o süreç beklenmez ve çıktı borusu kullanılmaz (boru Ollama'ya miras kalıp beklemeyi kilitlerdi). Başarıda
    kurulum dosyası silinir; başarısızlıkta "Tekrar dene" yeniden indirmesin diye kalır. Süre dolarsa kurulum
    öldürülmez (yarım kurulum Ollama'yı bozabilir). Hata → SetupError (kind "install" ya da "blocked")."""
    path, log_file = Path(path), Path(log_file)
    _make_upgrade_marker()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Kurulum günlüğü klasörü oluşturulamadı: %s", log_file.parent, exc_info=True)
    args = [str(path), *SETUP_FLAGS, f"/LOG={log_file}"]
    log.info("Ollama kuruluyor: %s", subprocess.list2cmdline(args))
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("Ollama kurulumu başlatılamadı", exc_info=True)
        raise error_from_os(e) from e
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        log.warning("Ollama kurulumu %s içinde bitmedi", _duration(timeout))
        raise _err(_MSG_INSTALL, f"Kurulum {_duration(timeout)} içinde bitmedi. Günlük: {log_file}",
                   "install") from e
    if code != 0:
        log.warning("Ollama kurulumu %s koduyla bitti; günlük: %s", code, log_file)
        raise _err(_MSG_INSTALL, f"Kurulum {code} koduyla bitti. Günlük: {log_file}", "install")
    log.info("Ollama kuruldu")
    _unlink(path)


# --- kayıt defteri ---------------------------------------------------------------------------------------------
_UNINSTALL_VALUES = ("InstallLocation", "Inno Setup: App Path", "DisplayVersion", "UninstallString")


def _hive(name: str):
    import winreg

    return {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[name]


def _reg_read(hive: str, subkey: str) -> dict[str, str] | None:
    """Kaldırma kaydının metin değerleri; anahtar yoksa None. Yalnız okur (64 bit görünüm)."""
    import winreg

    try:
        key = winreg.OpenKey(_hive(hive), subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None
    out: dict[str, str] = {}
    with key:
        for name in _UNINSTALL_VALUES:
            try:
                value, kind = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            if kind == winreg.REG_EXPAND_SZ:
                value = winreg.ExpandEnvironmentStrings(value)
            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                out[name] = value
    return out


def _reg_set_dword(hive: str, subkey: str, name: str, value: int) -> None:
    import winreg

    with winreg.CreateKeyEx(_hive(hive), subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))


def find_installed() -> Installed | None:
    """Kurulu Ollama'yı bulur: HKCU → HKLM → HKLM\\…\\WOW6432Node kaldırma kaydı (AppId), sonra varsayılan klasör
    %LOCALAPPDATA%\\Programs\\Ollama. Kayıt olup `ollama app.exe` yoksa (klasör elle silinmiş) o kayıt atlanır.
    Yalnız okur; Ollama'yı başlatmaz."""
    for hive, key in _UNINSTALL_LOCATIONS:
        values = _reg_read(hive, key)
        if not values:
            continue
        loc = (values.get("InstallLocation") or values.get("Inno Setup: App Path") or "").strip().strip('"')
        if not loc:
            continue
        exe = Path(loc) / APP_EXE
        if exe.is_file():
            return Installed(exe, values.get("DisplayVersion", "").strip(), values.get("UninstallString", "").strip())
        log.info("Ollama kaydı var ama %s yok (%s\\%s)", exe, hive, key)
    exe = _local_appdata() / "Programs" / "Ollama" / APP_EXE
    if exe.is_file():
        unins = exe.parent / "unins000.exe"
        return Installed(exe, "", f'"{unins}"' if unins.is_file() else "")
    return None


def mark_installed_by_us() -> None:
    """HKCU\\Software\\RobzVision altına OllamaKuruldu=1 (DWORD) yazar: Robz Vision'ın kaldırıcısı Ollama'yı da
    kaldırmayı yalnız onu biz kurduysak sorar. Yazılamazsa günlüğe not düşer, hata fırlatmaz."""
    try:
        _reg_set_dword("HKCU", OUR_KEY, INSTALLED_BY_US_VALUE, 1)
    except OSError:
        log.warning("Ollama kurulum işareti kayıt defterine yazılamadı", exc_info=True)
