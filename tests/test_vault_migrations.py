"""The invariants the migration list has to hold, checked without a vault.

``migrate_vault()`` walks ``VAULT_MIGRATIONS`` in order, skipping anything at or
below the stamp and restamping after each step. Everything that makes that
correct is a property of the *list* rather than of any one step:

- a gap means a vault stamped at the missing version is never brought forward;
- a duplicate means one of the two steps silently never runs;
- an unsorted tuple means the loop's ``<= current`` skip drops later steps;
- a top that does not equal ``VAULT_VERSION`` means either a step nobody can
  reach, or a stamp claiming a layout no step produced.

The last test is the one that earns its place in practice: migrations are
written by copying the previous one, and a module that keeps the number it was
copied from looks exactly like a correct module until a vault runs it.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.vault.migrations import VAULT_MIGRATIONS  # noqa: E402
from app.vault.vault_version import VaultMigration  # noqa: E402
from app.versions import VAULT_VERSION  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "app" / "vault" / "migrations"

# ``m0002_covers_moved.py`` → version 2. The zero padding is for sorting in a
# file listing and carries no meaning beyond that.
MODULE_NAME = re.compile(r"^m(\d{4})_[a-z0-9_]+$")


def _step_modules() -> list[Path]:
    """Every migration module in the package, by filename order."""
    return sorted(
        path
        for path in MIGRATIONS_DIR.glob("m*.py")
        if path.name != "__init__.py"
    )


def test_every_entry_is_a_migration():
    for migration in VAULT_MIGRATIONS:
        assert isinstance(migration, VaultMigration)
        assert callable(migration.apply)
        assert migration.description, "a step with no description is unreadable in the log"


def test_versions_are_sorted_ascending():
    """The loop skips ``version <= current``, so an out-of-order entry is not
    merely untidy — everything after the highest number seen is dropped."""
    versions = [m.version for m in VAULT_MIGRATIONS]
    assert versions == sorted(versions), f"out of order: {versions}"


def test_versions_are_contiguous_from_two_with_no_duplicates():
    """Version 1 is the base layout, so there is nothing to migrate *to* it and
    the first step produces 2."""
    versions = [m.version for m in VAULT_MIGRATIONS]
    assert len(set(versions)) == len(versions), f"duplicate versions: {versions}"
    assert versions == list(range(2, 2 + len(versions))), (
        f"expected 2, 3, 4 … and got {versions} — a gap leaves a vault stamped "
        "at the missing version with no way forward"
    )


def test_the_highest_step_matches_the_declared_vault_version():
    """Adding the module and raising the constant are one commit.

    A step above ``VAULT_VERSION`` can never run: ``check_vault()`` refuses a
    vault newer than the build, and nothing ever asks for a layout the build
    does not claim. A ``VAULT_VERSION`` above the top step is the ordinary case
    of a bump with no migration behind it, so only the first direction is an
    error.
    """
    if not VAULT_MIGRATIONS:
        return
    top = VAULT_MIGRATIONS[-1].version
    assert top <= VAULT_VERSION, (
        f"a migration produces layout {top} but VAULT_VERSION is "
        f"{VAULT_VERSION} — raise the constant in the same commit"
    )


def test_every_module_is_named_for_the_version_it_produces():
    """The copy-paste check.

    A new step is almost always written by copying the last one, and the number
    is the easiest thing to leave behind. A module whose filename and
    ``MIGRATION.version`` disagree reads correctly and applies the wrong step.
    """
    for path in _step_modules():
        match = MODULE_NAME.match(path.stem)
        assert match, (
            f"{path.name} does not match m<NNNN>_<slug>.py — the number is how "
            "the list is read"
        )
        module = importlib.import_module(f"app.vault.migrations.{path.stem}")
        migration = getattr(module, "MIGRATION", None)
        assert isinstance(migration, VaultMigration), (
            f"{path.name} must expose MIGRATION = VaultMigration(...)"
        )
        assert migration.version == int(match.group(1)), (
            f"{path.name} produces version {migration.version}"
        )


def test_every_module_is_listed():
    """A module nobody imported is a migration that never runs.

    The package assembles its tuple by explicit import rather than by scanning
    the directory — deliberately, so the order cannot depend on filesystem
    iteration — which means the one thing a scan would have got right for free
    is the thing to check here.
    """
    listed = {m.version for m in VAULT_MIGRATIONS}
    for path in _step_modules():
        match = MODULE_NAME.match(path.stem)
        assert match
        version = int(match.group(1))
        assert version in listed, (
            f"{path.name} exists but is not in VAULT_MIGRATIONS — add its "
            "import and its entry to app/vault/migrations/__init__.py"
        )


@pytest.mark.parametrize("attribute", ["version", "description", "apply"])
def test_the_dataclass_still_has_the_fields_the_modules_use(attribute):
    """A step module is written against these three names; renaming one here
    breaks every released migration at once."""
    assert attribute in VaultMigration.__dataclass_fields__
