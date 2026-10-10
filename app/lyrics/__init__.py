"""Everything about lyrics that is not the database and not the view model.

Four modules, layered the way ``docs/guides/add-a-feature.md`` prescribes —
nothing below :mod:`app.lyrics.workers` imports Qt, and nothing here imports
SQLAlchemy:

- :mod:`app.lyrics.parse`  — pure functions over a lyric document
- :mod:`app.lyrics.tags`   — reading the ``LYRICS`` tag out of an Opus file
- :mod:`app.lyrics.lookup` — searching the network for a track's lyrics
- :mod:`app.lyrics.workers` — the two of those that need a thread

Everything here is **read-only**. Writing lyrics — to the database and then to
the file, in that order — is
:class:`app.services.lyrics_service.LyricsService`, not anything here: it
touches the library, and services are where those live.
"""
