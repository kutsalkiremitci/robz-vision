@echo off
rem Arkadaslara gonderilecek kurulum dosyasini olusturur: dist\RobzVision-Kurulum-<surum>-x64.exe
rem Gerekli: kurulum.bat ile hazirlanmis .venv ve Inno Setup 6 (https://jrsoftware.org/isdl.php)
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Once kurulum.bat dosyasini calistirin.
    pause
    exit /b 1
)

set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo [HATA] Inno Setup 6 bulunamadi.
    echo        Kurmak icin: winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

for /f "delims=" %%V in ('".venv\Scripts\python.exe" -c "import altyazitr; print(altyazitr.__version__)"') do set "APPVER=%%V"

echo [1/3] Derleme araclari kontrol ediliyor...
rem Yalnizca derleme araclari kurulur: program acikken calisan paketlere (ornegin OpenCV) dokunulmaz.
".venv\Scripts\python.exe" -m pip install -q pyinstaller pillow || (pause & exit /b 1)

echo [2/3] Uygulama derleniyor (PyInstaller)...
".venv\Scripts\python.exe" tools\kurulum_gorselleri.py || (pause & exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller RobzVision.spec --noconfirm --clean || (pause & exit /b 1)
copy /y README.md dist\RobzVision\ >nul
copy /y LICENSE dist\RobzVision\ >nul
copy /y THIRD_PARTY_NOTICES.md dist\RobzVision\ >nul

echo [3/3] Kurulum dosyasi olusturuluyor (Inno Setup)...
"%ISCC%" /Qp /DAppVersion=%APPVER% installer\RobzVision.iss || (pause & exit /b 1)

echo.
echo Hazir: dist\RobzVision-Kurulum-%APPVER%-x64.exe
pause
