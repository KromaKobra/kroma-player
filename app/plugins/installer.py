"""Installing a plugin from a ``.kpp``, and removing one.

A ``.kpp`` is a zip: the plugin's folder at the top level — ``plugin.json``, the
backend, its vendored ``lib/`` — plus a ``kpp.json`` saying which container
format it is. The format is checked first, against ``KPP_FORMAT_VERSION``, so a
package from a future build is refused with a reason rather than half-read.

**Nothing in the archive is trusted to name its own location.** A zip entry is a
path the author wrote, and the classic attack is ``../../.bashrc``. Every name
is refused before anything is written if it is absolute, carries a drive letter
or a backslash, climbs out with ``..``, is a symlink, or duplicates another name
— case-insensitively, because on Windows and macOS ``Main.py`` and ``main.py``
are one file and the second write would silently replace the first. Totals are
capped too, so a zip bomb runs out of allowance rather than out of disk.

**Installing is atomic.** The package is extracted into a staging folder beside
the real one, its manifest is validated there, the running plugin is stopped,
and only then is the old folder moved aside and the new one moved in. The old
copy is kept until that move has succeeded, and put back if it has not — so a
failed upgrade leaves the previous version installed and working, never a
folder half one and half the other.

**Uninstalling removes code, never data.** Whether the plugin was on, its
consent and its settings live in the vault and stay there, and the ids it
identified tracks with are columns the core owns. Reinstalling brings all of it
back exactly as it was.
"""
from __future__ import annotations

import json
import logging
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from app.plugins.manifest import (
    MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    load_manifest,
    parse,
)
from app.plugins.paths import plugin_dir, plugins_root
from app.versions import KPP_FORMAT_VERSION, MIN_KPP_FORMAT_VERSION

logger = logging.getLogger(__name__)

KPP_SUFFIX = ".kpp"
KPP_RECORD = "kpp.json"

#: Ceilings for one package. Generous for a plugin that vendors a handful of
#: pure-Python wheels — the official one is a few megabytes — and small enough
#: that a hostile archive cannot fill a disk.
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024

STAGING_PREFIX = ".new-"
BACKUP_PREFIX = ".old-"


class InstallError(Exception):
    """The package cannot be installed. Nothing was changed."""


@dataclass(frozen=True)
class Inspection:
    """What the install screen shows before anything is written."""

    manifest: PluginManifest
    path: Path
    files: int
    size: int
    installed_version: str = ""


