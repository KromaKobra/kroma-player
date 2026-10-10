"""First-run vault setup — the popup that asks the user where their music lives.

This runs before ``create_service_registry()``: every path in the application
(the databases, the artwork cache, the installed GUI packages) hangs off the
vault, so nothing else can be built until it is known.

The window is deliberately built into the application rather than shipped as
part of a GUI package — a GUI package is installed *inside* the vault, so it
cannot be what asks for it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Property, QEventLoop, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from app.backup.locator import is_backup
from app.config.kord_config import get_vault_path
from app.vault.vault_locator import (
    configured_vault,
    create_vault,
    default_vault_path,
    is_vault,
    music_folder,
    remember_vault,
    resolve_vault_folder,
)

logger = logging.getLogger(__name__)

QML_FILENAME = "VaultSetup.qml"

_MISSING_VAULT_MESSAGE = (
    "The vault recorded in your settings could not be found — it may have been "
    "moved or deleted. Open it in its new location, or create a new one."
)


def qml_path() -> Path:
    """Return the path to the setup window's QML, frozen or from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "app" / "vault" / QML_FILENAME
    return Path(__file__).resolve().parent / QML_FILENAME


class VaultSetupController(QObject):
    """Backs ``VaultSetup.qml``; holds the chosen vault once one is settled on."""

    finished = Signal()
    error_message_changed = Signal()
    pending_backup_changed = Signal()

    def __init__(self, initial_error: str = "", parent: QObject | None = None):
        super().__init__(parent)
        self._error_message = initial_error
        self._result: Path | None = None
        self._done = False
        self._pending_backup: Path | None = None

    @property
    def result(self) -> Path | None:
        """The chosen vault, or ``None`` if the user closed the window."""
        return self._result

    # ── Properties exposed to QML ────────────────────────────────────────

    def _get_error_message(self) -> str:
        return self._error_message

    def _set_error_message(self, value: str) -> None:
        if self._error_message != value:
            self._error_message = value
            self.error_message_changed.emit()

    errorMessage = Property(  # type: ignore[arg-type]
        str, _get_error_message, _set_error_message, notify=error_message_changed,
    )

    def _get_music_folder_url(self) -> QUrl:
        return QUrl.fromLocalFile(str(music_folder()))

    musicFolderUrl = Property(QUrl, _get_music_folder_url, constant=True)  # type: ignore[arg-type]

    def _get_default_vault_path(self) -> str:
        return str(default_vault_path())

    defaultVaultPath = Property(str, _get_default_vault_path, constant=True)  # type: ignore[arg-type]

    def _get_pending_backup(self) -> str:
        """The backup folder awaiting confirmation, or ``""``."""
        return str(self._pending_backup) if self._pending_backup is not None else ""

    pendingBackup = Property(  # type: ignore[arg-type]
        str, _get_pending_backup, notify=pending_backup_changed,
    )

    # ── Slots ────────────────────────────────────────────────────────────

    @Slot(QUrl)
    def openExisting(self, folder: QUrl) -> None:
        """Accept *folder* as the vault, or explain why it is not one."""
        path = _to_path(folder)
        if path is None:
            self._set_error_message("That selection could not be read as a folder.")
            return

        resolved = resolve_vault_folder(path)
        if resolved is None:
            self._set_error_message(
                f"“{path}” is not a Kroma vault. Select the vault folder itself "
                "(the one containing a .data folder, usually named “venomvault”), "
                "or create a new vault instead."
            )
            return

        # A backup is a complete vault — that is deliberate, and it is what makes
        # recovery after a lost drive a matter of opening it. But it also means
        # a backup can be opened as the live library by accident, and then
        # played from, downloaded into and edited, which quietly turns the spare
        # copy into a second library that has diverged from the first. So this
        # one is asked about rather than assumed.
        if self._pending_backup != resolved and is_backup(resolved):
            self._pending_backup = resolved
            self._set_error_message("")
            self.pending_backup_changed.emit()
            return

        self._finish(resolved)

    @Slot()
    def confirmBackup(self) -> None:
        """Open the pending backup folder as the live library anyway."""
        if self._pending_backup is None:
            return
        logger.warning("Opening a backup as the live vault: %s", self._pending_backup)
        self._finish(self._pending_backup)

    @Slot()
    def dismissBackup(self) -> None:
        """Go back to the picker without opening the pending backup."""
        if self._pending_backup is None:
            return
        self._pending_backup = None
        self.pending_backup_changed.emit()

    @Slot(QUrl)
    def createNew(self, folder: QUrl) -> None:
        """Create a vault in *folder* and accept it."""
        path = _to_path(folder)
        if path is None:
            self._set_error_message("That selection could not be read as a folder.")
            return

        try:
            vault = create_vault(path)
        except OSError as exc:
            self._set_error_message(f"Could not create a vault in “{path}”: {exc}")
            return

        logger.info("Created vault at %s", vault)
        self._finish(vault)

    @Slot()
    def cancel(self) -> None:
        """Finish with no vault — the application will exit."""
        if self._done:
            return
        self._result = None
        self._done = True
        self.finished.emit()

    def _finish(self, vault: Path) -> None:
        if self._done:
            return
        self._result = vault
        self._done = True
        self._set_error_message("")
        self.finished.emit()


