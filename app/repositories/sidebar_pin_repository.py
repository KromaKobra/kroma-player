"""Sidebar-sized reads: no ORM relationship walks or whole-library hydration."""
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models.playlist import Playlist, PlaylistTrack
from app.models.sidebar_pin import SidebarPin
from app.models.track import Album, Artist, Track, track_albums_association, track_artists_association


TARGETS = {
    "track": (SidebarPin.track_id, Track),
    "album": (SidebarPin.album_id, Album),
    "artist": (SidebarPin.artist_id, Artist),
    "playlist": (SidebarPin.playlist_id, Playlist),
}


class SidebarPinRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def pin(self, kind: str, content_id: int) -> bool:
        column, model = TARGETS[kind]
        # Read the next order inside the INSERT. No read/modify/write window,
        # and a duplicate never changes its existing position.
        position = select(func.coalesce(func.max(SidebarPin.position) + 1, 0)).scalar_subquery()
        stmt = insert(SidebarPin).from_select(
            [column.name, "position"],
            select(model.id, position).where(model.id == content_id),
        ).on_conflict_do_nothing(index_elements=[column])
        try:
            result = self.session.execute(stmt)
            self.session.commit()
            return result.rowcount > 0
        except Exception:
            self.session.rollback()
            raise

    def unpin(self, kind: str, content_id: int) -> bool:
        column, _ = TARGETS[kind]
        try:
            result = self.session.execute(delete(SidebarPin).where(column == content_id))
            self.session.commit()
            return result.rowcount > 0
        except Exception:
            self.session.rollback()
            raise

    def summaries(self) -> list[dict]:
        pins = self.session.execute(
            select(
                SidebarPin.track_id, SidebarPin.album_id, SidebarPin.artist_id, SidebarPin.playlist_id,
                Track.title, Track.cover_hash.label("track_cover"),
                Album.name.label("album_name"), Album.cover_hash.label("album_cover"),
                Artist.name.label("artist_name"), Artist.image_hash,
                Playlist.name.label("playlist_name"), Playlist.cover_hash.label("playlist_cover"),
            )
            .outerjoin(Track, Track.id == SidebarPin.track_id)
            .outerjoin(Album, Album.id == SidebarPin.album_id)
            .outerjoin(Artist, Artist.id == SidebarPin.artist_id)
            .outerjoin(Playlist, Playlist.id == SidebarPin.playlist_id)
            .order_by(SidebarPin.position, SidebarPin.id)
        ).mappings().all()
        if not pins:
            return []

        # Each query is restricted by the pin table itself: its work grows with
        # the pinned content, and never with the number of unrelated tracks.
        credits = defaultdict(list)
        if any(p["track_id"] is not None for p in pins):
            ta = track_artists_association
            for tid, name in self.session.execute(
                select(ta.c.track_id, Artist.name).select_from(ta)
                .join(Artist, Artist.id == ta.c.artist_id)
                .where(ta.c.track_id.in_(
                    select(SidebarPin.track_id).where(SidebarPin.track_id.is_not(None))
                ))
                .order_by(ta.c.track_id, ta.c.position, ta.c.artist_id)
            ):
                credits[tid].append(name)

        counts = {}
        for kind, assoc, owner in (
            ("album", track_albums_association, track_albums_association.c.album_id),
            ("artist", track_artists_association, track_artists_association.c.artist_id),
            ("playlist", PlaylistTrack.__table__, PlaylistTrack.playlist_id),
        ):
            column, _ = TARGETS[kind]
            if any(p[column.name] is not None for p in pins):
                counts[kind] = dict(self.session.execute(
                    select(owner, func.count()).select_from(assoc)
                    .where(owner.in_(select(column).where(column.is_not(None))))
                    .group_by(owner)
                ).all())

        rows = []
        for p in pins:
            kind = next(k for k, (col, _) in TARGETS.items() if p[col.name] is not None)
            uid = p[kind + "_id"]
            title = p["title"] if kind == "track" else p[kind + "_name"]
            cover = p["image_hash"] if kind == "artist" else p[kind + "_cover"]
            if kind == "track":
                subtitle = ", ".join(credits[uid]) or "Track"
            else:
                count = counts.get(kind, {}).get(uid, 0)
                subtitle = f"{kind.title()} · {count} {'track' if count == 1 else 'tracks'}"
            rows.append({"contentType": kind, "contentId": uid, "title": title or "",
                         "subtitle": subtitle, "coverHash": cover or ""})
        return rows
