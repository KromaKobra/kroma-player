<#
.SYNOPSIS
    Install, update and uninstall a Kroma Player build the way a user's machine
    would, and fail on the first thing that is wrong.

.DESCRIPTION
    Run by .github/workflows/release.yml on windows-latest, after the installer
    has been built and before anything is published:

        pwsh packaging/smoke-installer.ps1 -Dist dist\kroma-player `
            -Installer dist\release\KromaPlayerSetup-1.2.0-x86_64.exe `
            -Version 1.2.0 -Work "$env:RUNNER_TEMP\kroma smoke test"

    Six steps, each one a thing that has only ever been checked on a real
    Windows machine at the end of an update:

      1. The bare PyInstaller tree passes --smoke-test and calls itself portable.
      2. A silent per-user install lands its files, its uninstall key and an exe
         whose version resource says what it is.
      3. The installed copy passes --smoke-test and calls itself an installed,
         per-user, updatable copy.
      4. The bare tree is *still* portable with that install on the machine —
         the uninstall key names a different folder.
      5. The real update path. The installed app is started and left holding
         its mutex; the app's own run_installer() hands off, exactly as
         "Update and restart" does; the installer waits for the app to exit,
         replaces _internal\ (a planted stale file must be gone), and /RELAUNCH
         starts the new copy.
      6. A silent uninstall leaves no folder and no key.

    -Work should contain a space: so does every real install path, starting
    with "%LOCALAPPDATA%\Programs\Kroma Player". Every Inno run writes a /LOG,
    and every smoke report is kept, under <Work>\logs, for the workflow to
    upload whether or not this passed.

    Needs the repository's Python environment for step 5 (-Repo, default the
    current directory), since that step calls app.update.apply directly.
