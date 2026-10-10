"""MPRIS2 D-Bus service — the app's presence on the desktop.

Registering here is what makes the hardware media keys work. On a modern Linux
desktop nothing delivers ``XF86AudioPlay`` to an application: the compositor or
shell grabs it and forwards the intent to whichever MPRIS player is active
(GNOME and KDE do it internally; Hyprland users bind the key to ``playerctl`` or
a shell dispatcher). The same registration is what puts the track, its artwork
and a working scrubber into the system media widget and the lock screen, and
what lets ``mpris-proxy`` route Bluetooth headset buttons here.

So this module writes no key handling and no playback logic. Every method maps
onto a slot ``PlayerViewModel`` already has, and every property reads state the
view model already publishes to QML. It is a second front-end onto the same
view model, spoken over D-Bus instead of QML.

Spec: https://specifications.freedesktop.org/mpris-spec/latest/

Deliberate omissions
--------------------
``LoopStatus`` and ``Shuffle`` are absent. Kroma's queue has no such modes —
shuffle is an action that builds a shuffled queue, not a state — and
``gui/nocturne/README.md`` states the rule this follows: *a control that
silently does nothing is worse than no control*. Both properties are optional
in the spec, and clients grey out the buttons when they are missing, which is
the honest outcome. See "Adding LoopStatus / Shuffle later" on
:class:`_PlayerAdaptor` for what to change when the queue grows the modes.

``TrackList`` and ``Playlists`` are not implemented either; ``HasTrackList`` is
false accordingly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import ClassInfo, QMetaType, QObject, QTimer, Property, Signal, Slot

from app.system import mpris_art, mpris_metadata

logger = logging.getLogger(__name__)

# QtDBus ships inside PySide6 on every platform, so this import normally
# succeeds even on Windows where there is no bus to connect to — the absence of
# a *bus* is handled in start(). The guard is for stripped or minimal builds:
# it keeps the module importable so the DI container can construct the service
# unconditionally, with start() being the single place that says no.
try:
    from PySide6.QtDBus import (
        QDBusAbstractAdaptor,
        QDBusArgument,
        QDBusConnection,
        QDBusMessage,
        QDBusObjectPath,
    )

    QTDBUS_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - platform dependent
    QDBusAbstractAdaptor = QObject  # type: ignore[assignment,misc]
    QDBusArgument = QDBusConnection = QDBusMessage = QDBusObjectPath = None  # type: ignore[assignment]
    QTDBUS_IMPORT_ERROR = _exc

# MPRIS types SetPosition's first argument as an object path ("o"), not a
# string. Declaring it as str would introspect as "(sx)" and every spec-correct
# client's seek would bounce off a signature mismatch.
_OBJECT_PATH_ARG = QDBusObjectPath if QDBusObjectPath is not None else str

OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# The bus name's last element identifies the app. It must match
# [A-Za-z_][A-Za-z0-9_-]* — "kroma-player" would be legal here but the trailing
# element is also what some clients show, so keep it aligned with the desktop
# entry basename.
BUS_NAME = "org.mpris.MediaPlayer2.kromaplayer"

IDENTITY = "Kroma Player"
DESKTOP_ENTRY = "kroma-player"


def _empty_string_array():
    """An empty D-Bus array typed as strings ("as"), not variants ("av").

    This exists because of a real, silent interoperability failure. The third
    argument of ``PropertiesChanged`` is the list of invalidated property names,
    always empty here since every change is announced with its new value — and
    PySide6 marshals a plain empty Python list as ``av``, because with no
    elements there is nothing to infer the element type from. The signal then
    goes out as ``(sa{sv}av)`` where the spec says ``(sa{sv}as)``.

    Nothing rejects it loudly. ``dbus-monitor`` prints an empty array either way.
    But GLib clients drop the whole message — playerctl logs *"Value for
    PropertiesChanged signal with type '(sa{sv}av)' does not match
    '(sa{sv}as)'"* — and Qt-based shells silently ignore it too, so every client
    keeps its first reading forever. The symptom is a media widget stuck on
    "paused" and a play/pause key that sends Play to an already-playing track,
    with no error anywhere to explain it.

    Streaming an empty array through QDBusArgument is what pins the element
    type, since PySide6 exposes no other way to name it.
    """
    argument = QDBusArgument()
    argument.beginArray(QMetaType.Type.QString)
    argument.endArray()
    return argument


class MprisUnavailable(RuntimeError):
    """Raised when the session bus cannot be used.

    Not an error condition — it is the normal state on Windows, in a bare CI
    container, and under sandboxes with no bus socket. Callers log it and carry
    on with the app fully functional minus system media controls.
    """


@ClassInfo({"D-Bus Interface": ROOT_IFACE})
class _RootAdaptor(QDBusAbstractAdaptor):
    """``org.mpris.MediaPlayer2`` — how the app presents itself.

    ``DesktopEntry`` is the mechanism behind "make it an official playback
    device": it names ``kroma-player.desktop``, from which the shell reads the
    app's real name and icon for the media widget instead of guessing from the
    process name. It is only a promise, though — the entry has to actually be
    installed, which is what ``scripts/install-desktop-entry`` is for.
    """

    def __init__(self, service: "MprisService") -> None:
        super().__init__(service)
        self._service = service

    # -- properties ----------------------------------------------------
    # Method names are the D-Bus member names, hence the capitalisation
    # throughout this module; the wire contract wins over PEP 8 here.

    def _can_quit(self) -> bool:
        return True

    def _can_raise(self) -> bool:
        # Answered honestly: without a window bound there is nothing to raise.
        return self._service.window is not None

    def _has_track_list(self) -> bool:
        return False

    def _identity(self) -> str:
        return IDENTITY

    def _desktop_entry(self) -> str:
        return DESKTOP_ENTRY

    def _supported_uri_schemes(self) -> list:
        # OpenUri is not supported; an empty list is how the spec says so.
        return []

    def _supported_mime_types(self) -> list:
        return []

    CanQuit = Property(bool, _can_quit)
    CanRaise = Property(bool, _can_raise)
    HasTrackList = Property(bool, _has_track_list)
    Identity = Property(str, _identity)
    DesktopEntry = Property(str, _desktop_entry)
    SupportedUriSchemes = Property("QStringList", _supported_uri_schemes)
    SupportedMimeTypes = Property("QStringList", _supported_mime_types)

    # -- methods -------------------------------------------------------

    @Slot()
    def Raise(self) -> None:
        self._service.raise_window()

    @Slot()
    def Quit(self) -> None:
        self._service.quit_app()


@ClassInfo({"D-Bus Interface": PLAYER_IFACE})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    """``org.mpris.MediaPlayer2.Player`` — transport, position and metadata.

    Every method delegates to :class:`~app.viewmodels.player_view_model.PlayerViewModel`.
    Note that they arrive on the Qt event loop like any other queued call, so
    they run on the GUI thread and need no extra synchronisation.

    Adding LoopStatus / Shuffle later
    ---------------------------------
    When the queue gains repeat and shuffle modes, three edits light them up —
    no restructuring, because registration and change-notification are already
    generic over property names:

    1. Add ``LoopStatus = Property(str, getter, setter)`` and
       ``Shuffle = Property(bool, getter, setter)`` in the marked block below,
       mapping onto the new view-model properties. LoopStatus is one of
       ``"None"``, ``"Track"``, ``"Playlist"``; Kroma's repeat-queue is
       ``"Playlist"``.
    2. Add their names to the dirty set in ``MprisService`` on whichever notify
       signal the view model emits for them.
    3. Nothing else. Introspection picks new properties up from the metaobject,
       and clients that gate on ``loopSupported`` / ``shuffleSupported`` enable
       their buttons on their own.
    """

    Seeked = Signal("qlonglong")

    def __init__(self, service: "MprisService") -> None:
        super().__init__(service)
        self._service = service

    # -- read-only properties ------------------------------------------

    def _playback_status(self) -> str:
        vm = self._service.player
        return mpris_metadata.playback_status(vm.is_playing, vm.has_current_track)

    def _metadata(self) -> dict:
        return self._service.metadata()

    def _position(self) -> int:
        return mpris_metadata.to_micros(self._service.player.position)

    def _rate(self) -> float:
        return 1.0

    def _set_rate(self, value: float) -> None:
        # The backend has no rate control. Min == max == 1.0 tells clients that,
        # and the spec says a value outside that range may be ignored — so this
        # accepts the write and does nothing rather than erroring at a client
        # that set the only legal value.
        return None

    def _minimum_rate(self) -> float:
        return 1.0

    def _maximum_rate(self) -> float:
        return 1.0

    def _volume(self) -> float:
        return self._service.player.volume

    def _set_volume(self, value: float) -> None:
        # Both scales are 0.0–1.0, so no conversion; set_volume already clamps.
        self._service.player.set_volume(float(value))

    def _can_go_next(self) -> bool:
        return self._service.player.can_go_next

    def _can_go_previous(self) -> bool:
        return self._service.player.can_go_previous

    def _can_play(self) -> bool:
        return True

    def _can_pause(self) -> bool:
        return True

    def _can_seek(self) -> bool:
        return self._service.player.has_current_track

    def _can_control(self) -> bool:
        return True

    PlaybackStatus = Property(str, _playback_status)
    Metadata = Property("QVariantMap", _metadata)
    Position = Property("qlonglong", _position)
    Rate = Property(float, _rate, _set_rate)
    MinimumRate = Property(float, _minimum_rate)
    MaximumRate = Property(float, _maximum_rate)
    Volume = Property(float, _volume, _set_volume)
    CanGoNext = Property(bool, _can_go_next)
    CanGoPrevious = Property(bool, _can_go_previous)
    CanPlay = Property(bool, _can_play)
    CanPause = Property(bool, _can_pause)
    CanSeek = Property(bool, _can_seek)
    CanControl = Property(bool, _can_control)

    # --- optional properties: LoopStatus, Shuffle ---
    # Intentionally absent while the queue has no such modes. See the class
    # docstring for the three-step recipe to add them.

    # -- methods -------------------------------------------------------

    @Slot()
    def Play(self) -> None:
        self._service.player.play()

    @Slot()
    def Pause(self) -> None:
        self._service.player.pause()

    @Slot()
    def PlayPause(self) -> None:
        self._service.player.toggle_playback()

    @Slot()
    def Stop(self) -> None:
        self._service.player.stop_player()

    @Slot()
    def Next(self) -> None:
        self._service.player.next()

    @Slot()
    def Previous(self) -> None:
        self._service.player.previous()

    @Slot("qlonglong")
    def Seek(self, offset: int) -> None:
        """Seek by a relative offset in microseconds; may be negative."""
        vm = self._service.player
        if not vm.has_current_track:
            return
        target = vm.position + mpris_metadata.from_micros(offset)
        # The spec: seeking past the end is "the same as a Next", and seeking
        # before the start clamps to zero.
        if vm.duration and target >= vm.duration:
            vm.next()
            return
        vm.seek(max(0.0, target))

    @Slot(_OBJECT_PATH_ARG, "qlonglong")
    def SetPosition(self, track_id, position: int) -> None:
        """Seek to an absolute position, guarded by the track it was meant for.

        The trackid check is the spec's protection against a stale seek: a
        client that renders a scrubber for track A, then has the track change
        under it before the user releases the drag, would otherwise jump 90
        seconds into track B.
        """
        vm = self._service.player
        if not vm.has_current_track:
            return
        path = track_id.path() if hasattr(track_id, "path") else str(track_id)
        if mpris_metadata.track_id_from_path(path) != vm.current_track_id:
            return
        seconds = mpris_metadata.from_micros(position)
        if seconds < 0 or (vm.duration and seconds > vm.duration):
            return
        vm.seek(seconds)

    @Slot(str)
    def OpenUri(self, uri: str) -> None:
        """No-op: ``SupportedUriSchemes`` is empty, so no client should call it.

        The method still has to exist — it is part of the interface, and a
        missing member is an introspection failure rather than a graceful
        "unsupported".
        """
        logger.debug("Ignoring MPRIS OpenUri(%s): no URI schemes supported", uri)


class MprisService(QObject):
    """Owns the bus connection, the adaptors, and the view-model wiring.

    Construct freely — it touches nothing until :meth:`start`, so the DI
    container can build it on every platform.
    """

    def __init__(
        self,
        player_view_model,
        cover_store=None,
        art_cache_dir: Optional[Path] = None,
        app=None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.player = player_view_model
        self._cover_store = cover_store
        self._art_cache_dir = art_cache_dir
        self._app = app
        self.window = None

        self._bus = None
        self._bus_name: Optional[str] = None
        self._root: Optional[_RootAdaptor] = None
        self._player_adaptor: Optional[_PlayerAdaptor] = None

        # Metadata is rebuilt on track change rather than on every property
        # read: a client polling Position also GetAll's the interface, and
        # exporting artwork on each of those would be wasteful.
        self._metadata_cache: dict[str, Any] = {"mpris:trackid": mpris_metadata.NO_TRACK_PATH}

        # Change notification is coalesced. Loading a track emits four view-model
        # signals back to back, and each would otherwise be its own bus message.
        self._dirty: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)  # end of the current event-loop turn
        self._flush_timer.timeout.connect(self._flush_properties)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Register on the session bus.

        Raises:
            MprisUnavailable: if QtDBus is missing, there is no session bus, or
                registration is refused. Always the caller's cue to continue
                without system integration, never to abort startup.
        """
        if QTDBUS_IMPORT_ERROR is not None:  # pragma: no cover - platform dependent
            raise MprisUnavailable(f"QtDBus is not available: {QTDBUS_IMPORT_ERROR}")

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise MprisUnavailable("no session bus")

        self._root = _RootAdaptor(self)
        self._player_adaptor = _PlayerAdaptor(self)

        if not bus.registerObject(OBJECT_PATH, self, QDBusConnection.RegisterOption.ExportAdaptors):
            raise MprisUnavailable(f"could not register object at {OBJECT_PATH}")

        name = BUS_NAME
        if not bus.registerService(name):
            # Another instance holds the primary name. The spec's answer is a
            # per-instance suffix, so both stay individually addressable rather
            # than the second one going invisible.
            name = f"{BUS_NAME}.instance{os.getpid()}"
            if not bus.registerService(name):
                bus.unregisterObject(OBJECT_PATH)
                raise MprisUnavailable(f"could not acquire a bus name (tried {BUS_NAME} and {name})")

        self._bus = bus
        self._bus_name = name
        self._connect_view_model()
        self._rebuild_metadata()
        logger.info("MPRIS registered as %s", name)

    def stop(self) -> None:
        """Unregister. Safe to call when never started, or twice."""
        if self._bus is None:
            return
        self._flush_timer.stop()
        if self._bus_name:
            self._bus.unregisterService(self._bus_name)
        self._bus.unregisterObject(OBJECT_PATH)
        self._bus = None
        self._bus_name = None

    @property
    def bus_name(self) -> Optional[str]:
        """The acquired bus name, or None when not registered."""
        return self._bus_name

    def set_window(self, window) -> None:
        """Bind the main window so ``Raise`` can bring it forward.

        Supplied after the QML engine has loaded rather than at construction,
        because the root object does not exist until then.
        """
        self.window = window
        self._mark_dirty(ROOT_IFACE, "CanRaise")

    # ------------------------------------------------------------------
    # Root interface actions
    # ------------------------------------------------------------------

    def raise_window(self) -> None:
        window = self.window
        if window is None:
            return
        # The window is frameless and may be minimised or on another workspace;
        # show() alone leaves it behind whatever has focus.
        window.show()
        window.raise_()
        window.requestActivate()

    def quit_app(self) -> None:
        if self._app is not None:
            self._app.quit()

    # ------------------------------------------------------------------
    # View-model wiring
    # ------------------------------------------------------------------

    def _connect_view_model(self) -> None:
        vm = self.player
        vm.current_track_changed.connect(self._on_track_changed)
        vm.is_playing_changed.connect(
            lambda: self._mark_dirty(PLAYER_IFACE, "PlaybackStatus")
        )
        vm.duration_changed.connect(self._on_duration_changed)
        vm.volume_changed.connect(lambda: self._mark_dirty(PLAYER_IFACE, "Volume"))
        vm.seeked.connect(self._on_seeked)
        # position_changed is deliberately not connected. Position is excluded
        # from PropertiesChanged by the spec precisely because it advances
        # continuously; clients poll it and use Seeked for discontinuities.
        # Connecting it would put several messages a second on the bus.

    def _on_track_changed(self) -> None:
        self._rebuild_metadata()
        self._mark_dirty(
            PLAYER_IFACE,
            "Metadata",
            "PlaybackStatus",
            "CanGoNext",
            "CanGoPrevious",
            "CanSeek",
        )

    def _on_duration_changed(self) -> None:
        # Duration lands in mpris:length, so the cached metadata has to be
        # rebuilt: it is often only known once the decoder has opened the file,
        # i.e. after the track change that already rebuilt it.
        self._rebuild_metadata()
        self._mark_dirty(PLAYER_IFACE, "Metadata")

    def _on_seeked(self, seconds: float) -> None:
        if self._player_adaptor is not None:
            self._player_adaptor.Seeked.emit(mpris_metadata.to_micros(seconds))

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def metadata(self) -> dict:
        return self._metadata_cache

    def _rebuild_metadata(self) -> None:
        vm = self.player
        if not vm.has_current_track:
            self._metadata_cache = self._as_dbus(mpris_metadata.build_metadata(None, 0.0, None))
            return

        track = self._fetch_track(vm.current_track_id)
        art = None
        if self._cover_store is not None and self._art_cache_dir is not None:
            art = mpris_art.art_url(
                mpris_art.export_cover(
                    self._cover_store, vm.current_track_cover_hash, self._art_cache_dir
                )
            )
        self._metadata_cache = self._as_dbus(
            mpris_metadata.build_metadata(track, vm.duration, art)
        )

    @staticmethod
    def _as_dbus(meta: dict) -> dict:
        """Apply the D-Bus typing that the pure metadata builder can't.

        ``mpris:trackid`` is an object path ("o"), not a string. Keeping the
        builder free of Qt types is what lets it be tested without a bus, so
        the conversion happens here, at the boundary.

        Known deviation: the spec types ``mpris:length`` as int64 ("x"), and
        PySide6 marshals a Python int as "i" whenever it fits in 32 bits, so
        tracks under ~35 minutes advertise their length as int32. The *value*
        is always correct — only the signature narrows, and it widens to "x" on
        its own for anything longer. It cannot be pinned from Python: PySide6
        exposes no QVariant and QDBusArgument's typed insertion is not bound,
        so nothing here can name the metatype. Clients read metadata as a
        variant map and coerce, so this is cosmetic; the alternative would be
        replacing QtDBus with a pure-Python bus library, which means a new
        dependency and its own event loop for a type width nothing trips on.
        """
        if QDBusObjectPath is None:  # pragma: no cover - platform dependent
            return meta
        typed = dict(meta)
        track_id = typed.get("mpris:trackid")
        if isinstance(track_id, str):
            typed["mpris:trackid"] = QDBusObjectPath(track_id)
        return typed

    def _fetch_track(self, track_id: int):
        """Load the playing track with its relationships eager-loaded.

        Goes through ``get_tracks_by_ids`` rather than the singular fetch
        because that one joins artists and genres in: metadata reads
        ``track.genres``, and a lazy load on a detached instance would raise.
        """
        if track_id is None or track_id < 0:
            return None
        try:
            rows = self.player.track_service.get_tracks_by_ids([int(track_id)])
            return rows[0] if rows else None
        except Exception:
            logger.debug("Could not load track %s for MPRIS metadata", track_id, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # PropertiesChanged
    # ------------------------------------------------------------------

    def _mark_dirty(self, interface: str, *names: str) -> None:
        if self._bus is None:
            return
        for name in names:
            self._dirty.add(f"{interface}\x00{name}")
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_properties(self) -> None:
        """Emit one PropertiesChanged per interface for everything that changed."""
        if self._bus is None or not self._dirty:
            self._dirty.clear()
            return

        pending, self._dirty = self._dirty, set()
        by_interface: dict[str, dict[str, Any]] = {}
        for key in pending:
            interface, name = key.split("\x00", 1)
            adaptor = self._root if interface == ROOT_IFACE else self._player_adaptor
            if adaptor is None:
                continue
            # Read back through the adaptor's own property so the announced
            # value is by construction the one a Get would return.
            by_interface.setdefault(interface, {})[name] = adaptor.property(name)

        for interface, changed in by_interface.items():
            self._emit_properties_changed(interface, changed)

    def _emit_properties_changed(self, interface: str, changed: dict) -> None:
        message = QDBusMessage.createSignal(OBJECT_PATH, PROPERTIES_IFACE, "PropertiesChanged")
        message.setArguments([interface, changed, _empty_string_array()])
        self._bus.send(message)
