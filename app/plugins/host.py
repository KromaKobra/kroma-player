"""The plugin host: which plugins exist, which are on, and their processes.

One object, built once in bootstrap and living on the main thread. Everything
that asks a plugin anything goes through :meth:`PluginHost.call`, from a worker
thread, and everything that asks *about* plugins — what is installed, what is
on, what they declare — reads the manifests held here and starts nothing.

**What makes a plugin usable**, all of it at once:

- it is installed on this machine, or loaded from ``$KROMA_PLUGIN_DIR``;
- its manifest is valid and its API level is one this build supports;
- this vault has it enabled;
- if it has terms, the user agreed to *this* text — the sha256 recorded at
  acceptance matches the file now, so an upgrade that changes the agreement asks
  again;
- it has not been disabled for crashing on startup three times in a row;
- Kroma was not started in safe mode.

A plugin failing any of those contributes nothing: no providers, no menu rows,
no id fields. That is the first of the three consent gates, and it is the one
that keeps a plain install free of every online feature.

**Lazily started, idly stopped.** A process starts the first time something
needs it, under a per-plugin lock so two workers arriving together start one,
and is told ``initialize`` before any request. After five minutes with nothing
outstanding it is stopped again; a library browsed for an evening costs no
background processes at all.

**Crashes are contained.** A process that dies fails its pending calls — every
waiting worker wakes with an error — and the next call starts a new one. A
plugin that keeps dying is backed off: calls fail fast for a few seconds rather
than restarting it on every request. One that dies three times running before it
has even answered ``initialize`` is switched off, with the reason recorded where
Settings → Plugins can show it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from app.plugins import paths, programs
from app.plugins.manifest import (
    IdKind,
    PluginManifest,
    PluginManifestError,
    load_manifest,
)
from app.plugins.process import (
    PluginCrashed,
    PluginError,
    PluginProcess,
    PluginTimeout,
    ProgressCallback,
    StopCheck,
)
from app.versions import APP_VERSION, PLUGIN_API_VERSION

logger = logging.getLogger(__name__)

SAFE_MODE_ENV = "KROMA_SAFE_MODE"
SAFE_MODE_FLAG = "--safe-mode"

#: How long ``initialize`` may take before the start counts as a crash.
INITIALIZE_TIMEOUT = 15.0
#: Idle processes are stopped after this long with nothing outstanding.
IDLE_SECONDS = 5 * 60
#: How often the idle check runs.
IDLE_CHECK_MS = 30_000
#: Consecutive startup crashes that switch a plugin off.
STARTUP_CRASH_LIMIT = 3
#: The longest a crashing plugin is backed off for.
MAX_BACKOFF_SECONDS = 30.0

# Keys inside the vault's ``plugins.<id>`` scope.
KEY_ENABLED = "enabled"
KEY_CONSENT = "consent"
KEY_SETTINGS = "settings"
KEY_DISABLED_REASON = "disabled_reason"
# Inside the ``app`` scope: the user's provider priority, ``{kind: [ids]}``.
PROVIDER_ORDER_KEY = "provider_order"

SOURCE_INSTALLED = "installed"
SOURCE_DEV = "dev"
SOURCE_MISSING = "missing"


def safe_mode_requested(argv: Sequence[str] = ()) -> bool:
    """Whether this launch should start with every plugin off."""
    if os.environ.get(SAFE_MODE_ENV, "").strip() not in ("", "0", "false"):
        return True
    return SAFE_MODE_FLAG in argv


def default_command(plugin_dir: Path) -> List[str]:
    """How to start a Python plugin from this build.

    Frozen, the ``kroma-plugin-host`` executable shipped beside Kroma's own. From
    source, this interpreter in isolated mode with no site-packages — see
    :mod:`app.plugins.host_entry` for why both halves of that matter.
    """
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        host = Path(sys.executable).parent / f"kroma-plugin-host{suffix}"
        return [str(host), str(plugin_dir)]
    entry = Path(__file__).with_name("host_entry.py")
    return [sys.executable, "-I", "-S", str(entry), str(plugin_dir)]


def consent_hash(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


@dataclass
class PluginRecord:
    """One plugin the host knows about, running or not."""

    id: str
    manifest: Optional[PluginManifest]
    path: Optional[Path]
    source: str
    error: str = ""
    process: Optional[PluginProcess] = None
    start_lock: threading.Lock = field(default_factory=threading.Lock)
    startup_crashes: int = 0
    crashes: int = 0
    backoff_until: float = 0.0
    last_crash: str = ""

    @property
    def name(self) -> str:
        return self.manifest.name if self.manifest else self.id


@dataclass
class Job:
    """A long request in flight, for the shared task list."""

    token: int
    plugin_id: str
    label: str
    stage: str = ""
    percent: float = -1.0


class PluginHost(QObject):
    """Discovers, enables, starts and stops plugins. Main thread."""

    #: The set of plugins, or any of their states, changed.
    plugins_changed = Signal()
    #: A plugin crashed. ``(plugin id, reason)``. Emitted from whichever thread
    #: noticed, so a connection to it is queued onto the main thread.
    plugin_crashed = Signal(str, str)
    #: The task list changed.
    jobs_changed = Signal()
    #: Settings a running plugin was sent, or will be on its next start.
    settings_changed = Signal(str)

    def __init__(
        self,
        store,
        *,
        safe_mode: bool = False,
        host_version: str = APP_VERSION,
        command_for: Optional[Callable[[Path], List[str]]] = None,
        guard_main_thread: bool = True,
        idle_seconds: float = IDLE_SECONDS,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._safe_mode = bool(safe_mode)
        self._host_version = host_version
        self._command_for = command_for or default_command
        self._guard_main_thread = guard_main_thread
        self._idle_seconds = idle_seconds
        self._records: Dict[str, PluginRecord] = {}
        self._lock = threading.RLock()
        self._jobs: Dict[int, Job] = {}
        self._job_tokens = 0
        self._shut_down = False
        # What `active()` answered, until something changes. It is asked at
        # paint time — the identity columns of every track list read the
        # declarations through it — and answering means hashing consent files,
        # which is not something a scroll should do once per row.
        self._active_cache: Optional[List[PluginManifest]] = None

        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self.stop_idle)
        # Only with an event loop to drive it. A host built headless — a test,
        # a script — has nothing to tick the timer, and `stop_idle` is public
        # for exactly that caller.
        if QCoreApplication.instance() is not None:
            self._idle_timer.start()

        self.discover()

    # ==================================================================
    # Discovery
    # ==================================================================

    @property
    def safe_mode(self) -> bool:
        return self._safe_mode

    def discover(self) -> None:
        """Re-read every manifest: installed, from ``$KROMA_PLUGIN_DIR``, and missing.

        A running process survives a rediscovery whose manifest has not moved
        — the record is updated in place — so pressing "check again" in
        Settings does not kill a download.
        """
        found: Dict[str, PluginRecord] = {}

        root = paths.plugins_root()
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if not (child / "plugin.json").is_file():
                    continue
                found[child.name] = self._load(child, SOURCE_INSTALLED, expect_id=child.name)

        # A checkout wins over an installed copy of the same plugin, exactly as
        # $KROMA_GUI_DIR wins over an installed GUI: it is what the developer is
        # working on.
        for checkout in paths.dev_dirs():
            record = self._load(checkout, SOURCE_DEV)
            found[record.id] = record

        # Enabled in this vault, absent from this machine. Listed so the vault
        # can say what it expects; nothing about them can run.
        for plugin_id in self._store.plugin_ids():
            if plugin_id in found:
                continue
            if self._store.plugin_get(plugin_id, KEY_ENABLED, False):
                found[plugin_id] = PluginRecord(
                    plugin_id, None, None, SOURCE_MISSING,
                    "Enabled in this library, but not installed on this computer",
                )

        with self._lock:
            for plugin_id, record in found.items():
                old = self._records.get(plugin_id)
                if old is not None and old.process is not None:
                    if record.manifest is not None and old.manifest == record.manifest and old.path == record.path:
                        record = old
                    else:
                        old.process.shutdown()
                found[plugin_id] = record
            for plugin_id, old in self._records.items():
                if plugin_id not in found and old.process is not None:
                    old.process.shutdown()
            self._records = found
        self._changed()

    def _load(self, directory: Path, source: str, expect_id: str = "") -> PluginRecord:
        try:
            manifest = load_manifest(directory)
        except PluginManifestError as exc:
            return PluginRecord(expect_id or directory.name, None, directory, source, str(exc))
        if expect_id and manifest.id != expect_id:
            return PluginRecord(
                expect_id, None, directory, source,
                f"is installed as {expect_id!r} but its manifest says {manifest.id!r}",
            )
        error = manifest.incompatibility_reason()
        return PluginRecord(manifest.id, manifest, directory, source, error)

    def _changed(self) -> None:
        """Forget every derived answer, and say that something moved."""
        self._active_cache = None
        self.plugins_changed.emit()

    def records(self) -> List[PluginRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda r: r.id)

    def record(self, plugin_id: str) -> Optional[PluginRecord]:
        with self._lock:
            return self._records.get(plugin_id)

    # ==================================================================
    # Enabled, consent, settings
    # ==================================================================

    def is_enabled(self, plugin_id: str) -> bool:
        return bool(self._store.plugin_get(plugin_id, KEY_ENABLED, False))

    def disabled_reason(self, plugin_id: str) -> str:
        return str(self._store.plugin_get(plugin_id, KEY_DISABLED_REASON, "") or "")

    def consent_text(self, plugin_id: str) -> str:
        data = self._consent_bytes(plugin_id)
        return data.decode("utf-8", "replace") if data else ""

    def _consent_bytes(self, plugin_id: str) -> bytes:
        record = self.record(plugin_id)
        if record is None or record.manifest is None or not record.manifest.consent:
            return b""
        try:
            return (record.path / record.manifest.consent).read_bytes()
        except OSError:
            return b""

    def needs_consent(self, plugin_id: str) -> bool:
        """Whether this plugin has terms the user has not agreed to — this text of them."""
        record = self.record(plugin_id)
        if record is None or record.manifest is None or not record.manifest.consent:
            return False
        recorded = self._store.plugin_get(plugin_id, KEY_CONSENT) or {}
        text = self._consent_bytes(plugin_id)
        if not text:
            # Terms the manifest promises and the folder does not have cannot
            # be agreed to; the plugin stays off rather than running unagreed.
            return True
        return not (isinstance(recorded, Mapping) and recorded.get("sha256") == consent_hash(text))

    def has_consent(self, plugin_id: str) -> bool:
        return not self.needs_consent(plugin_id)

    def accept_consent(self, plugin_id: str) -> bool:
        """Record agreement to the plugin's current terms. Flushed at once.

        Written straight to disk rather than on the debounce: a consent that a
        crash a minute later forgot would ask the user again, and a consent
        that was *recorded* without being saved could be relied on by a running
        download that the next launch then disowns.
        """
        text = self._consent_bytes(plugin_id)
        if not text:
            return False
        self._store.plugin_set(plugin_id, KEY_CONSENT, {
            "sha256": consent_hash(text),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        self._store.flush()
        self._notify_settings(plugin_id)
        self._changed()
        return True

    def set_enabled(self, plugin_id: str, enabled: bool) -> str:
        """Turn a plugin on or off. Returns ``""``, or why it could not be turned on.

        Turning on a plugin whose terms have not been agreed to is refused with
        ``"consent"`` — the caller shows the terms and calls
        :meth:`accept_consent`, then this again. Turning one off stops its
        process at once; whatever it was doing for a worker fails, which is
        what "off" means.
        """
        record = self.record(plugin_id)
        if enabled:
            if record is None or record.manifest is None:
                return record.error if record else "That plugin is not installed"
            if record.error:
                return record.error
            if self.needs_consent(plugin_id):
                return "consent"
            self._store.plugin_set(plugin_id, KEY_ENABLED, True)
            self._store.plugin_remove(plugin_id, KEY_DISABLED_REASON)
            if record is not None:
                record.startup_crashes = 0
                record.crashes = 0
                record.backoff_until = 0.0
        else:
            self._store.plugin_set(plugin_id, KEY_ENABLED, False)
            self.stop(plugin_id)
        self._store.flush()
        self._changed()
        return ""

    def settings(self, plugin_id: str) -> Dict[str, Any]:
        """The plugin's settings, every declared key present and of its type."""
        record = self.record(plugin_id)
        stored = self._store.plugin_get(plugin_id, KEY_SETTINGS) or {}
        if not isinstance(stored, Mapping):
            stored = {}
        if record is None or record.manifest is None:
            return dict(stored)
        return {
            decl.key: decl.coerce(stored[decl.key]) if decl.key in stored else decl.default
            for decl in record.manifest.settings
        }

    def set_setting(self, plugin_id: str, key: str, value: Any) -> bool:
        record = self.record(plugin_id)
        if record is None or record.manifest is None:
            return False
        decl = record.manifest.setting(key)
        if decl is None:
            return False
        stored = dict(self._store.plugin_get(plugin_id, KEY_SETTINGS) or {})
        stored[key] = decl.coerce(value)
        self._store.plugin_set(plugin_id, KEY_SETTINGS, stored)
        self._notify_settings(plugin_id)
        self._changed()
        return True

    def _notify_settings(self, plugin_id: str) -> None:
        record = self.record(plugin_id)
        process = record.process if record else None
        if process is not None and process.running:
            try:
                process.notify("settingsChanged", {
                    "settings": self.settings(plugin_id),
                    "consented": self.has_consent(plugin_id),
                })
            except Exception:  # noqa: BLE001
                logger.debug("Could not send settings to %s", plugin_id, exc_info=True)
        self.settings_changed.emit(plugin_id)

    # ==================================================================
    # What is active
    # ==================================================================

    def usable(self, plugin_id: str) -> bool:
        """Every condition in the module docstring, at once."""
        if self._safe_mode or self._shut_down:
            return False
        record = self.record(plugin_id)
        if record is None or record.manifest is None or record.error:
            return False
        if not self.is_enabled(plugin_id):
            return False
        return not self.needs_consent(plugin_id)

    def active(self) -> List[PluginManifest]:
        """Manifests of every usable plugin, in id order. Cached until anything changes."""
        cached = self._active_cache
        if cached is not None:
            return list(cached)
        fresh = [
            r.manifest for r in self.records()
            if r.manifest is not None and self.usable(r.id)
        ]
        self._active_cache = fresh
        return list(fresh)

    def provider_order(self, kind: str) -> List[str]:
        stored = self._store.app_get(PROVIDER_ORDER_KEY) or {}
        order = stored.get(kind) if isinstance(stored, Mapping) else None
        return [str(i) for i in order] if isinstance(order, list) else []

    def set_provider_order(self, kind: str, ids: Sequence[str]) -> None:
        stored = dict(self._store.app_get(PROVIDER_ORDER_KEY) or {})
        stored[kind] = [str(i) for i in ids]
        self._store.app_set(PROVIDER_ORDER_KEY, stored)
        self._changed()

    def providers(self, kind: str) -> List[PluginManifest]:
        """Usable plugins offering *kind*, in the user's priority order.

        Ids the user has ordered come first in that order; the rest follow by
        id — never by load order, which is an accident of the filesystem.
        """
        offering = [m for m in self.active() if m.provides_kind(kind)]
        order = self.provider_order(kind)
        rank = {plugin_id: index for index, plugin_id in enumerate(order)}
        return sorted(offering, key=lambda m: (rank.get(m.id, len(rank)), m.id))

    def declarations(self, entity: Optional[str] = None) -> List[IdKind]:
        """Every external-id kind usable plugins declare, first declarer wins per key."""
        seen: set = set()
        kinds: List[IdKind] = []
        for manifest in self.active():
            for kind in manifest.external_ids:
                if entity is not None and kind.entity != entity:
                    continue
                if (kind.entity, kind.key) in seen:
                    continue
                seen.add((kind.entity, kind.key))
                kinds.append(kind)
        return kinds

    # ==================================================================
    # Processes
    # ==================================================================

    def call(
        self,
        plugin_id: str,
        method: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        timeout: Optional[float] = 60.0,
        should_stop: StopCheck = None,
        on_progress: ProgressCallback = None,
        job_label: str = "",
    ) -> Any:
        """Ask *plugin_id* one question, starting it if it is not running.

        From a worker thread only — :class:`~app.plugins.process.PluginProcess`
        refuses the main thread. *job_label* puts the request on the shared
        task list for as long as it runs.
        """
        if not self.usable(plugin_id):
            raise PluginError(self._unusable_reason(plugin_id))
        process = self._ensure_started(plugin_id, should_stop)
        token = self._open_job(plugin_id, job_label) if job_label else 0

        def progress(params: Mapping[str, Any]) -> None:
            if token:
                self._update_job(token, params)
            if on_progress is not None:
                on_progress(params)

        try:
            result = process.call(
                method, params, timeout=timeout, should_stop=should_stop,
                on_progress=progress,
            )
        finally:
            if token:
                self._close_job(token)
        record = self.record(plugin_id)
        if record is not None:
            record.crashes = 0
        return result

    def _unusable_reason(self, plugin_id: str) -> str:
        record = self.record(plugin_id)
        name = record.name if record else plugin_id
        if self._safe_mode:
            return "Kroma was started in safe mode, with every plugin off"
        if record is None or record.manifest is None:
            return f"{name} is not installed"
        if record.error:
            return f"{name} {record.error}"
        if not self.is_enabled(plugin_id):
            reason = self.disabled_reason(plugin_id)
            return f"{name} is turned off" + (f" — {reason}" if reason else "")
        if self.needs_consent(plugin_id):
            return f"{name} needs you to agree to its terms first"
        return f"{name} is unavailable"

    def _ensure_started(self, plugin_id: str, should_stop: StopCheck) -> PluginProcess:
        record = self.record(plugin_id)
        if record is None or record.manifest is None or record.path is None:
            raise PluginError(self._unusable_reason(plugin_id))
        with record.start_lock:
            if record.process is not None and record.process.running:
                return record.process
            wait = record.backoff_until - time.monotonic()
            if wait > 0:
                raise PluginCrashed(
                    f"{record.name} keeps stopping — trying again in {int(wait) + 1}s"
                )
            return self._start(record, should_stop)

    def _start(self, record: PluginRecord, should_stop: StopCheck) -> PluginProcess:
        manifest = record.manifest
        assert manifest is not None and record.path is not None
        # The exit handler has to know *which* process ended: by the time a
        # dead one's reader thread gets round to reporting it, the record may
        # already hold its replacement.
        holder: Dict[str, PluginProcess] = {}
        process = PluginProcess(
            record.id,
            self._command_for(record.path),
            cwd=str(record.path),
            on_exit=lambda code, rid=record.id: self._on_exit(rid, holder.get("process"), code),
            guard_main_thread=self._guard_main_thread,
        )
        holder["process"] = process
        # A death during `initialize` is a startup crash, and `_startup_failed`
        # counts it; the exit handler must not count it a second time.
        process._initializing = True  # type: ignore[attr-defined]
        try:
            process.start()
        except OSError as exc:
            self._startup_failed(record, f"could not be started: {exc}")
            raise PluginCrashed(f"{record.name} could not be started: {exc}") from exc
        record.process = process

        params = {
            "api": PLUGIN_API_VERSION,
            "hostVersion": self._host_version,
            "dataDir": str(paths.plugin_data_dir(record.id)),
            "programs": {p.name: programs.path_of(p.name) for p in manifest.programs},
            "settings": self.settings(record.id),
            "consented": self.has_consent(record.id),
        }
        try:
            process.call("initialize", params, timeout=INITIALIZE_TIMEOUT, should_stop=should_stop)
        except PluginTimeout as exc:
            process.kill()
            record.process = None
            self._startup_failed(record, "did not answer initialize in time")
            raise PluginCrashed(f"{record.name} did not start in time") from exc
        except PluginCrashed as exc:
            process.kill()
            record.process = None
            self._startup_failed(record, str(exc))
            raise
        except PluginError as exc:
            process.kill()
            record.process = None
            self._startup_failed(record, f"refused to start: {exc}")
            raise PluginCrashed(f"{record.name} refused to start: {exc}") from exc

        process._initializing = False  # type: ignore[attr-defined]
        record.startup_crashes = 0
        logger.info("Started plugin %s (pid %s)", record.id, process.pid)
        self._changed()
        return process

    def _startup_failed(self, record: PluginRecord, reason: str) -> None:
        record.startup_crashes += 1
        record.crashes += 1
        record.last_crash = reason
        record.backoff_until = time.monotonic() + self._backoff(record.crashes)
        logger.warning("Plugin %s failed to start (%d in a row): %s",
                       record.id, record.startup_crashes, reason)
        if record.startup_crashes >= STARTUP_CRASH_LIMIT:
            message = f"switched off after failing to start {STARTUP_CRASH_LIMIT} times: {reason}"
            self._store.plugin_set(record.id, KEY_ENABLED, False)
            self._store.plugin_set(record.id, KEY_DISABLED_REASON, message)
            record.startup_crashes = 0
        self.plugin_crashed.emit(record.id, reason)
        self._changed()

    @staticmethod
    def _backoff(crashes: int) -> float:
        """No wait after the first crash, then doubling from two seconds."""
        if crashes <= 1:
            return 0.0
        return min(MAX_BACKOFF_SECONDS, 2.0 ** (crashes - 1))

    def _on_exit(
        self, plugin_id: str, process: Optional[PluginProcess], code: Optional[int]
    ) -> None:
        """A process ended — reader thread. Expected after stop; a crash otherwise."""
        record = self.record(plugin_id)
        if record is None or process is None or record.process is not process:
            return
        if getattr(process, "_stopping", False) or getattr(process, "_initializing", False):
            return
        record.crashes += 1
        record.last_crash = f"exited with code {code}" if code is not None else "exited"
        record.backoff_until = time.monotonic() + self._backoff(record.crashes)
        logger.warning("Plugin %s stopped unexpectedly (%s)", plugin_id, record.last_crash)
        self.plugin_crashed.emit(plugin_id, record.last_crash)

    def stop(self, plugin_id: str) -> None:
        """Stop *plugin_id*'s process, if it has one. Pending calls fail."""
        record = self.record(plugin_id)
        if record is None or record.process is None:
            return
        process = record.process
        process._stopping = True  # type: ignore[attr-defined]
        record.process = None
        process.shutdown()
        self._changed()

    def stop_idle(self) -> None:
        """Stop every process that has had nothing to do for the idle period."""
        now = time.monotonic()
        busy = {job.plugin_id for job in list(self._jobs.values())}
        for record in self.records():
            process = record.process
            if process is None or not process.running:
                continue
            if record.id in busy or process.pending_count:
                continue
            if now - process.last_activity >= self._idle_seconds:
                logger.info("Stopping idle plugin %s", record.id)
                self.stop(record.id)

    def running(self, plugin_id: str) -> bool:
        record = self.record(plugin_id)
        return bool(record and record.process and record.process.running)

    def shutdown(self) -> None:
        """Stop every plugin. Called before the window goes and before an update.

        Pending calls fail first — inside each process's own shutdown — so a
        worker blocked on a plugin wakes now, and its thread can be joined
        before the application tears down the objects it reports to.
        """
        self._shut_down = True
        self._active_cache = None
        self._idle_timer.stop()
        for record in self.records():
            if record.process is not None:
                self.stop(record.id)

    # ==================================================================
    # Jobs
    # ==================================================================

    def _open_job(self, plugin_id: str, label: str) -> int:
        with self._lock:
            self._job_tokens += 1
            token = self._job_tokens
            self._jobs[token] = Job(token, plugin_id, label)
        self.jobs_changed.emit()
        return token

    def _update_job(self, token: int, params: Mapping[str, Any]) -> None:
        job = self._jobs.get(token)
        if job is None:
            return
        job.stage = str(params.get("stage") or job.stage)
        try:
            job.percent = float(params["percent"]) if params.get("percent") is not None else job.percent
        except (TypeError, ValueError):
            pass
        self.jobs_changed.emit()

    def _close_job(self, token: int) -> None:
        with self._lock:
            self._jobs.pop(token, None)
        self.jobs_changed.emit()

    def jobs(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.token)
