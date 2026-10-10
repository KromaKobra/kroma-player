"""SQLAlchemy ORM models.

This module defines all the data models used in the application. These models are the
single source of truth for the database schema - when connection.py calls create_schema(),
it uses these models to create tables.

Model hierarchy:
- Track: Core entity, represents a music file in the library
- Artist: A person or group that created music
- Genre: A category of music
- VersionType: Variations of a track (e.g., "remix", "cover")

Relationships are many-to-many:
- One track can have multiple artists (and one artist can have multiple tracks)
- One track can be in multiple genres (and one genre can have multiple tracks)
- One track can have multiple version types (and one version can apply to multiple tracks)

Association tables:
These bridge tables handle the many-to-many relationships. SQLAlchemy manages them
automatically when you use relationship() on the models.

Key principle:
Database schema is declarative and model-driven. To change the schema, modify the
models in this file, and connection.py's create_schema() will handle it.
"""
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Float, Index, Integer, PrimaryKeyConstraint, String, ForeignKey,
    Table, text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from app.utils.library_paths import track_path

Base = declarative_base()

# ============================================================================
# Association Tables (many-to-many join tables)
# ============================================================================

track_artists_association = Table(
    'track_artists', Base.metadata,
    Column('track_id', Integer, ForeignKey('tracks.id'), primary_key=True),
    Column('artist_id', Integer, ForeignKey('artists.id'), primary_key=True),
    # Where this artist sits in *this track's* credits, 0 first.
    #
    # A per-track fact, not a fact about the artist: the same two names lead on
    # one track and support on another, and a track's credit order is the one
    # thing about it a global ordering can never express. Before this column the
    # read paths fell back on `artists.id` — the order the artist joined the
    # *library* — which put whoever was imported first at the front of every
    # track they appear on.
    #
    # The relationship below orders by it, but SQLAlchemy cannot *write* a
    # column of a `secondary` table: see TrackRepository.write_artist_order,
    # which every path that assigns artists calls once the collection is
    # flushed.
    Column('position', Integer, nullable=False, server_default=text("0")),
)

track_albums_association = Table(
    'track_albums', Base.metadata,
    # ON DELETE CASCADE, unlike track_artists, which has none. Not an
    # inconsistency worth fixing there: adding it to an existing table means a
    # full rebuild, whereas this table is new and the constraint is free. SQLite
    # enforces it because the connection sets PRAGMA foreign_keys=ON — see
    # app/database/connection.py.
    Column('track_id', Integer, ForeignKey('tracks.id', ondelete='CASCADE'), primary_key=True),
    Column('album_id', Integer, ForeignKey('albums.id', ondelete='CASCADE'), primary_key=True),
    # Two orderings, on two different axes, because a track and an album each
    # have an opinion about the other and they are not the same opinion.
    #
    # `position` is the *album's*: where this track sits in its running order,
    # 0 first. This is the one the user drags by the number column.
    Column('position', Integer, nullable=False, server_default=text("0")),
    # `album_position` is the *track's*: where this album sits in this track's
    # own list of albums, 0 first — and 0 is what "primary album" means.
    #
    # A track can be on its original release and on three compilations. One of
    # those is the album it belongs to, and that is the one whose artwork it
    # carries and whose name the `album` column shows. Recording that as an
    # order rather than a flag is what makes it unrepresentable for a track to
    # have two primaries, or none while it has albums; a `primary_album_id`
    # column on `tracks` would be a second copy of a fact this table already
    # holds, and the two could disagree.
    #
    # Same caveat as track_artists.position: SQLAlchemy orders by these but
    # cannot *write* a column of a `secondary` table. See
    # TrackRepository.write_album_positions.
    Column('album_position', Integer, nullable=False, server_default=text("0")),
    Index('ix_track_albums_album', 'album_id'),
)

