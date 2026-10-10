"""When two descriptions of a song are the same recording.

The rule that recognises a track the library downloaded from a *different
release* — which is the ordinary case, not an edge one: a catalogue's id names
an upload, and a catalogue mints one per release. Measured on a real 1951-track
library, eight of *Walkerworld*'s ten tracks are owned under the singles'
uploads and share no id with the album at all.

Every case here is a measurement rather than a guess. The rule was fitted to a
real library by counting how many pairs of *different* recordings each candidate
loosening would have declared identical, and the two that produced real errors
are pinned below by name. Loosen this module and those come back.
"""
from __future__ import annotations

import pytest

from app.library.recording_match import (
    DURATION_TOLERANCE_SECONDS,
    Recording,
    artist_names,
    best_match,
    duration_window,
    recording_of,
    same_recording,
    split_artists,
    title_key,
)


def _rec(title, artist, duration) -> Recording:
    return recording_of(title, artist, duration)


# ---------------------------------------------------------------------------
# The title
# ---------------------------------------------------------------------------

def test_a_credit_parenthetical_is_not_part_of_the_title():
    """Whether the credit is in the title or only in the artist field varies
    between releases of one recording, and says nothing about which it is."""
    assert title_key("Sorry (feat. ISÁK)") == title_key("Sorry")
    assert title_key("Burning Down (with Joe Jonas)") == title_key("Burning Down")
    assert title_key("Hero [ft. Someone]") == title_key("hero")


def test_a_version_suffix_is_part_of_the_title():
    """The measured reason not to strip every parenthetical.

    Stripping all of them matched *Barcelona* (206s) to *Barcelona (The Unborn
    Remix)* (207s) — two different recordings, one second apart, and nothing
    downstream would have caught it.
    """
    assert title_key("Barcelona") != title_key("Barcelona (The Unborn Remix)")
    assert title_key("Be With You Tonight") != title_key("Be With You Tonight (VIP Mix)")


def test_case_and_surrounding_space_are_not_part_of_the_title():
    assert title_key("  Land Of The Heroes ") == title_key("Land of the Heroes")


# ---------------------------------------------------------------------------
# The artists
# ---------------------------------------------------------------------------

def test_the_joined_credit_splits_on_commas_and_ampersands():
    assert split_artists("Alan Walker, Dash Berlin, & Vikkstar") == [
        "Alan Walker", "Dash Berlin", "Vikkstar",
    ]
    assert split_artists("Skrillex & Diplo") == ["Skrillex", "Diplo"]


def test_a_slash_is_never_a_separator():
    """It would tear "AC/DC" in half every time it was written."""
    assert split_artists("AC/DC") == ["AC/DC"]


def test_names_compare_casefolded():
    """Nothing normalises them on the way in — `get_or_create` matches exactly
    and case-sensitively, so a library may hold both spellings as two rows."""
    assert artist_names("Daft Punk") == artist_names("daft punk")


# ---------------------------------------------------------------------------
# The whole rule
# ---------------------------------------------------------------------------

def test_the_same_recording_from_two_releases_matches():
    assert same_recording(
        _rec("the cure", "Olivia Rodrigo", 298),
        _rec("the cure", "Olivia Rodrigo", 297),
    )


@pytest.mark.parametrize("delta", [0, 1, 2, -1, -2])
def test_a_difference_inside_the_tolerance_is_one_recording(delta):
    """A library's duration comes from the file it transcoded; the service's
    comes from the release. They disagree by a second often enough to matter."""
    assert same_recording(
        _rec("Darkride", "Alan Walker", 113),
        _rec("Darkride", "Alan Walker", 113 + delta),
    )


@pytest.mark.parametrize("delta", [3, -3, 40])
def test_a_difference_outside_it_is_not(delta):
    assert not same_recording(
        _rec("Darkride", "Alan Walker", 113),
        _rec("Darkride", "Alan Walker", 113 + delta),
    )


