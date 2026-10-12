"""Batched library metadata; painting a delegate never queries the database."""
from collections import defaultdict

from sqlalchemy import func, select

from app.albums.credits import contributing_artists, primary_artists
from app.models.playlist import PlaylistTrack
from app.models.track import Artist, Track, track_albums_association, track_artists_association


def artist_durations(session, rows: list[dict]) -> None:
    if not rows:
        return
    assoc = track_artists_association
    values = dict(session.execute(
        select(assoc.c.artist_id, func.coalesce(func.sum(Track.duration_seconds), 0))
        .join(Track, Track.id == assoc.c.track_id)
        .where(assoc.c.artist_id.in_([row["id"] for row in rows]))
        .group_by(assoc.c.artist_id)
    ).all())
    for row in rows:
        row["total_duration"] = values.get(row["id"], 0)


def album_metadata(session, rows: list[dict]) -> None:
    if not rows:
        return
    ids = [row["id"] for row in rows]
    assoc = track_albums_association
    credits = track_artists_association
    durations = defaultdict(float)
    dates = {}
    # Running order determines the temporary release date, not MIN(date).
    for album_id, duration, date in session.execute(
        select(assoc.c.album_id, Track.duration_seconds, Track.release_date)
        .join(Track, Track.id == assoc.c.track_id)
        .where(assoc.c.album_id.in_(ids))
        .order_by(assoc.c.album_id, assoc.c.position, assoc.c.track_id)
    ):
        durations[album_id] += duration or 0
        if date and album_id not in dates:
            dates[album_id] = date
    by_album = defaultdict(list)
    for album_id, artist_id, name in session.execute(
        select(assoc.c.album_id, Artist.id, Artist.name)
        .select_from(assoc)
        .join(credits, credits.c.track_id == assoc.c.track_id)
        .join(Artist, Artist.id == credits.c.artist_id)
        .where(assoc.c.album_id.in_(ids))
        .order_by(assoc.c.album_id, assoc.c.position, assoc.c.track_id,
                  credits.c.position, credits.c.artist_id)
    ):
        by_album[album_id].append({"artist_id": artist_id, "name": name or ""})
    for row in rows:
        row["total_duration"] = durations[row["id"]]
        row["release_date"] = dates.get(row["id"], 0)
        main = primary_artists(contributing_artists(by_album[row["id"]], row["track_count"]))
        row["primary_artists"] = ", ".join(artist["name"] for artist in main)


def playback_entries(session, kind: str, ids: list[int]) -> list[dict]:
    """One membership query; preserve order and first-source provenance on overlap."""
    if kind == "artist":
        assoc = track_artists_association
        stmt = (select(assoc.c.artist_id, assoc.c.track_id)
                .join(Track, Track.id == assoc.c.track_id)
                .where(assoc.c.artist_id.in_(ids))
                .order_by(Track.release_date.desc(), Track.id.desc()))
    elif kind == "album":
        assoc = track_albums_association
        stmt = (select(assoc.c.album_id, assoc.c.track_id)
                .where(assoc.c.album_id.in_(ids))
                .order_by(assoc.c.position, assoc.c.track_id))
    else:
        stmt = (select(PlaylistTrack.playlist_id, PlaylistTrack.track_id)
                .where(PlaylistTrack.playlist_id.in_(ids))
                .order_by(PlaylistTrack.position, PlaylistTrack.id))
    grouped = defaultdict(list)
    for entity_id, track_id in session.execute(stmt):
        grouped[entity_id].append(track_id)
    entries, seen = [], set()
    for uid in ids:
        for track_id in grouped[uid]:
            if track_id in seen:
                continue
            seen.add(track_id)
            entries.append({"id": track_id, "sourceType": kind, "sourceId": uid})
    return entries

