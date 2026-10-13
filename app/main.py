#!/usr/bin/env python3
"""Kroma Player - Main entry point."""

import logging
import os
import sys

from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import (
    QQmlApplicationEngine,
    qmlRegisterSingletonType,
    qmlRegisterType,
)
from PySide6.QtQuickControls2 import QQuickStyle

from app.artwork.local_art_provider import LocalArtImageProvider
from app.bootstrap import create_service_registry, persist_app_state
from app.backup.identity import ensure_vault_id
from app.gui.bundle import BundleError, mount
from app.gui.gui_paths import bundled_gui_bundle, gui_cache_dir, gui_root
from app.library.track_query import TrackQuery
from app.gui.native_window import enable_for_root_objects
from app.gui.resolver import ResolvedGui, inspect_bundle
from app.system.mpris import IDENTITY, MprisUnavailable
from app.versions import APP_VERSION
from app.viewmodels.settings_view_model import SettingsViewModel
from app.plugins.icons import SCHEME as PLUGIN_ICON_SCHEME, PluginIconProvider
from app.plugins.installer import sweep_leftovers
from app.plugins.paths import sweep_stale_temp
from app.smoke import run_smoke_test, smoke_args
from app.update.download import sweep_downloads
from app.utils.library_paths import set_library_root
from app.utils.logfile import configure_logging, current_log_file
from app.vault.startup import open_problem
from app.vault.vault_locator import remember_vault
from app.vault.vault_setup import ensure_vault_ready, run_vault_setup

logger = logging.getLogger(__name__)

# Basename of the installed XDG desktop entry (see packaging/kroma-player.desktop).
# Also the Wayland app_id, and what MPRIS advertises as DesktopEntry, so the
# three have to agree — the shell matches them up to find the app's icon.
DESKTOP_FILE_NAME = "kroma-player"

# ViewModel context properties handed to the GUI. This mapping *is* the object
# half of the GUI API — see docs/GUI_API.md. Adding an entry is a compatible
# change; removing or renaming one requires a GUI_API_VERSION bump.
CONTEXT_PROPERTIES = {
    "playerVM":         "player_view_model",
    "artworkPaletteVM": "artwork_palette_view_model",
    "searchVM":         "search_view_model",
    "queueVM":          "queue_view_model",
    "historyVM":        "history_view_model",
    "sidebarVM":        "sidebar_view_model",
    "databaseVM":       "database_view_model",
    "importVM":         "import_view_model",
    "settingsVM":       "settings_view_model",
    "lyricVM":          "lyrics_view_model",
    "playlistsVM":      "playlists_view_model",
    "playlistTracksVM": "playlist_tracks_view_model",
    "guiVM":            "gui_view_model",
    "libraryVM":        "library_view_model",
    "backingVM":        "backing_track_view_model",
    "editVM":           "track_edit_view_model",
    "artistsVM":        "artists_view_model",
    "artistVM":         "artist_edit_view_model",
    # Albums mirror the artist pair, plus a third for the open album's tracks —
    # an album has a running order and an artist has no order at all.
    "albumsVM":         "albums_view_model",
    "albumVM":          "album_edit_view_model",
    "albumTracksVM":    "album_tracks_view_model",
    "exportVM":         "export_view_model",
    "backupVM":         "backup_view_model",
    # Settings → Plugins, and every slot a plugin fills: import sources, track
    # actions, whether anything can fetch audio or identify a file at all.
    "pluginsVM":        "plugins_view_model",
    "dependenciesVM":   "dependencies_view_model",
    # Beside dependenciesVM: both answer questions about this machine rather
    # than about the library. That one says whether the programs Kroma and its
    # plugins need are installed; this one says whether Kroma itself is current.
    "updatesVM":        "updates_view_model",
}

# The named mutex packaging/kroma-player.iss declares as `AppMutex`. Held for
# the life of the process on Windows only — see `_claim_single_instance_mutex`.
SINGLE_INSTANCE_MUTEX = "KromaPlayerSingleInstance"

# Kept alive at module scope on purpose: the mutex is released when its handle
# closes, and a local would be collected the moment the function returned.
_single_instance_handle = None

# QML types a GUI can instantiate for itself. Unlike the context properties
# above — which are objects the backend owns and hands over — these let a GUI
# declare as many track lists as it wants, of whatever shape it wants:
#
#     import Kroma.Library 1.0
#     TrackQuery { sortField: "artist" }
#
# This is what stops the backend deciding which views exist.
QML_MODULE = "Kroma.Library"
QML_MODULE_MAJOR = 1
QML_MODULE_MINOR = 0

