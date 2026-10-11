"""Guard against per-row cover mask layers coming back.

`CoverArt` rounds its corners with a `MultiEffect` whose `maskSource` is a
rounded rectangle rendered into its own layer texture. Given one of those per
row, a scrolling list creates and destroys layer textures constantly, and some
of them are not ready when the effect first draws — the effect renders an empty
mask and never repaints, so that row shows no artwork until the delegate is
rebuilt. It is a different handful of rows every time, which is what made the
bug look random rather than structural.

Measured against a 288-track library, scrolling the full list and comparing
each rendered cell against the decoded artwork:

    mask per row     3-9 blank cells out of 279   (never zero)
    one shared mask  0 blank cells out of 279     (four consecutive runs)

These are source checks rather than render checks: reproducing it needs a real
GPU, a populated library and about a minute of scrolling, which does not belong
in a unit test. What is cheap to protect is the wiring that fixes it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NOCTURNE = REPO_ROOT / "gui" / "nocturne"

# Views that put many equally-sized covers on screen at once, and so must hand
# every CoverArt one shared mask.
LIST_VIEWS = [
    NOCTURNE / "components" / "TrackTable.qml",
]


def _block_at(source: str, open_brace: int) -> tuple[int, int]:
    """Span of the braced block whose opening brace is at *open_brace*."""
    depth, i = 0, open_brace
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, i
        i += 1
    return open_brace + 1, len(source)


def delegate_spans(source: str) -> list[tuple[int, int]]:
    """Character spans of every `delegate: X { ... }` block."""
    spans = []
    for match in re.finditer(r"(?:delegate:|property Component trackRowDelegate:)\s*\w*\s*\{", source):
        spans.append(_block_at(source, match.end() - 1))
    return spans


def repeated_cover_arts(source: str) -> list[str]:
    """CoverArt blocks that sit inside a delegate — i.e. one per row or cell.

    A one-off cover elsewhere in the same file (a playlist's hero image, say)
    is instantiated once and can keep its own mask.
    """
    spans = delegate_spans(source)
    blocks = []
    for match in re.finditer(r"CoverArt\s*\{", source):
        start = match.end() - 1
        if any(lo <= start < hi for lo, hi in spans):
            blocks.append(source[slice(*_block_at(source, start))])
    return blocks


@pytest.mark.parametrize("path", LIST_VIEWS, ids=lambda p: p.name)
def test_list_views_share_one_cover_mask(path):
    source = path.read_text(encoding="utf-8")
    blocks = repeated_cover_arts(source)
    assert blocks, f"{path.name}: no CoverArt inside a delegate — did it move?"

    for block in blocks:
        assert "maskSource:" in block, (
            f"{path.name} renders many covers at once but one of them does not set "
            "maskSource, so it will build a mask layer per row. See this module's "
            "docstring for what that costs."
        )

    # And the shared mask itself has to be declared in the view.
    assert re.search(r"CornerMask\s*\{", source), (
        f"{path.name}: declares maskSource but no CornerMask to point it at"
    )


def test_corner_mask_keeps_its_antialiasing():
    """The settings that stop masked corners looking stair-stepped.

    MultiEffect thresholds the mask, so the mask's antialiased fringe has to
    survive two hops: being rendered to a texture (needs `antialiasing`, and
    `layer.smooth` so it is not sampled nearest-neighbour), and being applied
    (needs spread on the threshold — see CoverArt.qml).
    """
    mask = (NOCTURNE / "components" / "CornerMask.qml").read_text(encoding="utf-8")
    assert re.search(r"antialiasing:\s*true", mask)
    assert re.search(r"layer\.smooth:\s*true", mask)
    assert re.search(r"layer\.enabled:\s*true", mask)

    cover = (NOCTURNE / "components" / "CoverArt.qml").read_text(encoding="utf-8")
    spread = re.search(r"maskSpreadAtMin:\s*([\d.]+)", cover)
    assert spread and float(spread.group(1)) > 0, (
        "MultiEffect's default maskSpreadAtMin of 0 applies the mask as a hard "
        "cut, which discards the antialiased corner entirely"
    )


def test_cover_art_only_builds_a_private_mask_when_unshared():
    """The private fallback must not allocate a texture when a mask was given."""
    source = (NOCTURNE / "components" / "CoverArt.qml").read_text(encoding="utf-8")

    assert "property Item maskSource" in source, "CoverArt must accept a shared mask"
    assert re.search(r"layer\.enabled:\s*root\.maskSource === null", source), (
        "CoverArt's private mask must only enable its layer when no shared mask "
        "was supplied, otherwise sharing saves nothing"
    )
    assert re.search(r"maskSource:\s*root\.maskSource !== null", source), (
        "CoverArt's MultiEffect must prefer the shared mask over the private one"
    )


@pytest.mark.parametrize("name", ["LibraryCard.qml", "SongTile.qml"])
def test_collection_delegates_consume_view_mask(name):
    source = (NOCTURNE / "components" / name).read_text()
    covers = [source[slice(*_block_at(source, match.end() - 1))]
              for match in re.finditer(r"CoverArt\s*\{", source)]
    assert covers
    assert all(re.search(r"maskSource:.*view\.artMask", block) for block in covers)
    assert not re.search(r"CornerMask\s*\{", source), "delegates must not allocate mask textures"


@pytest.mark.parametrize("name", ["LibraryGrid.qml", "LibraryList.qml"])
def test_shared_layouts_own_one_mask(name):
    source = (NOCTURNE / "components" / name).read_text()
    assert len(re.findall(r"CornerMask\s*\{", source)) == 1
    assert "property alias artMask: sharedMask" in source
