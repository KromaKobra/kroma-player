"""Spawning the sidecar and turning its output into progress.

One class, deliberately free of Qt so it can be tested without an event loop.
The worker that drives it lives in :mod:`app.separation.workers`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.separation import encode
from app.services.backing_track_service import (
    DURATION_TOLERANCE_SECONDS,
    probe_duration,
)
from app.utils.proc import no_window as _no_window

logger = logging.getLogger(__name__)

SIDECAR = Path(__file__).parent / "sidecar" / "separate.py"

# How long to wait for `--probe` before giving up. Importing torch is slow, and
# slow on a cold filesystem cache in a way that has nothing to do with anything
# being wrong, so this is generous.
PROBE_TIMEOUT_SECONDS = 120

# How many of the sidecar's last stderr lines to keep. Model libraries are
# chatty and a separation runs for minutes; only the end of it is ever useful,
# and that is where a traceback would be.
STDERR_LINES_KEPT = 200


def _utf8_pipes() -> dict:
    """Keyword arguments that make the pipe UTF-8 at both ends.

    The two sides of this are different Python installations, and on Windows a
    pipe defaults to the locale code page rather than to UTF-8. Left alone, the
    parent decodes what the child encoded under a different assumption, and the
    first non-ASCII character in an error message — an em dash in one of the
    sidecar's own sentences is enough — turns a real diagnosis into mojibake or
    a UnicodeDecodeError on top of the failure being reported.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return {"env": env, "encoding": "utf-8", "errors": "replace"}


class SeparationError(RuntimeError):
    """The sidecar could not produce an instrumental."""


class SeparationCancelled(RuntimeError):
    """The job was cancelled before it finished."""


@dataclass(frozen=True)
class Probe:
    """What an interpreter said about itself.

    ``error`` and ``backend_error`` are separate because they fail differently:
    the first means the question could not be asked at all, the second that it
    was answered and the answer was "nothing here can run a model". One is a
    broken interpreter, the other a missing install.
    """

    devices: list[str]
    error: str = ""
    backend: str = ""
    backend_error: str = ""


def probe_devices(python_path: str, msst_path: str = "") -> Probe:
    """Ask an interpreter what it can run, and what it can run it with.

    An empty device list always comes with an error explaining why, because "no
    devices" and "could not ask" look the same from the UI and mean very
    different things.
    """
    if not python_path:
        return Probe([], "No Python interpreter selected")
    if not Path(python_path).is_file():
        return Probe([], f"No such interpreter: {python_path}")

    command = [python_path, str(SIDECAR), "--probe"]
    if msst_path:
        command += ["--msst-path", msst_path]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            **_utf8_pipes(),
            **_no_window(),
        )
    except subprocess.TimeoutExpired:
        return Probe([], "Timed out asking the interpreter about its devices")
    except OSError as exc:
        return Probe([], f"Could not run the interpreter: {exc}")

    devices: list[str] = []
    error = ""
    backend = ""
    backend_error = ""
    for line in (proc.stdout or "").splitlines():
        tag, _, value = line.partition(" ")
        if tag == "KROMA_DEVICES":
            devices = [d for d in value.strip().split(",") if d]
        elif tag == "KROMA_ERROR":
            error = value.strip()
        elif tag == "KROMA_BACKEND":
            backend = value.strip()
        elif tag == "KROMA_BACKEND_ERROR":
            backend_error = value.strip()

    if not devices and not error:
        error = (proc.stderr or "").strip()[-300:] or "The interpreter said nothing"
    return Probe(devices, error, backend, backend_error)