def test_the_tolerance_has_not_moved():
    """Named, because widening it is the tempting fix for the next miss — and at
    ±3 the collisions on a real library start being remixes and edits."""
    assert DURATION_TOLERANCE_SECONDS == 2


def test_another_artists_song_of_the_same_name_is_not_the_same_recording():
    """The measured false positive the artist test exists to prevent.

    Faouzia — *Hero* is 175s; Alan Walker — *Hero* is 176s. Without this the
    import list hides a track the user came to download.
    """
    assert not same_recording(
        _rec("Hero", "Alan Walker, Sasha Alex Sloan", 176),
        _rec("Hero", "Faouzia", 175),
    )


def test_one_shared_name_is_enough():
    """Credit lists differ between releases — a remixer added, a feature
    dropped — without the recording changing."""
    assert same_recording(
        _rec("Paradise", "K-391, Boy In Space, Alan Walker", 184),
        _rec("Paradise", "Alan Walker", 184),
    )


@pytest.mark.parametrize("missing", [
    _rec("", "Daft Punk", 200),
    _rec("One More Time", "", 200),
    _rec("One More Time", "Daft Punk", 0),
    _rec("One More Time", "Daft Punk", None),
])
def test_a_missing_field_matches_nothing(missing):
    """Failing closed, rather than treating "unknown" as a wildcard.

    The other way round, one untimed row would match every other untimed row the
    moment a source stopped supplying a field — and nothing would report it.
    """
    complete = _rec("One More Time", "Daft Punk", 200)
    assert not same_recording(missing, complete)
    assert not same_recording(complete, missing)


# ---------------------------------------------------------------------------
# Asking the database as few times as possible
# ---------------------------------------------------------------------------

def test_the_duration_window_is_the_lengths_worth_asking_about():
    assert list(duration_window(200)) == [198, 199, 200, 201, 202]


def test_an_unknown_duration_asks_about_nothing():
    """It could not match anyway, and a wildcard here would drag the whole
    library into the candidate set."""
    assert list(duration_window(0)) == []
    assert list(duration_window(-5)) == []


def test_the_window_never_reaches_below_one_second():
    assert list(duration_window(1)) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Choosing among candidates
# ---------------------------------------------------------------------------

def test_the_one_matching_candidate_is_returned():
    library = {
        4: _rec("Aerodynamic", "Daft Punk", 212),
        7: _rec("One More Time", "Daft Punk", 320),
    }
    assert best_match(_rec("One More Time", "Daft Punk", 321), library) == 7


def test_nothing_matching_is_none():
    library = {4: _rec("Aerodynamic", "Daft Punk", 212)}
    assert best_match(_rec("Digital Love", "Daft Punk", 301), library) is None


def test_a_recording_owned_twice_answers_with_the_first_of_them():
    """The one comparison here that resolves an ambiguity rather than refusing.

    Everywhere else a tie is dropped, because the cost of guessing is a wrong
    answer written down. Here every candidate is a track the library already
    holds, so any of them is a true answer to "do you have this?" — and saying
    "no" over an embarrassment of matches would be the only false one available.

    Lowest id, so the same question asked twice gets the same track.
    """
    library = {
        9: _rec("Heading Home", "Alan Walker, Ruben", 187),
        3: _rec("Heading Home", "Alan Walker, Ruben", 185),
    }
    assert best_match(_rec("Heading Home", "Alan Walker", 186), library) == 3


# ---------------------------------------------------------------------------
# Reading whatever a caller has
# ---------------------------------------------------------------------------

def test_a_description_is_normalised_however_its_fields_arrive():
    """Callers are a search result, a database row and a QML metadata map, and
    not all of them are Python-typed."""
    assert recording_of("Hero ", "Alan Walker", "176") == recording_of(
        "hero", "alan walker", 176
    )


def test_an_unreadable_duration_becomes_no_duration_rather_than_raising():
    assert recording_of("Hero", "Alan Walker", "about four minutes").duration == 0
    assert recording_of(None, None, None) == Recording("", 0, frozenset())
