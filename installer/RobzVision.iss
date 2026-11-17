; Robz Vision — Windows kurulum sihirbazı (Inno Setup 6)
;
; Önce uygulama derlenmelidir:  python -m PyInstaller RobzVision.spec   (çıktı: dist\RobzVision\)
; Sonra:                        ISCC installer\RobzVision.iss            (çıktı: dist\RobzVision-Setup-<sürüm>-x64.exe)
; Tek adımda:                   kurulum_paketi.bat
;
; Not: Ücretsiz çeviri modeli (~400 MB) kurulum dosyasına eklenmez; program ilk açılışta bir kez indirir.

#define AppName "Robz Vision"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "RobzVision.exe"
#define AppPublisher "Robz Vision"

[Setup]
; AppId kurulumlar arasında aynı kalmalıdır (güncelleme/kaldırma bu kimlikle eşleşir).
AppId={{20358419-F95F-4F60-86CE-3AFF9A70B062}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=MIT lisansı
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} kurulumu
VersionInfoProductName={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\assets\robzvision.ico
WizardStyle=modern
WizardImageFile=wizard-164.bmp,wizard-246.bmp,wizard-328.bmp
WizardSmallImageFile=small-55.bmp,small-83.bmp,small-110.bmp
WizardImageStretch=no
OutputDir=..\dist
OutputBaseFilename=RobzVision-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
; x64compatible: x64 Windows ve x64 programları öykünmeyle çalıştırabilen ARM'li Windows 11.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
; Kullanıcı hesabına kurulum: yönetici onayı (UAC) istemez, %LOCALAPPDATA%\Programs\Robz Vision'a kurar ve
; yine "Yüklü uygulamalar" listesinde görünür. Tüm kullanıcılar için: Kurulum.exe /ALLUSERS
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
; Program açıksa kurulum/kaldırma önce kapatılmasını ister (uygulamanın tek örnek kilidi).
AppMutex=RobzVision_TekOrnek
CloseApplications=yes
RestartApplications=no
; Program yalnızca Türkçe olduğu için kurulum da her zaman Türkçe açılır.
ShowLanguageDialog=no
LanguageDetectionMethod=none

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
LaunchApp=Robz Vision'ı başlat
UninstallShortcut=Robz Vision'ı kaldır

[Messages]
; Not: [Messages] içinde {cm:...} gibi sabitler açılmaz; metin doğrudan buraya yazılmalı.
WelcomeLabel2=[name/ver] bilgisayarınıza kurulacak.%n%nRobz Vision, oyunlardaki İngilizce altyazıları canlı olarak Türkçeye çevirir; çeviriyi ekranınızda ve OBS yayınınızda gösterir.%n%nİlk açılışta ücretsiz çeviri modeli (yaklaşık 400 MB) bir kez indirilir.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\RobzVision\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallShortcut}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program klasöründe çalışma sırasında oluşabilecek önbellek dosyaları
Type: filesandordirs; Name: "{app}\__pycache__"
; Not: Ayarlar (%APPDATA%\RobzVision) ve indirilen model (%LOCALAPPDATA%\RobzVision) kaldırmada silinmez;
; program yeniden kurulursa model tekrar indirilmez.
