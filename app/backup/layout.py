"""Which parts of a vault a backup covers, and how to walk them.

Split out from the scan because the writer needs the same answers: the scan
decides what differs, but both have to agree on what was in scope in the first
place, and on which files are never anybody's business.

The vault layout this reflects is documented in :mod:`app.utils.library_paths`
and in the README:

    <vault>/mix/<shard>/<ID>.opus      the audio
    <vault>/bt/<shard>/<ID>.opus       instrumentals, mirroring mix/ exactly
    <vault>/.data/tracks.db            track metadata
    <vault>/.data/covers.db            cover thumbnail cache
    <vault>/.data/session.json         queue, history, window state
    <vault>/.data/settings.json        preferences, app and per-GUI
    <vault>/.data/vault.json           the vault's identity and layout version
    <vault>/.data/gui/<name>.kpi       installed interfaces
    <vault>/.data/models/              separation checkpoints and configs
    <vault>/.data/upgrade-snapshots/   database copies taken before a migration
                                       (app.vault.startup) — never backed up
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator, NamedTuple

from app.backup.locator import LOCK_FILENAME, MANIFEST_FILENAME
from app.backup.plan import BackupOptions
from app.utils.library_paths import BACKING_CATEGORY, DEFAULT_CATEGORY

logger = logging.getLogger(__name__)

DATA_DIR = ".data"

# Copied wholesale on every run rather than diffed. They are small, they must be
# internally consistent with each other, and "unchanged" is not a meaningful
# thing to say about a database that the app has open.
#
# tracks.db and covers.db do not appear here because they are not copied as
# files at all — see app.backup.sqlite_copy.
DATA_FILES = ("session.json", "settings.json", "vault.json")

# Caches of caches: copying them would copy churn and restore nothing.
#
# ``mpris-art`` is scratch JPEGs handed to the desktop's media widget, rewritten
# on every track change.
#
# ``.cache`` is the compiled-QML bytecode under ``.data/gui/`` — hundreds of
# small files, keyed by a GUI's build id, regenerated on the first launch that
# needs them and invalidated outright by a Qt upgrade. Without this the GUI
# backup option would replicate the whole of it and then replicate it again the
# next time an interface was rebuilt.
EXCLUDED_DIRS = frozenset({"mpris-art", ".cache"})

# -wal and -shm are SQLite's sidecars. Copying them is worse than useless: a
# stale WAL sitting next to a fresh database is how SQLite is told to undo the
# snapshot it was just given. .part files are somebody's interrupted write.
EXCLUDED_SUFFIXES = (".part", "-wal", "-shm", "-journal")

EXCLUDED_NAMES = frozenset({MANIFEST_FILENAME, LOCK_FILENAME})


class Entry(NamedTuple):
    """One file found on disk, as the directory itself described it.

    ``os.scandir`` carries size and mtime on the entry, so gathering these costs
    a single system call per directory rather than a ``stat`` per file. That is
    what makes a scan of a full library cheap enough to run before every sync.
    """

    relpath: str      # POSIX-style, relative to the vault root
    size: int
    mtime: float


def selected_roots(options: BackupOptions) -> tuple[str, ...]:
    """The subtrees this run covers, as vault-relative POSIX paths."""
    roots: list[str] = []
    if options.tracks:
        roots.append(DEFAULT_CATEGORY)
    if options.backing:
        roots.append(BACKING_CATEGORY)
    if options.guis:
        roots.append(f"{DATA_DIR}/gui")
    if options.models:
        roots.append(f"{DATA_DIR}/models")
    return tuple(roots)


def is_excluded(name: str) -> bool:
    """Whether a file name is one this package never copies."""
    return name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES)


def walk(root: Path, options: BackupOptions) -> dict[str, Entry]:
    """Index every in-scope file under *root*, keyed by relative path.

    Missing subtrees are not an error — ``mix/`` and ``bt/`` are created lazily
    on the first download, and a vault with no instrumentals simply has no
    ``bt/``. Same on the backup side, where on a first run none of them exist.
    """
    found: dict[str, Entry] = {}
    for relative_root in selected_roots(options):
        for entry in _walk_tree(root / relative_root, relative_root):
            found[entry.relpath] = entry
    return found


def _walk_tree(directory: Path, prefix: str) -> Iterator[Entry]:
    """Yield every file under *directory*, depth first.

    ``follow_symlinks=False`` throughout, and it is load-bearing rather than
    tidy: a symlinked directory inside the vault would otherwise let the copy
    escape the vault entirely, or — pointed at an ancestor — recurse until the
    path length gave out.

    Unreadable directories are logged and skipped rather than raising. One
    permission problem deep in the tree should cost that folder, not the sync.
    """
    try:
        scan = os.scandir(directory)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Skipping unreadable folder %s: %s", directory, exc)
        return

    with scan:
        for entry in scan:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in EXCLUDED_DIRS:
                        continue
                    yield from _walk_tree(Path(entry.path), f"{prefix}/{entry.name}")
                elif entry.is_file(follow_symlinks=False):
                    if is_excluded(entry.name):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    yield Entry(f"{prefix}/{entry.name}", stat.st_size, stat.st_mtime)
                # Anything else — a symlink, a socket, a device node — is not
                # library content and is left where it is.
            except OSError as exc:
                logger.warning("Skipping %s: %s", entry.path, exc)


def data_payload(source: Path) -> tuple[str, ...]:
    """The plain ``.data`` files this run copies, those that exist.

    Databases are excluded deliberately: they are snapshotted through SQLite
    rather than copied, because both run in WAL mode and a file copy of one
    misses whatever is still in its ``-wal``.
    """
    return tuple(
        f"{DATA_DIR}/{name}"
        for name in DATA_FILES
        if (source / DATA_DIR / name).is_file()
    )