def _check_names(archive: zipfile.ZipFile) -> tuple[int, int]:
    """Refuse every unsafe entry. Returns ``(file count, uncompressed bytes)``."""
    seen: set[str] = set()
    files = 0
    total = 0
    for info in archive.infolist():
        name = info.filename
        if not name or "\\" in name or name.startswith("/") or "\x00" in name:
            raise InstallError(f"The package contains an unsafe path: {name!r}")
        path = PurePosixPath(name)
        if any(part == ".." for part in path.parts):
            raise InstallError(f"The package contains an unsafe path: {name!r}")
        if path.parts and ":" in path.parts[0]:
            raise InstallError(f"The package contains a drive-letter path: {name!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise InstallError(f"The package contains a symbolic link: {name!r}")
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise InstallError(f"The package contains {name!r} twice")
        seen.add(folded)
        if info.is_dir():
            continue
        files += 1
        if info.file_size > MAX_FILE_BYTES:
            raise InstallError(f"{name!r} is larger than a plugin file may be")
        total += info.file_size
        if files > MAX_FILES:
            raise InstallError("The package holds more files than a plugin may")
        if total > MAX_TOTAL_BYTES:
            raise InstallError("The package is larger than a plugin may be")
    return files, total


def _read_record(archive: zipfile.ZipFile) -> dict:
    try:
        record = json.loads(archive.read(KPP_RECORD))
    except KeyError as exc:
        raise InstallError(f"Not a Kroma plugin package — there is no {KPP_RECORD}") from exc
    except ValueError as exc:
        raise InstallError(f"{KPP_RECORD} is not valid JSON") from exc
    if not isinstance(record, dict):
        raise InstallError(f"{KPP_RECORD} must be a JSON object")
    fmt = record.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise InstallError(f"{KPP_RECORD} has no format number")
    if fmt > KPP_FORMAT_VERSION:
        raise InstallError(
            f"This plugin package is format {fmt}, newer than this build reads "
            f"({KPP_FORMAT_VERSION}) — update Kroma Player"
        )
    if fmt < MIN_KPP_FORMAT_VERSION:
        raise InstallError(
            f"This plugin package is format {fmt}, which this build no longer reads"
        )
    return record


def inspect(path: Path | str) -> Inspection:
    """Read a package without installing it. Raises :class:`InstallError`.

    What the install screen is drawn from: the manifest, how big the package
    is, and which version — if any — it would replace.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            files, size = _check_names(archive)
            _read_record(archive)
            try:
                raw = archive.read(MANIFEST_FILENAME)
            except KeyError as exc:
                raise InstallError(f"The package has no {MANIFEST_FILENAME}") from exc
    except zipfile.BadZipFile as exc:
        raise InstallError("Not a Kroma plugin package — it is not a zip file") from exc
    except OSError as exc:
        raise InstallError(f"Could not read the package: {exc}") from exc

    try:
        manifest = parse(raw, f"{path.name}:{MANIFEST_FILENAME}")
    except PluginManifestError as exc:
        raise InstallError(str(exc)) from exc

    installed = ""
    try:
        installed = load_manifest(plugin_dir(manifest.id)).version
    except PluginManifestError:
        pass
    return Inspection(manifest, path, files, size, installed)


def _extract(archive: zipfile.ZipFile, target: Path) -> None:
    """Extract every entry under *target*, re-checking each resolved path."""
    root = target.resolve()
    for info in archive.infolist():
        destination = (target / info.filename).resolve()
        if destination != root and not destination.is_relative_to(root):
            raise InstallError(f"The package contains an unsafe path: {info.filename!r}")
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(destination, "wb") as sink:
            shutil.copyfileobj(source, sink)
        # Keep the executable bit, which zip carries in the high bits and
        # extract() would drop. Nothing else of the mode is honoured.
        if (info.external_attr >> 16) & 0o111:
            destination.chmod(destination.stat().st_mode | 0o755)


def install(
    path: Path | str, *, stop: Optional[Callable[[str], None]] = None
) -> PluginManifest:
    """Install the package at *path*, replacing any installed copy.

    *stop* is called with the plugin id after the new copy has been validated
    and before anything is moved, so the running process is not executing out
    of a folder that is about to be renamed. Raises :class:`InstallError`; on
    any failure the previously installed copy is left as it was.
    """
    inspection = inspect(path)
    manifest = inspection.manifest
    root = plugins_root()
    root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staging = root / f"{STAGING_PREFIX}{manifest.id}-{token}"
    backup = root / f"{BACKUP_PREFIX}{manifest.id}-{token}"
    target = plugin_dir(manifest.id)

    try:
        staging.mkdir()
        with zipfile.ZipFile(inspection.path) as archive:
            _check_names(archive)
            _extract(archive, staging)
        (staging / KPP_RECORD).unlink(missing_ok=True)
        try:
            staged = load_manifest(staging)
        except PluginManifestError as exc:
            raise InstallError(str(exc)) from exc
        if staged.id != manifest.id:
            raise InstallError("The package's manifest changed while it was being read")

        if stop is not None:
            stop(manifest.id)

        moved_old = False
        if target.exists():
            target.rename(backup)
            moved_old = True
        try:
            staging.rename(target)
        except OSError as exc:
            if moved_old and not target.exists():
                backup.rename(target)
            raise InstallError(f"Could not install {manifest.name}: {exc}") from exc
    except InstallError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError(f"Could not install {manifest.name}: {exc}") from exc

    shutil.rmtree(backup, ignore_errors=True)
    logger.info("Installed plugin %s %s", manifest.id, manifest.version)
    return staged


def uninstall(plugin_id: str, *, stop: Optional[Callable[[str], None]] = None) -> bool:
    """Remove *plugin_id*'s code. Returns whether there was any.

    Its vault-side record — enabled, consent, settings — is deliberately left
    alone; see the module docstring.
    """
    target = plugin_dir(plugin_id)
    if not target.exists():
        return False
    if stop is not None:
        stop(plugin_id)
    graveyard = plugins_root() / f"{BACKUP_PREFIX}{plugin_id}-{uuid.uuid4().hex[:8]}"
    try:
        # Renamed first so the folder stops being a plugin in one step, then
        # deleted at leisure. A delete that fails halfway leaves a dot-folder
        # nothing will load, not a plugin missing half its files.
        target.rename(graveyard)
    except OSError as exc:
        raise InstallError(f"Could not remove {plugin_id}: {exc}") from exc
    shutil.rmtree(graveyard, ignore_errors=True)
    logger.info("Uninstalled plugin %s", plugin_id)
    return True


def sweep_leftovers() -> None:
    """Delete staging and backup folders a crashed install left behind."""
    root = plugins_root()
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.name.startswith((STAGING_PREFIX, BACKUP_PREFIX)):
            shutil.rmtree(child, ignore_errors=True)
