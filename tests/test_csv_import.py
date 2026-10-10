"""Reading a CSV: the generic columns are the core's, the id columns a plugin's.

`parse_csv()` turns a spreadsheet into `ProbedFile` candidates. Title, artists,
album and the rest are columns every CSV can have, and the core knows their
names. An id column is different: what it is called and what a cell in it looks
like belong to the plugin that declares the kind, so the core reads one only
when a declaration hands it over — and reads the cell with that kind's own
parse rules, so a link and a bare id both work.
"""
from __future__ import annotations

import pytest

from app.importer.csv_import import CsvColumnsMissingError, parse_csv
from tests.fakes.providers import KINDS, VIDEO

VIDEO_ID = "Xq7Lm2Pz9Ka"


def _csv(tmp_path, text: str):
    path = tmp_path / "list.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_generic_columns_need_no_plugin(tmp_path):
    rows = parse_csv(_csv(tmp_path, "Title,Artist,Album,BPM\nOne,Someone,Record,128\n"))

    assert len(rows) == 1
    row = rows[0]
    assert (row.title, row.artist, row.album, row.bpm) == ("One", "Someone", "Record", 128)
    assert row.path is None and row.from_csv


def test_an_id_column_is_read_through_the_declaring_kind(tmp_path):
    path = _csv(tmp_path, (
        "title,fake_id\n"
        f"Bare,{VIDEO_ID}\n"
        f"Linked,https://fake.example/watch?v={VIDEO_ID}&t=3\n"
    ))

    rows = parse_csv(path, KINDS)

    assert [r.external_ids for r in rows] == [{VIDEO: VIDEO_ID}, {VIDEO: VIDEO_ID}]


def test_with_no_plugin_an_id_column_is_just_an_unknown_column(tmp_path):
    rows = parse_csv(_csv(tmp_path, f"title,fake_id\nOne,{VIDEO_ID}\n"))

    assert rows[0].external_ids == {}


def test_a_cell_the_kind_cannot_read_is_left_out_rather_than_kept(tmp_path):
    rows = parse_csv(_csv(tmp_path, "title,fake_id\nOne,not an id\n"), KINDS)

    assert rows[0].external_ids == {}


def test_an_id_column_alone_is_enough_to_identify_by(tmp_path):
    rows = parse_csv(_csv(tmp_path, f"fid\n{VIDEO_ID}\n"), KINDS)

    assert [r.external_ids for r in rows] == [{VIDEO: VIDEO_ID}]


def test_the_missing_columns_message_names_what_would_do(tmp_path):
    path = _csv(tmp_path, "colour,size\nred,large\n")

    with pytest.raises(CsvColumnsMissingError) as plain:
        parse_csv(path)
    assert "a title or artist column" in str(plain.value)

    with pytest.raises(CsvColumnsMissingError) as declared:
        parse_csv(path, KINDS)
    assert "Fake id or link" in str(declared.value), "the plugin's own label for its column"


def test_a_blank_row_is_skipped(tmp_path):
    rows = parse_csv(_csv(tmp_path, "title,artist\nOne,Someone\n,\n"))

    assert [r.title for r in rows] == ["One"]
