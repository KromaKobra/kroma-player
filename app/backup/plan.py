"""What a backup run is: its options, its verdict, and the plan it produces.

Everything here is a frozen snapshot. The plan crosses a thread boundary — the
scan worker builds it, the view model displays it, the apply worker executes it
— and more importantly **the plan the user confirms is the exact plan that
runs**. Nothing is re-decided while files are being written.

The error pair follows the convention the rest of the app uses for cancellable
work: ``ExportError``/``ExportCancelled``, ``SeparationError``/
``SeparationCancelled``, and now these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class BackupError(RuntimeError):
    """The backup could not run, or could not continue."""


class BackupCancelled(RuntimeError):
    """The backup was stopped by the user partway through."""


class Action(str, Enum):
    """What the sync would do with one relative path."""

    ADD = "add"          # in the vault, absent from the backup
    UPDATE = "update"    # in both, and the contents differ
    EXTRA = "extra"      # in the backup, absent from the vault


class Identity(str, Enum):
    """Whether the chosen folder is a backup of *this* vault.

    The whole reason this enum exists: a vault names its audio files by track id
    alone, so pointing a sync at an unrelated backup would quietly overwrite its
    ``0042.opus`` with a different song and then replace its database. The three
    cases are handled very differently — see ``scan.check_identity``.
    """

    MATCH = "match"        # ids agree; this is the same library
    ABSENT = "absent"      # nothing recorded — an older backup, or a bare folder
    MISMATCH = "mismatch"  # ids disagree; almost certainly the wrong folder
    NEW = "new"            # the folder is not a backup yet


@dataclass(frozen=True)
class BackupOptions:
    """Which parts of the vault this run covers.

    ``backing`` is separate from ``tracks`` because instrumentals are machine
    generated, roughly double the library's size, and are the one category that
    can be recreated from what is already backed up.

    ``delete_extras`` is deliberately not persisted anywhere. It is the only
    irreversible thing this feature does, so it is chosen per run rather than
    inherited from a previous session.
    """

    tracks: bool = True
    backing: bool = True
    guis: bool = True
    models: bool = False
    delete_extras: bool = False
    # Set only by an explicit, separate act in the dialog. Without it a
    # mismatched vault id refuses to run.
    override_mismatch: bool = False


@dataclass(frozen=True)
class FileAction:
    """One file the run would touch.

    *relpath* is POSIX-style and relative to the vault root, so it is identical
    on both sides and stable across platforms.
    """

    relpath: str
    action: Action
    size: int          # source size for add/update, backup size for extra
    mtime: float       # ditto


@dataclass(frozen=True)
class BackupPlan:
    """The complete answer to "what would this sync do?".

    Byte counts are separated because they answer different questions:
    *bytes_to_write* is how long it will take, while *backup_bytes_after* is
    whether it will fit.
    """

    source: Path
    destination: Path
    options: BackupOptions
    identity: Identity

    actions: tuple[FileAction, ...] = ()
    unchanged: int = 0

    # The .data payload is not diffed — it is small, it must be internally
    # consistent, and a database is not meaningfully "unchanged". It is one
    # fixed line item, sized from the source files.
    data_bytes: int = 0

    backup_bytes_before: int = 0
    free_bytes: int = 0

    source_vault_id: str = ""
    backup_vault_id: str = ""
    backup_created_at: str = ""

    # True when the last run against this backup did not finish. The diff
    # repairs it as a matter of course; this only exists so the dialog can say
    # so rather than silently doing more work than expected.
    interrupted: bool = False
    # True when there was no usable manifest, so every common file had to be
    # hashed. Correct, just slow — and worth telling the user why.
    full_verify: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ---- derived ------------------------------------------------------

    @property
    def added(self) -> tuple[FileAction, ...]:
        return tuple(a for a in self.actions if a.action is Action.ADD)

    @property
    def updated(self) -> tuple[FileAction, ...]:
        return tuple(a for a in self.actions if a.action is Action.UPDATE)

    @property
    def extras(self) -> tuple[FileAction, ...]:
        return tuple(a for a in self.actions if a.action is Action.EXTRA)

    @property
    def bytes_to_write(self) -> int:
        """Payload bytes this run would copy, databases included."""
        return sum(a.size for a in self.actions if a.action is not Action.EXTRA) + self.data_bytes

    @property
    def extra_bytes(self) -> int:
        return sum(a.size for a in self.extras)

    @property
    def backup_bytes_after(self) -> int:
        """Estimated size of the backup once this run finishes.

        An update replaces bytes rather than adding them, so only additions
        grow the total. Deletions shrink it, but only if the user opted in.
        """
        after = self.backup_bytes_before + sum(a.size for a in self.added)
        if self.options.delete_extras:
            after -= self.extra_bytes
        return after

    @property
    def touches_anything(self) -> bool:
        """Whether running this would change the backup at all.

        The databases are rewritten every run, so this is never false in
        practice — but the dialog still wants to distinguish "50 new tracks"
        from "nothing but the databases".
        """
        return bool(self.added or self.updated
                    or (self.options.delete_extras and self.extras))

    @property
    def is_blocked(self) -> bool:
        """Whether apply would refuse this plan as it stands."""
        return self.identity is Identity.MISMATCH and not self.options.override_mismatch

    @property
    def churn(self) -> float:
        """Share of the existing backup this run would rewrite, 0.0 to 1.0.

        The secondary guard, and the one the vault id cannot provide: a backup
        that really is this library's but is years out of date passes the id
        check and still deserves a hard look before it is rewritten wholesale.
        """
        existing = len(self.updated) + self.unchanged + len(self.extras)
        if existing <= 0:
            return 0.0
        return (len(self.updated) + len(self.extras)) / existing


@dataclass(frozen=True)
class BackupResult:
    """What a finished run actually did."""

    written: int = 0
    deleted: int = 0
    bytes_written: int = 0
    destination: Path | None = None
    # Non-fatal problems worth surfacing: a covers.db snapshot that failed, a
    # file that could not be read. The run completed regardless.
    warnings: tuple[str, ...] = field(default_factory=tuple)
