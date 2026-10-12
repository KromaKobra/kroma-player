"""Player ViewModel - state binding for playback controls."""

from PySide6.QtCore import QObject, Signal, Slot, Property
from typing import Optional, cast
from app.models.track import Track
from app.services.track_service import TrackService
from app.queue.queue_manager import QueueManager, QueueMutation
from app.audio.qt_multimedia_backend import QtMultimediaBackend, device_id
from app.events.event_bus import EventBus
from app.viewmodels.queue_view_model import QueueViewModel
from app.viewmodels.history_view_model import HistoryViewModel
from app.settings.playback import PlaybackSettings
from app.settings.backing_track import BackingTrackSettings, BEHAVIOR_SKIP
from app.services.backing_track_service import BackingTrackService
from app.utils.library_paths import BACKING_CATEGORY, DEFAULT_CATEGORY


class PlayerViewModel(QObject):
    """ViewModel for player controls."""

    # Signals
    current_track_changed = Signal()
    is_playing_changed    = Signal()
    position_changed      = Signal()
    duration_changed      = Signal()
    volume_changed        = Signal()
    playback_settings_changed = Signal()
    backing_track_changed = Signal()
    audio_devices_changed = Signal()
    # Emitted only when the playhead jumps, carrying the new position in
    # seconds. Distinct from ``position_changed``, which also fires several
    # times a second as playback simply advances: a listener cannot tell a seek
    # from normal progress by watching that one. Consumers that must react to
    # discontinuities specifically — MPRIS's ``Seeked`` signal, a waveform view
    # — want this instead.
    seeked = Signal(float)

    def __init__(
        self,
        track_service:       TrackService,
        queue_manager:       QueueManager,
        audio_backend:       QtMultimediaBackend,
        event_bus:           EventBus,
        queue_view_model:    QueueViewModel  | None = None,
        history_view_model:  HistoryViewModel | None = None,
        playback_settings:   PlaybackSettings | None = None,
        backing_service:     BackingTrackService | None = None,
        backing_settings:    BackingTrackSettings | None = None,
    ) -> None:
        super().__init__()

        self.track_service  = track_service
        self.queue_manager  = queue_manager
        self.audio_backend  = audio_backend
        self.event_bus      = event_bus
        self._queue_vm      = queue_view_model
        self._history_vm    = history_view_model
        self._settings      = playback_settings or PlaybackSettings(None)
        self._backing       = backing_service
        self._backing_settings = backing_settings or BackingTrackSettings(None)

        self._current_track: Optional[Track] = None
        self._playback_source: dict = {}
        self._is_playing = False
        self._position   = 0.0
        self._duration   = 0.0
        self._listened   = False  # irreversible per-track flag
        # Last session's level, pushed to the backend now so the first track to
        # play comes out at it rather than at whatever the output defaults to.
        self._volume     = self._settings.volume
        self.set_volume(self._volume)
        # Same reasoning: the instrumental trim is a saved level, so the first
        # instrumental to play has to come out at it rather than untrimmed.
        self.audio_backend.set_backing_gain_db(self._backing_settings.gain_db)
        # And again: the device is pushed before anything plays, because the
        # first track has to come out of the chosen one rather than out of
        # whatever happened to be the system default at launch.
        self.audio_backend.set_output_device(
            self._settings.output_device, self._settings.output_device_name
        )

        # Audio backend connections
        self.audio_backend.position_changed.connect(self._on_position_changed)
        self.audio_backend.playback_state_changed.connect(self._on_playback_state_changed)
        self.audio_backend.track_finished.connect(self._on_track_finished)
        # Devices come and go while the app runs, and the settings list is often
        # open while they do — that is exactly when someone is plugging things in.
        self.audio_backend.output_devices_changed.connect(
            self._on_output_devices_changed
        )

        # An instrumental appearing for the playing track has to light the
        # toggle up without the user touching anything — that is the whole
        # payoff of generating one while it plays.
        self.event_bus.subscribe("backing_track.added", self._on_backing_availability_changed)
        self.event_bus.subscribe("backing_track.removed", self._on_backing_availability_changed)

        # Renaming the track you are listening to has to show up in the title
        # bar and transport straight away. The ORM object held here is already
        # the updated one — only the notify is missing.
        self.event_bus.subscribe("track.updated", self._on_track_updated)

    def _on_track_updated(self, track) -> None:
        if self._current_track is not None and track is not None \
                and track.id == self._current_track.id:
            # The row we are holding is now stale; the one that arrived is not.
            # Swap it in so anything reading `_current_track` — including the
            # gain fallback below — sees what was actually committed.
            self._current_track = track
            # A saved gain has to be audible without waiting for the track to be
            # played again. The editor's preview has usually already put the
            # same number in place; re-pushing it is what makes the outcome the
            # same whether the edit came from this GUI or from anywhere else.
            self.audio_backend.set_track_gain_db(getattr(track, "gain_db", 0.0) or 0.0)
            self.current_track_changed.emit()

    # ------------------------------------------------------------------
    # Properties exposed to QML
    # ------------------------------------------------------------------

    @Property(str, notify=current_track_changed)
    def current_track_title(self) -> str:
        return cast(str, self._current_track.title) if self._current_track else "No track"

    @Property(str, notify=current_track_changed)
    def current_track_artist(self) -> str:
        return cast(str, self._current_track.artist) if self._current_track else "Unknown"

    @Property(int, notify=current_track_changed)
    def current_track_id(self) -> int:
        """Library id of the playing track, or -1 when nothing is loaded."""
        return cast(int, self._current_track.id) if self._current_track else -1

    @Property(str, notify=current_track_changed)
    def currentSourceType(self) -> str:
        return self._current_source().get("sourceType", "")

    @Property(int, notify=current_track_changed)
    def currentSourceId(self) -> int:
        return self._current_source().get("sourceId", -1)

    def _current_source(self) -> dict:
        return self._playback_source if self._current_track is not None else {}

    @Property(str, notify=current_track_changed)
    def current_track_cover_hash(self) -> str:
        """Content hash of the playing track's art, or "" when nothing is loaded."""
        if self._current_track and self._current_track.cover_hash: # type: ignore
            return cast(str, self._current_track.cover_hash)
        return ""

    @Property(bool, notify=is_playing_changed)
    def is_playing(self) -> bool:
        return self._is_playing

    @Property(bool, notify=current_track_changed)
    def has_current_track(self) -> bool:
        return self._current_track is not None

    @Property(bool, notify=current_track_changed)
    def can_go_next(self) -> bool:
        """Whether Next has somewhere to go.

        Index 0 is the playing track and ``advance()`` pops it, so a second
        entry is what makes Next meaningful; with one left it only stops
        playback. Queue edits that do not change the playing track leave this
        stale until the next track change — acceptable for greying out a
        button, and the alternative is a notify signal on every queue mutation.
        """
        return len(self.queue_manager.queue_entries()) > 1

    @Property(bool, notify=current_track_changed)
    def can_go_previous(self) -> bool:
        """Whether Previous does anything.

        True whenever a track is loaded, including with empty history: see
        :meth:`previous`, which restarts the current track rather than
        no-opping when there is nothing behind it.
        """
        return self._current_track is not None

    @Property(bool, notify=backing_track_changed)
    def currentTrackHasBacking(self) -> bool:
        """Whether the playing track has an instrumental to switch to."""
        if self._current_track is None or self._backing is None:
            return False
        return self._backing.has(cast(int, self._current_track.id))

    @Property(float, notify=position_changed)
    def position(self) -> float:
        return self._position

    @Property(float, notify=duration_changed)
    def duration(self) -> float:
        return cast(float, self._duration)

    @Property(float, notify=volume_changed)
    def volume(self) -> float:
        return self._volume

    # ------------------------------------------------------------------
    # Playback control slots
    # ------------------------------------------------------------------

    @Slot()
    def play(self) -> None:
        """Resume or start playback."""
        if self._current_track:
            self.audio_backend.play()
        else:
            # Nothing loaded yet — load the front of the queue and play.
            self._play_current()

    @Slot()
    def pause(self) -> None:
        self.audio_backend.pause()

    @Slot()
    def toggle_playback(self) -> None:
        if self._is_playing:
            self.pause()
        else:
            self.play()

    @Slot()
    def next(self) -> None:
        """
        Advance the queue: current track → history[0], queue[1] → queue[0].
        If the queue is empty after advancing, stop playback.

        Pressing Next is a deliberate "done with this one", so the play is
        recorded however short it was. That is the single exception to the
        listen threshold — see ``QueueManager.advance``.

        With backing-track mode on and the missing-instrumental policy set to
        skip, this keeps advancing past tracks that have none.
        """
        self._apply_mutation(self.queue_manager.advance())
        self._skip_tracks_without_backing()

        if self.queue_manager.current():
            self._play_current()
        else:
            self.stop_player()

    def _skip_tracks_without_backing(self) -> None:
        """Advance past queued tracks that have no instrumental.

        Only reached from :meth:`next` and end-of-track. Picking a specific
        track — double-clicking a row, a playlist's play button — always plays
        it; being silently skipped over after asking for something by name
        would read as the app ignoring you, whatever the setting says.

        Bounded by the queue length so a queue where *nothing* has an
        instrumental empties and stops rather than spinning.
        """
        if not self._backing_enabled():
            return
        if self._backing_settings.missing_behavior != BEHAVIOR_SKIP:
            return
        assert self._backing is not None

        remaining = len(self.queue_manager.queue_entries())
        while remaining > 0:
            current = self.queue_manager.current()
            if not current or self._backing.has(int(current["id"])):
                return
            self._apply_mutation(self.queue_manager.advance())
            remaining -= 1

    @Slot()
    def previous(self) -> None:
        """
        Previous button: restart the current track when playback is already
        past the listen threshold, otherwise rewind a step by pulling
        history[0] back into the queue. With nothing behind it, restart.

        The rewind shifts everything back one slot rather than displacing
        anything — the track that was playing lands at queue[1] whatever the
        playhead said, so ``_listened`` has nothing to say here.

        The restart test reads the live position, not ``_listened``: they
        answer different questions. "Am I far enough in that Previous means
        start-over?" is about where the playhead is now; "did this track count
        as played?" is a latch that survives seeking backwards.
        """
        if self._position >= self._settings.listened_threshold_seconds:
            self.seek(0.0)
            return

        if not self.queue_manager.history_entries():
            self.seek(0.0)
            return

        self._apply_mutation(self.queue_manager.previous())
        self._play_current()

    @Slot("QVariantList")
    def play_queue_rows(self, rows: list) -> None:
        """
        Play the given queue rows: they move to the front in the order given
        and the first one starts playing.

        Rows that were merely passed over stay exactly where they are — the
        queue only loses what is actually played. Pass a single-element list
        for a double-click or a single-row Play.
        """
        indices = [int(row) for row in rows]
        if not indices:
            return

        self._apply_mutation(self.queue_manager.play_at(indices, self._listened))
        self._play_current()

    @Slot("QVariantList")
    def play_from_ids(self, track_ids: list) -> None:
        """
        Play tracks from anywhere that is not the queue — library, search,
        playlist or history rows. They go to the front of the queue in the
        order given and the first one plays.

        The existing queue survives below them; only the header Play/Shuffle
        buttons replace it (``replace_queue_and_play``).
        """
        self.playEntries([{"id": int(t)} for t in track_ids])

    @Slot("QVariantList")
    def playEntries(self, entries: list) -> None:
        """Play a block with optional collection provenance, retaining the queue."""
        entries = self._resolve_entry_data(entries)
        if not entries:
            return

        self._apply_mutation(self.queue_manager.play_entries(entries, self._listened))
        self._play_current()

    @Slot("QVariantList")
    def replace_queue_and_play(self, track_ids: list) -> None:
        """Replace the entire queue with the given tracks and start playing.

        The one path that discards a queue, reserved for the deliberate "play
        this whole thing now" gestures: a playlist header's play button, a
        shuffle-all. History is left untouched. No-op if the list resolves to
        no real tracks.
        """
        self.replaceQueueAndPlayEntries([{"id": int(t)} for t in track_ids])

    @Slot("QVariantList")
    def replaceQueueAndPlayEntries(self, entries: list) -> None:
        entries = self._resolve_entry_data(entries)
        if not entries or not self._queue_vm:
            return

        self.queue_manager.set_queue(entries)
        self._queue_vm.notify_reset()
        self._queue_vm.refresh_selection()
        self._play_current()

    @Slot(int)
    def load_track(self, track_id: int) -> None:
        """
        Load a track by ID without touching the queue order.
        Use this only when the track is already at queue[0]
        (e.g. after next/previous has already mutated the queue).
        """
        track = self.track_service.get_track_by_id(track_id)
        if track:
            self._current_track = track
            current = self.queue_manager.current() or {}
            self._playback_source = {key: current[key] for key in ("sourceType", "sourceId")
                                     if key in current} if current.get("id") == track_id else {}
            self._duration      = track.duration_seconds
            self._position      = 0.0
            self._listened      = False
            self.audio_backend.load(track, self._category_for(track_id))
            self.current_track_changed.emit()
            self.duration_changed.emit()
            self.position_changed.emit()
            self.backing_track_changed.emit()

    @Slot(int)
    def deleteTrack(self, track_id: int) -> None:
        """Delete a track from the database. Views observe track.deleted via event bus."""
        self.track_service.delete_track(track_id)

    @Slot()
    def stop_player(self) -> None:
        """Stop playback and clear the currently loaded track state."""
        self.audio_backend.stop()
        self.audio_backend.clear()

        self._current_track = None
        self._is_playing    = False
        self._position      = 0.0
        self._duration      = 0.0

        self.current_track_changed.emit()
        self.is_playing_changed.emit()
        self.position_changed.emit()
        self.duration_changed.emit()
        self.backing_track_changed.emit()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        self._position = seconds
        self.audio_backend.seek(seconds)
        self.position_changed.emit()
        self.seeked.emit(seconds)

    @Slot(float)
    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        self.audio_backend.set_volume(self._volume)
        # Recorded on every change, not on the way out: a drag emits a value per
        # frame and the settings store already absorbs that — it holds the value
        # in memory and writes the file once, at shutdown.
        self._settings.volume = self._volume
        self.volume_changed.emit()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_and_play(self, track_id: int) -> None:
        """Load a track by ID and immediately start playback."""
        self.load_track(track_id)
        self.audio_backend.play()

    def _play_current(self) -> None:
        """Start whatever navigation left at the front of the queue."""
        current = self.queue_manager.current()
        if current:
            self._load_and_play(current["id"])

    def _resolve_entries(self, track_ids: list) -> list[dict]:
        """Turn a list of ids into queue entries, dropping ones that no longer
        resolve. One query rather than one per id."""
        return self._resolve_entry_data([{"id": int(t)} for t in track_ids])

    def _resolve_entry_data(self, entries: list) -> list[dict]:
        ids = [int(entry["id"]) for entry in entries]
        if not ids:
            return []
        existing = {t.id for t in self.track_service.get_tracks_by_ids(ids)}
        return [dict(entry, id=track_id) for entry, track_id in zip(entries, ids)
                if track_id in existing]

    def _apply_mutation(self, mutation: QueueMutation) -> None:
        """
        Fan out a QueueMutation to both view models so each emits the
        correct begin/end signals. Called after every queue navigation.

        Navigation also drops selection — a row played through leaves the queue
        and loses it, and one pulled back out of history re-enters as a new
        instance that was never selected. The manager has already done that by
        the time we get here, so each model is asked to re-announce its
        selection once the row signals are out; without it the GUI's mirror
        keeps a uid that no longer exists and the details panel goes on showing
        a track that is no longer selected.
        """
        if self._queue_vm:
            if mutation.queue_moved is not None:
                # One call, not a removal followed by a reorder: the steps are
                # indexed against each other's intermediate states, so they have
                # to be walked together.
                self._queue_vm.notify_played(
                    mutation.queue_removed_index, mutation.queue_moved)
            elif mutation.queue_removed_index is not None:
                self._queue_vm.notify_removed(mutation.queue_removed_index)
            if mutation.queue_inserted_index is not None:
                self._queue_vm.notify_inserted(mutation.queue_inserted_index)
            if mutation.queue_reset:
                self._queue_vm.notify_reset()
            self._queue_vm.refresh_selection()

        if self._history_vm:
            if mutation.history_removed_index is not None:
                self._history_vm.notify_top_removed()
            if mutation.history_inserted:
                self._history_vm.notify_prepended()
            if mutation.history_reset:
                self._history_vm.notify_reset()
            self._history_vm.refresh_selection()

    # ------------------------------------------------------------------
    # Backing tracks
    # ------------------------------------------------------------------

    def _backing_enabled(self) -> bool:
        """Whether instrumentals are wanted *and* obtainable."""
        return self._backing is not None and self._backing_settings.enabled

    def _category_for(self, track_id: int) -> str:
        """Which variant of a track should play: ``"bt"`` or ``"mix"``."""
        if self._backing_enabled() and self._backing.has(track_id):  # type: ignore[union-attr]
            return BACKING_CATEGORY
        return DEFAULT_CATEGORY

    @Slot()
    def toggleBackingTrack(self) -> None:  # noqa: N802  (Qt slot, camelCase for QML)
        """Flip between the instrumental and the mix.

        A mode, not a per-track choice: it persists across queue advances, which
        is what makes the missing-instrumental policy mean anything.
        """
        self.backingTrackEnabled = not self._backing_settings.enabled

    def _get_backing_enabled(self) -> bool:
        return self._backing_settings.enabled

    def _set_backing_enabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._backing_settings.enabled:
            return
        self._backing_settings.enabled = value

        # Apply to whatever is playing right now. The backend no-ops when the
        # variant is already the one loaded, and refuses when the file is
        # missing, so a track with no instrumental simply keeps playing its mix
        # while the mode stays on for everything that does have one.
        if self._current_track is not None:
            self.audio_backend.crossfade_to(
                self._category_for(cast(int, self._current_track.id))
            )

        self.backing_track_changed.emit()

    def _get_backing_missing_behavior(self) -> str:
        return self._backing_settings.missing_behavior

    def _set_backing_missing_behavior(self, value: str) -> None:
        if value == self._backing_settings.missing_behavior:
            return
        self._backing_settings.missing_behavior = value
        self.backing_track_changed.emit()

    def _get_backing_gain_db(self) -> float:
        return self._backing_settings.gain_db

    def _set_backing_gain_db(self, value: float) -> None:
        # Compared after the write, not before it, so the settings' own clamp is
        # what the comparison sees: nudging past the end of the range twice is
        # not a change, and QML must not be told it was one.
        previous = self._backing_settings.gain_db
        self._backing_settings.gain_db = value
        applied = self._backing_settings.gain_db
        if applied == previous:
            return
        self.audio_backend.set_backing_gain_db(applied)
        self.backing_track_changed.emit()

    backingTrackEnabled = Property(
        bool, _get_backing_enabled, _set_backing_enabled,
        notify=backing_track_changed,
    )
    backingMissingBehavior = Property(
        str, _get_backing_missing_behavior, _set_backing_missing_behavior,
        notify=backing_track_changed,
    )
    backingGainDb = Property(
        float, _get_backing_gain_db, _set_backing_gain_db,
        notify=backing_track_changed,
    )

    # ------------------------------------------------------------------
    # The track editor's gain preview
    # ------------------------------------------------------------------
    #
    # Connected to `TrackEditViewModel` in bootstrap rather than called by it.
    # Deciding whether a trim is audible means knowing what is playing, which
    # is this object's business and none of the editor's — the editor knows
    # only which track it has open, and says so.

    @Slot(int, float)
    def set_gain_preview(self, track_id: int, decibels: float) -> None:
        """Hear an unsaved gain, if *track_id* is the track playing.

        Nothing is persisted and nothing is staged here: the backend's override
        lasts until the next `load()`, so abandoning a preview costs nothing.
        """
        if self._current_track is None or int(track_id) != int(self._current_track.id):
            return
        self.audio_backend.set_track_gain_db(decibels)

    @Slot()
    def clear_gain_preview(self) -> None:
        """Drop any previewed gain and fall back to what is stored.

        "Stored" rather than "the value before the preview" is what makes this
        correct after a save as well as after a revert: by the time the editor
        clears, it has re-read the row, so falling back lands on whatever was
        actually committed either way.
        """
        if self._current_track is None:
            return
        self.audio_backend.set_track_gain_db(
            getattr(self._current_track, "gain_db", 0.0) or 0.0
        )

    def _on_backing_availability_changed(self, track_id: int) -> None:
        """An instrumental appeared or vanished somewhere in the library.

        Fired from the import and generation workers, so this can arrive on a
        worker thread; emitting a signal across threads is queued by Qt, which
        is how the rest of the app already handles worker-thread events.
        """
        current = self._current_track
        if current is not None and int(track_id) == cast(int, current.id):
            # Start playing it if the mode is on and we are on the mix only
            # because there was nothing else to play.
            self.audio_backend.crossfade_to(self._category_for(cast(int, current.id)))
        self.backing_track_changed.emit()

    # ------------------------------------------------------------------
    # Playback policy settings
    #
    # Backend-owned: they change what the queue does, not how it looks. A GUI
    # binds these rather than storing its own copy.
    # ------------------------------------------------------------------

    def _get_listened_threshold(self) -> float:
        return self._settings.listened_threshold_seconds

    def _set_listened_threshold(self, value: float) -> None:
        if value == self._settings.listened_threshold_seconds:
            return
        self._settings.listened_threshold_seconds = value
        self.playback_settings_changed.emit()

    def _get_max_history(self) -> int:
        return self._settings.max_history_size

    def _set_max_history(self, value: int) -> None:
        if value == self._settings.max_history_size:
            return
        self._settings.max_history_size = value
        # Apply immediately: shrinking the cap should trim history now, not on
        # the next restart.
        self.queue_manager.max_history = self._settings.max_history_size
        if self._history_vm:
            self._history_vm.notify_reset()
        self.playback_settings_changed.emit()

    def _get_queue_limit(self) -> int:
        return self._settings.persisted_queue_limit

    def _set_queue_limit(self, value: int) -> None:
        if value == self._settings.persisted_queue_limit:
            return
        self._settings.persisted_queue_limit = value
        self.playback_settings_changed.emit()

    listenedThresholdSeconds = Property(
        float, _get_listened_threshold, _set_listened_threshold,
        notify=playback_settings_changed,
    )
    maxHistorySize = Property(
        int, _get_max_history, _set_max_history, notify=playback_settings_changed,
    )
    persistedQueueLimit = Property(
        int, _get_queue_limit, _set_queue_limit, notify=playback_settings_changed,
    )

    # ------------------------------------------------------------------
    # Output device
    #
    # Also backend-owned, for the same reason as the volume: which speaker the
    # audio comes out of is what the player does. A second GUI inherits it.
    # ------------------------------------------------------------------

    def _get_audio_output_device_id(self) -> str:
        return self._settings.output_device

    def _set_audio_output_device_id(self, value: str) -> None:
        chosen = value or ""
        if chosen == self._settings.output_device:
            return

        # The name is stored alongside so a Bluetooth device can be recognised
        # after it reconnects under a different id. Looked up now, while the
        # device is in front of us — there is nothing to look it up from later.
        name = ""
        if chosen:
            for device in self.audio_backend.available_output_devices():
                if device_id(device) == chosen:
                    name = device.description()
                    break

        self._settings.output_device = chosen
        self._settings.output_device_name = name
        self.audio_backend.set_output_device(chosen, name)
        self.audio_devices_changed.emit()

    audioOutputDeviceId = Property(
        str, _get_audio_output_device_id, _set_audio_output_device_id,
        notify=audio_devices_changed,
    )

    @Property("QVariantList", notify=audio_devices_changed)
    def audioOutputDevices(self) -> list:
        """Every output device on the machine, for a picker to list.

        ``isDefault`` marks the one the system would pick on its own;
        ``isActive`` the one audio is coming out of right now. Those differ
        whenever a device is pinned, and the pinned one is missing while it is
        disconnected — which is why the picker must not build its selected state
        from this list. See :attr:`audioOutputDeviceId`.
        """
        active = self.audio_backend.active_output_device_id
        return [
            {
                "id": device_id(device),
                "name": device.description(),
                "isDefault": device.isDefault(),
                "isActive": device_id(device) == active,
            }
            for device in self.audio_backend.available_output_devices()
        ]

    @Property(str, notify=audio_devices_changed)
    def activeAudioOutputName(self) -> str:
        """The device audio is actually coming out of, named for display."""
        return self.audio_backend.active_output_device_name

    @Property(bool, notify=audio_devices_changed)
    def audioOutputDeviceMissing(self) -> bool:
        """A device is pinned, but it is not here — so the default is in use."""
        return not self.audio_backend.output_device_available

    def _on_output_devices_changed(self) -> None:
        """The devices changed, and the backend may have re-pinned itself.

        It adopts a new id when the pinned device comes back under a different
        Bluetooth profile. Writing that back is what keeps the saved pin matching
        by id — otherwise every future match would go through the name, and the
        picker would have no listed device to tick.
        """
        adopted = self.audio_backend.output_device_id
        if adopted != self._settings.output_device:
            self._settings.output_device = adopted
        self.audio_devices_changed.emit()

    # ------------------------------------------------------------------
    # Audio backend callbacks
    # ------------------------------------------------------------------

    def _on_position_changed(self, position: float) -> None:
        """The only writer of ``_listened``.

        It is a one-way latch per track, reset in ``load_track``: once a track
        has been heard past the threshold, seeking backwards does not un-hear
        it.
        """
        self._position = position
        if not self._listened and position >= self._settings.listened_threshold_seconds:
            self._listened = True
        self.position_changed.emit()

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        self._is_playing = is_playing
        self.is_playing_changed.emit()

    def _on_track_finished(self) -> None:
        """Auto-advance when a track reaches its end."""
        self.next()
