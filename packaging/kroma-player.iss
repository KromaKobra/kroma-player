; Kroma Player — Windows installer.
;
; Built by .github/workflows/release.yml after PyInstaller has produced
; dist\kroma-player\, and by hand with:
;
;     iscc /DAppVersion=1.2.0 /DAppRepo=KromaKobra/kroma-player packaging\kroma-player.iss
;
; packaging/smoke-installer.ps1 installs, updates and uninstalls what this
; produces on a Windows runner, so the paths through it below are exercised on
; every release rather than only on a user's machine.
;
; Why an installer at all, when the app is a self-contained folder: because a
; running program cannot replace itself, and Windows has no package manager in
; the loop to do it. An installer solves that with mechanisms the OS already
; provides — the Restart Manager closes the running copy and brings it back —
; so the app hands off and quits rather than spawning a helper to swap its own
; directory out from under itself. See ADR 0013.
;
; The whole update path is "run the new installer over the top". There is no
; separate update mechanism on Windows and this script is not trying to be one.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; The GitHub repository releases come from. CI passes /DAppRepo=$GITHUB_REPOSITORY
; so the links Windows shows for the install point where the build was
; published; the default is REPO in app/update/release.py, and
; tests/test_update_apply.py holds the two equal.
#ifndef AppRepo
  #define AppRepo "KromaKobra/kroma-player"
#endif

#define AppName        "Kroma Player"
#define AppExeName     "kroma-player.exe"
#define AppPublisher   "Kroma Kobra"
#define AppUrl         "https://github.com/" + AppRepo

; The named mutex app/main.py holds for the life of the process
; (SINGLE_INSTANCE_MUTEX there; the test suite holds the two equal). Used twice
; below: by AppMutex, and by InitializeSetup() to wait out the copy that just
; asked for this update.
#define AppMutexName   "KromaPlayerSingleInstance"

; How long a silent update waits for the app that started it to finish
; quitting, polling every MutexPollMs. The app ends itself after 60 s whatever
; is holding it up (EXIT_WATCHDOG_SECONDS in app/viewmodels/updates_view_model.py),
; so this is that plus a margin, and a timeout here means something else is
; holding the mutex.
#define MutexWaitMs    90000
#define MutexPollMs    250

