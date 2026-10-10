"""Reading and writing a scoped JSON document.

Kroma keeps two of them, and they have the same shape:

    {
      "version": 1,
      "app": { "<key>": <value>, ... },
      "gui": { "<package>": { "<key>": <value>, ... } }
    }

``session.json`` is disposable — the queue, the window, which page was open.
``settings.json`` is durable — everything in the Settings tab, plus every GUI
look-and-feel preference. They are separate files so they have separate blast
radii: a torn write to the queue must not revoke the download agreement or
forget which drive backups go to.

One helper serves both because the *shape* is the same; what differs is the
version constant, which each file owns, and how hard the write tries. A version
this module does not recognise reads as an empty document, which is how a format
change discards the old file without a migration step.

Leaf keys stay flat dotted strings. Nesting on every dot would make
``columns.queue`` — a key whose *value* is JSON — indistinguishable from a real
two-level path, and round-tripping it would need escaping rules. Nesting happens
only at the two boundaries that already exist: the ``app``/``gui`` split, and the
GUI package name under ``gui``.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.backup.identity import write_json_atomic

logger = logging.getLogger(__name__)


def load(path: Path, version: int) -> dict:
    """Read the document at *path*, or return ``{}`` on any error.

    A missing file, unreadable bytes, malformed JSON and a version that is not
    *version* all land on the same answer: nothing was saved. Every one of them
    means the app starts on defaults, which is the only behaviour that cannot
    stop it starting at all.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != version:
            return {}
        return data
    except Exception:
        return {}


def _collapse_int_arrays(text: str) -> str:
    """Collapse JSON arrays of integers from multi-line to a single line.

    A five-hundred-entry queue is a list of ids, not a structure worth five
    hundred lines of a file somebody may open in an editor.
    """
    def collapse(m: re.Match) -> str:
        nums = re.findall(r'\d+', m.group(0))
        return '[' + ', '.join(nums) + ']'
    return re.sub(r'\[[\s\d,]+\]', collapse, text, flags=re.DOTALL)


def save(path: Path, version: int, data: dict, *, atomic: bool = False) -> None:
    """Write *data* to *path* under *version*. Silently ignores errors.

    ``atomic=True`` routes through :func:`~app.backup.identity.write_json_atomic`
    so a reader never sees a half-written file. That is what ``settings.json``
    needs: it carries the download consent record, the backup destination and
    the machine's separation configuration, and a torn write costs real state.

    ``session.json`` writes in place, for the reason ``write_json_atomic``'s own
    docstring gives — it holds state the app rebuilds by being used, so the
    cheaper write is the right one.
    """
    document = dict(data)
    document["version"] = version
    try:
        if atomic:
            write_json_atomic(path, document)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _collapse_int_arrays(json.dumps(document, indent=2))
        path.write_text(text, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not save %s: %s", path.name, exc)