# The settings store, reachable from a QML singleton. See
# register_settings_singleton() for what that unblocks.
SETTINGS_MODULE = "Kroma.Settings"
SETTINGS_SINGLETON = "SettingsStore"


_settings_singleton_registered = False
_settings_singleton_source: SettingsViewModel | None = None
_qml_types_registered = False


def _build_settings_singleton(engine) -> SettingsViewModel:
    """Build this engine's ``Kroma.Settings`` object.

    A view model per engine, all of them over the one store — which is the part
    that matters, since the store is what holds the values and writes the files.
    Qt refuses to let a single registered *instance* be reached from two engines,
    and the second engine to try does not get a warning it can act on: it gets a
    ``Theme.qml`` whose singleton has no methods.

    Parented to the engine so it is freed with it, rather than accumulating for
    the life of the process.
    """
    source = _settings_singleton_source
    if source is None:
        raise RuntimeError(
            "Kroma.Settings was imported before the settings store was known; "
            "call register_settings_singleton() before loading a GUI"
        )
    return SettingsViewModel(source.store, source.guiPackage, parent=engine)


def register_settings_singleton(view_model: SettingsViewModel) -> None:
    """Make *view_model*'s store importable from QML as ``Kroma.Settings``.

    The blocker this removes: a QML ``pragma Singleton`` cannot see root-context
    properties, so a GUI's design tokens — a singleton by nature, since every
    component reads them — had no way to reach ``settingsVM`` and used to keep a
    QSettings file inside the GUI package folder instead.

    Kept separate from the registration below because it is the half that needs
    a built registry, and because a caller that only wants the types a GUI
    declares for itself has no registry to give.

    The registration itself happens once per process, and that guard is
    load-bearing rather than tidy: Qt caches each compiled QML file against the
    type ids current when it was compiled, so a second registration invalidates
    every unit compiled before it — the next engine then reports ``Theme.qml``
    as unavailable and loads no window at all. Calling this again re-points the
    factory, which is what a second engine in one process needs.
    """
    global _settings_singleton_source
    _settings_singleton_source = view_model
    _register_settings_singleton_type()


def _register_settings_singleton_type() -> None:
    """Register ``Kroma.Settings`` once, whether or not its store is known yet.

    Split from the above so the type can exist before the vault does: the
    factory reads the store only when an engine first asks for the singleton,
    so registering early and pointing it at a store later is safe.
    """
    global _settings_singleton_registered
    if _settings_singleton_registered:
        return
    qmlRegisterSingletonType(
        SettingsViewModel,
        SETTINGS_MODULE, QML_MODULE_MAJOR, QML_MODULE_MINOR, SETTINGS_SINGLETON,
        _build_settings_singleton,
    )
    _settings_singleton_registered = True


def _register_qml_types(registry=None) -> None:
    """Register everything a GUI package can ``import``.

    *registry* is optional because the types have to exist before the backend
    does. Without one, ``Kroma.Settings`` is registered but has no store behind
    it yet; calling this again with the registry points it at one. Repeat calls
    register nothing new.

    Call it **before the first QML engine in the process** — not merely before
    the GUI's. A type registered after any engine has existed, even one long
    since deleted such as the vault setup window's, is not reliably picked up,
    and the failure does not name the late type: Nocturne's `Theme` imports
    ``Kroma.Settings`` and dies with "Cannot assign object of type QtObject to
    property of type QObject*", taking every view that uses `Theme` with it.
    """
    global _qml_types_registered
    if not _qml_types_registered:
        qmlRegisterType(
            TrackQuery, QML_MODULE, QML_MODULE_MAJOR, QML_MODULE_MINOR, "TrackQuery"
        )
        _register_settings_singleton_type()
        _qml_types_registered = True
    if registry is not None:
        register_settings_singleton(registry.get_service("settings_view_model"))


