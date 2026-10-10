"""What a backup is, and how to recognise one.

Deliberately shaped like :mod:`app.vault.vault_locator`, which settled these
questions for vaults already:

===========================  ==================================================
``is_vault()`` — has ``.data/``       ``is_backup()`` — has ``.data/backup.json``
``resolve_vault_folder()``            ``candidate_backup_folders()``
``create_vault()``                    ``suggested_backup_name()``
===========================  ==================================================

A backup is identified by its marker file, never by its name. The
``venombackup_YYYYMMDD`` name is only ever a *suggestion at creation time*, so
syncing to the same backup a month later updates it in place instead of
renaming it or making a second one next to it.

A finished backup is also a valid vault — it has a ``.data/`` folder, so
``is_vault()`` is true for it and the first-run picker can open it directly if
the main drive dies. That is the whole recovery story, and it needs no code.
The price is that a backup can be opened as a live library by accident, which
is why :func:`describes_a_backup` exists for the setup window to ask about.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.vault.vault_locator import DATA_DIR_NAME

# The marker. Sits inside .data/ alongside the databases so that a backup is
# also a vault, rather than a folder with a vault buried somewhere in it.
MANIFEST_FILENAME = "backup.json"

# Held for the duration of a run so two app instances cannot write one backup.
LOCK_FILENAME = "backup.lock"

BACKUP_NAME_PREFIX = "venombackup"


def manifest_path(backup: Path) -> Path:
    """Where the manifest lives inside *backup*."""
    return backup / DATA_DIR_NAME / MANIFEST_FILENAME


def lock_path(backup: Path) -> Path:
    """Where the run lock lives inside *backup*."""
    return backup / DATA_DIR_NAME / LOCK_FILENAME


def is_backup(path: Path) -> bool:
    """True when *path* is a directory holding a backup marker."""
    try:
        return path.is_dir() and manifest_path(path).is_file()
    except OSError:
        return False


def has_backup_name(path: Path) -> bool:
    """True when *path* is named like a backup, whatever follows the underscore.

    ``venombackup_20260823``, ``venombackup_offsite`` and ``venombackup_nas`` all
    qualify. The date in a generated name is a convenience, not a format anyone
    has to keep, so nothing here parses it.

    This is only ever used to *find* a folder. Whether one can be trusted — that
    it is a backup at all, and of which library — is :func:`is_backup` and the
    vault id in its manifest. A folder that merely has the name has no manifest,
    no recorded id, and nothing in it worth protecting.

    Case-insensitive, because the filesystems this lands on usually are.
    """
    return path.name.lower().startswith(f"{BACKUP_NAME_PREFIX}_")


def candidate_backup_folders(path: Path) -> list[Path]:
    """Folders at or just inside *path* that could be a backup, newest first.

    A candidate either carries the marker or has the name. Ordered by
    modification time so a caller that has no better reason to prefer one takes
    the most recently touched.
    """
    found = [
        candidate
        for candidate in (path, *_immediate_dirs(path))
        if is_backup(candidate) or has_backup_name(candidate)
    ]
    return sorted(found, key=_modified_at, reverse=True)


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def describes_a_backup(path: Path) -> bool:
    """True when *path*, or the vault inside it, carries a backup marker.

    Used by the vault setup window. A backup is a perfectly good vault and
    opening one is sometimes exactly what the user means to do — after losing
    the original, say — so this is a question to ask, not a door to close.
    """
    if is_backup(path):
        return True
    return any(is_backup(child) for child in _immediate_dirs(path))


def suggested_backup_name(when: datetime | None = None) -> str:
    """The folder name to offer when creating a new backup.

    Only ever a suggestion. Once created, a backup keeps its name forever — it
    is found by its marker file, so the date in the name records when the
    backup was started, not when it was last synced.
    """
    stamp = (when or datetime.now()).strftime("%Y%m%d")
    return f"{BACKUP_NAME_PREFIX}_{stamp}"


def has_generated_name(path: Path) -> bool:
    """True when the folder's name is one this app made up, not one someone chose.

    Generated names are ``venombackup_`` followed by a date stamp and nothing
    else, so an all-digit suffix is the whole test — ``20260823`` and ``2026``
    both count. Anything with a letter in it (``venombackup_offsite``,
    ``venombackup_nas``) is a name a person picked and is theirs to keep.

    ``venombackup_20260823_2`` is deliberately *not* generated by this rule. It
    only ever exists because a name collided, and renaming it to the name it
    already lost a fight with would fail anyway.
    """
    if not has_backup_name(path):
        return False
    suffix = path.name[len(BACKUP_NAME_PREFIX) + 1:]
    return bool(suffix) and suffix.isdigit()


def suggest_destination(parent: Path) -> Path:
    """Return the path a new backup would be created at inside *parent*.

    If the suggested name is taken by something that is not a backup, a suffix
    is added rather than reusing it — writing a vault into a stranger's folder
    because the names collided is the exact failure this package exists to
    avoid.
    """
    base = suggested_backup_name()
    candidate = parent / base
    if not candidate.exists() or is_backup(candidate):
        return candidate
    for n in range(2, 100):
        candidate = parent / f"{base}_{n}"
        if not candidate.exists() or is_backup(candidate):
            return candidate
    return parent / f"{base}_{datetime.now().strftime('%H%M%S')}"


def _immediate_dirs(path: Path) -> list[Path]:
    """Subdirectories of *path*, or an empty list if it cannot be read."""
    try:
        if not path.is_dir():
            return []
        return [child for child in path.iterdir() if child.is_dir()]
    except OSError:
        return []