[Setup]
; The identity Windows keys the uninstall entry and every future upgrade on.
; Generated once, and never to be changed: a new AppId does not upgrade the
; existing install, it installs a second copy beside it, and the user is left
; with two entries in Settings → Apps and no way to tell which is which.
; app/update/install_kind.py reads the registry key derived from this value to
; tell an installed copy from an unzipped portable one, so the two must agree.
AppId={{CC0544F7-D2E1-41E7-AE17-1219E3363D15}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases

; Per-user by default, and this is the single most important line in the file.
; PrivilegesRequired=lowest resolves {autopf} to {localappdata}\Programs, which
; needs no administrator rights — so there is no UAC prompt on install and,
; crucially, none on update either. Into Program Files every update would need
; elevation, which is exactly the friction that kills self-updating.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
; A user who genuinely wants a machine-wide install can still ask for one; they
; then own the elevation prompt on every update, which is their choice to make.
PrivilegesRequiredOverridesAllowed=dialog

; Detecting the running app. AppMutex is the cheap path — app/main.py creates
; this named mutex at startup and holds it for the life of the process, so a
; user double-clicking the installer over a running copy is told to close it
; rather than being handed a half-replaced install. CloseApplications is the
; thorough path: the Restart Manager finds whatever holds the files being
; replaced and closes it, and RestartApplications brings it back. Both are
; needed — the mutex is what makes the *manual* case civil, the Restart Manager
; is what makes the silent case work at all.
;
; Kept for the update path too, behind a wait. A silent update is started by the
; app moments before it quits, so the mutex is still held when Setup starts and
; this check alone would say "Kroma Player is currently running" to a user who
; has just asked for it to close. InitializeSetup() below runs first and waits
; for the mutex to go; this check then only ever sees a copy that did not quit.
; The uninstaller uses the same check, unchanged.
AppMutex={#AppMutexName}
CloseApplications=yes
RestartApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd

UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputBaseFilename=KromaPlayerSetup-{#AppVersion}-x86_64
; Relative to *this script*, not to the working directory — the same rule the
; Source line below follows. `dist\installer` would land under packaging\.
OutputDir=..\dist\installer
SetupIconFile=..\assets\icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The onedir bundle is around 215 MB; saying so up front stops the estimate
; being read off the compressed size.
LicenseFile=
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the runtime outright rather than layering over it. Without
; this, a module, a Qt plugin or a DLL that the new build no longer ships stays
; in _internal\ forever, and the next PyInstaller or Qt upgrade can pick the
; stale one up. Only _internal\: the executables beside it are overwritten by
; [Files] anyway, and a user who dropped ffmpeg.exe next to kroma-player.exe —
; which the Dependencies pane suggests — keeps it. Nothing here is undone if
; Setup is cancelled, which is why app/update/apply.py passes /NOCANCEL.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The whole PyInstaller onedir tree, _internal\ and all. recursesubdirs is what
; makes that one line rather than a manifest that would go stale on every Qt
; upgrade.
Source: "..\dist\kroma-player\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Two entries, and they are mutually exclusive by design.
;
; The first is the ordinary "Launch Kroma Player" checkbox at the end of the
; wizard. `skipifsilent` suppresses it during a silent run, which is correct for
; a checkbox nobody is there to tick.
;
; The second is the silent-update relaunch, and it exists because `skipifsilent`
; does exactly the wrong thing there: a user who clicked "Install and restart"
; is owed the restart. RestartApplications cannot be relied on for it either —
; the Restart Manager only brings back processes that registered with it, and
; the app has by then exited on its own. So app/update/apply.py passes /RELAUNCH
; and this entry honours it. `runasoriginaluser` because an update of a
; machine-wide install runs elevated, and the app it starts must not.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall runasoriginaluser; Check: WantsRelaunch

[UninstallDelete]
; PyInstaller's tree and Qt's QML cache both leave __pycache__ and compiled
; artefacts that were not in the file list, so the folder would otherwise
; survive the uninstall holding nothing the user wants. Nothing the *user* owns
; is in here — the vault and kord-config.json both live elsewhere — so removing
; the lot is safe. See ADR 0013.
Type: filesandordirs; Name: "{app}"

[Code]
{ Whether Setup was started with the switch Name, compared case-insensitively.

  Deliberately a plain command-line scan rather than anything cleverer: the
  switches it is asked about are ones this installer invents, Inno passes
  unrecognised switches through untouched, and the app is the only thing that
  ever sets them. }
function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ True when the app asked for this install and wants to be started again.
  Checked by the second [Run] entry above. }
function WantsRelaunch(): Boolean;
begin
  Result := HasSwitch('/RELAUNCH');
end;

{ Wait for the copy that asked for this update to finish quitting.

  Inno calls this before its AppMutex check. During an update the app has
  started Setup and is on its way out, so the mutex is usually still held for a
  moment; without this wait the check would report the app as running to a user
  who has just asked for it to close. Only for /RELAUNCH, which only the app
  passes: a user running the installer by hand gets the ordinary prompt.

  A timeout is not a failure here. It falls through to the AppMutex check, whose
  prompt is the right thing to show for a copy that genuinely did not quit.
  Every outcome is logged, and packaging/smoke-installer.ps1 looks for the
  "exited after" line. }
function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Result := True;
  if not HasSwitch('/RELAUNCH') then
    Exit;
  if not CheckForMutexes('{#AppMutexName}') then
  begin
    Log('Kroma Player is not running; nothing to wait for.');
    Exit;
  end;

  Log('Kroma Player is still running; waiting for it to exit.');
  Waited := 0;
  while CheckForMutexes('{#AppMutexName}') and (Waited < {#MutexWaitMs}) do
  begin
    Sleep({#MutexPollMs});
    Waited := Waited + {#MutexPollMs};
  end;

  if CheckForMutexes('{#AppMutexName}') then
    Log(Format('Kroma Player was still running after %d ms; leaving it to the AppMutex check.', [Waited]))
  else
    Log(Format('Kroma Player exited after %d ms.', [Waited]));
end;
