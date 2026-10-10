"""The log, on a Windows build that has no stderr to write it to.

Windows is faked — ``sys.platform`` and ``%LOCALAPPDATA%`` — because the code
only branches on those two. ``faulthandler`` is faked too: pytest arms the real
one for its own crash reports, and a test that re-pointed it at a temp file
would leave the rest of the run without them.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.utils import logfile  # noqa: E402


class _FaultHandler:
    def __init__(self) -> None:
        self.enabled_with: list = []
        self.disabled = 0

    def enable(self, file=None):
        self.enabled_with.append(file)

    def disable(self):
        self.disabled += 1


@pytest.fixture
def fresh(monkeypatch):
    """A logging setup this module has never configured, put back afterwards."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    monkeypatch.setattr(logfile, "_configured", False)
    monkeypatch.setattr(logfile, "_log_file", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    faults = _FaultHandler()
    monkeypatch.setattr(logfile, "faulthandler", faults)
    yield faults
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def _windows(monkeypatch, tmp_path) -> Path:
    monkeypatch.setattr(sys, "platform", "win32")
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local / "Kroma" / "logs" / "kroma-player.log"


def _added(before: list) -> list:
    return [h for h in logging.getLogger().handlers if h not in before]


def test_linux_logs_to_stderr_as_it_always_did(fresh, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    before = list(logging.getLogger().handlers)

    assert logfile.configure_logging() is None

    [handler] = _added(before)
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter._fmt == logfile.STDERR_FORMAT
    assert logging.getLogger().level == logging.INFO
    assert fresh.enabled_with == []


def test_windows_also_logs_to_a_file(fresh, monkeypatch, tmp_path):
    expected = _windows(monkeypatch, tmp_path)

    path = logfile.configure_logging()

    assert path == expected
    assert logfile.current_log_file() == expected
    logging.getLogger("app.test").warning("the vault would not open")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = expected.read_text(encoding="utf-8")
    assert "WARNING app.test: the vault would not open" in text


def test_a_windowed_build_with_no_stderr_still_gets_its_file(fresh, monkeypatch, tmp_path):
    """The frozen exe's case: sys.stderr is None, and a StreamHandler on it
    would only produce errors about itself."""
    expected = _windows(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stderr", None)
    before = list(logging.getLogger().handlers)

    assert logfile.configure_logging() == expected

    added = _added(before)
    assert len(added) == 1
    assert isinstance(added[0], logging.handlers.RotatingFileHandler)


def test_native_crashes_go_into_the_same_file(fresh, monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)
    before = list(logging.getLogger().handlers)
    logfile.configure_logging()
    [handler] = [h for h in _added(before) if isinstance(h, logfile._FaultLogHandler)]

    assert fresh.enabled_with == [handler.stream]

    # Rotation swaps the stream; faulthandler follows it to the new one.
    handler.doRollover()
    assert fresh.enabled_with[-1] is handler.stream
    assert len(fresh.enabled_with) == 2

    handler.close()
    assert fresh.disabled == 1


def test_an_uncaught_exception_is_logged(fresh, monkeypatch, tmp_path):
    """An exception raised inside a Qt slot reaches sys.excepthook, whose
    default writes to a stderr the windowed build does not have."""
    expected = _windows(monkeypatch, tmp_path)
    logfile.configure_logging()

    try:
        raise ValueError("a slot went wrong")
    except ValueError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)

    for handler in logging.getLogger().handlers:
        handler.flush()
    text = expected.read_text(encoding="utf-8")
    assert "Uncaught exception" in text
    assert "ValueError: a slot went wrong" in text


def test_nowhere_to_write_is_not_a_reason_not_to_start(fresh, monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)

    def refuse(*_args, **_kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(Path, "mkdir", refuse)

    assert logfile.configure_logging() is None
    assert logfile.current_log_file() is None


def test_no_localappdata_means_no_file(fresh, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert logfile.configure_logging() is None


def test_configuring_twice_does_not_double_every_line(fresh, monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)
    before = list(logging.getLogger().handlers)

    first = logfile.configure_logging()
    count = len(_added(before))
    second = logfile.configure_logging()

    assert first == second
    assert len(_added(before)) == count
