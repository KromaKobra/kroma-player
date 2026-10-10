"""What a track's external ids are worth on screen, given what is enabled.

A track carries ids whatever plugins happen to be on — the core never clears
one — but whether the user *sees* any of them depends on the plugins that are:
an id no enabled plugin declares has no label, no link and nothing that can act
on it, so it is not shown. Turning the plugin back on brings it back exactly as
it was, because nothing here ever wrote.

Three answers, all derived on demand and never stored:

- ``externalIds`` — the ids an enabled plugin declares, as
  ``{"provider:kind": value}``;
- ``externalUrl`` — where the first recording id with a URL template points;
- ``sourceLabel`` — what that id is called, for a SOURCE column.

*kinds* is any sequence of declarations with ``entity``, ``key``, ``role``,
``label``, ``source_label`` and ``url_for`` — :class:`app.plugins.manifest.IdKind`
— passed in rather than imported, so this module stays a leaf of the library.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


def declared_ids(ids: Optional[Mapping[str, Any]], kinds: Sequence[Any],
                 entity: str = "track") -> Dict[str, str]:
    """The subset of *ids* some declaration in *kinds* names."""
    if not ids:
        return {}
    out: Dict[str, str] = {}
    for kind in kinds:
        if getattr(kind, "entity", entity) != entity:
            continue
        value = ids.get(kind.key)
        if value:
            out[kind.key] = str(value)
    return out


def _recording(ids: Optional[Mapping[str, Any]], kinds: Sequence[Any], entity: str):
    for kind in kinds:
        if getattr(kind, "entity", entity) != entity:
            continue
        if entity == "track" and getattr(kind, "role", "recording") != "recording":
            continue
        value = (ids or {}).get(kind.key)
        if value:
            yield kind, str(value)


def external_url(ids: Optional[Mapping[str, Any]], kinds: Sequence[Any],
                 entity: str = "track") -> str:
    """The first declared link for this entity's own id, or ``""``."""
    for kind, value in _recording(ids, kinds, entity):
        url = kind.url_for(value)
        if url:
            return url
    return ""


def source_label(ids: Optional[Mapping[str, Any]], kinds: Sequence[Any],
                 entity: str = "track") -> str:
    """What the first declared id of this entity is called, or ``""``."""
    for kind, _value in _recording(ids, kinds, entity):
        return kind.source_label or kind.label
    return ""


def identity_value(key: str, ids: Optional[Mapping[str, Any]], kinds: Sequence[Any]) -> Any:
    """One of the three fields, by its catalog key."""
    if key == "externalIds":
        return declared_ids(ids, kinds)
    if key == "externalUrl":
        return external_url(ids, kinds)
    if key == "sourceLabel":
        return source_label(ids, kinds)
    return None
