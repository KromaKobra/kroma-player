"""Database ViewModel — exposes library stats and import actions to QML."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.services.track_service import TrackService


class DatabaseViewModel(QObject):
    """Stats and actions for the Database tab."""

    stats_changed = Signal()

    def __init__(self, track_service: TrackService):
        super().__init__()
        self._track_service = track_service
        self._total:       int = 0
        self._holes:       int = 0
        self._track_mb:    float = 0.0
        self._vault_mb:    float = 0.0
        self._vault_path:  str = ""

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def _get_total(self) -> int:       return self._total
    def _get_holes(self) -> int:       return self._holes
    def _get_track_mb(self) -> float:  return self._track_mb
    def _get_vault_mb(self) -> float:  return self._vault_mb
    def _get_vault_path(self) -> str:  return self._vault_path

    total     = Property(int,   _get_total,      notify=stats_changed)
    holes     = Property(int,   _get_holes,      notify=stats_changed)
    trackMb   = Property(float, _get_track_mb,   notify=stats_changed)
    vaultMb   = Property(float, _get_vault_mb,   notify=stats_changed)
    vaultPath = Property(str,   _get_vault_path, notify=stats_changed)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot()
    def refresh(self):
        """Fetch all library stats from disk and database."""
        raw = self._track_service.get_library_stats()
        self._total      = raw.get("total", 0)
        self._holes      = raw.get("holes", 0)
        self._track_mb   = raw.get("track_bytes", 0) / (1024 * 1024)
        self._vault_mb   = raw.get("vault_bytes", 0) / (1024 * 1024)
        self._vault_path = raw.get("vault_path", "")
        self.stats_changed.emit()
