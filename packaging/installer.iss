; Inno Setup script for BulkSeq Studio.
; Build the exe first (pyinstaller packaging/BulkSeqStudio.spec), then compile:
;   iscc packaging\installer.iss
; Per-user install (no admin): installs to %LOCALAPPDATA%\Programs\BulkSeq Studio,
; which keeps the bundled scripts/logs path writable when the app drives the WSL
; setup at runtime. Enabling WSL itself still prompts for elevation separately.

#define MyAppName "BulkSeq Studio"
; Version is normally passed by build_release.ps1 (/DMyAppVersion=...) from
; app/constants.py so the installer name never drifts from APP_VERSION; the
; fallback below is only used when compiling installer.iss by hand.
#ifndef MyAppVersion
  #define MyAppVersion "0.16.0"
#endif
#define MyAppPublisher "Tuna Birgün"
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
; Brand the wizard with the BulkSeq Studio logo instead of the default Inno artwork.
; Inno requires BMP; 1x and 2x variants are listed so it picks the right one per display DPI.
WizardImageFile=wizard_large.bmp,wizard_large@2x.bmp
WizardSmallImageFile=wizard_small.bmp,wizard_small@2x.bmp
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

[Code]
{ Detect an existing BulkSeq Studio install (its Inno uninstall registry key) and
  offer to update or uninstall it before continuing. SetupSetting("AppId") emits the
  exact AppId so the key matches whatever this script's AppId resolves to. }
function GetInstalledUninstaller(var UninstStr: String; var InstalledVer: String): Boolean;
var
  key: String;
begin
  key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1';
  InstalledVer := '';
  Result := RegQueryStringValue(HKCU, key, 'UninstallString', UninstStr);
  if not Result then
    Result := RegQueryStringValue(HKLM, key, 'UninstallString', UninstStr);
  if Result then
    if not RegQueryStringValue(HKCU, key, 'DisplayVersion', InstalledVer) then
      RegQueryStringValue(HKLM, key, 'DisplayVersion', InstalledVer);
end;

function InitializeSetup(): Boolean;
var
  uninst, ver, verText: String;
  choice, rc: Integer;
begin
  Result := True;
  if not GetInstalledUninstaller(uninst, ver) then
    exit;

  if ver <> '' then
    verText := ' ' + ver
  else
    verText := '';

  choice := MsgBox(
    'BulkSeq Studio' + verText + ' is already installed.' + #13#10#13#10 +
    'Yes - remove it and install version {#MyAppVersion}' + #13#10 +
    'No - uninstall BulkSeq Studio and exit' + #13#10 +
    'Cancel - do nothing',
    mbConfirmation, MB_YESNOCANCEL);

  if choice = IDCANCEL then
  begin
    Result := False;
    exit;
  end;

  { Both Update (Yes) and Uninstall (No) remove the existing version first. }
  uninst := RemoveQuotes(uninst);
  if uninst <> '' then
    Exec(uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, rc);

  if choice = IDNO then
  begin
    { Uninstall-only: stop after removing the old version. }
    MsgBox('BulkSeq Studio has been uninstalled.', mbInformation, MB_OK);
    Result := False;
  end;
end;
