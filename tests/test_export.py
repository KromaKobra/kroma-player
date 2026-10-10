"""Exporting library audio back out to a folder.

Most of what can go wrong here goes wrong in filenames, so that is where the
weight of these tests sits. The engine tests use dummy bytes and monkeypatch the
retag step out: a valid Opus file cannot be synthesised in a line of Python, and
what the copy path is actually promising is "the bytes arrive unchanged", which
does not need one.

The one test that needs ffmpeg is skipped when it is missing, so the suite stays
runnable on a checkout that has not installed it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.exporter import export as export_mod  # noqa: E402
from app.exporter import naming  # noqa: E402
from app.exporter.export import (  # noqa: E402
    ExportCancelled,
    ExportError,
    ExportItem,
    ExportRequest,
    export_tracks,
)
from app.exporter.formats import FORMAT_MP3, FORMAT_OPUS  # noqa: E402
from app.utils.binaries import ffmpeg_path  # noqa: E402
from app.utils.library_paths import track_path  # noqa: E402


# ============================================================== naming


@pytest.mark.parametrize("raw, expected", [
    ("AC/DC", "AC_DC"),
    ("Hells Bells: Live?", "Hells Bells_ Live_"),
    ("Track .", "Track"),                       # Windows drops these silently
    ("  spaced   out  ", "spaced out"),
    ("CON", "CON_"),                            # reserved DOS device name
    ("con", "con_"),                            # …case-insensitively
    ("", "Untitled"),
    ("???", "___"),
])
def test_sanitize_component(raw, expected):
    assert naming.sanitize_component(raw) == expected


def test_control_characters_are_replaced():
    assert "\n" not in naming.sanitize_component("Two\nLines")


def test_long_titles_are_truncated():
    name = naming.track_filename("x" * 400, "Someone", ".mp3")
    assert len(Path(name).stem) <= naming.MAX_STEM


def test_track_filename_joins_artist_and_title():
    assert naming.track_filename("Archangel", "Burial", ".opus") == "Burial - Archangel.opus"


def test_track_filename_survives_a_missing_field():
    assert naming.track_filename("Archangel", "", ".mp3") == "Archangel.mp3"
    assert naming.track_filename("", "", ".mp3") == "Untitled.mp3"


def test_collisions_get_numbered(tmp_path):
    allocator = naming.NameAllocator(tmp_path)
    assert allocator.allocate("Song.opus").name == "Song.opus"
    assert allocator.allocate("Song.opus").name == "Song (2).opus"
    assert allocator.allocate("Song.opus").name == "Song (3).opus"


def test_files_already_on_disk_are_not_overwritten(tmp_path):
    (tmp_path / "Song.opus").write_bytes(b"old")
    (tmp_path / "Song (2).opus").write_bytes(b"older")

    allocator = naming.NameAllocator(tmp_path)
    assert allocator.allocate("Song.opus").name == "Song (3).opus"


def test_case_only_collisions_do_not_collapse(tmp_path):
    """NTFS and APFS treat these as one file; the allocator must too."""
    allocator = naming.NameAllocator(tmp_path)
    first = allocator.allocate("Faith.opus").name
    second = allocator.allocate("FAITH.opus").name
    assert first.casefold() != second.casefold()


def test_folder_allocation_keeps_dots_in_the_name(tmp_path):
    """A playlist called "Vol. 2" is not a file with a ".2" extension."""
    (tmp_path / "Vol. 2").mkdir()
    allocator = naming.NameAllocator(tmp_path)
    assert allocator.allocate_folder("Vol. 2").name == "Vol. 2 (2)"


# ============================================================== engine


def _seed(track_id: int, data: bytes = b"opus-bytes") -> Path:
    """Put dummy audio where the library would keep track *track_id*."""
    path = track_path(track_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _item(track_id: int, title: str = "Archangel", artist: str = "Burial",
          lyrics: str = "") -> ExportItem:
    return ExportItem(track_id=track_id, title=title, artist=artist, album="Untrue",
                      lyrics=lyrics)


@pytest.fixture
def no_retag(monkeypatch):
    """Skip the tag rewrite, which needs a real Opus container."""
    monkeypatch.setattr(export_mod, "_tag_opus", lambda item, path: None)


def _request(tmp_path, items, fmt=FORMAT_OPUS, name="Late Night Drive"):
    return ExportRequest(items=tuple(items), destination=tmp_path,
                         playlist_name=name, fmt=fmt)


def test_opus_export_copies_bytes_verbatim(tmp_path, no_retag):
    _seed(1, b"the-original-bytes")
    outcome = export_tracks(_request(tmp_path, [_item(1)]))

    assert outcome.exported == 1
    written = tmp_path / "Late Night Drive" / "Burial - Archangel.opus"
    assert written.read_bytes() == b"the-original-bytes"


def test_export_creates_a_folder_named_after_the_playlist(tmp_path, no_retag):
    _seed(1)
    outcome = export_tracks(_request(tmp_path, [_item(1)]))
    assert outcome.destination == tmp_path / "Late Night Drive"


def test_re_exporting_does_not_overwrite_the_first_run(tmp_path, no_retag):
    _seed(1)
    export_tracks(_request(tmp_path, [_item(1)]))
    second = export_tracks(_request(tmp_path, [_item(1)]))

    assert second.destination == tmp_path / "Late Night Drive (2)"
    assert (tmp_path / "Late Night Drive" / "Burial - Archangel.opus").exists()


def test_missing_source_is_skipped_not_fatal(tmp_path, no_retag):
    _seed(1)
    # Track 2 has a database row but no file on disk.
    outcome = export_tracks(_request(tmp_path, [_item(1), _item(2, title="Ghost")]))

    assert outcome.exported == 1
    assert [tid for tid, _ in outcome.skipped] == [2]
    assert (outcome.destination / "Burial - Archangel.opus").exists()


def test_no_part_files_survive_a_successful_run(tmp_path, no_retag):
    _seed(1)
    outcome = export_tracks(_request(tmp_path, [_item(1)]))
    assert list(outcome.destination.glob("*.part")) == []


def test_cancel_stops_between_tracks(tmp_path, no_retag):
    for track_id in (1, 2, 3):
        _seed(track_id)
    items = [_item(1, title="One"), _item(2, title="Two"), _item(3, title="Three")]

    calls = {"n": 0}

    def should_cancel() -> bool:
        # False for the first check, True from the second onwards.
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(ExportCancelled):
        export_tracks(_request(tmp_path, items), should_cancel=should_cancel)

    folder = tmp_path / "Late Night Drive"
    assert len(list(folder.glob("*.opus"))) == 1
    assert list(folder.glob("*.part")) == []


def test_progress_is_reported_per_track(tmp_path, no_retag):
    for track_id in (1, 2):
        _seed(track_id)
    seen: list[tuple[int, int]] = []

    export_tracks(
        _request(tmp_path, [_item(1, title="One"), _item(2, title="Two")]),
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_repeated_failures_abort_the_run(tmp_path, monkeypatch):
    for track_id in range(1, 6):
        _seed(track_id)
    monkeypatch.setattr(
        export_mod, "_tag_opus",
        lambda item, path: (_ for _ in ()).throw(OSError("No space left on device")),
    )

    items = [_item(i, title=f"Track {i}") for i in range(1, 6)]
    with pytest.raises(ExportError, match="No space left"):
        export_tracks(_request(tmp_path, items))


# ============================================================ preflight


def test_empty_playlist_is_refused(tmp_path):
    with pytest.raises(ExportError, match="no tracks"):
        export_tracks(_request(tmp_path, []))


def test_missing_destination_is_refused(tmp_path):
    with pytest.raises(ExportError, match="No such folder"):
        export_tracks(_request(tmp_path / "nope", [_item(1)]))


@pytest.mark.parametrize("blank", ["", "."])
def test_an_unset_destination_never_becomes_the_working_directory(blank, no_retag):
    """``Path("")`` collapses to ``Path(".")``, which exists and is writable.

    Without an explicit guard, an export with no folder chosen would silently
    write into whatever directory the app happens to be running from.
    """
    _seed(1)
    before = set(Path.cwd().iterdir())

    with pytest.raises(ExportError, match="Choose a folder"):
        export_tracks(_request(Path(blank), [_item(1)]))

    assert set(Path.cwd().iterdir()) == before


def test_mp3_without_ffmpeg_fails_preflight(tmp_path, monkeypatch):
    _seed(1)
    monkeypatch.setattr(export_mod, "ffmpeg_path", lambda: None)

    # Its own directory: the autouse vault fixture lives under tmp_path too, so
    # "nothing was created" is only checkable somewhere the fixture is not.
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ExportError, match="ffmpeg"):
        export_tracks(_request(destination, [_item(1)], fmt=FORMAT_MP3))

    # Refused before anything was created.
    assert list(destination.iterdir()) == []


# ================================================================= mp3


@pytest.mark.skipif(ffmpeg_path() is None, reason="needs ffmpeg on PATH")
def test_mp3_export_round_trips_its_tags(tmp_path):
    """The only test that runs the real transcode + ID3 path end to end."""
    import subprocess

    from mutagen.id3 import ID3

    source = track_path(1)
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1",
         "-c:a", "libopus", str(source)],
        check=True, capture_output=True,
    )

    outcome = export_tracks(_request(tmp_path, [_item(1)], fmt=FORMAT_MP3))
    assert outcome.exported == 1

    written = outcome.destination / "Burial - Archangel.mp3"
    assert written.is_file()

    tags = ID3(str(written))
    assert tags["TIT2"].text == ["Archangel"]
    assert tags["TPE1"].text == ["Burial"]
    assert tags["TALB"].text == ["Untrue"]


# ================================================================ lyrics
#
# Lyrics are re-applied from the database, like the title, artist and album, and
# for the reason at the top of app/exporter/export.py: the database is what gets
# written, so a copy is right even if the source file's tag had drifted. Both
# formats, because ffmpeg drops metadata on the MP3 transcode — an MP3 quietly
# being the one format that loses them is exactly the kind of gap nobody notices
# until a file is on a phone.

LRC = "[00:12.00]The first line\n[00:15.30]The second line"


def _encode_silence(path: Path) -> None:
    """A second of real Opus, so the tag writes have a container to write into."""
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1",
         "-c:a", "libopus", str(path)],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(ffmpeg_path() is None, reason="needs ffmpeg on PATH")
def test_lyrics_are_written_onto_the_opus_copy(tmp_path):
    from mutagen.oggopus import OggOpus

    _encode_silence(track_path(1))

    outcome = export_tracks(_request(tmp_path, [_item(1, lyrics=LRC)]))
    assert outcome.exported == 1

    tags = OggOpus(str(outcome.destination / "Burial - Archangel.opus")).tags
    # Timestamps intact: an LRC document is written verbatim, not flattened.
    assert tags["LYRICS"] == [LRC]
    # And the rest of the retag still happened.
    assert tags["TITLE"] == ["Archangel"]


@pytest.mark.skipif(ffmpeg_path() is None, reason="needs ffmpeg on PATH")
def test_a_track_without_lyrics_gets_no_empty_tag(tmp_path):
    """An empty value *removes* a tag, and the copied file carries the source's."""
    from mutagen.oggopus import OggOpus

    from app.importer.tagging import update_opus_tags

    source = track_path(1)
    _encode_silence(source)
    update_opus_tags(source, {"LYRICS": "words the file already carried"})

    outcome = export_tracks(_request(tmp_path, [_item(1)]))

    tags = OggOpus(str(outcome.destination / "Burial - Archangel.opus")).tags
    assert tags["LYRICS"] == ["words the file already carried"]


@pytest.mark.skipif(ffmpeg_path() is None, reason="needs ffmpeg on PATH")
def test_lyrics_are_written_onto_the_mp3_copy(tmp_path):
    from mutagen.id3 import ID3

    _encode_silence(track_path(1))

    outcome = export_tracks(
        _request(tmp_path, [_item(1, lyrics=LRC)], fmt=FORMAT_MP3)
    )
    assert outcome.exported == 1

    tags = ID3(str(outcome.destination / "Burial - Archangel.mp3"))
    frames = tags.getall("USLT")
    assert len(frames) == 1, "one USLT frame, not one per language/description key"
    assert frames[0].text == LRC
