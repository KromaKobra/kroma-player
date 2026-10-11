"""The lyrics editor's rows, driven through the real QML.

`tests/test_lyrics_edit.py` covers what the view model does with a finished
draft, and `tests/test_lyrics_editing.py` covers the arithmetic under it. What
neither reaches is the part in between: the draft itself lives in a QML
`ListModel` in `gui/nocturne/views/playback/NowPlayingView.qml`, deliberately, because a
per-keystroke conversation with the backend would rewrite the list the editor's
own text fields are reading and Qt drops a field's binding the moment somebody
types into it.

So the row operations — split a paste into rows, insert beneath, move, re-sort
on a typed timecode — are QML functions, and this is where they are pinned.
`tests/test_gui_packages.py` proves the file loads; it cannot prove that
pasting three lines produces three rows.

Two things about the harness are load-bearing:

**The pane must be genuinely visible.** It lives inside
`visible: playerVM.has_current_track`, and an invisible `ListView` builds no
delegates — so the whole editor would test as an empty list. The player is given
a real track for that reason, not for the metadata.

**The window must be wide.** The lyrics column only exists above 900px
(`root.wide`), which is a real product decision and not a detail to work around.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HARNESS = """
import QtQuick
import Nocturne
import "views/playback"

Window {
    width: 1600
    height: 900
    visible: true
    NowPlayingView {
        objectName: "npv"
        anchors.fill: parent
        active: true
    }
}
"""


def _call(obj, name, *args):
    """Invoke a QML-declared function. They are not Python attributes."""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    return QMetaObject.invokeMethod(
        obj, name, Qt.ConnectionType.DirectConnection,
        *[Q_ARG("QVariant", value) for value in args],
    )


@pytest.fixture
def editor(qt_app, tmp_path_factory):
    """The real Now Playing view, over a throwaway vault, with a track playing."""
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.models.track import Track
    from app.utils.library_paths import set_library_root

    QQuickStyle.setStyle("Fusion")
    vault = tmp_path_factory.mktemp("vault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    _register_qml_types(registry)

    warnings: list[str] = []
    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda errors: warnings.extend(e.toString() for e in errors)
    )
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))

    # The harness has to sit inside the package so `import "views/playback"` resolves.
    harness = directory / "__LyricsEditorHarness.qml"
    harness.write_text(HARNESS)
    try:
        engine.load(QUrl.fromLocalFile(str(harness)))
        roots = engine.rootObjects()
        assert roots, f"the harness failed to load: {warnings}"
        window = roots[0]

        track_service = registry.get_service("track_service")
        track_id = track_service.create_track_with_relationships(
            {"title": "A track", "artists": "An artist"}
        )
        player = registry.get_service("player_view_model")
        player._current_track = track_service.track_repo.session.get(Track, track_id)
        player.current_track_changed.emit()

        view = window.findChild(QObject, "npv")
        model = window.findChild(QObject, "lyricsDraft")
        assert view is not None and model is not None

        yield _Editor(
            view, model, registry.get_service("lyrics_view_model"),
            registry.get_service("lyrics_service"), track_id, qt_app, warnings,
        )
    finally:
        harness.unlink(missing_ok=True)
        del engine


class _Editor:
    """The handful of moves a test makes, and a way to read the draft back."""

    def __init__(self, view, model, lyric_vm, lyrics_service, track_id, app, warnings):
        self.view = view
        self.model = model
        self.lyric_vm = lyric_vm
        self.lyrics_service = lyrics_service
        self.track_id = track_id
        self.app = app
        self.warnings = warnings

    def open_with(self, document: str | None):
        if document is not None:
            self.lyrics_service.save(
                self.track_id, document, "search", write_tag=False,
            )
        self.lyric_vm._load()
        self.settle()
        _call(self.view, "beginEdit")
        self.settle()

    def settle(self, ticks: int = 10):
        for _ in range(ticks):
            self.app.processEvents()

    def _column(self, name) -> list[str]:
        """Read one of the draft's columns back.

        Through the view's own accessors rather than by walking the model:
        `ListModel.get()` returns a `QJSValue`, which does not come back
        through `invokeMethod` at all, and `draftTimes()`/`draftWords()` are
        what the save path itself uses.
        """
        from PySide6.QtCore import Q_RETURN_ARG, QMetaObject, Qt

        values = QMetaObject.invokeMethod(
            self.view, name, Qt.ConnectionType.DirectConnection,
            Q_RETURN_ARG("QVariant"),
        )
        # A QML function hands back a JS array, which arrives wrapped.
        if hasattr(values, "toVariant"):
            values = values.toVariant()
        return [str(value) for value in values]

    @property
    def times(self) -> list[str]:
        return self._column("draftTimes")

    @property
    def words(self) -> list[str]:
        return self._column("draftWords")

    @property
    def rows(self) -> list[tuple[str, str]]:
        return list(zip(self.times, self.words))

    def call(self, name, *args):
        result = _call(self.view, name, *args)
        self.settle()
        return result

    def drag(self, from_index, to_index):
        """Move a row the way the grip does — the model first, then the retime.

        `ListModel.move()` is invoked with real ints rather than QVariants;
        it is a C++ method with a typed signature, and a QVariant argument
        matches nothing and fails silently.
        """
        from PySide6.QtCore import Q_ARG, QMetaObject, Qt

        assert QMetaObject.invokeMethod(
            self.model, "move", Qt.ConnectionType.DirectConnection,
            Q_ARG(int, from_index), Q_ARG(int, to_index), Q_ARG(int, 1),
        )
        self.call("retimeMoved", to_index)

    def type_time(self, index, text):
        """Type a timecode and commit it, as leaving the field does."""
        from PySide6.QtCore import Q_ARG, QMetaObject, Qt

        assert QMetaObject.invokeMethod(
            self.model, "setProperty", Qt.ConnectionType.DirectConnection,
            Q_ARG(int, index), Q_ARG(str, "time"), Q_ARG("QVariant", text),
        )
        self.call("resortLine", index)


SYNCED = "[00:10.00]one\n[00:20.00]two"


# ==================================================================
# Opening the editor
# ==================================================================


def test_a_synced_document_opens_as_rows_of_timecode_and_words(editor):
    editor.open_with(SYNCED)

    assert editor.rows == [("00:10.00", "one"), ("00:20.00", "two")]


def test_a_track_with_no_lyrics_opens_as_one_blank_row(editor):
    """The editor is also how lyrics get written from nothing, and an empty
    list has no row to type into and no plus button to press."""
    editor.open_with(None)

    assert editor.rows == [("", "")]


# ==================================================================
# Pasting
# ==================================================================


def test_a_pasted_block_becomes_one_row_per_line(editor):
    """The whole point. A single-line field takes newlines quite happily and
    then renders them as one unreadable run, which is what this replaces."""
    editor.open_with(None)

    editor.call("splitPaste", 0, "", "", ["verse one", "verse two", "verse three"])

    assert editor.words == ["verse one", "verse two", "verse three"]


def test_a_paste_into_an_empty_document_leaves_it_unsynced(editor):
    """There are no timed neighbours to interpolate between, and inventing
    timecodes would make `is_synced()` start answering yes to numbers nobody
    chose. Pasting a sheet then retiming it is the intended pair."""
    editor.open_with(None)

    editor.call("splitPaste", 0, "", "", ["one", "two", "three"])

    assert editor.times == ["", "", ""]


def test_a_paste_between_two_timed_rows_spreads_across_the_gap(editor):
    editor.open_with(SYNCED)

    editor.call("splitPaste", 0, "", "", ["one", "a", "b", "c"])

    assert editor.times == ["00:10.00", "00:12.50", "00:15.00", "00:17.50", "00:20.00"]
    assert editor.words == ["one", "a", "b", "c", "two"]


def test_a_paste_splits_at_the_cursor(editor):
    """What every text editor does with a paste, spread over rows instead of
    over lines: what was before the cursor keeps the row, what was after it
    rides on the last new one."""
    editor.open_with(SYNCED)

    editor.call("splitPaste", 0, "before ", " after", ["X", "Y"])

    assert editor.words == ["before X", "Y after", "two"]


def test_a_single_line_paste_stays_in_its_row(editor):
    editor.open_with(SYNCED)

    editor.call("splitPaste", 0, "before ", " after", ["X"])

    assert editor.words == ["before X after", "two"]


# ==================================================================
# Inserting and removing
# ==================================================================


def test_the_plus_button_inserts_beneath_with_the_midpoint_time(editor):
    editor.open_with(SYNCED)

    editor.call("insertLines", 1, [""])

    assert editor.rows == [
        ("00:10.00", "one"), ("00:15.00", ""), ("00:20.00", "two"),
    ]


def test_inserting_at_the_end_lands_one_second_after_the_last(editor):
    editor.open_with(SYNCED)

    editor.call("insertLines", 2, [""])

    assert editor.times == ["00:10.00", "00:20.00", "00:21.00"]


def test_removing_the_last_row_leaves_a_blank_one(editor):
    """An editor with no rows has no way back to having one. Clearing that
    row's text and saving is still how lyrics get deleted."""
    editor.open_with("[00:10.00]only")

    editor.call("removeLine", 0)

    assert editor.rows == [("", "")]


