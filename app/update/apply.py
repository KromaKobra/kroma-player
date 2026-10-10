"""Installing what was downloaded. Two paths, one per install kind.

Both are short, and neither needs a helper process — which is the whole reason
the release ships an installer and an AppImage rather than a zip. "A running
program cannot replace itself" is a real constraint, and each platform already
solves it:

**Windows.** The Inno Setup installer asks the Restart Manager to close whatever
is holding the files it needs to replace, replaces them, and starts the app
again. The application's part is to launch it detached and quit tidily.

**Linux.** ``os.replace()`` over a running executable is safe: the kernel keeps
the old inode alive for the running process — and for the FUSE mount backing an
AppImage — until it exits. Then ``os.execve`` replaces the process image in
place, so there is no moment with no application and no second process racing
the first.

**Either way, the new build starts with a clean environment.** It inherits this
process's, and some of what is in there describes *this* build:
PyInstaller's ``_PYI_*`` variables tell a bootloader it is a child of an
already-unpacked application, and ``QML_DISK_CACHE_PATH`` points at the
compiled QML of the build being replaced. :func:`handoff_environment` is what
the installer — and through it the relaunched app — is given instead.

Every failure here falls back to *manual*: the view model opens the download
folder and names the file. Fifteen lines, and they are what stands between a
refused ``os.replace`` and a user with a verified update they cannot install.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# The switches handed to the Inno Setup installer.
#
# /SILENT rather than /VERYSILENT: it shows a progress window and no wizard,
# which is the right amount of feedback for replacing 215 MB of files. Under
# /VERYSILENT the user clicks "Update and restart", the window vanishes, and
# nothing appears to happen for half a minute.
#
# /NOCANCEL because the update cannot be rolled back halfway. [InstallDelete]
# removes the old _internal\ before the new files go in, and Inno has no undo
# for a deletion; a Cancel pressed after that leaves a folder with no runtime.
#
# /CLOSEAPPLICATIONS and /RESTARTAPPLICATIONS put the Restart Manager in play
# for anything still holding a file — the app itself has already quit by then,
# but a file dialog or an antivirus scanner may not have.
# /FORCECLOSEAPPLICATIONS makes that close stick when the holder ignores the
# polite request, which in a silent run nobody is there to answer.
#
# /NORESTART is a safety catch: on the rare occasion the Restart Manager decides
# a reboot is needed, an app the user asked to update must not reboot their
# machine.
#
# /RELAUNCH is ours, checked by WantsRelaunch() in packaging/kroma-player.iss.
# Inno passes unrecognised switches through untouched. It exists because the
# normal post-install launch is flagged `skipifsilent` — correct for a checkbox
# nobody is there to tick, wrong for a user who explicitly asked for a restart.
# It also tells InitializeSetup() to wait for this process to exit rather than
# reporting it as still running.
#
# Deliberately *not* /SUPPRESSMSGBOXES. A message box in a silent install is
# Inno saying something has gone wrong, and seeing it beats an installer that
# silently takes the default answer — usually "abort".
INSTALLER_ARGS = (
    "/SILENT",
    "/NOCANCEL",
    "/CLOSEAPPLICATIONS",
    "/FORCECLOSEAPPLICATIONS",
    "/RESTARTAPPLICATIONS",
    "/NORESTART",
    "/RELAUNCH",
)

# What a scope from install_kind.installer_scope() becomes on the command line.
# Without it, an installer started by a user who *could* elevate may ask which
# kind of install they want, and answering wrong installs a second copy.
SCOPE_SWITCHES = {"user": "/CURRENTUSER", "machine": "/ALLUSERS"}

# Variables that describe the running build and must not reach the next one.
# PyInstaller's bootloader exports `_PYI_*` to mark its children as children —
# a relaunched copy that inherits them believes it is running inside this
# process's unpacked bundle. QML_DISK_CACHE_PATH is set by app.main to a cache
# keyed by *this* build's id, and an existing value is deliberately honoured, so
# a new build that inherited it would reuse bytecode compiled by the old one.
HANDOFF_DROPPED_PREFIXES = ("_PYI_",)
HANDOFF_DROPPED = ("QML_DISK_CACHE_PATH",)

# PyInstaller's own "start over" switch, read by recent bootloaders: one that
# sees it treats itself as a fresh top-level process whatever else it
# inherited. Belt and braces with the removals above, which also cover older
# bootloaders that do not know it.
HANDOFF_RESET = ("PYINSTALLER_RESET_ENVIRONMENT", "1")


def handoff_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment to hand the installer, or the replacement AppImage.

    A copy of *base* (``os.environ`` by default) without the variables that
    belong to this build, and with PyInstaller told to reset. See the constants
    above for each one.
    """
    source = dict(os.environ if base is None else base)
    environment = {
        key: value
        for key, value in source.items()
        if not key.upper().startswith(HANDOFF_DROPPED_PREFIXES)
        and key.upper() not in HANDOFF_DROPPED
    }
    key, value = HANDOFF_RESET
    environment[key] = value
    return environment


