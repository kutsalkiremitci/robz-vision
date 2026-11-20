; Robz Vision — Windows kurulum sihirbazı (Inno Setup 6)
;
; Önce uygulama derlenmelidir:  python -m PyInstaller RobzVision.spec   (çıktı: dist\RobzVision\)
; Sonra:                        ISCC installer\RobzVision.iss            (çıktı: dist\RobzVision-Setup-<sürüm>-x64.exe)
; Tek adımda:                   kurulum_paketi.bat
;
; Not: Ücretsiz çeviri modeli (~400 MB) kurulum dosyasına eklenmez; program ilk açılışta bir kez indirir.
; Ollama ve Gemma 4 de eklenmez: kullanıcı isterse program ilk açılışta ya da Çeviri sayfasından indirip kurar.
;
; Uygulama içinden güncelleme (altyazitr/updater.py) bu dosyayı GitHub sürümünden indirip doğrular ve sessiz kipte
; /GUNCELLEME /BEKLE=<süreç> ile çalıştırır (bkz. [Code]). Güncelleyici dosya adına (OutputBaseFilename), AppId'ye ve
; AppMutex'e dayanır; değiştirmeyin. Ayarlar (%APPDATA%\RobzVision) ve modeller (%LOCALAPPDATA%\RobzVision) kurulum
; klasörünün dışındadır: kurulum ve güncelleme onlara dokunmaz.

#define AppName "Robz Vision"
#ifndef AppVersion
  #define AppVersion "1.5.0"
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
WelcomeLabel2=[name/ver] bilgisayarınıza kurulacak.%n%nRobz Vision, oyunlardaki İngilizce altyazıları canlı olarak Türkçeye çevirir; çeviriyi ekranınızda ve OBS yayınınızda gösterir.%n%nİlk açılışta ücretsiz hızlı çeviri modeli (yaklaşık 400 MB) bir kez indirilir. İsterseniz yapay zekâ çevirisini de (Ollama ve Gemma 4, yaklaşık 10 GB) kurabilirsiniz.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Önceki sürümün kütüphane klasörü: yeni sürümde artık bulunmayan eski DLL ve modüller kalmasın (yalnız kurulum
; klasörünün içi; uygulama buraya çalışırken bir şey yazmaz).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\RobzVision\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallShortcut}"; Filename: "{uninstallexe}"
; Güncellemede yalnız zaten varsa yenilenir: kullanıcının sildiği masaüstü kısayolu geri gelmez (bkz. [Code])
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon; Check: MasaustuKisayoluKurulsun

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent; Check: not GuncellemeKipi
; Uygulama içinden güncelleme (/GUNCELLEME): kurulum bitince Robz Vision kendiliğinden yeniden açılır
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Flags: nowait; Check: GuncellemeKipi

[UninstallDelete]
; Program klasöründe çalışma sırasında oluşabilecek önbellek dosyaları
Type: filesandordirs; Name: "{app}\__pycache__"
; Programın indirdiği Ollama kurulum dosyası ve yarım indirmesi (ollama_install.DOWNLOAD_DIR). Kurulum başarılı
; olunca dosya zaten silinir; burada kalan, yarıda kalmış ya da başarısız kurulumdan artandır.
Type: filesandordirs; Name: "{localappdata}\RobzVision\indirilenler"
; Uygulama içinden güncellemenin indirdiği kurulum dosyaları (altyazitr/updater.py UPDATE_DIR; açılışta da temizlenir)
Type: filesandordirs; Name: "{localappdata}\RobzVision\guncelleme"
; Not: Ayarlar (%APPDATA%\RobzVision) ve indirilen hızlı çeviri modeli (%LOCALAPPDATA%\RobzVision\modeller)
; kaldırmada silinmez; program yeniden kurulursa model tekrar indirilmez. Ollama ayrı bir programdır: o ve
; modelleri (%USERPROFILE%\.ollama\models) kalır, Ollama kendi kaldırıcısıyla kaldırılır.

[Code]
// Uygulama içinden güncelleme (altyazitr/updater.py). Uygulama kurulumu şu parametrelerle başlatır ve hemen kapanır:
//   /SILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /SP- /NORESTARTAPPLICATIONS /GUNCELLEME /BEKLE=<süreç> /LOG=<dosya>
// /BEKLE: Robz Vision'ın süreci tamamen bitene kadar (en çok BEKLEME_MS) beklenir; dosyalar ancak sonra değişir.
//   Uygulama tek örnek kilidini (AppMutex) kurulumu başlatmadan önce bırakır: sessiz kurulum kilidi görüp durmaz.
// /GUNCELLEME: kurulum bitince uygulama yeniden açılır ([Run]); masaüstü kısayolu yalnız zaten varsa yenilenir
//   (kullanıcının sildiği kısayol geri gelmez, olan silinmez). Önceki seçimler (UsePreviousTasks) yine geçerlidir.
// Elle çalıştırılan kurulum (parametresiz) eskisi gibidir.

const
  SYNCHRONIZE = $00100000;
  BEKLEME_MS = 60000;

function OpenProcess(DesiredAccess: Cardinal; InheritHandle: BOOL; ProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function GuncellemeKipi(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/GUNCELLEME') = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function MasaustuKisayoluKurulsun(): Boolean;
begin
  Result := (not GuncellemeKipi()) or FileExists(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
end;

function InitializeSetup(): Boolean;
var
  Pid: Integer;
  H: THandle;
begin
  Result := True;
  Pid := StrToIntDef(ExpandConstant('{param:BEKLE|0}'), 0);
  if Pid <= 0 then
    Exit;
  H := OpenProcess(SYNCHRONIZE, False, Pid);
  if H = 0 then
    Exit; // süreç zaten bitmiş
  Log(Format('Robz Vision kapanana kadar bekleniyor (süreç %d)', [Pid]));
  if WaitForSingleObject(H, BEKLEME_MS) <> 0 then
    Log('Bekleme süresi doldu; kurulum sürüyor');
  CloseHandle(H);
end;