# ==================================================================
# Reordering
# ==================================================================


def test_a_moved_row_is_re_timed_to_sit_between_its_new_neighbours(editor):
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")

    editor.drag(2, 0)

    assert editor.words == ["three", "one", "two"]
    # At the top, so the start of the track — not a midpoint of nothing.
    assert editor.times == ["00:00.00", "00:10.00", "00:20.00"]


def test_a_row_moved_to_the_end_lands_one_second_after_the_last(editor):
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")

    editor.drag(0, 2)

    assert editor.words == ["two", "three", "one"]
    assert editor.times == ["00:20.00", "00:30.00", "00:31.00"]


def test_only_the_moved_row_is_re_timed(editor):
    """Everything else keeps the timing it was given, because a drag is a
    statement about one line."""
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")

    editor.drag(0, 1)

    assert editor.times == ["00:20.00", "00:25.00", "00:30.00"]


def test_reordering_an_unsynced_document_invents_no_timings(editor):
    editor.open_with("one\ntwo\nthree")

    editor.drag(2, 0)

    assert editor.words == ["three", "one", "two"]
    assert editor.times == ["", "", ""]


# ==================================================================
# Re-sorting on a typed timecode
# ==================================================================


def test_a_retyped_timecode_moves_its_row_into_place(editor):
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")

    editor.type_time(2, "00:05.00")

    assert editor.words == ["three", "one", "two"]
    assert editor.times == ["00:05.00", "00:10.00", "00:20.00"]


