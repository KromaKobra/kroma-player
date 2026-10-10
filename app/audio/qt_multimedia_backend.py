"""Qt Multimedia audio backend implementation.

Two decks, not one. A deck is a ``QMediaPlayer`` plus its own ``QAudioOutput``;
one is *active* (audible, and the only one whose signals are believed) and the
other is idle, standing by to take over.

The second deck exists for exactly one reason: switching between a mix and its
instrumental mid-playback has to be inaudible. Doing that on a single player
cannot work — ``setSource`` is asynchronous, so there is a gap between the old
media stopping and the new one being seekable, and no amount of care hides it.
With two decks the replacement is already playing, in sync and silent, before
anything is taken away from the listener.

Both decks always point at the same output device, and this module is the only
thing that decides which one. That is not a nicety: Qt pins each stream to the
device its ``QAudioOutput`` was holding, and tears the stream down and rebuilds
it on every ``setSource``. So a device chosen anywhere else — the system mixer,
say — survives exactly as long as the current track, and the next one snaps back
to whatever the output was constructed with. Owning the choice here is what makes
it stick.
"""
import contextlib
import os
import sys

from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from PySide6.QtCore import QUrl, Signal, QObject, QVariantAnimation, QEasingCurve
from app.audio.gain import db_to_linear
from app.models.track import Track
from app.services.audio_import_service import AudioImportService
from app.utils.library_paths import BACKING_CATEGORY, DEFAULT_CATEGORY


# @contextlib.contextmanager # TEMPORARY FUNCTION (see below)
# def _suppress_stderr():
#     """Temporarily silence native stderr output from FFmpeg/Qt Multimedia."""
#     stderr_fd = sys.stderr.fileno()
#     saved_stderr = os.dup(stderr_fd)
#     try:
#         with open(os.devnull, "w") as devnull:
#             os.dup2(devnull.fileno(), stderr_fd)
#             yield
#     finally:
#         os.dup2(saved_stderr, stderr_fd)
#         os.close(saved_stderr)

# Long enough to cover the decode latency of the incoming deck and to mask the
# seam, short enough that nobody perceives it as a transition.
FADE_MS = 150

# Statuses at which a freshly-set source will accept a seek. Setting a position
# before this lands nowhere on some backends, which is the whole reason the
# swap is gated rather than fired immediately after setSource.
_SEEKABLE = (
    QMediaPlayer.MediaStatus.LoadedMedia,
    QMediaPlayer.MediaStatus.BufferedMedia,
)

# The stored value meaning "whatever the system is currently using". Not a
# device id, so it can never collide with one.
SYSTEM_DEFAULT_DEVICE = ""


def device_id(device) -> str:
    """A :class:`QAudioDevice`'s id as text.

    Qt hands the id back as a ``QByteArray``; everything above this module —
    settings, the GUI API — deals in strings.
    """
    return bytes(device.id()).decode("utf-8", "replace")


def resolve_output_device(devices, wanted_id: str, wanted_name: str):
    """Find the pinned device in *devices*, or ``None`` for the system default.

    Matched by id first and by description second, because a Bluetooth id
    carries the profile index it was connected under —
    ``bluez_output.66_78_14_B3_3A_24.1`` — and that index changes when the
    headphones reconnect. Falling through to the name is what lets the same
    pair of headphones be recognised as the ones that were pinned.

    ``None`` means "use the system default", which is both the unpinned case and
    the answer when a pinned device is not here right now. The caller keeps the
    preference either way, so the device is picked up again when it returns.
    """
    if wanted_id == SYSTEM_DEFAULT_DEVICE:
        return None

    for device in devices:
        if device_id(device) == wanted_id:
            return device

    if wanted_name:
        for device in devices:
            if device.description() == wanted_name:
                return device

    return None


class _Deck(QObject):
    """One player and its output, plus what it has been asked to do next."""

    def __init__(self) -> None:
        super().__init__()
        self.player = QMediaPlayer()
        self.output = QAudioOutput()
        self.player.setAudioOutput(self.output)

        # Which variant this deck holds. Owned per-deck rather than globally so
        # a load that never completes cannot leave the backend claiming to be
        # playing something it is not.
        self.category: str = DEFAULT_CATEGORY

        # Deferred instructions, applied once the media reports it is seekable.
        self.pending_seek_ms: int | None = None
        self.pending_play: bool = False
        self.pending_fade: bool = False


