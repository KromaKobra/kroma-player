"""Parse a CSV playlist/library export into matchable import candidates.

A CSV import is just another source of :class:`~app.importer.media_probe.ProbedFile`
candidates — the same objects the local-file importer produces — except the
metadata comes from spreadsheet columns instead of audio tags, and there is no
local audio file (``path is None``). A CSV is a list of *wanted* tracks: each
row is identified exactly like a local file, then fetched by whichever enabled
audio source can — with the CSV metadata applied over what the source supplies,
which fills any field the CSV omits.

With no audio source enabled there is nothing to fetch with, so the GUI does not
offer CSV import at all. The parser itself is core and stays: the track editor
reads its normalisers, and a CSV is a perfectly local file.

Column resolution
-----------------
``CSV_COLUMN_ALIASES`` maps each canonical field to an ordered list of accepted
column names. For each field the first alias that exists in the header wins.
Matching is case-insensitive and treats spaces and underscores as equivalent
(``"Release Date"`` == ``"release_date"``). To accept a new column name for an
existing field, just add it to that field's alias list.

External-id columns are not listed here. Each kind an enabled plugin declares
brings its own ``csvColumns``, and the cells under them are read through that
kind's own ``parse`` rules — so a link that works pasted into an editor works
in a spreadsheet cell, and the core never learns what either looks like.

A CSV must expose at least a *title*, an *artists* or an external-id column —
otherwise there is nothing to identify a row by and
:class:`CsvColumnsMissingError` is raised (surfaced to the user as a popup).
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from app.importer.media_probe import ProbedFile

logger = logging.getLogger(__name__)


class CsvColumnsMissingError(Exception):
    """Raised when a CSV lacks any column usable to identify a track."""


# Canonical field → ordered list of accepted column names. First match wins.
# Add new accepted spreadsheet headers here; matching is case/space/underscore
# insensitive so only one spelling per distinct name is needed.
CSV_COLUMN_ALIASES: Dict[str, List[str]] = {
    "title":        ["title", "song", "track", "name"],
    "artists":      ["artists", "artist", "composer", "composers", "writer"],
    "album":        ["album"],
    "genres":       ["genres", "genre", "style"],
    "release_date": ["release_date", "publication_date", "released", "year", "date", "album_date"],
    "bpm":          ["bpm", "tempo"],
    "key":          ["musical_key", "key", "camelot"],
    "isrc":         ["isrc"],
    "copyright":    ["copyright", "copywrite", "label"],
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_csv(path: str | Path, kinds: Sequence = ()) -> List[ProbedFile]:
    """Read *path* and return one CSV-sourced ProbedFile per usable row.

    *kinds* is the snapshot of declared external-id kinds (see the module
    docstring); each one's ``csvColumns`` becomes one more column this reads.

    Raises:
        CsvColumnsMissingError: when the header has no title, artist or
            external-id column (nothing to identify rows by).
    """
    p = Path(path)
    with p.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header_map = _map_columns(reader.fieldnames or [], kinds)
        id_fields = [_id_field(kind) for kind in kinds if header_map.get(_id_field(kind))]
        if not (header_map.get("title") or header_map.get("artists") or id_fields):
            names = ["a title", "artist"] + [k.label for k in kinds if k.csv_columns]
            wanted = (
                " or ".join(names) if len(names) == 2
                else ", ".join(names[:-1]) + ", or " + names[-1]
            )
            raise CsvColumnsMissingError(
                "This CSV has no usable columns.\n\n"
                f"It must contain at least {wanted} column."
            )
        rows: List[ProbedFile] = []
        for raw in reader:
            probed = _row_to_probed(raw, header_map, kinds)
            if probed is not None:
                rows.append(probed)
    return rows


def _id_field(kind) -> str:
    """The internal field name an id kind's column is resolved under."""
    return f"ext:{kind.key}"


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

