"""Staging an entity's external ids in an editor — and the traps that come with it.

Every editor (track, artist, album) shows one text field per kind of id an
enabled plugin declares, and saves only what the user changed. That sounds like
three lines and is where data gets lost, so the rules live here once:

1. **Every id the row holds is staged, whatever the plugin state.** The
   baseline is everything the row holds, declared or not. A kind it holds
   nothing for reads as ``""`` in both maps, so the diff on save can only ever
   see what moved.
2. **Only kinds that changed are sent.** A save with no id edits sends no ids at
   all, and the service leaves every id exactly as it was.
3. **A kind no enabled plugin declares cannot be edited.** It is not offered as
   a field, so its staged value can never move — and so it can never be sent,
   least of all as an empty string meaning "clear". Turning a plugin off hides
   its ids; it never deletes them.
4. **What is typed is parsed by the declaring plugin's own rules** — a pasted
   link becomes the bare id. Text that parses to nothing is refused with the
   kind's label, not saved as a clear: a typo must not cost an identity.

Nothing here is Qt. An editor holds one of these and exposes ``fields()`` as a
``QVariantList`` property and ``set()`` as a slot.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.plugins.icons import icon_url


def kind_icon(kind: Any) -> str:
    """The ``image://plugin`` URL of a declared kind's icon, or ``""``."""
    icon = getattr(kind, "icon", "")
    plugin_id = getattr(kind, "plugin_id", "")
    return icon_url(plugin_id, icon) if icon and plugin_id else ""


class IdStaging:
    """One entity's external ids, staged until saved."""

    def __init__(self, entity: str, registry: Any = None) -> None:
        self.entity = entity
        self._registry = registry
        self._baseline: Dict[str, str] = {}
        self._staged: Dict[str, str] = {}

    # -- loading -----------------------------------------------------------

    def load(self, ids: Mapping[str, Any]) -> None:
        """Take a fresh baseline. *ids* is what the row holds, ``{key: value}``."""
        self._baseline = {
            str(key): str(value) for key, value in (ids or {}).items() if value
        }
        self._staged = dict(self._baseline)

    def clear(self) -> None:
        self._baseline = {}
        self._staged = {}

    def revert(self) -> None:
        self._staged = dict(self._baseline)

    def settle(self, key: str, value: str) -> None:
        """Record *value* as both saved and staged — a write that happened elsewhere."""
        self._baseline[key] = value
        self._staged[key] = value

    # -- what is declared --------------------------------------------------

    def declarations(self, role: Optional[str] = None) -> List[Any]:
        if self._registry is None:
            return []
        kinds = list(self._registry.declarations(self.entity))
        if role is not None:
            kinds = [k for k in kinds if getattr(k, "role", "") == role]
        return kinds

    def declaration(self, key: str) -> Any:
        return next((k for k in self.declarations() if k.key == key), None)

    # -- reading -----------------------------------------------------------

    def baseline(self, key: str) -> str:
        return self._baseline.get(key, "")

    def staged(self, key: str) -> str:
        return self._staged.get(key, "")

    def baseline_ids(self, *, declared_only: bool = True) -> Dict[str, str]:
        """The saved ids, as ``{key: value}`` — by default only the declared ones."""
        keys = {k.key for k in self.declarations()} if declared_only else set(self._baseline)
        return {k: v for k, v in self._baseline.items() if v and k in keys}

    @property
    def dirty(self) -> bool:
        return any(
            self._staged.get(key, "") != self._baseline.get(key, "")
            for key in {*self._staged, *self._baseline}
        )

    def fields(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """What an editor draws: one entry per declared kind, in declaration order.

        ``{key, label, sourceLabel, role, value, saved, url, editable, iconUrl}``.
        ``value`` is the staged text and ``saved`` the stored id, so a page can
        show what the field holds and still link to what is actually saved.
        """
        out: List[Dict[str, Any]] = []
        for kind in self.declarations(role):
            saved = self._baseline.get(kind.key, "")
            out.append({
                "key": kind.key,
                "label": kind.label,
                "sourceLabel": kind.source_label or kind.label,
                "role": getattr(kind, "role", ""),
                "value": self._staged.get(kind.key, ""),
                "saved": saved,
                "url": kind.url_for(saved) if saved else "",
                # Parse rules are what make a kind editable: without them there
                # is no saying what a pasted link means, and nothing typed
                # could be saved. Shown, read-only, rather than hidden.
                "editable": bool(kind.parse),
                "iconUrl": kind_icon(kind),
            })
        return out

    def identified(self) -> Dict[str, str]:
        """The first saved id an enabled plugin declares, as ``{label, iconUrl}``.

        Empty when there is none — which is what "unidentified" means on screen,
        whatever the row holds under a plugin that is off.
        """
        for kind in self.declarations():
            if self._baseline.get(kind.key):
                return {"label": kind.source_label or kind.label, "iconUrl": kind_icon(kind)}
        return {}

    # -- writing -----------------------------------------------------------

    def set(self, key: str, text: Any) -> bool:
        """Stage *text* for *key*. Returns whether anything changed.

        Refused — silently, as a hidden field would be — for a kind no enabled
        plugin declares or one it declares read-only.
        """
        kind = self.declaration(key)
        if kind is None or not kind.parse:
            return False
        value = "" if text is None else str(text)
        if self._staged.get(key, "") == value:
            return False
        self._staged[key] = value
        return True

    def changes(self) -> Tuple[Dict[str, str], str]:
        """``({key: parsed value}, error)`` for the kinds that changed.

        An empty value means "clear", and only reaches here when the user
        emptied a field they could see. *error* is non-empty, and the map is
        empty, when something typed could not be read as the kind it was typed
        into.
        """
        out: Dict[str, str] = {}
        for key, raw in self._staged.items():
            if raw == self._baseline.get(key, ""):
                continue
            kind = self.declaration(key)
            if kind is None:
                # Cannot happen through `set`, which refuses undeclared kinds;
                # guarded anyway, because the cost of being wrong is a delete.
                continue
            cleaned = raw.strip()
            if not cleaned:
                out[key] = ""
                continue
            parsed = kind.parse_value(cleaned)
            if not parsed:
                return {}, f"“{cleaned}” is not a valid {kind.label}"
            if parsed != self._baseline.get(key, ""):
                out[key] = parsed
        return out, ""
