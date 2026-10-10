"""Backend-owned lyrics state: whether the tag walk has already run.

One flag, and it needs a module for the reason
``docs/architecture/cross-cutting/persistence.md`` gives — a new feature gets a
new typed façade over the ``app.*`` namespace rather than reaching into
:class:`~app.settings.store.SettingsStore` with a string literal at every call
site.

**Why it exists at all.** Copying the ``LYRICS`` tags out of a library's Opus
files into the ``lyrics`` table is a one-off walk over every track that has no
lyrics row. Tracks whose files never had a tag stay in that set permanently, so
without a marker the walk would re-open every one of them on every launch,
forever, to learn the same nothing.

**Why it is durable anyway.** The marker sits in ``settings.json`` with every
other ``app.*`` key rather than in the disposable ``session.json``, and not
because losing it would be expensive: it is idempotent, it only ever reads, and
the lazy fallback in :class:`~app.viewmodels.lyrics_view_model.LyricsViewModel`
catches any track it never reached. It is there because "already done" is a fact
about the library, not about where the user last was — and putting it in the
file that is meant to be deletable would make the walk re-run on a reset that
was asking for nothing of the sort.
"""
from __future__ import annotations

from typing import Optional

from app.settings.store import SettingsStore

TAG_SWEEP_DONE_KEY = "lyrics_tag_sweep_done"

DEFAULT_TAG_SWEEP_DONE = False


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return default


class LyricsSettings:
    """Typed accessors over the lyrics keys.

    Constructed with ``None`` it serves the defaults and swallows writes, so a
    view model built without a store — a test, a script — behaves as though the
    sweep had never run and never records that it did.
    """

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    @property
    def tag_sweep_done(self) -> bool:
        """Whether this library's embedded lyric tags have been copied in already."""
        if self._store is None:
            return DEFAULT_TAG_SWEEP_DONE
        return _as_bool(
            self._store.app_get(TAG_SWEEP_DONE_KEY, DEFAULT_TAG_SWEEP_DONE),
            DEFAULT_TAG_SWEEP_DONE,
        )

    @tag_sweep_done.setter
    def tag_sweep_done(self, value: bool) -> None:
        if self._store is not None:
            self._store.app_set(TAG_SWEEP_DONE_KEY, bool(value))
