"""Importing a whole release from an identified album.

The album page knows which record it is; the record knows what is on it. This is
the source that joins the two, and it is the only one whose candidates are
already finished when they arrive — one ``album.tracks`` answer returns every
field an identify run would have gone looking for, per track, so there is
nothing left to search.

That is why it does not go through ``_start_search``. Everything *after* the
merge is shared with every other source and is tested where that source is
tested; what is particular here is three things:

**It starts from the library's album, not from an id.** ``findAlbumTracks``
takes the album's row id and reads the album's own external ids; an album that
is not identified, or a library with no plugin able to list a release, is
refused before anything starts.

**The denominator arrives late.** Every other run knows how many candidates it
has before it starts. A release does not say how long it is until it has been
read, and the completion toast quotes that figure, so it comes over its own
signal ahead of the rows.

**Pressing it twice is not a mistake.** The whole album is listed every time,
including the tracks already in the library, so the second press has to collide
with the first rather than double the list.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.viewmodels import import_view_model  # noqa: E402
from app.viewmodels.import_view_model import (  # noqa: E402
    STATE_DOWNLOAD,
    STATE_IN_LIBRARY,
    STATE_UNAVAILABLE,
    ImportViewModel,
)
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import RELEASE as RELEASE_KEY, VIDEO, FakeMetadata, from_legacy, id_rows  # noqa: E402


BROWSE_ID = "MPREb_abcdefghijklm"
PLAYLIST_ID = "OLAK5uy_abcdefghijklm"


# -- doubles ---------------------------------------------------------------


class _StubImportService:
    """The library, as a map of ids and a list of recordings it holds.

    ``owned`` is the real rule run over ``recordings`` — plain
    ``(title, artist, duration)`` triples — rather than a hand-written stand-in,
    so a test asserting that a row is recognised is asserting about the rule the
    application actually applies.
    """

    def __init__(self, existing=None, recordings=None):
        self.existing = existing or {}
        # {track_id: (title, artist, duration_seconds)}
        self.recordings = recordings or {}

    def existing_ids(self, ids):
        return {
            yid: self.existing[yid]
            for yid in ids
            if yid and yid in self.existing
        }

    def owned_track_ids(self, results):
        from app.library.recording_match import best_match, recording_of
        from app.plugins.external_ids import lookup_values

        library = {
            track_id: recording_of(*fields)
            for track_id, fields in self.recordings.items()
        }
        owned = []
        for result in results:
            found = next(
                (self.existing[v] for _k, v in lookup_values(result) if v in self.existing),
                None,
            )
            if found is None:
                found = best_match(
                    recording_of(
                        result.get("title"),
                        result.get("artist"),
                        result.get("duration"),
                    ),
                    library,
                )
            owned.append(found)
        return owned


class _StubDownloads:
    allowed = True


class _StubImports:
    """Rows that reach the library stay listed — see tests/test_import_clear.py."""
    clear_imported = False


class _RefusingEngine:
    """These tests stop before the worker runs, so nothing here may be called."""

    def match_file(self, probed, use_filename_fallback):  # pragma: no cover
        raise AssertionError("the album path must never reach the match engine")


class _Albums:
    """`AlbumService.get`, for the albums a test names. Rows carry their ids."""

    def __init__(self, **albums):
        self.albums = albums

    def get(self, album_id):
        return self.albums.get(f"a{album_id}")


IDENTIFIED = SimpleNamespace(
    id=7, name="Discovery", external_id_rows=id_rows("album", {RELEASE_KEY: BROWSE_ID}),
)
UNIDENTIFIED = SimpleNamespace(id=8, name="Homework", external_id_rows=[])


def _registry():
    return registry_for(_RefusingEngine(), audio=FakeAudio(), metadata=FakeMetadata())


@pytest.fixture
def vm(qt_app):
    return ImportViewModel(
        import_service=_StubImportService(),
        track_service=None,
        registry=_registry(),
        import_settings=_StubImports(),
        album_service=_Albums(a7=IDENTIFIED, a8=UNIDENTIFIED),
    )


@pytest.fixture
def started(vm, monkeypatch):
    """Capture the album a run would be started for, without running it.

    Stubbing the start is not only for speed: left alone it spawns a real
    QThread, and an engine that refuses to be called would raise inside it,
    where a test cannot catch it and the process aborts.
    """
    calls: list = []
    monkeypatch.setattr(vm, "_start_album_search", lambda album_id, ids: calls.append((album_id, ids)))
    return calls


def _row_legacy(title, *, source_id, album="Discovery", alt_source_ids=()):
    """One entry as `fetch_album_tracks` assembles them."""
    return {
        "title": title,
        "artist": "Daft Punk",
        "album": album,
        "duration": 200,
        "source_id": source_id,
        "cover_url": "",
        "cover_master_url": "",
        "source_path": None,
        "release_date": 20010000,
        "artist_refs": [{"name": "Daft Punk", "source_id": "UCDaftPunk"}],
        "album_ref": {"name": album, "source_id": BROWSE_ID},
        # The music video's id for the same recording — see `lookup_values`.
        "alt_source_ids": list(alt_source_ids),
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


RELEASE = [
    _row("One More Time", source_id="vid1", alt_source_ids=["omv1"]),
    _row("Aerodynamic", source_id="vid2", alt_source_ids=["omv2"]),
    _row("Digital Love", source_id=""),      # blocked where the user is
]

#: What the library holds, for the recording-level check. Keyed by track id.
#: See `_StubImportService`.
OWNS_ONE_MORE_TIME = {77: ("One More Time", "Daft Punk", 200)}


def _deliver(vm, rows=RELEASE, total=None):
    """Play back what the worker emits: the count, then the rows."""
    vm._set_download_mode(True)
    vm._on_album_counted(len(rows) if total is None else total)
    vm._on_search_finished([dict(r) for r in rows])


def _states(vm):
    return [vm._state(row) for row in vm._results]


# ======================================================================
# The album
# ======================================================================


def test_an_identified_album_starts_a_run_for_its_release(vm, started):
    vm.findAlbumTracks(7)

    assert started == [(7, {RELEASE_KEY: BROWSE_ID})]


def test_an_album_with_no_id_is_refused_up_front(vm, started):
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.findAlbumTracks(8)

    assert started == [], "started a run with no release to read"
    assert errors and "not identified" in errors[0]


def test_an_album_no_longer_in_the_library_is_refused(vm, started):
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.findAlbumTracks(99)

    assert started == []
    assert errors == ["That album is no longer in the library."]


def test_no_plugin_that_lists_releases_is_refused(vm, started):
    """The button is hidden then, and a call that arrives anyway says why."""
    vm._registry.set(metadata_provider=None)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.findAlbumTracks(7)

    assert started == []
    assert errors == ["No enabled plugin can list an album's tracks."]


def test_the_rows_are_downloads(vm, started):
    """Nothing on the record is on the machine, so every row is fetched."""
    vm.findAlbumTracks(7)

    assert vm.isDownloadMode is True


def test_no_candidates_are_built(vm, started):
    """`_candidates` holds ProbedFiles for an identify run, and there is not one."""
    vm.findAlbumTracks(7)

    assert vm._candidates == []


# ======================================================================
# The denominator
# ======================================================================


def test_the_total_is_the_length_of_the_release(vm):
    """What the completion toast quotes `lastMatchedCount` against."""
    _deliver(vm)

    assert vm.searchTotal == 3


def test_the_total_is_provisional_until_the_release_answers(vm, monkeypatch):
    """A bar with no denominator at all is worse than one that corrects itself."""
    # The worker is built and wired but never runs: this is about the two
    # figures either side of `counted`, not about the fetch between them.
    monkeypatch.setattr(import_view_model.QThread, "start", lambda self: None)

    vm._set_download_mode(True)
    vm._start_album_search(7, {RELEASE_KEY: BROWSE_ID})

    assert vm.searchTotal == 1
    vm._on_album_counted(12)
    assert vm.searchTotal == 12


# ======================================================================
# The merge
# ======================================================================


def test_the_whole_release_is_listed(vm):
    _deliver(vm)

    assert [r["title"] for r in vm._results] == [
        "One More Time", "Aerodynamic", "Digital Love",
    ]


def test_a_track_already_owned_is_marked_rather_than_dropped(vm):
    """"Some of these you already have" is the answer the page exists to give."""
    vm._import_service = _StubImportService({"vid1": 77})

    _deliver(vm)

    assert _states(vm) == [STATE_IN_LIBRARY, STATE_DOWNLOAD, STATE_UNAVAILABLE]
    assert vm.inDatabaseCount == 1
    assert vm.lastInDatabaseCount == 1


def test_a_track_owned_under_the_music_videos_id_is_recognised(vm):
    """The release names the audio; the library downloaded the video.

    Same recording, two ids, and asking only about the row's own would report a
    record the user already has as entirely missing — which is what it did.
    """
    vm._import_service = _StubImportService({"omv1": 77})

    _deliver(vm)

    assert _states(vm) == [STATE_IN_LIBRARY, STATE_DOWNLOAD, STATE_UNAVAILABLE]
    assert vm._results[0]["id"] == 77
    # ...and the row still fetches the *audio* if it comes to that: an
    # alternate is a second name for the recording, not a better one.
    assert vm._results[0]["external_ids"] == {VIDEO: "vid1"}


def test_a_track_owned_under_its_own_id_still_is(vm):
    """The wider `IN` list must not have cost the ordinary case anything."""
    vm._import_service = _StubImportService({"vid1": 77})

    _deliver(vm)

    assert vm._results[0]["id"] == 77
    assert vm.lastInDatabaseCount == 1


def test_a_track_owned_under_a_third_release_is_recognised(vm):
    """The ordinary case, and the one no id can reach.

    A catalogue mints an id per *release*, so a track downloaded from the
    single shares neither the album's audio id nor its music video's. Measured
    on a real library, eight of Walkerworld's ten tracks are owned this way.
    """
    vm._import_service = _StubImportService(recordings=OWNS_ONE_MORE_TIME)

    _deliver(vm)

    assert _states(vm) == [STATE_IN_LIBRARY, STATE_DOWNLOAD, STATE_UNAVAILABLE]
    assert vm._results[0]["id"] == 77


def test_a_second_of_difference_does_not_lose_the_match(vm):
    """A library's duration comes from the file, the release's from the service.

    They disagree by one often enough to matter — *the cure* is 297s owned
    against 298s published — and exact equality misses it.
    """
    vm._import_service = _StubImportService(
        recordings={77: ("One More Time", "Daft Punk", 201)}
    )

    _deliver(vm)

    assert vm._results[0]["id"] == 77


def test_a_different_artists_song_of_the_same_name_is_not_owned(vm):
    """The artist test, and the false positive it exists to prevent.

    Faouzia's *Hero* is 175s and Alan Walker's is 176s. Without this the import
    list would hide a track the user came here to download.
    """
    vm._import_service = _StubImportService(
        recordings={77: ("One More Time", "Someone Else", 200)}
    )

    _deliver(vm)

    assert vm._results[0]["id"] is None
    assert _states(vm)[0] == STATE_DOWNLOAD


def test_a_length_well_outside_the_tolerance_is_not_owned(vm):
    """A remix of the same name is a different recording."""
    vm._import_service = _StubImportService(
        recordings={77: ("One More Time", "Daft Punk", 260)}
    )

    _deliver(vm)

    assert vm._results[0]["id"] is None


def test_a_row_with_no_id_is_never_matched_by_recording_alone(vm):
    """The blocked row has a title, a credit and a length, and stays unavailable.

    It has nothing to download from, so recognising it would be beside the
    point — but it must not be *mistaken* for owned either. Here the library
    genuinely has no Digital Love, and the row keeps saying so.
    """
    vm._import_service = _StubImportService(recordings=OWNS_ONE_MORE_TIME)

    _deliver(vm)

    assert _states(vm)[2] == STATE_UNAVAILABLE


def test_a_blocked_track_is_listed_as_unavailable(vm):
    """It is part of the record. Dropping it would shorten the album silently."""
    _deliver(vm)

    assert _states(vm)[2] == STATE_UNAVAILABLE


def test_the_album_ref_survives_the_merge(vm):
    """It is what files an import against the identified record, not a namesake."""
    _deliver(vm)

    assert vm._results[0]["album_ref"] == {"name": "Discovery", "external_ids": {RELEASE_KEY: BROWSE_ID}}


def test_pressing_it_twice_does_not_relist_the_tracks(vm):
    """Every track is listed every time, so the second run has to collide."""
    _deliver(vm)
    _deliver(vm)

    # The two that carry an id collide by it — that is `_without_matched`, and
    # it is the only comparison a row with no file can answer to.
    assert vm.duplicatesSkipped == 2
    assert [r["title"] for r in vm._results].count("One More Time") == 1

    # The blocked one has no identity to be compared by, so it arrives again.
    # Not a defect of this source: every unmatched row in the importer repeats
    # the same way, and an unavailable row is unimportable either way.
    assert [r["title"] for r in vm._results].count("Digital Love") == 2


def test_an_album_adds_to_a_list_another_source_started(vm):
    """One list, gathered from wherever — a folder, a CSV, and now a record."""
    vm._set_download_mode(False)
    vm._on_search_finished([{
        "title": "Something local", "artist": "A", "album": "", "duration": 1,
        "source_id": "vidLocal", "cover_url": "", "cover_master_url": "",
        "source_path": "/music/a.flac", "release_date": None, "not_found": False,
    }])

    _deliver(vm)

    assert len(vm._results) == 4
    # The row that arrived first keeps its kind. `is_download` is settled per
    # row when it lands, never re-read off the run that is going on now.
    assert vm._results[0]["is_download"] is False
    assert all(r["is_download"] for r in vm._results[1:])


# ======================================================================
# What the run tells the album about itself
# ======================================================================
#
# "You have nine of these twelve" is the answer the button exists to give, and
# until now it was given once, in a toast, and forgotten. These pin the other
# half: when the answer is "you have all twelve", the record is told so.
#
# The decision itself belongs to `AlbumService.record_release_check` and is
# tested in tests/test_albums.py. What is particular here is the *evidence* this
# view model hands it, and the one way that evidence can go wrong.


class _StubAlbum:
    def __init__(self, album_id=5, name="Discovery"):
        self.id = album_id
        self.name = name


class _StubAlbumService:
    """Just enough of AlbumService for the completeness hook and the claim.

    ``on_album`` is which library tracks this record already holds — the only
    thing `split_owned` needs to know, and the fact the offer turns on.
    """

    def __init__(self, album=_StubAlbum(), on_album=()):
        self.album = album
        self.checks: list = []
        self.claims: list = []
        self.on_album = {int(t) for t in on_album}

    def get(self, album_id):
        return self.album

    def record_release_check(self, album_id, *, release_count, owned_track_ids):
        self.checks.append(
            (int(album_id), release_count, sorted(t for t in owned_track_ids if t))
        )
        return True

    def split_owned(self, album_id, owned_track_ids):
        held: list[int] = []
        missing: list[int] = []
        seen: set[int] = set()
        for value in owned_track_ids:
            if not value or int(value) in seen:
                continue
            seen.add(int(value))
            (held if int(value) in self.on_album else missing).append(int(value))
        return held, missing

    def claim_tracks(self, album_id, track_ids, release):
        self.claims.append((int(album_id), list(track_ids), list(release)))
        self.on_album |= {int(t) for t in track_ids}
        return len(track_ids)


#: The whole release, owned: two by id, and the blocked one by its recording.
OWNS_THE_WHOLE_RECORD = {
    "existing": {"vid1": 11, "vid2": 22},
    "recordings": {33: ("Digital Love", "Daft Punk", 200)},
}


def _album_vm(qt_app, albums, **library):
    return ImportViewModel(
        import_service=_StubImportService(**library),
        track_service=None,
        registry=_registry(),
        import_settings=_StubImports(),
        album_service=albums,
    )


def _analyze(vm, rows=RELEASE):
    """A run started the way `findAlbumTracks` starts one, minus the worker."""
    vm._album_id = 5
    _deliver(vm, rows)


def test_a_release_the_library_already_holds_is_reported_as_all_present(qt_app):
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)

    _analyze(vm)

    assert albums.checks == [(5, 3, [11, 22, 33])]


def test_a_release_with_a_track_missing_is_reported_honestly(qt_app):
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, existing={"vid1": 11})

    _analyze(vm)

    assert albums.checks == [(5, 3, [11])]


def test_pressing_find_tracks_twice_still_weighs_the_whole_release(qt_app):
    """The trap the raw snapshot exists for.

    `_without_matched` drops rows already on the list, so the second press
    merges almost nothing and the run's own counters collapse — here to one, the
    blocked track, which carries no id to collide on. The *release* is exactly as
    long as it ever was, and a check reading those counters would decide a
    complete record had shrunk to a single song.
    """
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)

    _analyze(vm)
    _analyze(vm)

    assert vm.lastMatchedCount == 1, "the fixture must reproduce the collision"
    assert albums.checks == [(5, 3, [11, 22, 33])] * 2


def test_a_browse_id_no_album_row_holds_writes_nothing(qt_app):
    """A link pasted into the import tab need not name an album in the library."""
    albums = _StubAlbumService(album=None)
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)

    _analyze(vm)

    assert albums.checks == []


def test_an_ordinary_import_run_asks_no_album_anything(qt_app):
    """Nothing but `findAlbumTracks` sets the album, and nothing else may check."""
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)

    _deliver(vm)          # no browse id: a folder scan, a CSV, a pasted link

    assert albums.checks == []


def test_the_check_runs_again_when_the_import_it_prompted_has_drained(qt_app):
    """The pass that ticks a record the user has just finished filling in.

    No second fetch: the listing is still held, and the tracks that arrived in
    between are what make the answer different.
    """
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, existing={"vid1": 11})
    _analyze(vm)
    assert albums.checks == [(5, 3, [11])]

    # The rest of the record arrives, and the queue runs dry.
    vm._import_service.existing.update({"vid2": 22})
    vm._import_service.recordings.update({33: ("Digital Love", "Daft Punk", 200)})
    vm._on_stash_stopped()

    assert albums.checks[-1] == (5, 3, [11, 22, 33])


def test_clearing_the_list_forgets_which_release_it_was_about(qt_app):
    albums = _StubAlbumService()
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    _analyze(vm)
    albums.checks.clear()

    vm.clearResults()
    vm._on_stash_stopped()

    assert albums.checks == []


# ======================================================================
# Claiming the tracks the release proved belong here
# ======================================================================
#
# The tracks `record_release_check` throws away: owned, named by this release,
# and filed on some other record — a single, or another pressing. The run has
# just proved they belong to this one, so the finding is offered rather than
# spent on marking a row "already in your library" and dropping it.
#
# What the write does is `AlbumService.claim_tracks`' business and is pinned in
# tests/test_albums.py. What is particular here is *when the question is asked*
# — which is once, whatever the run does afterwards.


def _offers(vm):
    """Every claim offer the run makes, as it reaches a GUI."""
    seen: list[tuple] = []
    vm.album_claim_offered.connect(
        lambda album_id, name, ids, titles: seen.append(
            (album_id, name, list(ids), list(titles))
        )
    )
    return seen


def test_a_release_this_album_already_holds_offers_nothing(qt_app):
    """Every owned track is on the record. There is nothing to move."""
    albums = _StubAlbumService(on_album=[11, 22, 33])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    offers = _offers(vm)

    _analyze(vm)

    assert offers == []


def test_an_owned_track_filed_elsewhere_is_offered_by_name(qt_app):
    """The whole feature, in the case it exists for.

    Two of the record's tracks are on it; the third is in the library under a
    different release. Titles come off the listing's own rows — the ids alone
    would leave the dialog counting tracks instead of naming them.
    """
    albums = _StubAlbumService(on_album=[11])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    offers = _offers(vm)

    _analyze(vm)

    assert offers == [(5, "Discovery", [22, 33], ["Aerodynamic", "Digital Love"])]


def test_the_second_pass_does_not_ask_again(qt_app):
    """`_evaluate_album_completeness` runs twice off one fetch; this asks once.

    The second pass lands after the import the listing prompted has drained —
    which is after the user has answered. Re-asking a declined question is worse
    than never having asked it.
    """
    albums = _StubAlbumService(on_album=[11])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    offers = _offers(vm)

    _analyze(vm)
    vm._on_stash_stopped()

    assert len(offers) == 1


def test_a_fresh_run_may_offer_again(qt_app):
    """The guard belongs to the run, not to the list — see `_reset_flow_state`."""
    albums = _StubAlbumService(on_album=[11])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    offers = _offers(vm)
    _analyze(vm)

    vm.clearResults()
    _analyze(vm)

    assert len(offers) == 2


def test_a_browse_id_no_album_row_holds_offers_nothing(qt_app):
    """A link pasted into the import tab need not name an album in the library."""
    albums = _StubAlbumService(album=None)
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    offers = _offers(vm)

    _analyze(vm)

    assert offers == []


def test_accepting_hands_the_service_the_listing_it_is_still_holding(qt_app):
    """Why the slot lives here: the reorder needs the release, and this has it.

    The album page navigated away when *Find tracks* was pressed, so nothing
    else could pass it on — and passing it costs no second fetch.
    """
    albums = _StubAlbumService(on_album=[11])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    _analyze(vm)

    assert vm.claimTracksForAlbum(5, [22, 33]) == 2

    album_id, claimed, release = albums.claims[0]
    assert (album_id, claimed) == (5, [22, 33])
    assert [row["title"] for row in release] == [r["title"] for r in RELEASE]


def test_a_claim_that_fails_says_so_rather_than_taking_the_tab_down(qt_app):
    """The same posture the completeness check takes: report it and carry on."""
    albums = _StubAlbumService(on_album=[11])
    vm = _album_vm(qt_app, albums, **OWNS_THE_WHOLE_RECORD)
    _analyze(vm)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    def _boom(*args, **kwargs):
        raise RuntimeError("the database went away")

    albums.claim_tracks = _boom

    assert vm.claimTracksForAlbum(5, [22]) == 0
    assert errors == ["Could not add those tracks to the album."]
