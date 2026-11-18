"""Donanım algılama (altyazitr.hardware): ilk ekrandaki yapay zekâ önerisi (advise), nvidia-smi çıktısının
ayrıştırılması, tümleşik/harici kart ayrımı, DXGI ve kayıt defteri değerlerinin yorumlanması, yedek zinciri
(DXGI → kayıt defteri → nvidia-smi) ve sözleşmedeki imzalar.

advise() saf olduğu için kartlar elle kurulur; bellek değerleri kartların gerçekte raporladığına yakındır (12 GB'lık
kart DXGI'de ~11,7, nvidia-smi'de ~12,0 GiB). Son bölümdeki duman testleri bu makinedeki gerçek DXGI'yi, kayıt
defterini ve sistem çağrılarını dener; ekran kartı olmayan CI makinesinde boş kart listesi kabul edilir. Ağ gerekmez.
"""

from __future__ import annotations

import ctypes
import dataclasses
import inspect
import logging
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from altyazitr import hardware as hw
from altyazitr.hardware import GIB, MIB, MODEL_FULL, MODEL_LIGHT, UNKNOWN_GPU, Advice, Gpu

WIN11 = 26100
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="yalnız Windows")


def card(vendor: str, name: str, vram: int, driver: str = "") -> Gpu:
    """Kartı list_gpus() gibi kurar: tümleşik olup olmadığına is_integrated() karar verir."""
    return Gpu(name=name, vendor=vendor, vram=vram, shared=16 * GIB,
               integrated=hw.is_integrated(vendor, name, vram), driver=driver)


def nvidia(name: str, mib: int, driver: str = "621.14") -> Gpu:
    return card("NVIDIA", name, mib * MIB, driver)


RTX4070 = nvidia("NVIDIA GeForce RTX 4070", 12282)
RADEON_IGPU = card("AMD", "AMD Radeon(TM) Graphics", 486 * MIB)  # Ryzen 7000/9000 masaüstü işlemcinin tümleşiği
IRIS_XE = card("Intel", "Intel(R) Iris(R) Xe Graphics", 128 * MIB)
UHD620 = card("Intel", "Intel(R) UHD Graphics 620", 128 * MIB)
GTX1650 = nvidia("NVIDIA GeForce GTX 1650", 4096, "566.36")
RTX3060_6GB = nvidia("NVIDIA GeForce RTX 3060 Laptop GPU", 6144)
ARC_B580 = card("Intel", "Intel(R) Arc(TM) B580 Graphics", 12_216 * MIB)


# --- advise(): belgedeki durumlar ------------------------------------------------------------------------------
ADVISE_CASES = [
    # (kartlar, sistem belleği GiB, Windows derlemesi, kademe, sunulur, önceden seçili, model)
    pytest.param([], 16, WIN11, "none", False, False, None, id="kartsiz"),
    pytest.param([IRIS_XE], 16, WIN11, "none", False, False, None, id="iris_xe"),
    pytest.param([card("AMD", "AMD Radeon 780M Graphics", 8 * GIB)], 32, WIN11, "none", False, False, None,
                 id="780m_8gb_uma"),
    pytest.param([GTX1650], 16, WIN11, "weak", False, False, None, id="gtx1650"),
    pytest.param([RTX3060_6GB], 16, WIN11, "edge", True, False, MODEL_LIGHT, id="rtx3060_6gb"),
    pytest.param([nvidia("NVIDIA GeForce RTX 4060", 8188)], 16, WIN11, "mid", True, True, MODEL_LIGHT, id="rtx4060"),
    pytest.param([nvidia("NVIDIA GeForce RTX 3080", 10240)], 32, WIN11, "mid", True, True, MODEL_LIGHT,
                 id="rtx3080_10gb"),
    pytest.param([RTX4070], 32, WIN11, "good", True, True, MODEL_FULL, id="rtx4070"),
    pytest.param([nvidia("NVIDIA GeForce GTX 1080 Ti", 11264, "581.57")], 16, WIN11, "mid", True, True, MODEL_LIGHT,
                 id="gtx1080ti_bir_kademe_asagi"),
    pytest.param([ARC_B580], 32, WIN11, "good", True, False, MODEL_FULL, id="arc_b580_denenmedi"),
    pytest.param([card("AMD", "AMD Radeon RX 6700 XT", 12_868_124_672)], 32, WIN11, "good", True, True, MODEL_FULL,
                 id="rx6700xt"),
    pytest.param([nvidia("NVIDIA GeForce RTX 4090", 24564)], 64, WIN11, "strong", True, True, MODEL_FULL,
                 id="rtx4090"),
    pytest.param([UHD620, nvidia("NVIDIA GeForce RTX 4060 Laptop GPU", 8188)], 16, WIN11, "mid", True, True,
                 MODEL_LIGHT, id="optimus"),
    pytest.param([nvidia("NVIDIA GeForce RTX 4070", 12282, "546.33")], 32, WIN11, "good", True, True, MODEL_FULL,
                 id="eski_surucu"),
    pytest.param([RTX4070], 32, 19044, "none", False, False, None, id="windows_19044"),
    # ek durumlar
    pytest.param([RTX4070, RADEON_IGPU], 31, 26200, "good", True, True, MODEL_FULL, id="rtx4070_ve_ryzen_tumlesik"),
    pytest.param([RTX4070], 8, WIN11, "good", True, False, MODEL_FULL, id="ram_8gb"),
    pytest.param([card("AMD", "AMD Radeon RX 5700 XT", 8176 * MIB)], 16, WIN11, "mid", True, False, MODEL_LIGHT,
                 id="rx5700xt_denenmedi"),
    pytest.param([card("AMD", "AMD Radeon RX 7600S", 8176 * MIB)], 16, WIN11, "mid", True, True, MODEL_LIGHT,
                 id="rx7600s_dizustu"),
    pytest.param([nvidia("NVIDIA GeForce GTX 980 Ti", 6144, "581.57")], 16, WIN11, "weak", False, False, None,
                 id="gtx980ti_eski_kusak"),
    pytest.param([nvidia("NVIDIA GeForce GTX TITAN X", 12288, "581.57")], 16, WIN11, "weak", False, False, None,
                 id="titan_x_maxwell_12gb"),
    pytest.param([nvidia("NVIDIA GeForce GTX 1660 SUPER", 6144)], 16, WIN11, "weak", False, False, None,
                 id="gtx1660s_edgeden_asagi"),
    pytest.param([nvidia("NVIDIA GeForce RTX 4060 Ti", 16380)], 32, WIN11, "strong", True, True, MODEL_FULL,
                 id="rtx4060ti_16gb"),
    pytest.param([UNKNOWN_GPU], 16, WIN11, "unknown", True, False, MODEL_LIGHT, id="dxgi_okunamadi"),
    pytest.param([nvidia("NVIDIA GeForce RTX 4070", 0)], 16, WIN11, "unknown", True, False, MODEL_LIGHT,
                 id="bellek_okunamadi"),
    pytest.param([RTX4070], None, 0, "good", True, True, MODEL_FULL, id="ram_ve_windows_bilinmiyor"),
]


