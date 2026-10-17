"""Database schema management.

This module is responsible for initializing and evolving the SQLite database schema.
The schema is defined declaratively via SQLAlchemy ORM models (app/models/track.py),
not through raw SQL. This keeps schema and code in sync.

Two mechanisms, because they answer different questions:

- ``Base.metadata.create_all()`` creates any table that does not exist yet. That is
  the whole story for a new vault, and it is why adding a *table* has never needed
  anything else.
- ``MIGRATIONS`` below evolves tables that already exist. ``create_all`` will not
  touch one — it checks for the table, finds it, and moves on, so a column added to
  a model simply never appears in anybody's existing library. Every schema change
  that is not a brand-new table goes here.

Why ``PRAGMA user_version`` rather than a migrations table: it is a single integer
in the database header, it costs nothing to read, and it needs no table of its own
to bootstrap. SQLite has carried it for exactly this purpose since forever.

Why each step is *also* individually idempotent: a freshly created database gets its
columns from ``create_all``, so the ALTER that would add them must be a no-op there
regardless of what ``user_version`` says. Correctness must not depend on the stamp
being right — the stamp is an optimisation that lets an up-to-date database skip the
inspection, not the thing that makes the migration safe.

To add a migration: append one ``Migration`` with the next version number. Never
renumber or edit a released one; a library in the wild has already recorded it.

**A database from a newer build is refused, never opened.** Its ``user_version``
is ahead of ``SCHEMA_VERSION``, so there is nothing here to apply — the
migrations it has recorded are in this build's future — and running against it
would mean writing rows into columns this build has never heard of, or missing
ones a later migration dropped. ``create_schema`` checks before ``create_all``
touches anything and raises :class:`DatabaseTooNewError`;
:mod:`app.vault.startup` turns that into a sentence in the vault picker.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.models.track import AlbumExternalId, ArtistExternalId, Base, TrackExternalId
from app.models.sidebar_pin import SidebarPin  # noqa: F401 — register the table
# Importing the playlist models registers their tables on the shared Base.metadata
# so create_all() below creates them too. (noqa: imported for side effect only.)
import app.models.playlist  # noqa: F401

logger = logging.getLogger(__name__)


class DatabaseTooNewError(RuntimeError):
    """The database was last migrated by a newer build than this one.

    ``found`` is the ``user_version`` on disk; ``supported`` is this build's
    ``SCHEMA_VERSION``. Both travel with the exception so the caller can say
    which build would open it without parsing a message.
    """

    def __init__(self, found: int, supported: int) -> None:
        super().__init__(
            f"database schema {found} is newer than this build supports ({supported})"
        )
        self.found = found
        self.supported = supported


@dataclass(frozen=True)
class Migration:
    """One schema evolution step.

    ``apply`` receives an open connection inside a transaction. It is expected to
    inspect before it writes: see ``add_column``.
    """

    version: int
    description: str
    apply: Callable[[Connection], None]


# ---------------------------------------------------------------------------
# Inspection helpers — the reason a step can run twice without complaining
# ---------------------------------------------------------------------------


def _columns(conn: Connection, table: str) -> set[str]:
    """The column names of *table*, or an empty set if it does not exist."""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def add_column(conn: Connection, table: str, column: str, ddl_type: str) -> bool:
    """``ALTER TABLE ... ADD COLUMN`` unless the column is already there.

    Returns True if it added one. SQLite's ADD COLUMN only rewrites the header, so
    this is cheap even on a large table — but it raises "duplicate column name"
    rather than being a no-op, which is what the check is for.

    Note that ADD COLUMN cannot express UNIQUE or a non-constant default. Anything
    needing those is enforced in the service layer instead.
    """
    if column in _columns(conn, table):
        return False
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    return True


# ---------------------------------------------------------------------------
# The migrations
# ---------------------------------------------------------------------------


def _v1_artist_identity(conn: Connection) -> None:
    """Give artists an identity of their own: a YouTube id and a portrait.

    ``track_artists`` is keyed ``(track_id, artist_id)``, so a lookup by artist
    alone — every track by this artist, and the orphan sweep — cannot use the
    primary key. The index is what makes the artist page a single indexed scan
    rather than a table walk.
    """
    add_column(conn, "artists", "youtube_id", "VARCHAR(64)")
    add_column(conn, "artists", "image_hash", "VARCHAR(32)")
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_track_artists_artist "
            "ON track_artists (artist_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_artists_youtube_id "
            "ON artists (youtube_id)"
        )
    )


def _v2_artist_credit_order(conn: Connection) -> None:
    """Give each track its own artist order, recovered from the rows it already has.

    The order was never stored, but it was never quite lost either: every path
    that credits a track inserts its artists in the order the source gave them,
    so physical row order in ``track_artists`` *is* the credit order. Reading it
    back with ``rowid`` recovers what the reads were throwing away.

    ``artist_id`` would be the obvious thing to number by and is exactly wrong —
    that is the order the artist joined the library, which is the ordering this
    migration exists to stop using.

    Guarded on ``add_column`` so the backfill runs once and only on a database
    that did not have the column. A second run must not renumber an order the
    user has since arranged by hand, and on a fresh database ``create_all`` has
    already made the column and there are no rows to number.
    """
    if not add_column(conn, "track_artists", "position", "INTEGER NOT NULL DEFAULT 0"):
        return
    conn.execute(
        text(
            "UPDATE track_artists SET position = ("
            "  SELECT COUNT(*) FROM track_artists t2"
            "   WHERE t2.track_id = track_artists.track_id"
            "     AND t2.rowid < track_artists.rowid"
            ")"
        )
    )


def _v3_track_gain(conn: Connection) -> None:
    """Give each track a level trim of its own, in decibels.

    ``DEFAULT 0`` is what makes this a one-line migration rather than a
    backfill: SQLite writes the default into every existing row as it adds the
    column, and zero decibels is exactly "play it as it was recorded". There is
    no earlier state to recover here the way there was for credit order — the
    correction is a judgement nobody has made yet.
    """
    add_column(conn, "tracks", "gain_db", "FLOAT NOT NULL DEFAULT 0")


def _v4_albums(conn: Connection) -> None:
    """Turn the free-text ``tracks.album`` string into rows of its own.

    ``albums`` and ``track_albums`` are brand-new tables, so ``create_all`` has
    already made them — and their indexes with them, because both are declared
    on the models. That is why, unlike ``_v1_artist_identity``, there is no DDL
    here. What is left is the part ``create_all`` cannot do: move the data, and
    take the old column away.

    **The guard is the old column's existence.** A database still carrying
    ``tracks.album`` has not been through this; one that is not, has. That makes
    the step idempotent without consulting ``user_version`` — and on a fresh
    vault, where the model never declared the column, the whole function is a
    no-op, which is exactly right since ``create_all`` left nothing to convert.

    **Grouping is by (name, lead artist), not by name alone.** Two unrelated
    records are both called "Greatest Hits", and merging them would produce one
    forty-track album out of two real ones — a mess the user then has to take
    apart by hand. Getting this wrong in the safe direction costs a split;
    getting it wrong in the other costs data nobody can reconstruct.

    **Order comes from ``tracks.id``**, the same recovery ``_v2_artist_credit_order``
    makes: the real running order was never stored, but tracks were imported in
    the order the source listed them, so the row order is the best evidence
    there is. Every backfilled track lands at ``album_position = 0`` because it
    has exactly one album — the string could only ever name one.
    """
    if "album" not in _columns(conn, "tracks"):
        return

    rows = conn.execute(
        text(
            "SELECT t.id, t.album, t.cover_hash, ("
            "  SELECT ta.artist_id FROM track_artists ta"
            "   WHERE ta.track_id = t.id"
            "   ORDER BY ta.position, ta.artist_id LIMIT 1"
            ") AS lead_artist "
            "FROM tracks t "
            "WHERE t.album IS NOT NULL AND TRIM(t.album) <> '' "
            "ORDER BY t.id"
        )
    ).all()

    # (lowercased name, lead artist id) -> the rows that belong together.
    groups: dict[tuple[str, object], list] = {}
    for row in rows:
        groups.setdefault((str(row[1]).strip().lower(), row[3]), []).append(row)

    for members in groups.values():
        # The first track's spelling wins the display name. Grouping folded case
        # so "Discovery" and "DISCOVERY" met; one of them has to be the label,
        # and the earliest import is as good an answer as exists.
        name = str(members[0][1]).strip()
        # The cover of the first track that has one — not simply the first
        # track's, which may be the one missing artwork entirely.
        cover = next((m[2] for m in members if m[2]), None)
        album_id = conn.execute(
            text("INSERT INTO albums (name, cover_hash) VALUES (:name, :cover)"),
            {"name": name, "cover": cover},
        ).lastrowid
        conn.execute(
            text(
                "INSERT INTO track_albums (track_id, album_id, position, album_position)"
                " VALUES (:track_id, :album_id, :position, 0)"
            ),
            [
                {"track_id": int(m[0]), "album_id": int(album_id), "position": i}
                for i, m in enumerate(members)
            ],
        )

    # SQLite grew DROP COLUMN in 3.35 (2021). Below that the only way to remove
    # one is to rebuild the table, and rebuilding `tracks` — the table every
    # foreign key in the library points at — to tidy away a column nothing reads
    # any more is a bad trade. The model no longer declares it either way, so an
    # old runtime just carries a dead column.
    version = tuple(
        int(part) for part in
        str(conn.execute(text("SELECT sqlite_version()")).scalar() or "0").split(".")[:3]
    )
    if version >= (3, 35, 0):
        conn.execute(text("ALTER TABLE tracks DROP COLUMN album"))
    else:
        logger.warning(
            "SQLite %s cannot DROP COLUMN; leaving the unused tracks.album in place",
            ".".join(str(p) for p in version),
        )


def _v5_album_completeness(conn: Connection) -> None:
    """Let an album record that it holds the whole release.

    One line for ``_v3_track_gain``'s reason: ``DEFAULT 0`` is written into every
    existing row as the column arrives, and "nobody has checked this album yet"
    and "this album is not the whole record" are the same thing as far as a
    checkmark is concerned. Nothing can be backfilled here even in principle —
    the answer lives in an online catalogue, not in the library.
    """
    add_column(conn, "albums", "is_complete", "BOOLEAN NOT NULL DEFAULT 0")



def _v6_instrumental_youtube_id(conn: Connection) -> None:
    """Remember which YouTube video a track's instrumental came from.

    Nullable with nothing to backfill: an instrumental already in ``bt/`` was
    imported from a file or generated, and neither has a video behind it.

    Deliberately a column of its own rather than a second value in
    ``youtube_id``. That one answers web search's "you already have this", which
    only works while one video id names one track — and an instrumental is not
    the track, it is a second file for it.
    """
    add_column(conn, "tracks", "instrumental_youtube_id", "VARCHAR(32)")


# What the id columns held before ids had tables of their own, as
# (table, column, provider, kind). The keys are the ones the plugin that
# understands these ids declares, so it finds them where it expects them the
# first time it is enabled. This is history: it says what the old columns held.
# It is not a list of kinds the core supports. The core supports whatever a
# plugin declares.
_LEGACY_ID_COLUMNS = (
    ("tracks", "youtube_id", "youtube", "video"),
    ("tracks", "instrumental_youtube_id", "youtube", "instrumental-video"),
    ("tracks", "spotify_id", "spotify", "track"),
    ("artists", "youtube_id", "youtube", "channel"),
    ("albums", "youtube_id", "youtube", "release"),
)


def _v7_external_id_tables(conn: Connection) -> None:
    """Move every id out of its fixed column into the external-id tables.

    The core used to have one column per catalogue, so it knew which catalogues
    exist. Now it keeps ``(owner, provider, kind, value)`` rows and knows none of
    them; plugins declare the kinds. See ``_ExternalIdRow`` in
    app/models/track.py.

    **Nothing is lost.** Every non-empty value is copied first, and the column is
    dropped after. ``INSERT OR IGNORE`` makes the copy safe to repeat: the
    primary key is one value per kind per row, and a row that is already there
    is left alone. Values are trimmed, as the service layer always did when it
    wrote them. Duplicates across rows are copied as they are. Uniqueness was
    never a constraint on the columns, and deciding which of two tracks keeps a
    shared id is not something a migration can do safely.

    **The guard is each old column's existence**, as in ``_v4_albums``. A fresh
    vault never had the columns, so this does nothing there. The tables
    themselves come from ``create_all``, but they are created here too when
    missing, so the copy cannot depend on the order a caller set things up in.

    The two indexes on the old columns are dropped first, because SQLite will
    not drop an indexed column. Where DROP COLUMN does not exist (before 3.35)
    the columns stay as dead weight, for ``_v4_albums``' reason: rebuilding
    ``tracks`` to tidy them away is the worse trade.
    """
    targets = {
        "tracks": (TrackExternalId.__table__, "track_id"),
        "artists": (ArtistExternalId.__table__, "artist_id"),
        "albums": (AlbumExternalId.__table__, "album_id"),
    }
    for target, _owner in targets.values():
        target.create(conn, checkfirst=True)

    present = {
        (table, column)
        for table, column, _provider, _kind in _LEGACY_ID_COLUMNS
        if column in _columns(conn, table)
    }
    for table, column, provider, kind in _LEGACY_ID_COLUMNS:
        if (table, column) not in present:
            continue
        target, owner = targets[table]
        conn.execute(
            text(
                f"INSERT OR IGNORE INTO {target.name} ({owner}, provider, kind, value) "
                f"SELECT id, :provider, :kind, TRIM({column}) FROM {table} "
                f"WHERE {column} IS NOT NULL AND TRIM({column}) <> ''"
            ),
            {"provider": provider, "kind": kind},
        )

    conn.execute(text("DROP INDEX IF EXISTS ix_artists_youtube_id"))
    conn.execute(text("DROP INDEX IF EXISTS ix_albums_youtube_id"))

    version = tuple(
        int(part) for part in
        str(conn.execute(text("SELECT sqlite_version()")).scalar() or "0").split(".")[:3]
    )
    for table, column in sorted(present):
        if version >= (3, 35, 0):
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        else:
            logger.warning(
                "SQLite %s cannot DROP COLUMN; leaving the unused %s.%s in place",
                ".".join(str(p) for p in version), table, column,
            )


def _v8_playlist_modified_time(conn: Connection) -> None:
    """Convert playlist modification dates to Unix microseconds.

    Old dates have day precision only; retain that history at midnight UTC.
    Precise timestamps are already larger than any YYYYMMDD date, so this is
    safe to repeat and never overwrites a subsequent edit.
    """
    conn.execute(text(
        "UPDATE playlists SET updated_at = COALESCE("
        "CAST(strftime('%s', "
        "substr(COALESCE(updated_at, created_at), 1, 4) || '-' || "
        "substr(COALESCE(updated_at, created_at), 5, 2) || '-' || "
        "substr(COALESCE(updated_at, created_at), 7, 2)"
        ") AS INTEGER) * 1000000, 0) "
        "WHERE updated_at IS NULL OR updated_at BETWEEN 10000101 AND 99991231"
    ))


MIGRATIONS: Sequence[Migration] = (
    Migration(1, "artist youtube_id + image_hash, artist lookup indexes", _v1_artist_identity),
    Migration(2, "per-track artist credit order", _v2_artist_credit_order),
    Migration(3, "per-track playback gain", _v3_track_gain),
    Migration(4, "albums as rows, backfilled from the track album string", _v4_albums),
    Migration(5, "album completeness flag", _v5_album_completeness),
    Migration(6, "per-track instrumental YouTube id", _v6_instrumental_youtube_id),
    Migration(7, "external ids move from fixed columns to tables of their own", _v7_external_id_tables),
    Migration(8, "precise playlist modification times", _v8_playlist_modified_time),
)

SCHEMA_VERSION: int = MIGRATIONS[-1].version if MIGRATIONS else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_schema_version(db_path: Path) -> int | None:
    """The ``user_version`` stamped on the database at *db_path*, or ``None``.

    ``None`` when there is no database there yet, or it cannot be read. Opened
    with ``mode=rw`` so a path that does not exist stays that way: a plain
    ``sqlite3.connect`` would create an empty file, and a check that runs before
    the vault has been accepted must not leave anything behind in it. Read-write
    rather than read-only because a read-only connection to a WAL database fails
    when nothing else holds it open, which before startup is always the case.
    """
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        logger.debug("Could not open %s to read its schema version: %s", db_path, exc)
        return None
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        logger.debug("Could not read the schema version of %s: %s", db_path, exc)
        return None
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _refuse_if_newer(current: int) -> None:
    """Raise when *current* is ahead of what this build knows.

    ``SCHEMA_VERSION`` is looked up when this runs, not when the module loaded,
    so a test can lower it to stand in for an older build.
    """
    if current > SCHEMA_VERSION:
        raise DatabaseTooNewError(current, SCHEMA_VERSION)


def create_schema(engine: Engine) -> int:
    """Initialize and migrate the database schema. Returns the version it ends on.

    Creates every table defined in Base.metadata that does not exist yet, then runs
    any migration this database has not recorded.

    Raises :class:`DatabaseTooNewError`, **before anything is written**, when the
    database was migrated by a newer build. ``create_all`` would otherwise run
    first and add this build's idea of a missing table to a schema it does not
    understand.

    Args:
        engine: SQLAlchemy Engine connected to the target database.

    Note:
        - Tables are created idempotently, and so is every migration step
        - Foreign key constraints are enforced by SQLite (see _configure_sqlite)
        - The source of truth for the schema is app/models/, not this function
    """
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            _refuse_if_newer(conn.execute(text("PRAGMA user_version")).scalar() or 0)
    Base.metadata.create_all(engine)
    return migrate(engine)


def migrate(engine: Engine) -> int:
    """Apply outstanding migrations. Returns the version the database ends on.

    Each step runs in its own transaction with its ``user_version`` stamp, so an
    interrupted run leaves the database on the last version that fully landed
    rather than half-way through one.

    Raises :class:`DatabaseTooNewError` for a database ahead of this build, which
    used to fall through as "nothing to apply" and carry on.
    """
    if engine.dialect.name != "sqlite":
        return 0

    with engine.connect() as conn:
        current = conn.execute(text("PRAGMA user_version")).scalar() or 0
    _refuse_if_newer(current)

    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        # A connection of its own per step, rather than one held open across all
        # of them. SQLAlchemy 2.0 autobegins a transaction on the first execute,
        # so a connection that has already answered "what version is this?" is
        # inside one and cannot start another.
        with engine.begin() as conn:
            migration.apply(conn)
            # PRAGMA takes no bound parameter, and the value is an int from the
            # table above rather than anything reaching us from outside.
            conn.execute(text(f"PRAGMA user_version = {int(migration.version)}"))
        logger.info(
            "Applied schema migration %d: %s", migration.version, migration.description
        )
        current = migration.version

    return current
