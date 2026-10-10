"""Handing off to the installer, and swapping the AppImage.

Two arms, tested very differently on purpose.

The Windows arm asserts the **exact argv** handed to ``Popen``, because that is
the thing that silently breaks: a switch dropped or misspelled produces an
installer that puts up a wizard nobody is there to click, or one that finishes
and never restarts the app, and neither shows up anywhere but on a real Windows
machine at the end of an update.

The Linux arm does the real thing — a genuine ``os.replace`` over a dummy file
in ``tmp_path`` with ``$APPIMAGE`` pointed at it — with only ``execve`` stubbed,
since the alternative is a test that replaces the pytest process with a text
file. The mode is checked after the rename, because a build that lands without
its executable bit is an application the user can no longer start and there is
nothing left running to fix it.

Both arms hand the next build a cleaned environment, and the installer script
has to agree with this module about several names. Those agreements are
checked against the ``.iss`` text, since the script itself only runs on
Windows (``packaging/smoke-installer.ps1`` runs it in CI).
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.update import apply as ap  # noqa: E402

ISS = REPO_ROOT / "packaging" / "kroma-player.iss"


def _script() -> str:
    return ISS.read_text(encoding="utf-8")


def _define(name: str) -> str:
    """The value of `#define <name> "..."` in the installer script."""
    match = re.search(rf'#define\s+{name}\s+"([^"]*)"', _script())
    assert match, f"packaging/kroma-player.iss does not #define {name}"
    return match.group(1)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


@pytest.fixture
def popen_calls(monkeypatch):
    """Capture what would have been launched, and launch nothing."""
    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        return object()

    monkeypatch.setattr(ap.subprocess, "Popen", fake_popen)
    return calls


def test_the_installer_is_invoked_with_exactly_the_expected_switches(popen_calls, tmp_path):
    installer = tmp_path / "KromaPlayerSetup-1.2.0-x86_64.exe"
    installer.write_bytes(b"MZ")

    assert ap.run_installer(installer) is True

    argv = popen_calls[0]["argv"]
    assert argv == [
        str(installer),
        "/SILENT",              # a progress window, not a wizard and not nothing
        "/NOCANCEL",            # [InstallDelete] cannot be rolled back
        "/CLOSEAPPLICATIONS",   # the Restart Manager closes whatever holds a file
        "/FORCECLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
        "/NORESTART",           # never reboot the user's machine
        "/RELAUNCH",            # ours; WantsRelaunch() in the .iss checks it
    ]


def test_no_switch_hides_the_installers_own_messages():
    """A message box in a silent install is Inno reporting a problem. Seeing it
    beats an installer that quietly takes the default answer."""
    assert "/SUPPRESSMSGBOXES" not in ap.INSTALLER_ARGS
    assert "/VERYSILENT" not in ap.INSTALLER_ARGS


@pytest.mark.parametrize("scope,switch", [("user", "/CURRENTUSER"), ("machine", "/ALLUSERS")])
def test_the_scope_pins_the_update_to_the_copy_being_updated(tmp_path, scope, switch):
    installer = tmp_path / "setup.exe"
    log = tmp_path / "kroma-update" / "install.log"

    argv = ap.installer_argv(installer, scope=scope, log=log)

    assert argv == [str(installer), *ap.INSTALLER_ARGS, switch, f"/LOG={log}"]


def test_no_scope_and_no_log_add_nothing(tmp_path):
    installer = tmp_path / "setup.exe"
    assert ap.installer_argv(installer) == [str(installer), *ap.INSTALLER_ARGS]
    assert ap.installer_argv(installer, scope="nonsense") == [str(installer), *ap.INSTALLER_ARGS]


def test_run_installer_passes_scope_and_log_through(popen_calls, tmp_path):
    installer = tmp_path / "setup.exe"
    log = tmp_path / "install.log"

    ap.run_installer(installer, scope="user", log=log)

    assert popen_calls[0]["argv"] == ap.installer_argv(installer, scope="user", log=log)


# ---------------------------------------------------------------------------
# The environment the next build inherits
# ---------------------------------------------------------------------------


def test_the_handoff_drops_what_belongs_to_this_build():
    """`_PYI_*` would tell the relaunched copy it is a child of this one, and
    QML_DISK_CACHE_PATH would hand it this build's compiled QML."""
    base = {
        "PATH": r"C:\Windows",
        "_PYI_APPLICATION_HOME_DIR": r"C:\Kroma\_internal",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_pyi_lowercase_too": "x",
        "QML_DISK_CACHE_PATH": r"C:\vault\.data\gui\.cache\abc",
        "KROMA_GUI_DIR": "kept",
    }

    environment = ap.handoff_environment(base)

    assert environment["PATH"] == r"C:\Windows"
    assert environment["KROMA_GUI_DIR"] == "kept"
    assert not any(key.upper().startswith("_PYI_") for key in environment)
    assert "QML_DISK_CACHE_PATH" not in environment
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "_PYI_PARENT_PROCESS_LEVEL" in base, "the caller's mapping is left alone"