@pytest.mark.parametrize("gpus, ram_gib, build, tier, offer, preselect, model", ADVISE_CASES)
def test_advise_tiers(gpus, ram_gib, build, tier, offer, preselect, model):
    ram = None if ram_gib is None else int(ram_gib * GIB) - 300 * MIB  # donanıma ayrılan kısım düşülmüş gibi
    a = hw.advise(gpus, ram=ram, win_build=build)
    assert (a.tier, a.offer, a.preselect, a.model) == (tier, offer, preselect, model)
    assert a.line  # donanım satırı her zaman dolu
    assert a.offer or (not a.preselect and a.model is None)


@pytest.mark.parametrize("gpus, ram_gib, build, tier, offer, preselect, model", ADVISE_CASES)
def test_advice_texts_are_plain(gpus, ram_gib, build, tier, offer, preselect, model):
    """Arayüzde emoji/Unicode simge yok; simgeyi ([ok], [!], [i]) arayüz ekler."""
    ram = None if ram_gib is None else int(ram_gib * GIB)
    a = hw.advise(gpus, ram=ram, win_build=build)
    for text in (a.line, a.warn, a.tip):
        bad = [ch for ch in text if unicodedata.category(ch)[0] not in "LN" and ch not in " .,:;()'-/\n"]
        assert not bad, (text, bad)
        assert "[" not in text
    assert "\n" not in a.line and len(a.line) <= 90


def test_suitable_card_texts():
    a = hw.advise([RADEON_IGPU, RTX4070], ram=31 * GIB, win_build=26200)
    assert a.gpu == RTX4070  # tümleşik kart değil
    assert a.line == "NVIDIA GeForce RTX 4070 12 GB"
    assert a.warn == ""
    assert "12B" in a.tip and "Oyun da bu bilgisayarda çalışıyorsa" in a.tip


def test_edge_warns_with_card_size():
    a = hw.advise([RTX3060_6GB], ram=16 * GIB, win_build=WIN11)
    assert a.warn == "6 GB ekran kartı: yayın açıkken gecikebilir"
    assert a.line == "NVIDIA GeForce RTX 3060 Laptop GPU 6 GB"


