"""Backing-track generation — the model behind Nocturne's hidden tab.

A batch queue of tracks waiting to have their vocals stripped, run strictly one
at a time. Sequential is not a simplification here: separation saturates
whichever device it runs on, so a second concurrent job would make both slower
and could exhaust GPU memory outright.

The list is a ``QAbstractListModel`` of jobs rather than of library tracks,
because what the tab shows is per-job state — queued, running, done, failed —
which no track list has a place for.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QThread,
    Property,
    Qt,
    Signal,
    Slot,
)

from app.separation import models as model_catalog
from app.separation.workers import DeviceProbeWorker, SeparationWorker
from app.settings.backing_track import BackingTrackSettings, DEVICES
from app.services.track_service import TrackService
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)

# Job states, as strings because they go straight to QML.
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

_ROLE_BASE = int(Qt.ItemDataRole.UserRole) + 1
ROLE_TRACK_ID = _ROLE_BASE
ROLE_TITLE = _ROLE_BASE + 1
ROLE_ARTIST = _ROLE_BASE + 2
ROLE_STATE = _ROLE_BASE + 3
ROLE_PROGRESS = _ROLE_BASE + 4
ROLE_MESSAGE = _ROLE_BASE + 5

_ROLE_NAMES = {
    ROLE_TRACK_ID: b"trackId",
    ROLE_TITLE: b"title",
    ROLE_ARTIST: b"artist",
    ROLE_STATE: b"state",
    ROLE_PROGRESS: b"progress",
    ROLE_MESSAGE: b"message",
}


class BackingTrackViewModel(QAbstractListModel):
    """Queue, run and report on vocal-removal jobs."""

    queue_changed = Signal()
    running_changed = Signal()
    devices_changed = Signal()
    models_changed = Signal()
    settings_changed = Signal()
    error_occurred = Signal(str)
    batch_finished = Signal()

    def __init__(
        self,
        track_service: TrackService,
        backing_service,
        settings: BackingTrackSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._track_service = track_service
        self._backing = backing_service
        self._settings = settings or BackingTrackSettings(None)

        self._jobs: List[Dict[str, Any]] = []
        self._active_track_id: Optional[int] = None

        self._thread: Optional[QThread] = None
        self._worker: Optional[SeparationWorker] = None
        self._probe_thread: Optional[QThread] = None
        self._probe_worker: Optional[DeviceProbeWorker] = None

        self._devices: List[str] = []
        self._device_error: str = ""
        self._backend: str = ""
        self._backend_error: str = ""
        self._probing: bool = False

        self._models: List[Dict[str, Any]] = []
        self.refreshModels()

    # ==================================================================
    # Model
    # ==================================================================

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._jobs)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._jobs):
            return None
        job = self._jobs[index.row()]
        return {
            ROLE_TRACK_ID: job["track_id"],
            ROLE_TITLE: job["title"],
            ROLE_ARTIST: job["artist"],
            ROLE_STATE: job["state"],
            ROLE_PROGRESS: job["progress"],
            ROLE_MESSAGE: job["message"],
        }.get(int(role))

    def roleNames(self) -> dict[int, bytes]:
        return _ROLE_NAMES

    # ==================================================================
    # Properties
    # ==================================================================

    @Property(int, notify=queue_changed)
    def jobCount(self) -> int:
        return len(self._jobs)

    @Property(int, notify=queue_changed)
    def completedCount(self) -> int:
        """Jobs that will not run again — done, failed or cancelled."""
        return sum(
            1 for j in self._jobs
            if j["state"] in (STATE_DONE, STATE_FAILED, STATE_CANCELLED)
        )

    @Property(int, notify=queue_changed)
    def succeededCount(self) -> int:
        return sum(1 for j in self._jobs if j["state"] == STATE_DONE)

    @Property(int, notify=queue_changed)
    def failedCount(self) -> int:
        return sum(1 for j in self._jobs if j["state"] == STATE_FAILED)

    @Property(bool, notify=running_changed)
    def isRunning(self) -> bool:
        return self._active_track_id is not None

    @Property(int, notify=running_changed)
    def activeProgress(self) -> int:
        """0-100 within the track currently being separated."""
        job = self._job_for(self._active_track_id) if self._active_track_id else None
        return int(job["progress"]) if job else 0

    @Property(str, notify=running_changed)
    def activeStage(self) -> str:
        job = self._job_for(self._active_track_id) if self._active_track_id else None
        return str(job["message"]) if job else ""

    @Property(str, notify=running_changed)
    def activeTitle(self) -> str:
        job = self._job_for(self._active_track_id) if self._active_track_id else None
        return str(job["title"]) if job else ""

    # ---- configuration -----------------------------------------------

    @Property("QVariantList", notify=models_changed)
    def models(self) -> List[Dict[str, Any]]:
        """Every checkpoint in the vault's models folder, usable or not."""
        return self._models

    @Property(str, notify=settings_changed)
    def selectedModel(self) -> str:
        return self._settings.model_file

    @selectedModel.setter
    def selectedModel(self, value: str) -> None:
        if value == self._settings.model_file:
            return
        self._settings.model_file = value
        self.settings_changed.emit()

    @Property(str, notify=settings_changed)
    def device(self) -> str:
        return self._settings.device

    @device.setter
    def device(self, value: str) -> None:
        if value == self._settings.device:
            return
        self._settings.device = value
        self.settings_changed.emit()

    @Property("QVariantList", constant=True)
    def deviceChoices(self) -> List[str]:
        """Every device the UI may offer, whether or not it is available here."""
        return list(DEVICES)

    @Property("QVariantList", notify=devices_changed)
    def availableDevices(self) -> List[str]:
        """Devices the configured interpreter actually reported."""
        return self._devices

    @Property(str, notify=devices_changed)
    def deviceError(self) -> str:
        return self._device_error

    @Property(str, notify=devices_changed)
    def backend(self) -> str:
        """Name of the separation code the interpreter was found to have."""
        return self._backend

    @Property(str, notify=devices_changed)
    def backendError(self) -> str:
        """Why there is no backend, when there is none.

        Distinct from :attr:`deviceError`: this interpreter answered, it just
        has nothing installed that can run a model.
        """
        return self._backend_error

    @Property(bool, notify=devices_changed)
    def isProbing(self) -> bool:
        return self._probing

    @Property(str, notify=settings_changed)
    def pythonPath(self) -> str:
        return self._settings.python_path

    @pythonPath.setter
    def pythonPath(self, value: str) -> None:
        value = self._strip_url(value)
        if value == self._settings.python_path:
            return
        self._settings.python_path = value
        # What the old interpreter could do says nothing about the new one.
        self._devices = []
        self._device_error = ""
        self._backend = ""
        self._backend_error = ""
        self.devices_changed.emit()
        self.settings_changed.emit()
        if value:
            self.probeDevices()

    @Property(str, notify=settings_changed)
    def msstPath(self) -> str:
        return self._settings.msst_path

    @msstPath.setter
    def msstPath(self, value: str) -> None:
        value = self._strip_url(value)
        if value == self._settings.msst_path:
            return
        self._settings.msst_path = value
        self.settings_changed.emit()
        # Which backend is available is exactly what this changes, so the
        # answer on screen is stale the moment it is set.
        if self._settings.python_path:
            self.probeDevices()

    @Property(bool, notify=settings_changed)
    def canGenerate(self) -> bool:
        """Whether a generation could be started at all."""
        return bool(self._settings.python_path) and model_catalog.resolve(
            self._settings.model_file
        ) is not None

    # ==================================================================
    # Configuration slots
    # ==================================================================

    @Slot()
    def refreshModels(self) -> None:
        self._models = model_catalog.available_models()
        # A model that was selected and then deleted must not stay selected —
        # it would fail at the far end of a long job instead of here.
        if self._settings.model_file and model_catalog.resolve(self._settings.model_file) is None:
            usable = model_catalog.usable_models()
            self._settings.model_file = usable[0]["name"] if usable else ""
            self.settings_changed.emit()
        elif not self._settings.model_file:
            usable = model_catalog.usable_models()
            if usable:
                self._settings.model_file = usable[0]["name"]
                self.settings_changed.emit()
        self.models_changed.emit()

    @Slot(str)
    def importModel(self, path: str) -> None:
        """Copy a model file (and its config) into the vault."""
        try:
            model = model_catalog.import_model(self._strip_url(path))
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(str(exc))
            return

        if not model["usable"]:
            self.error_occurred.emit(
                f"{model['name']} was copied in, but {model['reason'][0].lower()}"
                f"{model['reason'][1:]} — it cannot be used until that is beside it"
            )
        else:
            self._settings.model_file = model["name"]
            self.settings_changed.emit()
        self.refreshModels()

    @Slot()
    def probeDevicesIfNeeded(self) -> None:
        """Probe when a page that shows the answer opens, but only once.

        Asking costs a child interpreter importing PyTorch, which is seconds —
        far too much to spend at startup for a feature most sessions never
        touch, and pointless to repeat once it has answered. A previous failure
        does count as no answer: whatever was wrong may well have just been
        fixed, which is generally why someone is back on this page.
        """
        if self._devices or self._probing or not self._settings.python_path:
            return
        self.probeDevices()

    @Slot()
    def probeDevices(self) -> None:
        """Ask the configured interpreter which accelerators it can use."""
        if self._probing:
            return
        python_path = self._settings.python_path
        if not python_path:
            self._devices = []
            self._device_error = "No Python interpreter selected"
            self.devices_changed.emit()
            return

        self._probing = True
        self._device_error = ""
        self._backend_error = ""
        self.devices_changed.emit()

        thread = QThread(self)
        worker = DeviceProbeWorker(python_path, self._settings.msst_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_devices_probed)
        own_worker(thread, worker, self._on_probe_stopped)
        self._probe_thread = thread
        self._probe_worker = worker
        thread.start()

    @Slot(list, str, str, str)
    def _on_devices_probed(self, devices: list, error: str,
                           backend: str, backend_error: str) -> None:
        self._devices = [str(d) for d in devices]
        self._device_error = error
        self._backend = backend
        self._backend_error = backend_error
        # A device that is selected but not present would silently fall back at
        # run time; move the selection to something real instead.
        if self._devices and self._settings.device not in ("auto", *self._devices):
            self._settings.device = "auto"
            self.settings_changed.emit()

    @Slot()
    def _on_probe_stopped(self) -> None:
        self._probe_thread = None
        self._probe_worker = None
        self._probing = False
        self.devices_changed.emit()

    # ==================================================================
    # Queue
    # ==================================================================

    @Slot("QVariantList")
    def enqueue(self, track_ids: list) -> None:
        """Add tracks to the batch, skipping ones already queued or covered.

        Does not start anything — the user presses Generate. Opening the tab
        with work already running should not silently add to it.
        """
        existing = {j["track_id"] for j in self._jobs
                    if j["state"] in (STATE_QUEUED, STATE_RUNNING)}
        additions: List[Dict[str, Any]] = []

        for raw in track_ids:
            try:
                track_id = int(raw)
            except (TypeError, ValueError):
                continue
            # 0xFFFF is the vault's ceiling: an id past it cannot name a file,
            # so let it fail here rather than deep inside a worker.
            if track_id <= 0 or track_id > 0xFFFF or track_id in existing:
                continue
            if self._backing.has(track_id):
                continue

            track = self._track_service.get_track_by_id(track_id)
            if track is None:
                continue

            existing.add(track_id)
            additions.append({
                "track_id": track_id,
                "title": track.title,
                "artist": track.artist,
                "state": STATE_QUEUED,
                "progress": 0,
                "message": "",
            })

        if not additions:
            return

        start = len(self._jobs)
        self.beginInsertRows(QModelIndex(), start, start + len(additions) - 1)
        self._jobs.extend(additions)
        self.endInsertRows()
        self.queue_changed.emit()

    @Slot(int)
    def removeJob(self, track_id: int) -> None:
        """Drop a job from the list. The running one cannot be removed, only cancelled."""
        row = self._row_for(track_id)
        if row is None or self._jobs[row]["state"] == STATE_RUNNING:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._jobs.pop(row)
        self.endRemoveRows()
        self.queue_changed.emit()

    @Slot()
    def clearFinished(self) -> None:
        """Remove every job that will not run again."""
        keep = [j for j in self._jobs
                if j["state"] in (STATE_QUEUED, STATE_RUNNING)]
        if len(keep) == len(self._jobs):
            return
        self.beginResetModel()
        self._jobs = keep
        self.endResetModel()
        self.queue_changed.emit()

    @Slot()
    def clearAll(self) -> None:
        """Empty the list, cancelling anything in flight."""
        self.cancel()
        self.beginResetModel()
        self._jobs = []
        self.endResetModel()
        self.queue_changed.emit()

    # ==================================================================
    # Running
    # ==================================================================

    @Slot()
    def generate(self) -> None:
        """Start working through the queue."""
        if self._active_track_id is not None:
            return

        model = model_catalog.resolve(self._settings.model_file)
        if model is None:
            self.error_occurred.emit(
                "Choose a vocal-removal model in Settings before generating"
            )
            return
        if not self._settings.python_path:
            self.error_occurred.emit(
                "Choose a Python interpreter in Settings before generating — "
                "Kroma cannot run the model itself"
            )
            return

        self._pump()

    @Slot()
    def cancel(self) -> None:
        """Stop after the current track, and abandon it if it can be interrupted.

        Everything still queued is marked cancelled immediately, so the list
        stops claiming work that will never happen.
        """
        for row, job in enumerate(self._jobs):
            if job["state"] == STATE_QUEUED:
                self._set_job(row, state=STATE_CANCELLED, message="Cancelled")

        if self._worker is not None:
            self._worker.cancel()
        self.queue_changed.emit()

    def _pump(self) -> None:
        """Start the next queued job, if nothing is running."""
        if self._active_track_id is not None:
            return

        row = next(
            (i for i, j in enumerate(self._jobs) if j["state"] == STATE_QUEUED),
            None,
        )
        if row is None:
            self.batch_finished.emit()
            return

        model = model_catalog.resolve(self._settings.model_file)
        if model is None:
            self.error_occurred.emit("The selected model is no longer available")
            return

        job = self._jobs[row]
        track_id = int(job["track_id"])
        self._active_track_id = track_id
        self._set_job(row, state=STATE_RUNNING, progress=0, message="Starting")

        thread = QThread(self)
        worker = SeparationWorker(
            track_id,
            self._settings.python_path,
            model["path"],
            self._settings.device,
            self._backing,
            model["config"],
            self._settings.msst_path,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage.connect(self._on_stage)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        own_worker(thread, worker, self._on_stopped)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.running_changed.emit()

    # ---- worker callbacks --------------------------------------------

    @Slot(int, str)
    def _on_stage(self, track_id: int, text: str) -> None:
        row = self._row_for(track_id)
        if row is not None:
            self._set_job(row, message=text)
            self.running_changed.emit()

    @Slot(int, int)
    def _on_progress(self, track_id: int, percent: int) -> None:
        row = self._row_for(track_id)
        if row is not None:
            self._set_job(row, progress=percent)
            self.running_changed.emit()

    @Slot(int)
    def _on_finished(self, track_id: int) -> None:
        row = self._row_for(track_id)
        if row is not None:
            self._set_job(row, state=STATE_DONE, progress=100, message="Done")

    @Slot(int, str)
    def _on_failed(self, track_id: int, message: str) -> None:
        row = self._row_for(track_id)
        if row is not None:
            self._set_job(row, state=STATE_FAILED, message=message)
        logger.warning("Generation failed for track %s: %s", track_id, message)

    @Slot(int)
    def _on_cancelled(self, track_id: int) -> None:
        row = self._row_for(track_id)
        if row is not None:
            self._set_job(row, state=STATE_CANCELLED, message="Cancelled")

    @Slot()
    def _on_stopped(self) -> None:
        """The separation thread has gone. Release it, then take the next job.

        Pumping from here rather than from the worker's `done` is what keeps
        "one separation at a time" true: the next child interpreter is not
        launched until the previous thread has actually stopped, instead of
        merely having been asked to. The track id `done` carries is not needed —
        every per-job state change already happened in the handlers above.
        """
        self._thread = None
        self._worker = None
        self._active_track_id = None
        self.running_changed.emit()
        self.queue_changed.emit()
        self._pump()

    def shutdown(self) -> None:
        """Stop everything before the app exits.

        A separation runs in a child interpreter that Kroma started, so closing
        the window without this would leave a process holding the GPU with
        nothing left to hand its output to. Called from
        ``bootstrap.persist_app_state``.
        """
        self.cancel()
        stopped = True
        for thread in (self._thread, self._probe_thread):
            if thread is None:
                continue
            thread.quit()
            # Brief: the child is already being terminated, and a hang here
            # would keep the window on screen after the user closed it.
            stopped = thread.wait(3000) and stopped

        # Only if every thread really stopped. Releasing a worker while its
        # thread still runs is the one thing that turns a slow shutdown into a
        # crashed one — see `app.viewmodels.worker_threads`. Holding them costs
        # nothing here; the process is on its way out either way.
        if stopped:
            self._thread = None
            self._probe_thread = None
            self._worker = None
            self._probe_worker = None
        self._active_track_id = None

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _strip_url(value: str) -> str:
        """Turn a QML ``file:///`` URL into a plain path."""
        text = str(value or "")
        if text.startswith("file:///"):
            # Windows paths come back as file:///C:/... ; POSIX as file:///home/...
            trimmed = text[8:]
            return trimmed if (len(trimmed) > 1 and trimmed[1] == ":") else "/" + trimmed
        if text.startswith("file://"):
            return text[7:]
        return text

    def _row_for(self, track_id: int) -> Optional[int]:
        for row, job in enumerate(self._jobs):
            if job["track_id"] == track_id:
                return row
        return None

    def _job_for(self, track_id: int) -> Optional[Dict[str, Any]]:
        row = self._row_for(track_id)
        return self._jobs[row] if row is not None else None

    def _set_job(self, row: int, **changes: Any) -> None:
        job = self._jobs[row]
        roles = []
        for key, value in changes.items():
            if job.get(key) == value:
                continue
            job[key] = value
            roles.append({
                "state": ROLE_STATE,
                "progress": ROLE_PROGRESS,
                "message": ROLE_MESSAGE,
            }.get(key, ROLE_STATE))
        if roles:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, roles)
