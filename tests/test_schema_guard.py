"""A database a newer build migrated is refused before anything touches it.

Before this guard, a build behind the database found nothing to migrate — the
migrations it had recorded were in this build's future — and carried on, after
``create_all`` had already added this build's idea of any missing table to a
schema it did not understand. So the property under test is the order as much
as the refusal: nothing is written, not even by ``create_all``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import schema  # noqa: E402
from app.database.connection import DatabaseManager, migrate_database  # noqa: E402


def _future_database(path: Path, version: int) -> None:
    """A database some later build wrote: one table this build has never heard
    of, and a stamp past SCHEMA_VERSION."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE from_the_future (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO from_the_future (note) VALUES ('keep me')")
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


def _tables(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ))
    finally:
        conn.close()


def test_create_schema_refuses_a_newer_database_before_creating_anything(tmp_path):
    path = tmp_path / "tracks.db"
    _future_database(path, schema.SCHEMA_VERSION + 1)
    engine = create_engine(f"sqlite:///{path}")

    try:
        with pytest.raises(schema.DatabaseTooNewError) as caught:
            schema.create_schema(engine)
    finally:
        engine.dispose()

    assert caught.value.found == schema.SCHEMA_VERSION + 1
    assert caught.value.supported == schema.SCHEMA_VERSION
    assert _tables(path) == ["from_the_future"], "create_all must not have run"


def test_migrate_refuses_a_newer_database_too(tmp_path):
    """It used to treat "ahead of me" as "nothing to apply"."""
    path = tmp_path / "tracks.db"
    _future_database(path, schema.SCHEMA_VERSION + 5)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(schema.DatabaseTooNewError):
            schema.migrate(engine)
    finally:
        engine.dispose()


def test_the_manager_refuses_it_as_well(tmp_path):
    """The registry's own path into the database, should startup ever be skipped."""
    path = tmp_path / "tracks.db"
    _future_database(path, schema.SCHEMA_VERSION + 1)

    with pytest.raises(schema.DatabaseTooNewError):
        DatabaseManager.from_path(path)
    assert _tables(path) == ["from_the_future"]


def test_the_supported_version_is_read_when_the_check_runs(tmp_path, monkeypatch):
    """So a test can stand in for an older build by lowering it."""
    path = tmp_path / "tracks.db"
    assert migrate_database(path) == schema.SCHEMA_VERSION

    monkeypatch.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION - 1)
    with pytest.raises(schema.DatabaseTooNewError):
        migrate_database(path)


def test_create_schema_returns_the_version_it_ends_on(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tracks.db'}")
    try:
        assert schema.create_schema(engine) == schema.SCHEMA_VERSION
    finally:
        engine.dispose()


def test_migrate_database_builds_a_new_database_and_lets_go(tmp_path):
    path = tmp_path / "nested" / "tracks.db"

    assert migrate_database(path) == schema.SCHEMA_VERSION
    assert schema.read_schema_version(path) == schema.SCHEMA_VERSION
    # Disposed: on Windows a pooled connection left open would hold the file.
    path.rename(path.with_name("moved.db"))


def test_a_second_run_changes_nothing(tmp_path):
    path = tmp_path / "tracks.db"
    migrate_database(path)
    before = _tables(path)

    assert migrate_database(path) == schema.SCHEMA_VERSION
    assert _tables(path) == before


def test_reading_the_version_never_creates_a_database(tmp_path):
    path = tmp_path / "tracks.db"

    assert schema.read_schema_version(path) is None
    assert not path.exists()


def test_reading_the_version_of_something_that_is_not_a_database(tmp_path):
    path = tmp_path / "tracks.db"
    path.write_bytes(b"this is not sqlite" * 100)

    assert schema.read_schema_version(path) is None