def test_no_suitable_card_names_the_card_and_explains():
    tip = ("Yapay zekâ çevirisi için en az 8 GB belleği olan bir NVIDIA ya da AMD ekran kartı gerekir. "
           "Bu bilgisayarda hızlı çeviri daha iyi sonuç verir.")
    empty = hw.advise([], ram=16 * GIB, win_build=WIN11)
    assert (empty.line, empty.tip, empty.gpu) == ("Uygun ekran kartı yok", tip, None)
    igpu = hw.advise([IRIS_XE], ram=16 * GIB, win_build=WIN11)
    assert (igpu.line, igpu.tip, igpu.gpu) == ("Uygun ekran kartı yok: Intel Iris Xe Graphics", tip, IRIS_XE)
    weak = hw.advise([GTX1650], ram=16 * GIB, win_build=WIN11)
    assert (weak.line, weak.tip) == ("Uygun ekran kartı yok: NVIDIA GeForce GTX 1650 4 GB", tip)
    old = hw.advise([nvidia("NVIDIA GeForce GTX TITAN X", 12288)], ram=16 * GIB, win_build=WIN11)
    assert "eski" in old.tip and "8 GB" not in old.tip  # 12 GB'lık eski karta "en az 8 GB" denmez


def test_untested_card_is_offered_not_preselected():
    a = hw.advise([ARC_B580], ram=32 * GIB, win_build=WIN11)
    assert a.warn == "Bu kartta denenmedi"
    assert "denenmedi" in a.tip


def test_old_driver_warning_thresholds():
    def warn(name: str, mib: int, driver: str) -> Advice:
        return hw.advise([nvidia(name, mib, driver)], ram=32 * GIB, win_build=WIN11)

    old = warn("NVIDIA GeForce RTX 4070", 12282, "546.33")
    assert old.warn == "Ekran kartı sürücüsü eski" and old.preselect
    assert "546.33" in old.tip and "551.61" in old.tip
    assert warn("NVIDIA GeForce RTX 4070", 12282, "551.61").warn == ""
    assert warn("NVIDIA GeForce RTX 4070", 12282, "").warn == ""  # sürücü okunamadıysa uyarı yok
    pascal = warn("NVIDIA GeForce GTX 1080 Ti", 11264, "566.36")  # GTX 10xx'te eşik 570
    assert pascal.warn == "Ekran kartı sürücüsü eski" and "570" in pascal.tip
    assert warn("NVIDIA GeForce GTX 1080 Ti", 11264, "572.16").warn == ""


def test_low_ram_blocks_preselect_with_tolerance():
    low = hw.advise([RTX4070], ram=int(7.8 * GIB), win_build=WIN11)
    assert not low.preselect and low.warn == "Sistem belleği 8 GB: yapay zekâ bilgisayarı yavaşlatabilir"
    twelve = hw.advise([RTX4070], ram=int(11.8 * GIB), win_build=WIN11)  # 12 GB'lık makine 11,7-11,9 GiB raporlar
    assert twelve.preselect and twelve.warn == ""


def test_old_windows_blocks_ai_but_keeps_card():
    a = hw.advise([RTX4070], ram=32 * GIB, win_build=19044)
    assert (a.line, a.gpu) == ("Windows sürümü eski", RTX4070)
    assert "22H2" in a.tip
    assert hw.advise([RTX4070], ram=32 * GIB, win_build=19045).tier == "good"


def test_unknown_texts():
    nothing = hw.advise([UNKNOWN_GPU], ram=16 * GIB, win_build=WIN11)
    assert (nothing.line, nothing.gpu, nothing.warn) == ("Ekran kartı belirlenemedi", None, "")
    named = hw.advise([nvidia("NVIDIA GeForce RTX 4070", 0)], ram=16 * GIB, win_build=WIN11)
    assert named.line == "NVIDIA GeForce RTX 4070: bellek okunamadı" and named.gpu is not None


def test_several_warnings_are_separate_lines():
    a = hw.advise([card("Intel", "Intel(R) Arc(TM) A380 Graphics", 6 * GIB)], ram=8 * GIB, win_build=WIN11)
    assert a.tier == "edge"
    assert a.warn.splitlines() == ["6 GB ekran kartı: yayın açıkken gecikebilir", "Bu kartta denenmedi",
                                   "Sistem belleği 8 GB: yapay zekâ bilgisayarı yavaşlatabilir"]


@pytest.mark.parametrize("vram, tier", [
    (12_593_397_760, "good"),  # bu makinedeki RTX 4070'in DXGI değeri (11,73 GiB)
    (int(15.65 * GIB), "strong"),  # 16 GB
    (int(9.8 * GIB), "mid"),  # 10 GB
    (int(7.82 * GIB), "mid"),  # 8 GB
    (int(5.87 * GIB), "edge"),  # 6 GB
    (int(3.9 * GIB), "weak"),  # 4 GB
])
def test_vram_thresholds_tolerate_dxgi_underreport(vram, tier):
    assert hw.advise([card("NVIDIA", "NVIDIA GeForce RTX 9999", vram)], ram=32 * GIB, win_build=WIN11).tier == tier


