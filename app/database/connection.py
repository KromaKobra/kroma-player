"""Database connection management.

This module provides centralized SQLAlchemy database connection and session management.
It abstracts away SQLAlchemy details so the rest of the app doesn't need to know about
engines, sessions, or SQLAlchemy plumbing.

Key responsibilities:
1. Create and configure the SQLite engine
2. Enable foreign key constraints at the SQLite level
3. Initialize the database schema (via schema.py)
4. Provide a factory for creating sessions
5. Provide a context manager for transactional sessions

Design pattern:
- DatabaseManager is created once at app startup (in bootstrap.py)
- Services/repositories get a session from db_manager.create_session()
- For operations that need transactional semantics, use db_manager.session_scope()

Modules that should NOT import from here:
- Models (app/models/) - they define the schema, not use the manager
- This keeps the dependency graph clean and tests easier to write
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.schema import create_schema


@dataclass(frozen=True)
class DatabaseManager:
    """Centralized manager for SQLAlchemy database connections and sessions.

    This class encapsulates all database connection logic. It ensures that:
    - The database file and its directory are created if needed
    - SQLite pragma settings are applied (e.g., foreign key enforcement)
    - The schema is initialized on first connection
    - Sessions are created in a consistent way

    Attributes:
        db_path: Path to the SQLite database file
        engine: SQLAlchemy Engine that manages the connection pool
        session_factory: sessionmaker bound to the engine for creating sessions

    Usage:
        # Initialize at app startup
        db_manager = DatabaseManager.from_path(Path("/path/to/tracks.db"))

        # Create a session for a single operation
        session = db_manager.create_session()
        track = session.query(Track).get(1)
        session.close()

        # Use a transactional context for multiple operations
        with db_manager.session_scope() as session:
            track = Track(title="My Song")
            session.add(track)
            # Automatically commits on exit, or rolls back on error
    """

    db_path: Path
    engine: Engine
    session_factory: sessionmaker

    @classmethod
    def from_path(cls, db_path: Path) -> "DatabaseManager":
        """Create a DatabaseManager from a filesystem path.

        This is the primary factory method. It orchestrates the full initialization:
        1. Ensures the directory exists
        2. Creates a SQLAlchemy engine for SQLite
        3. Configures SQLite pragmas (e.g., foreign key constraints)
        4. Initializes the schema (creates tables if needed)
        5. Creates a session factory

        Args:
            db_path: Path to the SQLite database file (can be a new file).

        Returns:
            Fully initialized DatabaseManager.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}")
        _configure_sqlite(engine)
        create_schema(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        return cls(db_path=db_path, engine=engine, session_factory=session_factory)

    def create_session(self) -> Session:
        """Create a new SQLAlchemy session.

        Sessions are used to query, create, update, or delete records. Each session
        manages its own transaction. Use this for simple, fire-and-forget operations,
        or for explicit transaction control.

        Returns:
            A new SQLAlchemy Session instance. Caller is responsible for closing it.

        Example:
            session = db_manager.create_session()
            try:
                track = session.query(Track).get(1)
                # use track
            finally:
                session.close()
        """
        return self.session_factory()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Provide a transactional context manager for database operations.

        This context manager handles the full session lifecycle:
        - Creates a session
        - Yields it for use
        - Commits on success
        - Rolls back on error
        - Always closes the session

        This should be preferred when doing multi-step operations that need
        atomicity (all-or-nothing). If one operation fails, all are rolled back.

        Yields:
            A new SQLAlchemy Session instance.

        Example:
            with db_manager.session_scope() as session:
                track = Track(title="New Song", duration_seconds=180)
                session.add(track)
                # Commits automatically on exit
                # If any error occurs, everything is rolled back

        Raises:
            Any exception raised during the context is re-raised after rollback.
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def migrate_database(db_path: Path) -> int:
    """Create and migrate the database at *db_path*, then let go of it.

    Startup's half of what :meth:`DatabaseManager.from_path` does, run on its
    own so it can happen *before* the registry exists — after the vault has
    been snapshotted, and where a failure can still send the user back to the
    vault picker instead of taking the window down half-built. ``from_path``
    then runs ``create_schema`` again, which on a database already at
    ``SCHEMA_VERSION`` is a version read and nothing else.

    Returns the version the database ends on. Raises
    :class:`~app.database.schema.DatabaseTooNewError` for a database a newer
    build migrated, and whatever a failed migration raised otherwise.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _configure_sqlite(engine)
        return create_schema(engine)
    finally:
        # Every pooled connection closed, so nothing holds the file between
        # here and the engine bootstrap builds for the session.
        engine.dispose()


def _configure_sqlite(engine: Engine) -> None:
    """Configure SQLite pragmas for data integrity and concurrent reads.

    Two settings, for two reasons:

    - ``foreign_keys=ON`` enforces referential integrity, which the playlist
      tables rely on for their ``ON DELETE CASCADE``.
    - ``journal_mode=WAL`` lets readers run while a write is in progress. Track
      lists read pages of rows as the user scrolls; without WAL a download
      committing mid-scroll would block them. This also makes it safe to move
      page reads onto a worker thread.

    Args:
        engine: SQLAlchemy Engine to configure.

    Note:
        This must be called before any connections are made to the engine.
        We use SQLAlchemy's event system to set the pragmas on each new connection.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        """Apply the pragmas whenever a SQLite connection is created."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL is a property of the database file, so this only has to take
        # once, but setting it per connection is harmless and self-healing.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
