"""Backend-owned import preferences.

Three settings, none of them about how anything is drawn — which is why they are
here and not in a GUI's own store. One is about what the import list *contains*;
the other two are about which files on disk become candidates at all. The list,
the six-state classifier and every count the header quotes belong to
:class:`~app.viewmodels.import_view_model.ImportViewModel`, so a rule about which
rows the list holds has to be decided next to the arithmetic that counts them.

The two option keys are remembered rather than asked per run because neither is
irreversible: a file imported by mistake is one row to drop from a list, not
something undone from the library. That is the distinction
``BackupOptions.delete_extras`` is on the other side of.

Kept in its own module rather than folded into an existing one for the same
reason the other five are separate: a settings module is named for the feature
it configures, and importing is not downloading, exporting or backing up.
"""
from __future__ import annotations

from typing import Iterable, Optional

from app.importer.media_probe import MEDIA_EXTENSIONS
from app.settings.store import SettingsStore

CLEAR_IMPORTED_KEY = "clear_imported_rows"
FILENAME_FALLBACK_KEY = "import_filename_fallback"
EXCLUDED_FORMATS_KEY = "import_excluded_formats"

# On. The import list accumulates — several folders, a CSV, files paired by hand
# — and what the user is deciding on is the rows that still have work in them. A
# row that is in the library has none: it has nothing to press, nothing to
# choose, and it dims itself to say so. Forty of them at the end of a forty-file
# import are forty rows to read past.
#
# Nothing is lost by default because nothing is thrown away: the count of what
# was already there is reported in the completion toast, which is where a figure
# about a run belongs.
DEFAULT_CLEAR_IMPORTED = True

# On. A file with no title and no artist is not a file the user wants skipped —
# it is the one they most need help with, and searching it by its own name is
# the only handle anything has on it. The cost of guessing wrong is bounded
# because a weak match is reported not-found rather than attached to the wrong
# recording, so the failure mode is a row that says "pair me", which is exactly
# where the file would have been had it never been searched at all.
DEFAULT_FILENAME_FALLBACK = True

# Nothing. Stored as what is *excluded* rather than what is included, so a
# container added to MEDIA_EXTENSIONS in a later release arrives switched on
# rather than silently dropped from every import by a setting written before it
# existed.
DEFAULT_EXCLUDED_FORMATS = ""


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_formats(value) -> frozenset[str]:
    """Parse the stored comma-separated list into known extension names.

    Anything unrecognised is dropped rather than kept: the store holds strings
    it never interprets, so this is the only place that can tell a container
    name from a typo, and an unreadable value has to read as "nothing excluded".
    Reading it the other way — excluding a name nothing matches — would be
    harmless, but reading a *malformed whole* that way would not, and one rule
    for both is the one worth having.
    """
    if not isinstance(value, str):
        return frozenset()
    known = {ext.lstrip(".") for ext in MEDIA_EXTENSIONS}
    return frozenset(
        part for part in (p.strip().lstrip(".").lower() for p in value.split(","))
        if part in known
    )


class ImportSettings:
    """Typed accessors over the import keys.

    Constructed with ``None`` it serves the defaults, so a test or a script
    built without a session still gets the documented behaviour.
    """

    def __init__(self, store: Optional[SettingsStore] = None) -> None:
        self._store = store

    @property
    def clear_imported(self) -> bool:
        """Whether a row that is in the library stays out of the import list."""
        if self._store is None:
            return DEFAULT_CLEAR_IMPORTED
        return _as_bool(
            self._store.app_get(CLEAR_IMPORTED_KEY, DEFAULT_CLEAR_IMPORTED),
            DEFAULT_CLEAR_IMPORTED,
        )

    def set_clear_imported(self, value) -> None:
        """Write it, and flush.

        Flushed here rather than left for the shutdown pass because this is a
        preference the user just reached into Settings to change, and a
        preference that does not survive a crash reads as one that did not take.
        The same reasoning as ``DownloadsViewModel._flush``.
        """
        if self._store is None:
            return
        self._store.app_set(
            CLEAR_IMPORTED_KEY, _as_bool(value, DEFAULT_CLEAR_IMPORTED)
        )
        self._store.flush()

    @property
    def filename_fallback(self) -> bool:
        """Whether files with no usable tags are searched by their file name."""
        if self._store is None:
            return DEFAULT_FILENAME_FALLBACK
        return _as_bool(
            self._store.app_get(FILENAME_FALLBACK_KEY, DEFAULT_FILENAME_FALLBACK),
            DEFAULT_FILENAME_FALLBACK,
        )

    @property
    def excluded_formats(self) -> frozenset[str]:
        """Container names the user has turned off, bare and lower-case."""
        if self._store is None:
            return frozenset()
        return _as_formats(
            self._store.app_get(EXCLUDED_FORMATS_KEY, DEFAULT_EXCLUDED_FORMATS)
        )

    def set_import_options(
        self, filename_fallback, excluded_formats: Iterable
    ) -> None:
        """Write both options the import card sets, and flush.

        One setter because they are answered together, by one press of one
        button. Two would let a crash between them leave a user's import
        remembered half one way and half the other, which is worse than
        forgetting it entirely. Flushed for the reason ``set_clear_imported``
        is: an option that does not survive a crash reads as one that did not
        take.
        """
        if self._store is None:
            return
        known = {ext.lstrip(".") for ext in MEDIA_EXTENSIONS}
        excluded = sorted(
            part
            for part in (
                str(ext).strip().lstrip(".").lower() for ext in excluded_formats
            )
            if part in known
        )
        self._store.app_set(
            FILENAME_FALLBACK_KEY, _as_bool(filename_fallback, DEFAULT_FILENAME_FALLBACK)
        )
        self._store.app_set(EXCLUDED_FORMATS_KEY, ",".join(excluded))
        self._store.flush()
