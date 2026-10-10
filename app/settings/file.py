"""Settings persistence — the durable half of what a launch remembers.

``<vault>/.data/settings.json`` holds *how you like it*: everything in the
Settings tab, and every GUI look-and-feel preference. Its counterpart is
``session.json`` (see :mod:`app.session`), which holds where you were.

Two files rather than one because they have two lifetimes and two blast radii.
The queue is rebuilt by playing music; the download consent record, the backup
destination and the separation interpreter path are not rebuilt by anything. So
this one is written atomically and versioned on its own — the two documents must
never share a version constant, or bumping the shape of one would silently
discard the other.
"""
from __future__ import annotations

from pathlib import Path

from app.settings import document
from app.utils.library_paths import data_root

SETTINGS_VERSION = 1


def settings_path() -> Path:
    return data_root() / "settings.json"


def load_settings() -> dict:
    """Load the saved settings dict, or return {} on any error."""
    return document.load(settings_path(), SETTINGS_VERSION)


def save_settings(data: dict) -> None:
    """Write settings to disk atomically. Silently ignores errors."""
    document.save(settings_path(), SETTINGS_VERSION, data, atomic=True)