track_genres_association = Table(
    'track_genres', Base.metadata,
    Column('track_id', Integer, ForeignKey('tracks.id'), primary_key=True),
    Column('genre_id', Integer, ForeignKey('genres.id'), primary_key=True)
)

track_versions_association = Table(
    'track_versions', Base.metadata,
    Column('track_id', Integer, ForeignKey('tracks.id'), primary_key=True),
    Column('version_type_id', Integer, ForeignKey('version_types.id'), primary_key=True)
)

class Track(Base):
    """Track model - represents a music file in the library.

    A track is the core entity. It contains metadata about a song:
    - Basic info: title, album, duration, release date
    - Audio quality markers: BPM, musical key
    - Relationships: artists (who created it), genres, version types

    Relationships:
    - artists: Many-to-many. One track can have multiple artists.
    - genres: Many-to-many. One track can be in multiple genres.
    - version_types: Many-to-many. One track can have multiple versions (remix, cover, etc).

    Storage:
    - The Track's `id` is used as the primary key in the database
    - The `id` maps to a file location via library_paths.track_path()
    - Audio file stored as: {category}/{shard}/{ID}.opus
    - Example: ~/Music/venomvault/mix/0001-0100/0042.opus

    Lifecycle:
    1. Track is created with metadata
    2. Inserted into database (gets an auto-generated ID)
    3. Audio file is moved to the path corresponding to that ID
    4. Track is now discoverable by search/query
    """
    __tablename__ = 'tracks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    # No `album` column. The name lives on the `albums` row and nowhere else —
    # see the `album` property below, and _v4_albums in app/database/schema.py
    # for the migration that took the string away.
    release_date = Column(Integer)
    duration_seconds = Column(Integer, nullable=False)
    musical_key = Column(String(50))
    bpm = Column(Integer)
    copyright_text = Column(String)
    # No id columns. What a track is called in an online catalogue is kept in
    # `track_external_ids` — see TrackExternalId below and
    # app/plugins/external_ids.py, the only reader and writer.
    isrc = Column(String(15))
    # Content address of this track's cover artwork. Points into the disposable
    # thumbnail cache (covers.db) and is shared by tracks with identical art.
    cover_hash = Column(String(32))
    created_at = Column(Integer, default=lambda: int(datetime.utcnow().strftime("%Y%m%d")))
    # Level trim for this recording, in decibels, applied when it plays. Zero —
    # "as recorded" — for everything until someone says otherwise, which is why
    # it is NOT NULL with a constant default rather than nullable: a missing
    # correction and no correction are the same thing, and making them two
    # states would mean every reader handling both.
    #
    # It is not mirrored into the Opus file. See `_FIELD_TO_TAG` in
    # app/services/track_service.py for why.
    gain_db = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    
    # Ordered by the credit position, which is what makes `Track.artist` below —
    # and with it the player bar, the details panel, MPRIS, the export filename
    # and the file's ARTIST tag — read the track's own order rather than the
    # order SQLite happens to serve the join in.
    artists = relationship(
        'Artist',
        secondary=track_artists_association,
        back_populates='tracks',
        order_by=track_artists_association.c.position,
    )
    # Ordered by *this track's* view of its albums, so `albums[0]` is the
    # primary one — the release this recording belongs to, as opposed to the
    # compilations it also appears on. Everything that used to read the `album`
    # string reads that instead.
    albums = relationship(
        'Album',
        secondary=track_albums_association,
        back_populates='tracks',
        order_by=track_albums_association.c.album_position,
    )
    genres = relationship('Genre', secondary=track_genres_association, back_populates='tracks')
    version_types = relationship('VersionType', secondary=track_versions_association, back_populates='tracks')
    # Read and written through app/plugins/external_ids.py and nowhere else.
    # `passive_deletes` leaves deleting them to the database's ON DELETE
    # CASCADE rather than loading every id of a track just to delete it.
    external_id_rows = relationship(
        'TrackExternalId', cascade='all, delete-orphan', passive_deletes=True,
    )

    def __repr__(self):
        return f"<Track(id={self.id}, title='{self.title}')>"
    
    def to_dict(self) -> dict:
        """Convert track to dictionary (used for API responses and UI).

        Returns a simplified representation suitable for JSON serialization.
        """
        return {
            'id': self.id,
            'title': self.title,
            'album': self.album,
            'duration_seconds': self.duration_seconds,
            'artist': self.artist,
        }
    
    @property
    def artist(self) -> str:
        """Get comma-separated artist names for display.

        Returns all artists joined by ", ". Falls back to "Unknown Artist" if empty.
        This is a convenience property for the UI layer.
        """
        return ", ".join(a.name for a in self.artists) if self.artists else "Unknown Artist"

    @property
    def album(self) -> str:
        """The primary album's name, or "" when the track is on none.

        This was a column until albums became rows of their own. Keeping the
        name as a property rather than deleting it is what let every reader
        stay as it was — ``to_dict`` below, ``build_metadata`` in
        app/system/mpris_metadata.py, the exporter — while the writers moved to
        TrackService._resolve_albums.

        Empty string rather than "Unknown Album", unlike :attr:`artist`. A
        missing album is an ordinary fact about a single, and MPRIS omits the
        key entirely when it is falsy so clients do not draw a blank line.
        """
        return self.albums[0].name if self.albums else ""

    @property
    def album_id(self) -> int:
        """The primary album's id, or 0. What the details panel links to."""
        return int(self.albums[0].id) if self.albums else 0

    @property
    def file_path(self) -> str:
        """Canonical path to this track's Opus file on disk."""
        return str(track_path(self.id))

