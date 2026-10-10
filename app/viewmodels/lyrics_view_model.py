"""Lyrics ViewModel — the words to the playing track, and finding them.

Reads the ``lyrics`` table rather than the audio file. That is the whole change
from what this used to be: ``_read_lyrics_tag()`` opened the playing track's
Opus file with mutagen **on the UI thread** every time the track changed while
the tab was open, so a track on a slow drive stalled the interface and nothing
could ask which tracks had lyrics at all. The files still carry their
``LYRICS`` tag — the table is a faster copy of it, not a replacement. See
``docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md``.

Three paths put lyrics in front of the user, in the order they are tried:

1. **The table.** A primary-key lookup on the shared session. The normal path.
2. **The file's own tag, lazily.** A miss falls back to reading that one file —
   one track, only when it is actually being played — and copies what it finds
   into the table. This is what makes the table correct for a library whose
   walk never ran or was interrupted, and it is why the table can be deleted
   without losing anything.
3. **A search.** ``fetchLyrics()``, behind the mic button, for a track that
   simply never had any. Off the UI thread; see :mod:`app.lyrics.workers`.

And once per library, the walk: the first time the tab is opened, every track
with no lyrics row has its file read in the background and its tag copied in.
See :class:`app.settings.lyrics.LyricsSettings` for why that is remembered.

And two ways the user changes what is there, both ending in the same write:

- **The editor.** ``saveLyrics()`` takes a whole document back from the GUI in
  one call and stores it with source ``manual``.
- **A retime.** ``beginRetime()`` arms a session at the top of the track with
  nothing lit; ``markLine()`` — the space bar — means "the next line starts
  now", so each press stamps a line and highlights it. It is the only practical
  way to give an unsynced document timings, and the least painful way to fix a
  synced one that drifts.

The arithmetic the editor needs while a draft is open — what time a newly
inserted line gets, where a re-timed one belongs, where a pasted block breaks —
is in :mod:`app.lyrics.editing` and reaches QML through three slots here.

Nothing here writes a file. Everything a search finds goes through
:class:`~app.services.lyrics_service.LyricsService`, which writes the row and
then the tag, in that order.

The QML surface (``active``, ``has_lyrics``, ``is_synced``, ``plain_text``,
``synced_lines``, ``current_line_index``) is unchanged by any of it — a GUI
written against the old file-reading version keeps working. Everything the
editor needs is *added* alongside it, which is why none of this bumps
``GUI_API_VERSION``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from app.lyrics.editing import sorted_index, split_pasted, times_for_insert
from app.lyrics.parse import (
    format_lrc,
    is_synced,
    parse_lrc,
    parse_lrc_header,
    parse_timecode,
)
from app.lyrics.tags import read_lyrics_tag
from app.lyrics.workers import LyricsFetchWorker, LyricsTagSweepWorker
from app.services.lyrics_service import (
    SOURCE_MANUAL,
    SOURCE_SEARCH,
    SOURCE_TAG,
    LyricsService,
)
from app.settings.lyrics import LyricsSettings
from app.viewmodels.worker_threads import own_worker

if TYPE_CHECKING:
    from app.viewmodels.player_view_model import PlayerViewModel

logger = logging.getLogger(__name__)


def _as_time(value) -> Optional[float]:
    """Seconds from whatever QML handed over, or ``None`` for "no time".

    Lenient where :meth:`LyricsViewModel._read_times` is strict, and the
    difference is what each is for. This reads a draft that is still being
    typed in, where a half-finished timecode is an ordinary thing to find and
    the right answer is "not a time yet"; that one reads a document on its way
    to disk, where the same text is a mistake worth stopping for.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if float(value) < 0 else float(value)
    return parse_timecode(str(value))