def _to_path(url: QUrl) -> Path | None:
    local = url.toLocalFile()
    return Path(local) if local else None


def run_vault_setup(initial_error: str = "") -> Path | None:
    """Show the setup window and block until the user picks or closes it.

    Returns the chosen vault, or ``None`` if the user closed the window (or the
    window could not be shown at all).
    """
    app = QGuiApplication.instance()

    engine = QQmlApplicationEngine()
    # Parented to the engine so the window can never outlive the object it is
    # bound to, whatever order Python drops the references in.
    controller = VaultSetupController(initial_error, parent=engine)
    engine.rootContext().setContextProperty("vaultSetup", controller)
    engine.load(QUrl.fromLocalFile(str(qml_path())))

    if not engine.rootObjects():
        logger.error("Vault setup window failed to load from %s", qml_path())
        return None

    # While the setup window is up it is the only window there is, so the
    # default "quit when the last window closes" would tear down the
    # application before the main window is ever built.
    restore_quit_on_close = False
    if isinstance(app, QGuiApplication) and app.quitOnLastWindowClosed():
        app.setQuitOnLastWindowClosed(False)
        restore_quit_on_close = True

    try:
        loop = QEventLoop()
        controller.finished.connect(loop.quit)
        loop.exec()

        # Dismiss the window before handing control back; `cancel()` is a no-op
        # once a choice has been made, so this cannot overwrite the result.
        for window in engine.rootObjects():
            window.close()
        result = controller.result
    finally:
        if restore_quit_on_close:
            app.setQuitOnLastWindowClosed(True)  # type: ignore[union-attr]

    del engine  # takes the window and the controller down with it
    return result


def ensure_vault_ready() -> Path | None:
    """Return the vault to use, asking the user for one if necessary.

    In order: the vault recorded in the kord config; the conventional
    ``~/Music/venomvault`` if it already exists (which adopts the library of
    anyone upgrading from a build that had no config); otherwise the setup
    window. Whatever is settled on is written back to the config.

    Returns ``None`` when the user declined to choose — the caller should exit.
    """
    recorded = configured_vault()
    if recorded is not None:
        return recorded

    default = default_vault_path()
    if is_vault(default):
        logger.info("Adopting existing vault at %s", default)
        remember_vault(default)
        return default

    # A recorded-but-missing vault is worth explaining rather than silently
    # presenting the first-run window.
    initial_error = _MISSING_VAULT_MESSAGE if get_vault_path() is not None else ""

    selected = run_vault_setup(initial_error)
    if selected is None:
        return None

    remember_vault(selected)
    return selected
