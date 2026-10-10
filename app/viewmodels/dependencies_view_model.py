"""DependenciesViewModel — whether the external programs are installed.

The programs Kroma and its plugins shell out to are not shipped with the app;
the user installs them once and they are found on PATH. That makes a missing one
invisible until the feature that needs it fails, which is a poor way to learn
about it — an import dies on a transcode, a plugin's fetch on "not installed".
This exposes the answer up front so a GUI can say so before anything is
attempted.

The core needs ffmpeg and nothing else. Every enabled plugin adds the programs
its manifest lists under ``requires.programs``, each with the plugin that needs
it, and a program can carry a minimum version: some go *stale* rather than old —
a downloader whose upstream changes every few weeks stops working outright when
it falls behind, with an error that says nothing about versions — so an
installed-but-stale program is reported here as its own state.
"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.plugins import programs
from app.utils import binaries


class DependenciesViewModel(QObject):
    """Read-only status of the external programs the app and its plugins use."""

    changed = Signal()

    def __init__(self, host: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # The plugin host, for which programs enabled plugins need. ``None`` —
        # no plugins — and the list is ffmpeg alone.
        self._host = host
        if host is not None and hasattr(host, "plugins_changed"):
            host.plugins_changed.connect(self.changed)

    @Property(str, notify=changed)
    def ffmpegPath(self) -> str:  # noqa: N802
        """Where ffmpeg was found, or empty when it was not."""
        return binaries.ffmpeg_path() or ""

    @Property(bool, notify=changed)
    def ffmpegAvailable(self) -> bool:  # noqa: N802
        """False disables non-Opus imports, MP3 export and vocal removal."""
        return binaries.ffmpeg_path() is not None

    @Property("QVariantList", notify=changed)
    def programs(self) -> List[Dict[str, Any]]:
        """Every program worth checking: ``[{name, path, available, version,
        minimumVersion, stale, neededBy, purpose, install}]``.

        ffmpeg first, needed by Kroma itself; then each enabled plugin's, in
        plugin order, with one row per program however many plugins need it —
        ``neededBy`` names them all, and the highest floor any of them states
        is the one checked. A version that cannot be read is never stale; see
        :func:`app.plugins.programs.is_stale`.
        """
        wanted: Dict[str, Dict[str, Any]] = {
            "ffmpeg": {
                "minimum": "", "args": ("-version",), "needed_by": ["Kroma Player"],
                "purpose": "Audio conversion. Without it: importing files that are "
                           "not already Opus, exporting to MP3, and generating "
                           "instrumentals.",
                "install": "winget install --id=Gyan.FFmpeg -e",
            },
        }
        for manifest in (self._host.active() if self._host is not None else []):
            for program in manifest.programs:
                entry = wanted.setdefault(program.name, {
                    "minimum": "", "args": program.version_args, "needed_by": [],
                    "purpose": "", "install": "",
                })
                if manifest.name not in entry["needed_by"]:
                    entry["needed_by"].append(manifest.name)
                # The first plugin to say what it is for and how to get it is
                # the one quoted; a second saying it again adds nothing.
                entry["purpose"] = entry["purpose"] or program.purpose
                entry["install"] = entry["install"] or program.install
                if program.min_version and (
                    not entry["minimum"]
                    or programs.is_stale(entry["minimum"], program.min_version)
                ):
                    entry["minimum"] = program.min_version
        return [
            {
                **programs.status(
                    name,
                    minimum=entry["minimum"],
                    version_args=entry["args"],
                    needed_by=entry["needed_by"],
                ).as_dict(),
                "purpose": entry["purpose"],
                "install": entry["install"],
            }
            for name, entry in wanted.items()
        ]

    @Slot()
    def refresh(self) -> None:
        """Look again.

        Lookups are cached for the life of the process, so without dropping that
        cache a program installed while the app is open would stay missing until
        a restart — which is exactly the moment a user wants to re-check. The
        versions are cached separately and just as stale after an update, so
        both caches go.
        """
        programs.clear_cache()
        self.changed.emit()