def test_tier_table_is_consistent():
    assert set(hw.TIERS) == {"none", "weak", "edge", "mid", "good", "strong", "unknown"}
    for tier, row in hw.TIERS.items():
        assert row.offer or (not row.preselect and row.model is None), tier
        assert row.model in (MODEL_FULL, MODEL_LIGHT, None) and row.tip, tier
    lows = [low for low, _tier in hw.VRAM_STEPS]
    assert lows == sorted(lows, reverse=True)
    assert (MODEL_FULL, MODEL_LIGHT) == ("gemma4:12b", "gemma4:e4b")


def test_advise_is_pure(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("advise() ölçüm yaptı")

    for name in ("list_gpus", "dxgi_adapters", "registry_gpus", "nvidia_smi", "system_ram", "windows_build"):
        monkeypatch.setattr(hw, name, boom)
    monkeypatch.setattr(hw.subprocess, "run", boom)
    args = ([RTX4070, RADEON_IGPU],)
    assert hw.advise(*args, ram=32 * GIB, win_build=WIN11) == hw.advise(*args, ram=32 * GIB, win_build=WIN11)


# --- best_gpu ve tümleşik kart ayrımı ---------------------------------------------------------------------------
def test_best_gpu_prefers_discrete_even_over_large_uma():
    uma = card("AMD", "AMD Radeon 780M Graphics", 16 * GIB)  # BIOS'ta 16 GB UMA: ayrık bellek gibi raporlanır
    rtx = nvidia("NVIDIA GeForce RTX 4060 Laptop GPU", 8188)
    assert hw.best_gpu([uma, rtx]) == rtx
    assert hw.best_gpu([uma, IRIS_XE]) == uma  # yalnız tümleşik: adı gösterilsin diye döner
    assert hw.best_gpu([]) is None


def test_best_gpu_most_memory_then_vendor():
    xtx = card("AMD", "AMD Radeon RX 7900 XTX", 24 * GIB)
    assert hw.best_gpu([RTX4070, xtx]) == xtx
    rtx12 = nvidia("NVIDIA GeForce RTX 3060", 12 * 1024)
    rx12 = card("AMD", "AMD Radeon RX 6700 XT", 12 * GIB)
    assert hw.best_gpu([rx12, rtx12]) == rtx12  # eşit bellekte NVIDIA


@pytest.mark.parametrize("vendor, name, vram, integrated", [
    ("AMD", "AMD Radeon(TM) Graphics", 512 * MIB, True),
    ("AMD", "AMD Radeon 780M Graphics", 8 * GIB, True),  # UMA ayrık gibi raporlanır: belleğe bakılmaz
    ("AMD", "AMD Radeon 8060S Graphics", 64 * GIB, True),
    ("AMD", "AMD Radeon RX Vega 11 Graphics", 2 * GIB, True),
    ("AMD", "AMD Radeon RX 6700 XT", 12 * GIB, False),
    ("AMD", "AMD Radeon RX 7600S", 8 * GIB, False),
    ("AMD", "Radeon RX 580 Series", 8 * GIB, False),
    ("AMD", "AMD Radeon PRO W7800", 32 * GIB, False),
    ("AMD", "AMD Radeon AI PRO R9700", 32 * GIB, False),
    ("Intel", "Intel(R) Iris(R) Xe Graphics", 128 * MIB, True),
    ("Intel", "Intel(R) UHD Graphics 770", 128 * MIB, True),
    ("Intel", "Intel(R) Arc(TM) Graphics", 128 * MIB, True),  # Core Ultra tümleşiği
    ("Intel", "Intel(R) Arc(TM) 140V GPU (16GB)", 0, True),
    ("Intel", "Intel(R) Arc(TM) B390 GPU", 0, True),  # adı "Arc B###" ama tümleşik (adında "GPU")
    ("Intel", "Intel(R) Arc(TM) A770 Graphics", 16 * GIB, False),
    ("Intel", "Intel(R) Arc(TM) A370M Graphics", 4 * GIB, False),
    ("Intel", "Intel(R) Arc(TM) Pro B60 Graphics", 24 * GIB, False),
    ("Intel", "Intel(R) Arc(TM) B580 Graphics", 12 * GIB, False),
    ("Intel", "Intel(R) Arc(TM) A750 Graphics", 512 * MIB, True),  # 2 GiB'tan küçük Intel kartı harici olamaz
    ("NVIDIA", "NVIDIA GeForce RTX 4070", 12 * GIB, False),
    ("NVIDIA", "NVIDIA GeForce MX450", 2 * GIB, False),
    ("Qualcomm", "Qualcomm(R) Adreno(TM) X1-85 GPU", 0, True),
    ("0x1ED5", "MTT S80", 16 * GIB, False),
    ("0x15AD", "VMware SVGA 3D", 128 * MIB, True),
])
def test_is_integrated_by_name(vendor, name, vram, integrated):
    assert hw.is_integrated(vendor, name, vram) is integrated


def test_display_name_and_vendor_name():
    assert hw.display_name("Intel(R) Arc(TM) A770 Graphics") == "Intel Arc A770 Graphics"
    assert hw.display_name("AMD Radeon(TM)  Graphics ") == "AMD Radeon Graphics"
    assert [hw.vendor_name(v) for v in (0x10DE, 0x1002, 0x8086, 0x1ED5)] == ["NVIDIA", "AMD", "Intel", "0x1ED5"]


# --- nvidia-smi --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, rows", [
    ("NVIDIA GeForce RTX 4070, 12282, 6646, 621.14\n",
     [("NVIDIA GeForce RTX 4070", 12282 * MIB, 6646 * MIB, "621.14")]),
    ("NVIDIA GeForce RTX 3060 Laptop GPU, [N/A], [N/A], 546.33",
     [("NVIDIA GeForce RTX 3060 Laptop GPU", 0, 0, "546.33")]),
    ("NVIDIA GeForce GTX 1080 Ti, 11264, [Not Supported], [N/A]",
     [("NVIDIA GeForce GTX 1080 Ti", 11264 * MIB, 0, "")]),
    ("NVIDIA GeForce RTX 4090, 24564, 1200, 581.57\r\n\r\nNVIDIA RTX A6000, 49140, 0, 581.57\r\n",
     [("NVIDIA GeForce RTX 4090", 24564 * MIB, 1200 * MIB, "581.57"),
      ("NVIDIA RTX A6000", 49140 * MIB, 0, "581.57")]),
    ("NVIDIA GeForce RTX 4070, 12282 MiB, 6646 MiB, 621.14", [("NVIDIA GeForce RTX 4070", 12282 * MIB, 6646 * MIB,
                                                              "621.14")]),  # nounits unutulursa
    ("Garip, Adlı Kart, 8192, 10, 560.94", [("Garip, Adlı Kart", 8192 * MIB, 10 * MIB, "560.94")]),
    ("No devices were found\n", []),
    ("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. Make sure that the latest "
     "NVIDIA driver is installed and running.\n", []),
    ("bir, iki, üç, dört", []),
    ("[N/A], 8192, 0, 560.94", []),
    ("", []),
])
def test_parse_nvidia_smi(text, rows):
    assert hw.parse_nvidia_smi(text) == rows


