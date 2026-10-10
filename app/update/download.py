"""Fetching a release asset, and proving it is the one that was published.

The hash is the only integrity control in this design, and it is enough because
of where it comes from: TLS to GitHub is the trust anchor, ``release.json`` is
published in the same release by the same builder as the artifact it describes,
and this module refuses to hand back a file whose digest does not match it. A
truncated download and a tampered one fail identically and fail *closed*.

**Verified before the file is run or moved, never after.** There is no point at
which an unverified byte reaches an installer or lands on top of the running
AppImage.

Nothing half-written is ever visible: the download goes to ``<name>.part`` and
is renamed into place only once the digest matches — the discipline
:mod:`app.backup.apply` already documents.

Nothing is left behind for good either. An installer cannot delete itself — it
is still running when the app it installed starts — so :func:`sweep_downloads`
clears installers for versions this build already is, at the next launch.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from app.update.install_kind import appimage_path
from app.update.release import TIMEOUT, Asset, is_newer
from app.versions import APP_VERSION

logger = logging.getLogger(__name__)

# 256 KB. Big enough that a 215 MB AppImage is not a million syscalls, small
# enough that cancelling is felt immediately — the cancel flag is checked once
# per chunk, so this is also the resolution of the Cancel button.
CHUNK = 256 * 1024

# A hard ceiling on what will be written to disk, whatever the server claims.
# The largest artifact today is around 230 MB; 1 GB leaves room for the build to
# grow several times over and still stops a broken or hostile response filling
# the user's disk.
MAX_ASSET_BYTES = 1 << 30

DOWNLOAD_DIR_NAME = "kroma-update"

# Where Inno writes what it did during an update — /LOG in apply.py. Beside the
# installer, and kept by the sweep: it is what "Show install log" opens after
# an update that did not take.
INSTALL_LOG_NAME = "install.log"

# The installer's name, as packaging/kroma-player.iss writes it
# (OutputBaseFilename). The version in it is what the sweep compares.
INSTALLER_NAME = re.compile(r"^KromaPlayerSetup-(\d+(?:\.\d+){1,2})-.*\.exe$", re.IGNORECASE)

# A `.part` older than this belongs to a download that is not coming back.
# Younger ones are left alone in case another copy of Kroma is mid-download.
STALE_PART_SECONDS = 24 * 3600

# Antivirus scanners open a freshly written executable to look at it, and on
# Windows an open file cannot be renamed. The scan takes moments; this waits
# up to two seconds for it before calling the download a failure.
RENAME_ATTEMPTS = 10
RENAME_DELAY = 0.2


class UpdateError(Exception):
    """A download that cannot be trusted, or could not be completed.

    The message is written for the user: it is shown in the pane verbatim, the
    way :class:`app.backup.plan.BackupError`'s is.
    """


class UpdateCancelled(Exception):
    """The user stopped the download. Not a failure; there is nothing to report
    beyond the fact that it stopped."""


def download_dir() -> Path:
    """Where the downloaded artifact should be written.

    For an AppImage, **the directory holding ``$APPIMAGE``** — so the final
    ``os.replace()`` is a rename within one filesystem rather than a copy across
    two. That matters more than it sounds: ``os.replace`` across devices raises,
    and the fallback would be to copy a 230 MB file over the running executable
    non-atomically, which is precisely the "window where the app does not exist"
    this design exists to avoid.

    Anywhere else — and for an AppImage whose own directory is not writable, a
    read-only ``/opt`` drop — the system temp directory, and the copy is
    accepted. The Windows installer is written there unconditionally: it is run
    once, it has no business sitting in the install folder, and
    :func:`sweep_downloads` deletes it at the first launch of the version it
    installed.
    """
    image = appimage_path()
    if image is not None:
        directory = image.parent
        if os.access(directory, os.W_OK):
            return directory
        logger.debug("%s is not writable — downloading to the temp directory", directory)

    return temp_download_dir()


def temp_download_dir() -> Path:
    """The temp-directory download folder, whatever this install kind uses.

    The only folder :func:`sweep_downloads` ever looks in. An AppImage's own
    directory is never swept: it is the user's, and it holds the app.
    """
    return Path(tempfile.gettempdir()) / DOWNLOAD_DIR_NAME


def install_log_path() -> Path:
    """Where the Windows installer is told to write its log."""
    return temp_download_dir() / INSTALL_LOG_NAME


def sweep_downloads(directory: Path | None = None) -> int:
    """Delete installers this build has outgrown, and dead partial downloads.

    Removes ``KromaPlayerSetup-<v>-*.exe`` for every ``v`` that is not newer
    than ``APP_VERSION`` — the one just installed, and anything older — and
    ``.part`` files older than a day. Keeps the install log, and an installer
    for a version *newer* than this one, which is a download the user has not
    installed yet. A file Windows will not let go of is skipped; the next launch
    tries again. Returns how many files went.
    """
    folder = directory or temp_download_dir()
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0

    removed = 0
    now = time.time()
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            match = INSTALLER_NAME.match(entry.name)
            if match:
                if is_newer(match.group(1), APP_VERSION):
                    continue
            elif entry.name.endswith(".part"):
                if now - entry.stat().st_mtime < STALE_PART_SECONDS:
                    continue
            else:
                continue
            entry.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("Could not remove %s: %s", entry, exc)
    if removed:
        logger.info("Removed %d old update download(s) from %s", removed, folder)
    return removed


def _replace_with_retry(source: Path, target: Path) -> None:
    """``os.replace``, retried while something briefly holds the file open."""
    for attempt in range(RENAME_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(RENAME_DELAY)


def download_asset(
    asset: Asset,
    destination: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Fetch *asset*, verify it, and return the path it was written to.

    *destination* defaults to :func:`download_dir`. *on_progress* is called with
    ``(bytes_so_far, total_bytes)``; ``total_bytes`` is the size from the
    manifest, so the progress bar is driven by what the publisher measured
    rather than by a header the server is free to omit.

    Raises :class:`UpdateCancelled` if *should_cancel* becomes true, and
    :class:`UpdateError` on anything else — including a digest mismatch, which
    reports both hashes because the whole point of the number is being able to
    compare it.
    """
    directory = destination or download_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError(f"Could not create {directory}: {exc}") from exc

    final = directory / asset.name
    partial = final.with_name(final.name + ".part")
    cancelled = should_cancel or (lambda: False)

    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(
        asset.url, headers={"User-Agent": "KromaPlayer", "Accept": "application/octet-stream"}
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            with partial.open("wb") as handle:
                while True:
                    if cancelled():
                        raise UpdateCancelled
                    block = response.read(CHUNK)
                    if not block:
                        break
                    received += len(block)
                    if received > MAX_ASSET_BYTES:
                        raise UpdateError(
                            f"{asset.name} is larger than {MAX_ASSET_BYTES // (1 << 20)} MB "
                            "— refusing to keep downloading it."
                        )
                    handle.write(block)
                    digest.update(block)
                    if on_progress is not None:
                        on_progress(received, asset.size or received)
    except (UpdateCancelled, UpdateError):
        _discard(partial)
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        _discard(partial)
        raise UpdateError(f"Could not download {asset.name}: {exc}") from exc

    actual = digest.hexdigest()
    if actual != asset.sha256:
        _discard(partial)
        raise UpdateError(
            f"{asset.name} did not match the checksum published with the release "
            f"(expected {asset.sha256}, got {actual}). It was not installed."
        )

    # Only now, and this ordering is the point of the whole module: the name
    # without `.part` on it means verified.
    try:
        _replace_with_retry(partial, final)
    except OSError as exc:
        _discard(partial)
        raise UpdateError(f"Could not finish writing {final}: {exc}") from exc

    logger.info("Downloaded %s (%s bytes) to %s", asset.name, received, final)
    return final


def verify(path: Path, expected_sha256: str) -> bool:
    """Whether *path* still hashes to *expected_sha256*.

    Used before the file is handed to an installer or renamed over the running
    executable. It has been sitting on disk since the download — possibly in a
    world-writable temp directory, possibly for as long as the user left the
    pane open — and re-reading 200 MB costs a second against an irreversible
    action.
    """
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                digest.update(block)
    except OSError as exc:
        logger.debug("Could not re-read %s: %s", path, exc)
        return False
    return digest.hexdigest() == (expected_sha256 or "").lower()


def _discard(path: Path) -> None:
    """Remove a partial download, ignoring whatever goes wrong doing so.

    A ``.part`` is always garbage — it exists only between the first byte and
    the rename — so failing to remove one is not worth reporting over whatever
    error brought us here.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove %s: %s", path, exc)
