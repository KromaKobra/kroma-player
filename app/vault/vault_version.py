"""The vault's layout version — what this build can open, and what it cannot.

A vault is a folder, and its shape is a contract: ``.data/tracks.db``,
``.data/gui/*.kpi``, ``mix/<shard>/<id>.opus``, and the handful of JSON
documents beside them. Every one of those pieces used to carry its own version
except the arrangement itself, and that gap had a real consequence: a build
opening a vault written by a newer build found a database whose
``PRAGMA user_version`` was ahead of its own ``SCHEMA_VERSION``, applied no
migrations — there were none to apply, they were in the future — and then ran
against columns and files it knew nothing about.

So the vault says which layout it is, in ``vault.json`` beside the vault id:

    {"vault_version": 1, ...}

Read before anything is opened, because that is the only moment the answer is
still useful. Too old is migrated. **Too new is refused**, and the user is
returned to the vault picker: an older build cannot be taught what a newer
layout means, and guessing would write into it.

Deliberately shaped like :mod:`app.database.schema`, down to restamping after
each step so an interrupted run lands on the last version that fully applied.
The two are the same problem at different scales, and there is no reason for
them to be read differently.

To add a migration: write one module under :mod:`app.vault.migrations`, named
for the version it produces, and raise ``VAULT_VERSION`` in :mod:`app.versions`
to match. Never renumber or edit a released one — a vault in the wild has
already recorded it. That package's docstring is where the rules are written
down; this module is only the loop that runs them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from app.backup.identity import read_document, update_document
from app.versions import MIN_VAULT_VERSION, VAULT_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultMigration:
    """One step in the evolution of the vault's layout.

    ``apply`` receives the vault root. Like a schema migration it is expected to
    inspect before it writes: a step that has already half-run must be able to
    run again, because the stamp is an optimisation and not the thing that makes
    the migration safe.
    """

    version: int
    description: str
    apply: Callable[[Path], None]


def _migrations() -> Sequence[VaultMigration]:
    """The ordered steps, from :mod:`app.vault.migrations`.

    Imported here rather than at module scope because the two modules need each
    other: every step is a ``VaultMigration``, which is defined above, and this
    loop needs the list of them. A top-level import in both directions is a
    cycle whose outcome depends on which module something happened to import
    first — and the failure mode is an ``ImportError`` at startup, from a file
    nobody was editing.

    One import per call is free: the module is in ``sys.modules`` after the
    first, and this runs once per launch.
    """
    from app.vault.migrations import VAULT_MIGRATIONS

    return VAULT_MIGRATIONS


def read_vault_version(vault: Path | None = None) -> int:
    """Return the layout version stamped on *vault*, or ``0`` when absent.

    ``0`` means "never stamped", which is not the same as "unknown" — see
    ``effective_version``.
    """
    stamped = read_document(vault).get("vault_version")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return 0
    return stamped if stamped > 0 else 0


def effective_version(vault: Path | None = None) -> int:
    """Return the layout version to treat *vault* as being at.

    An unstamped vault is version 1 by definition: stamping began with version
    1, so a vault that predates the stamp is a vault at the layout that existed
    before it. Treating it as version 0 instead would make every existing
    library look older than the oldest supported layout the moment
    ``MIN_VAULT_VERSION`` ever moves.
    """
    return read_vault_version(vault) or 1


def check_vault(vault: Path) -> str:
    """Return ``""`` when *vault* is usable, or a message explaining why not.

    The message is shown to the user in the vault picker, so it says what
    happened and what they can do about it rather than naming a constant.
    """
    version = effective_version(vault)

    if version > VAULT_VERSION:
        return (
            f"“{vault.name}” was last used by a newer version of Kroma Player "
            f"(vault layout {version}; this build understands up to "
            f"{VAULT_VERSION}). Opening it with this build could damage it. "
            "Update Kroma Player, or choose a different vault."
        )

    if version < MIN_VAULT_VERSION:
        return (
            f"“{vault.name}” uses vault layout {version}, which this build no "
            f"longer knows how to upgrade (it supports {MIN_VAULT_VERSION} and "
            "newer). Open it with an older release first, or choose a different "
            "vault."
        )

    return ""


def migrate_vault(vault: Path) -> int:
    """Bring *vault* up to ``VAULT_VERSION`` and return the version it lands on.

    Only ever called after ``check_vault()`` has passed, so the version is known
    to be in range and every pending migration is one this build carries.

    Each step is stamped as soon as it completes rather than all of them at the
    end, so an interrupted upgrade leaves the vault on the last layout that
    fully applied instead of somewhere between two.
    """
    current = effective_version(vault)

    for migration in _migrations():
        if migration.version <= current:
            continue
        logger.info(
            "Migrating vault to layout %s: %s", migration.version, migration.description
        )
        migration.apply(vault)
        _stamp(vault, migration.version)
        current = migration.version

    # Compared against what is *written* rather than against ``current``, which
    # is the effective version and reads as 1 for a vault that has never been
    # stamped at all. That vault is at layout 1 and simply has not said so yet;
    # comparing effective versions would decide there was nothing to do and
    # leave it unstamped forever.
    #
    # Stamping VAULT_VERSION rather than the last migration that ran is
    # deliberate: reaching the end of the list means the vault is at the current
    # layout, and a version bump with no migration behind it is the ordinary
    # case of a change that needs nothing done to existing vaults.
    if read_vault_version(vault) != VAULT_VERSION:
        _stamp(vault, VAULT_VERSION)

    return VAULT_VERSION


def _stamp(vault: Path, version: int) -> None:
    """Record *version* as the vault's layout, leaving the rest of the file alone."""
    if update_document(vault, vault_version=version):
        logger.info("Vault at %s is now layout version %s", vault, version)
