"""Dependency injection bootstrap for Kroma Player."""

import sys

from PySide6.QtCore import QCoreApplication

from app.database.connection import DatabaseManager
from app.repositories.track_repository import TrackRepository
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.lyrics_repository import LyricsRepository
from app.repositories.playlist_repository import PlaylistRepository
from app.plugins.host import PluginHost, safe_mode_requested
from app.plugins.providers import ProviderRegistry
from app.services.album_service import AlbumService
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService
from app.services.import_service import ImportService
from app.services.lyrics_service import LyricsService
from app.services.playlist_service import PlaylistService
from app.audio.qt_multimedia_backend import QtMultimediaBackend
from app.queue.queue_manager import QueueManager
from app.events.event_bus import EventBus
from app.viewmodels.player_view_model import PlayerViewModel
from app.viewmodels.search_view_model import SearchViewModel
from app.viewmodels.queue_view_model import QueueViewModel
from app.viewmodels.history_view_model import HistoryViewModel
from app.viewmodels.sidebar_view_model import SidebarViewModel
from app.viewmodels.pins_view_model import PinsViewModel
from app.repositories.sidebar_pin_repository import SidebarPinRepository
from app.viewmodels.album_edit_view_model import AlbumEditViewModel
from app.viewmodels.album_tracks_view_model import AlbumTracksViewModel
from app.viewmodels.albums_view_model import AlbumsViewModel
from app.viewmodels.artist_edit_view_model import ArtistEditViewModel
from app.viewmodels.artists_view_model import ArtistsViewModel
from app.viewmodels.track_edit_view_model import TrackEditViewModel
from app.viewmodels.database_view_model import DatabaseViewModel
from app.viewmodels.library_view_model import LibraryViewModel
from app.viewmodels.import_view_model import ImportViewModel
from app.viewmodels.settings_view_model import SettingsViewModel
from app.viewmodels.lyrics_view_model import LyricsViewModel
from app.viewmodels.playlists_view_model import PlaylistsViewModel
from app.viewmodels.playlist_tracks_view_model import PlaylistTracksViewModel
from app.artwork.cover_store import CoverStore
from app.artwork.cover_service import CoverService
from app.artwork.cover_provider import CoverImageProvider
from app.viewmodels.artwork_palette_view_model import ArtworkPaletteViewModel
from app.utils.library_paths import covers_db_path, data_root, tracks_db_path
from app.system.mpris import MprisService
from app.system.mpris_art import CACHE_DIR_NAME
from app.gui.installer import ensure_official_gui_installed
from app.gui.resolver import resolve_gui
from app.session import load_session
from app.settings.file import load_settings
from app.viewmodels.gui_view_model import GuiViewModel
from app.library.context import LibraryContext, set_library_context
from app.library.row_cache import TrackRowCache
from app.settings.store import SettingsStore
from app.settings.playback import PlaybackSettings
from app.settings.backing_track import BackingTrackSettings
from app.settings.backup import BackupSettings
from app.settings.export import ExportSettings
from app.settings.imports import ImportSettings
from app.settings.lyrics import LyricsSettings
from app.settings.updates import UpdateSettings
from app.services.backing_track_service import BackingTrackService
from app.viewmodels.backing_track_view_model import BackingTrackViewModel
from app.viewmodels.dependencies_view_model import DependenciesViewModel
from app.viewmodels.plugins_view_model import PluginsViewModel
from app.viewmodels.backup_view_model import BackupViewModel
from app.viewmodels.export_view_model import ExportViewModel
from app.viewmodels.updates_view_model import UpdatesViewModel


class ServiceRegistry:
    """Manages service instances and dependency injection."""
    
    def __init__(self):
        """Initialize service registry."""
        self._services = {}
    
    def register(self, name: str, service) -> None:
        """Register a service."""
        self._services[name] = service
    
    def get_service(self, name: str):
        """Get a registered service."""
        return self._services.get(name)
    
    def has_service(self, name: str) -> bool:
        """Check if service is registered."""
        return name in self._services


