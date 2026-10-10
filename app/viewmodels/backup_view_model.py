"""Backing up the vault — the model behind the backup dialog.

Shaped like :class:`~app.viewmodels.export_view_model.ExportViewModel`, with one
structural difference: this runs in two passes with the user in between, so it
has a ``phase`` rather than a bare ``isRunning``.

    idle → scanning → review → applying → done
                 ↘ (error or cancel) ↩ idle

The pause at ``review`` is the safety property, not a nicety. A vault names its
audio by track id alone, so two unrelated libraries collide from top to bottom
and a sync pointed at the wrong folder would overwrite it silently. Nothing is
written until somebody has read what would be written.

One backup at a time, and no attempt to make that configurable. The destination
is almost always a single external drive, where two concurrent writers make both
slower and would race each other for the same manifest.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Property, QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from app.backup.locator import is_backup, suggest_destination
from app.backup.plan import BackupOptions, BackupPlan, BackupResult, Identity
from app.backup.scan import choose_backup_folder
from app.backup.workers import BackupApplyWorker, BackupScanWorker
from app.settings.backup import BackupSettings
from app.utils.library_paths import library_root
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)

PHASE_IDLE = "idle"
PHASE_SCANNING = "scanning"
PHASE_REVIEW = "review"
PHASE_APPLYING = "applying"
PHASE_DONE = "done"


class BackupViewModel(QObject):
    """Configure, preview and run a backup, and report on it while it runs."""

    settings_changed = Signal()
    phase_changed = Signal()
    progress_changed = Signal()
    plan_changed = Signal()
    error_occurred = Signal(str)
    backup_finished = Signal(int, int, str)   # written, deleted, destination

    def __init__(
        self,
        settings: BackupSettings | None = None,
        app_version: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or BackupSettings(None)
        self._app_version = app_version

        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

        self._phase = PHASE_IDLE
        self._stage = ""
        self._processed = 0
        self._total = 0

        self._plan: Optional[BackupPlan] = None
        self._delete_extras = False
        self._override_mismatch = False

        self._result: Optional[BackupResult] = None

        # Set by a worker's result handler, consumed by ``_on_stopped``. See the
        # comment above those handlers for why the phase cannot move any sooner.
        self._pending_phase: Optional[str] = None
        self._pending_error: str = ""

    # ==================================================================
    # Configuration
    # ==================================================================

    @Property(str, notify=settings_changed)
    def destination(self) -> str:
        return self._settings.directory

    @destination.setter
    def destination(self, value: str) -> None:
        value = self._to_local_path(value)
        if value == self._settings.directory:
            return
        self._settings.directory = value
        # A different folder means a different answer to every question the
        # review pane was showing. Dropping it is not tidiness — leaving a plan
        # for the old folder on screen next to the new folder's name is how
        # somebody confirms a sync they did not mean.
        self._discard_plan()
        self.settings_changed.emit()

    @Slot(QUrl, result=str)
    def chooseFolder(self, url: QUrl) -> str:
        """Take a folder from the picker and set the destination from it.

        Picking a folder that already holds this library's backup selects that
        backup, so choosing the drive root does the right thing rather than
        proposing a second one beside the first. Otherwise a new backup is
        proposed inside the chosen folder, named for today — that folder does
        not exist yet, and is created when the backup is confirmed.

        Returns the resolved path so the dialog can show it immediately.
        """
        picked = Path(self._to_local_path(url.toString() if isinstance(url, QUrl) else str(url)))
        if not picked.is_dir():
            self.error_occurred.emit(f"No such folder: {picked}")
            return self.destination

        existing = choose_backup_folder(picked, library_root())
        chosen = existing if existing is not None else suggest_destination(picked)
        self.destination = str(chosen)
        return str(chosen)

    @Property(bool, notify=settings_changed)
    def destinationValid(self) -> bool:
        """Whether the remembered folder is somewhere we could write.

        Checked rather than assumed: the last backup very likely went to a drive
        that is not plugged in right now.
        """
        directory = self._settings.directory
        if not directory:
            return False
        path = Path(directory)
        # A backup that does not exist yet is valid as long as its parent does —
        # that is the "create a new one" case.
        return path.is_dir() or path.parent.is_dir()

    @Property(bool, notify=settings_changed)
    def destinationIsBackup(self) -> bool:
        """Whether the chosen folder already holds a backup.

        Drives the action button's wording: syncing an existing backup and
        creating a new one are the same operation, but they are not the same
        promise, and the button should not claim otherwise.
        """
        directory = self._settings.directory
        return bool(directory) and is_backup(Path(directory))

    @Property(str, notify=settings_changed)
    def actionLabel(self) -> str:
        return "Sync backup" if self.destinationIsBackup else "Create backup"

    # -- the option toggles --------------------------------------------

    @Property(bool, notify=settings_changed)
    def includeTracks(self) -> bool:
        return self._settings.tracks

    @includeTracks.setter
    def includeTracks(self, value: bool) -> None:
        self._set_option("tracks", bool(value))

    @Property(bool, notify=settings_changed)
    def includeBacking(self) -> bool:
        return self._settings.backing

    @includeBacking.setter
    def includeBacking(self, value: bool) -> None:
        self._set_option("backing", bool(value))

    @Property(bool, notify=settings_changed)
    def includeGuis(self) -> bool:
        return self._settings.guis

    @includeGuis.setter
    def includeGuis(self, value: bool) -> None:
        self._set_option("guis", bool(value))

    @Property(bool, notify=settings_changed)
    def includeModels(self) -> bool:
        return self._settings.models

    @includeModels.setter
    def includeModels(self, value: bool) -> None:
        self._set_option("models", bool(value))

    @Property(bool, notify=plan_changed)
    def deleteExtras(self) -> bool:
        """Whether this run removes backup files the library no longer has.

        Never persisted, and reset every time a plan is discarded. Deleting is
        the one thing here that cannot be undone, so it is a decision made
        against a specific set of numbers rather than a preference carried in
        from some earlier session.
        """
        return self._delete_extras

    @deleteExtras.setter
    def deleteExtras(self, value: bool) -> None:
        value = bool(value)
        if value == self._delete_extras:
            return
        self._delete_extras = value
        self.plan_changed.emit()

    @Property(bool, notify=plan_changed)
    def overrideMismatch(self) -> bool:
        """Whether the user has explicitly accepted a vault-id mismatch."""
        return self._override_mismatch

    @overrideMismatch.setter
    def overrideMismatch(self, value: bool) -> None:
        value = bool(value)
        if value == self._override_mismatch:
            return
        self._override_mismatch = value
        self.plan_changed.emit()

    @Property(bool, notify=settings_changed)
    def canScan(self) -> bool:
        return (
            self._phase in (PHASE_IDLE, PHASE_DONE)
            and self.destinationValid
            and (self._settings.tracks or self._settings.backing
                 or self._settings.guis or self._settings.models)
        )

    # ==================================================================
    # Progress and phase
    # ==================================================================

    @Property(str, notify=phase_changed)
    def phase(self) -> str:
        return self._phase

    @Property(bool, notify=phase_changed)
    def isRunning(self) -> bool:
        return self._phase in (PHASE_SCANNING, PHASE_APPLYING)

    @Property(str, notify=progress_changed)
    def stage(self) -> str:
        return self._stage

    @Property(int, notify=progress_changed)
    def processed(self) -> int:
        return self._processed

    @Property(int, notify=progress_changed)
    def total(self) -> int:
        return self._total

    @Property(int, notify=progress_changed)
    def progress(self) -> int:
        """Completion as a percentage, for the progress strip."""
        if self._total <= 0:
            return 0
        return int(self._processed * 100 / self._total)

    # ==================================================================
    # The plan
    # ==================================================================

    @Property(bool, notify=plan_changed)
    def hasPlan(self) -> bool:
        return self._plan is not None

    @Property(int, notify=plan_changed)
    def addedCount(self) -> int:
        return len(self._plan.added) if self._plan else 0

    @Property(int, notify=plan_changed)
    def updatedCount(self) -> int:
        return len(self._plan.updated) if self._plan else 0

    @Property(int, notify=plan_changed)
    def unchangedCount(self) -> int:
        return self._plan.unchanged if self._plan else 0

    @Property(int, notify=plan_changed)
    def extraCount(self) -> int:
        return len(self._plan.extras) if self._plan else 0

    @Property(float, notify=plan_changed)
    def writeMb(self) -> float:
        return self._mb(self._plan.bytes_to_write) if self._plan else 0.0

    @Property(float, notify=plan_changed)
    def extraMb(self) -> float:
        return self._mb(self._plan.extra_bytes) if self._plan else 0.0

    @Property(float, notify=plan_changed)
    def backupBeforeMb(self) -> float:
        return self._mb(self._plan.backup_bytes_before) if self._plan else 0.0

    @Property(float, notify=plan_changed)
    def backupAfterMb(self) -> float:
        if self._plan is None:
            return 0.0
        return self._mb(self._current_plan().backup_bytes_after)

    @Property(float, notify=plan_changed)
    def freeMb(self) -> float:
        return self._mb(self._plan.free_bytes) if self._plan else 0.0

    @Property(str, notify=plan_changed)
    def identity(self) -> str:
        """``"new"``, ``"match"``, ``"absent"`` or ``"mismatch"``."""
        return self._plan.identity.value if self._plan else ""

    @Property(bool, notify=plan_changed)
    def identityMismatch(self) -> bool:
        return bool(self._plan and self._plan.identity is Identity.MISMATCH)

    @Property(bool, notify=plan_changed)
    def identityUnverified(self) -> bool:
        return bool(self._plan and self._plan.identity is Identity.ABSENT)

    @Property(str, notify=plan_changed)
    def sourceVaultId(self) -> str:
        return self._plan.source_vault_id if self._plan else ""

    @Property(str, notify=plan_changed)
    def backupVaultId(self) -> str:
        return self._plan.backup_vault_id if self._plan else ""

    @Property(str, notify=plan_changed)
    def backupCreatedAt(self) -> str:
        return self._plan.backup_created_at if self._plan else ""

    @Property(bool, notify=plan_changed)
    def fullVerify(self) -> bool:
        """Whether the scan had to hash everything for want of an index."""
        return bool(self._plan and self._plan.full_verify)

    @Property("QVariantList", notify=plan_changed)
    def warnings(self) -> list:
        return list(self._plan.warnings) if self._plan else []

    @Property(bool, notify=plan_changed)
    def canConfirm(self) -> bool:
        """Whether Confirm should do anything right now."""
        if self._plan is None or self._phase != PHASE_REVIEW:
            return False
        return not self._current_plan().is_blocked

    @Property(str, notify=plan_changed)
    def vaultPath(self) -> str:
        return str(library_root())

    # -- the result -----------------------------------------------------

    @Property(int, notify=plan_changed)
    def writtenCount(self) -> int:
        return self._result.written if self._result else 0

    @Property(int, notify=plan_changed)
    def deletedCount(self) -> int:
        return self._result.deleted if self._result else 0

    @Property("QVariantList", notify=plan_changed)
    def resultWarnings(self) -> list:
        return list(self._result.warnings) if self._result else []

    # ==================================================================
    # Slots
    # ==================================================================

    @Slot()
    def scan(self) -> None:
        """Work out what a sync would do. Writes nothing."""
        if self.isRunning:
            self.error_occurred.emit("A backup is already running")
            return
        if not self._settings.directory:
            self.error_occurred.emit("Choose a folder to back up to")
            return

        worker = BackupScanWorker(
            library_root(), Path(self._settings.directory), self._options(),
        )
        worker.finished.connect(self._on_scanned)

        self._discard_plan()
        self._reset_progress()
        if not self._start(worker):
            self.error_occurred.emit("A backup is already running")
            return
        self._set_phase(PHASE_SCANNING)

    @Slot()
    def confirm(self) -> None:
        """Carry out the plan currently on screen."""
        if self._plan is None or self._phase != PHASE_REVIEW:
            return
        plan = self._current_plan()
        if plan.is_blocked:
            self.error_occurred.emit(
                "This folder is a backup of a different library."
            )
            return

        worker = BackupApplyWorker(plan, self._app_version)
        worker.finished.connect(self._on_applied)

        self._reset_progress()
        if not self._start(worker):
            self.error_occurred.emit("A backup is already running")
            return
        self._set_phase(PHASE_APPLYING)

    @Slot()
    def cancel(self) -> None:
        """Stop after the file currently being written."""
        if self._worker is not None:
            self._worker.cancel()  # type: ignore[attr-defined]

    @Slot()
    def reset(self) -> None:
        """Drop the plan and go back to the start. For the dialog's Cancel."""
        if self.isRunning:
            return
        self._discard_plan()
        self._set_phase(PHASE_IDLE)

    @Slot()
    def revealDestination(self) -> None:
        """Open the backup folder in the file manager."""
        if self._settings.directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._settings.directory))

    def shutdown(self) -> None:
        """Stop the backup before the app exits.

        Without this, closing the window mid-backup leaves a thread writing
        files onto the user's drive after the app they started it from is gone —
        and, worse, leaves the lock file behind with nothing left to remove it.
        Called from ``bootstrap.persist_app_state``.
        """
        self.cancel()
        if self._thread is None:
            return
        self._thread.quit()
        # Brief: the worker stops at the next file boundary, and a hang here
        # would keep the window on screen after the user closed it.
        if self._thread.wait(5000):
            # Only once it really stopped. Releasing a worker while its thread
            # still runs is what `app.viewmodels.worker_threads` exists to stop.
            self._thread = None
            self._worker = None

    # ==================================================================
    # Internals
    # ==================================================================

    def _options(self) -> BackupOptions:
        return BackupOptions(
            tracks=self._settings.tracks,
            backing=self._settings.backing,
            guis=self._settings.guis,
            models=self._settings.models,
            delete_extras=self._delete_extras,
            override_mismatch=self._override_mismatch,
        )

    def _current_plan(self) -> BackupPlan:
        """The scanned plan, refreshed with the toggles set since it was made.

        The two toggles that live in the review pane — delete and override —
        change what the plan *does* without changing what it *found*, so they
        are folded in here rather than costing another scan.
        """
        assert self._plan is not None
        return replace(self._plan, options=self._options())

    def _set_option(self, name: str, value: bool) -> None:
        """Persist one of the four include toggles and tell the dialog.

        Changing what is in scope invalidates any plan already worked out, for
        the same reason changing the destination does: the numbers on screen
        would no longer describe what confirming would actually do.
        """
        if getattr(self._settings, name) == value:
            return
        setattr(self._settings, name, value)
        self._discard_plan()
        self.settings_changed.emit()

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
        thread.started.connect(worker.run)   # type: ignore[attr-defined]
        worker.stage.connect(self._on_stage)         # type: ignore[attr-defined]
        worker.progress.connect(self._on_progress)   # type: ignore[attr-defined]
        worker.failed.connect(self._on_failed)       # type: ignore[attr-defined]
        worker.cancelled.connect(self._on_cancelled)  # type: ignore[attr-defined]
        own_worker(thread, worker, self._on_stopped)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.settings_changed.emit()   # canScan is false while running
        return True

    def _discard_plan(self) -> None:
        self._plan = None
        self._result = None
        self._delete_extras = False
        self._override_mismatch = False
        self.plan_changed.emit()

    def _set_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self.phase_changed.emit()
        self.settings_changed.emit()

    def _reset_progress(self) -> None:
        self._stage = ""
        self._processed = 0
        self._total = 0
        self.progress_changed.emit()

    @staticmethod
    def _mb(value: int) -> float:
        return value / (1024 * 1024)

    @staticmethod
    def _to_local_path(value: str) -> str:
        """Turn a QML ``file:///`` URL into a plain path."""
        text = str(value or "")
        if text.startswith("file:"):
            return QUrl(text).toLocalFile()
        return text

    # ---- worker callbacks --------------------------------------------

    @Slot(str)
    def _on_stage(self, text: str) -> None:
        self._stage = text
        self.progress_changed.emit()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self._processed = done
        self._total = total
        self.progress_changed.emit()

    # The result handlers below record the outcome but do **not** move the
    # phase. They run on the main thread, but they are invoked from the
    # worker's ``finished``/``failed``/``cancelled`` signals, which are emitted
    # from inside ``run`` — so the worker's thread is by definition still alive
    # when they fire.
    #
    # The phase is what tells the dialog it may start the next pass. Flipping it
    # here let ``confirm()`` launch the apply worker while the scan thread was
    # still shutting down, and that thread's ``finished`` then cleared the
    # references belonging to the *new* worker. The last Python reference to a
    # live worker goes away, and the process segfaults — exactly the failure
    # ``app.viewmodels.worker_threads`` exists to describe. So the transition
    # waits for ``_on_stopped``, where there is genuinely no thread left.

    # Both read their payload off the worker rather than taking it as a signal
    # argument — see the note in app.backup.workers. The worker is still
    # referenced here when these run; it is only released in `_on_stopped`.

    @Slot()
    def _on_scanned(self) -> None:
        worker = self._worker
        if worker is not None:
            self._plan = worker.plan   # type: ignore[attr-defined]
        self._pending_phase = PHASE_REVIEW

    @Slot()
    def _on_applied(self) -> None:
        worker = self._worker
        if worker is not None:
            self._result = worker.result   # type: ignore[attr-defined]
        self._stage = ""
        self._pending_phase = PHASE_DONE

        # A successful run restamps the folder with today's date, so the path we
        # were given is very likely not the path we ended up at. Adopting it
        # matters: left pointing at the old name, the next sync would find
        # nothing there and cheerfully offer to create a second backup beside
        # the one it just wrote.
        if self._result is not None and self._result.destination is not None:
            self._settings.directory = str(self._result.destination)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._stage = ""
        # Back to whichever screen the user can act from: a scan that failed
        # returns them to the picker, an apply that failed leaves the plan up so
        # they can read what it was about to do and try again.
        self._pending_phase = PHASE_REVIEW if self._plan is not None else PHASE_IDLE
        self._pending_error = message

    # Not decorated as a Slot: it is connected to both workers' ``cancelled``
    # signals, and they carry different arguments — the scan has nothing to
    # report, the apply says how many files it got through.
    def _on_cancelled(self, written: int = 0) -> None:
        self._stage = ""
        self._pending_phase = PHASE_REVIEW if self._plan is not None else PHASE_IDLE
        if written:
            self._pending_error = (
                f"Backup stopped — {written} file{'' if written == 1 else 's'} "
                "written. Running it again picks up where it left off."
            )
        else:
            self._pending_error = "Backup stopped"

    @Slot()
    def _on_stopped(self) -> None:
        """The thread has genuinely finished; release it and announce the result.

        Everything that lets the user start something else happens here, and
        nowhere earlier, so there is never a moment where the dialog offers a
        button whose handler would trample a running worker.
        """
        self._thread = None
        self._worker = None

        phase, self._pending_phase = self._pending_phase, None
        error, self._pending_error = self._pending_error, ""
        result = self._result

        if phase is not None:
            self._set_phase(phase)
        self.plan_changed.emit()
        self.progress_changed.emit()
        self.phase_changed.emit()
        self.settings_changed.emit()

        if error:
            self.error_occurred.emit(error)
        elif phase == PHASE_DONE and result is not None:
            self.backup_finished.emit(
                result.written, result.deleted, str(result.destination or ""),
            )
