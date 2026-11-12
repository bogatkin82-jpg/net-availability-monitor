; Inno Setup: установщик net-availability-monitor для Windows (x64, per-user).
; Собирается в CI:  ISCC.exe windows\installer.iss
; Выход: dist\NetMonitor-Setup-x64.exe (подписывается отдельным шагом CI).

#define MyAppName "net-availability-monitor"
#ifndef MyAppVersion
  #define MyAppVersion "1.5.3"
#endif
#define MyAppPublisher "net-availability-monitor"
#define MyAppURL "https://github.com/bogatkin82-jpg/net-availability-monitor"
#define MyAppExeName "NetMonitor.exe"

[Setup]
AppId={{6E2F6C1B-3B7E-4E1E-9C0A-7C4F2D8A1B55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\net-availability-monitor
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=NetMonitor-Setup-x64
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=net-availability-monitor installer

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Запускать при входе в Windows"; GroupDescription: "Дополнительно:"; Flags: unchecked
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.json"; DestDir: "{app}"; DestName: "config.json.example"; Flags: ignoreversion
Source: "..\docs\windows.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "..\windows\netmon-code-signing.cer"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NetMonitor"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