class QtMultimediaBackend(QObject): # Should also inhert from AudioBackend, but that causes metaclass issues with QObject. Still works without inheritance.
    """Audio backend using Qt Multimedia."""

    position_changed = Signal(float)
    track_finished = Signal()
    playback_state_changed = Signal(bool)
    # The set of output devices changed, or the one in use did. Carries nothing:
    # what is available and what is active are both read back off the backend.
    output_devices_changed = Signal()

    def __init__(self):
        """Initialize Qt Multimedia backend."""
        super().__init__()
        self._decks = (_Deck(), _Deck())
        self._active_index = 0
        self.current_track = None

        # Which output device to use. Empty id = follow the system default.
        # The description is kept alongside the id purely so a reconnected
        # Bluetooth device can still be recognised — see resolve_output_device.
        self._device_id = SYSTEM_DEFAULT_DEVICE
        self._device_name = ""

        # Master volume. The outputs are driven to fractions of this during a
        # fade, so it — not the output's own value — is the truth.
        self._volume = 1.0
        # Linear multiplier applied on top of it for instrumentals only, so the
        # two variants of a track can be levelled against each other. 1.0 = off.
        self._backing_gain = 1.0
        # The loaded track's own trim, for a recording mastered louder or
        # quieter than the rest of the library. Re-read from the Track on every
        # `load()`, so it never outlives the track it belongs to. 1.0 = off.
        self._track_gain = 1.0
        self._fade: QVariantAnimation | None = None

        for deck in self._decks:
            deck.player.positionChanged.connect(
                lambda ms, d=deck: self._on_position_changed(d, ms)
            )
            deck.player.mediaStatusChanged.connect(
                lambda status, d=deck: self._on_media_status_changed(d, status)
            )
            deck.player.playbackStateChanged.connect(
                lambda state, d=deck: self._on_playback_state_changed(d, state)
            )

        self._active.output.setVolume(self._level(self._active))

        # Last, because applying a device reads the fade and the levels above.
        # Held as an attribute rather than used statically: the notifications
        # only come from a live instance, and they are the whole point. Devices
        # appear and disappear while the app runs, and a pinned one that comes
        # back has to be picked up again.
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self._apply_output_device()

    # ------------------------------------------------------------------
    # Output device
    # ------------------------------------------------------------------

    @property
    def output_device_id(self) -> str:
        """The pinned device's id, or ``""`` when following the system default.

        What was *asked for*, not what is in use — the two differ while a pinned
        device is disconnected. See :attr:`active_output_device_id`.
        """
        return self._device_id

    @property
    def output_device_name(self) -> str:
        """The pinned device's description, kept for the reconnect match."""
        return self._device_name

    @property
    def active_output_device_id(self) -> str:
        """The id of the device audio is actually coming out of."""
        return device_id(self._active.output.device())

    @property
    def active_output_device_name(self) -> str:
        """The description of the device audio is actually coming out of."""
        return self._active.output.device().description()

    @property
    def output_device_available(self) -> bool:
        """Whether the pinned device is here. Trivially true with none pinned."""
        if self._device_id == SYSTEM_DEFAULT_DEVICE:
            return True
        return resolve_output_device(
            QMediaDevices.audioOutputs(), self._device_id, self._device_name
        ) is not None

    def available_output_devices(self) -> list:
        """Every output device Qt can see, in Qt's order."""
        return list(QMediaDevices.audioOutputs())

    def set_output_device(self, device_id_: str, device_name: str = "") -> None:
        """Pin playback to a device, or to the system default with ``""``.

        Takes effect on whatever is playing rather than on the next track: this
        is chosen by ear, with something audible, and a setting that only
        applied later would be untestable by the person setting it.
        """
        self._device_id = device_id_ or SYSTEM_DEFAULT_DEVICE
        self._device_name = device_name or ""
        if self._apply_output_device():
            self.output_devices_changed.emit()

    def _on_audio_outputs_changed(self) -> None:
        """The machine's output devices changed under us.

        Re-resolving covers both directions at once: a pinned device that has
        gone hands playback to the system default, and one that comes back takes
        it again. With nothing pinned this is what follows the default around.
        """
        self._apply_output_device()
        # Emitted whether or not the active device moved: the *list* changed,
        # and anything showing it — the settings picker — is looking at it now.
        self.output_devices_changed.emit()

    def _apply_output_device(self) -> bool:
        """Point both decks at the resolved device. The only caller of setDevice.

        Both, always. The decks trade places at the end of a crossfade, so an
        output device set on one of them is a device that lasts until the next
        instrumental toggle.

        Returns whether anything moved.
        """
        # setDevice rebuilds the stream, and a fade owns both outputs for 150 ms
        # while it runs. Land it first, for the same reason seek() does.
        self._cancel_fade()

        target = resolve_output_device(
            QMediaDevices.audioOutputs(), self._device_id, self._device_name
        )
        if target is None:
            target = QMediaDevices.defaultAudioOutput()
        elif device_id(target) != self._device_id:
            # Found by description, so this is the pinned device back under a new
            # profile id. Adopt the new id: matching by name is the fallback, and
            # leaving the stale one stored would keep it the only thing working.
            self._device_id = device_id(target)

        changed = False
        for deck in self._decks:
            if deck.output.device().id() == target.id():
                continue
            deck.output.setDevice(target)
            # The sink was rebuilt underneath it, so the level is re-asserted
            # from _level() — which is the authority on it — rather than trusted
            # to have survived.
            deck.output.setVolume(self._level(deck))
            changed = True

        return changed

    # ------------------------------------------------------------------
    # Decks
    # ------------------------------------------------------------------

    @property
    def _active(self) -> _Deck:
        """The audible deck — the only one whose signals mean anything."""
        return self._decks[self._active_index]

    @property
    def _idle(self) -> _Deck:
        return self._decks[1 - self._active_index]

    @property
    def current_category(self) -> str:
        """Which variant is audible right now — ``"mix"`` or ``"bt"``."""
        return self._active.category

    @property
    def player(self):
        """The active deck's player.

        Kept as an attribute-style accessor because this backend used to expose
        a single ``player`` directly and outside code may still reach for it.
        """
        return self._active.player

    @property
    def audio_output(self):
        """The active deck's output. See :attr:`player`."""
        return self._active.output

    def _level(self, deck: _Deck) -> float:
        """What *deck* should come out at — master volume, trimmed twice.

        The two trims stack because they answer different questions. The track's
        own gain says this *recording* was mastered away from everything around
        it, and holds for both of its variants; the backing gain says separation
        handed back a quieter instrumental than the mix it came from, and holds
        for every track. A quietly-mastered song with a quiet instrumental wants
        both, and neither is a substitute for the other.

        Which is also why only the second one is per-deck: during a handover the
        two decks hold different variants of the same recording, and the whole
        point of that trim is that those two do not play at the same level. The
        track gain is the same on both, because it is the same recording.

        A boost cannot exceed the output's ceiling: Qt clamps at 1.0 and there
        is no headroom above it, so with the volume already near maximum a large
        boost simply arrives as full scale. That only costs the *difference* at
        the very top of the slider, and it is preferable to the alternative of
        quietly turning everything else down to make room.
        """
        level = self._volume * self._track_gain
        if deck.category == BACKING_CATEGORY:
            level *= self._backing_gain
        return max(0.0, min(1.0, level))

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def load(self, track: Track, category: str = DEFAULT_CATEGORY) -> None:
        """Load a track, replacing whatever was playing."""
        self._cancel_fade()
        self._silence(self._idle)

        self.current_track = track
        track_path = AudioImportService.resolve_track(track, category)

        # Read the trim off the track we were handed, before anything is
        # audible. Doing it here rather than plumbing it in separately is what
        # keeps a level correction from ever arriving a moment late — the gain
        # travels with the recording it belongs to, and `load()` already has it.
        self._track_gain = db_to_linear(getattr(track, "gain_db", 0.0) or 0.0)

        deck = self._active
        deck.category = category
        deck.pending_seek_ms = None
        deck.pending_play = False
        deck.pending_fade = False
        deck.output.setVolume(self._level(deck))
        # with _suppress_stderr(): # NOTE: Temporary fix for silencing stderr for bugfixing. Later the statement below can be called directly and _suppress_stderr can be removed.
        deck.player.setSource(QUrl.fromLocalFile(track_path))

    def crossfade_to(self, category: str) -> None:
        """Switch the playing track to another variant of itself, seamlessly.

        Same recording, same position, different file — a mix and its
        instrumental. No-op if nothing is loaded or the variant is already the
        one playing.
        """
        if self.current_track is None or category == self.current_category:
            return

        target = AudioImportService.resolve_track(self.current_track, category)
        if not target.exists():
            return

        # A fade already running is finished instantly rather than queued, so
        # there is never more than one handover in flight.
        self._cancel_fade()

        position_ms = self._active.player.position()

        if not self._active.player.isPlaying():
            # Nothing to mask while paused: swap the source underneath and
            # restore the playhead once the new file is seekable.
            deck = self._active
            deck.category = category
            deck.pending_seek_ms = position_ms
            deck.pending_play = False
            deck.pending_fade = False
            # The variant changed under it, so the trim that goes with it has to
            # be in place before the first sample is heard on resume.
            deck.output.setVolume(self._level(deck))
            deck.player.setSource(QUrl.fromLocalFile(target))
            return

        incoming = self._idle
        incoming.category = category
        incoming.output.setVolume(0.0)
        incoming.pending_seek_ms = position_ms
        incoming.pending_play = True
        incoming.pending_fade = True
        incoming.player.setSource(QUrl.fromLocalFile(target))

    def play(self) -> None:
        """Start playback."""
        self._active.player.play()

    def pause(self) -> None:
        """Pause playback."""
        self._active.player.pause()

    def resume(self) -> None:
        """Resume playback."""
        self._active.player.play()

    def stop(self) -> None:
        """Stop playback."""
        self._cancel_fade()
        self._active.player.stop()

    def clear(self) -> None:
        """Clear the currently loaded media source."""
        self._cancel_fade()
        self.current_track = None
        for deck in self._decks:
            deck.category = DEFAULT_CATEGORY
            self._silence(deck)
        self._active.output.setVolume(self._level(self._active))

    def seek(self, seconds: float) -> None:
        """Seek to position."""
        # Land the fade first: seeking two decks that are mid-handover would
        # have to keep them in sync through the seek, and the fade is 150 ms.
        self._cancel_fade()
        self._active.player.setPosition(int(seconds * 1000))

    def set_volume(self, volume: float) -> None:
        """Set volume."""
        self._volume = max(0.0, min(1.0, volume))
        if self._fade is None:
            self._active.output.setVolume(self._level(self._active))
        # Mid-fade the animation owns both outputs and picks the new master up
        # on its next tick, a few milliseconds away.

    def set_backing_gain_db(self, decibels: float) -> None:
        """Set the level trim applied to instrumentals, in decibels.

        Takes effect on whatever is playing, not just on the next track: the
        point of the setting is to be dialled in by ear against something
        audible, which is impossible if it only applies later.
        """
        self._backing_gain = db_to_linear(decibels)
        if self._fade is None:
            self._active.output.setVolume(self._level(self._active))

    def set_track_gain_db(self, decibels: float) -> None:
        """Set the level trim for the loaded track, in decibels.

        Takes effect on whatever is playing, for the same reason the
        instrumental trim does: a level is dialled in by ear against something
        audible, and a control that only applies to the *next* track cannot be.

        This is an override of what `load()` read off the track, and it lives
        only until the next `load()` — which is exactly what makes the editor's
        live preview free to abandon: nothing has to be undone when the user
        moves on, because playing anything else re-reads the stored value.
        """
        self._track_gain = db_to_linear(decibels)
        if self._fade is None:
            self._active.output.setVolume(self._level(self._active))

    def get_position(self) -> float:
        """Get current position."""
        return self._active.player.position() / 1000.0

    def is_playing(self) -> bool:
        """Check if playing."""
        return self._active.player.isPlaying()

    # ------------------------------------------------------------------
    # Crossfade
    # ------------------------------------------------------------------

    def _start_fade(self) -> None:
        """Ramp the incoming deck up and the outgoing one down."""
        outgoing = self._active
        incoming = self._idle

        fade = QVariantAnimation(self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(FADE_MS)
        fade.setEasingCurve(QEasingCurve.Type.Linear)
        fade.valueChanged.connect(
            lambda t: self._apply_fade(outgoing, incoming, float(t))
        )
        fade.finished.connect(self._finish_fade)
        self._fade = fade
        fade.start()

    def _apply_fade(self, outgoing: _Deck, incoming: _Deck, t: float) -> None:
        # Each end of the fade is levelled for the variant it is holding, so a
        # mix→instrumental handover lands on the trimmed level rather than
        # arriving at the mix's and stepping afterwards.
        outgoing.output.setVolume(self._level(outgoing) * (1.0 - t))
        incoming.output.setVolume(self._level(incoming) * t)

    def _finish_fade(self) -> None:
        """Hand over: the incoming deck becomes active, the old one is dropped."""
        if self._fade is None:
            return
        self._fade = None

        outgoing = self._active
        # Swap before silencing, so the old deck's stop is filtered out as a
        # non-active deck's signal instead of being reported as "playback
        # stopped" to the rest of the app.
        self._active_index = 1 - self._active_index
        self._active.output.setVolume(self._level(self._active))
        self._silence(outgoing)

    def _cancel_fade(self) -> None:
        """Complete any running fade immediately rather than leaving it half-done."""
        if self._fade is None:
            return
        # stop() does not emit finished(), so the handover is run by hand.
        self._fade.stop()
        self._finish_fade()

    def _silence(self, deck: _Deck) -> None:
        """Stop a deck and let go of its media."""
        deck.pending_seek_ms = None
        deck.pending_play = False
        deck.pending_fade = False
        deck.player.stop()
        deck.player.setSource(QUrl())

    ### Qt signal handlers ###
    #
    # Every handler takes the deck that emitted it and ignores anything from
    # the deck that is not active. During a handover both decks are playing the
    # same recording, and only one of them is the app's idea of "playback".

    def _on_position_changed(self, deck: _Deck, position_ms: int) -> None:
        """Handle Qt playback position updates."""
        if deck is not self._active:
            return
        self.position_changed.emit(position_ms / 1000.0)

    def _on_media_status_changed(self, deck: _Deck, status) -> None:
        """Apply deferred seeks, and detect the end of the track."""
        if status in _SEEKABLE and deck.pending_seek_ms is not None:
            deck.player.setPosition(deck.pending_seek_ms)
            deck.pending_seek_ms = None
            if deck.pending_play:
                deck.pending_play = False
                deck.player.play()
            return

        if status == QMediaPlayer.MediaStatus.InvalidMedia and deck is not self._active:
            # The replacement never loaded, so there is nothing to hand over
            # to. Drop it and stay where we are — the deck's own `category`
            # goes with it, so `current_category` still reports the truth.
            self._silence(deck)
            deck.category = self._active.category
            return

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Mid-fade the outgoing deck can run out while the incoming one is
            # still coming up. The track has not finished — its replacement is
            # playing the same seconds — so let the new active deck say so when
            # it genuinely gets there.
            if deck is not self._active or self._fade is not None:
                return
            self.track_finished.emit()

    def _on_playback_state_changed(self, deck: _Deck, state) -> None:
        """True = playing | False = paused/stopped."""
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)

        if deck.pending_fade and is_playing:
            # The incoming deck is running. Re-sync to the outgoing deck's
            # playhead — it has advanced since the seek was queued — then start
            # the handover. This bounds drift to a single event-loop tick.
            deck.pending_fade = False
            deck.player.setPosition(self._active.player.position())
            self._start_fade()
            return

        if deck is not self._active:
            return
        self.playback_state_changed.emit(is_playing)