def create_service_registry() -> ServiceRegistry:
    """Create and configure the service registry."""
    registry = ServiceRegistry()
    
    # Setup database connection
    db_manager = DatabaseManager.from_path(tracks_db_path())  # TODO: Move to config when settings UI exists
    session = db_manager.create_session()

    registry.register('db_manager', db_manager)
    registry.register('db_engine', db_manager.engine)
    registry.register('db_session', session)
    
    # Setup event bus
    event_bus = EventBus()
    registry.register('event_bus', event_bus)

    # A namespaced key/value store the GUI can persist anything into, and the
    # backend never interprets. This is what lets column layouts, window
    # geometry and pane state move out of the backend without losing
    # persistence along the way.
    #
    # Built early, before the GUI is even resolved and before the plugin host:
    # it owns both documents — settings.json for preferences, session.json for
    # where you were — and which GUI to load is a key in the second of them,
    # which plugins are on a key in the first. Everything that persists
    # anything reads and writes through this one object, so there is one copy
    # of each document and one path to disk.
    settings_store = SettingsStore(load_session(), load_settings())
    registry.register('settings_store', settings_store)

    # The plugin host, and the one object everything else asks for providers.
    # Built before any view model, because which plugins are on decides what a
    # good half of them offer. Nothing starts here: a plugin's process starts
    # the first time something asks it a question, and in safe mode never.
    plugin_host = PluginHost(settings_store, safe_mode=safe_mode_requested(sys.argv))
    registry.register('plugin_host', plugin_host)
    provider_registry = ProviderRegistry(plugin_host)
    registry.register('provider_registry', provider_registry)
    # Said on the bus as well as by the host's own signal, so the list models
    # that already listen there — every TrackQuery a GUI declares — repaint
    # their id columns without each being handed the host.
    plugin_host.plugins_changed.connect(lambda: event_bus.emit("plugins.changed"))

    # Setup cover-art subsystem (thumbnail cache + generator + QML image provider).
    # The store is a disposable, content-addressed cache living beside tracks.db.
    cover_store = CoverStore(covers_db_path())
    cover_service = CoverService(cover_store)
    cover_provider = CoverImageProvider(cover_store)
    registry.register('cover_store', cover_store)
    registry.register('cover_service', cover_service)
    registry.register('cover_provider', cover_provider)
    registry.register('artwork_palette_view_model', ArtworkPaletteViewModel(cover_store))
    
    # Setup repositories
    track_repo = TrackRepository(session)
    album_repo = AlbumRepository(session)
    artist_repo = ArtistRepository(session)
    genre_repo = GenreRepository(session)
    playlist_repo = PlaylistRepository(session)
    lyrics_repo = LyricsRepository(session)
    registry.register('track_repository', track_repo)
    registry.register('album_repository', album_repo)
    registry.register('artist_repository', artist_repo)
    registry.register('genre_repository', genre_repo)
    registry.register('playlist_repository', playlist_repo)
    registry.register('lyrics_repository', lyrics_repo)

    # Setup services (business logic layer)
    #
    # The backing-track service comes first: TrackService needs it to take a
    # deleted track's instrumental down with it.
    backing_track_service = BackingTrackService(event_bus)
    registry.register('backing_track_service', backing_track_service)

    track_service = TrackService(
        track_repo, artist_repo, genre_repo, event_bus, cover_store,
        backing_service=backing_track_service,
        # A *repository*, not AlbumService — and that is what keeps the graph
        # acyclic. AlbumService needs TrackService for the shared cover sweep,
        # so TrackService must not need AlbumService; resolving an album name on
        # import is a repository job, exactly as resolving an artist name is.
        album_repo=album_repo,
        # What each kind of track id means — unique or not, which tag it is
        # mirrored into — is whatever the enabled plugins declare.
        id_kinds=provider_registry,
    )
    registry.register('track_service', track_service)

    playlist_service = PlaylistService(playlist_repo, event_bus, cover_store)
    registry.register('playlist_service', playlist_service)

    pin_repo = SidebarPinRepository(session)
    registry.register('sidebar_pin_repository', pin_repo)
    registry.register('pins_view_model', PinsViewModel(pin_repo, event_bus, playlist_service))

    # The only writer of the `lyrics` table. Every path that obtains lyrics —
    # a download, a metadata refresh, the Now Playing mic button, the one-off
    # port of the old embedded tags — goes through this one object, which is
    # also what makes `lyrics.updated` a single emitter.
    lyrics_service = LyricsService(lyrics_repo, event_bus)
    registry.register('lyrics_service', lyrics_service)

    # After TrackService, which it borrows for the cover sweep: artist portraits
    # share the content-addressed store with album art, so only the service that
    # owns that store can decide a hash is genuinely dead.
    artist_service = ArtistService(artist_repo, event_bus, track_service=track_service)
    registry.register('artist_service', artist_service)

    # After TrackService for the same reason, and it is the stronger case of the
    # two: an album cover is very often a hash no track carries — an identified
    # album holds the release's real artwork while its files keep whatever they
    # were tagged with — so only the service that counts every table can tell a
    # dead hash from a live one.
    album_service = AlbumService(album_repo, event_bus, track_service=track_service)
    registry.register('album_service', album_service)
    
    import_service = ImportService(track_service)
    registry.register('import_service', import_service)

    # The library core. One row cache is shared by every track list, so a track
    # visible in the queue, a search result and a playlist at once is read once.
    # The context is what lets a GUI-declared TrackQuery find its services.
    row_cache = TrackRowCache(track_repo)
    registry.register('row_cache', row_cache)
    set_library_context(
        LibraryContext(
            repository=track_repo, row_cache=row_cache, event_bus=event_bus,
            # What the identity columns are filtered against at paint time.
            id_kinds=provider_registry.declarations,
        )
    )

    # Setup audio backend
    audio_backend = QtMultimediaBackend()
    registry.register('audio_backend', audio_backend)

    # Resolve the GUI before anything QML-facing is built. The official GUI is
    # installed into ~/Music/venomvault/.data/gui/nocturne.kpi first — one
    # compiled file, refreshed only when its build id has moved — so the vault
    # holds the interface it is opened with rather than depending on the
    # application that happens to be launching it.
    ensure_official_gui_installed()
    resolved_gui = resolve_gui(settings_store.selected_gui)
    registry.register('resolved_gui', resolved_gui)
    registry.register('gui_view_model', GuiViewModel(resolved_gui, settings_store))

    # The backend's own policy, read through typed façades rather than string
    # literals at each call site. All of it durable: these are preferences.
    playback_settings = PlaybackSettings(settings_store)
    registry.register('playback_settings', playback_settings)
    backing_track_settings = BackingTrackSettings(settings_store)
    registry.register('backing_track_settings', backing_track_settings)
    export_settings = ExportSettings(settings_store)
    registry.register('export_settings', export_settings)
    backup_settings = BackupSettings(settings_store)
    registry.register('backup_settings', backup_settings)
    import_settings = ImportSettings(settings_store)
    registry.register('import_settings', import_settings)
    # One flag: whether this library's embedded lyric tags have been ported into
    # the table yet. See the module for why losing it is harmless.
    lyrics_settings = LyricsSettings(settings_store)
    registry.register('lyrics_settings', lyrics_settings)
    # Whether to look for a newer release at launch, when that last happened,
    # and which version the user said no to. Preferences, like the rest.
    update_settings = UpdateSettings(settings_store)
    registry.register('update_settings', update_settings)

    queue_manager = QueueManager(max_history=playback_settings.max_history_size)
    registry.register('queue_manager', queue_manager)

    # Restore the queue and history from saved track IDs. Done here — before
    # the view models are created — so they see the populated lists on first
    # render. History is restored too because it doubles as the back-stack, so
    # without it the Previous button would be dead after every restart.
    def _restore(key: str) -> list[dict]:
        """Resolve a saved id list into queue entries, dropping what is gone.

        A queue entry is just an id — the row cache fills in the rest when
        something paints it, so restoring a long list costs one query.
        """
        saved = settings_store.session_app_get(key, [])
        if not saved:
            return []
        ids = [int(tid) for tid in saved]
        existing = {t.id for t in track_service.get_tracks_by_ids(ids)}
        return [{"id": tid} for tid in ids if tid in existing]

    restored_queue = _restore("queue_track_ids")
    if restored_queue:
        queue_manager.set_queue(restored_queue)

    restored_history = _restore("history_track_ids")
    if restored_history:
        queue_manager.set_history(restored_history)

    # Setup ViewModels
    queue_vm = QueueViewModel(queue_manager, event_bus, row_cache)
    registry.register('queue_view_model', queue_vm)

    history_vm = HistoryViewModel(queue_manager, event_bus, row_cache)
    registry.register('history_view_model', history_vm)

    player_vm = PlayerViewModel(
        track_service, queue_manager, audio_backend, event_bus,
        queue_vm, history_vm, playback_settings,
        backing_service=backing_track_service,
        backing_settings=backing_track_settings,
    )
    registry.register('player_view_model', player_vm)

    # Desktop media integration. Constructed unconditionally and inert until
    # started, so this stays platform-independent; app/main.py calls start()
    # and swallows MprisUnavailable when there is no session bus.
    mpris_service = MprisService(
        player_vm,
        cover_store=cover_store,
        art_cache_dir=data_root() / CACHE_DIR_NAME,
    )
    registry.register('mpris_service', mpris_service)

    # Library search resolves to an id vector through the repository; online
    # search is a worker thread over whichever plugin track source is chosen.
    search_vm = SearchViewModel(
        track_repo, provider_registry, event_bus, track_service,
        cover_service, row_cache, artist_service=artist_service,
        lyrics_service=lyrics_service, album_service=album_service,
    )
    registry.register('search_view_model', search_vm)

    db_vm = DatabaseViewModel(track_service)
    registry.register('database_view_model', db_vm)

    # What a GUI needs to build its own track lists: the field catalog and the
    # filter vocabulary. The lists themselves it declares as TrackQuery objects.
    library_vm = LibraryViewModel(track_service)
    registry.register('library_view_model', library_vm)

    import_vm = ImportViewModel(
        import_service, track_service, provider_registry, cover_service,
        import_settings=import_settings,
        lyrics_service=lyrics_service,
        # So an album search can write down that it found nothing missing. The
        # service is built well above this, with the repositories.
        album_service=album_service,
    )
    registry.register('import_view_model', import_vm)

    # Settings → Plugins, and every slot a plugin fills in a GUI.
    plugins_vm = PluginsViewModel(
        plugin_host, provider_registry, settings_store, track_service,
    )
    registry.register('plugins_view_model', plugins_vm)

    # Vocal removal. Runs the model in a separate interpreter the user
    # configures, so this VM is inert — but still present — on a machine that
    # has none set up.
    backing_track_vm = BackingTrackViewModel(
        track_service, backing_track_service, backing_track_settings,
    )
    registry.register('backing_track_view_model', backing_track_vm)

    settings_vm = SettingsViewModel(settings_store, resolved_gui.manifest.name if resolved_gui else "")
    registry.register('settings_view_model', settings_vm)

    # Right-sidebar track-info panel. Reads full track metadata via the service
    # and falls back to the playing track when nothing is selected.
    sidebar_vm = SidebarViewModel(track_service, player_vm, event_bus)
    registry.register('sidebar_view_model', sidebar_vm)

    # The track editor's hidden tab. One track at a time; see the module docs.
    track_edit_vm = TrackEditViewModel(
        track_service, backing_track_service, cover_service, lyrics_service,
        registry=provider_registry,
    )
    registry.register('track_edit_view_model', track_edit_vm)

    # The gain slider is heard while it is dragged, so the editor has to reach
    # the transport. Signals rather than a constructor argument: the editor has
    # no business holding the player — it announces which track it has open and
    # at what level, and the player decides whether that is the one playing.
    track_edit_vm.gain_preview_requested.connect(player_vm.set_gain_preview)
    track_edit_vm.gain_preview_cleared.connect(player_vm.clear_gain_preview)

    # Artists: the whole list, and the one that is open. Two objects for the same
    # reason the track side has two — a list is browsed, an entity is edited, and
    # an editor holding a selection would have to answer what "the name" means for
    # forty rows at once.
    artists_vm = ArtistsViewModel(artist_service, event_bus)
    registry.register('artists_view_model', artists_vm)

    artist_edit_vm = ArtistEditViewModel(artist_service, provider_registry, cover_service)
    registry.register('artist_edit_view_model', artist_edit_vm)

    # Albums, in the same three-way split as the track side and for the same
    # reason: a grid that is browsed, an entity that is edited, and — unlike
    # artists — an ordered track list, because an album's running order is a fact
    # about the album that no TrackQuery sort can express.
    albums_vm = AlbumsViewModel(album_service, event_bus)
    registry.register('albums_view_model', albums_vm)

    album_edit_vm = AlbumEditViewModel(album_service, provider_registry, cover_service)
    registry.register('album_edit_view_model', album_edit_vm)

    album_tracks_vm = AlbumTracksViewModel(album_service, event_bus, row_cache)
    registry.register('album_tracks_view_model', album_tracks_vm)

    lyrics_vm = LyricsViewModel(player_vm, lyrics_service, lyrics_settings)
    registry.register('lyrics_view_model', lyrics_vm)

    # Playlist view models: the grid of all playlists, and the track list shown
    # when one playlist is opened. The grid VM is also the hub the queue/history/
    # search/import context menus use to add tracks to a playlist.
    playlists_vm = PlaylistsViewModel(playlist_service, event_bus)
    registry.register('playlists_view_model', playlists_vm)

    playlist_tracks_vm = PlaylistTracksViewModel(playlist_service, event_bus, row_cache)
    registry.register('playlist_tracks_view_model', playlist_tracks_vm)

    # Copying audio back out of the vault. Takes both services because a GUI can
    # export a whole playlist or an arbitrary selection of tracks.
    export_vm = ExportViewModel(
        playlist_service, track_service, export_settings, lyrics_service,
    )
    registry.register('export_view_model', export_vm)

    # Replicating the whole vault somewhere else. Depends on no service: it
    # works off the filesystem and takes its own consistent snapshot of the
    # databases, so it neither needs nor may borrow the shared session.
    # The version is read off the application rather than imported from
    # app.main, which imports this module.
    backup_vm = BackupViewModel(
        backup_settings, QCoreApplication.applicationVersion(),
    )
    registry.register('backup_view_model', backup_vm)

    # Whether ffmpeg and every program an enabled plugin needs are installed. A
    # release does not ship them, so the answer comes from the machine; the
    # host says which plugins are on and so which programs to ask about.
    registry.register('dependencies_view_model', DependenciesViewModel(plugin_host))

    # Whether there is a newer Kroma, and installing it. Beside the dependency
    # view model for the same reason it is: it depends on no service. What it
    # reads is the network and the machine — how this copy was installed decides
    # whether it can update itself at all, and no part of the library affects
    # that answer.
    registry.register('updates_view_model', UpdatesViewModel(
        update_settings, before_apply=plugin_host.shutdown,
    ))

    return registry


