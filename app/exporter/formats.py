"""The output formats export can produce.

One module so the engine, the settings façade, the view model and the GUI's
format picker all spell the formats the same way. Adding a third format means
adding it here, teaching :mod:`app.exporter.export` how to write it, and
nothing else.
"""
from __future__ import annotations

FORMAT_OPUS = "opus"
FORMAT_MP3 = "mp3"

# Order matters: this is the order the GUI paints the choices in.
EXPORT_FORMATS = (FORMAT_OPUS, FORMAT_MP3)
DEFAULT_FORMAT = FORMAT_OPUS

# Labels for display. The leading dot is what makes it read as a file format
# rather than a codec name.
FORMAT_LABELS = {FORMAT_OPUS: ".opus", FORMAT_MP3: ".mp3"}

# The library stores 192k Opus, so an MP3 export is always a second-generation
# encode. 320k CBR costs disk space to buy back most of what that would
# otherwise lose, and disk space is not what someone exporting a playlist to a
# USB stick is short of.
MP3_BITRATE = "320k"


def extension(fmt: str) -> str:
    """Return the filename extension for *fmt*, including the dot."""
    return f".{normalize(fmt)}"


def label(fmt: str) -> str:
    """Return the human-facing name for *fmt*."""
    return FORMAT_LABELS[normalize(fmt)]


def normalize(value) -> str:
    """Coerce *value* to a supported format name.

    Anything unrecognised becomes the default rather than raising — a settings
    file hand-edited to nonsense should fall back, not stop the app starting.
    """
    if isinstance(value, str) and value.lower() in EXPORT_FORMATS:
        return value.lower()
    return DEFAULT_FORMAT
