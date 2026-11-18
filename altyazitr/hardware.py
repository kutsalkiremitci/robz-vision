"""Donanım algılama ve ilk açılıştaki yapay zekâ önerisi.

Ölçülenler: ekran kartları (DXGI; NVIDIA'da nvidia-smi ile sürücü sürümü ve kesin bellek; DXGI okunamazsa kayıt
defteri), sistem belleği (GlobalMemoryStatusEx), boş disk alanı, pil durumu (GetSystemPowerStatus) ve Windows
derleme numarası (RtlGetVersion). Yalnız standart kitaplık ve ctypes kullanılır; Qt yok.

``advise()`` ölçümleri ilk ekrandaki öneriye çevirir: kademe (none, weak, edge, mid, good, strong, unknown),
"Yapay zekâ ile" seçeneğinin sunulup sunulmayacağı ve önceden seçili gelip gelmeyeceği, model (gemma4:12b ya da
gemma4:e4b) ve arayüzde görünen kısa Türkçe metinler. Saf ve tablo güdümlüdür: G/Ç yapmaz.

Birimler: bellek ve disk değerleri bayttır (GiB = 2**30). Ölçüm fonksiyonları hata fırlatmaz: sorunlar günlüğe
yazılır, değer okunamazsa None (ya da boş liste) döner. ``dxgi_adapters()`` ve ``registry_gpus()`` bu kuralın
dışındadır: okuyamazlarsa OSError fırlatırlar; ``list_gpus()`` bunları yakalayıp yedeklere geçer.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)

GIB = 1 << 30
MIB = 1 << 20

VENDOR_NVIDIA = "NVIDIA"
VENDOR_AMD = "AMD"
VENDOR_INTEL = "Intel"
VENDOR_QUALCOMM = "Qualcomm"
VENDOR_MICROSOFT = "Microsoft"
_VENDOR_IDS = {
    0x10DE: VENDOR_NVIDIA,
    0x1002: VENDOR_AMD,
    0x1022: VENDOR_AMD,
    0x8086: VENDOR_INTEL,
    0x8087: VENDOR_INTEL,
    0x5143: VENDOR_QUALCOMM,
    0x4D4F4351: VENDOR_QUALCOMM,  # "QCOM" (ACPI kimliği, Snapdragon)
    0x1414: VENDOR_MICROSOFT,  # Microsoft Basic Render/Display, Uzak Masaüstü bağdaştırıcısı
}
_VENDOR_RANK = {VENDOR_NVIDIA: 3, VENDOR_AMD: 2, VENDOR_INTEL: 1}  # eşit bellekte tercih sırası

MODEL_FULL = "gemma4:12b"
MODEL_LIGHT = "gemma4:e4b"

MIN_WIN_BUILD = 19045  # Windows 10 22H2; Ollama daha eskisinde desteklenmiyor
MIN_RAM_PRESELECT = int(11.5 * GIB)  # "12 GiB" toleranslı: 12 GB'lık ya da iGPU'su bellek ayıran 16 GB'lık makine
NVIDIA_MIN_DRIVER = (551, 61)
NVIDIA_MIN_DRIVER_OLD_ARCH = (570, 0)  # GTX 9xx/10xx (Maxwell, Pascal)


@dataclass(frozen=True)
class Gpu:
    """Bir ekran kartı. ``vram``: ayrık bellek (bayt; 0 = okunamadı), ``shared``: paylaşılan sistem belleği (bayt),
    ``vendor``: "NVIDIA" | "AMD" | "Intel" | "Qualcomm" | bilinmeyende "0x1234" biçiminde PCI üretici kimliği,
    ``driver``: NVIDIA sürücü sürümü (ör. "621.14"; yalnız nvidia-smi okunabildiyse)."""

    name: str
    vendor: str
    vram: int
    shared: int
    integrated: bool
    driver: str = ""


#: DXGI ve yedekler hiçbir kart okuyamadığında list_gpus()'un döndürdüğü tek kayıt: adı ve belleği bilinmeyen kart.
#: advise() bunu "unknown" kademesine çevirir ("Ekran kartı belirlenemedi"; sunulur, önceden seçili gelmez).
UNKNOWN_GPU = Gpu(name="", vendor="", vram=0, shared=0, integrated=False)


@dataclass(frozen=True)
class Advice:
    """İlk ekrandaki öneri.

    ``tier``: none | weak | edge | mid | good | strong | unknown. ``offer``: "Yapay zekâ ile" seçilebilir mi;
    ``preselect``: önceden seçili mi (offer=False ise hep False). ``model``: önerilen Ollama modeli (sunulmuyorsa
    None). ``gpu``: önerinin dayandığı kart (yoksa ya da hiç okunamadıysa None).
    Metinler arayüz içindir (simgeyi arayüz ekler): ``line`` donanım satırı (her zaman dolu), ``warn`` turuncu
    uyarı (yoksa boş; birden çoksa satır satır), ``tip`` açıklama/ipucu."""

    tier: str
    offer: bool
    preselect: bool
    model: str | None
    gpu: Gpu | None
    line: str
    warn: str
    tip: str


# --- öneri tablosu --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Row:
    offer: bool
    preselect: bool
    model: str | None
    tip: str


_TIP_NO_GPU = ("Yapay zekâ çevirisi için en az 8 GB belleği olan bir NVIDIA ya da AMD ekran kartı gerekir. "
               "Bu bilgisayarda hızlı çeviri daha iyi sonuç verir.")

#: Kademe → ilk ekran davranışı (belgedeki öneri tablosu). Düzeltmeler advise() içinde bu satırların üstüne gelir.
TIERS: dict[str, _Row] = {
    "none": _Row(False, False, None, _TIP_NO_GPU),
    "weak": _Row(False, False, None, _TIP_NO_GPU),
    "edge": _Row(True, False, MODEL_LIGHT,
                 "Bu kartta hafif model (Gemma 4 E4B) kurulur. Oyun ve OBS de ekran kartını kullandığı için "
                 "çeviri gecikebilir; hızlı çeviri yedekte kalır."),
    "mid": _Row(True, True, MODEL_LIGHT, "Bu kart için hafif model önerilir: Gemma 4 E4B (6,6 GB)."),
    "good": _Row(True, True, MODEL_FULL,
                 "Gemma 4 12B bu karta sığar. Oyun da bu bilgisayarda çalışıyorsa hafif model (E4B) önerilir."),
    "strong": _Row(True, True, MODEL_FULL, "Gemma 4 12B bu karta rahatça sığar."),
    "unknown": _Row(True, False, MODEL_LIGHT,
                    "Ekran kartı bilgisi okunamadı. Yapay zekâyı yine de kurabilirsiniz; Ollama ekran kartını "
                    "kullanamazsa model indirilmez."),
}
#: Bellek kademeleri: (alt sınır GiB, kademe), büyükten küçüğe; en alttakinin altı "weak". Eşikler toleranslı
#: (12 GB'lık kart 11,7-12,0 GiB, 8 GB'lık 7,8-8,0 GiB raporlar).
VRAM_STEPS: tuple[tuple[float, str], ...] = ((15.0, "strong"), (10.5, "good"), (7.5, "mid"), (5.5, "edge"))
_ORDER = ("weak", "edge", "mid", "good", "strong")

_TEXT_NO_GPU = "Uygun ekran kartı yok"
_TEXT_UNKNOWN = "Ekran kartı belirlenemedi"
_TEXT_OLD_WINDOWS = "Windows sürümü eski"
_TIP_OLD_WINDOWS = ("Yapay zekâ çevirisi Windows 10 22H2 ya da daha yeni bir sürüm ister. Windows Update ile "
                    "güncelleyebilirsiniz.")
_TIP_OLD_CARD = "Bu ekran kartı yapay zekâ çevirisi için eski. Bu bilgisayarda hızlı çeviri daha iyi sonuç verir."
_WARN_EDGE = "{gb} ekran kartı: yayın açıkken gecikebilir"
_WARN_UNTESTED = "Bu kartta denenmedi"
_TIP_UNTESTED = ("Robz Vision bu kartta denenmedi. Yapay zekâyı yine de kurabilirsiniz; Ollama ekran kartını "
                 "kullanamazsa model indirilmez.")
_WARN_OLD_DRIVER = "Ekran kartı sürücüsü eski"
_TIP_OLD_DRIVER = "Sürücü {have}; yapay zekâ için en az {need} gerekir. NVIDIA App ya da nvidia.com ile güncelleyin."
_WARN_LOW_RAM = "Sistem belleği {gb}: yapay zekâ bilgisayarı yavaşlatabilir"

# NVIDIA kuşakları (ada göre). "old": GTX 9xx ve öncesi → weak. "pascal" (GTX 10xx) ve "turing16" (GTX 16xx,
# T serisi): tensör çekirdeği yok → bir kademe aşağı. Eski kuşaklarda (old, pascal) sürücü eşiği 570.
_NV_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("old", re.compile(r"\bGTX?\s*\d{3}(?!\d)|\bGTX\s+TITAN\b|\b(?:QUADRO|TESLA)\s+[KM]\d")),
    ("pascal", re.compile(r"\bGTX?\s*10\d{2}\b|\bTITAN\s+X(?:P\b|\s*\(PASCAL\))|\b(?:QUADRO|TESLA)\s+P\d")),
    ("turing16", re.compile(r"\bGTX\s*16\d{2}\b|\b(?:QUADRO\s+)?T\d{3,4}\b")),
)
_NV_STEP_DOWN = frozenset({"pascal", "turing16"})
_NV_OLD_DRIVER_ARCH = frozenset({"old", "pascal"})

# Tümleşik kart ayrımı ada göre (AMD APU'lar BIOS'ta ayrılan 8-16 GB UMA'yı ayrık bellek gibi raporlar).
# AMD: "RX ####", "PRO W####" ve birkaç eski/özel ad harici; gerisi (Radeon Graphics, 780M, 8060S, RX Vega 11) tümleşik.
_AMD_DISCRETE = re.compile(
    r"\bRX\s*\d{3,4}|\bPRO\s+WX?\s*\d{4}|\bAI\s+PRO\s+R\d{4}|\bR[579]\s+(?:\d{3}|FURY)"
    r"|\bRX\s+VEGA\s+(?:56|64)\b|\bRADEON\s+VII\b|\bVEGA\s+FRONTIER\b")
# RX 6000 ve sonrası (dizüstü sonekleri dahil: RX 6800M, RX 7600S), PRO W6000/W7000, AI PRO R9700
_AMD_TESTED = re.compile(r"\bRX\s*[6-9]\d{3}(?!\d)|\bPRO\s+W[6-9]\d{3}(?!\d)|\bAI\s+PRO\s+R\d{4}(?!\d)")
# Intel: harici Arc "Arc A770 / A370M / Pro B60 … Graphics"; Core Ultra tümleşikleri "Arc Graphics", "Arc 140V GPU",
# "Arc B390 GPU" (adında "GPU" geçer).
_INTEL_DISCRETE = re.compile(r"\bARC\s+(?:PRO\s+)?[AB]\d{2,3}[A-Z]?\b")
_INTEL_IGPU_HINT = re.compile(r"\bGPU\b")
_SMALL_VRAM = 2 * GIB  # harici Arc'ın en küçüğü 4 GB; bundan azını raporlayan Intel kartı tümleşiktir
_TRADEMARKS = re.compile(r"\((?:R|TM)\)|[®™]", re.I)


# --- ekran kartları ----------------------------------------------------------------------------------------------
def vendor_name(vendor_id: int) -> str:
    """PCI üretici kimliği → kısa ad ("NVIDIA", "AMD", "Intel", …); bilinmeyende "0x1ED5" biçimi."""
    return _VENDOR_IDS.get(vendor_id, f"0x{vendor_id:04X}")


def display_name(name: str) -> str:
    """Arayüz için sade kart adı: "(R)", "(TM)" işaretleri ve fazla boşluklar atılır.
    "Intel(R) Arc(TM) A770 Graphics" → "Intel Arc A770 Graphics"."""
    return " ".join(_TRADEMARKS.sub(" ", name).split())


def is_integrated(vendor: str, name: str, vram: int) -> bool:
    """Kart işlemciye tümleşik mi? Belleğe göre değil ADA göre karar verilir (AMD APU'ları BIOS'taki 8-16 GB UMA'yı
    ayrık bellek gibi raporlar). Intel: adında "Arc A###/B###" yoksa tümleşik ("Intel Arc Graphics" Core Ultra'nın
    tümleşiğidir). AMD: adında "RX ####" ya da "PRO W####" yoksa tümleşik. NVIDIA hep harici.

    Bellek yalnız tek yönde, yedek olarak kullanılır: 2 GiB'tan az ayrık bellek raporlayan Intel kartı ve üreticisi
    bilinmeyen kart tümleşik sayılır (hiçbir harici Arc 4 GB'tan küçük değil). ``vram`` = 0 "bilinmiyor" demektir."""
    n = display_name(name).upper()
    if vendor == VENDOR_NVIDIA:
        return False
    if vendor == VENDOR_AMD:
        return not _AMD_DISCRETE.search(n)
    if vendor == VENDOR_INTEL:
        if 0 < vram < _SMALL_VRAM:
            return True
        return not _INTEL_DISCRETE.search(n) or bool(_INTEL_IGPU_HINT.search(n))
    if vendor == VENDOR_QUALCOMM:
        return True
    return 0 < vram < _SMALL_VRAM


def best_gpu(gpus: Sequence[Gpu]) -> Gpu | None:
    """Önerinin dayandığı kart: ayrık belleği en çok olan HARİCİ kart (Optimus dizüstüde NVIDIA, tümleşik değil);
    eşit bellekte NVIDIA > AMD > Intel. Harici kart yoksa belleği en çok tümleşik kart (arayüzde adı gösterilsin
    diye; advise() bunu "none" sayar). Liste boşsa None."""
    if not gpus:
        return None
    return max(gpus, key=lambda g: (not g.integrated, g.vram, _VENDOR_RANK.get(g.vendor, 0)))


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):  # noqa: N801 (Windows SDK adı)
    """https://learn.microsoft.com/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1 (64 bit'te 312 bayt)."""

    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint32),
        ("DeviceId", ctypes.c_uint32),
        ("SubSysId", ctypes.c_uint32),
        ("Revision", ctypes.c_uint32),
        ("DedicatedVideoMemory", ctypes.c_size_t),  # SIZE_T, 64 bit süreçte 64 bit: WMI AdapterRAM gibi 4 GB'ta kalmaz
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint32),
    ]