def _build_engine(registry) -> QQmlApplicationEngine:
    """A QML engine with the image providers and view models a GUI expects.

    Everything short of loading a GUI into it, so the smoke test in
    :mod:`app.smoke` builds exactly the engine a launch does rather than a copy
    of it that could drift.
    """
    engine = QQmlApplicationEngine()

    # Register the album-art image provider so QML can resolve image://cover/<hash>.
    engine.addImageProvider("cover", registry.get_service('cover_provider')) # type: ignore
    # Register the local embedded-art provider so QML can resolve image://localart/<path>.
    engine.addImageProvider("localart", LocalArtImageProvider()) # type: ignore
    # And the pictures enabled plugins ship: image://plugin/<id>/<file>.
    engine.addImageProvider(
        PLUGIN_ICON_SCHEME, PluginIconProvider(registry.get_service("plugin_host"))
    )

    # Register ViewModels with QML
    context = engine.rootContext()
    for qml_name, service_name in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service_name))
    return engine


def _load_gui(engine: QQmlApplicationEngine, resolved: ResolvedGui) -> bool:
    """Mount a GUI bundle and load its entry file.

    Returns True if a window was created. A bundle that will not mount is one
    more way for a GUI to fail, and it is handled like all the others — by
    returning False so the caller can fall back.
    """
    try:
        mount(resolved.bundle_path, resolved.mount_root)
    except BundleError as exc:
        logger.error("Could not mount %s: %s", resolved.bundle_path, exc)
        return False

    # The mount root goes on the import path so a package can ship its own QML
    # modules (a folder with a qmldir) and import them by name, exactly as it
    # could when the package was a folder.
    engine.addImportPath(resolved.import_path)
    engine.load(resolved.entry_url)
    if not engine.rootObjects():
        return False

    # A GUI that drew its own title bar asked Qt for a frameless window, which
    # on Windows costs it snapping, taskbar minimising and the rest. QML cannot
    # reach the native handle to get those back, so the backend does it.
    enable_for_root_objects(engine.rootObjects())
    return True


def _bundled_fallback(already_tried: ResolvedGui) -> ResolvedGui | None:
    """The GUI shipped inside the application, unless that is what just failed."""
    path = bundled_gui_bundle()
    if path == already_tried.bundle_path or not path.is_file():
        return None
    candidate = inspect_bundle(path, bundled=True)
    if candidate.manifest is None or not candidate.manifest.is_compatible:
        logger.error("The GUI shipped with this build is unusable: %s", candidate.error)
        return None
    return ResolvedGui(
        candidate.manifest, path, candidate.build_id, already_tried.requested
    )


def _enable_qml_disk_cache(resolved: ResolvedGui) -> None:
    """Point Qt's compiled-QML cache at the vault, keyed by build id.

    Qt caches the bytecode it compiles each QML file into and reuses it on the
    next launch, which is most of the difference between a warm start and a cold
    one. Left alone it writes into a system temp folder that gets swept; put in
    the vault it survives, and keyed by build id it cannot be mistaken for the
    cache of a bundle that has since been rebuilt.

    Read by Qt when the engine first compiles something, so it has to be set
    before the engine exists. An existing value is left alone — someone
    debugging a caching problem set it on purpose.
    """
    if os.environ.get("QML_DISK_CACHE_PATH"):
        return
    cache = gui_cache_dir(resolved.build_id)
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A read-only vault still runs; it just recompiles its QML each launch.
        logger.debug("No QML disk cache at %s: %s", cache, exc)
        return
    os.environ["QML_DISK_CACHE_PATH"] = str(cache)


def _log_runtime() -> None:
    """Record which interpreter this launch is running on.

    Ordinarily uninteresting, and then one day the whole answer — a debugger
    attached to last week's dev shell, a frozen build picking up a stray
    PYTHONPATH. Programs a plugin needs are its own to report; Settings →
    Dependencies shows which ones were found and how old they are.
    """
    logger.info("Python %s at %s", sys.version.split()[0], sys.executable)


def _sweep_plugin_leftovers() -> None:
    """Clear what crashed plugin work left behind: temp output, half installs.

    Each sweep only ever touches folders this application made and no running
    process owns, and neither is worth failing a launch over.
    """
    try:
        sweep_stale_temp()
        sweep_leftovers()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not sweep plugin leftovers: %s", exc)


def _sweep_update_leftovers() -> None:
    """Delete installers for versions this build already is, and dead ``.part``s.

    The installer cannot delete itself — it is still running when the app it
    installed starts — so the launch after an update is where it goes.
    """
    try:
        sweep_downloads()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not sweep update downloads: %s", exc)


