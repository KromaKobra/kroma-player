"""Getting a vault open: refuse what this build cannot read, copy before migrating,
and say what happened when something goes wrong.

Each test builds a real vault in ``tmp_path`` with a real ``tracks.db``, because
the properties worth having are about real files: that a refused database is
left byte-for-byte as it was, that a snapshot opens and holds the same rows, and
that a failed upgrade leaves the one clean copy where the next attempt finds it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backup import layout  # noqa: E402
from app.backup.identity import update_document  # noqa: E402
from app.backup.plan import BackupOptions  # noqa: E402
from app.database import schema  # noqa: E402
from app.database.connection import migrate_database  # noqa: E402
from app.vault import startup  # noqa: E402
from app.versions import VAULT_VERSION  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "venomvault"
    (root / ".data").mkdir(parents=True)
    return root


def _db(vault: Path) -> Path:
    return vault / ".data" / "tracks.db"


def _library(vault: Path, *, version: int | None = None, artists=("Daft Punk", "Justice")) -> Path:
    """A real library database, optionally stamped back to *version*.

    Every migration inspects before it writes, so a database built at the
    current version and stamped back is one the migrations rerun over as no-ops
    — a pending upgrade that is safe to perform for real.
    """
    path = _db(vault)
    migrate_database(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.executemany("INSERT INTO artists (name) VALUES (?)", [(a,) for a in artists])
        if version is not None:
            conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    finally:
        conn.close()
    return path


def _version(path: Path) -> int | None:
    return schema.read_schema_version(path)


def _artists(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT name FROM artists ORDER BY name")]
    finally:
        conn.close()


def _snapshots(vault: Path) -> list[Path]:
    root = startup.snapshot_root(vault)
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _failing_migration(monkeypatch, error: Exception | None = None):
    """Give this build one more migration than the database has, which raises."""
    def boom(_conn):
        raise error or RuntimeError("disk on fire")

    monkeypatch.setattr(
        schema, "MIGRATIONS",
        (*schema.MIGRATIONS, schema.Migration(schema.SCHEMA_VERSION + 1, "boom", boom)),
    )
    monkeypatch.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION + 1)


# ---------------------------------------------------------------------------
# Refusing
# ---------------------------------------------------------------------------


def test_a_database_from_a_newer_build_is_refused_and_left_alone(vault):
    path = _library(vault, version=schema.SCHEMA_VERSION + 3)
    before = path.read_bytes()
    sidecars = sorted(p.name for p in path.parent.iterdir())

    problem = startup.check_database(vault)

    assert "newer version of Kroma Player" in problem
    assert str(schema.SCHEMA_VERSION + 3) in problem
    assert vault.name in problem
    assert path.read_bytes() == before, "the refusal read the file and nothing else"
    assert sorted(p.name for p in path.parent.iterdir()) == sidecars


def test_open_problem_refuses_it_before_snapshotting_or_migrating(vault):
    path = _library(vault, version=schema.SCHEMA_VERSION + 1)
    before = path.read_bytes()

    problem = startup.open_problem(vault)

    assert "newer version" in problem
    assert path.read_bytes() == before
    assert _snapshots(vault) == []


def test_a_vault_with_no_database_yet_is_fine_and_stays_empty(vault):
    assert startup.check_database(vault) == ""
    assert not _db(vault).exists(), "checking must not create the database"


def test_a_current_database_passes(vault):
    _library(vault)
    assert startup.check_database(vault) == ""


def test_a_newer_layout_is_refused_before_the_database_is_looked_at(vault, monkeypatch):
    update_document(vault, vault_version=VAULT_VERSION + 1)
    monkeypatch.setattr(
        startup, "check_database", lambda _v: pytest.fail("looked at the database")
    )

    assert "newer version" in startup.open_problem(vault)


# ---------------------------------------------------------------------------
# What is pending
# ---------------------------------------------------------------------------


def test_a_new_vault_has_nothing_pending(vault):
    upgrade = startup.pending_upgrade(vault)

    assert upgrade.schema_from is None
    assert not upgrade.pending


def test_a_database_behind_this_build_is_pending(vault):
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    upgrade = startup.pending_upgrade(vault)

    assert upgrade.schema_from == schema.SCHEMA_VERSION - 1
    assert upgrade.schema_to == schema.SCHEMA_VERSION
    assert upgrade.schema_pending and upgrade.pending
    assert not upgrade.vault_pending


def test_a_layout_behind_this_build_is_pending(vault, monkeypatch):
    _library(vault)
    monkeypatch.setattr(startup, "VAULT_VERSION", VAULT_VERSION + 1)

    upgrade = startup.pending_upgrade(vault)

    assert upgrade.vault_pending and upgrade.pending
    assert not upgrade.schema_pending


def test_a_current_vault_has_nothing_pending(vault):
    _library(vault)
    assert not startup.pending_upgrade(vault).pending


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_a_snapshot_holds_the_database_and_the_documents(vault):
    path = _library(vault, version=schema.SCHEMA_VERSION - 1)
    (vault / ".data" / "settings.json").write_text('{"app": {"x": 1}}', encoding="utf-8")
    (vault / ".data" / "session.json").write_text("{}", encoding="utf-8")
    update_document(vault, vault_version=VAULT_VERSION)
    upgrade = startup.pending_upgrade(vault)

    folder = startup.snapshot_before_upgrade(vault, upgrade)

    data = folder / startup.SNAPSHOT_DATA_DIR
    assert _artists(data / "tracks.db") == _artists(path) == ["Daft Punk", "Justice"]
    assert _version(data / "tracks.db") == schema.SCHEMA_VERSION - 1, "as it was before"
    assert (data / "settings.json").read_text(encoding="utf-8") == '{"app": {"x": 1}}'
    assert (data / "session.json").is_file()
    assert (data / "vault.json").is_file()
    assert (folder / startup.SNAPSHOT_README).is_file()

    manifest = json.loads((folder / startup.SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema"] == {"from": schema.SCHEMA_VERSION - 1, "to": schema.SCHEMA_VERSION}
    assert manifest["vault_layout"] == {"from": VAULT_VERSION, "to": VAULT_VERSION}
    assert set(manifest["files"]) == {"tracks.db", "settings.json", "session.json", "vault.json"}
    assert manifest["created_by"]
    assert manifest["completed_at"] is None
    assert not any(p.name.endswith(".part") for p in folder.parent.iterdir())


def test_the_snapshot_is_a_single_file_database(vault):
    """Copied through SQLite, so a WAL sidecar's rows are in it and no -wal or
    -shm rides along to be recovered over it later."""
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    folder = startup.snapshot_before_upgrade(vault, startup.pending_upgrade(vault))

    names = {p.name for p in (folder / startup.SNAPSHOT_DATA_DIR).iterdir()}
    assert not any(name.endswith(("-wal", "-shm", "-journal", ".part")) for name in names)


def test_the_cover_cache_is_left_out(vault, monkeypatch):
    """A derived cache no migration touches, and possibly hundreds of MB."""
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    covers = vault / ".data" / "covers.db"
    conn = sqlite3.connect(str(covers))
    conn.execute("CREATE TABLE covers (hash TEXT)")
    conn.commit()
    conn.close()

    folder = startup.snapshot_before_upgrade(vault, startup.pending_upgrade(vault))
    assert not (folder / startup.SNAPSHOT_DATA_DIR / "covers.db").exists()

    monkeypatch.setattr(startup, "INCLUDE_COVERS", True)
    folder = startup.snapshot_before_upgrade(vault, startup.pending_upgrade(vault))
    assert (folder / startup.SNAPSHOT_DATA_DIR / "covers.db").is_file()


def test_snapshots_taken_in_the_same_second_do_not_collide(vault):
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    upgrade = startup.pending_upgrade(vault)

    first = startup.snapshot_before_upgrade(vault, upgrade)
    second = startup.snapshot_before_upgrade(vault, upgrade)

    assert first != second
    assert first.is_dir() and second.is_dir()


def test_a_snapshot_that_fails_leaves_nothing_half_written(vault, monkeypatch):
    _library(vault, version=schema.SCHEMA_VERSION - 1)

    def refuse(*_args):
        raise OSError("No space left on device")

    monkeypatch.setattr(startup, "snapshot_database", refuse)

    with pytest.raises(OSError):
        startup.snapshot_before_upgrade(vault, startup.pending_upgrade(vault))
    assert list(startup.snapshot_root(vault).iterdir()) == []


def _fake_snapshot(vault: Path, name: str, *, completed: bool = True) -> Path:
    folder = startup.snapshot_root(vault) / name
    folder.mkdir(parents=True)
    (folder / startup.SNAPSHOT_MANIFEST).write_text(json.dumps({
        "schema": {"from": 6, "to": 7},
        "vault_layout": {"from": 1, "to": 1},
        "completed_at": "2026-10-09T00:00:00+00:00" if completed else None,
    }), encoding="utf-8")
    return folder


def test_pruning_keeps_the_newest(vault):
    names = [f"2026100{i}T000000Z_schema-6-to-7" for i in range(1, 6)]
    for name in names:
        _fake_snapshot(vault, name)

    assert startup.prune_snapshots(vault, keep=3) == 2

    assert [p.name for p in _snapshots(vault)] == names[2:]


def test_pruning_clears_old_partial_snapshots_but_not_fresh_ones(vault):
    root = startup.snapshot_root(vault)
    stale = root / "20260101T000000Z_schema-6-to-7.part"
    fresh = root / "20261009T000000Z_schema-6-to-7.part"
    stale.mkdir(parents=True)
    fresh.mkdir()
    old = time.time() - startup.STALE_PART_SECONDS - 60
    os.utime(stale, (old, old))

    startup.prune_snapshots(vault, keep=3)

    assert not stale.exists()
    assert fresh.exists(), "may belong to another copy starting right now"


# ---------------------------------------------------------------------------
# Preparing: snapshot, then migrate
# ---------------------------------------------------------------------------


def test_an_upgrade_snapshots_first_then_migrates(vault):
    path = _library(vault, version=schema.SCHEMA_VERSION - 1)

    assert startup.prepare_vault(vault) == ""

    assert _version(path) == schema.SCHEMA_VERSION
    [folder] = _snapshots(vault)
    assert _version(folder / "data" / "tracks.db") == schema.SCHEMA_VERSION - 1
    manifest = json.loads((folder / startup.SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["completed_at"], "marked done once the upgrade landed"


def test_a_second_launch_takes_no_new_snapshot(vault):
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    startup.prepare_vault(vault)

    assert startup.prepare_vault(vault) == ""
    assert len(_snapshots(vault)) == 1


def test_a_new_vault_is_created_without_a_snapshot(vault):
    assert startup.prepare_vault(vault) == ""
    assert _version(_db(vault)) == schema.SCHEMA_VERSION
    assert _snapshots(vault) == []


def test_a_snapshot_that_cannot_be_taken_means_nothing_is_migrated(vault, monkeypatch):
    path = _library(vault, version=schema.SCHEMA_VERSION - 1)
    migrated: list[Path] = []
    monkeypatch.setattr(startup, "migrate_vault", lambda v: migrated.append(v))

    def refuse(*_args):
        raise OSError("No space left on device")

    monkeypatch.setattr(startup, "snapshot_database", refuse)

    problem = startup.prepare_vault(vault)

    assert "safety copy" in problem
    assert "No space left on device" in problem
    assert "Nothing was changed" in problem
    assert _version(path) == schema.SCHEMA_VERSION - 1
    assert migrated == []


def test_a_failed_migration_names_the_snapshot(vault, monkeypatch):
    path = _library(vault)
    _failing_migration(monkeypatch)

    problem = startup.prepare_vault(vault)

    [folder] = _snapshots(vault)
    assert str(folder) in problem
    assert "disk on fire" in problem
    assert "safe" in problem
    assert _version(path) == schema.SCHEMA_VERSION - 1, "the failing step rolled back"


def test_a_retry_reuses_the_snapshot_from_before_the_first_attempt(vault, monkeypatch):
    """Snapshotting again would copy half-migrated data, and after three
    attempts prune away the one clean copy."""
    _library(vault)
    _failing_migration(monkeypatch)

    startup.prepare_vault(vault)
    [first] = _snapshots(vault)
    for _ in range(4):
        assert startup.prepare_vault(vault)

    assert _snapshots(vault) == [first]


def test_once_the_retry_succeeds_the_next_upgrade_gets_its_own(vault, monkeypatch):
    _library(vault)
    _failing_migration(monkeypatch)
    startup.prepare_vault(vault)
    [first] = _snapshots(vault)

    # Fixed: the same version, now one that applies.
    monkeypatch.setattr(
        schema, "MIGRATIONS",
        (*schema.MIGRATIONS[:-1],
         schema.Migration(schema.SCHEMA_VERSION, "fixed", lambda _conn: None)),
    )
    assert startup.prepare_vault(vault) == ""
    assert _snapshots(vault) == [first]

    # A later release with one more migration: a new upgrade, a new snapshot.
    monkeypatch.setattr(
        schema, "MIGRATIONS",
        (*schema.MIGRATIONS,
         schema.Migration(schema.SCHEMA_VERSION + 1, "later", lambda _conn: None)),
    )
    monkeypatch.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION + 1)
    assert startup.prepare_vault(vault) == ""
    assert len(_snapshots(vault)) == 2


def test_a_database_that_turns_out_newer_is_refused_not_crashed(vault, monkeypatch):
    """check_database runs first, but prepare_vault does not rely on it."""
    _library(vault)

    def too_new(_path):
        raise schema.DatabaseTooNewError(99, schema.SCHEMA_VERSION)

    monkeypatch.setattr(startup, "migrate_database", too_new)

    assert "newer version" in startup.prepare_vault(vault)


def test_open_problem_on_a_good_vault_opens_it(vault):
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    assert startup.open_problem(vault) == ""
    assert _version(_db(vault)) == schema.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Never carried into a backup
# ---------------------------------------------------------------------------


def test_a_backup_never_includes_the_upgrade_snapshots(vault):
    """They live on the same disk as the library and are not a backup; copying
    them into one would only carry three databases along with every sync."""
    _library(vault, version=schema.SCHEMA_VERSION - 1)
    startup.prepare_vault(vault)
    assert _snapshots(vault), "there is something to leave out"

    everything = BackupOptions(tracks=True, backing=True, guis=True, models=True)
    walked = layout.walk(vault, everything)
    payload = layout.data_payload(vault)

    assert not any(startup.SNAPSHOT_ROOT in path for path in walked)
    assert not any(startup.SNAPSHOT_ROOT in path for path in payload)
    assert not any(startup.SNAPSHOT_ROOT in root for root in layout.selected_roots(everything))
