"""Genre repository - data access for Genre model.

This module provides specialized queries for the Genre model.

Responsibilities:
- Get genres by name
- Create new genres (with deduplication)
- Support genre lookup during track import/download

Design:
- Inherits generic CRUD from BaseRepository[Genre]
- Adds domain-specific queries for genre resolution

Relationship to other modules:
- Used by TrackService when resolving genre names during track creation
- Called during download flow to ensure genres are created/linked properly
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.models.track import Genre
from app.repositories.base_repository import BaseRepository


class GenreRepository(BaseRepository[Genre]):
    """Repository for genre data access.

    Provides queries specific to genres, primarily for resolving genre
    names during track creation/import.
    """

    def __init__(self, session: Session):
        """Initialize genre repository.

        Args:
            session: SQLAlchemy session for database access
        """
        super().__init__(session, Genre)

    def get_by_name(self, name: str) -> Optional[Genre]:
        """Get genre by exact name.

        Args:
            name: Genre name to search for (case-sensitive)

        Returns:
            The genre if found, None otherwise
        """
        return self.session.query(Genre).filter(Genre.name == name).first()

    def get_or_create(self, name: str) -> Genre:
        """Get existing genre or create a new one.

        This is the primary method for genre resolution during track creation.
        It ensures that the same genre name always maps to the same database
        entry, preventing duplicates.

        Args:
            name: Genre name (will be created if doesn't exist)

        Returns:
            The existing or newly created genre object
        """
        genre = self.get_by_name(name)
        if genre:
            return genre
        # Create new genre
        return self.create(name=name)
