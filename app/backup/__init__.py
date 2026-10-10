"""Incremental backup of the vault to a folder somewhere else.

The vault is already a portable, self-contained directory. This package makes a
second copy of it that can be brought up to date cheaply, by writing only what
actually differs rather than re-copying gigabytes of Opus that are already
there and already identical.

The layering matches :mod:`app.exporter`: everything here is free of Qt and of
SQLAlchemy so it can be driven from a test or from a worker thread, and
:mod:`app.backup.workers` is the only thing standing between it and the view
model.

Two passes, always in this order:

- :func:`app.backup.scan.build_plan` reads both sides and works out what would
  change. It writes nothing, and it is what the user is shown.
- :func:`app.backup.apply.apply_plan` executes exactly the plan it is handed.

They are separate because the destination is a folder the user picked, every
audio file in a vault is named only by its track id, and two unrelated vaults
therefore have colliding filenames from top to bottom. Nothing may be
overwritten before someone has seen what would be overwritten.
"""