class SeparationRunner:
    """Runs one separation at a time in a child interpreter."""

    def __init__(self, python_path: str, model_path: str, device: str = "auto",
                 config_path: str = "", msst_path: str = "") -> None:
        self._python = python_path
        self._model = model_path
        # Passed explicitly rather than inferred: a config is not required to
        # share the checkpoint's name, so the far side cannot work it out.
        self._config = config_path
        # Optional: where the user's Music-Source-Separation-Training checkout
        # is, for interpreters that run the model from source rather than from
        # the `audio-separator` package. Empty leaves the sidecar to find one.
        self._msst = msst_path
        self._device = device
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
        # ``cancel`` runs on the UI thread while ``_run_sidecar`` is reading the
        # child's stdout on a worker thread. Both touch ``_process``, and
        # without this the cancel could land on a handle the worker is in the
        # middle of reaping — terminating an already-dead process, or a freed
        # one. The lock is only ever held for the duration of a terminate().
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Ask the running job to stop.

        Safe to call from another thread — that is the point of it; the UI's
        cancel button cannot wait for a GPU pass to end before being handled.
        """
        self._cancelled = True
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                if process.poll() is None:
                    process.terminate()
            except OSError:
                # Already gone between the poll and the terminate. Nothing to do
                # — _cancelled is set, so the worker will raise on its own.
                logger.debug("Sidecar had already exited when cancelled")

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------

    def generate(
        self,
        mix_path: Path,
        destination_dir: Path,
        on_stage: Callable[[str], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> Path:
        """Produce an Opus instrumental of *mix_path*, exactly as long as it.

        Returns the path to the finished file inside *destination_dir*. Raises
        :class:`SeparationError` on failure and :class:`SeparationCancelled` if
        :meth:`cancel` was called.
        """
        stage = on_stage or (lambda _: None)
        progress = on_progress or (lambda _: None)

        target_length = probe_duration(mix_path)
        if target_length is None:
            raise SeparationError(f"Could not read the length of {mix_path.name}")

        with tempfile.TemporaryDirectory(prefix="kroma-bt-") as tmp:
            work = Path(tmp)

            stage("Decoding")
            progress(1)
            wav_in = encode.to_wav(mix_path, work / "input.wav")
            self._raise_if_cancelled()

            stripped = self._run_sidecar(wav_in, work / "out", stage, progress)
            self._raise_if_cancelled()

            stage("Encoding")
            progress(96)
            # Named for the mix so a leftover temp file is identifiable; the
            # caller renames it into the vault.
            opus = encode.to_opus_exact(
                stripped, destination_dir / f"{mix_path.stem}.opus", target_length
            )

            written = probe_duration(opus)
            if written is None or abs(written - target_length) > DURATION_TOLERANCE_SECONDS:
                raise SeparationError(
                    "The generated instrumental came out the wrong length "
                    f"({written}s against {target_length}s) — not installing it"
                )

            progress(100)
            return opus

    # ------------------------------------------------------------------

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise SeparationCancelled("Cancelled")

    @staticmethod
    def _drain(stream, tail: deque) -> None:
        """Keep the child's stderr pipe empty, remembering only the last of it."""
        if stream is None:
            return
        try:
            for line in stream:
                tail.append(line)
        except (OSError, ValueError):
            # The pipe was closed under us — the process is being terminated,
            # which is a cancel, not a failure worth reporting.
            logger.debug("Sidecar stderr closed while draining")

    def _run_sidecar(
        self,
        wav_in: Path,
        out_dir: Path,
        stage: Callable[[str], None],
        progress: Callable[[int], None],
    ) -> Path:
        """Drive the child process, translating its protocol into callbacks."""
        if not self._python:
            raise SeparationError(
                "No Python interpreter is configured for generation — set one in "
                "Settings"
            )
        if not self._model:
            raise SeparationError("No model is selected")

        out_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self._python, str(SIDECAR),
            "--model", self._model,
            "--input", str(wav_in),
            "--output-dir", str(out_dir),
            "--device", self._device,
        ]
        if self._config:
            command += ["--config", self._config]
        if self._msst:
            command += ["--msst-path", self._msst]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                **_utf8_pipes(),
                **_no_window(),
            )
        except OSError as exc:
            raise SeparationError(f"Could not start the interpreter: {exc}") from exc

        # Published only once it is fully constructed, so a cancel arriving
        # mid-spawn cannot see a half-built Popen.
        with self._lock:
            self._process = process
            if self._cancelled:
                # Cancelled while we were starting up. Kill it here rather than
                # reading a stream nobody is waiting for.
                process.terminate()

        # stderr has to be drained while stdout is being read, not after it.
        # The model libraries write progress bars and warnings there by the
        # tens of kilobytes, and a pipe nobody is reading fills up and blocks
        # the child — mid-separation, with the parent waiting on a stdout line
        # that will never come.
        tail: deque[str] = deque(maxlen=STDERR_LINES_KEPT)
        drain = threading.Thread(
            target=self._drain, args=(process.stderr, tail), daemon=True
        )
        drain.start()

        output: Optional[str] = None
        error: Optional[str] = None

        assert process.stdout is not None
        for line in process.stdout:
            tag, _, value = line.strip().partition(" ")
            value = value.strip()
            if tag == "KROMA_STAGE":
                stage(value)
            elif tag == "KROMA_PROGRESS":
                try:
                    # The sidecar's 0-100 is squeezed into the band between
                    # decoding and encoding, which this side owns.
                    progress(max(1, min(95, int(value))))
                except ValueError:
                    pass
            elif tag == "KROMA_OUTPUT":
                output = value
            elif tag == "KROMA_ERROR":
                error = value
            elif line.strip():
                logger.debug("sidecar: %s", line.strip())

        drain.join(timeout=5)
        stderr = "".join(tail)
        code = process.wait()
        # Reaped: no further cancel may touch it.
        with self._lock:
            self._process = None

        if self._cancelled:
            raise SeparationCancelled("Cancelled")

        if error:
            raise SeparationError(error)
        if code != 0:
            tail = stderr.strip()[-400:] or f"exit code {code}"
            raise SeparationError(f"Separation failed: {tail}")
        if not output or not Path(output).exists():
            raise SeparationError("The sidecar reported success but wrote nothing")

        if stderr.strip():
            logger.debug("sidecar stderr: %s", stderr.strip()[-2000:])

        return Path(output)
