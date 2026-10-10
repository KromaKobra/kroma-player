"""A key/value store the backend persists without interpreting.

This is what lets presentation leave the backend without losing persistence.
Column widths, window geometry, which pane was open — all of it is the GUI's
business, but somebody has to write it to disk, and only the backend knows
where the vault is.

So the backend stores strings. It does not know that ``columns.queue`` holds a
column order, and it must never grow code that does.

Two axes, not one.

**What kind of state** — the two documents, with separate lifetimes and separate
blast radii:

- ``settings.json`` — durable. How you like it: every preference in the Settings
  tab, every GUI look-and-feel choice. Written atomically.
- ``session.json`` — disposable. Where you were: the queue, the history, the
  window, which page was open, which GUI to load next.

**Whose state it is** — the scope inside each document:

- ``app``  — backend state. Written by the backend on its own schedule, never as
  a side effect of a GUI call.
- ``gui.<package>`` — whatever a GUI wants to remember. Namespaced by package so
  two GUIs cannot collide, and so an uninstalled GUI's state sits inert rather
  than confusing its successor.
- ``plugins.<id>`` — whether a plugin is on, the consent it was given, and its
  own settings. Durable only, and written only by the plugin host: like
  ``app``, no GUI can reach it, because nothing that takes a key from QML puts
  it there. That is what keeps "nothing outside a plugin can grant that
  plugin's consent" true by construction rather than by a check.

The namespacing is **structural**, not a string prefix. A GUI hands over a leaf
key and the store places it at ``doc["gui"][package][key]``; *package* comes from
the resolved manifest and never from QML. A GUI cannot reach ``app`` because
there is no code path that would put its key there — not because a prefix makes
the resulting string fail to match.

Writes are debounced: a column drag emits a value per frame, and none of them
are worth a file write on their own.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Optional

from app.session import load_session, save_session
from app.settings.file import load_settings, save_settings

logger = logging.getLogger(__name__)

APP_SCOPE = "app"
GUI_SCOPE = "gui"
PLUGINS_SCOPE = "plugins"

# The key under which session.json records which GUI package to load next. Top
# level rather than inside ``app``: it is chosen before any scope exists, by the
# resolver, which reads the raw document.
SELECTED_GUI_KEY = "selected_gui"


def _valid_key(key: Any) -> bool:
    """Whether *key* is something a document can hold.

    The boundary check for values arriving from QML. Anything else — a number, a
    null, an empty string — is refused rather than coerced, because a coerced key
    is one nothing can read back.
    """
    return isinstance(key, str) and bool(key)


class SettingsStore:
    """Namespaced settings over the session and settings documents.

    Both arguments default to being read off disk. Pass explicit dicts —
    ``SettingsStore({}, {})`` — for a store that starts empty and touches
    nothing until it is flushed.
    """

    def __init__(
        self, session: Optional[dict] = None, settings: Optional[dict] = None
    ) -> None:
        # Deep copies: the caller keeps its own document, and the nested scope
        # dicts must not be shared with it. A shallow copy here is how the GUI
        # selection used to be reverted by the next flush.
        self._session: dict = (
            copy.deepcopy(session) if session is not None else load_session()
        )
        self._settings: dict = (
            copy.deepcopy(settings) if settings is not None else load_settings()
        )
        self._session_dirty = False
        self._settings_dirty = False

    # -- document access -------------------------------------------------

    @staticmethod
    def _read(document: dict, scope: tuple[str, ...], key: str, default: Any) -> Any:
        node: Any = document
        for part in scope:
            node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return default
        return node.get(key, default)

    @staticmethod
    def _write(document: dict, scope: tuple[str, ...], key: str, value: Any) -> bool:
        """Store *value*, returning whether anything actually changed.

        The compare-before-store is what stops a volume drag dirtying the store
        on every frame, and with it the debounce timer that would restart on
        every one of those frames.
        """
        node = document
        for part in scope:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        if key in node and node[key] == value:
            return False
        node[key] = value
        return True

    @staticmethod
    def _delete(document: dict, scope: tuple[str, ...], key: str) -> bool:
        node: Any = document
        for part in scope:
            node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return False
        return node.pop(key, None) is not None

    @staticmethod
    def _plugin_scope(plugin_id: Any) -> Optional[tuple[str, ...]]:
        if not _valid_key(plugin_id):
            logger.warning("Refusing a plugin scope with no id: %r", plugin_id)
            return None
        return (PLUGINS_SCOPE, plugin_id)

    @staticmethod
    def _gui_scope(package: Any) -> Optional[tuple[str, ...]]:
        if not _valid_key(package):
            logger.warning("Refusing a GUI scope with no package name: %r", package)
            return None
        return (GUI_SCOPE, package)

    # -- durable: settings.json ------------------------------------------

    def app_get(self, key: str, default: Any = None) -> Any:
        return self._read(self._settings, (APP_SCOPE,), key, default)

    def app_set(self, key: str, value: Any) -> None:
        if not _valid_key(key):
            return
        if self._write(self._settings, (APP_SCOPE,), key, value):
            self._settings_dirty = True

    def app_remove(self, key: str) -> None:
        if _valid_key(key) and self._delete(self._settings, (APP_SCOPE,), key):
            self._settings_dirty = True

    def gui_get(self, package: str, key: str, default: Any = None) -> Any:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return default
        return self._read(self._settings, scope, key, default)

    def gui_set(self, package: str, key: str, value: Any) -> None:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return
        if self._write(self._settings, scope, key, value):
            self._settings_dirty = True

    def gui_remove(self, package: str, key: str) -> None:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return
        if self._delete(self._settings, scope, key):
            self._settings_dirty = True

    # -- durable, plugin host only: settings.json -------------------------

    def plugin_get(self, plugin_id: str, key: str, default: Any = None) -> Any:
        scope = self._plugin_scope(plugin_id)
        if scope is None or not _valid_key(key):
            return default
        return self._read(self._settings, scope, key, default)

    def plugin_set(self, plugin_id: str, key: str, value: Any) -> None:
        scope = self._plugin_scope(plugin_id)
        if scope is None or not _valid_key(key):
            return
        if self._write(self._settings, scope, key, value):
            self._settings_dirty = True

    def plugin_remove(self, plugin_id: str, key: str) -> None:
        scope = self._plugin_scope(plugin_id)
        if scope is None or not _valid_key(key):
            return
        if self._delete(self._settings, scope, key):
            self._settings_dirty = True

    def plugin_ids(self) -> list[str]:
        """Every plugin this vault has recorded anything about."""
        node = self._settings.get(PLUGINS_SCOPE)
        if not isinstance(node, dict):
            return []
        return sorted(k for k, v in node.items() if _valid_key(k) and isinstance(v, dict))

    # -- disposable: session.json ----------------------------------------

    def session_app_get(self, key: str, default: Any = None) -> Any:
        return self._read(self._session, (APP_SCOPE,), key, default)

    def session_app_set(self, key: str, value: Any) -> None:
        if not _valid_key(key):
            return
        if self._write(self._session, (APP_SCOPE,), key, value):
            self._session_dirty = True

    def session_app_remove(self, key: str) -> None:
        if _valid_key(key) and self._delete(self._session, (APP_SCOPE,), key):
            self._session_dirty = True

    def session_gui_get(self, package: str, key: str, default: Any = None) -> Any:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return default
        return self._read(self._session, scope, key, default)

    def session_gui_set(self, package: str, key: str, value: Any) -> None:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return
        if self._write(self._session, scope, key, value):
            self._session_dirty = True

    def session_gui_remove(self, package: str, key: str) -> None:
        scope = self._gui_scope(package)
        if scope is None or not _valid_key(key):
            return
        if self._delete(self._session, scope, key):
            self._session_dirty = True

    # -- the chosen GUI ---------------------------------------------------

    @property
    def selected_gui(self) -> str:
        """The GUI package to load next launch, or ``""``.

        Session state rather than a preference for a practical reason: it names
        a folder on *this* machine, and a settings file restored from a backup
        onto a machine without that package would otherwise send the app to the
        fallback GUI with no explanation.
        """
        value = self._session.get(SELECTED_GUI_KEY, "")
        return value if isinstance(value, str) else ""

    @selected_gui.setter
    def selected_gui(self, name: str) -> None:
        if self._session.get(SELECTED_GUI_KEY) == name:
            return
        self._session[SELECTED_GUI_KEY] = name
        self._session_dirty = True

    # -- persistence ----------------------------------------------------

    def flush(self) -> None:
        """Write whichever documents changed."""
        if self._settings_dirty:
            save_settings(self._settings)
            self._settings_dirty = False
        if self._session_dirty:
            save_session(self._session)
            self._session_dirty = False

    @property
    def dirty(self) -> bool:
        return self._session_dirty or self._settings_dirty


def encode(value: Any) -> str:
    """JSON-encode a structured value for storage."""
    return json.dumps(value, separators=(",", ":"))


def decode(raw: Any, default: Any = None) -> Any:
    """Decode a stored value, returning *default* if it is not usable."""
    if not isinstance(raw, str):
        return raw if raw is not None else default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default
