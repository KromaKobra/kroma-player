"""Fetching an asset, and every way that is allowed to go wrong.

The property under test throughout is that a download either produces a file
whose digest matches what the release published, or produces nothing at all.
There is no third state — no partially written artifact left where the next run
might find it, and no file at the final name that was never checked.

No network: ``urlopen`` serves bytes from memory.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.update import download as dl  # noqa: E402
from app.update.release import Asset  # noqa: E402

PAYLOAD = b"kroma" * 20000    # 100 KB, so it spans no chunk boundary by accident
NAME = "kroma-player-1.2.0-x86_64.AppImage"


def _asset(payload: bytes = PAYLOAD, *, sha256: str | None = None) -> Asset:
    return Asset(
        name=NAME,
        url=f"https://example.invalid/{NAME}",
        kind="appimage",
        arch="x86_64",
        size=len(payload),
        sha256=sha256 if sha256 is not None else hashlib.sha256(payload).hexdigest(),
    )


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


@pytest.fixture
def dest(tmp_path) -> Path:
    """A directory holding nothing but this test's downloads.

    Not ``tmp_path`` itself: the autouse ``isolated_library_root`` fixture in
    conftest puts a throwaway vault in there, and half of these tests assert
    that a failed download left the directory *empty*.
    """
    return tmp_path / "downloads"


def _serve(monkeypatch, payload: bytes | Exception):
    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_matching_download_lands_under_its_own_name(monkeypatch, dest):
    _serve(monkeypatch, PAYLOAD)
    path = dl.download_asset(_asset(), dest)

    assert path == dest / NAME
    assert path.read_bytes() == PAYLOAD
    assert list(dest.glob("*.part")) == [], "the .part must be gone"


def test_progress_is_reported_against_the_published_size(monkeypatch, dest):
    """Driven by what the publisher measured, not by a header the server is free
    to omit — a progress bar with no total is worse than none."""
    _serve(monkeypatch, PAYLOAD)
    seen: list[tuple[int, int]] = []
    dl.download_asset(_asset(), dest, on_progress=lambda a, b: seen.append((a, b)))

    assert seen, "nothing reported progress"
    assert all(total == len(PAYLOAD) for _, total in seen)
    assert seen[-1][0] == len(PAYLOAD)
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)


def test_the_directory_is_created_if_it_does_not_exist(monkeypatch, tmp_path):
    _serve(monkeypatch, PAYLOAD)
    target = tmp_path / "kroma-update"
    assert dl.download_asset(_asset(), target).is_file()


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


def test_a_hash_mismatch_raises_and_leaves_nothing_behind(monkeypatch, dest):
    """The only integrity control in the design. A truncated download and a
    tampered one fail identically, and both fail closed."""
    _serve(monkeypatch, PAYLOAD)
    with pytest.raises(dl.UpdateError) as caught:
        dl.download_asset(_asset(sha256="b" * 64), dest)

    assert "b" * 64 in str(caught.value), "the expected digest is in the message"
    assert hashlib.sha256(PAYLOAD).hexdigest() in str(caught.value), "and the actual one"
    assert list(dest.iterdir()) == [], "no .part, and certainly no final file"


def test_cancelling_mid_stream_leaves_nothing_behind(monkeypatch, dest):
    _serve(monkeypatch, b"x" * (dl.CHUNK * 8))
    calls = {"n": 0}

    def cancel_after_two() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(dl.UpdateCancelled):
        dl.download_asset(
            _asset(b"x" * (dl.CHUNK * 8)), dest, should_cancel=cancel_after_two
        )

    assert list(dest.iterdir()) == []


def test_the_size_cap_trips(monkeypatch, dest):
    """Whatever the server claims, this is what will be written to disk."""
    monkeypatch.setattr(dl, "MAX_ASSET_BYTES", dl.CHUNK * 2)
    _serve(monkeypatch, b"x" * (dl.CHUNK * 10))

    with pytest.raises(dl.UpdateError, match="larger than"):
        dl.download_asset(_asset(b"x" * (dl.CHUNK * 10)), dest)

    assert list(dest.iterdir()) == []


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("no route to host"),
    urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, None),
    TimeoutError("timed out"),
    OSError("connection reset"),
])
def test_a_network_failure_becomes_an_update_error(monkeypatch, dest, failure):
    """Unlike a check, a download was explicitly asked for — so it reports
    rather than degrading silently. It still leaves nothing behind."""
    _serve(monkeypatch, failure)
    with pytest.raises(dl.UpdateError):
        dl.download_asset(_asset(), dest)

    assert list(dest.iterdir()) == []


# ---------------------------------------------------------------------------
# Re-verifying before an irreversible step
# ---------------------------------------------------------------------------


def test_verify_accepts_the_file_it_wrote(monkeypatch, dest):
    _serve(monkeypatch, PAYLOAD)
    asset = _asset()
    path = dl.download_asset(asset, dest)

    assert dl.verify(path, asset.sha256)
    assert dl.verify(path, asset.sha256.upper()), "case is not part of a hash"


def test_verify_rejects_a_file_that_changed_on_disk(monkeypatch, dest):
    """It has been sitting in a possibly world-writable temp directory since the
    download, and what happens next is irreversible."""
    _serve(monkeypatch, PAYLOAD)
    asset = _asset()
    path = dl.download_asset(asset, dest)
    path.write_bytes(PAYLOAD + b"tampered")

    assert not dl.verify(path, asset.sha256)


def test_verify_rejects_a_file_that_is_gone(tmp_path):
    assert not dl.verify(tmp_path / "never-existed", "a" * 64)


# ---------------------------------------------------------------------------
# Where the download goes
# ---------------------------------------------------------------------------


def test_an_appimage_downloads_beside_itself(monkeypatch, tmp_path):
    """So the final os.replace() is a rename within one filesystem.

    Across devices it raises, and the fallback would be copying 230 MB over the
    running executable non-atomically — precisely the window this design exists
    to avoid.
    """
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))

    assert dl.download_dir() == tmp_path


def test_an_unwritable_appimage_directory_falls_back_to_temp(monkeypatch, tmp_path):
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setattr(dl.os, "access", lambda *_args, **_kwargs: False)

    assert dl.download_dir().name == dl.DOWNLOAD_DIR_NAME


def test_anything_else_downloads_to_the_temp_directory(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert dl.download_dir().name == dl.DOWNLOAD_DIR_NAME


def test_the_install_log_goes_beside_the_installer(monkeypatch):
    """Where /LOG points, and so where "Show install log" looks."""
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert dl.install_log_path() == dl.download_dir() / dl.INSTALL_LOG_NAME


# ---------------------------------------------------------------------------
# Antivirus, holding the file it just watched being written
# ---------------------------------------------------------------------------


def test_a_briefly_locked_file_is_renamed_once_it_is_let_go(monkeypatch, dest):
    """A scanner opens a fresh executable to look at it, and on Windows an open
    file cannot be renamed. A moment later it can."""
    _serve(monkeypatch, PAYLOAD)
    real_replace = dl.os.replace
    attempts: list[int] = []

    def flaky_replace(source, target):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("The process cannot access the file")
        return real_replace(source, target)

    monkeypatch.setattr(dl.os, "replace", flaky_replace)
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)

    path = dl.download_asset(_asset(), dest)

    assert path.read_bytes() == PAYLOAD
    assert len(attempts) == 3


def test_a_file_that_stays_locked_fails_closed(monkeypatch, dest):
    _serve(monkeypatch, PAYLOAD)

    def refuse(*_args):
        raise PermissionError("The process cannot access the file")

    monkeypatch.setattr(dl.os, "replace", refuse)
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)

    with pytest.raises(dl.UpdateError):
        dl.download_asset(_asset(), dest)
    assert list(dest.iterdir()) == [], "the .part is discarded"


# ---------------------------------------------------------------------------
# Sweeping up after an update
# ---------------------------------------------------------------------------


def _installer(folder: Path, version: str) -> Path:
    path = folder / f"KromaPlayerSetup-{version}-x86_64.exe"
    path.write_bytes(b"MZ")
    return path


def test_the_sweep_removes_installers_this_build_has_outgrown(monkeypatch, tmp_path):
    """The installer cannot delete itself — it is still running when the app it
    installed starts — so the next launch does."""
    monkeypatch.setattr(dl, "APP_VERSION", "1.2.0")
    folder = tmp_path / "kroma-update"
    folder.mkdir()
    current = _installer(folder, "1.2.0")
    older = _installer(folder, "1.1.0")
    newer = _installer(folder, "1.3.0")
    log = folder / dl.INSTALL_LOG_NAME
    log.write_text("Log closed.")
    unrelated = folder / "notes.txt"
    unrelated.write_text("not ours")

    assert dl.sweep_downloads(folder) == 2

    assert not current.exists()
    assert not older.exists()
    assert newer.exists(), "a download the user has not installed yet"
    assert log.exists(), "what Show install log opens"
    assert unrelated.exists(), "only files this module names are touched"


def test_the_sweep_clears_old_partial_downloads_only(monkeypatch, tmp_path):
    folder = tmp_path / "kroma-update"
    folder.mkdir()
    stale = folder / "KromaPlayerSetup-9.9.9-x86_64.exe.part"
    stale.write_bytes(b"half")
    fresh = folder / "kroma-player-9.9.9-x86_64.AppImage.part"
    fresh.write_bytes(b"half")
    day_and_a_bit = 25 * 3600
    old = stale.stat().st_mtime - day_and_a_bit
    os.utime(stale, (old, old))

    assert dl.sweep_downloads(folder) == 1

    assert not stale.exists()
    assert fresh.exists(), "maybe another copy of Kroma is still downloading it"


def test_a_locked_installer_is_skipped_not_fatal(monkeypatch, tmp_path):
    """Windows will not delete a file something holds open. The next launch
    tries again; this one carries on."""
    monkeypatch.setattr(dl, "APP_VERSION", "1.2.0")
    folder = tmp_path / "kroma-update"
    folder.mkdir()
    locked = _installer(folder, "1.1.0")
    free = _installer(folder, "1.0.0")
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == locked:
            raise PermissionError("The process cannot access the file")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert dl.sweep_downloads(folder) == 1
    assert locked.exists()
    assert not free.exists()


def test_the_sweep_never_looks_beside_an_appimage(monkeypatch, tmp_path):
    """That folder is the user's, and it holds the app."""
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    system_temp = tmp_path / "temp"
    (system_temp / dl.DOWNLOAD_DIR_NAME).mkdir(parents=True)
    monkeypatch.setattr(dl.tempfile, "gettempdir", lambda: str(system_temp))
    beside = _installer(tmp_path, "0.0.1")
    in_temp = _installer(system_temp / dl.DOWNLOAD_DIR_NAME, "0.0.1")

    dl.sweep_downloads()

    assert beside.exists()
    assert not in_temp.exists()


def test_sweeping_a_folder_that_does_not_exist_is_nothing(tmp_path):
    assert dl.sweep_downloads(tmp_path / "never-made") == 0
