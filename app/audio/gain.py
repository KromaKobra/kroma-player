"""Level trims, in decibels, and the one place they become a multiplier.

Two different trims share this module. The instrumental trim
(:class:`~app.settings.backing_track.BackingTrackSettings`) is a blanket
correction for what separation loses, stored in the settings; a track's own
``gain_db`` is a correction for how *one recording* was mastered, stored on the
row. They answer different questions and a track can want both, so they stack —
but the arithmetic and the bounds are the same either way, and duplicating
``10 ** (db / 20)`` across the backend and the editor is how the two would
eventually disagree.

Both are playback-time trims. **Nothing here ever rewrites an audio file**, so
raising or lowering a value later costs nothing and loses nothing.
"""
from __future__ import annotations

# Bounds for a track's level trim, in decibels. Deliberately the same range as
# the instrumental trim: wide enough to rescue a recording mastered well below
# everything around it, narrow enough that a mistyped value cannot make the next
# track deafening.
MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 12.0

# Zero means "play it as it was recorded", which is why it is the default: a
# correction is only right for the recording that needed it.
DEFAULT_GAIN_DB = 0.0


def clamp_gain_db(value) -> float:
    """Coerce *value* to decibels within the allowed range.

    Anything unparseable is treated as absent rather than raising — a hand-edited
    database or a GUI sending nonsense should land on "no correction", not stop
    a track from playing.
    """
    try:
        return max(MIN_GAIN_DB, min(float(value), MAX_GAIN_DB))
    except (TypeError, ValueError):
        return DEFAULT_GAIN_DB


def db_to_linear(decibels) -> float:
    """Turn a decibel trim into the factor an audio output multiplies by.

    Unity on bad input, for the same reason :func:`clamp_gain_db` returns zero:
    the failure mode of a level control should be "no effect", never silence and
    never full scale.
    """
    try:
        return 10.0 ** (float(decibels) / 20.0)
    except (TypeError, ValueError):
        return 1.0