_IID_IDXGIFactory1 = _GUID.from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
_DXGI_ERROR_NOT_FOUND = 0x887A0002
_DXGI_ADAPTER_FLAG_SOFTWARE = 2
_MICROSOFT_VENDOR_ID = 0x1414
_MAX_ADAPTERS = 32


@functools.cache
def _dxgi_api():
    """CreateDXGIFactory1 ve COM vtable yöntemleri: Release (2), IDXGIFactory1::EnumAdapters1 (12),
    IDXGIAdapter1::GetDesc1 (10). ctypes'ın COM yöntemi biçimi: ilk bağımsız değişken arayüz işaretçisidir (this)."""
    create = ctypes.WinDLL("dxgi").CreateDXGIFactory1
    create.argtypes = (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
    create.restype = ctypes.c_long  # HRESULT; ctypes.HRESULT kendiliğinden hata fırlatırdı, DXGI_ERROR_NOT_FOUND normal
    proto = ctypes.WINFUNCTYPE
    release = proto(ctypes.c_ulong)(2, "Release")
    enum_adapters1 = proto(ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(12, "EnumAdapters1")
    get_desc1 = proto(ctypes.c_long, ctypes.POINTER(_DXGI_ADAPTER_DESC1))(10, "GetDesc1")
    return create, release, enum_adapters1, get_desc1


def _hr(code: int) -> str:
    return f"0x{code & 0xFFFFFFFF:08X}"


def _gpu_from_desc(desc: _DXGI_ADAPTER_DESC1) -> Gpu | None:
    """DXGI_ADAPTER_DESC1 → Gpu. Yazılım sürücüsü (bayrak 2), Microsoft Basic Render/Uzak Masaüstü (0x1414) ve
    ayrık belleği 0 olan sanal sürücüler (Parsec, IDD) atlanır: None."""
    if desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE or desc.VendorId == _MICROSOFT_VENDOR_ID:
        return None
    vram = int(desc.DedicatedVideoMemory)
    if vram <= 0:
        return None
    vendor = vendor_name(desc.VendorId)
    name = desc.Description.strip()
    return Gpu(name=name, vendor=vendor, vram=vram, shared=int(desc.SharedSystemMemory),
               integrated=is_integrated(vendor, name, vram))


def dxgi_adapters() -> list[Gpu]:
    """DXGI ile ekran kartları (birincil yöntem): CreateDXGIFactory1 → EnumAdapters1 → GetDesc1; her arayüz Release
    ile bırakılır. Atlananlar için bkz. _gpu_from_desc. DXGI okunamazsa (Windows değil, fabrika kurulamadı) OSError."""
    if sys.platform != "win32":
        raise OSError("DXGI yalnız Windows'ta var")
    create, release, enum_adapters1, get_desc1 = _dxgi_api()
    factory = ctypes.c_void_p()
    hr = create(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory))
    if hr < 0 or not factory.value:
        raise OSError(f"CreateDXGIFactory1 başarısız: {_hr(hr)}")
    out: list[Gpu] = []
    try:
        for index in range(_MAX_ADAPTERS):
            adapter = ctypes.c_void_p()
            hr = enum_adapters1(factory, index, ctypes.byref(adapter))
            if (hr & 0xFFFFFFFF) == _DXGI_ERROR_NOT_FOUND:
                break
            if hr < 0 or not adapter.value:
                if not out:
                    raise OSError(f"EnumAdapters1({index}) başarısız: {_hr(hr)}")
                log.warning("EnumAdapters1(%d) başarısız: %s; okunan kartlarla sürülüyor", index, _hr(hr))
                break
            try:
                desc = _DXGI_ADAPTER_DESC1()
                hr = get_desc1(adapter, ctypes.byref(desc))
                if hr < 0:
                    log.debug("GetDesc1(%d) başarısız: %s", index, _hr(hr))
                    continue
                gpu = _gpu_from_desc(desc)
                if gpu is None:
                    log.debug("DXGI kartı atlandı: %r (üretici 0x%04X, bayrak %d, bellek %d)", desc.Description,
                              desc.VendorId, desc.Flags, desc.DedicatedVideoMemory)
                else:
                    out.append(gpu)
            finally:
                release(adapter)
    finally:
        release(factory)
    return out


# --- kayıt defteri yedeği ---------------------------------------------------------------------------------------
_DEVICEMAP_VIDEO = r"HARDWARE\DEVICEMAP\VIDEO"
_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_REG_MACHINE = "\\registry\\machine\\"
_PCI_VENDOR = re.compile(r"^PCI\\VEN_([0-9A-F]{4})", re.I)


def _devicemap_path(value: str) -> str | None:
    r"""DEVICEMAP\VIDEO değeri ("\Registry\Machine\System\…\Control\Video\{GUID}\0000") → HKLM altındaki yol.
    Kart anahtarı değilse (ör. Services\BasicDisplay) None."""
    v = value.strip()
    if not v.lower().startswith(_REG_MACHINE):
        return None
    path = v[len(_REG_MACHINE):]
    return path if "\\control\\video\\{" in path.lower() else None


def _gpu_from_registry(desc: object, matching_id: object, mem: object) -> Gpu | None:
    """Kayıt değerleri (DriverDesc, MatchingDeviceId, HardwareInformation.qwMemorySize) → Gpu. Yalnız PCI aygıtları
    (sanal ve yazılım sürücüleri PCI değildir); Microsoft (0x1414) atlanır. Bellek yoksa vram=0 (bilinmiyor)."""
    if not isinstance(desc, str) or not desc.strip() or not isinstance(matching_id, str):
        return None
    m = _PCI_VENDOR.match(matching_id)
    if not m:
        return None
    vendor_id = int(m.group(1), 16)
    if vendor_id == _MICROSOFT_VENDOR_ID:
        return None
    if isinstance(mem, bytes):
        mem = int.from_bytes(mem[:8], "little")
    vram = mem if isinstance(mem, int) and mem > 0 else 0
    vendor, name = vendor_name(vendor_id), desc.strip()
    return Gpu(name=name, vendor=vendor, vram=vram, shared=0, integrated=is_integrated(vendor, name, vram))


def registry_gpus() -> list[Gpu]:
    r"""DXGI'nin yedeği: kayıt defterindeki görüntü bağdaştırıcıları ve HardwareInformation.qwMemorySize (64 bit).

    Bu açılışta etkin kartlar HARDWARE\DEVICEMAP\VIDEO'dan (açılışta yeniden kurulur) bulunur ve her kartın
    Control\Video\{GUID}\0000 anahtarı bir kez okunur. DEVICEMAP hiç açılamazsa sürücü sınıfı anahtarı
    ({4d36e968-…}\00xx) taranır; orada sökülmüş eski kartlar da kalabildiği için yalnız son çaredir.
    Kayıt defteri hiç açılamazsa OSError."""
    if sys.platform != "win32":
        raise OSError("Kayıt defteri yalnız Windows'ta var")
    import winreg

    hklm = winreg.HKEY_LOCAL_MACHINE
    paths: list[str] = []
    try:
        with winreg.OpenKey(hklm, _DEVICEMAP_VIDEO) as key:
            seen: set[str] = set()
            for i in range(1024):
                try:
                    name, value, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                if not name.lower().startswith("\\device\\video") or not isinstance(value, str):
                    continue
                path = _devicemap_path(value)
                card = path.rsplit("\\", 1)[0].lower() if path else ""  # bir kartın çıkışları: {GUID}\0000, \0001…
                if path and card not in seen:
                    seen.add(card)
                    paths.append(path)
    except OSError as e:
        log.info("DEVICEMAP\\VIDEO okunamadı (%s); sürücü sınıfı anahtarı taranıyor", e)
        with winreg.OpenKey(hklm, _DISPLAY_CLASS) as key:
            for i in range(256):
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                if sub.isdigit():
                    paths.append(f"{_DISPLAY_CLASS}\\{sub}")

    def query(key, name: str) -> object:
        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    gpus: list[Gpu] = []
    for path in paths:
        try:
            with winreg.OpenKey(hklm, path) as key:
                gpu = _gpu_from_registry(query(key, "DriverDesc"), query(key, "MatchingDeviceId"),
                                         query(key, "HardwareInformation.qwMemorySize"))
        except OSError as e:
            log.debug("Kayıt anahtarı okunamadı (%s): %s", path, e)
            continue
        if gpu is not None:
            gpus.append(gpu)
    return gpus


# --- nvidia-smi ------------------------------------------------------------------------------------------------
_SMI_ARGS = ("--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_INT_FIELD = re.compile(r"^(\d+)(?:\s*MiB)?$", re.I)
_VERSION = re.compile(r"^\d+(?:\.\d+)+$")


def _nvidia_smi_path() -> str | None:
    """nvidia-smi.exe: önce sürücünün bilinen yerleri (System32; eski sürücülerde NVSMI klasörü), sonra PATH."""
    windir = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    for candidate in (Path(windir) / "System32" / "nvidia-smi.exe",
                      Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("nvidia-smi")


def nvidia_smi(timeout: float = 4.0) -> str | None:
    """`nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader,nounits` çıktısı.
    Konsol penceresi açılmaz (CREATE_NO_WINDOW). Program yoksa, zaman aşımında ya da hata koduyla biterse None."""
    exe = _nvidia_smi_path()
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, *_SMI_ARGS], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace",
                              creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        log.warning("nvidia-smi %.0f sn içinde yanıt vermedi", timeout)
        return None
    except OSError as e:
        log.warning("nvidia-smi çalıştırılamadı: %s", e)
        return None
    if proc.returncode != 0:
        log.info("nvidia-smi %d koduyla bitti: %s", proc.returncode, (proc.stdout or proc.stderr or "").strip()[:200])
        return None
    return proc.stdout


def _smi_int(field: str) -> int | None:
    """Bellek alanı (MiB) → bayt. "[N/A]", "[Not Supported]" gibi köşeli değerler 0; sayı değilse None."""
    if field.startswith("[") and field.endswith("]"):
        return 0
    m = _INT_FIELD.match(field)
    return int(m.group(1)) * MIB if m else None


def parse_nvidia_smi(text: str) -> list[tuple[str, int, int, str]]:
    """nvidia_smi() çıktısı → [(ad, toplam bellek, kullanılan bellek, sürücü sürümü)], kart başına bir satır.
    Bellekler bayttır; "[N/A]" ve benzeri okunamayan değerler 0 (bellek) ya da "" (sürücü). Biçime uymayan satırlar
    ("No devices were found", hata iletileri) atlanır. Adda virgül olsa da son üç alan sağdan ayrılır."""
    rows: list[tuple[str, int, int, str]] = []
    for raw in (text or "").splitlines():
        parts = raw.strip().rsplit(",", 3)
        if len(parts) != 4:
            continue
        name, total_s, used_s, driver = (p.strip() for p in parts)
        total, used = _smi_int(total_s), _smi_int(used_s)
        bracketed = driver.startswith("[") and driver.endswith("]")
        if not name or name.startswith("[") or total is None or used is None:
            continue
        if not bracketed and not _VERSION.match(driver):
            continue
        rows.append((name, total, used, "" if bracketed else driver))
    return rows


def _name_key(name: str) -> str:
    """Ad eşleştirme anahtarı: eski sürücüler "GeForce GTX 1080 Ti", DXGI "NVIDIA GeForce GTX 1080 Ti" diyebilir."""
    n = display_name(name).casefold()
    return n.removeprefix("nvidia ").strip()


def _merge_nvidia(gpus: Sequence[Gpu], rows: Sequence[tuple[str, int, int, str]]) -> list[Gpu]:
    """nvidia-smi satırlarını NVIDIA kartlarına işler: kesin bellek (memory.total) ve sürücü sürümü. Önce ada göre,
    kalanlar sırayla eşlenir; DXGI'nin görmediği kartlar (TCC kipi ya da DXGI okunamadı) sona eklenir."""
    out = list(gpus)
    left: list[tuple[str, int, int, str] | None] = list(rows)
    nv = [i for i, g in enumerate(out) if g.vendor == VENDOR_NVIDIA]
    pairs: dict[int, tuple[str, int, int, str]] = {}
    for i in nv:
        key = _name_key(out[i].name)
        for j, row in enumerate(left):
            if row is not None and _name_key(row[0]) == key:
                pairs[i], left[j] = row, None
                break
    rest = [row for row in left if row is not None]
    for i in nv:
        if i not in pairs and rest:
            pairs[i] = rest.pop(0)
    for i, (_name, total, _used, driver) in pairs.items():
        out[i] = replace(out[i], vram=total or out[i].vram, driver=driver or out[i].driver)
    out.extend(Gpu(name=name, vendor=VENDOR_NVIDIA, vram=total, shared=0, integrated=False, driver=driver)
               for name, total, _used, driver in rest)
    return out


# --- toplu ölçüm -------------------------------------------------------------------------------------------------
def _describe(g: Gpu) -> str:
    extra = " tümleşik" if g.integrated else ""
    if g.driver:
        extra += f", sürücü {g.driver}"
    return f"{g.name or '?'} {g.vram / GIB:.1f} GiB{extra}"


def _list_gpus() -> list[Gpu]:
    t0 = time.perf_counter()
    gpus: list[Gpu] = []
    dxgi_failed = False
    source = "DXGI"
    try:
        gpus = dxgi_adapters()
    except Exception as e:  # noqa: BLE001 — OSError, ctypes erişim hatası: yedeğe geç
        dxgi_failed = True
        log.warning("DXGI ile ekran kartları okunamadı: %s", e)
    if not gpus:  # DXGI okunamadı ya da yalnız yazılım/uzak masaüstü sürücüsü gördü
        try:
            found = registry_gpus()
        except Exception as e:  # noqa: BLE001
            found = []
            log.warning("Ekran kartları kayıt defterinden okunamadı: %s", e)
        if found:
            gpus, source = found, "kayıt defteri"
    if not gpus or any(g.vendor == VENDOR_NVIDIA for g in gpus):
        try:
            text = nvidia_smi()
            rows = parse_nvidia_smi(text) if text else []
            if rows:
                if not gpus:
                    source = "nvidia-smi"
                gpus = _merge_nvidia(gpus, rows)
        except Exception:  # noqa: BLE001
            log.warning("nvidia-smi bilgisi işlenemedi", exc_info=True)
    if not gpus and dxgi_failed:
        gpus = [UNKNOWN_GPU]
    ms = (time.perf_counter() - t0) * 1000
    log.info("Ekran kartları (%s, %.0f ms): %s", source, ms, "; ".join(_describe(g) for g in gpus) or "yok")
    if ms > 1000:
        log.warning("Ekran kartı okuması yavaş: %.0f ms", ms)
    return gpus


def list_gpus() -> list[Gpu]:
    """Ekran kartları: DXGI (birincil) → DXGI okunamaz ya da kart göstermezse kayıt defteri → NVIDIA varsa ya da
    hiç kart yoksa nvidia-smi (sürücü sürümü, kesin bellek). Hiçbir yol okuyamadıysa ve DXGI başarısız olduysa
    [UNKNOWN_GPU]; DXGI çalıştı ama yalnız yazılım sürücüsü gördüyse []. Asla hata fırlatmaz; sorunlar günlüğe
    yazılır. Tipik süre 0,1-0,3 sn (nvidia-smi'nin zaman aşımı 4 sn)."""
    try:
        return _list_gpus()
    except Exception:  # noqa: BLE001 — ilk açılış penceresi bu yüzden açılamaz olmasın
        log.exception("Ekran kartları okunurken beklenmeyen hata")
        return [UNKNOWN_GPU]


# --- sistem: bellek, disk, pil, Windows --------------------------------------------------------------------------
class _MEMORYSTATUSEX(ctypes.Structure):  # noqa: N801
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class _SYSTEM_POWER_STATUS(ctypes.Structure):  # noqa: N801
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]


class _OSVERSIONINFOW(ctypes.Structure):  # noqa: N801
    _fields_ = [("dwOSVersionInfoSize", ctypes.c_uint32), ("dwMajorVersion", ctypes.c_uint32),
                ("dwMinorVersion", ctypes.c_uint32), ("dwBuildNumber", ctypes.c_uint32),
                ("dwPlatformId", ctypes.c_uint32), ("szCSDVersion", ctypes.c_wchar * 128)]


@functools.cache
def _kernel32():
    """Bu modüle ait kernel32 örneği: argtypes ayarları winutil'in örneğine karışmaz."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_MEMORYSTATUSEX),)
    k32.GlobalMemoryStatusEx.restype = ctypes.c_int
    k32.GetSystemPowerStatus.argtypes = (ctypes.POINTER(_SYSTEM_POWER_STATUS),)
    k32.GetSystemPowerStatus.restype = ctypes.c_int
    return k32


def system_ram() -> int | None:
    """Toplam fiziksel bellek (bayt; GlobalMemoryStatusEx, donanıma ayrılan kısım hariç). Okunamazsa None."""
    if sys.platform != "win32":
        return None
    try:
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(st)):
            log.warning("GlobalMemoryStatusEx başarısız: %d", ctypes.get_last_error())
            return None
        return int(st.ullTotalPhys) or None
    except Exception as e:  # noqa: BLE001
        log.warning("Sistem belleği okunamadı: %s", e)
        return None


def free_space(path: Path) -> int | None:
    """``path``'in bulunduğu sürücüdeki boş alan (bayt). Klasör henüz yoksa var olan ilk üst klasöre bakılır
    (ör. model klasörü kurulumdan önce yoktur). Okunamazsa None."""
    try:
        p = Path(path).absolute()
        for candidate in (p, *p.parents):
            if candidate.exists():
                return int(shutil.disk_usage(candidate).free)
    except (OSError, ValueError) as e:
        log.warning("Boş disk alanı okunamadı (%s): %s", path, e)
        return None
    log.warning("Boş disk alanı okunamadı: %s ve üst klasörleri yok", path)
    return None


def _battery_state(ac_line: int, battery_flag: int) -> bool | None:
    """SYSTEM_POWER_STATUS → pille mi? ACLineStatus 0 pil, 1 şebeke, 255 bilinmiyor; BatteryFlag 128 pil yok,
    255 bilinmiyor."""
    if battery_flag != 255 and battery_flag & 128:
        return False
    return {0: True, 1: False}.get(ac_line)


def on_battery() -> bool | None:
    """Bilgisayar pille mi çalışıyor? Pili olmayan masaüstü: False. Durum bilinmiyorsa ya da okunamazsa None."""
    if sys.platform != "win32":
        return None
    try:
        st = _SYSTEM_POWER_STATUS()
        if not _kernel32().GetSystemPowerStatus(ctypes.byref(st)):
            log.warning("GetSystemPowerStatus başarısız: %d", ctypes.get_last_error())
            return None
    except Exception as e:  # noqa: BLE001
        log.warning("Pil durumu okunamadı: %s", e)
        return None
    return _battery_state(st.ACLineStatus, st.BatteryFlag)


def windows_build() -> int:
    """Windows derleme numarası (ör. 19045 = Windows 10 22H2, 26100 = Windows 11 24H2); okunamazsa 0.
    RtlGetVersion uyumluluk bildiriminden (manifest) etkilenmez; kernel32.dll'in dosya sürümü ise 22H2'de 19041
    gösterdiği için kullanılmaz."""
    if sys.platform != "win32":
        return 0
    try:
        info = _OSVERSIONINFOW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        rtl_get_version = ctypes.WinDLL("ntdll").RtlGetVersion
        rtl_get_version.argtypes = (ctypes.POINTER(_OSVERSIONINFOW),)
        rtl_get_version.restype = ctypes.c_long
        if rtl_get_version(ctypes.byref(info)) == 0 and info.dwBuildNumber:
            return int(info.dwBuildNumber)
    except Exception as e:  # noqa: BLE001
        log.debug("RtlGetVersion okunamadı: %s", e)
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return 0


# --- öneri -------------------------------------------------------------------------------------------------------
def _gb(nbytes: int) -> str:
    """Arayüz için yuvarlanmış GB: 11,99 GiB → "12 GB"; 2 GB'tan küçükse bir ondalık ("0,5 GB")."""
    gib = nbytes / GIB
    if gib >= 1.95:
        return f"{int(gib + 0.5)} GB"
    return f"{gib:.1f} GB".replace(".", ",")


def _label(gpu: Gpu) -> str:
    """"NVIDIA GeForce RTX 4070 12 GB"; tümleşik kartta ve bellek bilinmiyorsa yalnız ad."""
    name = display_name(gpu.name)
    return f"{name} {_gb(gpu.vram)}" if gpu.vram > 0 and not gpu.integrated else name


def _vram_tier(vram: int) -> str:
    gib = vram / GIB
    return next((tier for low, tier in VRAM_STEPS if gib >= low), "weak")


def _nvidia_family(name: str) -> str:
    """NVIDIA kuşağı (bkz. _NV_FAMILIES): "old", "pascal", "turing16" ya da tensör çekirdekli kartlarda ""."""
    n = display_name(name).upper()
    return next((family for family, pattern in _NV_FAMILIES if pattern.search(n)), "")


def _untested(gpu: Gpu) -> bool:
    """Sunulur ama önceden seçili gelmez: harici Intel Arc, AMD RX 5000 ve öncesi, NVIDIA/AMD/Intel dışı kartlar.
    Üreticisi hiç okunamayan kart (UNKNOWN_GPU) burada sayılmaz; o "unknown" kademesinin kendi metnini alır."""
    if not gpu.vendor or gpu.vendor == VENDOR_NVIDIA:
        return False
    if gpu.vendor == VENDOR_AMD:
        return not _AMD_TESTED.search(display_name(gpu.name).upper())
    return True


def _driver_version(text: str) -> tuple[int, int] | None:
    """NVIDIA sürücü sürümü "621.14" → (621, 14); okunamazsa None."""
    m = re.match(r"\s*(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _version_text(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]:02d}" if version[1] else str(version[0])


def advise(gpus: Sequence[Gpu], *, ram: int | None, win_build: int) -> Advice:
    """İlk ekran önerisi (saf; tablo güdümlü: TIERS, VRAM_STEPS, _NV_FAMILIES).

    ``gpus``: list_gpus() sonucu; ``ram``: system_ram() (bayt, None = bilinmiyor, engel sayılmaz);
    ``win_build``: windows_build() (0 = bilinmiyor, engel sayılmaz).

    Sıra: Windows < 19045 → none. En iyi kart (best_gpu) yoksa ya da tümleşikse → none. NVIDIA GTX 9xx ve öncesi →
    weak. Bellek okunamadıysa → unknown. Yoksa bellek kademesi (VRAM_STEPS); GTX 10xx/16xx bir kademe aşağı (en
    aşağı weak). Düzeltmeler: edge → turuncu uyarı; harici Intel Arc, AMD RX 5000 ve öncesi, diğer üreticiler →
    önceden seçili gelmez ("Bu kartta denenmedi"); NVIDIA sürücüsü < 551.61 (GTX 9xx/10xx'te < 570) → uyarı;
    sistem belleği < 12 GiB (toleranslı: 11,5) → önceden seçili gelmez + uyarı."""
    gpu = best_gpu(gpus)
    card = gpu if gpu is not None and gpu.name else None  # UNKNOWN_GPU arayüze kart olarak gitmez

    def result(tier: str, line: str, *, warn: str = "", tip: str | None = None,
               preselect: bool | None = None) -> Advice:
        row = TIERS[tier]
        chosen = row.preselect if preselect is None else (preselect and row.offer)
        return Advice(tier=tier, offer=row.offer, preselect=chosen, model=row.model, gpu=card, line=line, warn=warn,
                      tip=row.tip if tip is None else tip)

    if 0 < win_build < MIN_WIN_BUILD:
        return result("none", _TEXT_OLD_WINDOWS, tip=_TIP_OLD_WINDOWS)
    if gpu is None or gpu.integrated:
        return result("none", f"{_TEXT_NO_GPU}: {_label(card)}" if card else _TEXT_NO_GPU)

    family = _nvidia_family(gpu.name) if gpu.vendor == VENDOR_NVIDIA else ""
    if family == "old":
        return result("weak", f"{_TEXT_NO_GPU}: {_label(gpu)}", tip=_TIP_OLD_CARD)
    if gpu.vram <= 0:
        tier = "unknown"
        line = f"{display_name(card.name)}: bellek okunamadı" if card else _TEXT_UNKNOWN
    else:
        tier = _vram_tier(gpu.vram)
        if family in _NV_STEP_DOWN:
            tier = _ORDER[max(0, _ORDER.index(tier) - 1)]
        line = _label(gpu)
    if tier == "weak":
        return result("weak", f"{_TEXT_NO_GPU}: {line}")

    row = TIERS[tier]
    preselect = row.preselect
    warns: list[str] = []
    tips = [row.tip]
    if tier == "edge":
        warns.append(_WARN_EDGE.format(gb=_gb(gpu.vram)))
    if _untested(gpu):
        preselect = False
        warns.append(_WARN_UNTESTED)
        tips = [_TIP_UNTESTED]
    if gpu.vendor == VENDOR_NVIDIA:
        have = _driver_version(gpu.driver)
        need = NVIDIA_MIN_DRIVER_OLD_ARCH if family in _NV_OLD_DRIVER_ARCH else NVIDIA_MIN_DRIVER
        if have is not None and have < need:
            warns.append(_WARN_OLD_DRIVER)
            tips.append(_TIP_OLD_DRIVER.format(have=gpu.driver, need=_version_text(need)))
    if ram is not None and 0 < ram < MIN_RAM_PRESELECT:
        preselect = False
        warns.append(_WARN_LOW_RAM.format(gb=_gb(ram)))
    return result(tier, line, warn="\n".join(warns), tip=" ".join(tips), preselect=preselect)
