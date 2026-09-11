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
  #define MyAppVersion "0.28.0"
#endif
#define MyAppPublisher "Tuna Birgün"
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
; The application holds this mutex while it runs (app/main.py). Setup checks it before
; InitializeSetup removes the previous version, so an open application makes Setup stop
; with the old version intact instead of uninstalling first and then aborting on a locked
; file. Force-closing covers helper processes (QtWebEngine) at the copy stage.
AppMutex=BulkSeqStudioRunning
CloseApplications=force

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

  { Never remove anything while the application is open: an older build (before the
    AppMutex existed) holds its files, the uninstaller then races the copy step, and the
    user is left with a half-deleted tree. Detect it by its main window title. }
  if FindWindowByWindowName('{#MyAppName}') <> 0 then
  begin
    Log('BulkSeq Studio main window found; refusing to modify the existing installation.');
    if not WizardSilent then
      MsgBox('BulkSeq Studio is running. Close it, then run Setup again.', mbError, MB_OK);
    Result := False;
    exit;
  end;

  { Read the install directory now, while the uninstall registry key still exists;
    the uninstaller deletes that key, so it cannot be read afterwards. }
  instLoc := InstalledLocation();

  if ver <> '' then
    verText := ' ' + ver
  else
    verText := '';

  { A caller that explicitly requested /SILENT or /VERYSILENT cannot answer a
    custom MsgBox. Treat that mode as the normal fresh-update choice; otherwise
    the setup process waits forever behind an invisible prompt. Interactive
    launches retain the explicit update / uninstall / cancel decision. }
  if WizardSilent then
    choice := IDYES
  else
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
    completes. It undoes the install in reverse order: the uninstall registry key,
    written last at install, is deleted first, and the uninstaller's own unins000.exe
    and unins000.dat go last. Waiting on the key alone let the old uninstaller finish
    after the new files were written and delete the new unins000.exe (seen in CI and
    locally), so wait until its files are gone as well, then give the temp copy a
    moment to exit (up to ~60 s, then proceed regardless). }
  uninst := RemoveQuotes(uninst);
  if uninst <> '' then
  begin
    Exec(uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, rc);
    waited := 0;
    while (StillInstalled() or FileExists(uninst) or FileExists(ChangeFileExt(uninst, '.dat')))
          and (waited < 60000) do
    begin
      Sleep(500);
      waited := waited + 500;
    end;
    Sleep(1500);
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