#>
param(
    [Parameter(Mandatory)] [string] $Dist,
    [Parameter(Mandatory)] [string] $Installer,
    [Parameter(Mandatory)] [string] $Version,
    [Parameter(Mandatory)] [string] $Work,
    [string] $Repo = (Get-Location).Path,
    [string] $Python = "python",
    [int] $TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The AppId in packaging/kroma-player.iss, plus Inno's `_is1`. app/update/install_kind.py
# reads the same key.
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{CC0544F7-D2E1-41E7-AE17-1219E3363D15}_is1'
$ExeName = 'kroma-player.exe'

$Dist = (Resolve-Path $Dist).Path
$Installer = (Resolve-Path $Installer).Path
$Repo = (Resolve-Path $Repo).Path
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Work = (Resolve-Path $Work).Path
$Logs = Join-Path $Work 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# A CI runner's display has no GPU worth the name. Software rendering is what
# makes QML draw there at all; it changes nothing the test is about.
if (-not $env:QT_QUICK_BACKEND) { $env:QT_QUICK_BACKEND = 'software' }

function Step([string] $Text) { Write-Host "`n=== $Text" -ForegroundColor Cyan }

function Assert-That([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Host "  ok  $Message"
}

function Wait-Until([scriptblock] $Condition, [int] $Seconds, [string] $What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "FAILED: timed out after $Seconds s waiting for $What"
}

function Read-Report([string] $Path) {
    $report = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    foreach ($stage in $report.stages) {
        $mark = if ($stage.ok) { 'ok  ' } else { 'FAIL' }
        $note = if ($stage.ok) { '' } else { " — $($stage.error)" }
        Write-Host "  $mark stage $($stage.name) ($($stage.seconds) s)$note"
    }
    return $report
}

# Run an exe's --smoke-test to completion and return its report. The exe is a
# windowed program, so `&` would not wait for it; Start-Process -Wait does.
function Invoke-Smoke([string] $Exe, [string] $Name) {
    $report = Join-Path $Logs "smoke-$Name.json"
    Remove-Item -LiteralPath $report -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $Exe -ArgumentList "`"--smoke-test=$report`"" -PassThru -Wait
    Assert-That (Test-Path -LiteralPath $report) "$Name wrote a smoke report"
    $parsed = Read-Report $report
    Assert-That ($process.ExitCode -eq 0) "$Name exited 0 (got $($process.ExitCode))"
    Assert-That ([bool] $parsed.ok) "$Name passed every smoke stage"
    return $parsed
}

function Stop-Kroma([string] $Folder) {
    Get-Process -Name 'kroma-player', 'kroma-plugin-host' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Folder, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

$installRoot = $null
try {
    # ----------------------------------------------------------------- 1
    Step 'The bare build is a portable copy'
    $portable = Join-Path $Work 'portable copy'
    Remove-Item -LiteralPath $portable -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $Dist -Destination $portable -Recurse
    $portableExe = Join-Path $portable $ExeName
    $report = Invoke-Smoke $portableExe 'portable'
    Assert-That ($report.install_kind -eq 'portable') "the bare tree reports portable (got $($report.install_kind))"
    Assert-That ($report.update_eligibility -eq 'portable') "and is not offered in-app updates"

    # ----------------------------------------------------------------- 2
    Step 'A silent per-user install'
    $installLog = Join-Path $Logs 'inno-install.log'
    $process = Start-Process -FilePath $Installer -PassThru -Wait -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/CURRENTUSER', '/NORESTART', "`"/LOG=$installLog`""
    )
    Assert-That ($process.ExitCode -eq 0) "the installer exited 0 (got $($process.ExitCode))"
    Assert-That (Test-Path -LiteralPath $UninstallKey) 'the uninstall key exists under HKCU'
    $entry = Get-ItemProperty -LiteralPath $UninstallKey
    $installRoot = $entry.'Inno Setup: App Path'
    Assert-That ([bool] $installRoot) "the key names the install folder ($installRoot)"
    Assert-That ($entry.DisplayVersion -eq $Version) "DisplayVersion is $Version (got $($entry.DisplayVersion))"
    $installedExe = Join-Path $installRoot $ExeName
    Assert-That (Test-Path -LiteralPath $installedExe) "$ExeName is installed"
    Assert-That (Test-Path -LiteralPath (Join-Path $installRoot '_internal')) '_internal\ is installed'
    Assert-That (Test-Path -LiteralPath (Join-Path $installRoot 'kroma-plugin-host.exe')) 'the plugin host is installed'
    $info = (Get-Item -LiteralPath $installedExe).VersionInfo
    Assert-That ($info.ProductVersion -eq $Version) "the exe's version resource says $Version (got $($info.ProductVersion))"
    Assert-That ($info.FileDescription -eq 'Kroma Player') "and calls itself Kroma Player (got $($info.FileDescription))"

    # ----------------------------------------------------------------- 3
    Step 'The installed copy knows it is installed'
    $report = Invoke-Smoke $installedExe 'installed'
    Assert-That ($report.install_kind -eq 'windows_installer') "it reports windows_installer (got $($report.install_kind))"
    Assert-That ($report.installer_scope -eq 'user') "with scope user (got $($report.installer_scope))"
    Assert-That ($report.update_eligibility -eq '') "and may update itself (got '$($report.update_eligibility)')"

    # ----------------------------------------------------------------- 4
    Step 'A portable copy beside an install is still portable'
    $report = Invoke-Smoke $portableExe 'portable-beside-install'
    Assert-That ($report.install_kind -eq 'portable') "the bare tree still reports portable (got $($report.install_kind))"

    # ----------------------------------------------------------------- 5
    Step 'Updating a running copy'
    $stale = Join-Path $installRoot '_internal\stale-from-the-old-build.txt'
    Set-Content -LiteralPath $stale -Value 'left behind by a previous build'

    $lingerReport = Join-Path $Logs 'smoke-linger.json'
    Remove-Item -LiteralPath $lingerReport -ErrorAction SilentlyContinue
    $running = Start-Process -FilePath $installedExe -PassThru -ArgumentList @(
        "`"--smoke-test=$lingerReport`"", '--smoke-linger=15'
    )
    # The report is written before the linger starts, so once it exists the
    # process is sitting on its mutex exactly as a copy that has just handed off.
    Wait-Until { Test-Path -LiteralPath $lingerReport } $TimeoutSeconds 'the running copy to finish starting'
    Assert-That (-not $running.HasExited) 'the running copy is holding on'

    $updateLog = Join-Path $Logs 'inno-update.log'
    $env:KROMA_SMOKE_REPO = $Repo
    $env:KROMA_SMOKE_INSTALLER = $Installer
    $env:KROMA_SMOKE_INSTALL_LOG = $updateLog
    # The app's own hand-off — the same function "Update and restart" calls,
    # with the same switches, scope and environment.
    & $Python -c "import os, sys; sys.path.insert(0, os.environ['KROMA_SMOKE_REPO']); from pathlib import Path; from app.update.apply import run_installer; sys.exit(0 if run_installer(Path(os.environ['KROMA_SMOKE_INSTALLER']), scope='user', log=Path(os.environ['KROMA_SMOKE_INSTALL_LOG'])) else 1)"
    Assert-That ($LASTEXITCODE -eq 0) 'run_installer() started the installer'

    Wait-Until { (Test-Path -LiteralPath $updateLog) -and (Select-String -LiteralPath $updateLog -Pattern 'Log closed' -Quiet) } $TimeoutSeconds 'the update to finish'
    $finished = Get-Date
    $logText = Get-Content -LiteralPath $updateLog -Raw
    Assert-That ($logText -match 'Kroma Player exited after \d+ ms') 'the installer waited for the running copy to exit'
    Assert-That ($logText -notmatch 'still running after') 'and did not time out doing so'
    Assert-That ($running.HasExited) 'the old process is gone'
    Assert-That ($running.ExitTime -lt $finished) 'it exited before the install finished'
    Assert-That (-not (Test-Path -LiteralPath $stale)) '[InstallDelete] cleared the stale file from _internal\'
    Assert-That (Test-Path -LiteralPath $installedExe) "$ExeName is back in place"

    $relaunched = $null
    Wait-Until {
        $script:relaunched = Get-Process -Name 'kroma-player' -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -ne $running.Id -and $_.Path -and ($_.Path -ieq $installedExe) } |
            Select-Object -First 1
        [bool] $script:relaunched
    } 60 '/RELAUNCH to start the updated copy'
    Assert-That ([bool] $relaunched) "/RELAUNCH started a new $ExeName (pid $($relaunched.Id))"
    # It is the real app now, with no smoke flag, putting up the vault picker on
    # a machine with no vault. Nothing to wait for.
    Stop-Kroma $installRoot
    Start-Sleep -Seconds 2

    # ----------------------------------------------------------------- 6
    Step 'A silent uninstall'
    $uninstaller = Join-Path $installRoot 'unins000.exe'
    Assert-That (Test-Path -LiteralPath $uninstaller) 'the uninstaller is installed'
    $uninstallLog = Join-Path $Logs 'inno-uninstall.log'
    $process = Start-Process -FilePath $uninstaller -PassThru -Wait -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "`"/LOG=$uninstallLog`""
    )
    Assert-That ($process.ExitCode -eq 0) "the uninstaller exited 0 (got $($process.ExitCode))"
    # It hands itself off to a copy in %TEMP% so it can delete its own folder,
    # and that copy may still be finishing.
    Wait-Until { -not (Test-Path -LiteralPath $installRoot) -and -not (Test-Path -LiteralPath $UninstallKey) } 60 'the uninstall to finish'
    Assert-That (-not (Test-Path -LiteralPath $installRoot)) 'the install folder is gone'
    Assert-That (-not (Test-Path -LiteralPath $UninstallKey)) 'the uninstall key is gone'
    $installRoot = $null

    Write-Host "`nAll installer checks passed." -ForegroundColor Green
}
finally {
    if ($installRoot) { Stop-Kroma $installRoot }
    Stop-Kroma $Work
    # The app's own log, which is where a smoke stage's traceback also goes.
    $appLogs = Join-Path $env:LOCALAPPDATA 'Kroma\logs'
    if (Test-Path -LiteralPath $appLogs) {
        Copy-Item -Path (Join-Path $appLogs '*') -Destination $Logs -ErrorAction SilentlyContinue
    }
}
