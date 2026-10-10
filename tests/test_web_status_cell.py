"""A downloaded web result has to say so.

The status cell serves two lists. An import row is classified by the backend and
arrives with an `importState`; a web result has no such role — SearchViewModel
serves a different list — so the one question a web row answers, *is this
already in the library*, is worked out in the cell from `trackId`.

That makes the ordering of the tests inside `stateIcon`, `stateColor` and
`stateTooltip` load-bearing: ask `isWeb` first and a finished download keeps the
download glyph forever, because `isWeb` never stops being true. This pins the
finished web row to the same tick every other list uses.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Property, QObject, QUrl, Signal
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root


class _SearchStub(QObject):
    """Just enough of SearchViewModel for the web branch's bindings."""

    changed = Signal()

    @Property(int, notify=changed)
    def downloadingUid(self) -> int:
        return -1

    @Property(bool, notify=changed)
    def isDownloading(self) -> bool:
        return False


def _cell(qt_app, tmp_path, *, track_id, uid=7):
    nocturne = bundled_gui_root() / "nocturne"
    source = tmp_path / "Probe.qml"
    source.write_text(
        """
        import QtQuick
        import Nocturne
        import "file:///%s"

        Item {
            property alias icon: cell.stateIcon
            property alias tint: cell.stateColor
            property alias tip: cell.stateTooltip
            property alias done: cell.inLibrary
            property color positive: Theme.positive
            TrackStatusCell {
                id: cell
                statusMode: "web"
                trackId: %s
                uid: %s
            }
        }
        """
        % ((nocturne / "components").as_posix(), track_id, uid),
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(nocturne))
    engine.rootContext().setContextProperty("importVM", None)
    stub = _SearchStub()
    engine.rootContext().setContextProperty("searchVM", stub)
    warnings: list = []
    engine.warnings.connect(warnings.extend)
    engine.load(QUrl.fromLocalFile(str(source)))

    roots = engine.rootObjects()
    assert roots, "probe failed to load:\n" + "\n".join(w.toString() for w in warnings)
    qt_app.processEvents()
    # The engine and the stub both have to outlive the reads.
    return roots[0], engine, stub


def test_a_downloaded_web_row_wears_the_library_tick(qt_app, tmp_path):
    cell, engine, _stub = _cell(qt_app, tmp_path, track_id="41")
    try:
        assert cell.property("done") is True
        assert cell.property("icon") == "check", (
            "a web row already in the library still showed the download glyph"
        )
        assert cell.property("tint") == cell.property("positive")
        assert cell.property("tip") == "Already in your library"
    finally:
        del engine


@pytest.mark.parametrize("track_id", ["null", "0", "-1"])
def test_a_web_row_not_in_the_library_still_offers_the_download(
    qt_app, tmp_path, track_id
):
    """The other half of the ordering: nothing else may be swallowed by it."""
    cell, engine, _stub = _cell(qt_app, tmp_path, track_id=track_id)
    try:
        assert cell.property("done") is False
        assert cell.property("icon") == "download"
        assert cell.property("tip") == "Download to library"
    finally:
        del engine
