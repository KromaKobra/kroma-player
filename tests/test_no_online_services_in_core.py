"""The core names no online service, and has to stay that way.

Kroma is a local player. Searching, matching and fetching come from plugins,
and the ids those plugins deal in are kept by a core that does not know whose
they are (see ``app/plugins/external_ids.py``). The easy way to undo that is one
convenient import, one hard-coded link or one column named after a catalogue.
So this scans everything in ``app/``, ``gui/`` and ``sdk/``, comments and
docstrings included, for the names that would mean it had happened.

One file is allowed to say them: ``app/database/schema.py``. Its migrations are
history. Migration 7 has to name the columns it moves ids out of, and the
released migrations before it created those columns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

NAMES = re.compile(
    r"youtube|youtu\.be|ytmusic|yt[-_]dlp|ytimg|googleusercontent|spotify",
    re.IGNORECASE,
)

#: The one file allowed to name them, and why: see the module docstring.
HISTORY = "app/database/schema.py"

SCANNED = {
    "app": ("*.py", "*.qml"),
    "gui": ("*.qml", "*.js", "*.json", "*.md"),
    "sdk": ("*.py",),
}


def _files():
    for folder, patterns in SCANNED.items():
        for pattern in patterns:
            yield from (REPO_ROOT / folder).rglob(pattern)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_the_core_names_no_online_service():
    hits = []
    for path in sorted(set(_files())):
        if _rel(path) == HISTORY:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NAMES.search(line):
                hits.append(f"{_rel(path)}:{number}: {line.strip()[:80]}")
    assert not hits, (
        "the core names an online service; that belongs in a plugin:\n" + "\n".join(hits)
    )


def test_the_history_exemption_points_at_a_real_file():
    """An exemption for a file that moved would quietly exempt nothing."""
    assert (REPO_ROOT / HISTORY).is_file()


@pytest.mark.parametrize("folder", sorted(SCANNED))
def test_every_scanned_folder_has_files(folder):
    """A scan of an empty glob passes for the wrong reason."""
    assert any((REPO_ROOT / folder).rglob(SCANNED[folder][0]))
