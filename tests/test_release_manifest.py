"""``scripts/release-manifest``, and the updater reading what it writes.

The two ends of the one contract the updater cannot work without. The script
runs once per release, on a CI runner, after both builds; the parser runs on
every user's machine for the life of the build. Nothing else checks that they
agree, and the failure is silent — a release whose installer the parser does not
recognise reads, to every installed copy, as "nothing for this system".

So the files here are named exactly as ``.github/workflows/release.yml`` names
its output, the script is run on them, and its ``release.json`` is served to
:func:`app.update.release.fetch_latest_release` the way GitHub would serve it.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database.schema import SCHEMA_VERSION  # noqa: E402
from app.update import release as rel  # noqa: E402
from app.update.install_kind import InstallKind  # noqa: E402
from app.versions import APP_VERSION, VAULT_VERSION  # noqa: E402

VERSION = APP_VERSION

# The names release.yml gives the four artifacts.
CI_NAMES = {
    f"KromaPlayerSetup-{VERSION}-x86_64.exe": "windows_installer",
    f"kroma-player-{VERSION}-windows-x86_64.zip": "windows_portable",
    f"kroma-player-{VERSION}-x86_64.AppImage": "appimage",
    f"kroma-player-{VERSION}-linux-x86_64.tar.gz": "linux_portable",
}


def _load_script():
    """Import the extension-less script as a module."""
    path = REPO_ROOT / "scripts" / "release-manifest"
    loader = importlib.machinery.SourceFileLoader("release_manifest", str(path))
    spec = importlib.util.spec_from_loader("release_manifest", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


@pytest.fixture
def release_dir(tmp_path) -> Path:
    folder = tmp_path / "release"
    folder.mkdir()
    for name in CI_NAMES:
        # Distinct contents, so a hash attached to the wrong file is caught.
        (folder / name).write_bytes(f"stand-in for {name}".encode("utf-8"))
    return folder


def test_every_ci_artifact_is_classified(script, release_dir):
    manifest, _sums = script.build(release_dir)

    kinds = {asset["name"]: asset["kind"] for asset in manifest["assets"]}
    assert kinds == CI_NAMES
    assert {asset["arch"] for asset in manifest["assets"]} == {"x86_64"}


def test_the_manifest_carries_the_versions_the_updater_warns_about(script, release_dir):
    manifest, _sums = script.build(release_dir)

    assert manifest["version"] == APP_VERSION
    assert manifest["vault_version"] == VAULT_VERSION
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["versions"]["schema"] == SCHEMA_VERSION


def test_the_hashes_and_sums_describe_the_files(script, release_dir):
    manifest, sums = script.build(release_dir)

    for asset in manifest["assets"]:
        data = (release_dir / asset["name"]).read_bytes()
        assert asset["sha256"] == hashlib.sha256(data).hexdigest()
        assert asset["size"] == len(data)
        assert f"{asset['sha256']}  {asset['name']}" in sums.splitlines()


def test_main_writes_both_files_and_ignores_its_own_output(script, release_dir):
    assert script.main([str(release_dir)]) == 0
    first = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))

    # A second run over the same folder must not describe the first run's files.
    assert script.main([str(release_dir)]) == 0
    second = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))

    assert (release_dir / "SHA256SUMS").is_file()
    names = {asset["name"] for asset in second["assets"]}
    assert "release.json" not in names and "SHA256SUMS" not in names
    assert first["assets"] == second["assets"]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def test_the_updater_reads_what_the_script_wrote(script, release_dir, monkeypatch):
    """The round trip: the script's release.json, served beside the assets the
    way GitHub's API lists them, parsed by the app."""
    assert script.main([str(release_dir)]) == 0
    manifest_bytes = (release_dir / "release.json").read_bytes()

    api = {
        "tag_name": f"v{VERSION}",
        "draft": False,
        "prerelease": False,
        "body": "## Highlights",
        "published_at": "2026-10-09T12:00:00Z",
        "html_url": f"https://github.com/{rel.REPO}/releases/tag/v{VERSION}",
        "assets": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "browser_download_url": f"https://example.invalid/{path.name}",
            }
            for path in sorted(release_dir.iterdir())
        ],
    }

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        url = request.full_url
        if "api.github.com" in url:
            return _Response(json.dumps(api).encode("utf-8"))
        if url.endswith("/release.json"):
            return _Response(manifest_bytes)
        raise AssertionError(f"unexpected request for {url}")

    monkeypatch.setattr(rel.urllib.request, "urlopen", fake_urlopen)

    release = rel.fetch_latest_release()

    assert release is not None
    assert release.version == VERSION
    assert release.schema_version == SCHEMA_VERSION
    installer = release.asset_for(InstallKind.WINDOWS_INSTALLER)
    assert installer is not None, "every installed Windows copy would see nothing to install"
    expected = release_dir / f"KromaPlayerSetup-{VERSION}-x86_64.exe"
    assert installer.sha256 == hashlib.sha256(expected.read_bytes()).hexdigest()
    assert installer.size == expected.stat().st_size
    assert release.asset_for(InstallKind.APPIMAGE) is not None
