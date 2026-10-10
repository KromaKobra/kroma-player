"""Backend-owned playback policy, read and written through the settings store.

These are not presentation. "How long must a track play before it counts as
listened" changes what the queue *does*, so it belongs to the backend and would
still make sense if the frontend were a CLI — unlike a column width, which the
GUI owns outright. They live in the ``app`` scope of ``settings.json``, through
:class:`~app.settings.store.SettingsStore`.

The store keeps strings and never interprets them, so coercion and clamping
happen here, once, rather than at every reader.
"""
from __future__ import annotations

from typing import Optional

from app.settings.store import SettingsStore

# Keys, and the values used when the store has nothing to say.
LISTENED_THRESHOLD_KEY = "listened_threshold_seconds"
MAX_HISTORY_KEY = "max_history_size"
QUEUE_LIMIT_KEY = "persisted_queue_limit"
VOLUME_KEY = "volume"
OUTPUT_DEVICE_KEY = "audio_output_device"
OUTPUT_DEVICE_NAME_KEY = "audio_output_device_name"

DEFAULT_LISTENED_THRESHOLD = 3.0
DEFAULT_MAX_HISTORY = 100
DEFAULT_QUEUE_LIMIT = 500
DEFAULT_VOLUME = 0.2
# Empty = follow whatever the system is using. Not a device id, so it can never
# collide with one.
DEFAULT_OUTPUT_DEVICE = ""
DEFAULT_OUTPUT_DEVICE_NAME = ""


def _as_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _as_str(value) -> str:
    """Coerce to text, treating absence as the empty string.

    No default parameter because every string key here defaults to empty and
    means the same thing by it: nothing was chosen.
    """
    return "" if value is None else str(value)


class PlaybackSettings:
    """Typed accessors over the three playback keys.

    Constructed with ``None`` it simply serves the defaults, so anything that
    builds a :class:`~app.viewmodels.player_view_model.PlayerViewModel` without
    a session — a test, a script — still gets the documented behaviour.
    """

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    # -- listen threshold ----------------------------------------------

    @property
    def listened_threshold_seconds(self) -> float:
        if self._store is None:
            return DEFAULT_LISTENED_THRESHOLD
        return _as_float(
            self._store.app_get(LISTENED_THRESHOLD_KEY, DEFAULT_LISTENED_THRESHOLD),
            DEFAULT_LISTENED_THRESHOLD, 0.0, 60.0,
        )

    @listened_threshold_seconds.setter
    def listened_threshold_seconds(self, value: float) -> None:
        if self._store is not None:
            self._store.app_set(
                LISTENED_THRESHOLD_KEY,
                _as_float(value, DEFAULT_LISTENED_THRESHOLD, 0.0, 60.0),
            )

    # -- history cap ----------------------------------------------------

    @property
    def max_history_size(self) -> int:
        if self._store is None:
            return DEFAULT_MAX_HISTORY
        return _as_int(
            self._store.app_get(MAX_HISTORY_KEY, DEFAULT_MAX_HISTORY),
            DEFAULT_MAX_HISTORY, 1, 10_000,
        )

    @max_history_size.setter
    def max_history_size(self, value: int) -> None:
        if self._store is not None:
            self._store.app_set(
                MAX_HISTORY_KEY, _as_int(value, DEFAULT_MAX_HISTORY, 1, 10_000)
            )

    # -- queue persistence cap ------------------------------------------

    @property
    def persisted_queue_limit(self) -> int:
        if self._store is None:
            return DEFAULT_QUEUE_LIMIT
        return _as_int(
            self._store.app_get(QUEUE_LIMIT_KEY, DEFAULT_QUEUE_LIMIT),
            DEFAULT_QUEUE_LIMIT, 0, 100_000,
        )

    @persisted_queue_limit.setter
    def persisted_queue_limit(self, value: int) -> None:
        if self._store is not None:
            self._store.app_set(
                QUEUE_LIMIT_KEY, _as_int(value, DEFAULT_QUEUE_LIMIT, 0, 100_000)
            )

    # -- output level ---------------------------------------------------
    #
    # Backend state rather than a GUI preference: the level the audio comes out
    # at is what the player *does*, and a second GUI should inherit it rather
    # than start over at a default. Zero is a real saved value — muting and
    # closing has to come back muted, or the setting is only half remembered.

    @property
    def volume(self) -> float:
        if self._store is None:
            return DEFAULT_VOLUME
        return _as_float(
            self._store.app_get(VOLUME_KEY, DEFAULT_VOLUME), DEFAULT_VOLUME, 0.0, 1.0
        )

    @volume.setter
    def volume(self, value: float) -> None:
        if self._store is not None:
            self._store.app_set(VOLUME_KEY, _as_float(value, DEFAULT_VOLUME, 0.0, 1.0))

    # -- output device --------------------------------------------------
    #
    # Same reasoning as the level: which speaker the audio comes out of is what
    # the player does, not how a GUI draws it. Empty means "follow the system
    # default", which is both the shipped behaviour and what an unrecognised
    # value degrades to.
    #
    # The description is stored beside the id because a Bluetooth id embeds the
    # profile the device connected under, and that changes when it reconnects.
    # Without the name, a pair of headphones would be a different device every
    # time it was switched on.

    @property
    def output_device(self) -> str:
        if self._store is None:
            return DEFAULT_OUTPUT_DEVICE
        return _as_str(self._store.app_get(OUTPUT_DEVICE_KEY, DEFAULT_OUTPUT_DEVICE))

    @output_device.setter
    def output_device(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(OUTPUT_DEVICE_KEY, _as_str(value))

    @property
    def output_device_name(self) -> str:
        if self._store is None:
            return DEFAULT_OUTPUT_DEVICE_NAME
        return _as_str(
            self._store.app_get(OUTPUT_DEVICE_NAME_KEY, DEFAULT_OUTPUT_DEVICE_NAME)
        )

    @output_device_name.setter
    def output_device_name(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(OUTPUT_DEVICE_NAME_KEY, _as_str(value))
