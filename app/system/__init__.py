"""Desktop-environment integration for Kroma Player.

Everything in this package talks to the machine the app is running on rather
than to the library: the session bus, the audio server's client identity, the
window manager. It is the only place that is allowed to fail because of the
environment instead of because of a bug.

Contents
--------
- ``mpris_metadata`` — pure conversions between Kroma's model and the MPRIS2
  wire format. No Qt, no D-Bus, no I/O.
- ``mpris_art``      — bridges the AVIF-in-SQLite cover cache to the plain image
  file that ``mpris:artUrl`` has to point at.
- ``mpris``          — the D-Bus service itself.

Design contract
---------------
* **Optional, never load-bearing.** There is no session bus on Windows, none in
  a bare CI container, and none under some sandboxes. Every entry point here
  either degrades to a no-op or raises :class:`~app.system.mpris.MprisUnavailable`
  for the caller to swallow. Playback must behave identically with the whole
  package inert.
* **The system is a mirror, not a source of truth.** State flows out of
  ``PlayerViewModel`` and commands flow back into its existing slots. Nothing
  here owns playback state or reimplements queue policy.
"""

from __future__ import annotations
