"""Settings ViewModel — persistence for whatever the GUI wants to remember.

Replaces both the old ``settingsVM`` (a theme stub with no store behind it) and
``sessionVM`` (whose ``save(w, h, maximized, sidebarVisible, sidebarWidth)``
baked one GUI's layout into a versioned API, and which also carried the queue,
so a GUI that skipped it silently stopped persisting unrelated state).

Two pairs, because a GUI has two kinds of state to keep:

- ``value`` / ``setValue`` — durable, into ``settings.json``. How you like it:
  the accent, the sort order, the column widths.
- ``sessionValue`` / ``setSessionValue`` — disposable, into ``session.json``.
  Where you were: the window's size, which page was open.

Everything written from QML lands under the running GUI package, so two GUIs
never collide and neither can reach the backend's own scope. The backend does
not interpret any of it: a value goes in as it came and comes back the same.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from app.settings.store import SettingsStore

# A column drag emits a value per frame; none of them deserve a file write.
FLUSH_DELAY_MS = 750


class SettingsViewModel(QObject):
    """A namespaced key/value store for the GUI."""

    valueChanged = Signal(str)

    def __init__(
        self,
        store: SettingsStore,
        gui_package: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._package = gui_package

        # One timer for both documents: the flush writes whichever is dirty, so
        # a second timer would only mean a second write of the same edit.
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_DELAY_MS)
        self._flush_timer.timeout.connect(self._store.flush)

    @Property(str, constant=True)
    def guiPackage(self) -> str:  # noqa: N802
        """The package whose scope these keys land in."""
        return self._package

    @property
    def store(self) -> SettingsStore:
        """The store behind this view model.

        Not exposed to QML — a GUI has no business with the other scopes. It is
        here for :func:`app.main.register_settings_singleton`, which builds a
        second view model over the same store for the QML singleton: Qt will not
        let one registered instance serve two engines, and the store is the part
        that has to be shared.
        """
        return self._store

    # -- durable: settings.json ------------------------------------------

    @Slot(str, result="QVariant")
    @Slot(str, "QVariant", result="QVariant")
    def value(self, key: str, default: Any = None) -> Any:
        """Read a preference previously stored under *key*."""
        return self._store.gui_get(self._package, key, default)

    @Slot(str, "QVariant")
    def setValue(self, key: str, value: Any) -> None:  # noqa: N802
        """Store *value* under *key*. Written to disk shortly afterwards."""
        self._store.gui_set(self._package, key, value)
        self.valueChanged.emit(key)
        self._flush_timer.start()

    @Slot(str)
    def remove(self, key: str) -> None:
        self._store.gui_remove(self._package, key)
        self.valueChanged.emit(key)
        self._flush_timer.start()

    # -- disposable: session.json ----------------------------------------

    @Slot(str, result="QVariant")
    @Slot(str, "QVariant", result="QVariant")
    def sessionValue(self, key: str, default: Any = None) -> Any:  # noqa: N802
        """Read session state previously stored under *key*."""
        return self._store.session_gui_get(self._package, key, default)

    @Slot(str, "QVariant")
    def setSessionValue(self, key: str, value: Any) -> None:  # noqa: N802
        """Store *value* as session state — kept only until it is superseded."""
        self._store.session_gui_set(self._package, key, value)
        self.valueChanged.emit(key)
        self._flush_timer.start()

    @Slot(str)
    def removeSession(self, key: str) -> None:  # noqa: N802
        self._store.session_gui_remove(self._package, key)
        self.valueChanged.emit(key)
        self._flush_timer.start()

    # -- persistence ------------------------------------------------------

    @Slot()
    def sync(self) -> None:
        """Write pending changes now.

        Rarely needed — the debounce handles ordinary edits, and the backend
        flushes on shutdown. Worth calling before something that might not come
        back, such as a restart to switch GUI.
        """
        self._flush_timer.stop()
        self._store.flush()
