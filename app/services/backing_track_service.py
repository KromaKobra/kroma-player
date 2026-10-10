"""Instrumental counterparts to library tracks.

Every track *may* have a backing track — the same recording without vocals —
stored at ``venomvault/bt/{shard}/{ID}.opus``. The tree mirrors ``mix/``
exactly: same shard math, same local id, so a track's instrumental is found by
id alone with no extra bookkeeping.

**Availability is filesystem state, not a database column.** There is no
migration system (``app.database.schema`` is ``create_all`` and nothing else,
and ``create_all`` silently ignores a missing column on an existing table), so a
``Track.has_backing_track`` flag would work on a fresh vault and raise on every
existing one. Instead this service keeps an in-memory set of ids, filled by one
directory scan on first use and kept current by explicit ``mark_available`` /
``mark_removed`` calls from the two things that write to ``bt/`` — the importer
and the generator.

The set is the only cached state, so a stale entry costs one failed open rather
than a wrong answer: :meth:`has` re-checks the disk whenever the cache says no.

**Durations must match exactly** or the mid-playback toggle is not seamless.
``Track.duration_seconds`` is an integer column written from ``int(length)``, so
it carries up to a second of slop and is useless here. Every comparison in this
module reads the float length off the actual file.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from mutagen import File as MutagenFile

from app.events.event_bus import EventBus
from app.importer.stash import StopCheck, _stopped
from app.importer.tagging import strip_opus_tags
from app.importer.transcode import ensure_opus
from app.utils.library_paths import BACKING_CATEGORY, category_root, track_path

logger = logging.getLogger(__name__)

# How far two recordings may differ and still be considered the same length.
# Opus stores whole frames, so a re-encode of an identical source can land a
# frame or two out; 50 ms is comfortably inside "inaudible on a crossfade" and
# comfortably outside "this is a different edit of the song".
DURATION_TOLERANCE_SECONDS = 0.05


def probe_duration(path: Path | str) -> Optional[float]:
    """Return a media file's length in seconds, or None if it cannot be read.

    Deliberately a float — see the module docstring on why the stored integer
    duration is not good enough for the length check.
    """
    try:
        audio = MutagenFile(str(path))
    except Exception:  # noqa: BLE001 - mutagen raises a wide variety on bad input
        logger.debug("Could not probe duration of %s", path, exc_info=True)
        return None

    length = getattr(getattr(audio, "info", None), "length", None)
    if not length:
        return None
    return float(length)


class BackingTrackService:
    """Locates, ingests and removes instrumental counterparts."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        # None until the first scan. Distinguishing "not scanned" from "scanned,
        # found nothing" keeps an empty vault from rescanning on every lookup.
        self._available: Optional[set[int]] = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def path(self, track_id: int) -> Path:
        """Where this track's instrumental lives, whether or not it exists."""
        return track_path(track_id, category=BACKING_CATEGORY)

    def has(self, track_id: int) -> bool:
        """Whether an instrumental exists for this track.

        A predicate, so it is total: an id that could not name a file — zero,
        negative, or past the vault's 0xFFFF ceiling — is answered ``False``
        rather than raised on. Callers ask this about ids from queues, QML lists
        and stale selections, and none of those should be able to crash a
        lookup.

        A cache hit is trusted; a miss falls through to the disk, so a file that
        appeared behind our back (a user dropping one into the vault by hand) is
        picked up rather than denied until restart.
        """
        try:
            path = self.path(track_id)
        except ValueError:
            return False

        if self._available is None:
            self.refresh()

        assert self._available is not None
        if track_id in self._available:
            return True

        if path.exists():
            self._available.add(track_id)
            return True
        return False

    def available_ids(self) -> set[int]:
        """Every track id that currently has an instrumental."""
        if self._available is None:
            self.refresh()
        assert self._available is not None
        return set(self._available)

    def refresh(self) -> None:
        """Rebuild the availability set from the ``bt/`` tree."""
        found: set[int] = set()
        root = category_root(BACKING_CATEGORY)

        if root.is_dir():
            for opus in root.glob("*/*.opus"):
                try:
                    found.add(int(opus.stem, 16))
                except ValueError:
                    logger.debug("Ignoring unrecognised file in bt/: %s", opus)

        self._available = found
        logger.info("Backing tracks available: %d", len(found))

    def mark_available(self, track_id: int) -> None:
        """Record that an instrumental now exists, and tell the app."""
        if self._available is None:
            self.refresh()
        assert self._available is not None

        if track_id in self._available:
            return
        self._available.add(track_id)
        self._event_bus.emit("backing_track.added", track_id)

    def mark_removed(self, track_id: int) -> None:
        """Record that an instrumental is gone, and tell the app."""
        if self._available is None:
            self.refresh()
        assert self._available is not None

        if track_id not in self._available:
            return
        self._available.discard(track_id)
        self._event_bus.emit("backing_track.removed", track_id)

    # ------------------------------------------------------------------
    # Length checking
    # ------------------------------------------------------------------

    def mix_duration(self, track_id: int) -> Optional[float]:
        """Float length of the track's mix file, read off the file itself."""
        mix = track_path(track_id)
        if not mix.exists():
            return None
        return probe_duration(mix)

    def duration_matches(self, track_id: int, source: Path | str) -> bool:
        """Whether *source* is the same length as the track's mix.

        Both sides are probed from disk. A file we cannot read never matches —
        silently accepting an unknown length is exactly the case this guard
        exists to prevent.
        """
        mix_length = self.mix_duration(track_id)
        source_length = probe_duration(source)
        if mix_length is None or source_length is None:
            return False
        return abs(mix_length - source_length) <= DURATION_TOLERANCE_SECONDS

    # ------------------------------------------------------------------
    # Ingest / removal
    # ------------------------------------------------------------------

    def import_file(
        self,
        track_id: int,
        source: Path | str,
        should_stop: StopCheck = None,
        match_length: bool = True,
    ) -> Path | None:
        """Transcode *source* to Opus and install it as this track's instrumental.

        The length check is re-run here rather than trusted from the caller: the
        UI grays ineligible rows out, but this is the function that actually
        writes to the vault, and a mismatched file makes the toggle audibly
        wrong forever after.

        *match_length* False skips both length checks. That is for an
        instrumental fetched from an audio source, which is a separate upload of the
        song and is rarely the mix's length to the millisecond. Refusing it would
        refuse nearly all of them, so it is installed as it is and the toggle is
        only as seamless as the two uploads happen to line up.

        *should_stop* is polled before the transcode and again before the file
        is moved into ``bt/``; returns ``None`` instead of a path when it says
        yes. Everything up to that move happens in a temporary directory, so
        stopping leaves the vault exactly as it was.
        """
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"No such file: {source}")
        if _stopped(should_stop):
            return None
        if match_length and not self.duration_matches(track_id, source):
            raise ValueError(
                "Instrumental length does not match the mix — the toggle would "
                "not be seamless"
            )

        destination = self.path(track_id)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            opus = ensure_opus(source, Path(tmp))
            # An instrumental carries no metadata: it is found by the mix's id
            # and the mix already holds the title, artist and artwork. See
            # `strip_opus_tags` for why `ensure_opus` is not enough on its own.
            strip_opus_tags(opus)
            # Transcoding can shift the length even when the source matched, so
            # the encoded file gets the same test before it is installed.
            encoded_length = probe_duration(opus)
            mix_length = self.mix_duration(track_id)
            if not match_length:
                logger.info(
                    "Installing instrumental for track %d without a length match "
                    "(instrumental %s s, mix %s s)",
                    track_id, encoded_length, mix_length,
                )
            elif (
                encoded_length is None
                or mix_length is None
                or abs(encoded_length - mix_length) > DURATION_TOLERANCE_SECONDS
            ):
                raise ValueError(
                    "Encoded instrumental drifted from the mix length; not installing"
                )
            if _stopped(should_stop):
                return None
            shutil.move(str(opus), str(destination))

        self.mark_available(track_id)
        logger.info("Backing track installed for track %d", track_id)
        return destination

    def install(self, track_id: int, opus_path: Path | str) -> Path:
        """Move an already-encoded, already-verified Opus into ``bt/``.

        The generation pipeline encodes to the mix's exact length itself (see
        ``app.separation.runner``), so it has already done the checking this
        skips. Tags are still stripped, so that "nothing in ``bt/`` carries
        metadata" holds for every file there rather than for most of them.
        """
        destination = self.path(track_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        strip_opus_tags(Path(opus_path))
        shutil.move(str(opus_path), str(destination))
        self.mark_available(track_id)
        logger.info("Generated backing track installed for track %d", track_id)
        return destination

    def remove(self, track_id: int) -> bool:
        """Delete a track's instrumental. Returns whether a file was removed."""
        destination = self.path(track_id)
        removed = False
        try:
            if destination.exists():
                destination.unlink()
                removed = True
        except OSError as exc:
            logger.warning("Could not delete backing track for %d: %s", track_id, exc)
            return False

        self.mark_removed(track_id)
        return removed