def _claim_single_instance_mutex() -> None:
    """Create the named mutex the Windows installer looks for.

    ``packaging/kroma-player.iss`` declares ``AppMutex=KromaPlayerSingleInstance``.
    That is how Inno Setup tells whether Kroma is running *before it starts
    replacing files*, and without something creating the mutex the setting does
    nothing at all: a user double-clicking the installer over a running copy
    would get no warning, and detection would fall back entirely to the Restart
    Manager finding files in use partway through.

    Held for the life of the process — the handle is kept at module scope
    because closing it releases the mutex. Nothing here reads the result:
    "already running" is not enforced anywhere, and this is not a
    single-instance guard despite the name Inno gives the setting. A second copy
    of Kroma starts exactly as it did before.

    Windows only, and never fatal. ``ctypes.windll`` does not exist elsewhere,
    and a machine where this fails is a machine where the installer falls back
    to the Restart Manager — worse, and not a reason not to start.
    """
    global _single_instance_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes

        _single_instance_handle = ctypes.windll.kernel32.CreateMutexW(  # type: ignore[attr-defined]
            None, False, SINGLE_INSTANCE_MUTEX
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not create the installer mutex: %s", exc)


def _announce_audio_identity() -> None:
    """Tell the audio server who we are, before we ever connect to it.

    Without this the stream is labelled from whatever the PulseAudio/PipeWire
    client library can infer, which for a Python app is the interpreter — the
    mixer, the per-app volume list and the volume OSD all show "python3" with a
    generic icon. Naming it here is the difference between an anonymous stream
    and an application the desktop recognises.

    ``PULSE_PROP_OVERRIDE`` rather than ``PULSE_PROP``, and the distinction is
    the whole point: the former wins over what the client sets, the latter only
    supplies defaults the client may replace. Qt Multimedia sets
    ``application.name`` itself, from ``QCoreApplication::applicationName()`` —
    which is ``"kord"`` here and must stay that way, because the config
    directory is derived from it. With plain ``PULSE_PROP`` the stream comes out
    labelled "kord"; with the override it comes out labelled "Kroma Player".

    Must run before the first ``QGuiApplication``: the client proplist is read
    once, when the connection is opened, and Qt Multimedia opens it lazily off
    the back of the application object. Setting it later has no effect.

    An existing value is left alone so a user or a wrapper script can override
    this, and it is skipped off Linux where the variable means nothing.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("PULSE_PROP_OVERRIDE"):
        return
    os.environ["PULSE_PROP_OVERRIDE"] = (
        f"application.name='{IDENTITY}' "
        f"application.icon_name='{DESKTOP_FILE_NAME}' "
        f"media.role='music'"
    )


def _report_fatal() -> None:
    """Tell the user the app has stopped, on a build that has no console.

    Frozen Windows only. Anywhere else the traceback is already on stderr, and
    a dialog would be one more thing in the way. Never during a smoke test: a
    modal box on a CI runner waits for a click that will never come.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    if smoke_args(sys.argv) is not None:
        return
    log = current_log_file()
    text = "Kroma Player stopped because of an unexpected error."
    if log is not None:
        text += f"\n\nThe details are in:\n{log}"
    try:
        import ctypes

        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(None, text, "Kroma Player", MB_ICONERROR)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — nothing left to report it with
        pass


def main():
    """Initialize and run the Kroma Player application.

    A wrapper, so that an exception nothing else caught is logged — to the file
    on Windows, where there is no stderr — and explained, rather than ending a
    windowed process without a word.
    """
    configure_logging()
    try:
        return _main()
    except Exception:  # noqa: BLE001
        logger.exception("Kroma Player stopped on an unexpected error")
        _report_fatal()
        return 1


def _main():
    _log_runtime()

    # Before the application object exists — see the docstring.
    _announce_audio_identity()

    # And before anything can hold a file the installer might want to replace.
    _claim_single_instance_mutex()

    # Create Qt application
    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication(sys.argv)
    app.setOrganizationName("kroma")
    # Do not change this to the display name. kord_dir() in
    # app/config/kord_config.py derives the config directory from
    # applicationName(), so renaming it moves ~/.config/kord and orphans the
    # stored vault path — the user would be sent back through vault setup with
    # an apparently empty library. The human-readable name belongs in the
    # desktop entry and PULSE_PROP, both of which are set independently.
    app.setApplicationName("kord")
    app.setApplicationVersion(APP_VERSION)
    # Ties the window to packaging/kroma-player.desktop: sets the Wayland app_id
    # (matched by StartupWMClass) and lets Qt resolve the themed icon.
    app.setDesktopFileName(DESKTOP_FILE_NAME)

    app.setWindowIcon(QIcon("assets/icon.png"))

    # The frozen build's self-test, for CI. Here — after the mutex, so a test
    # can hold it the way a running copy does, and before the vault, because it
    # brings a throwaway one of its own and must never touch the user's.
    smoke = smoke_args(sys.argv)
    if smoke is not None:
        return run_smoke_test(app, smoke.report, smoke.linger)

    # Locate the vault before anything else. The databases, the artwork cache
    # and the installed GUI bundles all live inside it, so nothing below can be
    # built until it is known. If the user closes the setup window without
    # choosing one, there is no library to open — exit quietly.
    #
    # A vault this build cannot open is sent back to the picker rather than
    # opened anyway: a layout from a newer build (vault.json), or a library
    # database a newer build has migrated (its user_version is past this build's
    # SCHEMA_VERSION, so there would be nothing to apply and the app would run
    # against columns it does not know). Opening one also means migrating it,
    # after a snapshot of its database; a migration that fails comes back here
    # too, with the snapshot's location. All of it is app.vault.startup's, and
    # the picker already renders an initial error, so explaining costs no new UI.
    # The QML types go in before the setup window can build the process's first
    # engine — see _register_qml_types. The settings singleton has no store
    # behind it until the registry exists; nothing before then imports it.
    _register_qml_types()

    vault = ensure_vault_ready()
    while vault is not None:
        # Before the checks, not after: a migration step may reach the vault
        # through the path helpers, which resolve against the library root.
        set_library_root(vault)
        problem = open_problem(vault)
        if not problem:
            break
        logger.warning("Refusing the vault at %s: %s", vault, problem)
        vault = run_vault_setup(problem)
        if vault is not None:
            remember_vault(vault)
    if vault is None:
        logger.info("No vault selected — exiting.")
        return 0

    logger.info("Using vault at %s", vault)

    # Make sure the vault can say which library it is. Done here, eagerly,
    # rather than when the first backup runs: an id assigned now is inherited by
    # every copy of the vault made afterwards, which is what lets a backup and
    # its source recognise each other. Two copies that were both made before the
    # id existed would each invent a different one and then refuse to sync.
    ensure_vault_id(vault)

    _sweep_plugin_leftovers()
    _sweep_update_leftovers()

    # Create service registry (dependency injection). This also seeds and
    # resolves the GUI package, and builds the plugin host — which starts no
    # plugin until something asks one a question.
    registry = create_service_registry()

    resolved: ResolvedGui | None = registry.get_service('resolved_gui')
    if resolved is None:
        logger.error(
            "No GUI could be loaded. Install a .kpi under %s or set KROMA_GUI_DIR.",
            gui_root(),
        )
        return 1

    # Before the engine exists — see the docstring.
    _enable_qml_disk_cache(resolved)

    # Point the settings singleton at the store, now that there is one. The
    # types themselves were registered before the vault setup window.
    _register_qml_types(registry)

    # Create QML engine, with the providers and view models in place.
    engine = _build_engine(registry)

    # Load the GUI. A third-party GUI that fails to load must not leave the user
    # with a dead application, so fall back to the copy inside the bundle.
    if not _load_gui(engine, resolved):
        logger.error(
            "GUI '%s' failed to load from %s", resolved.manifest.name, resolved.bundle_path
        )
        fallback = _bundled_fallback(resolved)
        if fallback is None:
            return 1
        logger.warning("Falling back to the bundled '%s' GUI", fallback.manifest.name)
        engine.clearComponentCache()
        if not _load_gui(engine, fallback):
            return 1

    # Join the desktop's media controls. This is what makes the hardware media
    # keys reach the app and what puts it in the system media widget, so it is
    # attempted on every launch — but it depends entirely on the environment
    # (no session bus on Windows, in a bare container, or under some sandboxes),
    # and playback is completely unaffected by its absence. Never fatal.
    mpris = registry.get_service('mpris_service')
    if mpris is not None:
        try:
            mpris.start()
        except MprisUnavailable as exc:
            logger.debug("System media controls unavailable: %s", exc)
            mpris = None
        else:
            # Raise() needs the window, which only exists once QML has loaded.
            roots = engine.rootObjects()
            if roots:
                mpris.set_window(roots[0])
            app.aboutToQuit.connect(mpris.stop)

    # The backend persists what it owns — the queue, and whatever the GUI
    # asked it to store — rather than relying on a GUI to call in on the way out.
    app.aboutToQuit.connect(lambda: persist_app_state(registry))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
