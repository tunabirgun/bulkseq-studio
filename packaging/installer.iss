; Inno Setup script for BulkSeq Studio.
; Build through scripts\build_release.ps1. Manual compiles must supply a reviewed
; BuildSource onedir and a new PackageDir through /D definitions.
; Per-user install (no admin): installs to %LOCALAPPDATA%\Programs\BulkSeq Studio.
; The backend setup log is written under the user's application-data directory, so the
; bundled scripts remain read-only. Enabling WSL itself still prompts for elevation separately.

#define MyAppName "BulkSeq Studio"
; The build helper passes these macros from the verified frozen stage.
#ifndef MyAppVersion
  #define MyAppVersion "0.34.0"
#endif
#ifndef BuildSource
  #error BuildSource must name the verified frozen onedir
#endif
#ifndef PackageDir
  #error PackageDir must name a new package output directory
#endif
#define MyAppPublisher "Tuna Birgun"
#define MyAppExeName "BulkSeqStudio.exe"

[Setup]
AppId={{B7E4B2A1-6F1C-4E1A-9C2A-BULKSEQSTUDIO}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir={#PackageDir}
OutputBaseFilename=BulkSeqStudio-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile=..\app\assets\icons\bulkseq.ico
; Brand the wizard with the BulkSeq Studio logo instead of the default Inno artwork.
; Inno requires BMP; 1x and 2x variants are listed so it picks the right one per display DPI.
WizardImageFile=wizard_large.bmp,wizard_large@2x.bmp
WizardSmallImageFile=wizard_small.bmp,wizard_small@2x.bmp
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; The application holds this mutex while it runs (app/main.py). An open application
; stops the in-place update before files are copied. Force-closing covers helper
; processes (QtWebEngine) at the copy stage.
AppMutex=BulkSeqStudioRunning
CloseApplications=force
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#BuildSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := FindWindowByWindowName('{#MyAppName}') = 0;
  if not Result then
  begin
    Log('BulkSeq Studio is running; leaving the existing installation untouched.');
    if not WizardSilent then
      MsgBox('BulkSeq Studio is running. Close it, then run Setup again.', mbError, MB_OK);
  end;
end;
