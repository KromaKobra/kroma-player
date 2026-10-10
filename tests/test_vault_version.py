"""The vault's layout version: stamped, migrated, and refused when it is ahead.

The gap this closes is the one case that used to be silent. A vault written by a
newer build has a database whose ``user_version`` is ahead of this build's
``SCHEMA_VERSION``, so ``migrate()`` finds nothing to apply and returns happily,
and the application then runs against columns and files it knows nothing about.
Nothing anywhere said "stop".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backup.identity import ensure_vault_id, identity_path, read_document  # noqa: E402
from app.vault import vault_version as vv  # noqa: E402
from app.versions import APP_VERSION, VAULT_VERSION  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    """A bare vault: the metadata folder and nothing else."""
    root = tmp_path / "venomvault"
    (root / ".data").mkdir(parents=True)
    return root


def _write_identity(vault: Path, document: dict) -> None:
    identity_path(vault).write_text(json.dumps(document), encoding="utf-8")


# -- reading -------------------------------------------------------------


def test_an_unstamped_vault_reads_as_version_one(vault):
    """Stamping began at 1, so a vault that predates it is a version 1 vault.

    Reading it as 0 instead would make every library in existence look older
    than the oldest supported layout the moment MIN_VAULT_VERSION ever moves.
    """
    assert vv.read_vault_version(vault) == 0
    assert vv.effective_version(vault) == 1
    assert vv.check_vault(vault) == ""


def test_a_nonsense_stamp_reads_as_unstamped(vault):
    for value in ("2", 0, -1, True, None, {"v": 1}):
        _write_identity(vault, {"vault_version": value})
        assert vv.read_vault_version(vault) == 0, value


def test_a_stamped_vault_reads_back_what_was_written(vault):
    _write_identity(vault, {"vault_version": VAULT_VERSION})
    assert vv.read_vault_version(vault) == VAULT_VERSION


# -- checking ------------------------------------------------------------


def test_a_current_vault_is_usable(vault):
    _write_identity(vault, {"vault_version": VAULT_VERSION})
    assert vv.check_vault(vault) == ""


def test_a_vault_from_the_future_is_refused_with_a_reason(vault):
    _write_identity(vault, {"vault_version": VAULT_VERSION + 1})

    problem = vv.check_vault(vault)

    assert problem, "a newer vault must not open silently"
    assert str(VAULT_VERSION + 1) in problem
    assert vault.name in problem, "the message names the vault the user picked"
    assert "Update Kroma Player" in problem or "different vault" in problem


def test_a_vault_too_old_to_upgrade_is_refused(vault, monkeypatch):
    """The other end of the range. Unreachable today, since MIN is 1 and an
    unstamped vault reads as 1 — so it is pinned by moving MIN."""
    monkeypatch.setattr(vv, "MIN_VAULT_VERSION", 3)
    monkeypatch.setattr(vv, "VAULT_VERSION", 4)
    _write_identity(vault, {"vault_version": 2})

    problem = vv.check_vault(vault)

    assert problem
    assert "older release" in problem or "no longer" in problem


# -- migrating -----------------------------------------------------------


def test_an_unstamped_vault_gets_stamped(vault):
    assert vv.migrate_vault(vault) == VAULT_VERSION
    assert vv.read_vault_version(vault) == VAULT_VERSION


def test_stamping_does_not_disturb_the_vault_id(vault):
    """Two numbers, one file. The layout stamp and the identity are written by
    different modules at different moments, and neither may drop the other."""
    identity = ensure_vault_id(vault)
    vv.migrate_vault(vault)

    document = read_document(vault)
    assert document["vault_id"] == identity
    assert document["vault_version"] == VAULT_VERSION
    assert document["last_opened_by"] == APP_VERSION


def test_a_vault_created_before_it_has_an_id_keeps_its_stamp(vault):
    """The startup order: migrate_vault() runs before ensure_vault_id()."""
    vv.migrate_vault(vault)
    ensure_vault_id(vault)

    document = read_document(vault)
    assert document["vault_version"] == VAULT_VERSION
    assert document["vault_id"]


def test_a_current_vault_is_not_migrated_again(vault, monkeypatch):
    applied: list[Path] = []
    monkeypatch.setattr(vv, "VAULT_VERSION", 2)
    monkeypatch.setattr(vv, "_migrations", lambda: (
        vv.VaultMigration(2, "does something", applied.append),
    ))
    _write_identity(vault, {"vault_version": 2})

    assert vv.migrate_vault(vault) == 2
    assert applied == [], "a vault already at the target must not be touched"


def test_a_pending_migration_runs_and_restamps(vault, monkeypatch):
    applied: list[Path] = []
    monkeypatch.setattr(vv, "VAULT_VERSION", 2)
    monkeypatch.setattr(vv, "_migrations", lambda: (
        vv.VaultMigration(2, "does something", applied.append),
    ))
    _write_identity(vault, {"vault_version": 1})

    assert vv.migrate_vault(vault) == 2
    assert applied == [vault]
    assert vv.read_vault_version(vault) == 2


def test_each_step_is_stamped_as_it_lands(vault, monkeypatch):
    """An interrupted upgrade leaves the vault on the last layout that fully
    applied, not somewhere between two."""
    monkeypatch.setattr(vv, "VAULT_VERSION", 3)

    def explode(_: Path) -> None:
        raise RuntimeError("interrupted")

    monkeypatch.setattr(vv, "_migrations", lambda: (
        vv.VaultMigration(2, "lands", lambda _: None),
        vv.VaultMigration(3, "does not", explode),
    ))
    _write_identity(vault, {"vault_version": 1})

    with pytest.raises(RuntimeError):
        vv.migrate_vault(vault)

    assert vv.read_vault_version(vault) == 2


def test_a_read_only_vault_does_not_stop_the_app(vault, monkeypatch):
    """A vault that cannot be written to is a real situation — a backup mounted
    read-only. Losing the stamp is not a reason to refuse to start."""
    from app.backup import identity

    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(identity, "write_json_atomic", refuse)

    assert vv.migrate_vault(vault) == VAULT_VERSION
