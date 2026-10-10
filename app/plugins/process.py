"""One running plugin, and the JSON-RPC 2.0 client over its pipes.

The protocol is one JSON message per line: requests and notifications go down
the plugin's stdin, responses and notifications come back up its stdout, and
stderr is a log — drained into :mod:`logging`, never parsed. That last rule is
ADR 0003's for the separation sidecar, and it is what stops a library's chatter
from becoming part of the protocol.

**Three threads per process**, and none of them is the caller's:

- a *writer*, fed from a queue, so a caller never blocks on a full pipe and two
  callers never interleave half-lines;
- a *reader*, which resolves each pending call's future by request id —
  replies can arrive in any order, because a plugin answers several requests at
  once — and routes notifications;
- a *stderr drain*, which exists because an undrained stderr pipe fills and
  then blocks the plugin mid-write, which looks exactly like a hang.

**Calls block, so they are refused on the main thread.** A plugin is a network
round trip at best and a thirty-second download at worst; the UI thread waiting
on either is a frozen window. :meth:`PluginProcess.call` raises instead, which
turns the mistake into a test failure rather than a stutter in the field.

**Cancelling kills the whole tree.** A plugin runs programs of its own — a
downloader, a JavaScript runtime — and stopping only the plugin would leave
those running with nobody to read their output. On POSIX the plugin starts in a
session of its own and the session is killed; on Windows it is put in a Job
Object that kills everything in it when its handle closes.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.utils.proc import no_window

logger = logging.getLogger(__name__)

#: How often a waiting call wakes to ask whether it should stop.
POLL_SECONDS = 0.1

#: JSON-RPC error codes the protocol gives meaning to. The rest of the range is
#: JSON-RPC's own; anything else is the plugin's, reported as it was sent.
ERROR_CANCELLED = -32800
ERROR_PROGRAM_MISSING = -32001
ERROR_CONSENT_REQUIRED = -32002

StopCheck = Optional[Callable[[], bool]]
ProgressCallback = Optional[Callable[[Mapping[str, Any]], None]]


class PluginError(RuntimeError):
    """A call did not produce a result. ``code`` and ``data`` are the plugin's."""

    def __init__(self, message: str, code: int = 0, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class PluginCancelled(PluginError):
    """The caller asked to stop, or the plugin reported the request cancelled."""


class PluginTimeout(PluginError):
    """No answer arrived in time. The request has been cancelled."""


class PluginCrashed(PluginError):
    """The process went away with this request still outstanding."""


class ProgramMissing(PluginError):
    """The plugin needs a program this machine does not have."""

    @property
    def program(self) -> str:
        return str((self.data or {}).get("program") or "") if isinstance(self.data, Mapping) else ""


class MainThreadCall(RuntimeError):
    """A blocking plugin call was made from the main thread."""


def error_from(payload: Mapping[str, Any]) -> PluginError:
    """The exception a JSON-RPC error object stands for."""
    code = int(payload.get("code") or 0)
    message = str(payload.get("message") or "The plugin reported an error")
    data = payload.get("data")
    if code == ERROR_CANCELLED:
        return PluginCancelled(message, code, data)
    if code == ERROR_PROGRAM_MISSING:
        error = ProgramMissing(message, code, data)
        if error.program:
            return ProgramMissing(f"{error.program} is not installed", code, data)
        return error
    return PluginError(message, code, data)


class _Pending:
    """One outstanding request: its future, and where its progress goes."""

    __slots__ = ("future", "on_progress", "method")

    def __init__(self, method: str, on_progress: ProgressCallback) -> None:
        self.future: Future = Future()
        self.on_progress = on_progress
        self.method = method


class _WindowsJob:
    """A Job Object that kills every process in it when its handle closes.

    Held for the life of the plugin. When Kroma exits — cleanly or not — the
    handle closes and Windows takes the plugin and everything it started down
    with it, which no amount of careful shutdown code in a crashing process can
    promise.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObjectW failed")

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        )

    def assign(self, process: subprocess.Popen) -> None:
        handle = int(getattr(process, "_handle", 0))
        if handle:
            self._kernel32.AssignProcessToJobObject(self.handle, handle)

    def close(self) -> None:
        if self.handle:
            self._kernel32.TerminateJobObject(self.handle, 1)
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class PluginProcess:
    """The client end of one plugin process.

    Created stopped; :meth:`start` spawns it. Not restartable — the host makes
    a new one after a crash, so nothing about the dead process (its pending
    calls, its counters) can leak into the next.

    *on_notification* is called on the reader thread for every notification
    that is not a request's progress. *on_exit* is called once, on the reader
    thread, when stdout closes — with the exit code if it is known by then.
    """

    def __init__(
        self,
        plugin_id: str,
        command: List[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        on_notification: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
        on_exit: Optional[Callable[[Optional[int]], None]] = None,
        guard_main_thread: bool = True,
    ) -> None:
        self.plugin_id = plugin_id
        self._command = list(command)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._guard_main_thread = guard_main_thread

        self._process: Optional[subprocess.Popen] = None
        self._job: Optional[_WindowsJob] = None
        self._ids = itertools.count(1)
        self._pending: Dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._outbox: "queue.Queue[Optional[str]]" = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._exited = threading.Event()
        self._exit_code: Optional[int] = None
        self._log = logging.getLogger(f"plugin.{plugin_id}")
        #: When a request was last sent or answered. What the idle stop reads.
        self.last_activity = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the process and its three threads. Raises ``OSError`` if it cannot."""
        env = dict(self._env if self._env is not None else os.environ)
        # The two ends are different interpreters, and on Windows a pipe
        # defaults to the locale code page. Left alone, the first em dash in a
        # title arrives as mojibake — see `separation.runner._utf8_pipes`.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        kwargs: Dict[str, Any] = dict(no_window())
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True

        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **kwargs,
        )
        if sys.platform == "win32":
            try:
                self._job = _WindowsJob()
                self._job.assign(self._process)
            except Exception:  # noqa: BLE001 — a missing job only weakens cleanup
                logger.warning("Could not put %s in a job object", self.plugin_id, exc_info=True)
                self._job = None

        for name, target in (
            ("writer", self._write_loop),
            ("reader", self._read_loop),
            ("stderr", self._drain_stderr),
        ):
            thread = threading.Thread(
                target=target, name=f"plugin-{self.plugin_id}-{name}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        self.last_activity = time.monotonic()

    @property
    def running(self) -> bool:
        return self._process is not None and not self._exited.is_set()

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process is not None else None

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def wait_exit(self, timeout: Optional[float] = None) -> bool:
        """Whether the process has exited, waiting up to *timeout* seconds."""
        return self._exited.wait(timeout)

    def shutdown(self, grace: float = 1.0) -> None:
        """Ask nicely, wait *grace* seconds, then kill the tree.

        Every pending call fails first, so a worker blocked in :meth:`call`
        wakes now rather than after the grace period — which is the difference
        between a window that closes and one that hangs on its way out.
        """
        self._fail_pending(PluginCrashed("Kroma is stopping this plugin"))
        if self.running:
            try:
                # A request, as the protocol lists it, but nobody waits for
                # the answer: what is being waited for is the exit below.
                self._send({"jsonrpc": "2.0", "id": next(self._ids), "method": "shutdown"})
            except Exception:  # noqa: BLE001
                pass
            # Closing stdin is the other half of the request: the SDK exits on
            # EOF, and a plugin that ignores `shutdown` cannot ignore that.
            self._outbox.put(None)
            self._exited.wait(grace)
        self.kill()

    def kill(self) -> None:
        """Stop the process and everything it started, now."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                if sys.platform == "win32":
                    if self._job is not None:
                        self._job.close()
                        self._job = None
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Plugin %s did not exit after being killed", self.plugin_id)
        if self._job is not None:
            self._job.close()
            self._job = None
        self._outbox.put(None)
        self._fail_pending(PluginCrashed("The plugin stopped"))

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def notify(self, method: str, params: Optional[Mapping[str, Any]] = None) -> None:
        """Send a notification. Nothing comes back, and nothing waits."""
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def call(
        self,
        method: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        should_stop: StopCheck = None,
        on_progress: ProgressCallback = None,
    ) -> Any:
        """Send a request and wait for its result.

        Raises :class:`PluginCancelled` when *should_stop* says so — after
        sending ``cancel`` for this request, so the plugin can stop the work
        rather than finish it for nobody — :class:`PluginTimeout` after
        *timeout* seconds, :class:`PluginCrashed` if the process dies first,
        and the plugin's own error otherwise.
        """
        if self._guard_main_thread and threading.current_thread() is threading.main_thread():
            raise MainThreadCall(
                f"{method} on {self.plugin_id} was called from the main thread"
            )
        if not self.running:
            raise PluginCrashed(f"{self.plugin_id} is not running")

        request_id = next(self._ids)
        pending = _Pending(method, on_progress)
        with self._lock:
            self._pending[request_id] = pending
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        self.last_activity = time.monotonic()
        self._send(message)

        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                if should_stop is not None and should_stop():
                    self._cancel(request_id)
                    raise PluginCancelled("Cancelled", ERROR_CANCELLED)
                try:
                    return pending.future.result(POLL_SECONDS)
                except FutureTimeout:
                    pass
                if deadline is not None and time.monotonic() >= deadline:
                    self._cancel(request_id)
                    raise PluginTimeout(
                        f"{self.plugin_id} did not answer {method} within {timeout:g}s"
                    )
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            self.last_activity = time.monotonic()

    def _cancel(self, request_id: int) -> None:
        try:
            self.notify("cancel", {"id": request_id})
        except Exception:  # noqa: BLE001 — a dead plugin has nothing to cancel
            pass

    def _send(self, message: Mapping[str, Any]) -> None:
        self._outbox.put(json.dumps(message, ensure_ascii=False, separators=(",", ":")))

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def _write_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdin is not None
        while True:
            line = self._outbox.get()
            if line is None:
                break
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                break
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass

    def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    # The SDK keeps stdout clean, but a plugin is not obliged to
                    # use the SDK. One bad line is logged and skipped; it must
                    # not take every other request down with it.
                    self._log.warning("Not a protocol message: %s", line[:200])
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._exit_code = None
            self._exited.set()
            self._fail_pending(PluginCrashed(
                f"{self.plugin_id} stopped unexpectedly"
                + (f" (exit code {self._exit_code})" if self._exit_code not in (None, 0) else "")
            ))
            self._outbox.put(None)
            if self._on_exit is not None:
                try:
                    self._on_exit(self._exit_code)
                except Exception:  # noqa: BLE001
                    logger.exception("Plugin exit handler failed")

    def _dispatch(self, message: Dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            try:
                request_id = int(message["id"])
            except (TypeError, ValueError):
                return
            with self._lock:
                pending = self._pending.get(request_id)
            if pending is None or pending.future.done():
                return
            self.last_activity = time.monotonic()
            if "error" in message and isinstance(message["error"], Mapping):
                pending.future.set_exception(error_from(message["error"]))
            else:
                pending.future.set_result(message.get("result"))
            return

        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return
        if method == "log":
            level = str(params.get("level") or "info").upper()
            self._log.log(getattr(logging, level, logging.INFO), "%s", params.get("message", ""))
            return
        if method == "progress":
            try:
                request_id = int(params.get("id"))
            except (TypeError, ValueError):
                request_id = -1
            with self._lock:
                pending = self._pending.get(request_id)
            self.last_activity = time.monotonic()
            if pending is not None and pending.on_progress is not None:
                try:
                    pending.on_progress(params)
                except Exception:  # noqa: BLE001
                    logger.exception("Progress handler failed")
        if self._on_notification is not None:
            try:
                self._on_notification(method, params)
            except Exception:  # noqa: BLE001
                logger.exception("Notification handler failed")

    def _drain_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            for line in process.stderr:
                line = line.rstrip()
                if line:
                    self._log.info("%s", line)
        except (OSError, ValueError):
            pass

    def _fail_pending(self, error: PluginError) -> None:
        with self._lock:
            pending = list(self._pending.values())
        for entry in pending:
            if not entry.future.done():
                try:
                    entry.future.set_exception(error)
                except Exception:  # noqa: BLE001 — raced a result; that one wins
                    pass
