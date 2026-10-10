"""The backup's index: what is in it, and what it was when it was written.

``<backup>/.data/backup.json`` records, per file, the size and modification
time of *both* sides plus the content digest, as of the last successful sync.
That index is what makes an incremental sync cheap. Without it, deciding
whether a file needs copying means hashing it on both sides, every time.

Why not simply compare the two files directly and skip the bookkeeping:

- **It compares like with like.** Modification times do not survive a copy onto
  another filesystem cleanly — FAT32 stores them to two-second granularity, and
  some drives round. Comparing the source against the backup copy directly would
  therefore report differences that are not differences. Recording what each
  side actually reported at the last sync, and comparing each side against its
  own recorded value, sidesteps that entirely: the numbers being compared came
  from the same filesystem both times.
- **The backup side is recorded too**, so a file edited directly on the backup
  drive is noticed rather than trusted.

Both comparisons are exact, and deliberately so. Allowing a tolerance — the
obvious move, and the one this originally made — is actively harmful here: a
two-second window means a track retagged within two seconds of a backup matches
its recorded mtime, is skipped, and then goes on being skipped forever, because
the recorded value never stops matching. A tolerance can only ever hide a real
change. Being too strict costs one needless hash; being too loose loses data.

A missing or corrupt manifest is not an error. It degrades to hashing
everything, which is slower but correct, and the dialog says so.

Per-file values are four-element arrays rather than objects. At the 65 536
track ceiling baked into ``_coerce_local_id`` that is roughly a 4 MB file
instead of a 13 MB one, and this gets rewritten at the end of every run.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.backup.identity import utc_now, write_json_atomic
from app.backup.locator import manifest_path
from app.versions import VAULT_VERSION

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1

# Identifies the file for anything that stumbles across it. A folder carrying
# this is a backup; a folder carrying a JSON file that happens to be called
# backup.json is not.
MANIFEST_KIND = "kroma-backup"

STATE_COMPLETE = "complete"
STATE_SYNCING = "syncing"


@dataclass(frozen=True)
class FileRecord:
    """One file as of the last successful sync.

    *src_mtime* is the source's modification time then; *dst_mtime* is what the
    backup copy reported after being written, which is usually but not always
    the same number — see the note about FAT32 above. Keeping both is what lets
    each side be compared against a value from its own filesystem.
    """

    size: int
    src_mtime: float
    dst_mtime: float
    digest: str

    def to_json(self) -> list:
        return [self.size, self.src_mtime, self.dst_mtime, self.digest]

    @classmethod
    def from_json(cls, value: Any) -> Optional["FileRecord"]:
        """Parse one entry, returning ``None`` if it is not usable.

        Tolerant on purpose: a single malformed entry should cost one file a
        re-hash, not invalidate the whole index.
        """
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        size, src_mtime, dst_mtime, digest = value
        if not isinstance(size, int) or not isinstance(digest, str):
            return None
        if not isinstance(src_mtime, (int, float)) or not isinstance(dst_mtime, (int, float)):
            return None
        return cls(size, float(src_mtime), float(dst_mtime), digest)


@dataclass
class Manifest:
    """The parsed contents of ``backup.json``.

    Mutable, unlike most things in this package: the apply pass updates entries
    as it writes files, and saves once at the end.
    """

    source_vault_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    app_version: str = ""
    # The layout of the vault this backup is a copy of. A backup *is* a vault,
    # so restoring one by opening it goes through the same check as any other
    # folder — this is here so the state is legible from the index too, without
    # having to open the copy's own vault.json.
    vault_version: int = 0
    state: str = STATE_COMPLETE
    options: dict = field(default_factory=dict)
    files: dict[str, FileRecord] = field(default_factory=dict)

    # False when there was no readable manifest and this is a blank one. The
    # scan uses it to decide whether it must hash every common file, and the
    # dialog uses it to explain why the first sync is slow.
    loaded: bool = False

    @property
    def interrupted(self) -> bool:
        """True when the previous run did not reach the end.

        Not a problem to repair specially — the diff notices whatever is
        missing and copies it. This exists so the dialog can say what happened
        instead of the user wondering why a sync they thought had finished has
        work to do.
        """
        return self.loaded and self.state == STATE_SYNCING

    # ---- persistence --------------------------------------------------

    @classmethod
    def load(cls, backup: Path) -> "Manifest":
        """Read the manifest from *backup*, or return an empty one.

        Never raises. Every failure — absent, unreadable, truncated, written by
        something else entirely — collapses to "no usable index", which the
        scan already handles by hashing everything.
        """
        path = manifest_path(backup)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read backup manifest at %s: %s", path, exc)
            return cls()

        if not isinstance(raw, dict) or raw.get("kind") != MANIFEST_KIND:
            logger.warning("%s is not a Kroma backup manifest", path)
            return cls()

        files: dict[str, FileRecord] = {}
        for relpath, value in (raw.get("files") or {}).items():
            record = FileRecord.from_json(value)
            if record is not None and isinstance(relpath, str):
                files[relpath] = record

        return cls(
            source_vault_id=str(raw.get("source_vault_id") or ""),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            app_version=str(raw.get("app_version") or ""),
            vault_version=(
                raw["vault_version"]
                if isinstance(raw.get("vault_version"), int)
                and not isinstance(raw.get("vault_version"), bool)
                else 0
            ),
            state=str(raw.get("state") or STATE_COMPLETE),
            options=raw.get("options") if isinstance(raw.get("options"), dict) else {},
            files=files,
            loaded=True,
        )

    def save(self, backup: Path) -> None:
        """Write the manifest to *backup*, atomically.

        Raises ``OSError`` — unlike loading, a manifest that cannot be written
        is a real failure. The payload may be on the drive, but a backup whose
        index did not survive is one that will re-hash its entire contents next
        time and cannot say which vault it came from.
        """
        if not self.created_at:
            self.created_at = utc_now()
        self.updated_at = utc_now()
        # Whatever wrote the backup last is the build that decided its layout,
        # so this is stamped at save rather than carried from the loaded index.
        self.vault_version = VAULT_VERSION

        write_json_atomic(manifest_path(backup), {
            "version": MANIFEST_VERSION,
            "kind": MANIFEST_KIND,
            "source_vault_id": self.source_vault_id,
            "app_version": self.app_version,
            "vault_version": self.vault_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state,
            "options": self.options,
            "files": {k: v.to_json() for k, v in sorted(self.files.items())},
        })

    # ---- the fast path ------------------------------------------------

    def is_unchanged(
        self,
        relpath: str,
        src_size: int, src_mtime: float,
        dst_size: int, dst_mtime: float,
    ) -> bool:
        """True when both sides still match what was recorded, so neither needs hashing.

        This is the whole optimisation. A sync of a library where 50 tracks were
        added to 1000 answers "yes" 1000 times here, and hashes nothing at all.

        It requires agreement on *both* sides: the source being untouched is not
        enough if somebody has been editing files on the backup drive.

        Exact on the timestamps. See the module docstring for why a tolerance
        would silently lose edits made just after a backup.
        """
        record = self.files.get(relpath)
        if record is None:
            return False
        return (
            record.size == src_size == dst_size
            and record.src_mtime == src_mtime
            and record.dst_mtime == dst_mtime
        )

    def record(self, relpath: str, size: int, src_mtime: float,
               dst_mtime: float, digest: str) -> None:
        self.files[relpath] = FileRecord(size, src_mtime, dst_mtime, digest)

    def forget(self, relpath: str) -> None:
        self.files.pop(relpath, None)
