@echo off
rem Windows icin tasinabilir surum olusturur: dist\RobzVision\RobzVision.exe
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Once kurulum.bat dosyasini calistirin.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q pyinstaller pillow || (pause & exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller RobzVision.spec --noconfirm --clean || (pause & exit /b 1)
copy /y README.md dist\RobzVision\ >nul
copy /y LICENSE dist\RobzVision\ >nul
copy /y THIRD_PARTY_NOTICES.md dist\RobzVision\ >nul
echo.
echo Derleme tamam: dist\RobzVision\RobzVision.exe
pause
