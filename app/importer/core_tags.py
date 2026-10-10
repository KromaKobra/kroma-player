"""The Vorbis comments Kroma writes into a track's file as its own.

A module of its own, with no imports, so the plugin manifest parser can refuse
a ``writeTag`` that names one of them without loading the tagging code, and so
a plugin's tests can run that parser without Kroma's audio libraries.
"""
from __future__ import annotations

#: Every Vorbis comment the core writes and reads back as its own. A plugin
#: names the tag one of its id kinds is mirrored into (``writeTag``), and it
#: may not name one of these: the manifest refuses it, and
#: :func:`app.importer.tagging.tag_opus_file` would not let it through anyway.
#: ``LOCALID`` is how a file is matched back to its row, and a plugin able to
#: overwrite it could break that link for every file it touched.
CORE_TAGS: frozenset[str] = frozenset({
    "LOCALID", "TITLE", "ARTIST", "ALBUM", "DATE", "COPYRIGHT", "GENRE", "KEY",
    "BPM", "CONTENTTYPE", "AUDIOPROCESSING", "ISRC", "LYRICS", "TRANSCODEDFROM",
    "METADATA_BLOCK_PICTURE",
})
