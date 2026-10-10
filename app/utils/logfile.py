"""Where the application's log goes, on a machine where nobody is watching stderr.

From a terminal the log is stderr, and that is all Linux needs: a desktop entry
or an AppImage launched from a file manager loses it, but anyone who needs it
can start the app from a shell. Windows is different in kind. The release is a
*windowed* executable, so ``sys.stderr`` is ``None`` — every record, every
traceback and every "migration failed" went nowhere at all, and a frozen build
that died at startup did so without a trace.

So on Windows the log is also a file:

    %LOCALAPPDATA%\\Kroma\\logs\\kroma-player.log      (1 MB, three old copies kept)

Local rather than roaming AppData, because a log describes this machine and has
no business following the user to another one. ``Kroma`` for the reason
:func:`app.config.kord_config.kord_dir` gives — it is the application's name to
Qt, and the config already lives under it.

``faulthandler`` writes into the same file. A crash inside Qt or a C extension
never reaches Python's exception machinery; this is the only way it leaves a
stack behind.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR_NAME = "logs"
LOG_FILENAME = "kroma-player.log"

# Small, because the useful part of a log is the last launch or two, and a user
# attaching it to a bug report should not be attaching a novel.
MAX_BYTES = 1 << 20
BACKUP_COUNT = 3

STDERR_FORMAT = "%(levelname)s %(name)s: %(message)s"
# The file carries a timestamp the terminal does not need: a log read the next
# day has no other way to say which launch a line came from.
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_log_file: Path | None = None
_configured = False


class _FaultLogHandler(RotatingFileHandler):
    """A rotating file that ``faulthandler`` writes into as well.

    ``faulthandler`` holds a file descriptor, not a path, so rotation would
    leave it pointed at a closed one. Re-arming it on the new stream after each
    rollover keeps a native crash landing in the current file.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._arm()

    def _arm(self) -> None:
        if self.stream is None:
            return
        try:
            faulthandler.enable(self.stream)
        except (OSError, RuntimeError, ValueError):
            pass

    def doRollover(self) -> None:  # noqa: N802 — logging's own name
        super().doRollover()
        self._arm()

    def close(self) -> None:
        # Before the stream goes: faulthandler writing to a closed descriptor
        # during interpreter shutdown is worse than not writing at all.
        try:
            faulthandler.disable()
        except RuntimeError:
            pass
        super().close()


def log_dir() -> Path | None:
    """The folder the log file goes in, or ``None`` where there is none.

    Windows only. Elsewhere stderr is the log, and this returns ``None``.
    """
    if sys.platform != "win32":
        return None
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        return None
    from app.config.kord_config import CONFIG_DIR_NAME

    return Path(base) / CONFIG_DIR_NAME / LOG_DIR_NAME


def current_log_file() -> Path | None:
    """The file this process is logging to, or ``None`` if it is not."""
    return _log_file


def configure_logging() -> Path | None:
    """Set up the root logger for the application. Returns the log file, if any.

    Called once, first thing in ``main``. A stderr handler only when there is a
    stderr to write to; the file only on Windows. Calling it again does nothing,
    so a test or a second entry point cannot double every line.
    """
    global _configured, _log_file
    if _configured:
        return _log_file
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(STDERR_FORMAT))
        root.addHandler(stream)

    directory = log_dir()
    if directory is None:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = _FaultLogHandler(
            directory / LOG_FILENAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        # Nowhere to write is not a reason not to start. The app runs exactly
        # as it did before this module existed.
        return None
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(handler)
    _log_file = directory / LOG_FILENAME

    # An exception raised inside a Qt slot is handed to sys.excepthook, whose
    # default prints to a stderr this process does not have. Logged instead,
    # and then passed on, so a console build still prints it.
    previous = sys.excepthook

    def _log_uncaught(kind, value, traceback) -> None:
        logging.getLogger("app").error(
            "Uncaught exception", exc_info=(kind, value, traceback)
        )
        if sys.stderr is not None:
            previous(kind, value, traceback)

    sys.excepthook = _log_uncaught
    return _log_file
