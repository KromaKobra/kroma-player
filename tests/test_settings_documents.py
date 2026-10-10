"""Two documents, two lifetimes.

Everything a user could change used to sit in one flat bag inside
``session.json``: the queue and the download agreement, the window size and the
backup destination. One file, one blast radius — and ``load_session()`` returns
``{}`` on *any* error, so a torn write to the queue also revoked the download
permission and forgot which drive backups went to. That permission is a
plugin's consent now, kept in ``settings.json``'s ``plugins`` scope, and the
same split protects it.

The split is the point of these tests. ``settings.json`` is how you like it,
``session.json`` is where you were, and neither can take the other down. The
lifetime tests below are the ones that pin that; nothing else in the suite does.

The other half is the boundary. It used to be a string prefix — a GUI's key was
run through ``gui_key()`` and could not be made to *match* ``app.*``. It is
structural now: a GUI hands over a leaf key and the store places it inside its
own package's subtree, so there is no path that would put it anywhere else — not
``app``, and not ``plugins``, where a plugin's consent lives. The tests here try
to escape it rather than asserting the prefix is applied.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.session import SESSION_VERSION, load_session, session_path  # noqa: E402
from app.settings import document  # noqa: E402
from app.settings.file import (  # noqa: E402
    SETTINGS_VERSION,
    load_settings,
    settings_path,
)
from app.settings.store import SettingsStore  # noqa: E402

PACKAGE = "nocturne"
PLUGIN = "kroma.fetch-audio"
CONSENT = {"sha256": "0" * 64, "at": "2026-10-08T00:00:00+00:00"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def store():
    """A store over two empty documents, as a first launch has."""
    return SettingsStore({}, {})


# -- where things land ---------------------------------------------------


def test_the_two_documents_sit_beside_each_other():
    assert settings_path().parent == session_path().parent
    assert settings_path().name == "settings.json"


def test_the_documents_do_not_share_a_version():
    """Two shapes, two constants — or bumping one would discard the other."""
    assert SESSION_VERSION == 2
    assert SETTINGS_VERSION == 1


def test_a_preference_goes_to_settings_and_session_state_to_the_session(store):
    store.app_set("volume", 0.4)
    store.session_app_set("queue_track_ids", [7, 8])
    store.gui_set(PACKAGE, "accent", "ember")
    store.session_gui_set(PACKAGE, "window.width", 1280)
    store.flush()

    settings = _read(settings_path())
    session = _read(session_path())

    assert settings["app"]["volume"] == 0.4
    assert settings["gui"][PACKAGE]["accent"] == "ember"
    assert "queue_track_ids" not in settings.get("app", {})

    assert session["app"]["queue_track_ids"] == [7, 8]
    assert session["gui"][PACKAGE]["window.width"] == 1280
    assert "volume" not in session.get("app", {})


def test_a_leaf_key_with_a_dot_in_it_stays_one_key(store):
    """`columns.queue` holds JSON. Nesting on the dot would make it ambiguous."""
    store.gui_set(PACKAGE, "columns.queue", '{"v":1}')
    store.flush()

    assert _read(settings_path())["gui"][PACKAGE]["columns.queue"] == '{"v":1}'


def test_the_chosen_gui_is_session_state(store):
    store.selected_gui = "someone-elses"
    store.flush()

    assert _read(session_path())["selected_gui"] == "someone-elses"
    assert SettingsStore(None).selected_gui == "someone-elses"


def test_a_clean_store_writes_nothing(store):
    store.flush()

    assert not settings_path().exists()
    assert not session_path().exists()


def test_writing_the_value_that_is_already_there_is_not_a_change(store):
    """What stops a volume drag dirtying the store on every frame."""
    store.app_set("volume", 0.4)
    store.flush()
    assert store.dirty is False

    store.app_set("volume", 0.4)
    assert store.dirty is False

    store.app_set("volume", 0.5)
    assert store.dirty is True


# -- independent lifetimes -----------------------------------------------


def test_a_corrupt_session_leaves_every_preference_intact(store):
    """The whole point of the split, and nothing else pins it."""
    store.plugin_set(PLUGIN, "consent", CONSENT)
    store.app_set("backup_directory", "/mnt/backup")
    store.session_app_set("queue_track_ids", [1, 2, 3])
    store.flush()

    session_path().write_text("{ this is not json", encoding="utf-8")

    reopened = SettingsStore(None)
    assert reopened.plugin_get(PLUGIN, "consent") == CONSENT
    assert reopened.app_get("backup_directory") == "/mnt/backup"
    assert reopened.session_app_get("queue_track_ids", []) == []


def test_a_corrupt_settings_file_leaves_the_session_intact(store):
    store.plugin_set(PLUGIN, "consent", CONSENT)
    store.session_app_set("queue_track_ids", [1, 2, 3])
    store.session_gui_set(PACKAGE, "window.width", 1280)
    store.flush()

    settings_path().write_text("{ this is not json", encoding="utf-8")

    reopened = SettingsStore(None)
    assert reopened.session_app_get("queue_track_ids") == [1, 2, 3]
    assert reopened.session_gui_get(PACKAGE, "window.width") == 1280
    # Fails closed, which is what a permission has to do.
    assert reopened.plugin_get(PLUGIN, "consent") is None


@pytest.mark.parametrize("path_of, version", [
    (settings_path, SETTINGS_VERSION),
    (session_path, SESSION_VERSION),
])
def test_a_version_it_does_not_know_reads_as_empty(path_of, version):
    """How the old flat shape is discarded: no migration code at all."""
    path = path_of()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version + 1, "app": {"volume": 0.9}}), encoding="utf-8"
    )

    assert document.load(path, version) == {}


def test_only_the_durable_document_pays_for_an_atomic_write(store, monkeypatch):
    """A torn settings.json costs real state; a torn session.json costs a queue.

    Asserted rather than assumed because the two files are written by the same
    helper, and the flag that separates them is a keyword argument nothing else
    would notice going missing.
    """
    written: list[str] = []
    real = document.write_json_atomic

    def spy(path, doc):
        written.append(path.name)
        return real(path, doc)

    monkeypatch.setattr(document, "write_json_atomic", spy)

    store.app_set("volume", 0.4)
    store.session_app_set("queue_track_ids", [1])
    store.flush()

    assert written == ["settings.json"]
    assert _read(session_path())["app"]["queue_track_ids"] == [1]


def test_a_long_queue_does_not_become_a_long_file(store):
    """Five hundred ids are a list, not five hundred lines to scroll past."""
    store.session_app_set("queue_track_ids", list(range(500)))
    store.flush()

    text = session_path().read_text(encoding="utf-8")
    assert "499]" in text, "the whole queue is there"
    assert len(text.splitlines()) < 10


# -- the boundary --------------------------------------------------------


def test_a_gui_write_lands_inside_its_own_package(qt_app, store):
    from app.viewmodels.settings_view_model import SettingsViewModel

    vm = SettingsViewModel(store, PACKAGE)
    vm.setValue("accent", "ember")
    vm.setSessionValue("navIndex", 3)
    store.flush()

    assert _read(settings_path())["gui"][PACKAGE]["accent"] == "ember"
    assert _read(session_path())["gui"][PACKAGE]["navIndex"] == 3


@pytest.mark.parametrize("key", [
    "downloads_allowed",
    "app.downloads_allowed",
    "../app/downloads_allowed",
    "plugins",
    f"plugins.{'kroma.fetch-audio'}.consent",
    "../plugins/kroma.fetch-audio/consent",
    "..",
    "gui.nocturne.x",
])
def test_no_key_a_gui_supplies_reaches_the_app_scope(qt_app, store, key):
    """There is no string that lands outside `gui.<package>`, by construction.

    Not because a prefix makes it fail to match — because the store descends
    into the package's subtree and writes the key there, and the package comes
    from the resolved manifest rather than from QML.
    """
    from app.viewmodels.settings_view_model import SettingsViewModel

    vm = SettingsViewModel(store, PACKAGE)
    vm.setValue(key, True)
    vm.setSessionValue(key, True)
    store.flush()

    settings = _read(settings_path())
    session = _read(session_path())

    assert settings.get("app", {}) == {}
    assert settings.get("plugins", {}) == {}
    assert session.get("app", {}) == {}
    assert key in settings["gui"][PACKAGE]
    assert store.plugin_get(PLUGIN, "consent") is None


def test_two_guis_do_not_tread_on_each_other(store):
    store.gui_set(PACKAGE, "accent", "ember")
    store.gui_set("someone-elses", "accent", "bloom")

    assert store.gui_get(PACKAGE, "accent") == "ember"
    assert store.gui_get("someone-elses", "accent") == "bloom"


@pytest.mark.parametrize("key", ["", None, 3, b"accent"])
def test_a_key_that_is_not_a_key_is_refused(store, key):
    store.gui_set(PACKAGE, key, "x")
    store.app_set(key, "x")

    assert store.dirty is False


def test_a_gui_scope_needs_a_package(store):
    store.gui_set("", "accent", "ember")

    assert store.dirty is False
    assert store.gui_get("", "accent", "none") == "none"


# -- the GUI selection ---------------------------------------------------


def test_choosing_a_gui_is_not_undone_by_the_next_flush(qt_app, tmp_path):
    """The regression: selecting a GUI and then dragging a column lost it.

    `GuiViewModel.select()` used to mutate its own copy of the session dict and
    save it, while the store held a different copy that knew nothing about the
    change — so the store's next flush, 750 ms after any column drag, wrote the
    boot-time value straight back over it.
    """
    from app.gui.installer import ensure_official_gui_installed
    from app.gui.resolver import resolve_gui
    from app.viewmodels.gui_view_model import GuiViewModel

    ensure_official_gui_installed()
    store = SettingsStore({}, {})
    view_model = GuiViewModel(resolve_gui(store.selected_gui), store)

    assert view_model.select(PACKAGE) is True
    assert load_session()["selected_gui"] == PACKAGE

    # Anything at all afterwards, and then the debounced write.
    store.gui_set(PACKAGE, "columns.queue", '{"v":1}')
    store.flush()

    assert load_session()["selected_gui"] == PACKAGE
    assert SettingsStore(None).selected_gui == PACKAGE


def test_an_invalid_gui_name_writes_nothing(qt_app):
    from app.gui.installer import ensure_official_gui_installed
    from app.gui.resolver import resolve_gui
    from app.viewmodels.gui_view_model import GuiViewModel

    ensure_official_gui_installed()
    store = SettingsStore({}, {})
    view_model = GuiViewModel(resolve_gui(store.selected_gui), store)

    assert view_model.select("../../etc") is False
    assert view_model.select("not-installed") is False
    assert store.selected_gui == ""
    assert load_settings() == {}


# -- the retired file ----------------------------------------------------

# A QML `Settings` file the package used to write inside its own folder, holding
# the accent, the theme and which tab was open. Those keys live in settings.json
# and session.json now, where a read-only install can still write them and a
# backup carries them.
RETIRED_PACKAGE_FILE = "nocturne.ini"


def test_an_installed_gui_cannot_accumulate_state(qt_app):
    """The retired ini cannot come back, because a bundle is not writable.

    This used to need sweeping: the installer carried across every file the
    bundle did not ship, so a stale `nocturne.ini` survived each update forever.
    A `.kpi` is a read-only Qt resource — there is no longer anywhere inside a
    package for a GUI to write, so there is nothing to sweep.
    """
    from app.gui.gui_paths import gui_bundle_path
    from app.gui.installer import ensure_official_gui_installed

    installed = ensure_official_gui_installed()

    assert installed == gui_bundle_path(PACKAGE)
    assert installed.is_file(), "the package is one file"
    assert not installed.is_dir()


def test_the_source_tree_ships_no_ini_of_its_own():
    """A build cut from a dirty tree must not carry a developer's theme.

    The compiler folds the source tree in verbatim, so a stray ini sitting in a
    working copy would be compiled straight into the released bundle.
    """
    from app.gui.gui_paths import bundled_gui_source

    assert not (bundled_gui_source(PACKAGE) / RETIRED_PACKAGE_FILE).exists()
