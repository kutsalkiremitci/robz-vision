@echo off
setlocal
cd /d "%~dp0"
title Robz Vision kurulum

echo ============================================
echo   Robz Vision - kurulum
echo ============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=py -3.12"
    py -3.12 -c "import sys" >nul 2>nul || set "PYCMD=py -3"
) else (
    set "PYCMD=python"
)

%PYCMD% -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
if errorlevel 1 (
    echo [HATA] Python 3.11 veya ustu bulunamadi.
    echo        https://www.python.org/downloads/ adresinden Python 3.12 kurun
    echo        ^(kurulumda "Add python.exe to PATH" kutusunu isaretleyin^).
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam olusturuluyor...
    %PYCMD% -m venv .venv || (echo [HATA] Sanal ortam olusturulamadi. & pause & exit /b 1)
)

echo Bagimliliklar kuruluyor ^(ilk seferde birkac dakika surebilir^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [HATA] Paketler kurulamadi. & pause & exit /b 1)

echo.
echo Kurulum tamamlandi. Programi baslatmak icin baslat.bat dosyasini calistirin.
echo Ilk baslatmada ucretsiz ceviri modeli ^(~400 MB^) bir kez indirilir.
pause