def installer_argv(path: Path, *, scope: str = "", log: Path | None = None) -> list[str]:
    """The exact command line the installer is started with.

    Pure, so the tests can pin it down: a switch dropped or misspelled here
    produces an installer that puts up a wizard nobody is there to click, or
    one that finishes and never restarts the app, and neither shows up anywhere
    but on a real Windows machine at the end of an update.

    *scope* pins the install to where this copy already is; *log* is where Inno
    writes what it did, which is what the user is pointed at if the update did
    not take.
    """
    argv = [str(path), *INSTALLER_ARGS]
    switch = SCOPE_SWITCHES.get(scope)
    if switch:
        argv.append(switch)
    if log is not None:
        argv.append(f"/LOG={log}")
    return argv


def run_installer(path: Path, *, scope: str = "", log: Path | None = None) -> bool:
    """Start the Windows installer detached, and return whether it started.

    Detached, and with a working directory outside the install folder — both
    matter. A child process would die with the parent, and the parent is about
    to quit; and a process's working directory holds an open handle on it, which
    is a poor thing to be pointing at a folder an installer is replacing. The
    environment is :func:`handoff_environment`'s, because the installer passes
    its own on to the copy it relaunches.

    Returns ``False`` rather than raising when the installer will not start, so
    the caller can fall back to telling the user where the file is. There is
    nothing else to do with that failure, and it should not take the app down.
    """
    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS gives it no console; CREATE_NEW_PROCESS_GROUP stops a
        # Ctrl-C in the parent's group reaching it. Both are Windows-only
        # constants, hence the guard — this module is imported everywhere.
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )

    try:
        subprocess.Popen(  # noqa: S603
            installer_argv(path, scope=scope, log=log),
            creationflags=creationflags,
            close_fds=True,
            cwd=tempfile.gettempdir(),
            env=handoff_environment(),
        )
    except OSError as exc:
        logger.error("Could not start the installer at %s: %s", path, exc)
        return False

    logger.info("Handed off to the installer at %s", path)
    return True


def replace_appimage(path: Path, target: Path | None = None) -> None:
    """Put the downloaded AppImage in place of the running one and re-exec.

    *target* defaults to ``$APPIMAGE``. Does not return: ``os.execve`` replaces
    this process, with :func:`handoff_environment` as the new one's environment.

    **Call this only after the application's state has been persisted.**
    ``execve`` does not unwind anything — no ``atexit``, no Qt shutdown, no
    database close — so anything not already on disk is gone. The view model
    defers it to after ``persist_app_state()`` for exactly that reason.

    Raises ``OSError`` (``PermissionError`` for a system-wide drop the user
    cannot write to) so the caller can fall back to the manual path.
    """
    from app.update.install_kind import appimage_path

    destination = target or appimage_path()
    if destination is None:
        raise OSError("APPIMAGE is not set — this is not an AppImage install")

    # Before the rename, not after: a file that lands in place without its
    # executable bit is an application the user can no longer start, and there
    # would be nothing left running to fix it.
    os.chmod(path, 0o755)

    # Atomic. The running process keeps the old inode — and so does the FUSE
    # mount serving its own contents — until it exits, which is the property
    # that makes this whole approach possible.
    os.replace(path, destination)
    logger.info("Replaced %s; restarting", destination)

    os.execve(str(destination), [str(destination)], handoff_environment())
