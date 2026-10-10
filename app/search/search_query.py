"""Search query models."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SearchOperator(Enum):
    """Comparison operators."""

    EQUALS = "="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    CONTAINS = "Contains"


class SearchField(Enum):
    """Searchable fields."""

    TITLE = "title"
    ARTIST = "artist"
    # Filter-only, and deliberately absent from the field catalog: a track credits
    # a *set* of artists, so there is no scalar for a delegate to read or a list to
    # sort by. It exists so a GUI can ask for one artist's catalogue with an
    # ordinary TrackQuery instead of needing a list model of its own.
    ARTIST_ID = "artistId"
    ALBUM = "album"
    # Filter-only, and absent from the field catalog for the same reason
    # ARTIST_ID is: a track can be on several albums, so there is no scalar here
    # for a delegate to read. It exists so the album page can ask for one
    # album's running order with an ordinary TrackQuery. Note that `album` in the
    # catalog is a different thing — the *primary* album's name, which is a
    # scalar and does sort.
    ALBUM_ID = "albumId"
    GENRE = "genre"
    BPM = "bpm"
    KEY = "key"
    DATE = "date"
    DURATION = "duration"
    URL = "url"
    ISRC = "isrc"
    STASHED_DATE = "stashed_date"


@dataclass
class AdvancedSearchFilter:
    """Single advanced search filter."""

    field: SearchField
    operator: SearchOperator
    value: Any


# ---------------------------------------------------------------------------
# String → enum helpers (used by SearchViewModel to parse QML filter dicts)
# ---------------------------------------------------------------------------

# Maps the string labels sent from QML to SearchField enum members.
FIELD_FROM_STRING: dict[str, SearchField] = {f.value: f for f in SearchField}

# Maps the operator strings used in QML dropdowns to SearchOperator members.
# "Equals" is an alias for "=" (the text-field label vs. the numeric-field label).
OPERATOR_FROM_STRING: dict[str, SearchOperator] = {
    op.value: op for op in SearchOperator
}
OPERATOR_FROM_STRING["Equals"] = SearchOperator.EQUALS


def parse_filter(raw: dict) -> Optional[AdvancedSearchFilter]:
    """Convert a raw QML filter dict to an AdvancedSearchFilter, or None if invalid."""
    field    = FIELD_FROM_STRING.get(raw.get("field", ""))
    operator = OPERATOR_FROM_STRING.get(raw.get("operator", ""))
    value    = raw.get("value")

    if field is None or operator is None:
        return None
    if value is None or str(value).strip() == "":
        return None

    return AdvancedSearchFilter(field=field, operator=operator, value=value)