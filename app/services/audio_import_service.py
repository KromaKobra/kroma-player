"""Track storage and path resolution service."""

from pathlib import Path
import logging

from app.models.track import Track
from app.utils.library_paths import DEFAULT_CATEGORY, track_path

logger = logging.getLogger(__name__)


class AudioImportService:  # Not sure why this is a class when everything is static methods
    """Service responsible for resolving track file locations inside the VenomVault music library.

    Every method takes a ``category`` naming which variant of the recording is
    wanted — ``"mix"`` (the default, and what every caller predating backing
    tracks means) or ``"bt"`` for the instrumental.
    """

    @staticmethod
    def get_track_path(track_id: int, category: str = DEFAULT_CATEGORY) -> Path:
        """Get the file path for a given track ID."""

        if track_id <= 0:
            raise ValueError("track_id must be greater than 0")

        return track_path(track_id, category=category)

    @staticmethod
    def track_exists(track_id: int, category: str = DEFAULT_CATEGORY) -> bool:
        """Check whether a track file exists."""
        return track_path(track_id, category=category).exists()

    @staticmethod
    def get_track_path_str(track_id: int, category: str = DEFAULT_CATEGORY) -> str:
        """Get track path as string."""
        return str(AudioImportService.get_track_path(track_id, category))

    @staticmethod
    def resolve_track(track: Track, category: str = DEFAULT_CATEGORY) -> Path:
        """
        Resolve a Track ORM object to its file path.
        """
        return AudioImportService.get_track_path(track.id, category) # type: ignore

    @staticmethod
    def resolve_track_str(track: Track, category: str = DEFAULT_CATEGORY) -> str:
        """
        Resolve a Track ORM object to a string file path.
        """
        return str(AudioImportService.resolve_track(track, category))