def _normalize(name: Optional[str]) -> str:
    """Canonicalise a header/alias: lower-case, collapse spaces+underscores to '_'."""
    if not name:
        return ""
    return re.sub(r"[\s_]+", "_", name.strip().lower())


def _map_columns(fieldnames: List[Optional[str]], kinds: Sequence = ()) -> Dict[str, str]:
    """Resolve canonical field → original header string using the alias lists."""
    norm_to_orig: Dict[str, str] = {}
    for header in fieldnames:
        if header is None:
            continue
        norm_to_orig.setdefault(_normalize(header), header)

    aliases_by_field: Dict[str, List[str]] = dict(CSV_COLUMN_ALIASES)
    for kind in kinds:
        if kind.csv_columns:
            aliases_by_field[_id_field(kind)] = list(kind.csv_columns)

    resolved: Dict[str, str] = {}
    for field, aliases in aliases_by_field.items():
        for alias in aliases:
            norm = _normalize(alias)
            if norm in norm_to_orig:
                resolved[field] = norm_to_orig[norm]
                break
    return resolved


def _row_to_probed(
    raw: Dict[str, str], header_map: Dict[str, str], kinds: Sequence = ()
) -> Optional[ProbedFile]:
    """Build a CSV-sourced ProbedFile from one CSV row (skips empty rows)."""

    def value(field: str) -> str:
        header = header_map.get(field)
        if not header:
            return ""
        cell = raw.get(header)
        if cell is None:
            return ""
        return str(cell).strip()

    title = value("title")
    artists = value("artists")
    album = value("album")
    genres = value("genres")
    external: Dict[str, str] = {}
    for kind in kinds:
        found = kind.parse_value(value(_id_field(kind)))
        if found:
            external.setdefault(kind.key, found)
    isrc = value("isrc")
    copyright_text = value("copyright")
    release_date = normalize_date(value("release_date"))
    bpm = parse_bpm(value("bpm"))
    musical_key = normalize_key(value("key")) or ""

    # Skip blank/separator rows that carry no identifying information at all.
    if not any([title, artists, external, album, genres]):
        return None

    return ProbedFile(
        path=None,
        title=title,
        artist=artists,
        album=album,
        external_ids=external,
        release_date=release_date,
        from_csv=True,
        genres=genres,
        musical_key=musical_key,
        bpm=bpm,
        isrc=isrc,
        copyright_text=copyright_text,
    )


# ---------------------------------------------------------------------------
# Field normalisers
# ---------------------------------------------------------------------------

