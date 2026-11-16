# -*- mode: python ; coding: utf-8 -*-
# PyInstaller tanımı:  python -m PyInstaller RobzVision.spec --noconfirm --clean
# Çıktı: dist/RobzVision/RobzVision.exe  (onedir; Qt DLL'leri ayrı dosyalar olarak kalır — LGPL uyumu)
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = [
    ("altyazitr/web", "altyazitr/web"),
    ("altyazitr/data", "altyazitr/data"),
    ("THIRD_PARTY_NOTICES.md", "."),
]
binaries = []

# Windows OCR için YALNIZCA kullanılan pywinrt modülleri. (Tümünü toplamak, kullanılmayan ~80 paketin çok uzun
# adlı .dist-info klasörlerini ekliyor ve uzun kurulum yollarında Windows'un 260 karakter sınırını aşıyordu.)
WINRT = ["winrt.runtime", "winrt.system", "winrt.windows.foundation", "winrt.windows.foundation.collections",
         "winrt.windows.globalization", "winrt.windows.graphics.imaging", "winrt.windows.media.ocr",
         "winrt.windows.storage.streams"]
hiddenimports = ["winrt._winrt"]
for mod in WINRT:
    hiddenimports += collect_submodules(mod)

for pkg in ("ctranslate2", "onnxruntime", "windows_capture"):
    binaries += collect_dynamic_libs(pkg)
for pkg in ("rapidocr",):
    datas += collect_data_files(pkg)

a = Analysis(
    ["altyazitr/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["altyazitr.gui.main_window", "altyazitr.gui.setup_dialog", "altyazitr.gui.tour"],
    excludes=["tkinter", "torch", "matplotlib", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
              "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RobzVision",
    icon="assets/robzvision.ico",
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RobzVision")
