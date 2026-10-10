"""Library core — the view-agnostic data layer the GUI binds to.

This package holds everything a GUI needs to show tracks without the backend
knowing anything about how they are shown:

- ``fields``    — what a track exposes, and how expensive each field is.
- ``row_cache`` — a shared, id-keyed row cache filled on demand.
- ``track_query`` — a list model whose rows are ids until someone paints them.
- ``context``   — how a QML-constructed ``TrackQuery`` finds its services.

Nothing in here knows about columns, colours, widths, or screens.
"""