def persist_app_state(registry: ServiceRegistry) -> None:
    """Write the state the backend owns, and flush anything the GUI stored.

    Called on shutdown. Deliberately not something the GUI has to ask for: the
    old arrangement hung queue persistence off a GUI's window-closing handler,
    so a GUI that did not call it silently stopped saving the queue.
    """
    # Before anything else: every plugin process. Their pending calls fail
    # first, so a worker blocked on one — a fetch, an identify — wakes now and
    # its thread can finish before the objects it reports to are torn down.
    plugin_host = registry.get_service('plugin_host')
    if plugin_host is not None:
        plugin_host.shutdown()
    search_vm = registry.get_service('search_view_model')
    if search_vm is not None:
        search_vm.shutdown()

    # Then the separation, which runs in a child interpreter Kroma started:
    # closing the window must not leave it holding the GPU with nowhere to
    # deliver its output.
    backing_vm = registry.get_service('backing_track_view_model')
    if backing_vm is not None:
        backing_vm.shutdown()

    # Likewise an export: it writes into a folder outside the vault, and a
    # thread still doing that after the window is gone would be indefensible.
    export_vm = registry.get_service('export_view_model')
    if export_vm is not None:
        export_vm.shutdown()

    # And a backup, for the same reason twice over: it writes outside the vault,
    # and it holds a lock file inside the backup that nothing else will clear.
    backup_vm = registry.get_service('backup_view_model')
    if backup_vm is not None:
        backup_vm.shutdown()

    # And an update, for a third reason again: a download in flight has a
    # partial file open outside the vault, and — on Linux — an update the user
    # confirmed is *performed here*, once everything below has been written.
    # `execv` unwinds nothing, so it has to be the last thing that happens.
    updates_vm = registry.get_service('updates_view_model')

    # And the lyric tag port, which on a first run over a large library is still
    # walking files when the window closes. It writes nothing itself — the view
    # model does, on this thread — so stopping it costs only the batch in hand.
    # And a unify run, which rewrites Opus files inside the vault. A half-written
    # tag is not something to leave behind at exit.
    album_edit_vm = registry.get_service('album_edit_view_model')
    if album_edit_vm is not None:
        album_edit_vm.shutdown()

    lyrics_vm = registry.get_service('lyrics_view_model')
    if lyrics_vm is not None:
        lyrics_vm.shutdown()

    playlist_service = registry.get_service('playlist_service')
    if playlist_service is not None:
        playlist_service.shutdown()

    store = registry.get_service('settings_store')
    queue_manager = registry.get_service('queue_manager')
    playback = registry.get_service('playback_settings')

    if store is not None:
        if queue_manager is not None:
            settings = playback or PlaybackSettings(store)
            # History is saved as well as the queue: it is the back-stack the
            # Previous button pops from, so a session that restored only the queue
            # would come back unable to step backwards.
            def saved_ids(entries: list[dict], limit: int) -> list[int]:
                # ``is not None`` rather than a truth test: an id of 0 is a real id.
                return [e["id"] for e in entries if e.get("id") is not None][:limit]

            store.session_app_set(
                "queue_track_ids",
                saved_ids(queue_manager.queue_entries(), settings.persisted_queue_limit),
            )
            store.session_app_set(
                "history_track_ids",
                saved_ids(queue_manager.history_entries(), settings.max_history_size),
            )

        store.flush()

    # Last, and after the flush above rather than beside the other shutdowns.
    # On Linux this is where a confirmed update actually happens: it replaces
    # the running AppImage and calls `execv`, which unwinds nothing — no
    # atexit, no Qt teardown, no second chance to write a file. Anything not
    # already on disk at this line is gone. Ordinarily it does nothing at all.
    if updates_vm is not None:
        updates_vm.shutdown()
