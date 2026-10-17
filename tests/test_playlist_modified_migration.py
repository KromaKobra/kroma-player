"""Existing playlist dates retain their history when precise edit times arrive."""
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import text

from app.database.connection import DatabaseManager
from app.database.schema import SCHEMA_VERSION, migrate
from app.repositories.playlist_repository import PlaylistRepository


def test_playlist_modified_dates_migrate_and_survive_repeated_migration(tmp_path):
    path = tmp_path / "tracks.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE playlists (
                id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL,
                description VARCHAR, cover_hash VARCHAR(32),
                created_at INTEGER, updated_at INTEGER
            )
        """)
        conn.executemany(
            "INSERT INTO playlists (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            [(1, "Older", 20261001, 20261002),
             (2, "Newer", 20261001, 20261003),
             (3, "No modification date", 20261001, None)],
        )
        conn.execute("PRAGMA user_version = 7")

    manager = DatabaseManager.from_path(path)
    try:
        with manager.create_session() as session:
            repo = PlaylistRepository(session)
            dates = {row["id"]: row["updated_at"] for row in repo.get_summaries()}
            expected = [int(datetime(2026, 10, day, tzinfo=timezone.utc).timestamp()) * 1_000_000
                        for day in (2, 3, 1)]
            assert dates == dict(zip((1, 2, 3), expected))
            assert repo.get_by_id(1).created_at == 20261001
            repo.rename_playlist(1, "Edited after migration")
            edited_at = repo.get_by_id(1).updated_at
            assert edited_at > max(expected)

        # Replay the migration even if its version stamp was lost.
        with manager.engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 7"))
        assert migrate(manager.engine) == SCHEMA_VERSION
        with manager.create_session() as session:
            assert PlaylistRepository(session).get_by_id(1).updated_at == edited_at
    finally:
        manager.engine.dispose()
