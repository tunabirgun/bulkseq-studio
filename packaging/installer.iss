; Inno Setup script for BulkSeq Studio.
; Build the exe first (pyinstaller packaging/BulkSeqStudio.spec), then compile:
;   iscc packaging\installer.iss
; Per-user install (no admin): installs to %LOCALAPPDATA%\Programs\BulkSeq Studio,
; which keeps the bundled scripts/logs path writable when the app drives the WSL
; setup at runtime. Enabling WSL itself still prompts for elevation separately.

#define MyAppName "BulkSeq Studio"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Tuna Birgun"
#define MyAppExeName "BulkSeqStudio.exe"

[Setup]
AppId={{B7E4B2A1-6F1C-4E1A-9C2A-BULKSEQSTUDIO}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=BulkSeqStudio-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=..\app\assets\icons\bulkseq.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\BulkSeq Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
