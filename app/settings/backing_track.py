"""Backend-owned backing-track policy and generation configuration.

Same reasoning as :mod:`app.settings.playback`: none of this is presentation.
Whether the instrumental or the mix is playing changes what you hear, and "skip
tracks that have no instrumental" changes what the queue *does* — both would
still make sense if the frontend were a CLI. So they live in the ``app`` scope of
``settings.json``, through :class:`~app.settings.store.SettingsStore`, not in a
GUI package's own state.

The generation settings sit here too, for a duller reason: they are machine
configuration (which interpreter, which model file, which device), and a GUI
that stored them would lose them the moment the user switched interfaces.

The store keeps strings and never interprets them, so coercion and clamping
happen here, once, rather than at every reader.
"""
from __future__ import annotations

from typing import Optional

from app.settings.store import SettingsStore

# Keys, and the values used when the store has nothing to say.
ENABLED_KEY = "backing_track_enabled"
MISSING_BEHAVIOR_KEY = "backing_track_missing_behavior"
GAIN_DB_KEY = "backing_track_gain_db"
MODEL_FILE_KEY = "backing_track_model_file"
DEVICE_KEY = "backing_track_device"
PYTHON_PATH_KEY = "backing_track_python_path"
MSST_PATH_KEY = "backing_track_msst_path"

# What to do when the mode is on and a track has no instrumental.
BEHAVIOR_PLAY_MIX = "mix"
BEHAVIOR_SKIP = "skip"
MISSING_BEHAVIORS = (BEHAVIOR_PLAY_MIX, BEHAVIOR_SKIP)

# "auto" resolves at generation time to the best device the sidecar reports.
DEVICE_AUTO = "auto"
DEVICES = (DEVICE_AUTO, "cuda", "rocm", "cpu")

# Bounds for the instrumental level trim, in decibels. Wide enough to undo the
# level a separation model typically loses, narrow enough that a mistyped value
# cannot make the next track deafening.
MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 12.0

DEFAULT_ENABLED = False
DEFAULT_MISSING_BEHAVIOR = BEHAVIOR_PLAY_MIX
DEFAULT_GAIN_DB = 0.0
DEFAULT_MODEL_FILE = ""
DEFAULT_DEVICE = DEVICE_AUTO
DEFAULT_PYTHON_PATH = ""
DEFAULT_MSST_PATH = ""


def _as_choice(value, default: str, allowed: tuple[str, ...]) -> str:
    """Coerce to one of *allowed*, falling back to *default*.

    Anything unrecognised is treated as absent rather than raising: a settings
    file hand-edited to nonsense should degrade to the default, not stop the
    app from starting.
    """
    if isinstance(value, str) and value in allowed:
        return value
    return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return default


