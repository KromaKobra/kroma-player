"""The update dialog, in the real window: it opens, its button commits, and the
backup it recommends hands back to it.

Everything the dialog shows is ``updatesVM``'s and tested there; what is left is
wiring that only exists in QML and fails silently when it breaks. A handler
under the wrong name never fires, a signal nobody connected goes nowhere, and
the window loads clean either way — so each of the three paths a user takes
through the dialog is walked here, by its objectNames.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database.schema import SCHEMA_VERSION  # noqa: E402
from app.update.install_kind import InstallKind  # noqa: E402
from app.update.release import Asset, Release  # noqa: E402
from app.versions import VAULT_VERSION  # noqa: E402
from app.viewmodels import updates_view_model as uvm  # noqa: E402


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault, collecting every QML warning."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("vault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load"
    qt_app.processEvents()

    yield roots[0], qt_app, registry, warnings

    del engine


def _settle(qt_app, ms: int = 600) -> None:
    """Pump long enough for a popup's enter or exit transition to finish."""
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def _find(window, name):
    from PySide6.QtCore import QObject

    found = window.findChild(QObject, name)
    assert found is not None, f"{name} is gone or renamed"
    return found


@pytest.fixture
def vm(gui, monkeypatch):
    """The window's updatesVM on an eligible AppImage install, reset afterwards.

    Module-scoped window, function-scoped state: whatever a test leaves behind —
    an offered release, a phase, an open dialog — is put back so the next one
    starts from a window that has never heard of an update.
    """
    window, qt_app, registry, _warnings = gui
    model = registry.get_service("updates_view_model")
    monkeypatch.setattr(uvm, "update_eligibility", lambda: "")
    monkeypatch.setattr(uvm, "install_kind", lambda: InstallKind.APPIMAGE)
    # A download must not start a real thread, let alone reach the network.
    monkeypatch.setattr(model, "_start", lambda _worker: True)

    yield model

    # close() is not subject to closePolicy, which only governs the user.
    for name in ("updateDialog", "backupDialog"):
        _find(window, name).close()
    model._phase = uvm.PHASE_IDLE
    model._release = None
    model._asset = None
    model._install_when_ready = False
    model._failed_version = ""
    model.phase_changed.emit()
    model.release_changed.emit()
    model.settings_changed.emit()
    _settle(qt_app)


def _offer(model, *, schema_version: int = SCHEMA_VERSION) -> None:
    """A launch check that found 9.9.9, delivered the way the worker does."""
    release = Release(
        version="9.9.9",
        tag="v9.9.9",
        notes="<div>\n\n# v9.9.9 Changelog\n\n</div>\n\n## Highlights\n\nNew things.\n\n---\n\n- more",
        published_at="2026-10-09T12:00:00Z",
        url="https://example.invalid/release",
        vault_version=VAULT_VERSION,
        assets=(Asset(
            name="kroma-player-9.9.9-x86_64.AppImage",
            url="https://example.invalid/asset",
            kind="appimage",
            arch="x86_64",
            size=200 * 1024 * 1024,
            sha256="a" * 64,
        ),),
        schema_version=schema_version,
    )
    model._checking_automatic = True
    model._worker = type("W", (), {"release": release})()
    model._on_checked()
    model._worker = None
    model._on_stopped()


def test_an_announced_update_opens_the_dialog(gui, vm):
    window, qt_app, _registry, _warnings = gui
    dialog = _find(window, "updateDialog")
    assert not dialog.property("visible")

    _offer(vm)
    _settle(qt_app)

    assert dialog.property("visible"), (
        "update_available did not open the dialog — main.qml's handler is under "
        "the wrong name, or it opens something else"
    )
    assert _find(window, "updateNowButton").property("visible")
    assert _find(window, "backupFirstButton").property("visible")


def test_update_and_restart_commits_to_the_whole_thing(gui, vm):
    window, qt_app, _registry, _warnings = gui
    _offer(vm)
    _settle(qt_app)

    _find(window, "updateNowButton").clicked.emit()
    _settle(qt_app, 200)

    assert vm._install_when_ready, "the click must carry the install across the download"
    assert vm.phase == uvm.PHASE_DOWNLOADING
    dialog = _find(window, "updateDialog")
    assert dialog.property("visible"), "it stays up while the download runs"
    # What switches closePolicy to NoAutoClose. The flags themselves have no
    # converter on the Python side, so the condition is read instead.
    assert dialog.property("downloading") is True, "and cannot be clicked away"


def test_back_up_first_goes_to_the_backup_and_comes_back(gui, vm):
    window, qt_app, _registry, _warnings = gui
    update = _find(window, "updateDialog")
    backup = _find(window, "backupDialog")
    _offer(vm)
    _settle(qt_app)
    original_view = window.property("viewIndex")

    _find(window, "backupFirstButton").clicked.emit()
    _settle(qt_app)

    assert backup.property("visible"), "Back up first did not open the backup dialog"
    assert not update.property("visible"), "the update dialog steps aside for it"
    assert window.property("viewIndex") == original_view, "backup preserves navigation context"

    backup.close()
    _settle(qt_app)

    assert update.property("visible"), "the update was not offered again after the backup"
    assert not window.property("updateAfterBackup"), "the round trip is spent"


def test_the_system_tabs_back_up_first_takes_the_same_road(gui, vm):
    window, qt_app, _registry, _warnings = gui
    backup = _find(window, "backupDialog")
    _offer(vm)
    _settle(qt_app)
    _find(window, "updateDialog").close()
    _settle(qt_app)

    _find(window, "systemTab").backupRequested.emit()
    _settle(qt_app)

    assert backup.property("visible")
    backup.close()
    _settle(qt_app)
    assert _find(window, "updateDialog").property("visible")


def test_closing_a_backup_opened_for_its_own_sake_brings_nothing_back(gui, vm):
    window, qt_app, _registry, _warnings = gui
    backup = _find(window, "backupDialog")
    _offer(vm)
    _settle(qt_app)
    _find(window, "updateDialog").close()
    _settle(qt_app)

    # A normal library backup must not reopen an available update afterwards.
    window.setProperty("viewIndex", 5)
    _find(window, "settingsView").setProperty("currentTab", 3)
    _settle(qt_app)
    _find(window, "libraryBackupButton").clicked.emit()
    _settle(qt_app)
    assert backup.property("visible")
    assert window.property("viewIndex") == 5
    assert not window.property("updateAfterBackup")
    backup.close()
    _settle(qt_app)

    assert not _find(window, "updateDialog").property("visible")


def test_a_failed_update_is_reported_at_startup(gui, vm):
    window, qt_app, _registry, _warnings = gui
    dialog = _find(window, "updateDialog")

    vm._failed_version = "9.9.9"
    vm.settings_changed.emit()
    _settle(qt_app, 2200)

    assert dialog.property("visible"), "the startup notice never opened"
    assert _find(window, "showInstallLogButton").property("visible")
    assert _find(window, "checkAgainButton").property("visible")
    assert not _find(window, "updateNowButton").property("visible"), "nothing to install yet"


def test_nothing_the_dialog_does_warns_in_qml(gui, vm):
    """A binding error is a warning, not a failure — and a dialog with one
    still opens, showing the wrong thing. So the warnings themselves are read."""
    window, qt_app, _registry, warnings = gui
    _offer(vm, schema_version=SCHEMA_VERSION + 1)
    _settle(qt_app)
    _find(window, "updateNowButton").clicked.emit()
    _settle(qt_app, 200)

    ours = [w for w in warnings if "UpdateDialog.qml" in w or "SystemTab.qml" in w]
    assert ours == []