def parse_bpm(raw: str) -> Optional[int]:
    """Parse the first number out of a BPM/tempo cell into a rounded int."""
    if not raw:
        return None
    match = re.search(r"\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        return int(round(float(match.group())))
    except ValueError:
        return None


# Textual month formats tried after the numeric forms. Order matters only for
# disambiguating, which these don't.
_TEXT_DATE_FORMATS: tuple[str, ...] = (
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    "%B %Y", "%b %Y", "%Y%m%d",
)


def normalize_date(raw: str) -> Optional[int]:
    """Normalise any reasonable date string to an int ``YYYYMMDD``.

    Missing month/day components become ``00`` (e.g. ``2020`` → ``20200000``,
    ``2020-05`` → ``20200500``). Numeric ISO forms and common US ``M/D/Y`` forms
    are handled directly; a handful of textual month formats are tried after.
    Returns ``None`` when nothing parseable is found.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    # Year only.
    if re.fullmatch(r"\d{4}", text):
        return int(text) * 10000

    # ISO-ish: YYYY-MM or YYYY-MM-DD (also '/' or '.' separators).
    iso = re.fullmatch(r"(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", text)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        day = int(iso.group(3)) if iso.group(3) else 0
        return year * 10000 + month * 100 + day

    # M/D/Y (US). If the first field can't be a month it's read as D/M/Y.
    mdy = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    if mdy:
        a, b, year = int(mdy.group(1)), int(mdy.group(2)), int(mdy.group(3))
        month, day = (b, a) if a > 12 and b <= 12 else (a, b)
        return year * 10000 + month * 100 + day

    # M/YYYY.
    my = re.fullmatch(r"(\d{1,2})[-/.](\d{4})", text)
    if my:
        return int(my.group(2)) * 10000 + int(my.group(1)) * 100

    for fmt in _TEXT_DATE_FORMATS:
        try:
            parsed = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        has_day = "%d" in fmt
        has_month = "%m" in fmt or "%B" in fmt or "%b" in fmt
        month = parsed.month if has_month else 0
        day = parsed.day if has_day else 0
        return parsed.year * 10000 + month * 100 + day

    # Last resort: grab digits and treat as a YYYY[MM[DD]] prefix.
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 4:
        value = int(digits[:8])
        while value < 10000000:
            value *= 10
        return value
    return None


# Camelot wheel → key (sharps), e.g. "8B" → C major, "8A" → A minor.
_CAMELOT_TO_KEY: Dict[str, str] = {
    "1A": "G#m", "1B": "B",
    "2A": "D#m", "2B": "F#",
    "3A": "A#m", "3B": "C#",
    "4A": "Fm",  "4B": "G#",
    "5A": "Cm",  "5B": "D#",
    "6A": "Gm",  "6B": "A#",
    "7A": "Dm",  "7B": "F",
    "8A": "Am",  "8B": "C",
    "9A": "Em",  "9B": "G",
    "10A": "Bm", "10B": "D",
    "11A": "F#m", "11B": "A",
    "12A": "C#m", "12B": "E",
}

# Normalise flats / enharmonic oddities to the sharp spelling used for output.
_FLAT_TO_SHARP: Dict[str, str] = {
    "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",
    "CB": "B", "FB": "E", "E#": "F", "B#": "C",
}


def normalize_key(raw: str) -> Optional[str]:
    """Normalise any key notation (incl. camelot) to ``C`` / ``Cm`` / ``C#m`` form.

    Major keys are the bare note (``C``, ``F#``); minor keys get a trailing
    ``m`` (``Am``, ``C#m``). Flats are converted to their sharp spelling.
    Returns ``None`` when the input isn't a recognisable key.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None

    # Camelot wheel notation (e.g. "8B", "12a").
    camelot = re.fullmatch(r"(\d{1,2})([ABab])", text.replace(" ", ""))
    if camelot:
        return _CAMELOT_TO_KEY.get(f"{int(camelot.group(1))}{camelot.group(2).upper()}")

    note_match = re.match(r"^([A-Ga-g])([#♯b♭]?)", text)
    if not note_match:
        return None
    letter = note_match.group(1).upper()
    accidental = note_match.group(2)
    note = letter
    if accidental in ("#", "♯"):
        note = letter + "#"
    elif accidental in ("b", "♭"):
        note = letter + "b"
    note = _FLAT_TO_SHARP.get(note.upper(), note)

    rest = text[note_match.end():].lower()
    if "maj" in rest:
        is_minor = False
    elif "min" in rest or rest.strip().startswith("m"):
        is_minor = True
    else:
        is_minor = False
    return note + ("m" if is_minor else "")


def csv_overlay_result(result: dict, probed: ProbedFile) -> dict:
    """Overlay a CSV row's metadata onto an identified result (in place).

    The displayed title/artist/album reflect the CSV (what will actually be
    saved), and the full CSV override is attached under ``"csv_override"`` for
    the fetch step to apply over the source's track data. A no-op for non-CSV
    probes.
    """
    override = probed.csv_override()
    if not override:
        return result
    if override.get("title"):
        result["title"] = override["title"]
    if override.get("artists"):
        result["artist"] = override["artists"]
    if override.get("album"):
        result["album"] = override["album"]
    result["csv_override"] = override
    return result