class FakeRun:
    """subprocess.run yerine: çağrıları kaydeder; verilen sonucu döndürür ya da istisnayı fırlatır."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


SMI_EXE = r"C:\Windows\System32\nvidia-smi.exe"
SMI_LINE = "NVIDIA GeForce RTX 4070, 12282, 6646, 621.14\n"


def test_nvidia_smi_runs_hidden_with_timeout(monkeypatch):
    monkeypatch.setattr(hw, "_nvidia_smi_path", lambda: SMI_EXE)
    fake = FakeRun(subprocess.CompletedProcess([], 0, stdout=SMI_LINE, stderr=""))
    monkeypatch.setattr(hw.subprocess, "run", fake)
    assert hw.nvidia_smi() == SMI_LINE
    args, kwargs = fake.calls[0]
    assert args == [SMI_EXE, "--query-gpu=name,memory.total,memory.used,driver_version",
                    "--format=csv,noheader,nounits"]
    assert kwargs["timeout"] == 4.0 and kwargs["stdin"] == subprocess.DEVNULL
    if sys.platform == "win32":
        assert kwargs["creationflags"] & 0x08000000  # CREATE_NO_WINDOW: konsol penceresi yanıp sönmez


@pytest.mark.parametrize("outcome", [
    subprocess.TimeoutExpired(SMI_EXE, 4.0),
    FileNotFoundError(2, "yok"),
    PermissionError(13, "erişim engellendi"),
    subprocess.CompletedProcess([], 9, stdout="NVIDIA-SMI has failed because it couldn't communicate", stderr=""),
], ids=["zaman_asimi", "dosya_yok", "erisim", "hata_kodu"])
def test_nvidia_smi_failures_return_none(monkeypatch, outcome):
    monkeypatch.setattr(hw, "_nvidia_smi_path", lambda: SMI_EXE)
    monkeypatch.setattr(hw.subprocess, "run", FakeRun(outcome))
    assert hw.nvidia_smi() is None


def test_nvidia_smi_missing_spawns_nothing(monkeypatch):
    fake = FakeRun(AssertionError("süreç başlatılmamalıydı"))
    monkeypatch.setattr(hw, "_nvidia_smi_path", lambda: None)
    monkeypatch.setattr(hw.subprocess, "run", fake)
    assert hw.nvidia_smi() is None and not fake.calls


# --- DXGI ve kayıt defteri değerleri -----------------------------------------------------------------------------
def dxgi_desc(name: str, vendor_id: int, vram: int, flags: int = 0) -> hw._DXGI_ADAPTER_DESC1:
    d = hw._DXGI_ADAPTER_DESC1()
    d.Description, d.VendorId, d.DedicatedVideoMemory, d.SharedSystemMemory, d.Flags = (
        name, vendor_id, vram, 16 * GIB, flags)
    return d


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_wchar) != 2,
                    reason="64 bit Windows düzeni")
def test_dxgi_desc_layout_matches_sdk():
    desc = hw._DXGI_ADAPTER_DESC1
    assert ctypes.sizeof(desc) == 312
    assert (desc.VendorId.offset, desc.DedicatedVideoMemory.offset, desc.SharedSystemMemory.offset,
            desc.Flags.offset) == (256, 272, 288, 304)


def test_dxgi_desc_filters_virtual_adapters_and_keeps_64bit_memory():
    assert hw._gpu_from_desc(dxgi_desc("Microsoft Basic Render Driver", 0x1414, 0, flags=2)) is None
    assert hw._gpu_from_desc(dxgi_desc("Microsoft Remote Display Adapter", 0x1414, 0)) is None
    assert hw._gpu_from_desc(dxgi_desc("Parsec Virtual Display Adapter", 0x0000, 0)) is None
    assert hw._gpu_from_desc(dxgi_desc("Yazılım sürücüsü", 0x10DE, 4 * GIB, flags=2)) is None
    big = hw._gpu_from_desc(dxgi_desc("NVIDIA GeForce RTX 4090", 0x10DE, 24 * GIB))
    assert big == Gpu("NVIDIA GeForce RTX 4090", "NVIDIA", 24 * GIB, 16 * GIB, False)  # 4 GB'ta tıkanmaz
    igpu = hw._gpu_from_desc(dxgi_desc("AMD Radeon(TM) Graphics", 0x1002, 509_382_656))
    assert igpu is not None and igpu.vendor == "AMD" and igpu.integrated


@pytest.mark.parametrize("desc, matching_id, mem, expected", [
    ("NVIDIA GeForce RTX 4070", r"pci\ven_10de&dev_2786&subsys_51371462", 12_878_610_432,
     Gpu("NVIDIA GeForce RTX 4070", "NVIDIA", 12_878_610_432, 0, False)),
    ("AMD Radeon(TM) Graphics", r"PCI\VEN_1002&DEV_164E&REV_CA", 536_870_912,
     Gpu("AMD Radeon(TM) Graphics", "AMD", 536_870_912, 0, True)),
    ("NVIDIA GeForce GTX 1080", r"PCI\VEN_10DE&DEV_1B80", None, Gpu("NVIDIA GeForce GTX 1080", "NVIDIA", 0, 0, False)),
    ("NVIDIA GeForce GTX 1080", r"PCI\VEN_10DE&DEV_1B80", (8 * GIB).to_bytes(8, "little"),
     Gpu("NVIDIA GeForce GTX 1080", "NVIDIA", 8 * GIB, 0, False)),
    ("Microsoft Basic Display Adapter", r"PCI\CC_0300", None, None),
    ("Parsec Virtual Display Adapter", r"Root\Parsec\VDA", None, None),
    ("Microsoft Hyper-V Video", r"PCI\VEN_1414&DEV_5353", 8 * MIB, None),
    (None, r"PCI\VEN_10DE&DEV_2786", 12 * GIB, None),
], ids=["nvidia", "amd_tumlesik", "bellek_yok", "bellek_ikili", "temel_surucu", "sanal", "hyperv", "adsiz"])
def test_registry_values_to_gpu(desc, matching_id, mem, expected):
    assert hw._gpu_from_registry(desc, matching_id, mem) == expected


@pytest.mark.parametrize("value, path", [
    (r"\Registry\Machine\System\CurrentControlSet\Control\Video\{E1708C65-A0D1-11F1-B3E6-806E6F6E6963}\0000",
     r"System\CurrentControlSet\Control\Video\{E1708C65-A0D1-11F1-B3E6-806E6F6E6963}\0000"),
    (r"\REGISTRY\MACHINE\SYSTEM\ControlSet001\Services\BasicDisplay", None),
    ("", None),
])
def test_devicemap_path(value, path):
    assert hw._devicemap_path(value) == path


# --- list_gpus(): yedek zinciri ----------------------------------------------------------------------------------
def fake(value):
    """Kaynak yerine: değer döndürür; istisna verilirse fırlatır."""

    def source(*_a, **_k):
        if isinstance(value, BaseException):
            raise value
        return list(value) if isinstance(value, list) else value

    return source


@pytest.fixture
def sources(monkeypatch):
    def set_sources(dxgi, registry, smi):
        monkeypatch.setattr(hw, "dxgi_adapters", fake(dxgi))
        monkeypatch.setattr(hw, "registry_gpus", fake(registry))
        monkeypatch.setattr(hw, "nvidia_smi", fake(smi))

    return set_sources


def test_list_gpus_enriches_nvidia_from_smi(sources):
    dxgi_1080ti = card("NVIDIA", "NVIDIA GeForce GTX 1080 Ti", int(10.76 * GIB))
    uhd = card("Intel", "Intel(R) UHD Graphics 630", 128 * MIB)
    sources([uhd, dxgi_1080ti], AssertionError("kayıt defterine gerek yoktu"),
            "GeForce GTX 1080 Ti, 11264, 300, 566.36\n")  # eski sürücüler adı "NVIDIA" öneki olmadan yazar
    gpus = hw.list_gpus()
    assert gpus == [uhd, dataclasses.replace(dxgi_1080ti, vram=11264 * MIB, driver="566.36")]


def test_list_gpus_skips_smi_without_nvidia(sources):
    amd = [card("AMD", "AMD Radeon RX 7800 XT", 16 * GIB), RADEON_IGPU]
    sources(amd, AssertionError("kayıt defterine gerek yoktu"), AssertionError("nvidia-smi çalıştırılmamalıydı"))
    assert hw.list_gpus() == amd


def test_list_gpus_adds_cards_dxgi_does_not_see(sources):
    rtx4090 = card("NVIDIA", "NVIDIA GeForce RTX 4090", int(23.6 * GIB))
    sources([rtx4090], [], "NVIDIA GeForce RTX 4090, 24564, 0, 581.57\nNVIDIA RTX A6000, 49140, 0, 581.57\n")
    gpus = hw.list_gpus()  # TCC kipindeki hesaplama kartı DXGI'de görünmez
    assert [(g.name, g.vram, g.driver) for g in gpus] == [("NVIDIA GeForce RTX 4090", 24564 * MIB, "581.57"),
                                                           ("NVIDIA RTX A6000", 49140 * MIB, "581.57")]


@pytest.mark.parametrize("dxgi", [OSError("CreateDXGIFactory1 başarısız: 0x887A0004"), []],
                         ids=["dxgi_hata", "yalniz_yazilim_surucusu"])
def test_list_gpus_registry_fallback(sources, dxgi):
    reg = [Gpu("NVIDIA GeForce RTX 4070", "NVIDIA", 12_878_610_432, 0, False), RADEON_IGPU]
    sources(dxgi, reg, None)  # ör. Uzak Masaüstü oturumu: DXGI yalnız Microsoft sürücüsünü gösterir
    assert hw.list_gpus() == reg


def test_list_gpus_unknown_when_nothing_readable(sources):
    sources(OSError("DXGI yok"), OSError("kayıt defteri yok"), None)
    gpus = hw.list_gpus()
    assert gpus == [UNKNOWN_GPU]
    a = hw.advise(gpus, ram=16 * GIB, win_build=WIN11)
    assert (a.tier, a.offer, a.preselect, a.line) == ("unknown", True, False, "Ekran kartı belirlenemedi")


def test_list_gpus_empty_when_dxgi_sees_only_software(sources):
    sources([], [], None)  # ekran kartsız sanal makine (CI)
    assert hw.list_gpus() == []
    assert hw.advise([], ram=16 * GIB, win_build=WIN11).tier == "none"


def test_list_gpus_from_smi_alone(sources):
    sources(OSError("DXGI yok"), [], SMI_LINE)
    assert hw.list_gpus() == [Gpu("NVIDIA GeForce RTX 4070", "NVIDIA", 12282 * MIB, 0, False, "621.14")]


def test_list_gpus_never_raises(sources, monkeypatch, caplog):
    sources(RuntimeError("beklenmeyen"), ValueError("bozuk değer"), KeyError("smi"))
    assert hw.list_gpus() == [UNKNOWN_GPU]
    monkeypatch.setattr(hw, "_list_gpus", fake(ZeroDivisionError("hata")))
    with caplog.at_level(logging.ERROR, logger="altyazitr.hardware"):
        assert hw.list_gpus() == [UNKNOWN_GPU]
    assert "beklenmeyen hata" in caplog.text


# --- sistem bilgisi ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ac_line, flag, battery", [
    (1, 128, False),  # masaüstü: pil yok
    (1, 8, False),  # dizüstü, şarj oluyor
    (0, 1, True),  # dizüstü, pille
    (255, 255, None),  # bilinmiyor
])
def test_battery_state(ac_line, flag, battery):
    assert hw._battery_state(ac_line, flag) is battery


def test_free_space_failures(monkeypatch, tmp_path):
    def broken(_path):
        raise OSError(21, "aygıt hazır değil")

    monkeypatch.setattr(hw.shutil, "disk_usage", broken)
    assert hw.free_space(tmp_path) is None


def test_contract_signatures():
    """Başka modüller (ai_setup, ilk açılış penceresi) bu imzalara göre yazılıyor."""
    assert [f.name for f in dataclasses.fields(Gpu)] == ["name", "vendor", "vram", "shared", "integrated", "driver"]
    assert [f.name for f in dataclasses.fields(Advice)] == ["tier", "offer", "preselect", "model", "gpu", "line",
                                                            "warn", "tip"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        RTX4070.vram = 0  # type: ignore[misc]
    params = inspect.signature(hw.advise).parameters
    assert [(p.name, p.kind) for p in params.values()] == [
        ("gpus", inspect.Parameter.POSITIONAL_OR_KEYWORD), ("ram", inspect.Parameter.KEYWORD_ONLY),
        ("win_build", inspect.Parameter.KEYWORD_ONLY)]
    assert inspect.signature(hw.nvidia_smi).parameters["timeout"].default == 4.0
    for name, arity in (("list_gpus", 0), ("dxgi_adapters", 0), ("parse_nvidia_smi", 1), ("is_integrated", 3),
                        ("best_gpu", 1), ("system_ram", 0), ("free_space", 1), ("on_battery", 0)):
        assert len(inspect.signature(getattr(hw, name)).parameters) == arity, name


# --- bu makinede gerçek ölçüm (duman testleri) --------------------------------------------------------------------
@windows_only
def test_dxgi_smoke_on_this_machine():
    t0 = time.perf_counter()
    gpus = hw.dxgi_adapters()  # CI'daki ekran kartsız sanal makinede boş liste döner
    assert time.perf_counter() - t0 < 1.0
    for g in gpus:
        assert g.name and g.vram > 0 and g.vendor != "Microsoft", g
        assert g.integrated == hw.is_integrated(g.vendor, g.name, g.vram)


@windows_only
def test_registry_fallback_smoke_on_this_machine():
    reg = hw.registry_gpus()
    for g in reg:
        assert g.name and g.vendor != "Microsoft", g
    seen = {hw._name_key(g.name) for g in reg}
    assert {hw._name_key(g.name) for g in hw.dxgi_adapters() if not g.integrated} <= seen


@windows_only
def test_nvidia_smi_on_this_machine():
    text = hw.nvidia_smi()
    if text is None:
        pytest.skip("nvidia-smi yok ya da NVIDIA kartı yok")
    rows = hw.parse_nvidia_smi(text)
    assert rows and all(name and total > 0 for name, total, _used, _driver in rows), text


@windows_only
def test_system_info_on_this_machine(tmp_path):
    ram = hw.system_ram()
    assert ram is not None and ram > GIB
    free = hw.free_space(tmp_path)
    assert free is not None and free > 0
    assert hw.free_space(tmp_path / "henuz" / "yok") == pytest.approx(free, rel=0.01)  # var olan üst klasör
    missing = next((f"{c}:\\" for c in "ZYXWVUTSRQ" if not os.path.exists(f"{c}:\\")), None)
    if missing:
        assert hw.free_space(Path(missing) / "klasor") is None
    assert hw.on_battery() in (True, False, None)
    assert hw.windows_build() == sys.getwindowsversion().build >= 10240


@windows_only
def test_measurement_is_fast(monkeypatch):
    """Ölçümün kendisi (DXGI, kayıt defteri, bellek, disk, pil, Windows) 1 sn'nin çok altında kalmalı. nvidia-smi
    dışarıda tutulur: süresi sürücüye bağlı (tipik 0,1 sn; dizüstüde ekran kartını uyandırırsa daha uzun) ve
    4 sn zaman aşımıyla sınırlı. Gerçek nvidia-smi ile bütün ölçüm: aşağıdaki test."""
    monkeypatch.setattr(hw, "nvidia_smi", lambda timeout=4.0: None)
    t0 = time.perf_counter()
    hw.list_gpus()
    hw.registry_gpus()
    hw.system_ram()
    hw.free_space(Path.home())
    hw.on_battery()
    hw.windows_build()
    assert time.perf_counter() - t0 < 1.0


@windows_only
def test_full_measurement_and_advice_on_this_machine():
    t0 = time.perf_counter()
    gpus = hw.list_gpus()
    build = hw.windows_build()
    advice = hw.advise(gpus, ram=hw.system_ram(), win_build=build)
    hw.free_space(Path.home())
    hw.on_battery()
    assert time.perf_counter() - t0 < 4.5  # nvidia-smi takılsa bile zaman aşımı (4 sn) keser
    assert advice.tier in hw.TIERS and advice.line
    best = hw.best_gpu(gpus)
    if best is not None and "RTX" in best.name and best.vram >= 10.5 * GIB and build >= hw.MIN_WIN_BUILD:
        # ör. bu makinedeki RTX 4070 12 GB (yanında Ryzen'in tümleşik kartı) → good, gemma4:12b
        assert (advice.tier in ("good", "strong"), advice.model, advice.gpu) == (True, MODEL_FULL, best)
