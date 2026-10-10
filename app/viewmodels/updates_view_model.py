"""UpdatesViewModel — noticing a newer Kroma, and installing it.

A **job runner** in the ``docs/architecture/03-components/viewmodels.md``
taxonomy, built on :class:`~app.viewmodels.backup_view_model.BackupViewModel`'s
skeleton because it is the same shape: two background passes with the user's
decision in between, and a phase rather than a bare ``isRunning``.

    idle → checking → available → downloading → ready → applying
              ↘ up_to_date            ↘ (error / cancel) ↩ idle · available
                                      ↘ manual

The pause at ``available`` is not decoration. Nothing arrives on the user's disk
until they have read what it is and pressed a button — which is most of what
distinguishes this from the silent auto-updating ADR 0012 refused to do on
another program's behalf. That button is :meth:`UpdatesViewModel.updateNow`:
one click that downloads, verifies, installs and restarts, by carrying the
decision across the download (``_install_when_ready``) rather than stopping at
``ready`` to ask again. ``download()`` and ``installAndRestart()`` remain for a
GUI that wants the two steps separately.

``manual`` is the terminal state for a verified download that could not be
installed: an ``os.replace`` refused on a read-only ``/opt`` drop, an installer
Windows would not start. The file is on disk and it is known good, so the pane
says where it is rather than throwing it away.

Two things this deliberately does not do. It never offers a **downgrade** —
``is_newer()`` is strictly greater and there is no version picker — because a
vault migrated by a newer build is refused outright by ``app.vault.startup``
(its layout by ``check_vault()``, its database by ``check_database()``), so
that door does not open from this side. And it never **takes a backup** on the
user's behalf: every GUI is expected to advise one before updating and to offer
Library → Backup, whose two-pass user-reviewed flow exists because a vault names
its audio by track id alone and a misaimed sync overwrites silently. Inheriting
that risk to save a click would be indefensible. What *is* automatic is
narrower and safe: the first launch of the new build snapshots the library
database before migrating it (``app.vault.startup``), which ``willMigrate``
lets the GUI mention.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Property, QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from app.database import schema
from app.settings.updates import UpdateSettings
from app.update.apply import replace_appimage, run_installer
from app.update.download import download_dir, install_log_path, verify
from app.update.install_kind import (
    REASON_DISABLED,
    REASON_MANAGED,
    REASON_PORTABLE,
    REASON_SOURCE,
    InstallKind,
    install_kind,
    installer_scope,
    update_eligibility,
)
from app.update.release import RELEASES_PAGE, Asset, Release, is_newer
from app.update.workers import UpdateCheckWorker, UpdateDownloadWorker
from app.versions import APP_VERSION, VAULT_VERSION
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)

PHASE_IDLE = "idle"
PHASE_CHECKING = "checking"
PHASE_UP_TO_DATE = "up_to_date"
PHASE_AVAILABLE = "available"
PHASE_DOWNLOADING = "downloading"
PHASE_READY = "ready"
PHASE_APPLYING = "applying"
PHASE_MANUAL = "manual"

# Once the installer has been started, how long this process gets to finish
# quitting before it is ended outright. The installer waits up to 90 s for the
# app's mutex to be released (InitializeSetup in packaging/kroma-player.iss),
# so a shutdown that hangs — a thread that will not join, a child that will not
# die — must not be allowed to outlast that and turn an update into an error.
EXIT_WATCHDOG_SECONDS = 60

# Where a release body's summary ends: the first horizontal rule. The changelog
# puts its highlights above one and the itemised lists below.
_SUMMARY_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_HEADING_1 = re.compile(r"^\s*#[ \t]+.*$", re.MULTILINE)


def summarize_notes(notes: str) -> str:
    """The part of a release body worth showing in a dialog.

    Everything above the first horizontal rule, without HTML — GitHub renders
    the changelog's centring ``<div>``, Qt's Markdown would print it — and
    without the top-level heading, which only repeats the version the dialog
    already names.
    """
    kept: list[str] = []
    for line in (notes or "").replace("\r\n", "\n").split("\n"):
        if _SUMMARY_RULE.match(line):
            break
        kept.append(line)
    text = "\n".join(kept)
    text = _HTML_COMMENT.sub("", text)
    text = _HTML_TAG.sub("", text)
    text = _HEADING_1.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _force_exit() -> None:
    """End a process that has handed off to the installer and will not quit."""
    logger.error(
        "Still running %s s after handing off to the installer — exiting", EXIT_WATCHDOG_SECONDS
    )
    os._exit(0)

# Prose for the reason keys `update_eligibility()` returns. It lives here rather
# than beside them because a sentence a user reads is a presentation decision,
# and `app.update.install_kind` has no business holding four of them.
INELIGIBLE_PROSE = {
    REASON_SOURCE: (
        "Kroma Player is running from a source checkout, so there is nothing "
        "here to replace. Pull the latest changes with git instead."
    ),
    REASON_MANAGED: (
        "This copy of Kroma Player was installed by your package manager, which "
        "owns keeping it up to date. Update it the way you update everything "
        "else on this machine."
    ),
    REASON_PORTABLE: (
        "This is a portable copy of Kroma Player, so it cannot replace itself "
        "safely. Download the latest release and unpack it over this folder."
    ),
    REASON_DISABLED: (
        "In-app updates were switched off for this build with KROMA_NO_UPDATE."
    ),
}


class UpdatesViewModel(QObject):
    """Check for a newer release, download it, and install it."""

    phase_changed = Signal()
    release_changed = Signal()
    progress_changed = Signal()
    settings_changed = Signal()
    error_occurred = Signal(str)
    update_available = Signal(str)          # the version, for a toast

    def __init__(
        self,
        settings: UpdateSettings | None = None,
        parent: QObject | None = None,
        *,
        before_apply: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or UpdateSettings(None)
        # What has to stop before an update touches the installation: the
        # plugin processes, which run out of the same folder — the Windows
        # installer would otherwise find `kroma-plugin-host.exe` in use.
        self._before_apply = before_apply

        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

        self._phase = PHASE_IDLE
        self._stage = ""
        self._received = 0
        self._expected = 0

        self._release: Optional[Release] = None
        self._asset: Optional[Asset] = None
        self._downloaded: Optional[Path] = None

        # An AppImage swap that has been decided on but must not happen yet.
        # ``execv`` unwinds nothing, so it waits for ``shutdown()`` — by which
        # point the queue, the settings and the databases are on disk.
        self._pending_replacement: Optional[Path] = None

        # Set by a worker's result handler, consumed by ``_on_stopped``. See the
        # comment above those handlers for why the phase cannot move sooner —
        # it is the same crash `app.viewmodels.worker_threads` describes.
        self._pending_phase: Optional[str] = None
        self._pending_error: str = ""
        self._pending_announcement: str = ""

        # The user pressed "Update and restart" before the download existed.
        # Carried across it and acted on in `_on_stopped` once the phase reaches
        # `ready`; dropped if it does not get there.
        self._install_when_ready = False

        # Whether the check in flight was the launch check. A skipped version
        # is silenced only for that one — see `_on_checked`.
        self._checking_automatic = False

        # Set once the installer has been started and this process is on its
        # way out — see `_arm_exit_watchdog`.
        self._watchdog: Optional[threading.Timer] = None

        # An update this process's predecessor handed off to and that did not
        # take: the installer was started for a newer version, and this is
        # still the older one. Read once, here, at launch.
        self._failed_version = ""
        pending = self._settings.pending_version
        if pending:
            if is_newer(pending, APP_VERSION):
                self._failed_version = pending
                logger.warning(
                    "The update to %s did not complete; still running %s",
                    pending, APP_VERSION,
                )
            else:
                self._settings.pending_version = ""

    # ==================================================================
    # Eligibility
    # ==================================================================

    @Property(bool, notify=settings_changed)
    def eligible(self) -> bool:
        """Whether this copy can install an update at all.

        False for a source checkout, a package-managed copy, a portable folder,
        and a build with ``KROMA_NO_UPDATE`` set. The pane shows the reason and
        **no buttons** in that case: a clear sentence beats a button that cannot
        work.
        """
        return update_eligibility() == ""

    @Property(str, notify=settings_changed)
    def ineligibleReason(self) -> str:  # noqa: N802
        """Why updating is unavailable, or ``""`` when it is available."""
        return INELIGIBLE_PROSE.get(update_eligibility(), "")

    @Property(str, notify=settings_changed)
    def installKind(self) -> str:  # noqa: N802
        """How this copy was installed, for the diagnostics line."""
        return install_kind().value

    # ==================================================================
    # What is out there
    # ==================================================================

    @Property(str, notify=settings_changed)
    def currentVersion(self) -> str:  # noqa: N802
        return APP_VERSION

    @Property(str, notify=release_changed)
    def latestVersion(self) -> str:  # noqa: N802
        return self._release.version if self._release else ""

    @Property(str, notify=release_changed)
    def releaseNotes(self) -> str:  # noqa: N802
        """The release body, as GitHub returned it.

        Markdown, rendered as plain text with a link to the full notes. Adding a
        Markdown renderer so a settings pane can draw headings would be a large
        dependency for a paragraph nobody reads twice.
        """
        return self._release.notes if self._release else ""

    @Property(str, notify=release_changed)
    def releaseSummary(self) -> str:  # noqa: N802
        """The release's highlights, as Markdown — see :func:`summarize_notes`.

        For a dialog with room for a paragraph, not the whole changelog; the
        full notes are a link away.
        """
        return summarize_notes(self._release.notes) if self._release else ""

    @Property(str, notify=release_changed)
    def releaseUrl(self) -> str:  # noqa: N802
        return self._release.url if self._release else RELEASES_PAGE

    @Property(str, notify=release_changed)
    def publishedAt(self) -> str:  # noqa: N802
        return self._release.published_at if self._release else ""

    @Property(float, notify=release_changed)
    def downloadMb(self) -> float:  # noqa: N802
        """How large the download is, so the user can decide on a phone tether."""
        return (self._asset.size / (1024 * 1024)) if self._asset else 0.0

    @Property(bool, notify=release_changed)
    def updateAvailable(self) -> bool:  # noqa: N802
        return self._release is not None and self._asset is not None

    @Property(bool, notify=release_changed)
    def willMigrateVault(self) -> bool:  # noqa: N802
        """Whether installing this release would upgrade the vault's layout.

        A one-way door: once migrated, ``check_vault()`` refuses the vault to
        every older build with a message telling the user to update. That is
        exactly the kind of thing somebody should find out *before* they press
        the button, and it is the reason ``release.json`` exists rather than
        just ``SHA256SUMS`` — the GitHub API has nowhere to put this.
        """
        return self._release is not None and self._release.vault_version > VAULT_VERSION

    @Property(int, notify=release_changed)
    def vaultVersionAfter(self) -> int:  # noqa: N802
        """The layout the update would move the vault to, for the warning."""
        return self._release.vault_version if self._release else VAULT_VERSION

    @Property(bool, notify=release_changed)
    def willMigrate(self) -> bool:  # noqa: N802
        """Whether installing this release would migrate the library at all.

        The vault's layout **or** its database. Both are one-way: after either,
        this build refuses the vault (``app.vault.startup``). The database is
        the one that actually moves — ``SCHEMA_VERSION`` rose six times between
        1.1.0 and 1.2.0 while the layout stayed put — and is why this exists
        beside ``willMigrateVault``, which watched only the layout. Either way
        the new build snapshots the database before touching it, which the
        warning can say.
        """
        if self._release is None:
            return False
        return (
            self._release.vault_version > VAULT_VERSION
            or self._release.schema_version > schema.SCHEMA_VERSION
        )

    # ==================================================================
    # Phase and progress
    # ==================================================================

    @Property(str, notify=phase_changed)
    def phase(self) -> str:
        return self._phase

    @Property(bool, notify=phase_changed)
    def isBusy(self) -> bool:  # noqa: N802
        return self._phase in (PHASE_CHECKING, PHASE_DOWNLOADING, PHASE_APPLYING)

    @Property(bool, notify=phase_changed)
    def installWhenReady(self) -> bool:  # noqa: N802
        """Whether the download in flight will install and restart by itself.

        True after :meth:`updateNow` until it either does so or the download
        stops short, so a GUI can say "Kroma Player will restart when the
        download finishes" rather than leave the user waiting for a second
        button that is not coming.
        """
        return self._install_when_ready

    # ==================================================================
    # The last update
    # ==================================================================

    @Property(bool, notify=settings_changed)
    def lastUpdateFailed(self) -> bool:  # noqa: N802
        """Whether the previous launch handed off an update that did not take.

        The installer was started for :attr:`failedVersion` and this is still
        the older build. Nothing in the library was touched by that — the new
        build never ran — so the message is "it did not install", with the
        installer's log one click away.
        """
        return bool(self._failed_version)

    @Property(str, notify=settings_changed)
    def failedVersion(self) -> str:  # noqa: N802
        return self._failed_version

    @Property(str, notify=progress_changed)
    def stage(self) -> str:
        return self._stage

    @Property(int, notify=progress_changed)
    def progress(self) -> int:
        """Completion as a percentage, for the progress strip."""
        if self._expected <= 0:
            return 0
        return min(100, int(self._received * 100 / self._expected))

    # ==================================================================
    # Preferences
    # ==================================================================

    @Property(bool, notify=settings_changed)
    def checkOnLaunch(self) -> bool:  # noqa: N802
        return self._settings.check_on_launch

    @checkOnLaunch.setter
    def checkOnLaunch(self, value: bool) -> None:  # noqa: N802
        value = bool(value)
        if value == self._settings.check_on_launch:
            return
        self._settings.check_on_launch = value
        self.settings_changed.emit()

    @Property(str, notify=settings_changed)
    def lastChecked(self) -> str:  # noqa: N802
        """When the last check ran, as a local date and time, or ``""``.

        Formatted here rather than stored that way: the stamp is UTC so the gate
        behaves the same either side of a timezone change, and a user reading
        the pane wants their own clock.
        """
        stamp = self._settings.last_checked
        if not stamp:
            return ""
        try:
            return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ""

    # ==================================================================
    # Slots
    # ==================================================================

    @Slot()
    def check(self) -> None:
        """Ask GitHub what the newest stable release is.

        Bypasses the once-a-day gate: somebody who pressed the button is owed an
        answer, and one request is not what trips a rate limit.
        """
        self._check(automatic=False)

    @Slot()
    def updateNow(self) -> None:  # noqa: N802
        """Download, verify, install and restart — the one-click path.

        From ``available`` it starts the download and remembers that the user
        has already decided; the install follows by itself once the download
        has been verified. From ``ready`` it installs at once. During a download
        it attaches the same decision to the one in flight. Anywhere else there
        is nothing it can do.
        """
        if self._phase == PHASE_READY:
            self.installAndRestart()
            return
        if self._phase == PHASE_DOWNLOADING:
            self._install_when_ready = True
            self.phase_changed.emit()
            return
        if self._phase != PHASE_AVAILABLE:
            return

        self._install_when_ready = True
        self.download()
        if self._phase != PHASE_DOWNLOADING:
            # Refused before it began — nothing to download, not eligible, or a
            # worker already running. The decision does not outlive the attempt.
            self._install_when_ready = False
        self.phase_changed.emit()

    @Slot()
    def checkOnStartup(self) -> None:  # noqa: N802
        """The launch check, subject to the preference and the 24 h gate.

        Called from the GUI shortly after the window appears rather than from
        ``bootstrap``, so a check never sits between the user and their library.
        """
        if not self._settings.due_for_check():
            return
        self._check(automatic=True)

    @Slot()
    def refresh(self) -> None:
        """Re-read the local answers. **Never touches the network.**

        What the pane hangs off ``onActiveChanged``, mirroring
        ``dependenciesVM.refresh()`` beside it. Only eligibility and the
        last-checked stamp can have moved, and both are read from this machine.
        Firing a check here instead would put a network request behind every
        visit to a settings tab, which is the objection ADR 0012 raises and the
        24 h gate answers.
        """
        self.settings_changed.emit()

    @Slot()
    def download(self) -> None:
        """Fetch the artifact matching how this copy was installed."""
        if self._asset is None:
            self.error_occurred.emit("There is nothing to download")
            return
        if not self.eligible:
            self.error_occurred.emit(self.ineligibleReason)
            return

        worker = UpdateDownloadWorker(self._asset)
        worker.finished.connect(self._on_downloaded)

        self._received = 0
        self._expected = self._asset.size
        self.progress_changed.emit()
        if not self._start(worker):
            self.error_occurred.emit("An update is already in progress")
            return
        self._set_phase(PHASE_DOWNLOADING)

    @Slot()
    def installAndRestart(self) -> None:  # noqa: N802
        """Install the downloaded update and restart. The irreversible one.

        Re-checks eligibility and **re-verifies the file's hash** before doing
        anything: it has been sitting on disk since the download, possibly in a
        world-writable temp directory, possibly for as long as the user left the
        pane open. Re-reading a couple of hundred megabytes costs a second
        against an action that cannot be undone.
        """
        if self._phase != PHASE_READY or self._downloaded is None or self._asset is None:
            return
        if not self.eligible:
            self.error_occurred.emit(self.ineligibleReason)
            return

        if not verify(self._downloaded, self._asset.sha256):
            self._downloaded = None
            self._set_phase(PHASE_AVAILABLE)
            self.error_occurred.emit(
                "The downloaded update no longer matches its checksum. "
                "Download it again."
            )
            return

        self._set_phase(PHASE_APPLYING)
        self._install_when_ready = False
        if self._before_apply is not None:
            try:
                self._before_apply()
            except Exception:  # noqa: BLE001 — an update is not refused over it
                logger.exception("Stopping work before the update failed")
        kind = install_kind()

        if kind is InstallKind.WINDOWS_INSTALLER:
            # Recorded first, and written through: the next launch compares it
            # with its own version to tell whether the update took.
            self._settings.pending_version = self._release.version if self._release else ""

            # The installer waits for this process's mutex to go, so quitting
            # through Qt — which lets persist_app_state() run, closing the
            # databases and writing settings.json whole — costs it nothing.
            # os._exit() here would save 200 ms and lose the session. The scope
            # keeps the update on the copy being updated; the log is what the
            # user is shown if it does not take.
            started = run_installer(
                self._downloaded, scope=installer_scope(), log=install_log_path()
            )
            if not started:
                self._settings.pending_version = ""
                self._fall_back_to_manual("Kroma Player could not start the installer.")
                return
            self._arm_exit_watchdog()
            self._quit()
            return

        if kind is InstallKind.APPIMAGE:
            # Deferred, and this is the whole reason `_pending_replacement`
            # exists: execv unwinds nothing — no atexit, no Qt shutdown, no
            # database close — so it must not run from a slot that returns into
            # a live event loop with the session still in memory. shutdown(),
            # called from persist_app_state(), performs it once everything the
            # backend owns is on disk.
            self._pending_replacement = self._downloaded
            self._quit()
            return

        self._fall_back_to_manual(
            "Kroma Player does not know how to install an update for this kind "
            "of installation."
        )

    @Slot()
    def cancel(self) -> None:
        """Stop a download at the next chunk boundary."""
        if self._worker is not None and hasattr(self._worker, "cancel"):
            self._worker.cancel()

    @Slot()
    def skipThisVersion(self) -> None:  # noqa: N802
        """Stop announcing this release. A later one is still announced.

        Silences the launch check only: "Check for updates" still offers it, for
        somebody who skipped it and has since changed their mind. One version,
        not a set: skipping 1.2.0 and later being offered 1.3.0 is the behaviour
        somebody skipping a release actually wants.
        """
        if self._release is None:
            return
        self._settings.skipped_version = self._release.version
        self._release = None
        self._asset = None
        self._set_phase(PHASE_UP_TO_DATE)
        self.release_changed.emit()
        self.settings_changed.emit()

    @Slot()
    def openReleasePage(self) -> None:  # noqa: N802
        """Open the release on GitHub — the full notes, and every past build."""
        QDesktopServices.openUrl(QUrl(self.releaseUrl))

    @Slot()
    def revealDownload(self) -> None:  # noqa: N802
        """Open the folder holding the downloaded update, for the manual path."""
        folder = self._downloaded.parent if self._downloaded else download_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot()
    def revealInstallLog(self) -> None:  # noqa: N802
        """Open the installer's log from the update that did not take.

        The file if it is there, else the folder it would be in — an installer
        that never started wrote nothing, and the folder still holds the
        download.
        """
        log = install_log_path()
        target = log if log.is_file() else log.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def dismissUpdateFailure(self) -> None:  # noqa: N802
        """Stop reporting the update that did not take."""
        if not self._failed_version:
            return
        self._failed_version = ""
        self._settings.pending_version = ""
        self.settings_changed.emit()

    def shutdown(self) -> None:
        """Stop any work in flight, and perform a deferred AppImage swap.

        Called from ``bootstrap.persist_app_state`` — which is also why the
        AppImage replacement happens here and nowhere earlier. By this point the
        queue, the settings and the databases are on disk, so ``execve`` taking
        the process without unwinding anything costs nothing.

        After a Windows handoff the exit watchdog is already running — armed in
        :meth:`installAndRestart`, not here, because this is the *last* step of
        shutdown and a hang anywhere before it is just as fatal to the update.
        """
        self.cancel()
        if self._thread is not None:
            self._thread.quit()
            # Brief: a download stops at the next chunk, and a hang here would
            # keep the window on screen after the user closed it.
            if self._thread.wait(5000):
                # Only once it really stopped. Releasing a worker while its
                # thread runs is what worker_threads.py exists to prevent.
                self._thread = None
                self._worker = None

        pending = self._pending_replacement
        if pending is None:
            return
        self._pending_replacement = None
        try:
            replace_appimage(pending)   # does not return
        except OSError as exc:
            # Nothing is left to show a message with — the window is closing —
            # so this is the log's to carry. The file is verified and still on
            # disk, and the next launch's check will offer it again.
            logger.error("Could not install the update in place: %s", exc)

    # ==================================================================
    # Internals
    # ==================================================================

    def _arm_exit_watchdog(self) -> None:
        """End the process if it is still here ``EXIT_WATCHDOG_SECONDS`` from now.

        A daemon thread, so a shutdown that finishes normally takes it with it.
        """
        if self._watchdog is not None:
            return
        timer = threading.Timer(EXIT_WATCHDOG_SECONDS, _force_exit)
        timer.daemon = True
        timer.start()
        self._watchdog = timer

    def _check(self, automatic: bool) -> None:
        if not self.eligible:
            # Not an error, and not silent either: the pane already says why.
            self._set_phase(PHASE_IDLE)
            self.settings_changed.emit()
            return

        worker = UpdateCheckWorker()
        worker.finished.connect(self._on_checked)

        if not self._start(worker):
            if not automatic:
                self.error_occurred.emit("A check is already running")
            return
        self._checking_automatic = automatic
        self._settings.stamp_checked()
        self.settings_changed.emit()
        self._set_phase(PHASE_CHECKING)

    def _start(self, worker: QObject) -> bool:
        """Put *worker* on its own thread and run it. False if one is running.

        The guard is not belt and braces. Overwriting ``_thread``/``_worker``
        while the previous thread is still alive orphans the new worker the
        moment the old thread's ``finished`` reaches ``_on_stopped``, and a
        worker whose last Python reference goes while its thread runs takes the
        process down with it.
        """
        if self._thread is not None:
            return False

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)              # type: ignore[attr-defined]
        worker.failed.connect(self._on_failed)          # type: ignore[attr-defined]
        if hasattr(worker, "stage"):
            worker.stage.connect(self._on_stage)        # type: ignore[attr-defined]
        if hasattr(worker, "progress"):
            worker.progress.connect(self._on_progress)  # type: ignore[attr-defined]
        if hasattr(worker, "cancelled"):
            worker.cancelled.connect(self._on_cancelled)  # type: ignore[attr-defined]
        own_worker(thread, worker, self._on_stopped)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.phase_changed.emit()   # isBusy is true from here
        return True

    def _set_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self.phase_changed.emit()

    def _quit(self) -> None:
        """Close the application through Qt, so shutdown runs.

        Imported here rather than at module scope only to keep the view model
        importable without an application object, which is how the tests build
        it.
        """
        from PySide6.QtCore import QCoreApplication

        application = QCoreApplication.instance()
        if application is not None:
            application.quit()

    def _fall_back_to_manual(self, message: str) -> None:
        """Give up on installing automatically, and say where the file is.

        Fifteen lines, and they are what stands between a refused ``os.replace``
        and a user holding a verified update they cannot install.
        """
        self._set_phase(PHASE_MANUAL)
        self.error_occurred.emit(message)

    def _select_asset(self, release: Release) -> Asset | None:
        """The artifact for this install kind, if the release ships one."""
        return release.asset_for(install_kind())

    # ---- worker callbacks --------------------------------------------

    @Slot(str)
    def _on_stage(self, text: str) -> None:
        self._stage = text
        self.progress_changed.emit()

    @Slot(int, int)
    def _on_progress(self, received: int, expected: int) -> None:
        self._received = received
        self._expected = expected
        self.progress_changed.emit()

    # The result handlers below record the outcome but do **not** move the
    # phase. They run on the main thread, but they are invoked from the worker's
    # finished/failed/cancelled signals, which are emitted from inside ``run`` —
    # so the worker's thread is by definition still alive when they fire.
    #
    # The phase is what tells the pane it may start the next pass. Flipping it
    # here would let download() launch a worker while the check thread was still
    # shutting down, and that thread's `finished` would then clear the
    # references belonging to the *new* worker. The last Python reference to a
    # live worker goes away, and the process segfaults — exactly the failure
    # app.viewmodels.worker_threads exists to describe.
    #
    # Both read their payload off the worker rather than taking it as a signal
    # argument, per the note in app/update/workers.py.

    @Slot()
    def _on_checked(self) -> None:
        worker = self._worker
        release = getattr(worker, "release", None) if worker is not None else None

        if release is None or not is_newer(release.version, APP_VERSION):
            self._release = None
            self._asset = None
            self._pending_phase = PHASE_UP_TO_DATE
            return

        skipped = release.version == self._settings.skipped_version
        if skipped and self._checking_automatic:
            # Offered once, refused once — at launch. A later release is still
            # offered, because `skipped_version` holds one version rather than a
            # set; and a manual check still offers this one, below.
            self._release = None
            self._asset = None
            self._pending_phase = PHASE_UP_TO_DATE
            return

        asset = self._select_asset(release)
        if asset is None:
            # The release exists but ships nothing for this platform — an
            # architecture nobody has built for yet, most likely. Saying
            # "up to date" would be a lie, so it is reported as a failure the
            # user can act on by visiting the release page.
            self._release = release
            self._asset = None
            self._pending_phase = PHASE_AVAILABLE
            self._pending_error = (
                f"Kroma Player {release.version} is available, but this release "
                "does not include a build for this system."
            )
            return

        self._release = release
        self._asset = asset
        self._pending_phase = PHASE_AVAILABLE
        # Not announced if skipped: somebody who asked can see it in the pane,
        # and a dialog for a version they said no to would be nagging.
        self._pending_announcement = "" if skipped else release.version

    @Slot()
    def _on_downloaded(self) -> None:
        worker = self._worker
        self._downloaded = getattr(worker, "downloaded_path", None) if worker else None
        self._pending_phase = PHASE_READY if self._downloaded else PHASE_AVAILABLE

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._stage = ""
        # Back to whichever screen the user can act from: a failed check leaves
        # them where they were, a failed download leaves the release on screen
        # so they can read it and try again.
        self._pending_phase = PHASE_AVAILABLE if self._release is not None else PHASE_IDLE
        self._pending_error = message

    @Slot()
    def _on_cancelled(self) -> None:
        self._stage = ""
        self._pending_phase = PHASE_AVAILABLE if self._release is not None else PHASE_IDLE

    @Slot()
    def _on_stopped(self) -> None:
        """The thread has genuinely finished; release it and announce the result.

        Everything that lets the user start something else happens here and
        nowhere earlier, so there is never a moment where the pane offers a
        button whose handler would trample a running worker.
        """
        self._thread = None
        self._worker = None

        phase, self._pending_phase = self._pending_phase, None
        error, self._pending_error = self._pending_error, ""
        announcement, self._pending_announcement = self._pending_announcement, ""

        if phase is not None:
            self._set_phase(phase)

        # "Update and restart" was pressed before the download existed. Acted
        # on now that the thread is gone — never from the worker's own result
        # handler, for the reason above — and only once the file is verified.
        install_now = False
        if self._install_when_ready and self._phase != PHASE_DOWNLOADING:
            # Spent either way: it installs now, or the download fell short and
            # the next attempt is a new decision.
            self._install_when_ready = False
            install_now = self._phase == PHASE_READY and not error

        self.release_changed.emit()
        self.progress_changed.emit()
        self.phase_changed.emit()
        self.settings_changed.emit()

        if error:
            self.error_occurred.emit(error)
        elif announcement:
            self.update_available.emit(announcement)

        if install_now:
            self.installAndRestart()
