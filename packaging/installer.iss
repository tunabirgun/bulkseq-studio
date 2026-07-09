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
  #define MyAppVersion "0.19.4"
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
// Detect an existing BulkSeq Studio install (its Inno uninstall registry key) and
// offer to update or uninstall it before continuing. The subkey below is hardcoded to
// the exact value Inno writes for this AppId (verified against the registry): a single
// leading brace and two trailing braces. SetupSetting("AppId") would emit the raw
// double-leading-brace form and never match, silently skipping the prompt.
const
  UNINST_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E4B2A1-6F1C-4E1A-9C2A-BULKSEQSTUDIO}}_is1';

function GetInstalledUninstaller(var UninstStr: String; var InstalledVer: String): Boolean;
begin
  InstalledVer := '';
  Result := RegQueryStringValue(HKCU, UNINST_KEY, 'UninstallString', UninstStr);
  if not Result then
    Result := RegQueryStringValue(HKLM, UNINST_KEY, 'UninstallString', UninstStr);
  if Result then
    if not RegQueryStringValue(HKCU, UNINST_KEY, 'DisplayVersion', InstalledVer) then
      RegQueryStringValue(HKLM, UNINST_KEY, 'DisplayVersion', InstalledVer);
end;

function StillInstalled(): Boolean;
var
  s: String;
begin
  Result := RegQueryStringValue(HKCU, UNINST_KEY, 'UninstallString', s)
         or RegQueryStringValue(HKLM, UNINST_KEY, 'UninstallString', s);
end;

function InstalledLocation(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UNINST_KEY, 'InstallLocation', Result) then
    RegQueryStringValue(HKLM, UNINST_KEY, 'InstallLocation', Result);
end;

function InitializeSetup(): Boolean;
var
  uninst, ver, verText, instLoc: String;
  choice, rc, waited: Integer;
begin
  Result := True;
  if not GetInstalledUninstaller(uninst, ver) then
    exit;

  { Read the install directory now, while the uninstall registry key still exists;
    the uninstaller deletes that key, so it cannot be read afterwards. }
  instLoc := InstalledLocation();

  if ver <> '' then
    verText := ' ' + ver
  else
    verText := '';

  choice := MsgBox(
    'BulkSeq Studio' + verText + ' is already installed.' + #13#10#13#10 +
    'Yes - remove it completely and install version {#MyAppVersion} fresh' + #13#10 +
    'No - uninstall BulkSeq Studio and exit' + #13#10 +
    'Cancel - do nothing',
    mbConfirmation, MB_YESNOCANCEL);

  if choice = IDCANCEL then
  begin
    Result := False;
    exit;
  end;

  { Both Update (Yes) and Uninstall (No) remove the existing version first. Inno's
    uninstaller relaunches itself from a temp copy, so Exec returns before removal
    completes; wait until the uninstall key is gone so a following install cannot race
    the old uninstaller (up to ~30 s, then proceed regardless). }
  uninst := RemoveQuotes(uninst);
  if uninst <> '' then
  begin
    Exec(uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, rc);
    waited := 0;
    while StillInstalled() and (waited < 30000) do
    begin
      Sleep(500);
      waited := waited + 500;
    end;
  end;

  { Guarantee a completely clean slate: delete any leftover install directory the
    uninstaller did not remove, so the new version installs fresh with no stale files.
    Gate on the app name so DelTree can never target an unrelated directory. }
  if (instLoc <> '') and (Pos('BulkSeq Studio', instLoc) > 0) and DirExists(instLoc) then
    DelTree(instLoc, True, True, True);

  if choice = IDNO then
  begin
    { Uninstall-only: stop after removing the old version. }
    MsgBox('BulkSeq Studio has been uninstalled.', mbInformation, MB_OK);
    Result := False;
  end;
end;