def _as_text(value, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


class BackingTrackSettings:
    """Typed accessors over the backing-track keys.

    Constructed with ``None`` it simply serves the defaults, so anything built
    without a session — a test, a script — still gets the documented behaviour.
    """

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    # -- playback policy -------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether instrumentals play in place of mixes where one exists.

        Persisted because it is a mode, not a per-track choice: turning it on
        and closing the app should not silently turn it off again.
        """
        if self._store is None:
            return DEFAULT_ENABLED
        return _as_bool(self._store.app_get(ENABLED_KEY, DEFAULT_ENABLED), DEFAULT_ENABLED)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if self._store is not None:
            self._store.app_set(ENABLED_KEY, bool(value))

    @property
    def missing_behavior(self) -> str:
        """``"mix"`` or ``"skip"`` — what happens to a track with no instrumental."""
        if self._store is None:
            return DEFAULT_MISSING_BEHAVIOR
        return _as_choice(
            self._store.app_get(MISSING_BEHAVIOR_KEY, DEFAULT_MISSING_BEHAVIOR),
            DEFAULT_MISSING_BEHAVIOR, MISSING_BEHAVIORS,
        )

    @missing_behavior.setter
    def missing_behavior(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(
                MISSING_BEHAVIOR_KEY,
                _as_choice(value, DEFAULT_MISSING_BEHAVIOR, MISSING_BEHAVIORS),
            )

    @property
    def gain_db(self) -> float:
        """Level trim applied to every instrumental, in decibels.

        Separation models reconstruct the instrumental from the mix and tend to
        hand back something quieter than the same music was in it, so playing
        one is a step down in level even though nothing about the volume
        changed. This is a blanket correction for that — one number for the
        whole library rather than a per-track edit — and it is deliberately a
        playback-time trim: the stored file is never rewritten, so raising or
        lowering it later costs nothing and loses nothing.

        Zero means "play it as it was generated", which is why it is the
        default: the correction is only right for the model that needed it.
        """
        if self._store is None:
            return DEFAULT_GAIN_DB
        return _as_float(
            self._store.app_get(GAIN_DB_KEY, DEFAULT_GAIN_DB),
            DEFAULT_GAIN_DB, MIN_GAIN_DB, MAX_GAIN_DB,
        )

    @gain_db.setter
    def gain_db(self, value: float) -> None:
        if self._store is not None:
            self._store.app_set(
                GAIN_DB_KEY,
                _as_float(value, DEFAULT_GAIN_DB, MIN_GAIN_DB, MAX_GAIN_DB),
            )

    # -- generation ------------------------------------------------------

    @property
    def model_file(self) -> str:
        """File name (not path) of the chosen model inside ``.data/models/``.

        Stored as a bare name so moving the vault does not invalidate it.
        """
        if self._store is None:
            return DEFAULT_MODEL_FILE
        return _as_text(self._store.app_get(MODEL_FILE_KEY, DEFAULT_MODEL_FILE),
                        DEFAULT_MODEL_FILE)

    @model_file.setter
    def model_file(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(MODEL_FILE_KEY, _as_text(value, DEFAULT_MODEL_FILE))

    @property
    def device(self) -> str:
        if self._store is None:
            return DEFAULT_DEVICE
        return _as_choice(
            self._store.app_get(DEVICE_KEY, DEFAULT_DEVICE), DEFAULT_DEVICE, DEVICES
        )

    @device.setter
    def device(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(DEVICE_KEY, _as_choice(value, DEFAULT_DEVICE, DEVICES))

    @property
    def python_path(self) -> str:
        """Interpreter that runs the separation sidecar.

        Kroma's own interpreter cannot do it — PyTorch publishes no wheels for
        the Python this app runs on — so generation is delegated to a separate
        environment the user owns. Empty means generation is unavailable.
        """
        if self._store is None:
            return DEFAULT_PYTHON_PATH
        return _as_text(self._store.app_get(PYTHON_PATH_KEY, DEFAULT_PYTHON_PATH),
                        DEFAULT_PYTHON_PATH)

    @python_path.setter
    def python_path(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(PYTHON_PATH_KEY, _as_text(value, DEFAULT_PYTHON_PATH))

    @property
    def msst_path(self) -> str:
        """Folder of a Music-Source-Separation-Training checkout, if any.

        The second way to run a model, and the only one available to people
        whose PyTorch came from a vendor index — pip resolving
        ``audio-separator``'s dependencies against a ROCm or CUDA wheel set is
        how a working GPU install becomes a CPU-only one. MSST is not on PyPI,
        so it is named by folder rather than imported.

        Empty means "work it out": the sidecar looks beside the interpreter,
        which is where a checkout's own virtualenv normally sits.
        """
        if self._store is None:
            return DEFAULT_MSST_PATH
        return _as_text(self._store.app_get(MSST_PATH_KEY, DEFAULT_MSST_PATH),
                        DEFAULT_MSST_PATH)

    @msst_path.setter
    def msst_path(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(MSST_PATH_KEY, _as_text(value, DEFAULT_MSST_PATH))