def test_the_handoff_defaults_to_this_processs_environment(monkeypatch):
    monkeypatch.setenv("_PYI_SPLASH_IPC", "7")
    monkeypatch.setenv("KROMA_HANDOFF_PROBE", "here")

    environment = ap.handoff_environment()

    assert environment["KROMA_HANDOFF_PROBE"] == "here"
    assert "_PYI_SPLASH_IPC" not in environment


def test_the_installer_is_given_the_handoff_environment(popen_calls, monkeypatch, tmp_path):
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "kroma-player.exe")
    monkeypatch.setenv("QML_DISK_CACHE_PATH", str(tmp_path))

    ap.run_installer(tmp_path / "setup.exe")

    env = popen_calls[0]["env"]
    assert "_PYI_ARCHIVE_FILE" not in env
    assert "QML_DISK_CACHE_PATH" not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_the_installer_is_started_outside_the_folder_it_is_about_to_replace(
    popen_calls, tmp_path
):
    """A process's working directory holds an open handle on it, which is a poor
    thing to be pointing at a folder an installer is replacing."""
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")
    ap.run_installer(installer)

    cwd = Path(popen_calls[0]["cwd"])
    assert cwd != tmp_path
    assert cwd.is_dir()
    assert popen_calls[0]["close_fds"] is True


def test_a_refused_installer_reports_rather_than_raising(monkeypatch, tmp_path):
    """The caller falls back to telling the user where the file is. There is
    nothing else to do with this failure, and it must not take the app down."""
    def refuse(*_args, **_kwargs):
        raise OSError("Access is denied")

    monkeypatch.setattr(ap.subprocess, "Popen", refuse)
    assert ap.run_installer(tmp_path / "setup.exe") is False


def test_the_switches_the_installer_script_understands_are_the_ones_sent():
    """/RELAUNCH is invented by this application, so it exists in exactly two
    places and they have to agree."""
    script = _script()
    assert "/RELAUNCH" in script
    assert "WantsRelaunch" in script
    assert "/RELAUNCH" in ap.INSTALLER_ARGS


# ---------------------------------------------------------------------------
# What the installer script has to say for an update to work
# ---------------------------------------------------------------------------


def test_an_update_replaces_the_runtime_rather_than_layering_over_it():
    """Without [InstallDelete], a module the new build no longer ships stays in
    _internal\\ forever. Only _internal\\: a user's ffmpeg.exe beside the exe
    must survive."""
    script = _script()
    section = script[script.index("[InstallDelete]"):]
    section = section[: section.index("\n[", 1)]
    entries = [line for line in section.splitlines() if line.startswith("Type:")]
    assert entries == ['Type: filesandordirs; Name: "{app}\\_internal"']


def test_a_silent_update_waits_for_the_app_that_started_it():
    """Inno runs InitializeSetup before its AppMutex check. Without the wait, a
    silent update reports the app as running to the user who just asked for it
    to close."""
    script = _script()
    code = script[script.index("[Code]"):]
    assert "function InitializeSetup(): Boolean;" in code
    assert "CheckForMutexes('{#AppMutexName}')" in code
    assert "HasSwitch('/RELAUNCH')" in code
    assert "exited after" in code, "packaging/smoke-installer.ps1 looks for this line"
    assert "AppMutex={#AppMutexName}" in script, "the interactive check stays"


def test_the_relaunched_app_does_not_inherit_an_elevated_installer():
    script = _script()
    relaunch = next(line for line in script.splitlines() if "Check: WantsRelaunch" in line)
    assert "runasoriginaluser" in relaunch


def test_the_mutex_the_installer_watches_is_the_one_the_app_holds():
    from app.main import SINGLE_INSTANCE_MUTEX

    assert _define("AppMutexName") == SINGLE_INSTANCE_MUTEX


