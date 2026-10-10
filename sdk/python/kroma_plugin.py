"""The Kroma plugin SDK for Python.

A plugin is a program that reads JSON-RPC 2.0 requests from stdin, one per line,
and writes responses to stdout. This module is that loop, so a plugin is only
its handlers::

    from kroma_plugin import Plugin

    plugin = Plugin()

    @plugin.handler("trackSource.rows")
    def rows(request):
        request.progress("searching")
        return {"rows": [...]}

    plugin.run()

What it takes care of, so a plugin cannot get it wrong:

- **A thread per request.** A thirty-second download must not hold up an
  identify or an artwork call that arrived after it, and the host sends them
  concurrently.
- **Cancellation.** The host sends ``cancel {id}``; the request's
  :attr:`Request.cancelled` event is set, and :meth:`Request.check` raises
  :class:`Cancelled`, which is answered with the protocol's cancelled code.
- **A clean stdout.** The protocol writes to a private copy of file
  descriptor 1, and descriptor 1 itself — with ``sys.stdout`` — is pointed at
  stderr. A library that prints, or a child process that inherits stdout, lands
  in the log instead of corrupting the protocol.
- **Exiting.** On ``shutdown``, or when stdin reaches EOF because the host has
  gone, the process exits. A plugin never outlives Kroma.
- **Consent.** ``audio.get`` is refused unless ``initialize`` said the user
  consented — the third of three gates; the host checks twice before sending.

Imports nothing outside the standard library, and nothing from Kroma: it is
frozen into the plugin host executable and must work there.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = [
    "Plugin", "Request", "Cancelled", "PluginError", "ProgramMissing",
    "ERROR_CANCELLED", "ERROR_PROGRAM_MISSING", "ERROR_CONSENT_REQUIRED",
]

ERROR_CANCELLED = -32800
ERROR_PROGRAM_MISSING = -32001
ERROR_CONSENT_REQUIRED = -32002
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_REQUEST = -32600
ERROR_INTERNAL = -32000

#: Methods that obtain a recording, and so need the user's consent.
CONSENT_METHODS = frozenset({"audio.get"})


class PluginError(Exception):
    """An error with a protocol code, raised from a handler."""

    code = ERROR_INTERNAL

    def __init__(self, message: str, code: Optional[int] = None, data: Any = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.data = data


class Cancelled(PluginError):
    """The host cancelled this request. Raise it (or let :meth:`Request.check`) to stop."""

    code = ERROR_CANCELLED

    def __init__(self, message: str = "Cancelled") -> None:
        super().__init__(message)


class ProgramMissing(PluginError):
    """A program this plugin needs is not installed. The host names it to the user."""

    code = ERROR_PROGRAM_MISSING

    def __init__(self, program: str) -> None:
        super().__init__(f"{program} is not installed", data={"program": program})
        self.program = program


class Request:
    """One request in flight: its parameters, and how to report on it."""

    def __init__(self, plugin: "Plugin", request_id: Any, method: str, params: Mapping[str, Any]) -> None:
        self._plugin = plugin
        self.id = request_id
        self.method = method
        self.params: Mapping[str, Any] = params
        self.cancelled = threading.Event()

    @property
    def stopped(self) -> bool:
        """Whether the host has cancelled this request."""
        return self.cancelled.is_set()

    def stopped_check(self) -> bool:
        """:attr:`stopped` as a callable, for code that polls a ``should_stop``."""
        return self.cancelled.is_set()

    def check(self) -> None:
        """Raise :class:`Cancelled` if the host has cancelled this request."""
        if self.cancelled.is_set():
            raise Cancelled()

    def progress(self, stage: str = "", percent: Optional[float] = None) -> None:
        """Tell the host how far this request has got. Never raises."""
        params: Dict[str, Any] = {"id": self.id, "stage": stage}
        if percent is not None:
            params["percent"] = max(0.0, min(100.0, float(percent)))
        self._plugin.notify("progress", params)


Handler = Callable[[Request], Any]


class Plugin:
    """The plugin's end of the protocol."""

    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}
        self._requests: Dict[Any, Request] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._out = None
        self._settings_hooks: list = []
        self._init_hooks: list = []
        #: What ``initialize`` said. Empty until it has arrived.
        self.api: int = 0
        self.host_version: str = ""
        self.data_dir: str = ""
        self.programs: Dict[str, Optional[str]] = {}
        self.settings: Dict[str, Any] = {}
        self.consented: bool = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def handler(self, method: str) -> Callable[[Handler], Handler]:
        """Decorator: answer *method* with the decorated function."""
        def register(fn: Handler) -> Handler:
            self._handlers[method] = fn
            return fn
        return register

    def on_initialize(self, fn: Callable[["Plugin"], None]) -> Callable[["Plugin"], None]:
        """Decorator: called once ``initialize`` has filled in the fields above."""
        self._init_hooks.append(fn)
        return fn

    def on_settings_changed(self, fn: Callable[[Dict[str, Any]], None]) -> Callable[[Dict[str, Any]], None]:
        """Decorator: called with the new settings when the user changes them."""
        self._settings_hooks.append(fn)
        return fn

    def program(self, name: str) -> str:
        """The path the host found for *name*. Raises :class:`ProgramMissing` if none."""
        path = self.programs.get(name)
        if not path:
            raise ProgramMissing(name)
        return path

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _send(self, message: Mapping[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            out = self._out
            if out is None:
                return
            try:
                out.write(line + "\n")
                out.flush()
            except (OSError, ValueError):
                pass

    def notify(self, method: str, params: Optional[Mapping[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def log(self, message: str, level: str = "info") -> None:
        """Send a line to Kroma's log, attributed to this plugin."""
        self.notify("log", {"level": level, "message": str(message)})

    def _reply(self, request_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str, data: Any = None) -> None:
        error: Dict[str, Any] = {"code": int(code), "message": str(message)}
        if data is not None:
            error["data"] = data
        self._send({"jsonrpc": "2.0", "id": request_id, "error": error})

    # ------------------------------------------------------------------
    # Built-in methods
    # ------------------------------------------------------------------

    def _initialize(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        self.api = int(params.get("api") or 0)
        self.host_version = str(params.get("hostVersion") or "")
        self.data_dir = str(params.get("dataDir") or "")
        self.programs = dict(params.get("programs") or {})
        self.settings = dict(params.get("settings") or {})
        self.consented = bool(params.get("consented"))
        for hook in self._init_hooks:
            hook(self)
        return {"methods": sorted(self._handlers)}

    def _settings_changed(self, params: Mapping[str, Any]) -> None:
        self.settings = dict(params.get("settings") or {})
        if "consented" in params:
            self.consented = bool(params.get("consented"))
        for hook in self._settings_hooks:
            try:
                hook(dict(self.settings))
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def _take_stdout(self) -> None:
        """Keep descriptor 1 for the protocol and point everything else at stderr."""
        sys.stdout.flush()
        private = os.dup(1)
        os.dup2(2, 1)
        self._out = os.fdopen(private, "w", encoding="utf-8", newline="\n", buffering=1)
        sys.stdout = sys.stderr

    def run(self) -> None:
        """Serve requests until ``shutdown`` or EOF. Does not return."""
        self._take_stdout()
        stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", errors="replace",
                     newline="\n", closefd=False)
        for raw in stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                sys.stderr.write(f"kroma_plugin: unreadable message: {raw[:200]}\n")
                continue
            if not isinstance(message, dict):
                continue
            if self._handle(message):
                break
        self._exit()

    def _exit(self) -> None:
        with self._lock:
            for request in self._requests.values():
                request.cancelled.set()
        try:
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    def _handle(self, message: Mapping[str, Any]) -> bool:
        """Dispatch one message. True means stop serving."""
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")
        is_request = "id" in message
        if not isinstance(method, str) or not isinstance(params, Mapping):
            if is_request:
                self._error(request_id, ERROR_INVALID_REQUEST, "Invalid request")
            return False

        if method == "cancel":
            with self._lock:
                request = self._requests.get(params.get("id"))
            if request is not None:
                request.cancelled.set()
            return False
        if method == "shutdown":
            if is_request:
                self._reply(request_id, None)
            return True
        if method == "settingsChanged":
            self._settings_changed(params)
            if is_request:
                self._reply(request_id, None)
            return False
        if not is_request:
            return False
        if method == "initialize":
            try:
                self._reply(request_id, self._initialize(params))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._error(request_id, ERROR_INTERNAL, str(exc) or type(exc).__name__)
            return False
        if method == "ping":
            self._reply(request_id, {"pong": True})
            return False

        handler = self._handlers.get(method)
        if handler is None:
            self._error(request_id, ERROR_METHOD_NOT_FOUND, f"Method not found: {method}")
            return False
        if method in CONSENT_METHODS and not self.consented:
            self._error(request_id, ERROR_CONSENT_REQUIRED,
                        "The user has not agreed to this plugin's terms")
            return False

        request = Request(self, request_id, method, params)
        with self._lock:
            self._requests[request_id] = request
        thread = threading.Thread(
            target=self._serve, args=(handler, request),
            name=f"request-{request_id}", daemon=True,
        )
        thread.start()
        return False

    def _serve(self, handler: Handler, request: Request) -> None:
        try:
            result = handler(request)
            if request.cancelled.is_set():
                self._error(request.id, ERROR_CANCELLED, "Cancelled")
            else:
                self._reply(request.id, result)
        except Cancelled as exc:
            self._error(request.id, ERROR_CANCELLED, str(exc) or "Cancelled")
        except PluginError as exc:
            self._error(request.id, exc.code, str(exc), exc.data)
        except Exception as exc:  # noqa: BLE001 — every failure becomes an answer
            traceback.print_exc()
            self._error(request.id, ERROR_INTERNAL, str(exc) or type(exc).__name__)
        finally:
            with self._lock:
                self._requests.pop(request.id, None)
