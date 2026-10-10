"""Import service — local-file analysis and import orchestration.

Bridges the import UI and the import subsystem (``app.importer``). It owns the
*analysis* step (scan local paths, probe each file, bucket them by what identity
information is available) and the database "already imported" lookup used to
flag rows the library already has. The heavy work (scan/identify/stash) runs in
the workers; this service holds no Qt state and is safe to call from a worker
thread.

It knows nothing about plugins. Which external ids a probe should look for, and
which CSV columns carry one, arrive as a snapshot of declared kinds from the
caller — the import view model, which reads them off the provider registry on
the UI thread.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Collection, Dict, Iterable, List, Mapping, Optional, Sequence,
)

from app.importer.csv_import import parse_csv as _parse_csv
from app.importer.media_probe import ProbedFile, gather_media_files, probe_media_files
from app.services.track_service import TrackService


@dataclass
class ImportAnalysis:
    """Result of probing a batch of media files, ready to drive the import card."""

    files: List[ProbedFile] = field(default_factory=list)

    # ---- Identity buckets (counts) ------------------------------------

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def with_external_id(self) -> int:
        return sum(1 for f in self.files if f.has_external_id)

    @property
    def with_title_artist(self) -> int:
        return sum(1 for f in self.files if f.has_title_artist)

    @property
    def with_metadata(self) -> int:
        """Files identifiable without the file-name fallback (id or title+artist)."""
        return sum(1 for f in self.files if f.has_metadata)

    @property
    def without_metadata(self) -> int:
        """Files with neither an external id nor a title+artist pair."""
        return sum(1 for f in self.files if not f.has_metadata)

    @property
    def unreadable(self) -> int:
        """Files that degraded to an empty probe — corrupt, or not really audio.

        Probing is best-effort so that one bad file never aborts a 400-file
        import, which means these are *in* the scan and will be identified by
        name if anything is. Worth saying on a card about what was found: it is the
        one number that explains a file arriving with nothing known about it.
        """
        return sum(1 for f in self.files if not f.readable)

    # ---- Candidate selection -----------------------------------------

    def selection(
        self,
        use_filename_fallback: bool,
        excluded_formats: Collection[str] = (),
    ) -> List[ProbedFile]:
        """Files that will actually be imported, given the import options.

        The two questions the options card asks, answered in one place so that
        the card's arithmetic and the run's cannot drift apart:

        * a file whose container the user unticked is dropped outright;
        * a file carrying neither an external id nor a title+artist pair proceeds
          only with the fallback on, because otherwise there is nothing to
          identify it by.

        *excluded_formats* holds bare, lower-case extension names — ``"mp3"``,
        not ``".mp3"`` — which is the form :meth:`file_type_counts` keys by and
        the form the setting stores.
        """
        excluded = {str(ext).lstrip(".").lower() for ext in excluded_formats}
        return [
            f
            for f in self.files
            if f.ext.lstrip(".") not in excluded
            and (use_filename_fallback or f.has_metadata)
        ]

    def makeup(
        self,
        use_filename_fallback: bool,
        excluded_formats: Collection[str] = (),
    ) -> Dict[str, Any]:
        """Everything the options card draws, for one setting of its controls.

        Recomputed on every flip rather than derived in the GUI. A card that
        summed its own totals would be a second statement of :meth:`selection`,
        free to drift from the one the run reads — the same rule ``stashAll``
        states for ``importState``, and the reason ``selected`` is given per
        format rather than left to be worked out from ``count``.
        """
        excluded = {str(ext).lstrip(".").lower() for ext in excluded_formats}
        chosen = self.selection(use_filename_fallback, excluded)
        per_format = self.file_type_counts(self.files)
        chosen_per_format = self.file_type_counts(chosen)
        return {
            "total": self.total,
            "selected": len(chosen),
            "unreadable": self.unreadable,
            "withExternalId": self.with_external_id,
            "withTitleArtist": self.with_title_artist,
            "withoutMetadata": self.without_metadata,
            "formats": [
                {
                    "ext": ext,
                    "count": count,
                    "selected": chosen_per_format.get(ext, 0),
                    "untagged": sum(
                        1
                        for f in self.files
                        if f.ext.lstrip(".") == ext and not f.has_metadata
                    ),
                    "excluded": ext in excluded,
                    "transcoded": ext != "opus",
                }
                for ext, count in per_format.items()
            ],
        }

    def candidates(self, use_filename_fallback: bool) -> List[ProbedFile]:
        """Files imported when no container has been excluded.

        The pre-card shape of :meth:`selection`, kept because ``chooseMetadata``
        is still part of the GUI API and answers only the fallback question.
        """
        return self.selection(use_filename_fallback)

    @staticmethod
    def file_type_counts(files: Iterable[ProbedFile]) -> Dict[str, int]:
        """Map of extension (without dot) → count, e.g. ``{"opus": 12, "mp3": 3}``."""
        counter = Counter(f.ext.lstrip(".") for f in files)
        # Sort opus first (the native format), then by descending count.
        return dict(
            sorted(counter.items(), key=lambda kv: (kv[0] != "opus", -kv[1], kv[0]))
        )


class ImportService:
    """Local-file analysis + shared import collaborators."""

    def __init__(self, track_service: TrackService) -> None:
        self.track_service = track_service

    def analyze(
        self,
        paths: Iterable[str],
        should_stop: Callable[[], bool] | None = None,
        kinds: Sequence[Any] = (),
    ) -> ImportAnalysis:
        """Expand files/folders, probe each media file, and bucket the results.

        *should_stop* is polled between files while probing, so a cancelled
        import stops reading the disk instead of running to completion in the
        background. The analysis it returns in that case is partial and the
        caller is expected to throw it away.

        *kinds* is the snapshot of declared external-id kinds the probe looks
        for — see :mod:`app.importer.media_probe`.
        """
        media_files = gather_media_files(paths)
        probed = probe_media_files(media_files, should_stop, kinds)
        return ImportAnalysis(files=probed)

    def owned_track_ids(
        self, results: Sequence[Mapping[str, Any]]
    ) -> List[Optional[int]]:
        """Which library track each result already is — positionally, or None.

        By every id that names the row, and failing that by the recording
        itself, which recognises a track the library fetched from a *different
        release* — the ordinary case for an album. See
        :meth:`TrackService.find_owned`.

        The import tab's door onto that rule. It exists here rather than being
        called through ``track_service`` directly because the import flow is
        already wired to this service, and because leaving two doors open is how
        the list and the album importer would drift apart about what "already in
        your library" means.

        Runs on the main thread, like its neighbour, and for the same reason.
        """
        return self.track_service.find_owned(results)

    def parse_csv(self, path: str, kinds: Sequence[Any] = ()) -> List[ProbedFile]:
        """Parse a CSV export into CSV-sourced ProbedFile candidates.

        Each row becomes a path-less :class:`ProbedFile` (``from_csv=True``)
        carrying the spreadsheet metadata, ready to be identified by the same
        worker the local-file import uses. Raises
        :class:`~app.importer.csv_import.CsvColumnsMissingError` when the header
        has no title, artist or external-id column.
        """
        return _parse_csv(path, kinds)
