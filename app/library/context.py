"""How a QML-constructed object finds its backend services.

A type registered with ``qmlRegisterType`` is instantiated by QML, which means
it must be default-constructible — QML cannot pass it a repository. So the
services are parked here once, during bootstrap, and every ``TrackQuery`` a GUI
declares resolves through this module.

This is the only global in the library core, and it is deliberate: the
alternative is making every GUI thread a service object through its QML tree,
which would put backend plumbing back into the thing we are trying to keep free
of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

if TYPE_CHECKING:
    from app.events.event_bus import EventBus
    from app.library.row_cache import TrackRowCache
    from app.repositories.track_repository import TrackRepository


@dataclass(frozen=True)
class LibraryContext:
    """Everything a list model needs to resolve rows."""

    repository: "TrackRepository"
    row_cache: "TrackRowCache"
    event_bus: "EventBus"
    #: The external-id kinds enabled plugins declare, asked for at paint time.
    #: Absent — no plugin host, as in most tests — means none: a track's ids
    #: are kept and simply not shown, exactly as with every plugin off.
    id_kinds: Optional[Callable[[], Sequence[Any]]] = None


def declared_id_kinds() -> Sequence[Any]:
    """What the installed context says is declared, or nothing."""
    if _context is None or _context.id_kinds is None:
        return ()
    try:
        return tuple(_context.id_kinds())
    except Exception:  # noqa: BLE001 — a paint must never fail over this
        return ()


_context: Optional[LibraryContext] = None


def set_library_context(context: LibraryContext) -> None:
    """Install the context. Called once, from bootstrap, before the engine loads."""
    global _context
    _context = context


def library_context() -> LibraryContext:
    """Return the installed context.

    Raises:
        RuntimeError: if a list model is constructed before bootstrap ran. That
            is a wiring bug, and failing loudly beats a silently empty list.
    """
    if _context is None:
        raise RuntimeError(
            "Library context not set — call set_library_context() during bootstrap "
            "before loading the QML engine."
        )
    return _context


def clear_library_context() -> None:
    """Drop the context. For tests that want a clean slate."""
    global _context
    _context = None
