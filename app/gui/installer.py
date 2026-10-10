"""Keeping the vault's copy of the official GUI up to date.

The GUI ships as one compiled file, so installing it is one atomic replace and
deciding whether to bother is two small JSON reads: the build record beside the
shipped bundle, and the index of what is already installed. If the build ids
agree and the installed file is still the size and modification time the index
recorded, there is nothing to do.

That is the whole point of the build id. The previous arrangement read every
byte of both trees on every launch — 812 KB twice — to answer the same question,
and needed an edited/pristine ladder on top of it to avoid clobbering hand edits
in a folder the user was invited to fork. Nothing is loose on disk now, so there
is nothing to preserve and nothing to compare.

In a checkout there is one extra step: the source tree is fingerprinted and
recompiled if it has changed, so ``python run.py`` after editing QML just works.
It costs about 50 ms, it cannot happen in a frozen build — there is no source
tree there — and ``KROMA_NO_GUI_BUILD=1`` turns it off.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from app.backup.identity import utc_now, write_json_atomic
from app.gui.bundle import BundleError, read_records
from app.gui.compiler import (
    CompilerError,
    ENV_NO_BUILD,
    build_bundle,
    read_sidecar,
    source_fingerprint,
)
from app.gui.gui_paths import (
    BACKUP_SUFFIX,
    CACHE_DIRNAME,
    INDEX_FILENAME,
    OFFICIAL_GUI_NAME,
    bundled_gui_bundle,
    bundled_gui_sidecar,
    bundled_gui_source,
    gui_bundle_path,
    gui_cache_root,
    gui_index_path,
    gui_root,
)

logger = logging.getLogger(__name__)

INDEX_VERSION = 1


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def read_index() -> dict:
    """Return the installed-GUI index, or an empty one.

    Never raises. A missing or unreadable index costs an inspection of whatever
    bundles are actually there and is then rewritten, so losing it is a slow
    launch rather than a broken one.
    """
    try:
        data = json.loads(gui_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": INDEX_VERSION, "guis": {}}
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
        return {"version": INDEX_VERSION, "guis": {}}
    guis = data.get("guis")
    return {"version": INDEX_VERSION, "guis": guis if isinstance(guis, dict) else {}}


def write_index(index: dict) -> None:
    """Write the index atomically. Raises ``OSError`` if it cannot be written."""
    write_json_atomic(gui_index_path(), index)


def index_entry(path: Path, record: dict) -> dict:
    """Describe an installed bundle for the index.

    The size and modification time are what let the next launch trust the
    recorded build id without opening the file. They are not a security
    boundary — anyone can touch a file — they are the cheapest way to notice
    that the thing on disk is no longer the thing that was written there.
    """
    stat = path.stat()
    return {
        "build_id": str(record.get("build_id") or ""),
        "version": str(record.get("version") or ""),
        "api_version": record.get("api_version"),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "installed_at": utc_now(),
    }


def entry_matches(path: Path, entry: dict | None) -> bool:
    """True when *path* is still the file *entry* was written for."""
    if not isinstance(entry, dict) or not entry.get("build_id"):
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size == entry.get("size") and stat.st_mtime_ns == entry.get("mtime_ns")


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def _shipped_record(name: str) -> dict:
    """The build record of the bundle shipped with the application.

    Read from the sidecar, which exists so this never has to open the bundle. A
    missing or unhelpful sidecar falls back to inspecting the bundle itself, so
    a hand-assembled build still works.
    """
    record = read_sidecar(bundled_gui_sidecar(name))
    if record.get("build_id"):
        return record
    try:
        return read_records(bundled_gui_bundle(name)).build
    except BundleError as exc:
        logger.warning("Shipped GUI bundle for '%s' is unusable: %s", name, exc)
        return {}


def _rebuild_from_source(name: str) -> None:
    """Recompile the shipped bundle when the checkout's QML has moved on.

    Only ever runs from a source tree. A frozen build has no ``gui/<name>/``
    folder to compile, so it takes the copy path unconditionally and never goes
    looking for rcc.
    """
    source = bundled_gui_source(name)
    if not source.is_dir() or os.environ.get(ENV_NO_BUILD, "").strip():
        return

    bundle = bundled_gui_bundle(name)
    sidecar = bundled_gui_sidecar(name)
    try:
        fingerprint = source_fingerprint(source)
    except OSError as exc:
        logger.warning("Could not fingerprint %s: %s", source, exc)
        return

    if bundle.is_file() and read_sidecar(sidecar).get("build_id") == fingerprint.build_id:
        return

    logger.info("GUI source at %s has changed — rebuilding %s", source, bundle.name)
    try:
        build_bundle(source, bundle, sidecar=sidecar)
    except (CompilerError, OSError) as exc:
        # A checkout that cannot compile still starts, on whatever bundle is
        # already there. Losing the auto-rebuild is an inconvenience; failing to
        # launch over it would not be.
        logger.warning("Could not rebuild the GUI bundle: %s", exc)


def _installed_record(path: Path) -> dict:
    """Inspect an installed bundle, returning ``{}`` if it cannot be read."""
    try:
        return read_records(path).build
    except BundleError as exc:
        logger.warning("Installed bundle %s is unreadable: %s", path, exc)
        return {}


def _install(bundle: Path, target: Path) -> None:
    """Replace *target* with *bundle*, atomically.

    Written beside the destination and renamed over it, so a launch interrupted
    mid-copy leaves the previous bundle intact rather than a truncated file that
    will not mount.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        shutil.copyfile(bundle, temporary)
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def sweep_caches(index: dict) -> None:
    """Delete compiled-QML caches belonging to builds nobody has installed.

    The cache is keyed by build id, so an upgrade orphans the previous build's
    folder. Sweeping against the index rather than against one known-old id also
    clears up after a third-party bundle that has been removed by hand.
    """
    root = gui_cache_root()
    if not root.is_dir():
        return
    live = {
        str(entry.get("build_id"))
        for entry in index.get("guis", {}).values()
        if isinstance(entry, dict) and entry.get("build_id")
    }
    for child in root.iterdir():
        if not child.is_dir() or child.name in live:
            continue
        try:
            shutil.rmtree(child)
        except OSError as exc:
            logger.warning("Could not remove stale QML cache %s: %s", child, exc)
            continue
        logger.info("Removed stale QML cache for build %s", child.name)


