"""Pure conversions between Kroma's track model and the MPRIS2 wire format.

Deliberately free of Qt, D-Bus and I/O so the fiddly parts — unit conversion,
object-path escaping, partial dates — can be tested without a session bus, the
same way ``resolve_output_device`` in ``app.audio.qt_multimedia_backend`` is
testable without an audio stack.

The MPRIS2 metadata vocabulary is xesam plus a few ``mpris:`` keys:
https://www.freedesktop.org/wiki/Specifications/mpris-spec/metadata/
"""

from __future__ import annotations

from typing import Any

# Object path prefix for track ids. MPRIS requires ``mpris:trackid`` to be a
# valid D-Bus object path, not a number or a string.
TRACK_PATH_PREFIX = "/org/kroma/kord/track/"

# The spec's designated "no track" path. Sent as the trackid when nothing is
# loaded; clients treat it as "this is not a real track", and it is also the
# value SetPosition can never match, so a stale seek from a client that missed
# a track change is rejected rather than applied to the wrong song.
NO_TRACK_PATH = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

PLAYING = "Playing"
PAUSED = "Paused"
STOPPED = "Stopped"


def to_micros(seconds: float) -> int:
    """Convert seconds to the microseconds MPRIS uses for every time value."""
    return int(round(float(seconds) * 1_000_000))


def from_micros(micros: int) -> float:
    """Convert MPRIS microseconds back to the seconds the backend speaks."""
    return int(micros) / 1_000_000


def track_object_path(track_id: int | None) -> str:
    """Return the ``mpris:trackid`` object path for a track id.

    ``None`` yields the spec's NoTrack path. Ids are integers from the database
    primary key, so no escaping is needed — but the result is still built
    through here rather than inline, so ``SetPosition`` can compare against
    exactly the same string it handed out.
    """
    if track_id is None:
        return NO_TRACK_PATH
    return f"{TRACK_PATH_PREFIX}{int(track_id)}"


def track_id_from_path(path: str) -> int | None:
    """Inverse of :func:`track_object_path`; ``None`` if the path isn't ours."""
    if not isinstance(path, str) or not path.startswith(TRACK_PATH_PREFIX):
        return None
    try:
        return int(path[len(TRACK_PATH_PREFIX):])
    except ValueError:
        return None


def playback_status(is_playing: bool, has_track: bool) -> str:
    """Map Kroma's two booleans onto the MPRIS three-state PlaybackStatus.

    Kroma has no explicit "stopped" flag — it has a current track or it does
    not. Nothing loaded is Stopped; loaded but not advancing is Paused.
    """
    if not has_track:
        return STOPPED
    return PLAYING if is_playing else PAUSED


def iso_date(yyyymmdd: int | None) -> str | None:
    """Render Kroma's integer ``release_date`` as an ISO 8601 date.

    Dates are stored as a single ``yyyymmdd`` integer (see the ``date`` field in
    ``app.library.fields``), and real library data has partial ones — a year
    with no month, a month with no day — because that is all the source knew.
    ISO 8601 allows the truncated forms, so emit the longest prefix the value
    actually supports rather than inventing a January 1st.
    """
    if not yyyymmdd:
        return None
    try:
        value = int(yyyymmdd)
    except (TypeError, ValueError):
        return None

    year, month, day = value // 10000, (value // 100) % 100, value % 100
    if not 1 <= year <= 9999:
        return None
    if not 1 <= month <= 12:
        return f"{year:04d}"
    if not 1 <= day <= 31:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


def split_artists(artist: str | None) -> list[str]:
    """Split the display artist string back into the list xesam wants.

    ``Track.artist`` comma-joins the related artist rows for the UI. MPRIS wants
    ``xesam:artist`` as an array of strings, so undo the join rather than
    reaching for the ORM relationship — this keeps the function usable with a
    detached or stubbed track, and it agrees with what the app displays even
    when an artist name itself is odd.
    """
    if not artist:
        return []
    return [part.strip() for part in artist.split(",") if part.strip()]


def build_metadata(
    track: Any,
    duration_seconds: float,
    art_url: str | None = None,
) -> dict[str, Any]:
    """Build the ``Metadata`` dict for a track.

    Args:
        track: Anything with the ``Track`` attribute surface (``id``, ``title``,
            ``artist``, ``album``, ``genres``, ``release_date``, ``bpm``). A
            stub is fine — nothing here touches the session or the filesystem.
        duration_seconds: The live duration from the player, which is the
            decoder's answer and so more trustworthy than the stored column.
        art_url: A ``file://`` URL from :func:`app.system.mpris_art.art_url`,
            or ``None``.

    Returns:
        A dict ready to be marshalled as ``a{sv}``. Absent values are omitted
        rather than sent as empty strings: a client that gets ``xesam:album: ""``
        will render an empty album line, whereas a missing key lets it lay the
        card out without one.
    """
    if track is None:
        # The spec has no "null metadata" — an empty-ish dict carrying only the
        # NoTrack id is the conventional way to say nothing is loaded.
        return {"mpris:trackid": track_object_path(None)}

    meta: dict[str, Any] = {
        "mpris:trackid": track_object_path(getattr(track, "id", None)),
        "xesam:title": getattr(track, "title", None) or "Unknown Title",
    }

    if duration_seconds and duration_seconds > 0:
        meta["mpris:length"] = to_micros(duration_seconds)

    artists = split_artists(getattr(track, "artist", None))
    if artists:
        meta["xesam:artist"] = artists

    album = getattr(track, "album", None)
    if album:
        meta["xesam:album"] = album

    if art_url:
        meta["mpris:artUrl"] = art_url

    genres = [g.name for g in (getattr(track, "genres", None) or []) if getattr(g, "name", None)]
    if genres:
        meta["xesam:genre"] = genres

    created = iso_date(getattr(track, "release_date", None))
    if created:
        meta["xesam:contentCreated"] = created

    bpm = getattr(track, "bpm", None)
    if bpm:
        meta["xesam:audioBPM"] = int(bpm)

    return meta
