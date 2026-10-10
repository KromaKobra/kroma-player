"""Every step that has ever changed the shape of a vault, in order.

:mod:`app.vault.vault_version` owns the *loop* — read the stamp, skip what is
already applied, run the rest, restamp after each one. This package owns the
*list*. They were one file until the list needed room to grow: a tuple of
inline lambdas is fine at zero entries and unreadable at six, and a migration
is exactly the kind of code that has to stay readable years after it was
written, because the vault that needs it is older than everyone's memory of it.

So: one module per step, and this file is only the ordering.

**The rules**, which are the same rules the tuple carried in its docstring:

- **One step per module, numbered for the version it produces.** Version 1 is
  the layout as it first shipped, so there is nothing to migrate *to* it and
  numbering starts at 2. ``m0002_<slug>.py`` produces layout 2.
- **A step inspects before it writes, and is safe to run twice.** The stamp is
  an optimisation. What makes a migration safe is that running it against a
  vault it has already been run against does nothing — an interrupted upgrade
  is restamped only after the step it was in the middle of *completed*, so the
  next launch starts that step again from wherever it got to.
- **Never renumber or edit a released step.** A vault in the wild has already
  recorded that it ran, and will never run it again; changing what it does
  changes it only for vaults that have not seen it yet, which is the worst of
  both outcomes.
- **The module and the constant land together.** Raising ``VAULT_VERSION`` in
  :mod:`app.versions` and adding the module here are one commit, and
  ``tests/test_vault_migrations.py`` fails if they come apart.
- **A release that raises ``VAULT_VERSION`` says so in its changelog
  highlights.** Migrating is a one-way door — ``check_vault()`` refuses the
  migrated vault to every older build — so the updater warns before an update
  that carries one, and it can only warn about what the release announced.

Imports are explicit and in order rather than discovered by scanning the
directory. An ordering that depends on filesystem iteration is an ordering that
can surprise you, and this is the one list in the application where being
surprised means writing into somebody's library in the wrong sequence.

Most releases add nothing here, and that is correct rather than a gap:
``VAULT_VERSION`` is the layout contract, not the release number, and it rises
only when the layout actually changes. ``migrate_vault()`` already handles a
version bump with no migration behind it.
"""

from __future__ import annotations

from typing import Sequence

from app.vault.vault_version import VaultMigration

# Add each new step's import here, in ascending version order, and append its
# MIGRATION to the tuple below. Both halves, or the test says so.
#
# from app.vault.migrations import m0002_example

VAULT_MIGRATIONS: Sequence[VaultMigration] = (
    # m0002_example.MIGRATION,
)

__all__ = ["VAULT_MIGRATIONS"]
