"""GuiViewModel — lets the running GUI list and switch the installed GUIs."""

from __future__ import annotations

import logging

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from app.gui.gui_paths import GUI_API_VERSION, gui_root, is_valid_gui_name
from app.gui.resolver import ResolvedGui, available_guis
from app.settings.store import SettingsStore

logger = logging.getLogger(__name__)


class GuiViewModel(QObject):
    """Exposes GUI package discovery and selection to QML.

    Switching takes effect on the next launch: swapping the QML tree under a
    live engine would tear down the very objects handling the click.
    """

    available_changed = Signal()
    selection_changed = Signal()

    def __init__(
        self,
        resolved: ResolvedGui | None,
        store: SettingsStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._resolved = resolved
        self._store = store
        self._selected = resolved.manifest.name if resolved else ""
        self._available: list[dict] = []
        self.refresh()

    # ------------------------------------------------------------------
    # The GUI that is actually running
    # ------------------------------------------------------------------

    @Property(str, constant=True)
    def currentName(self) -> str:
        return self._resolved.manifest.name if self._resolved else ""

    @Property(str, constant=True)
    def currentTitle(self) -> str:
        return self._resolved.manifest.title if self._resolved else ""

    @Property(str, constant=True)
    def currentVersion(self) -> str:
        return self._resolved.manifest.version if self._resolved else ""

    @Property(str, constant=True)
    def currentBundle(self) -> str:
        """The .kpi file the running GUI was loaded from."""
        return str(self._resolved.bundle_path) if self._resolved else ""

    @Property(str, constant=True)
    def currentBuildId(self) -> str:
        """Which build of it — the version string does not move between rebuilds."""
        return self._resolved.build_id if self._resolved else ""

    @Property(bool, constant=True)
    def isFallback(self) -> bool:
        """True when the requested GUI failed to load and this one stood in."""
        return bool(self._resolved and self._resolved.is_fallback)

    @Property(int, constant=True)
    def apiVersion(self) -> int:
        return GUI_API_VERSION

    @Property(str, constant=True)
    def guiFolder(self) -> str:
        """Where GUI bundles are installed — shown in Settings, opened on click."""
        return str(gui_root())

    # ------------------------------------------------------------------
    # Discovery and selection
    # ------------------------------------------------------------------

    @Property("QVariantList", notify=available_changed)
    def available(self) -> list[dict]:
        """Every GUI found on disk, loadable or not.

        Unloadable entries are included with a populated ``error`` so the
        picker can explain the problem rather than silently omitting them.
        """
        return self._available

    @Property(str, notify=selection_changed)
    def selectedName(self) -> str:
        """The GUI that will load next launch."""
        return self._selected

    @Property(bool, notify=selection_changed)
    def restartRequired(self) -> bool:
        return bool(self._selected) and self._selected != self.currentName

    @Slot()
    def refresh(self) -> None:
        """Re-scan the gui folder."""
        current = self.currentName
        self._available = [
            {
                "name":        candidate.name,
                "title":       candidate.manifest.title if candidate.manifest else candidate.name,
                "version":     candidate.manifest.version if candidate.manifest else "",
                "author":      candidate.manifest.author if candidate.manifest else "",
                "description": candidate.manifest.description if candidate.manifest else "",
                "homepage":    candidate.manifest.homepage if candidate.manifest else "",
                "apiVersion":  candidate.manifest.api_version if candidate.manifest else 0,
                "bundle":      str(candidate.bundle_path),
                "buildId":     candidate.build_id,
                "loadable":    candidate.loadable,
                "error":       candidate.error,
                "bundled":     candidate.bundled,
                "current":     candidate.name == current,
            }
            for candidate in available_guis()
        ]
        self.available_changed.emit()

    @Slot(str, result=bool)
    def select(self, name: str) -> bool:
        """Choose the GUI to load next launch. Returns False if it was rejected.

        The choice goes through the settings store rather than writing the
        session file here. One writer, one write path: this used to mutate its
        own copy of the session dict and save it, while the store held a
        different copy that knew nothing about the change — so the next flush,
        750 ms after any column drag, wrote the boot-time value back over it.

        Flushed immediately rather than left to the debounce, so a crash between
        here and quitting cannot lose it.
        """
        name = (name or "").strip()
        if not is_valid_gui_name(name):
            logger.warning("Refusing invalid GUI name: %r", name)
            return False
        if not any(g["name"] == name and g["loadable"] for g in self._available):
            logger.warning("Refusing to select GUI '%s': not installed or not loadable", name)
            return False

        self._store.selected_gui = name
        self._store.flush()

        self._selected = name
        self.selection_changed.emit()
        return True

    @Slot()
    def openGuiFolder(self) -> None:
        """Open the gui folder in the system file manager."""
        folder = gui_root()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create %s: %s", folder, exc)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
