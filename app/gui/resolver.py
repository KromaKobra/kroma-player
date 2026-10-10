"""Selection of which GUI bundle to load at startup.

Resolution order (first one that loads wins):

1. ``KROMA_GUI_DIR``  — a GUI *source checkout*. It is compiled and then loaded,
   so an author working out of a tree does not have to install anything. Dev
   only: a release never sets it and a frozen build ships no compiler input.
2. ``KROMA_GUI``      — the name of an installed GUI.
3. the ``selected_gui`` key in session.json (the user's choice in Settings).
4. the official GUI, installed in the vault.
5. the official GUI, shipped inside the application.

Every step degrades rather than crashes: a bundle that will not mount, one with
a broken manifest, a missing entry file, an unreadable build record or an
incompatible API version is skipped with a log line and the next candidate is
tried. The application therefore always starts, even if the only thing it can
start with is the bundle inside its own installation.

Nothing here mounts a bundle for keeps. Candidates are inspected at a private
resource root and unmounted again; ``app.main`` mounts the winner at the root it
will actually be loaded from.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl

from app.gui.bundle import BundleContents, BundleError, mount_root, read_records
from app.gui.compiler import CompilerError, build_bundle
from app.gui.gui_paths import (
    BUNDLE_SUFFIX,
    OFFICIAL_GUI_NAME,
    bundled_gui_bundle,
    gui_bundle_path,
    gui_dev_bundle_path,
    gui_root,
    is_valid_gui_name,
)
from app.gui.manifest import GuiManifest, GuiManifestError, from_bytes

logger = logging.getLogger(__name__)

ENV_GUI_NAME = "KROMA_GUI"
ENV_GUI_DIR = "KROMA_GUI_DIR"


@dataclass(frozen=True)
class ResolvedGui:
    """The GUI bundle the application is going to load."""

    manifest: GuiManifest
    bundle_path: Path
    build_id: str
    requested: str

    @property
    def mount_root(self) -> str:
        """The resource root this bundle is mounted at.

        The build id is part of it, so a rebuilt bundle can never be confused
        with a stale mount of its previous self inside one process.
        """
        return mount_root(self.manifest.name, self.build_id)

    @property
    def import_path(self) -> str:
        """What to hand ``QQmlApplicationEngine.addImportPath``.

        A GUI can ship its own QML modules — a subfolder with a ``qmldir`` and a
        ``pragma Singleton`` in it — and importing them by name needs the mount
        root on the import path, exactly as the package folder used to be.
        """
        return f"qrc:{self.mount_root}"

    @property
    def entry_url(self) -> QUrl:
        """The URL of the QML file to hand the engine."""
        return QUrl(f"qrc:{self.mount_root}/{self.manifest.entry}")

    @property
    def is_fallback(self) -> bool:
        """True when the loaded GUI is not the one that was asked for."""
        return bool(self.requested) and self.requested != self.manifest.name


@dataclass(frozen=True)
class GuiCandidate:
    """A ``.kpi`` found on disk, loadable or not."""

    bundle_path: Path
    manifest: GuiManifest | None
    build_id: str
    error: str
    bundled: bool = False

    @property
    def name(self) -> str:
        # Falling back to the file name matters for a bundle that could not be
        # read at all: the picker still has something to put beside the reason.
        return self.manifest.name if self.manifest else self.bundle_path.stem

    @property
    def loadable(self) -> bool:
        return self.manifest is not None and self.manifest.is_compatible


def _manifest_of(contents: BundleContents, path: Path) -> GuiManifest:
    """Parse the manifest out of an inspected bundle."""
    return from_bytes(contents.manifest_json, contents.files, str(path))


def inspect_bundle(path: Path, bundled: bool = False) -> GuiCandidate:
    """Read a bundle's records, capturing rather than raising any problem."""
    try:
        contents = read_records(path)
    except BundleError as exc:
        return GuiCandidate(path, None, "", str(exc), bundled)

    if not contents.build_id:
        return GuiCandidate(path, None, "", f"{path}: build record has no build id", bundled)

    try:
        manifest = _manifest_of(contents, path)
    except GuiManifestError as exc:
        return GuiCandidate(path, None, contents.build_id, str(exc), bundled)

    return GuiCandidate(
        path, manifest, contents.build_id, manifest.incompatibility_reason(), bundled
    )