def test_the_installers_default_repository_is_the_updaters():
    """CI overrides AppRepo with $GITHUB_REPOSITORY, and the guard holds REPO to
    the same, so the default only matters for a hand-built installer — which
    should still point where the app looks."""
    from app.update.release import REPO

    assert _define("AppRepo") == REPO


def test_the_mutex_wait_outlasts_the_apps_exit_watchdog():
    """The app ends itself 60 s after handing off. The installer must wait at
    least that long, or a slow shutdown becomes a "still running" prompt."""
    from app.viewmodels.updates_view_model import EXIT_WATCHDOG_SECONDS

    match = re.search(r"#define\s+MutexWaitMs\s+(\d+)", _script())
    assert match
    assert int(match.group(1)) > EXIT_WATCHDOG_SECONDS * 1000


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


@pytest.fixture
def no_exec(monkeypatch):
    """Stub execve. The alternative is replacing the test runner with a text file."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        ap.os, "execve", lambda path, argv, env: calls.append((path, argv, env))
    )
    return calls


def test_the_downloaded_image_replaces_the_running_one(monkeypatch, tmp_path, no_exec):
    running = tmp_path / "kroma-player.AppImage"
    running.write_bytes(b"old")
    downloaded = tmp_path / "kroma-player-1.2.1-x86_64.AppImage"
    downloaded.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(running))

    ap.replace_appimage(downloaded)

    assert running.read_bytes() == b"new"
    assert not downloaded.exists(), "os.replace is a rename, not a copy"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit")
def test_the_replacement_is_executable(monkeypatch, tmp_path, no_exec):
    """A build that lands without its executable bit is an application the user
    can no longer start, and nothing is left running to fix it."""
    running = tmp_path / "kroma-player.AppImage"
    running.write_bytes(b"old")
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")
    os.chmod(downloaded, 0o600)
    monkeypatch.setenv("APPIMAGE", str(running))

    ap.replace_appimage(downloaded)

    assert stat.S_IMODE(running.stat().st_mode) == 0o755


def test_it_re_execs_the_file_it_just_wrote(monkeypatch, tmp_path, no_exec):
    """execv replaces the process image in place, so there is no window with no
    application and no second process racing the first."""
    running = tmp_path / "kroma-player.AppImage"
    running.write_bytes(b"old")
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(running))

    ap.replace_appimage(downloaded)

    assert [(path, argv) for path, argv, _env in no_exec] == [(str(running), [str(running)])]


def test_the_re_exec_gets_the_handoff_environment(monkeypatch, tmp_path, no_exec):
    """An AppImage is a PyInstaller tree inside: the new image's bootloader must
    not believe it is a child of the old one."""
    running = tmp_path / "kroma-player.AppImage"
    running.write_bytes(b"old")
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(running))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/tmp/.mount_kroma/usr/bin/_internal")

    ap.replace_appimage(downloaded)

    env = no_exec[0][2]
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_an_explicit_target_wins_over_the_environment(monkeypatch, tmp_path, no_exec):
    """So the view model can pass what it resolved rather than re-reading a
    variable that may have been inherited from somewhere else."""
    target = tmp_path / "explicit.AppImage"
    target.write_bytes(b"old")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "something-else.AppImage"))
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")

    ap.replace_appimage(downloaded, target)

    assert target.read_bytes() == b"new"


def test_a_read_only_location_raises_for_the_caller_to_catch(monkeypatch, tmp_path, no_exec):
    """A system-wide /opt drop the user cannot write to. The view model catches
    this and switches to the manual path — a verified update the user can
    install by hand beats a silent failure."""
    running = tmp_path / "kroma-player.AppImage"
    running.write_bytes(b"old")
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(running))

    def refuse(*_args, **_kwargs):
        raise PermissionError("Read-only file system")

    monkeypatch.setattr(ap.os, "replace", refuse)

    with pytest.raises(PermissionError):
        ap.replace_appimage(downloaded)

    assert no_exec == [], "nothing was restarted"


def test_no_appimage_at_all_raises(monkeypatch, tmp_path, no_exec):
    monkeypatch.delenv("APPIMAGE", raising=False)
    downloaded = tmp_path / "new.AppImage"
    downloaded.write_bytes(b"new")

    with pytest.raises(OSError, match="APPIMAGE"):
        ap.replace_appimage(downloaded)
