"""Library filesystem layout helpers."""

from __future__ import annotations

import os
from pathlib import Path

from app.config.kord_config import get_vault_path

DEFAULT_LIBRARY_ROOT = Path.home() / "Music" / "venomvault"
DEFAULT_CATEGORY = "mix"

# Instrumental counterparts. The tree mirrors ``mix/`` exactly — same shard
# math, same local ids — it is just sparser, because not every mix has one.
BACKING_CATEGORY = "bt"

# Resolved once per run and then reused: every track path goes through
# library_root(), so it must not re-read the config file each time.
_library_root: Path | None = None

# TODO: Make sure inputs to all function local_ids can accurately accept str hex.
# I think it would be a good idea to make int local_ids the standard, and only use str hex for files/folders

def set_library_root(path: Path | str) -> None:
    """Pin the library root for this run.

    Called at startup once the vault has been resolved (see ``app.vault``), so
    every later path lookup is a plain attribute read.
    """
    global _library_root
    _library_root = Path(path).expanduser()


def _under_test() -> bool:
    """True when we are running inside the test suite.

    ``PYTEST_CURRENT_TEST`` is set by pytest itself; ``KROMA_TESTING`` is set by
    ``tests/conftest.py`` so the guard also covers collection and fixture setup,
    where pytest's own variable is absent.
    """
    return bool(os.environ.get("KROMA_TESTING") or os.environ.get("PYTEST_CURRENT_TEST"))


def library_root() -> Path:
    """Return the root folder for the media library."""
    global _library_root
    if _library_root is None:
        # Under test the fallback is a hazard, not a convenience: it resolves to
        # the developer's real vault, so anything that writes through a track
        # path would edit their actual music. Fail loudly instead. The autouse
        # fixture in tests/conftest.py normally makes this unreachable.
        if _under_test():
            raise RuntimeError(
                "library_root() was called before set_library_root() while running "
                "under test. Refusing to fall back to the real vault — point the "
                "path helpers at a temporary directory first (see the "
                "isolated_library_root fixture in tests/conftest.py)."
            )
        configured = get_vault_path()
        _library_root = configured if configured is not None else DEFAULT_LIBRARY_ROOT
    return _library_root


def data_root(vault: Path | None = None) -> Path:
    """Return the folder that stores app metadata (databases, caches).

    *vault* names a vault explicitly, for the startup code that has to look at
    one before it becomes the library root; omitted, it is the current library.
    The same convention :func:`app.backup.identity.identity_path` follows.
    """
    return (vault if vault is not None else library_root()) / ".data"


def tracks_db_path(vault: Path | None = None) -> Path:
    """Return the SQLite database path for track metadata."""
    return data_root(vault) / "tracks.db"


def covers_db_path(vault: Path | None = None) -> Path:
    """Return the SQLite path for the cover-thumbnail cache.

    This is a derived, disposable cache (see ``app.artwork.cover_store``) and
    lives alongside ``tracks.db`` so the whole library stays a portable folder.
    """
    return data_root(vault) / "covers.db"


def models_root() -> Path:
    """Return the folder holding user-supplied vocal-removal models.

    Lives under ``.data/`` because it is machine-local support material rather
    than library content: a vault copied to another machine keeps its models,
    but nothing here is regenerable from the tracks.
    """
    return data_root() / "models"


def normalize_local_id(value: str | int) -> str:
    """Return a zero-padded four-digit uppercase hexadecimal identifier."""
    numeric = _coerce_local_id(value)
    return f"{numeric:04X}"


def shard_name(local_id: str | int) -> str:
    """Map a local id to the 256-track shard folder name (uppercase)."""
    numeric = _coerce_local_id(local_id)

    # Shift to 1..65536 space so groups become 0001-0100, 0101-0200, ...
    x = numeric + 1
    group = (x - 1) // 0x100
    low = group * 0x100 + 1
    high = (group + 1) * 0x100

    return f"{low:04X}-{high:04X}"


def track_filename(local_id: str | int) -> str:
    """Return the uppercase file name for a track."""
    numeric = _coerce_local_id(local_id)
    return f"{numeric:04X}.opus"


def category_root(category: str = DEFAULT_CATEGORY) -> Path:
    """Return the root folder for a library category (mix, stems, etc.)."""
    return library_root() / category


def track_path(local_id: str | int, category: str = DEFAULT_CATEGORY) -> Path:
    """Return the canonical uppercase path to a track within the library."""
    return category_root(category) / shard_name(local_id) / track_filename(local_id)


def _coerce_local_id(value: str | int) -> int:
    if isinstance(value, int):
        numeric = value
    else:
        numeric = int(str(value), 16)
    if numeric < 0 or numeric > 0xFFFF:
        raise ValueError("local_id must be within 0000 to FFFF")
    return numeric
