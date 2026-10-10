"""Backend-owned export preferences.

Same reasoning as :mod:`app.settings.backing_track`: which format you export in
and where you last exported to are machine configuration, not presentation, and
a GUI that stored them would lose them the moment the user switched interfaces.

The destination is kept as an absolute path rather than something vault-relative
because the entire point of it is to name somewhere the app does not own.
"""
from __future__ import annotations

from typing import Optional

from app.exporter.formats import DEFAULT_FORMAT, EXPORT_FORMATS
from app.settings.store import SettingsStore

FORMAT_KEY = "export_format"
DIRECTORY_KEY = "export_directory"

DEFAULT_DIRECTORY = ""


def _as_choice(value, default: str, allowed: tuple[str, ...]) -> str:
    """Coerce to one of *allowed*, falling back to *default*."""
    if isinstance(value, str) and value in allowed:
        return value
    return default


def _as_text(value, default: str) -> str:
    return value if isinstance(value, str) else default


class ExportSettings:
    """Typed accessors over the export keys.

    Constructed with ``None`` it serves the defaults, so a test or a script
    built without a session still gets the documented behaviour.
    """

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    @property
    def format(self) -> str:
        """``"opus"`` or ``"mp3"`` — what the next export produces."""
        if self._store is None:
            return DEFAULT_FORMAT
        return _as_choice(
            self._store.app_get(FORMAT_KEY, DEFAULT_FORMAT),
            DEFAULT_FORMAT, EXPORT_FORMATS,
        )

    @format.setter
    def format(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(
                FORMAT_KEY, _as_choice(value, DEFAULT_FORMAT, EXPORT_FORMATS)
            )

    @property
    def directory(self) -> str:
        """The folder the last export was written into.

        Remembered so a second export does not mean navigating the file picker
        again. Not validated here — the folder may have been deleted or been on
        a drive that is no longer attached, and the view model is where that
        becomes a disabled button rather than an exception.
        """
        if self._store is None:
            return DEFAULT_DIRECTORY
        return _as_text(self._store.app_get(DIRECTORY_KEY, DEFAULT_DIRECTORY),
                        DEFAULT_DIRECTORY)

    @directory.setter
    def directory(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(DIRECTORY_KEY, _as_text(value, DEFAULT_DIRECTORY))
