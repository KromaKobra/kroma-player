"""Session persistence — the disposable half of what a launch remembers.

``<vault>/.data/session.json`` holds *where you were*: the queue, the history,
the window's size, which page was open, which GUI package to load. Losing it
costs a session, never a preference — *how you like it* lives in
``settings.json`` beside it, written by :mod:`app.settings.file`.

Version 2 is the nested shape. Version 1 was a single flat ``settings`` bag that
mixed queue state, durable preferences and GUI presentation into one lifetime;
:func:`~app.settings.document.load` discards it, which is the whole of the
migration.
"""
from __future__ import annotations

from pathlib import Path

from app.settings import document
from app.utils.library_paths import data_root

SESSION_VERSION = 2


def session_path() -> Path:
    return data_root() / "session.json"


def load_session() -> dict:
    """Load the saved session dict, or return {} on any error."""
    return document.load(session_path(), SESSION_VERSION)


def save_session(data: dict) -> None:
    """Write session data to disk. Silently ignores errors."""
    document.save(session_path(), SESSION_VERSION, data)
