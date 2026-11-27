; Starholm's per-user Windows installer (Inno Setup 6). ops/build-windows-app.ps1 compiles it with ISCC and passes:
;   /DAppVersion=1.0.5  /DStageDir=<the assembled app folder>  /DOutputDir=<dist>  /DIconFile=<starholm.ico>
;
; It installs for the current user only, into %LOCALAPPDATA%\Programs\Starholm, and asks for no admin rights. The
; app's own data lives in %LOCALAPPDATA%\Starholm (ops/windows-launcher.c), outside the install folder: uninstalling
; leaves it, so a reinstall finds the library, chats and settings again. Unsigned for now: Windows SmartScreen shows
; "Windows protected your PC" until the installer is signed (README.public.md says how to get past it).

#ifndef AppVersion
  #define AppVersion "1.0.5"
#endif

[Setup]
AppId={{63B4D449-2116-4BDC-A784-18C33E143B59}
AppName=Starholm
AppVersion={#AppVersion}
AppVerName=Starholm {#AppVersion}
AppPublisher=Starholm
AppPublisherURL=https://starholm.app
AppSupportURL=https://starholm.app
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Starholm
VersionInfoDescription=Starholm installer
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Starholm
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 version 22H2 (build 19045) or newer, as Ollama asks.
MinVersion=10.0.19045
OutputDir={#OutputDir}
OutputBaseFilename=Starholm-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Starholm.exe
UninstallDisplayName=Starholm
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a Starholm shortcut on the desktop"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; An older version's files go first, so nothing of it stays beside the new one.
[InstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\starholm"
Type: filesandordirs; Name: "{app}\poppler"

[Icons]
Name: "{userprograms}\Starholm"; Filename: "{app}\Starholm.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Starholm"; Filename: "{app}\Starholm.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Starholm.exe"; Description: "Open Starholm"; Flags: nowait postinstall skipifsilent