def test_a_timecode_that_is_already_in_order_moves_nothing(editor):
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")

    editor.type_time(1, "00:15.00")

    assert editor.words == ["one", "two", "three"]


def test_a_half_typed_timecode_leaves_the_row_where_it_is(editor):
    """`0` on its way to `01:30` must not fling the row out from under the
    cursor. The re-sort is on commit for that reason, and even then a value
    that is not a time yet is not an instruction."""
    editor.open_with("[00:10.00]one\n[00:20.00]two")

    editor.type_time(1, "0:")

    assert editor.words == ["one", "two"]


# ==================================================================
# Nothing above emits a QML warning
# ==================================================================


def test_the_editor_runs_without_a_single_qml_warning(editor):
    """A binding loop or a missing property loads clean and behaves wrong, so
    the warnings are collected across every operation above and asserted here
    the way `tests/test_gui_packages.py` does for the load."""
    editor.open_with(SYNCED)
    editor.call("insertLines", 1, ["x"])
    editor.call("splitPaste", 0, "", "", ["a", "b"])
    editor.drag(0, 2)
    editor.call("resortLine", 2)
    editor.call("removeLine", 0)

    assert editor.warnings == []


# ==================================================================
# The drag gesture's own arithmetic
# ==================================================================


def test_a_drag_moves_the_row_as_the_pointer_crosses_each_boundary(editor):
    """The model is moved live, so the list shows where the row would land
    rather than describing it — the rule ArtistChips already follows. Only the
    timecode waits for the release."""
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")
    editor.view.setProperty("dragRow", 0)

    # Just under one row of travel: nothing has crossed yet.
    editor.call("dragTo", 20.0)
    assert editor.words == ["one", "two", "three"]

    # Past the midpoint of the next row.
    editor.call("dragTo", 50.0)
    assert editor.words == ["two", "one", "three"]

    editor.call("endDrag", True)
    assert editor.times == ["00:20.00", "00:25.00", "00:30.00"]


def test_a_cancelled_drag_puts_the_row_back(editor):
    """`onCanceled` is an explicit revert, not a no-op — the row has already
    been moved in the model by the time the gesture is taken away."""
    editor.open_with("[00:10.00]one\n[00:20.00]two\n[00:30.00]three")
    editor.view.setProperty("dragRow", 0)
    editor.call("dragTo", 100.0)
    assert editor.words == ["two", "three", "one"]

    editor.call("endDrag", False)

    assert editor.rows == [
        ("00:10.00", "one"), ("00:20.00", "two"), ("00:30.00", "three"),
    ]


def test_a_drag_cannot_be_pulled_off_either_end_of_the_list(editor):
    editor.open_with("[00:10.00]one\n[00:20.00]two")
    editor.view.setProperty("dragRow", 0)

    editor.call("dragTo", 5000.0)
    assert editor.words == ["two", "one"]

    editor.call("dragTo", -5000.0)
    assert editor.words == ["one", "two"]