class TrackLyrics(Base):
    """The words to one track, as a document.

    Lyrics live in two places on purpose: here, and as the ``LYRICS`` Vorbis
    comment inside the track's own Opus file. The file copy is what travels with
    the audio; this one is what makes reading them cheap — before the table,
    showing the playing track's words cost a file open on the UI thread, and
    nothing could ask which tracks had lyrics at all.

    The table is therefore **rebuildable**: delete it and the one-off walk in
    :class:`~app.viewmodels.lyrics_view_model.LyricsViewModel` fills it from the
    files again. See
    ``docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md``.

    Storage:
        - ``track_id`` is both the foreign key and the primary key: a track has
          one set of lyrics or none, which makes every read a primary-key
          lookup and makes a second row for the same track unrepresentable.
        - ``ON DELETE CASCADE``, like ``PlaylistTrack``, so a deleted track
          takes its lyrics with it at the database level rather than depending
          on a service remembering to.

    ``content`` is either an LRC document (timestamped lines) or plain text.
    Which one is **not** stored: that is derivable from the document itself,
    and a stored flag is a second copy of a fact that can disagree with the
    first. ``app/lyrics/parse.py`` decides it at read time.

    ``source`` *is* stored, because it is not derivable — it says whether a row
    came from a file's own tag or from a lyric search. That answers the question
    the walk in ``app/lyrics/workers.py`` needs answered, and it is also what
    tells a write whether the file already has this text or still needs it.
    """
    __tablename__ = 'lyrics'

    track_id = Column(
        Integer, ForeignKey('tracks.id', ondelete='CASCADE'), primary_key=True
    )
    content = Column(String, nullable=False)
    source = Column(String(16), nullable=False)
    updated_at = Column(Integer, default=lambda: int(datetime.utcnow().strftime("%Y%m%d")))

    def __repr__(self):
        return f"<TrackLyrics(track_id={self.track_id}, source='{self.source}')>"