def _migrate_loose_install(name: str) -> None:
    """Set aside a pre-``.kpi`` install directory, once.

    ``<vault>/.data/gui/<name>/`` used to be the package: loose QML, readable and
    forkable. Nothing loads a directory any more, so leaving it would only be
    confusing — but it may hold edits somebody made, so it is renamed rather
    than deleted and the log says where it went.
    """
    directory = gui_root() / name
    if not directory.is_dir():
        return
    backup = directory.with_name(directory.name + BACKUP_SUFFIX)
    try:
        if backup.exists():
            shutil.rmtree(backup)
        directory.rename(backup)
    except OSError as exc:
        logger.warning("Could not set aside the old GUI folder %s: %s", directory, exc)
        return
    logger.info(
        "GUIs are single .kpi files now; your old '%s' folder was moved to %s",
        name, backup,
    )


def ensure_official_gui_installed(name: str = OFFICIAL_GUI_NAME) -> Path | None:
    """Seed or refresh the official GUI bundle in the vault's gui folder.

    Returns the installed bundle, or ``None`` if nothing could be installed — in
    which case the resolver falls back to the copy inside the application, so a
    failure here is never fatal.
    """
    _migrate_loose_install(name)
    _rebuild_from_source(name)

    bundle = bundled_gui_bundle(name)
    if not bundle.is_file():
        logger.warning("No GUI bundle shipped at %s — nothing to install", bundle)
        return None

    shipped = _shipped_record(name)
    build_id = str(shipped.get("build_id") or "")
    if not build_id:
        logger.warning("Shipped GUI '%s' does not say what build it is", name)
        return None

    target = gui_bundle_path(name)
    index = read_index()
    entry = index["guis"].get(name)

    try:
        if target.is_file():
            # The fast path, and the one taken on every launch after the first:
            # the index says which build is installed and the file still looks
            # exactly as it did when that was written.
            if entry_matches(target, entry) and entry.get("build_id") == build_id:
                return target

            # The file is there but the index cannot vouch for it — deleted,
            # from an older format, or touched. Ask the bundle itself before
            # copying 400 KB for nothing.
            installed = _installed_record(target)
            if installed.get("build_id") == build_id:
                index["guis"][name] = index_entry(target, installed)
                write_index(index)
                sweep_caches(index)
                return target

        _install(bundle, target)
        index["guis"][name] = index_entry(target, shipped)
        write_index(index)
        sweep_caches(index)
        logger.info(
            "Installed GUI '%s' %s (build %s) to %s",
            name, shipped.get("version", "?"), build_id, target,
        )
        return target

    except OSError as exc:
        logger.warning("Could not install GUI '%s' to %s: %s", name, target, exc)
        return target if target.is_file() else None


__all__ = [
    "INDEX_VERSION",
    "INDEX_FILENAME",
    "CACHE_DIRNAME",
    "ensure_official_gui_installed",
    "entry_matches",
    "index_entry",
    "read_index",
    "sweep_caches",
    "write_index",
]