def available_guis() -> list[GuiCandidate]:
    """Return every GUI bundle found, sorted with the official one first.

    Includes bundles that cannot be loaded — each carries the reason in
    ``error`` so a settings picker can explain itself instead of hiding them.

    Every candidate is mounted, read and unmounted. That is what discovers a
    third-party ``.kpi`` somebody has dropped into the folder, and it is why
    this is called when the picker is opened rather than at startup, where only
    the one bundle being loaded is ever touched.
    """
    candidates: list[GuiCandidate] = []
    seen: set[str] = set()

    root = gui_root()
    if root.is_dir():
        for entry in sorted(root.glob(f"*{BUNDLE_SUFFIX}")):
            if not entry.is_file():
                continue
            candidate = inspect_bundle(entry)
            candidates.append(candidate)
            seen.add(candidate.name)

    # The shipped bundle is only listed when it is not already installed, so the
    # picker never shows it twice.
    if OFFICIAL_GUI_NAME not in seen:
        shipped = bundled_gui_bundle()
        if shipped.is_file():
            candidates.append(inspect_bundle(shipped, bundled=True))

    candidates.sort(key=lambda c: (c.name != OFFICIAL_GUI_NAME, c.name.lower()))
    return candidates


def _compiled_checkout(directory: Path) -> Path | None:
    """Compile the source tree at *directory*, returning the bundle it produced.

    The bundle goes to a dot-folder inside the gui folder rather than beside the
    installed ones: it is a build product of somebody's working copy, and it has
    no business turning up in the picker as a second copy of the GUI it was
    built from.
    """
    output = gui_dev_bundle_path(directory.name)
    try:
        build_bundle(directory, output)
    except (CompilerError, OSError) as exc:
        logger.warning("Could not compile the GUI at %s: %s", directory, exc)
        return None
    return output


def _candidates(preferred: str | None) -> list[tuple[str, Path]]:
    """Build the ordered (source, bundle path) list to try."""
    entries: list[tuple[str, Path]] = []

    checkout = os.environ.get(ENV_GUI_DIR, "").strip()
    if checkout:
        directory = Path(checkout).expanduser()
        compiled = _compiled_checkout(directory)
        if compiled is not None:
            entries.append((f"${ENV_GUI_DIR}", compiled))

    for source, name in (
        (f"${ENV_GUI_NAME}", os.environ.get(ENV_GUI_NAME, "").strip()),
        ("session preference", (preferred or "").strip()),
        ("default", OFFICIAL_GUI_NAME),
    ):
        if not name:
            continue
        if not is_valid_gui_name(name):
            logger.warning("Ignoring invalid GUI name from %s: %r", source, name)
            continue
        entries.append((source, gui_bundle_path(name)))

    entries.append(("bundled", bundled_gui_bundle()))

    # Preserve order while dropping repeats (the common case is the session
    # preference already being the official GUI).
    unique: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for source, path in entries:
        if path not in seen:
            seen.add(path)
            unique.append((source, path))
    return unique


def requested_gui_name(preferred: str | None) -> str:
    """Return the GUI name the user asked for, before any fallback."""
    for value in (os.environ.get(ENV_GUI_NAME, "").strip(), (preferred or "").strip()):
        if value and is_valid_gui_name(value):
            return value
    return OFFICIAL_GUI_NAME


def resolve_gui(preferred: str | None = None) -> ResolvedGui | None:
    """Return the first loadable GUI, or ``None`` if there is not one anywhere."""
    requested = requested_gui_name(preferred)

    for source, path in _candidates(preferred):
        if not path.is_file():
            if source not in ("default", "bundled"):
                logger.warning("GUI from %s not found at %s", source, path)
            continue

        candidate = inspect_bundle(path)
        if candidate.manifest is None:
            logger.warning("Skipping bundle at %s: %s", path, candidate.error)
            continue
        if not candidate.manifest.is_compatible:
            logger.warning(
                "Skipping GUI '%s' at %s: %s",
                candidate.manifest.name, path, candidate.error,
            )
            continue

        logger.info(
            "Loading GUI '%s' %s from %s (%s)",
            candidate.manifest.name, candidate.manifest.version, path, source,
        )
        return ResolvedGui(candidate.manifest, path, candidate.build_id, requested)

    logger.error(
        "No loadable GUI found — checked %s and the application's own bundle", gui_root()
    )
    return None
