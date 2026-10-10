"""Unifying an album's artwork — the one operation here that rewrites audio.

Everything in this package is free of Qt and of SQLAlchemy so it can be driven
from a test or from a worker thread, and ``workers.py`` is the only thing
standing between it and the view model. That is the shape
``docs/guides/add-a-feature.md`` prescribes for a subsystem, and the shape
``app/exporter/`` and ``app/backup/`` already have.

**Why this is a subsystem at all**, when setting an album's cover is one UPDATE:
because unifying does not set the album's cover, it sets *the tracks'*. The
artwork a library actually shows comes out of each Opus file's own
``METADATA_BLOCK_PICTURE`` — ``covers.db`` is a rebuildable cache in front of it
— so changing only the database would leave the files disagreeing with it and
the next rescan would undo the repair. Unify therefore rewrites N audio files,
which is slow, fallible, and has to be cancellable.

**The plan the user confirms is the plan that runs.** :class:`UnifyPlan` is a
frozen snapshot taken on the UI thread; nothing is re-decided while files are
being written. The same bargain ``BackupPlan`` makes, for the same reason —
this one also crosses two thread boundaries.

**Which tracks change is an input, never a default.** A track's artwork belongs
to the album it is *primary* for, so a track that merely appears on this album
is entitled to different art and must not be swept up. The caller passes the
exact ids; this package never widens them.
"""
from app.albums.unify import (
    UnifyCancelled,
    UnifyError,
    UnifyPlan,
    build_plan,
    rewrite_track,
)

__all__ = [
    "UnifyCancelled",
    "UnifyError",
    "UnifyPlan",
    "build_plan",
    "rewrite_track",
]
