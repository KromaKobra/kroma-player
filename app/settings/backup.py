"""Backend-owned backup preferences.

Same reasoning as :mod:`app.settings.export`: where you last backed up to, and
which parts of the vault you include, are machine configuration rather than
presentation, and a GUI that stored them would lose them the moment the user
switched interfaces. Re-picking an external drive on every sync would be a
small, permanent irritation.

One thing is deliberately *not* here: whether to delete files the library no
longer has. That is the only irreversible action in the feature, so it is
chosen per run rather than remembered — see ``BackupOptions.delete_extras``.
"""
from __future__ import annotations

from typing import Optional

from app.settings.store import SettingsStore

DIRECTORY_KEY = "backup_directory"
TRACKS_KEY = "backup_tracks"
BACKING_KEY = "backup_backing"
GUIS_KEY = "backup_guis"
MODELS_KEY = "backup_models"

DEFAULT_DIRECTORY = ""

# Tracks and the interfaces are on by default: they are the library and the way
# it looks, and both are small trouble to copy.
DEFAULT_TRACKS = True
DEFAULT_GUIS = True

# Instrumentals are on because losing one means re-running a long GPU job, and
# the user who has any has already decided they are worth the time.
DEFAULT_BACKING = True

# Separation models are off: they are hundreds of megabytes each, they are not
# library content, and they came from somewhere the user can fetch them again.
DEFAULT_MODELS = False


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_text(value, default: str) -> str:
    return value if isinstance(value, str) else default


class BackupSettings:
    """Typed accessors over the backup keys.

    Constructed with ``None`` it serves the defaults, so a test or a script
    built without a session still gets the documented behaviour.
    """

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    @property
    def directory(self) -> str:
        """The folder the last backup was written to.

        Not validated here — it is usually an external drive, and whether it is
        plugged in right now is the view model's question to answer as a
        disabled button rather than this module's to answer as an exception.
        """
        if self._store is None:
            return DEFAULT_DIRECTORY
        return _as_text(self._store.app_get(DIRECTORY_KEY, DEFAULT_DIRECTORY),
                        DEFAULT_DIRECTORY)

    @directory.setter
    def directory(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(DIRECTORY_KEY, _as_text(value, DEFAULT_DIRECTORY))

    @property
    def tracks(self) -> bool:
        return self._flag(TRACKS_KEY, DEFAULT_TRACKS)

    @tracks.setter
    def tracks(self, value: bool) -> None:
        self._set_flag(TRACKS_KEY, value, DEFAULT_TRACKS)

    @property
    def backing(self) -> bool:
        return self._flag(BACKING_KEY, DEFAULT_BACKING)

    @backing.setter
    def backing(self, value: bool) -> None:
        self._set_flag(BACKING_KEY, value, DEFAULT_BACKING)

    @property
    def guis(self) -> bool:
        return self._flag(GUIS_KEY, DEFAULT_GUIS)

    @guis.setter
    def guis(self, value: bool) -> None:
        self._set_flag(GUIS_KEY, value, DEFAULT_GUIS)

    @property
    def models(self) -> bool:
        return self._flag(MODELS_KEY, DEFAULT_MODELS)

    @models.setter
    def models(self, value: bool) -> None:
        self._set_flag(MODELS_KEY, value, DEFAULT_MODELS)

    # -- internals ------------------------------------------------------

    def _flag(self, key: str, default: bool) -> bool:
        if self._store is None:
            return default
        return _as_bool(self._store.app_get(key, default), default)

    def _set_flag(self, key: str, value, default: bool) -> None:
        if self._store is not None:
            self._store.app_set(key, _as_bool(value, default))