class LyricsViewModel(QObject):
    """Exposes the lyrics for the currently-playing track."""

    lyrics_changed = Signal()
    current_line_changed = Signal()
    active_changed = Signal()

    # The mic button's own state. Separate from `lyrics_changed` because it
    # moves while the lyrics do not: a search that finds nothing changes the
    # button back and leaves the pane exactly as it was.
    fetching_changed = Signal()
    fetch_failed = Signal(str)

    # The retime session's own state — which line is armed, what has been
    # stamped. Separate from `lyrics_changed` because a session moves several
    # times a minute while the stored document does not move at all until it
    # ends.
    retime_changed = Signal()

    # A refused save or a refused retime. Distinct from `fetch_failed` because
    # they are different sentences about different things: one is "the internet
    # had nothing", this one is "what you typed cannot be stored". A GUI is
    # free to route both to the same toast, but it should not have to guess.
    edit_failed = Signal(str)

    def __init__(
        self,
        player_vm: PlayerViewModel,
        lyrics_service: Optional[LyricsService] = None,
        settings: Optional[LyricsSettings] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._player_vm = player_vm
        # Optional so a test — or a GUI harness — can build the view model with
        # nothing behind it. Without a service there is no table to read, which
        # reads as "this track has no lyrics" everywhere rather than as an error.
        self._service = lyrics_service
        self._settings = settings if settings is not None else LyricsSettings(None)

        self._active = False
        self._dirty = True
        self._loading = False

        self._has_lyrics = False
        self._is_synced = False
        self._plain_text = ""
        self._synced_lines: list[str] = []
        self._synced_timestamps: list[float] = []
        self._current_line_index = -1

        # The document's `[ar:]`/`[ti:]` block, kept aside so a save can put it
        # back. `parse_lrc` drops it, and without this an edit would delete
        # metadata the user never saw and never asked to lose.
        self._lrc_header = ""

        # The retime session. `_retime_times` runs parallel to `_retime_lines`
        # and holds None for a line not yet stamped; `_retime_index` is the
        # line most recently marked, and therefore the one lit — -1 before the
        # first press, when nothing is lit at all.
        self._retiming = False
        self._retime_lines: list[str] = []
        self._retime_times: list[Optional[float]] = []
        self._retime_original: list[Optional[float]] = []
        self._retime_index = -1
        self._retime_started = False

        # The mic button's search.
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[LyricsFetchWorker] = None

        # The one-off walk that copies existing tags into the table.
        self._sweep_thread: Optional[QThread] = None
        self._sweep_worker: Optional[LyricsTagSweepWorker] = None

        player_vm.current_track_changed.connect(self._on_track_changed)
        player_vm.position_changed.connect(self._on_position_changed)

        if self._service is not None:
            self._service.event_bus.subscribe("lyrics.updated", self._on_lyrics_updated)

    # ------------------------------------------------------------------
    # Active / lazy-load control
    # ------------------------------------------------------------------

    @Property(bool, notify=active_changed)
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        value = bool(value)
        if value == self._active:
            return
        self._active = value
        self.active_changed.emit()
        if value:
            if self._dirty:
                self._load()
            # The table exists for this tab, so this is the right moment to pay
            # for filling it — and a user who never opens Now Playing never does.
            self._maybe_start_sweep()

    # ------------------------------------------------------------------
    # Properties exposed to QML
    # ------------------------------------------------------------------

    @Property(bool, notify=lyrics_changed)
    def has_lyrics(self) -> bool:
        return self._has_lyrics

    @Property(bool, notify=lyrics_changed)
    def is_synced(self) -> bool:
        return self._is_synced

    @Property(str, notify=lyrics_changed)
    def plain_text(self) -> str:
        return self._plain_text

    @Property('QVariantList', notify=lyrics_changed) # type: ignore
    def synced_lines(self) -> list:
        return self._synced_lines

    @Property('QVariantList', notify=lyrics_changed)  # type: ignore
    def synced_timestamps(self) -> list:
        """When each line in ``synced_lines`` is sung, in seconds.

        Parallel to ``synced_lines`` and empty for an unsynced document. This
        is what a GUI needs to draw a timecode beside a line or to seed the
        editor — without it QML holds the words and no way back to a time.
        """
        return list(self._synced_timestamps)

    @Property(int, notify=current_line_changed)
    def current_line_index(self) -> int:
        # During a retime the armed line *is* the current line, and answering
        # so here rather than making the GUI switch sources means the existing
        # highlight binding carries the new mode with no change at all.
        if self._retiming:
            return self._retime_index
        return self._current_line_index

    @Property(bool, notify=fetching_changed)
    def is_fetching(self) -> bool:
        """Whether a lyric search is running. The mic button's disabled state."""
        return self._fetch_thread is not None

    # ------------------------------------------------------------------
    # The retime session, as QML sees it
    # ------------------------------------------------------------------

    @Property(bool, notify=retime_changed)
    def is_retiming(self) -> bool:
        """Whether a retime is in progress.

        The GUI reads this to decide what the space bar means. It is the one
        moment in the application where a transport key is not a transport key.
        """
        return self._retiming

    @Property(int, notify=retime_changed)
    def retime_index(self) -> int:
        """The line most recently marked — the one to highlight, or ``-1``.

        The line that has been *reached*, not the one being waited for. A press
        says "this line starts now", so the line it stamps is the line it
        lights, and the two stay the same thing all the way to playback.

        ``-1`` before the first press, when the session is armed and nothing is
        lit yet.
        """
        return self._retime_index

    @Property('QVariantList', notify=retime_changed)  # type: ignore
    def retime_lines(self) -> list:
        """The lines the session is timing.

        Usually the same as ``synced_lines``, but not always, and that is the
        reason it exists: retiming an *unsynced* document is one of the two
        things the feature is for, and for that document ``synced_lines`` is
        empty. A GUI that drew the session from ``synced_lines`` would show
        nothing in exactly the case the user most needs to see.
        """
        return list(self._retime_lines)

    @Property('QVariantList', notify=retime_changed)  # type: ignore
    def retime_times(self) -> list:
        """The session's timings so far, ``-1`` for a line not yet stamped.

        ``-1`` rather than ``None`` because a QVariantList of mixed null and
        float is awkward to test against in QML, and no real timestamp is
        negative.
        """
        return [-1.0 if t is None else float(t) for t in self._retime_times]

    @Property(bool, notify=retime_changed)
    def can_finish_retime(self) -> bool:
        """Whether every line has a time — a new stamp or the one it arrived with.

        This is what lets a synced document be half-retimed: the lines past
        where the user stopped keep their original timings, so finishing early
        is a partial fix rather than a document with holes in it. An unsynced
        document has no originals to fall back on, so the same rule requires
        carrying it to the last line.
        """
        if not self._retiming:
            return False
        return all(
            new is not None or old is not None
            for new, old in zip(self._retime_times, self._retime_original)
        )

    # ------------------------------------------------------------------
    # Fetching — the mic button
    # ------------------------------------------------------------------

    @Slot()
    def fetchLyrics(self) -> None:  # noqa: N802 — a QML slot, camelCase by convention
        """Search for the playing track's lyrics and store what comes back.

        Deliberately **not** behind any plugin's consent: that gate guards
        obtaining a *recording*, not reading public text about one. This is also
        the same search the fetch path already makes unprompted.

        The identity is frozen here, on the UI thread, and the worker gets three
        plain values — the threading rule again.
        """
        if self._service is None or self._fetch_thread is not None:
            return

        track = self._current_track()
        if track is None:
            return

        track_id = int(track.id)
        title = str(track.title or "")
        artist = str(track.artist or "")

        thread = QThread(self)
        worker = LyricsFetchWorker(track_id, title, artist)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.found.connect(self._on_fetch_found)
        worker.not_found.connect(self._on_fetch_not_found)
        worker.failed.connect(self._on_fetch_failed)
        own_worker(thread, worker, self._on_fetch_stopped)
        self._fetch_thread = thread
        self._fetch_worker = worker
        thread.start()
        self.fetching_changed.emit()

    @Slot(int, str)
    def _on_fetch_found(self, track_id: int, content: str) -> None:
        """Write the result. Runs on the UI thread, which is why it may write."""
        if self._service is None:
            return
        try:
            self._service.save(track_id, content, SOURCE_SEARCH)
        except Exception as exc:  # noqa: BLE001 — a failed write must not kill the tab
            logger.exception("Saving fetched lyrics for track %s failed", track_id)
            self.fetch_failed.emit(str(exc))

    @Slot(int)
    def _on_fetch_not_found(self, track_id: int) -> None:
        # An ordinary outcome, not an error — plenty of tracks have no lyrics
        # anywhere. The view says so as a plain toast.
        self.fetch_failed.emit("No lyrics found for this track")

    @Slot(str)
    def _on_fetch_failed(self, message: str) -> None:
        self.fetch_failed.emit(message)

    @Slot()
    def _on_fetch_stopped(self) -> None:
        """Released on `finished`, not on `done` — see `own_worker`."""
        self._fetch_thread = None
        self._fetch_worker = None
        self.fetching_changed.emit()

    # ------------------------------------------------------------------
    # Editing — the pencil button
    # ------------------------------------------------------------------

    @Slot('QVariantList', 'QVariantList')
    def saveLyrics(self, times, lines) -> None:  # noqa: N802 — a QML slot
        """Store a whole document the user edited. The editor's only write.

        The GUI hands back both lists in **one call** rather than reporting
        each keystroke. That is not an optimisation, it is the only shape that
        works: a per-line setter would have to rewrite the ``QVariantList`` the
        editor's own fields are reading, and Qt destroys a text field's binding
        the moment somebody types into it. ``docs/GUI_API.md`` states the rule
        for ``editVM`` and ``TrackEditView.qml`` obeys it with ``syncFields()``;
        this is the same rule with the traffic pointed the other way.

        Args:
            times: One entry per line — seconds, or a negative number / empty
                string for "this line has no time". Strings are accepted so a
                GUI can hand the field's raw text straight back, and they go
                through :func:`app.lyrics.parse.parse_timecode`.
            lines: The words. Trailing blanks are dropped; interior ones are
                kept, because a blank line between verses is a real part of a
                lyric document.

        A document that comes back empty is a **delete**, not a save:
        ``LyricsService.save()`` treats empty content as a no-op so that a
        failed search cannot wipe good lyrics, which means clearing the editor
        has to go somewhere else to be heard.
        """
        if self._service is None:
            return
        track = self._current_track()
        if track is None:
            self.edit_failed.emit("Nothing is playing")
            return

        try:
            stamps = self._read_times(times, len(lines))
        except ValueError as exc:
            self.edit_failed.emit(str(exc))
            return

        texts = [str(line if line is not None else "") for line in lines]
        while texts and not texts[-1].strip():
            texts.pop()
        stamps = stamps[:len(texts)]

        self._store(int(track.id), stamps, texts)

    def _read_times(self, times, count: int) -> list[Optional[float]]:
        """Normalise whatever QML handed over into seconds-or-None, per line.

        Raises:
            ValueError: naming the first field that is not a timecode. Refused
                rather than discarded, for the reason ``docs/GUI_API.md`` gives
                about the track editor: text that cannot be parsed is a
                mistake to be shown, not a request to clear the field.
        """
        stamps: list[Optional[float]] = []
        for index in range(count):
            raw = times[index] if index < len(times) else None
            if raw is None:
                stamps.append(None)
                continue
            if isinstance(raw, (int, float)):
                stamps.append(None if float(raw) < 0 else float(raw))
                continue
            text = str(raw).strip()
            if not text:
                stamps.append(None)
                continue
            value = parse_timecode(text)
            if value is None:
                raise ValueError(f"“{text}” is not a timecode (line {index + 1})")
            stamps.append(value)
        return stamps

    def _store(self, track_id: int, stamps: list, texts: list[str]) -> None:
        """Serialise and hand to the service. The end of every editing path."""
        if not any(text.strip() for text in texts):
            try:
                self._service.delete(track_id)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 — a failed write must not kill the tab
                logger.exception("Deleting lyrics for track %s failed", track_id)
                self.edit_failed.emit(str(exc))
            return

        document = format_lrc(stamps, texts, header=self._lrc_header)
        try:
            self._service.save(track_id, document, SOURCE_MANUAL)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — a failed write must not kill the tab
            logger.exception("Saving edited lyrics for track %s failed", track_id)
            self.edit_failed.emit(str(exc))

    # ------------------------------------------------------------------
    # The editor's rules, for the GUI that draws it
    # ------------------------------------------------------------------
    #
    # Three questions the editor has to answer while a draft is open, and the
    # only parts of it worth a test. They are published rather than left in
    # QML so a replacement GUI writing its own editor gets these rules instead
    # of inventing different ones — and so they can be tested without a window.
    #
    # All three are called on a *commit* — a drop, a button, a paste, Enter in
    # a timecode field — never per keystroke, so none of them threatens the
    # one-call rule that `saveLyrics()` exists to keep.
    #
    # -1 means "no time", the same convention `retime_times` uses, because a
    # QVariantList of mixed null and float is awkward to read from QML.

    @Slot('QVariantList', int, int, result='QVariantList')
    def insertionTimes(self, times, index, count):  # noqa: N802 — a QML slot
        """The timecodes for *count* lines inserted at *index*.

        *times* is the draft's current column of timecodes, before the
        insertion; *index* is where the new lines land in the list as it will
        look afterwards. Entries may be numbers or the raw text of a timecode
        field, the same latitude :meth:`saveLyrics` allows, so a GUI never has
        to carry its own copy of the timecode parser.

        See :func:`app.lyrics.editing.insertion_times` for the four cases. A
        line being *moved* would otherwise be its own neighbour, so blank its
        time before asking.

        Returns:
            *count* timecodes in seconds, ``-1`` where the answer is "no time"
            — which is every entry in a document that has none to begin with.
        """
        result = times_for_insert(
            [_as_time(value) for value in times], int(index), int(count),
        )
        return [-1.0 if t is None else float(t) for t in result]

    @Slot('QVariantList', int, result=int)
    def sortedIndex(self, times, index):  # noqa: N802 — a QML slot
        """Where the line at *index* belongs now that its timecode has changed.

        Returns *index* itself when it is already in the right place, which is
        the caller's signal to leave the list alone.
        """
        return sorted_index([_as_time(value) for value in times], int(index))

    @Slot(str, result='QVariantList')
    def splitPastedLines(self, text):  # noqa: N802 — a QML slot
        """A pasted block as the lines it should become.

        A single-line text field has nowhere to put a multi-line paste, so the
        GUI intercepts one and asks here where it breaks — which is how a whole
        lyric sheet becomes a whole editor's worth of rows in one gesture.
        """
        return split_pasted(str(text or ""))

    @Slot('QVariantList', result='QVariantList')
    def parseTimes(self, times):  # noqa: N802 — a QML slot
        """The draft's column of timecodes as seconds, ``-1`` where there is none.

        An editor that lays its lines out *in proportion* needs the draft's
        timings as numbers on every frame, while the draft itself holds them as
        the raw text of a field — pushed, never bound, for the reason
        :meth:`saveLyrics` states. Parsing that text in QML would put a second
        copy of the timecode parser in the GUI, which is the thing its three
        neighbours above exist to prevent.

        Same latitude as those three: half-typed stamps are ordinary in a draft
        somebody is still working on, and read as "no time" rather than as an
        error.
        """
        return [
            -1.0 if (value := _as_time(raw)) is None else float(value)
            for raw in times
        ]

    # ------------------------------------------------------------------
    # Retiming — the space bar
    # ------------------------------------------------------------------

    @Slot()
    def beginRetime(self) -> None:  # noqa: N802 — a QML slot
        """Arm a retime at the top of the track, paused.

        Paused rather than playing, so the session starts on the user's beat
        rather than in a race between hitting play and hearing the first lyric.
        Nothing is highlighted until the first press, which starts the track and
        marks line one in the same gesture.
        """
        if self._retiming:
            return
        if self._service is None or self._current_track() is None:
            self.edit_failed.emit("Nothing is playing")
            return

        lines = list(self._synced_lines) if self._is_synced else self._plain_lines()
        if not lines:
            self.edit_failed.emit("There are no lyrics to retime")
            return

        self._retime_lines = lines
        self._retime_times = [None] * len(lines)
        self._retime_original = [
            self._synced_timestamps[i] if i < len(self._synced_timestamps) else None
            for i in range(len(lines))
        ]
        self._retime_index = -1
        self._retime_started = False
        self._retiming = True

        self._player_vm.pause()
        self._player_vm.seek(0.0)

        self.retime_changed.emit()
        # The highlight is driven by `retime_index` from here until the session
        # ends, and the playhead scan stands down — see `_on_position_changed`.
        self.current_line_changed.emit()

    @Slot()
    def markLine(self) -> None:  # noqa: N802 — a QML slot
        """Mark the moment the next line *starts*, and light it.

        A press means "this line begins now", so it stamps the line it is about
        to highlight rather than the one already lit. That equivalence is the
        whole contract of a retime and it is worth stating as one sentence:

            the line highlighted at time *t* during a session is the line
            highlighted at time *t* on playback.

        It used to stamp the outgoing line instead, which reads plausibly —
        the press does land on the moment that line stops — but a stored
        timestamp is a line's *start*, so every line ended up wearing the time
        of the one after it and playback ran a line behind the session.

        The opening press stamps line one at zero and starts the track,
        because the session is armed at 0:00 and that is where line one is lit
        from. A song with an intro therefore shows its first line through the
        intro, in the session and on playback alike — which is the same answer
        in both places, which is the point.
        """
        if not self._retiming:
            return

        # Read before starting the track: on the opening press this is the
        # 0.0 the session was armed at, and it must not pick up however long
        # `play()` takes to return.
        position = max(0.0, float(self._player_vm.position))  # type: ignore[arg-type]

        if not self._retime_started:
            self._retime_started = True
            self._player_vm.play()

        self._retime_index += 1
        self._retime_times[self._retime_index] = position

        if self._retime_index >= len(self._retime_lines) - 1:
            # The last line has been marked; there is nothing left to wait for,
            # so the session ends itself rather than making the user say so
            # twice.
            self.finishRetime()
            return

        self.retime_changed.emit()
        self.current_line_changed.emit()

    @Slot()
    def undoMark(self) -> None:  # noqa: N802 — a QML slot
        """Take back the last stamp and rewind so the moment comes round again.

        Rewinding is the point. Un-stamping alone would leave the playhead
        somewhere past the line that has to be caught, so the only way to use
        it would be to seek by hand — which is the thing a retime exists to
        avoid.

        Taking back the *first* press undoes the whole opening gesture: the
        track pauses, returns to 0:00, and nothing is lit again.
        """
        if not self._retiming or self._retime_index < 0:
            return

        self._retime_times[self._retime_index] = None
        self._retime_index -= 1

        if self._retime_index < 0:
            # Back to where the session started: paused at the top with nothing
            # lit, because the press being taken back is the one that started
            # the track as well as the one that timed line one.
            self._retime_started = False
            self._player_vm.pause()
            self._player_vm.seek(0.0)
        else:
            # To where the line now lit begins, so the next one comes round
            # again. Un-stamping alone would leave the playhead somewhere past
            # the moment that has to be caught.
            previous = self._retime_times[self._retime_index]
            self._player_vm.seek(float(previous) if previous is not None else 0.0)

        self.retime_changed.emit()
        self.current_line_changed.emit()

    @Slot()
    def finishRetime(self) -> None:  # noqa: N802 — a QML slot
        """Write the session's timings and leave the mode.

        Refuses while any line still has no time at all. A line the user never
        reached keeps the one it arrived with, so stopping halfway through a
        synced document is a partial retime rather than a document with holes;
        an unsynced one has nothing to fall back on and has to be carried to
        the end.
        """
        if not self._retiming:
            return
        if not self.can_finish_retime:
            missing = sum(
                1 for new, old in zip(self._retime_times, self._retime_original)
                if new is None and old is None
            )
            self.edit_failed.emit(
                f"{missing} line{'s' if missing != 1 else ''} still ha"
                f"{'ve' if missing != 1 else 's'} no time"
            )
            return

        stamps = [
            new if new is not None else old
            for new, old in zip(self._retime_times, self._retime_original)
        ]
        texts = list(self._retime_lines)
        track = self._current_track()

        self._end_retime()
        if track is not None:
            self._store(int(track.id), stamps, texts)

    @Slot()
    def cancelRetime(self) -> None:  # noqa: N802 — a QML slot
        """Drop the session. Nothing stored, nothing changed."""
        if not self._retiming:
            return
        self._end_retime()

    def _end_retime(self) -> None:
        self._retiming = False
        self._retime_started = False
        self._retime_lines = []
        self._retime_times = []
        self._retime_original = []
        self._retime_index = -1
        self.retime_changed.emit()
        # The playhead scan takes the highlight back, but it only recomputes on
        # the next position tick — which on a paused track never comes. Nudging
        # it here is what keeps the highlight from freezing where the session
        # left it.
        self._current_line_index = -1
        self._on_position_changed()
        self.current_line_changed.emit()

    def _plain_lines(self) -> list[str]:
        """An unsynced document as the lines a retime will hang times on.

        Blank separators are dropped: they are typography, and stamping one
        would put a timestamp on nothing and leave the highlight sitting on an
        empty row for as long as the gap lasts.
        """
        return [line.strip() for line in self._plain_text.split('\n') if line.strip()]

    # ------------------------------------------------------------------
    # The one-off tag walk
    # ------------------------------------------------------------------

    def _maybe_start_sweep(self) -> None:
        """Copy every remaining embedded ``LYRICS`` tag into the table, once.

        The target list is built here, on the UI thread, from the repository and
        from ``Track.file_path`` — the worker receives frozen pairs and touches
        no session of its own.
        """
        if self._service is None or self._sweep_thread is not None:
            return
        if self._settings.tag_sweep_done:
            return

        try:
            track_ids = self._service.track_ids_without_lyrics()
        except Exception:  # noqa: BLE001 — a failed walk must not kill the tab
            logger.exception("Could not list tracks without lyrics")
            return

        if not track_ids:
            # Nothing to copy is a finished walk: a library that arrives with
            # every track already covered should not be asked again next launch.
            self._settings.tag_sweep_done = True
            return

        targets = self._sweep_targets(track_ids)

        thread = QThread(self)
        worker = LyricsTagSweepWorker(targets)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.batch.connect(self._on_sweep_batch)
        worker.finished.connect(self._on_sweep_finished)
        own_worker(thread, worker, self._on_sweep_stopped)
        self._sweep_thread = thread
        self._sweep_worker = worker
        thread.start()

    def _sweep_targets(self, track_ids: list[int]) -> list[tuple[int, str]]:
        """Freeze ``(track_id, file_path)`` for every id, on the UI thread.

        ``track_path()`` is pure id arithmetic, so this costs no queries and no
        file access — the worker does all of the opening.
        """
        from app.utils.library_paths import track_path

        return [(int(tid), str(track_path(int(tid)))) for tid in track_ids]

    @Slot(list)
    def _on_sweep_batch(self, entries: list) -> None:
        """Store one batch. On the UI thread, so this may write."""
        if self._service is None or not entries:
            return
        try:
            self._service.save_many(entries, SOURCE_TAG)
        except Exception:  # noqa: BLE001 — one bad batch must not stop the walk
            logger.exception("Storing a batch of copied lyrics failed")

    @Slot(int)
    def _on_sweep_finished(self, found: int) -> None:
        """Mark the walk done. Only on a run that reached the end.

        An interrupted walk leaves the flag clear on purpose: what it did write
        is already in the table and will not be looked at again, so the next run
        picks up exactly where this one stopped.
        """
        self._settings.tag_sweep_done = True
        logger.info("Copied embedded lyrics into the table for %d track(s)", found)

    @Slot()
    def _on_sweep_stopped(self) -> None:
        self._sweep_thread = None
        self._sweep_worker = None

    def shutdown(self) -> None:
        """Ask a running walk to stop at the next file.

        The walk is the one job here that can still be going when the window
        closes — a fetch is seconds, a first-run walk over a large library is
        not. It writes nothing itself, so stopping it mid-run loses only the
        batch in hand.

        A retime is dropped for the opposite reason: it holds unsaved work, and
        finishing one on the way out would write a document the user was still
        halfway through rather than one they approved.
        """
        self.cancelRetime()
        if self._sweep_worker is not None:
            self._sweep_worker.cancel()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _current_track(self):
        return getattr(self._player_vm, '_current_track', None)

    def _on_track_changed(self) -> None:
        # A session belongs to one track. Playing another one — deliberately or
        # because the song ended under it — ends it rather than stamping the
        # new track's playhead onto the old track's words.
        self.cancelRetime()
        if self._active:
            self._load()
        else:
            self._dirty = True

    def _on_lyrics_updated(self, track_id: int) -> None:
        """Reload when the track that changed is the one on screen.

        The walk writes a batch at a time and emits per track, so this fires
        often during a first-run copy and almost never after — hence the id
        test rather than an unconditional reload.
        """
        track = self._current_track()
        if track is None or int(track_id) != int(track.id):
            return
        if self._active:
            self._load()
        else:
            self._dirty = True

    def _on_position_changed(self) -> None:
        # A retime owns the highlight while it runs. Letting the scan keep
        # moving it would fight the user: they are stamping the line they can
        # hear, and the stored timings are exactly the ones being replaced.
        if self._retiming:
            return
        if not self._is_synced or not self._synced_timestamps:
            return
        pos: float = self._player_vm.position  # type: ignore[assignment]
        # -1, not 0. A document whose first line is sung at 00:12 has *no*
        # current line before then, and starting the scan at 0 lit that line
        # from the top of the track — through the whole intro, on every song
        # that has one.
        idx = -1
        for i, ts in enumerate(self._synced_timestamps):
            if ts <= pos:
                idx = i
            else:
                break
        if idx != self._current_line_index:
            self._current_line_index = idx
            self.current_line_changed.emit()

    def _load(self) -> None:
        # Re-entrant by construction: the lazy tag copy below writes through
        # the service, which emits `lyrics.updated` synchronously, which lands
        # back here for the same track. Guarding is cheaper than making that
        # write silent — it genuinely is worth announcing to everyone else.
        if self._loading:
            return
        self._loading = True
        try:
            self._load_now()
        finally:
            self._loading = False

    def _load_now(self) -> None:
        self._dirty = False
        raw = self._read_lyrics()
        if not raw or not raw.strip():
            self._has_lyrics = False
            self._is_synced = False
            self._plain_text = ""
            self._synced_lines = []
            self._synced_timestamps = []
            self._lrc_header = ""
            self._current_line_index = -1
            self.lyrics_changed.emit()
            return

        self._has_lyrics = True
        # Kept whether or not this document is LRC, because an unsynced one
        # that a retime turns into LRC should carry its metadata across.
        self._lrc_header = parse_lrc_header(raw)
        if is_synced(raw):
            self._is_synced = True
            self._synced_timestamps, self._synced_lines = parse_lrc(raw)
            self._plain_text = ""
        else:
            self._is_synced = False
            self._plain_text = self._without_header(raw)
            self._synced_lines = []
            self._synced_timestamps = []

        self._current_line_index = -1
        self.lyrics_changed.emit()

    def _without_header(self, raw: str) -> str:
        """*raw* with its metadata block taken off the front.

        Only the unsynced path needs this. An LRC document's header never
        reaches the screen because ``parse_lrc`` drops every untimestamped
        line, but plain text is shown as it arrives — so a document carrying
        ``[ar:Someone]`` would otherwise display it as the first lyric. The
        block itself is not lost; ``_lrc_header`` is holding it.
        """
        if not self._lrc_header:
            return raw.strip()
        remaining = raw.strip().split('\n')
        for _ in self._lrc_header.split('\n'):
            while remaining and not remaining[0].strip():
                remaining.pop(0)
            if remaining:
                remaining.pop(0)
        return '\n'.join(remaining).strip()

    def _read_lyrics(self) -> Optional[str]:
        """The table, then this one track's file tag as a fallback."""
        track = self._current_track()
        if track is None or self._service is None:
            return None

        track_id = int(track.id)
        stored = self._service.get(track_id)
        if stored:
            return stored

        return self._port_tag(track_id, track)

    def _port_tag(self, track_id: int, track) -> Optional[str]:
        """Read this track's ``LYRICS`` tag, copy it into the table, return it.

        The lazy half of the walk. One file open, for a track the user is
        listening to right now, and only until the walk or this has covered it —
        after which the row exists and this is never reached again.

        The service is told not to write the tag back: this text came out of
        that exact file a line ago, so there is nothing to mirror.
        """
        raw = read_lyrics_tag(track.file_path)
        if not raw or not raw.strip():
            return None
        try:
            self._service.save(track_id, raw, SOURCE_TAG)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — showing the lyrics matters more
            logger.exception("Copying the LYRICS tag of track %s failed", track_id)
        return raw
