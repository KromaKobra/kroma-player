"""Online search results serve their own row data.

Online results are not library tracks: there is no database row behind them, so
every field they show has to come out of the row the plugin's track source
returned. The fetchable ones go through ``local_value``; the ROW-tier ones —
``coverUrl``, the only artwork an online result has — go through ``row_value``,
because ``data()`` never consults the cache for that tier.

The identity fields are derived from the row's ids and what enabled plugins
declare, at paint time — so a plugin turned off takes the label and the link
away without anything being re-read.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QModelIndex

from app.library.context import LibraryContext, set_library_context
from app.library.roles import FIELD_TO_ROLE
from app.viewmodels.search_view_model import SearchViewModel
from tests.fakes.providers import KINDS, VIDEO, FakeRegistry, FakeTrackSource, row


COVER_URL = "https://img.fake.example/thumb=w120"

WEB_RESULTS = [
    {**row("Web Track", "Web Artist", album="Web Album", duration=210,
           video="vidweb00001"), "cover_url": COVER_URL},
]


from tests.conftest import FindsOwnedByIdOnly  # noqa: E402


class _StubTrackService(FindsOwnedByIdOnly):
    def existing_ids(self, values):
        return {}


@pytest.fixture(autouse=True)
def declared(repo, cache, event_bus):
    """The library context, with the fake plugin's id kinds declared."""
    kinds = list(KINDS)
    set_library_context(LibraryContext(repo, cache, event_bus, id_kinds=lambda: kinds))
    yield kinds


def _web_vm(repo, cache, event_bus):
    source = FakeTrackSource()
    vm = SearchViewModel(
        track_repository=repo,
        registry=FakeRegistry(sources=[source]),
        event_bus=event_bus,
        track_service=_StubTrackService(),
        row_cache=cache,
    )
    vm.toggle_search_source()
    assert not vm.search_in_database
    vm._on_web_search_finished(source.key, [dict(r) for r in WEB_RESULTS])
    return vm


def _read(vm, row, key):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE[key])


def test_web_result_serves_its_cover_url(repo, cache, event_bus):
    vm = _web_vm(repo, cache, event_bus)
    assert _read(vm, 0, "coverUrl") == COVER_URL


def test_web_result_serves_its_own_fetchable_fields(repo, cache, event_bus):
    vm = _web_vm(repo, cache, event_bus)
    assert _read(vm, 0, "title") == "Web Track"
    assert _read(vm, 0, "externalIds") == {VIDEO: "vidweb00001"}
    assert _read(vm, 0, "externalUrl") == "https://fake.example/watch?v=vidweb00001"
    assert _read(vm, 0, "sourceLabel") == "Fake Music"
    assert _read(vm, 0, "coverHash") is None, "a web result has no cached art"


def test_web_result_keeps_identity_and_selection(repo, cache, event_bus):
    """The row dict must not shadow what the base model answers."""
    vm = _web_vm(repo, cache, event_bus)
    uid = _read(vm, 0, "uid")

    assert uid is not None
    assert _read(vm, 0, "trackId") is None, "not in the library, so no id"
    assert _read(vm, 0, "isSelected") is False

    vm.setSelected(uid, True)
    assert _read(vm, 0, "isSelected") is True


def test_database_rows_are_unaffected(repo, cache, event_bus):
    """Switching back to the library reads from the cache, not the web rows."""
    vm = _web_vm(repo, cache, event_bus)
    vm.toggle_search_source()
    vm.load_all_tracks()

    assert _read(vm, 0, "coverUrl") is None, "library rows have a hash, not a URL"
    _read(vm, 0, "title")
    vm.flush_now()
    assert _read(vm, 0, "title") == "Track 00000"


def test_ids_no_enabled_plugin_declares_are_not_shown(repo, cache, event_bus, declared):
    """Off is off: the row keeps its id, and nothing on screen speaks for it."""
    vm = _web_vm(repo, cache, event_bus)
    declared.clear()

    assert _read(vm, 0, "externalIds") == {}
    assert _read(vm, 0, "externalUrl") == ""
    assert _read(vm, 0, "sourceLabel") == ""
    assert vm.webResult(_read(vm, 0, "uid"))["external_ids"] == {VIDEO: "vidweb00001"}