class Artist(Base):
    """Artist model - represents a person or group that created music.

    Artists are referenced by tracks via a many-to-many relationship.
    One artist can have multiple tracks, and one track can have multiple artists.

    Examples:
    - Solo artists: One track, one artist
    - Collaborations: One track, multiple artists
    - Artist discography: One artist, many tracks

    Storage:
    - Name is unique (prevents duplicate artist entries)
    - `external_id_rows` are what make an artist identifiable outside this
      library: given one, a plugin can always re-fetch the name and the
      portrait. See ArtistExternalId.
    - `image_hash` points into the same content-addressed cache as album art
      (covers.db). An artist portrait and an album cover are both just images
      keyed by their content, so they share one store and dedupe against
      each other.

    Note that unlike a track's artwork, a portrait has no copy embedded in a file
    on disk — an external id is what it can be rebuilt from instead.
    """
    __tablename__ = 'artists'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    image_hash = Column(String(32))
    tracks = relationship('Track', secondary=track_artists_association, back_populates='artists')
    # See Track.external_id_rows.
    external_id_rows = relationship(
        'ArtistExternalId', cascade='all, delete-orphan', passive_deletes=True,
    )
    def __repr__(self):
        return f"<Artist(id={self.id}, name='{self.name}')>"

class Album(Base):
    """Album model - a release a track belongs to.

    An album used to be a string on the track and nothing else, which meant two
    tracks were "on the same album" only because their spelling matched, and
    nothing could be said *about* the album — no artwork, no identity, no
    running order.

    Storage:
    - `name` is **deliberately not unique**, unlike Artist and Genre. Two
      different records really are called "Greatest Hits", and the split flow in
      AlbumService exists precisely to produce a second album with the same name
      and a different id. Identity here is the row id, not the name.
    - `external_id_rows` name the release in an online catalogue. Same role
      they play for an artist: given one, a plugin can always re-fetch the name
      and the cover. See AlbumExternalId.
    - `cover_hash` points into the same content-addressed store as
      `Track.cover_hash` and `Artist.image_hash` (covers.db), so an album cover
      and the track artwork it came from are one row rather than two.

    Where the artwork comes from, in order:
    1. If the album is identified, from the release it is identified as. That
       is authoritative — it is the release's own cover rather than whatever a
       particular file happened to be tagged with.
    2. Otherwise, seeded from the first track's `cover_hash`.

    That second case is why the tracks of an album are expected to agree on
    their artwork, and why disagreement is worth flagging. But only among the
    tracks this album is *primary* for: a track that merely appears on this
    compilation carries its own release's cover, correctly, and is not evidence
    of anything wrong. See AlbumService.
    """
    __tablename__ = 'albums'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    cover_hash = Column(String(32))

    # Whether this album holds the whole release, rather than some of it.
    #
    # The one fact about an album that is genuinely *not* derivable from the
    # library. `needs_attention` is recomputed in SQL every time it is asked
    # because everything it compares is already stored; completeness compares
    # this album against a release listing that lives in an online catalogue and
    # is never written down. So this is not a cached answer — it is the record that
    # a check happened and what it found, which is the only way the answer
    # survives the network call that produced it.
    #
    # NOT NULL with a constant default, for `gain_db`'s reason: an album nobody
    # has checked is not complete, and a reader that had to tell "false" from
    # "never asked" would have three states to draw where the user sees two.
    is_complete = Column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    # The album's own running order. `track_albums.position` is the album's
    # opinion of where each track sits; this is the relationship that reads it.
    tracks = relationship(
        'Track',
        secondary=track_albums_association,
        back_populates='albums',
        order_by=track_albums_association.c.position,
    )
    # See Track.external_id_rows.
    external_id_rows = relationship(
        'AlbumExternalId', cascade='all, delete-orphan', passive_deletes=True,
    )

    def __repr__(self):
        return f"<Album(id={self.id}, name='{self.name}')>"


# ============================================================================
# External ids
# ============================================================================

