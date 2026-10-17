"""Ordered sidebar shortcuts, with database-enforced lifetime and uniqueness."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer

from app.models.track import Base


class SidebarPin(Base):
    __tablename__ = "sidebar_pins"

    id = Column(Integer, primary_key=True)
    position = Column(Integer, nullable=False, index=True)
    # A polymorphic (type, id) pair cannot have a foreign key in SQLite. Four
    # exclusive references give us real CASCADEs, including for bulk deletes.
    # UNIQUE permits multiple NULLs, but only one pin for each actual target.
    track_id = Column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"), unique=True)
    album_id = Column(Integer, ForeignKey("albums.id", ondelete="CASCADE"), unique=True)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), unique=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), unique=True)

    __table_args__ = (
        CheckConstraint(
            "(track_id IS NOT NULL) + (album_id IS NOT NULL) + "
            "(artist_id IS NOT NULL) + (playlist_id IS NOT NULL) = 1",
            name="ck_sidebar_pin_one_target",
        ),
        CheckConstraint("position >= 0", name="ck_sidebar_pin_position"),
    )
