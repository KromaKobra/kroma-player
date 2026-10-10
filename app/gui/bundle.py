"""The ``.kpi`` container: mounting one, and reading what is inside it.

A ``.kpi`` — Kroma Player Interface — is a binary Qt resource built with
``rcc --binary`` at prefix ``/``, holding a GUI's file tree verbatim plus two
JSON documents: the author's ``gui.json`` manifest and the compiler's
``build.json`` record. One file is the entire package.

Being built at prefix ``/`` is what makes it mount-point agnostic. The same
bytes can be registered anywhere in the resource tree, so every mount goes to

    /kroma/gui/<name>/<build_id>

and the build id in the path is load-bearing rather than decorative: the
settings picker inspects candidate bundles while a GUI is already loaded, and a
rebuilt bundle must not be able to collide with a stale mount of its previous
self inside one process. Qt supports the same file mounted at two roots at once,
and unregistering one root leaves the other intact, which is what makes that
inspection safe.

This is the only module that touches ``QResource``. Everything else deals in
paths, roots and the records read out of them.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from pathlib import Path

import json

from PySide6.QtCore import QDirIterator, QFile, QIODevice, QResource

from app.gui.gui_paths import BUILD_RECORD_FILENAME, MANIFEST_FILENAME
from app.versions import KPI_FORMAT_VERSION, MIN_KPI_FORMAT_VERSION

logger = logging.getLogger(__name__)

# Every mount lives under here, so nothing this application registers can
# collide with a resource compiled into Qt or into PySide6 itself.
MOUNT_PREFIX = "/kroma/gui"

# Scratch roots for inspecting a bundle without disturbing a loaded one.
_INSPECT_PREFIX = "/kroma/inspect"
_inspect_counter = itertools.count()

# (path, root) pairs this process has registered. Mounting is made idempotent
# through it, and unmounting only ever unregisters something we put there.
_mounted: set[tuple[str, str]] = set()


class BundleError(Exception):
    """Raised when a ``.kpi`` cannot be mounted or does not describe itself."""


@dataclass(frozen=True)
class BundleContents:
    """What was found inside a bundle.

    ``manifest_json`` is handed over as bytes rather than parsed here: parsing
    and validating a manifest is :mod:`app.gui.manifest`'s job, and it reports
    problems in its own vocabulary.
    """

    build: dict
    manifest_json: bytes
    files: tuple[str, ...]

    @property
    def build_id(self) -> str:
        return str(self.build.get("build_id") or "")

    @property
    def name(self) -> str:
        return str(self.build.get("name") or "")


def mount_root(name: str, build_id: str) -> str:
    """Return the resource root a bundle for *name* at *build_id* mounts at."""
    return f"{MOUNT_PREFIX}/{name}/{build_id}"


def mount(path: Path, root: str) -> None:
    """Register *path* into the resource tree at *root*.

    Idempotent: mounting the same file at the same root twice is a no-op rather
    than two mounts that would each need unregistering.

    Raises ``BundleError`` when Qt will not take the file — which covers a
    truncated bundle, one written by a newer ``rcc`` format, and one that is not
    a resource at all.
    """
    key = (str(path), root)
    if key in _mounted:
        return
    if not QResource.registerResource(str(path), root):
        raise BundleError(f"{path}: not a readable .kpi bundle")
    _mounted.add(key)
    logger.debug("Mounted %s at %s", path, root)


def unmount(path: Path, root: str) -> None:
    """Remove the mount of *path* at *root*, leaving any other mount of it alone."""
    key = (str(path), root)
    if key not in _mounted:
        return
    QResource.unregisterResource(str(path), root)
    _mounted.discard(key)
    logger.debug("Unmounted %s from %s", path, root)


def is_mounted(path: Path, root: str) -> bool:
    """True when this process has *path* registered at *root*."""
    return (str(path), root) in _mounted


def read_file(root: str, relative: str) -> bytes:
    """Read one file out of the bundle mounted at *root*.

    Raises ``BundleError`` if it is not there, which for the two documents this
    module reads means the bundle is not a GUI package.
    """
    handle = QFile(f":{root}/{relative}")
    if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
        raise BundleError(f"{root}: bundle contains no {relative}")
    try:
        return bytes(handle.readAll().data())
    finally:
        handle.close()


def list_files(root: str) -> tuple[str, ...]:
    """Return every file in the bundle mounted at *root*, as relative paths."""
    base = f":{root}"
    found: list[str] = []
    iterator = QDirIterator(
        base,
        QDirIterator.IteratorFlag.Subdirectories,
    )
    while iterator.hasNext():
        entry = iterator.next()
        info = iterator.fileInfo()
        if info.isFile() and entry.startswith(base + "/"):
            found.append(entry[len(base) + 1:])
    return tuple(sorted(found))


def read_contents(root: str) -> BundleContents:
    """Read the two records and the file list out of an already-mounted bundle."""
    raw = read_file(root, BUILD_RECORD_FILENAME)
    try:
        build = json.loads(raw)
    except ValueError as exc:
        raise BundleError(f"{root}: {BUILD_RECORD_FILENAME} is not valid JSON ({exc})") from exc
    if not isinstance(build, dict):
        raise BundleError(f"{root}: {BUILD_RECORD_FILENAME} must contain a JSON object")

    problem = format_problem(build)
    if problem:
        raise BundleError(problem)

    return BundleContents(
        build=build,
        manifest_json=read_file(root, MANIFEST_FILENAME),
        files=list_files(root),
    )


def read_records(path: Path) -> BundleContents:
    """Mount *path* somewhere private, read it, and unmount it again.

    This is how a bundle nobody has asked to load gets inspected: the settings
    picker listing what is installed, and the installer working out what a
    third-party ``.kpi`` dropped into the folder actually is. The scratch root
    is unique per call, so this can run while the same file is mounted for real
    and the loaded GUI is untouched.
    """
    root = f"{_INSPECT_PREFIX}/{next(_inspect_counter)}"
    mount(path, root)
    try:
        return read_contents(root)
    finally:
        unmount(path, root)


def format_problem(build: dict) -> str:
    """Return why this build record cannot be read, or ``""``.

    The ``.kpi`` format is a contract like any other: the compiler stamps one
    integer, this build supports a range. A bundle from the future is skipped
    rather than guessed at, because the container is what everything else is
    read out of.
    """
    stamped = build.get("format")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return "bundle build record has no 'format' version"
    if stamped > KPI_FORMAT_VERSION:
        return (
            f"bundle is format {stamped}, built by a newer Kroma Player "
            f"(this build reads up to {KPI_FORMAT_VERSION}) — update Kroma Player"
        )
    if stamped < MIN_KPI_FORMAT_VERSION:
        return (
            f"bundle is format {stamped}, which this build no longer reads "
            f"(minimum {MIN_KPI_FORMAT_VERSION}) — rebuild it"
        )
    return ""