class _ExternalIdRow:
    """One id a row of the library is known by in an online catalogue.

    The core keeps these and does not understand them. ``provider`` and
    ``kind`` are whatever a plugin declared in its manifest, and ``value`` is an
    opaque string. What a kind means — whether it is unique, which file tag
    carries it — comes from that declaration at the time it is used, never
    from here. See app/plugins/external_ids.py, the only reader and writer.

    One table per entity rather than one with an ``entity`` column, so that
    each row can have a real foreign key. ON DELETE CASCADE means a deleted
    track, artist or album takes its ids with it at the database level. Nothing
    else ever removes one: turning a plugin off only hides its ids.

    The primary key is ``(owner, provider, kind)``: one value per kind per row.
    It leads with the owner, so reading every id of a page of rows is an index
    range scan. The second index answers the other question, "who holds this
    value?", which is what lookups and the uniqueness check ask.

    Uniqueness *across* rows is not a constraint. Only some kinds are unique,
    and which ones depends on a plugin's declaration, so external_ids.write
    enforces it instead.

    Each subclass names its owner column after the table it points at
    (``track_id``, …) and maps it as ``owner_id``, so the code that reads them
    is written once for all three.
    """

    provider = Column(String(64), nullable=False)
    kind = Column(String(64), nullable=False)
    value = Column(String(255), nullable=False)

    def __repr__(self):
        return (
            f"<{type(self).__name__}(owner_id={self.owner_id}, "
            f"key='{self.provider}:{self.kind}', value='{self.value}')>"
        )


class TrackExternalId(_ExternalIdRow, Base):
    __tablename__ = 'track_external_ids'
    owner_id = Column(
        'track_id', Integer, ForeignKey('tracks.id', ondelete='CASCADE'), nullable=False
    )
    __table_args__ = (
        PrimaryKeyConstraint('track_id', 'provider', 'kind'),
        Index('ix_track_external_ids_value', 'provider', 'kind', 'value'),
    )


class ArtistExternalId(_ExternalIdRow, Base):
    __tablename__ = 'artist_external_ids'
    owner_id = Column(
        'artist_id', Integer, ForeignKey('artists.id', ondelete='CASCADE'), nullable=False
    )
    __table_args__ = (
        PrimaryKeyConstraint('artist_id', 'provider', 'kind'),
        Index('ix_artist_external_ids_value', 'provider', 'kind', 'value'),
    )


class AlbumExternalId(_ExternalIdRow, Base):
    __tablename__ = 'album_external_ids'
    owner_id = Column(
        'album_id', Integer, ForeignKey('albums.id', ondelete='CASCADE'), nullable=False
    )
    __table_args__ = (
        PrimaryKeyConstraint('album_id', 'provider', 'kind'),
        Index('ix_album_external_ids_value', 'provider', 'kind', 'value'),
    )


class Genre(Base):
    """Genre model - represents a category of music.

    Genres are descriptive tags attached to tracks via many-to-many relationship.
    One track can be in multiple genres (e.g., a song can be both "electronic" and "ambient").
    One genre can apply to many tracks.

    Examples:
    - Electronic, Rock, Jazz, Pop, Hip-Hop, Ambient, Techno, etc.

    Storage:
    - Name is unique (prevents duplicate genre entries)
    """
    __tablename__ = 'genres'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    tracks = relationship('Track', secondary=track_genres_association, back_populates='genres')
    def __repr__(self):
        return f"<Genre(id={self.id}, name='{self.name}')>"

class VersionType(Base):
    """VersionType model - represents variations or edits of a track.

    Version types describe how a track differs from the original.
    One track can have multiple versions (e.g., a track can be both "remix" and "VIP edit").
    One version type can apply to many tracks.

    Examples:
    - Remix, Acoustic, Live, VIP Edit, Remaster, Cover, Instrumental, etc.

    Storage:
    - Name is unique (prevents duplicate version entries)

    Use case:
    - Search/filter by version type (e.g., "show me all remixes")
    - Track curation and organization
    """
    __tablename__ = 'version_types'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    tracks = relationship('Track', secondary=track_versions_association, back_populates='version_types')
    def __repr__(self):
        return f"<VersionType(id={self.id}, name='{self.name}')>"
