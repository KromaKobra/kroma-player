"""Reading GitHub's answer, and refusing to be confused by it.

**No test here touches the network.** ``urlopen`` is monkeypatched to serve
canned bytes, the way :mod:`tests.test_backup` fakes a filesystem — a suite that
reached out would fail on an aeroplane, fail in a sandbox, and start failing on
the day somebody publishes a release.

Two things are being checked, and the second is the one that matters. The first
is that a well-formed release parses. The second is that every malformed,
missing, hostile or merely unfamiliar answer produces ``None`` and raises
nothing: this runs on a background thread at launch, and an exception escaping
it would take out the window over a captive portal.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.update import release as rel  # noqa: E402
from app.update.install_kind import InstallKind  # noqa: E402

APPIMAGE = "kroma-player-1.2.0-x86_64.AppImage"
INSTALLER = "KromaPlayerSetup-1.2.0-x86_64.exe"
DIGEST = "a" * 64


def _api(tag: str = "v1.2.0", **overrides) -> dict:
    payload = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "body": "## Highlights\n\nKroma updates itself now.",
        "published_at": "2026-09-08T12:00:00Z",
        "html_url": f"https://github.com/{rel.REPO}/releases/tag/{tag}",
        "assets": [
            {"name": INSTALLER, "size": 118293504,
             "browser_download_url": f"https://example.invalid/{INSTALLER}"},
            {"name": APPIMAGE, "size": 231735296,
             "browser_download_url": f"https://example.invalid/{APPIMAGE}"},
            {"name": "release.json", "size": 800,
             "browser_download_url": "https://example.invalid/release.json"},
        ],
    }
    payload.update(overrides)
    return payload


def _manifest(**overrides) -> dict:
    payload = {
        "version": "1.2.0",
        "vault_version": 1,
        "gui_api_version": 11,
        "min_gui_api_version": 2,
        "assets": [
            {"name": INSTALLER, "kind": "windows_installer", "arch": "x86_64",
             "size": 118293504, "sha256": DIGEST},
            {"name": APPIMAGE, "kind": "appimage", "arch": "x86_64",
             "size": 231735296, "sha256": DIGEST.upper()},
        ],
    }
    payload.update(overrides)
    return payload


class _Response(io.BytesIO):
    """Just enough of an ``http.client.HTTPResponse`` for a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _serve(monkeypatch, routes: dict[str, object]):
    """Answer each URL from *routes*: bytes are served, exceptions are raised.

    Keyed by a substring rather than the whole URL so a test can say "the
    manifest" without repeating the host.
    """
    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        url = request.full_url if hasattr(request, "full_url") else str(request)
        for fragment, answer in routes.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, (dict, list)):
                    return _Response(json.dumps(answer).encode("utf-8"))
                if isinstance(answer, str):
                    return _Response(answer.encode("utf-8"))
                return _Response(answer)
        raise AssertionError(f"no canned answer for {url}")

    monkeypatch.setattr(rel.urllib.request, "urlopen", fake_urlopen)


def _both(monkeypatch, api: dict | None = None, manifest: dict | None = None):
    _serve(
        monkeypatch,
        {
            "api.github.com": api if api is not None else _api(),
            "release.json": manifest if manifest is not None else _manifest(),
        },
    )


# ---------------------------------------------------------------------------
# normalise_tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag,expected", [
    ("v1.2.0", "1.2.0"),
    ("1.2.0", "1.2.0"),
    ("1.2", "1.2"),
    ("v10.20.30", "10.20.30"),
    ("  v1.2.0  ", "1.2.0"),
])
def test_a_plain_version_tag_is_accepted(tag, expected):
    assert rel.normalise_tag(tag) == expected


@pytest.mark.parametrize("tag", [
    "BETA-v0.1.0",   # this repository carries two of these
    "1.2.0-rc1",
    "v1.2.0-beta.1",
    "nightly",
    "v1",            # one component says nothing about the other two
    "v1.2.3.4",
    "",
    "release-1.2.0",
])
def test_anything_else_is_refused(tag):
    """Refused rather than parsed, and that is the whole reason this exists.

    ``version_tuple()`` strips non-digits per segment, so it reads
    "BETA-v0.1.0" as (0, 1, 0) and "1.2.0-beta.1" as (1, 2, 0, 1) — which sorts
    *above* the final 1.2.0. A prerelease that outranks the release it precedes
    would offer every user a downgrade dressed as an upgrade.
    """
    assert rel.normalise_tag(tag) == ""


# ---------------------------------------------------------------------------
# is_newer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("candidate,current,expected", [
    ("1.2.1", "1.2.0", True),
    ("1.10.0", "1.9.0", True),     # not a string comparison
    ("2.0.0", "1.99.99", True),
    ("1.2.0", "1.2.0", False),     # strictly greater: equal is not newer
    ("1.1.0", "1.2.0", False),     # and never a downgrade
    ("1.2", "1.2.0", False),       # padded, so these are the same release
    ("1.2.0", "1.2", False),
    ("1.3", "1.2.9", True),
    ("", "1.2.0", False),
    ("1.2.0", "", False),
])
def test_version_ordering(candidate, current, expected):
    assert rel.is_newer(candidate, current) is expected


# ---------------------------------------------------------------------------
# fetch_latest_release
# ---------------------------------------------------------------------------


def test_a_well_formed_release_parses(monkeypatch):
    _both(monkeypatch)
    release = rel.fetch_latest_release()

    assert release is not None
    assert release.version == "1.2.0"
    assert release.tag == "v1.2.0"
    assert release.vault_version == 1
    assert "Highlights" in release.notes
    assert release.published_at.startswith("2026-09-08")
    assert len(release.assets) == 2


def test_the_schema_version_is_read_from_the_manifest(monkeypatch):
    """What willMigrate compares: the database is the part of a vault that
    actually moves between releases."""
    _both(monkeypatch, manifest=_manifest(schema_version=9, versions={"schema": 4}))
    assert rel.fetch_latest_release().schema_version == 9


def test_a_manifest_from_before_the_field_falls_back_to_the_registry(monkeypatch):
    """Every manifest has carried the whole version registry under `versions`,
    and `schema` has always been in it — so an older release still says
    whether it migrates the database."""
    _both(monkeypatch, manifest=_manifest(versions={"schema": 7, "vault": 1}))
    assert rel.fetch_latest_release().schema_version == 7


@pytest.mark.parametrize("manifest", [
    _manifest(),
    _manifest(versions="not an object"),
    _manifest(schema_version="7"),
    _manifest(schema_version=True),
])
def test_no_usable_schema_version_reads_as_zero(monkeypatch, manifest):
    """Zero can only under-warn: the comparison is `>`."""
    _both(monkeypatch, manifest=manifest)
    assert rel.fetch_latest_release().schema_version == 0


def test_the_manifests_hashes_reach_the_assets(monkeypatch):
    """The reason release.json exists at all: the API carries no hash."""
    _both(monkeypatch)
    release = rel.fetch_latest_release()

    installer = release.asset_for(InstallKind.WINDOWS_INSTALLER)
    appimage = release.asset_for(InstallKind.APPIMAGE)

    assert installer is not None and appimage is not None
    assert installer.sha256 == DIGEST
    # Lower-cased on the way in, so the comparison in download.py never has to
    # think about it.
    assert appimage.sha256 == DIGEST
    assert installer.url.endswith(INSTALLER)
    assert appimage.size == 231735296


def test_an_install_kind_the_release_does_not_ship_is_none(monkeypatch):
    _both(monkeypatch, manifest=_manifest(assets=[
        {"name": APPIMAGE, "kind": "appimage", "arch": "x86_64",
         "size": 231735296, "sha256": DIGEST},
    ]))
    release = rel.fetch_latest_release()

    assert release.asset_for(InstallKind.APPIMAGE) is not None
    assert release.asset_for(InstallKind.WINDOWS_INSTALLER) is None


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_a_draft_or_prerelease_is_invisible(monkeypatch, flag):
    """The entire stable-channel policy, in one condition.

    A tag pushed for testing and published as a prerelease reaches nobody, with
    nothing to configure and no way for a user to land on a channel they did not
    choose.
    """
    _both(monkeypatch, api=_api(**{flag: True}))
    assert rel.fetch_latest_release() is None


def test_a_release_with_no_manifest_is_not_offered(monkeypatch):
    """An update that cannot be verified is not an update."""
    api = _api()
    api["assets"] = [a for a in api["assets"] if a["name"] != "release.json"]
    _serve(monkeypatch, {"api.github.com": api})

    assert rel.fetch_latest_release() is None


def test_an_asset_with_no_hash_is_dropped(monkeypatch):
    _both(monkeypatch, manifest=_manifest(assets=[
        {"name": INSTALLER, "kind": "windows_installer", "arch": "x86_64", "size": 1},
        {"name": APPIMAGE, "kind": "appimage", "arch": "x86_64",
         "size": 2, "sha256": DIGEST},
    ]))
    release = rel.fetch_latest_release()

    assert [asset.name for asset in release.assets] == [APPIMAGE]


def test_a_hash_for_a_file_that_is_not_published_is_dropped(monkeypatch):
    """A manifest naming an asset the release does not carry describes a file
    with no URL, which is not something that can be fetched."""
    _both(monkeypatch, manifest=_manifest(assets=[
        {"name": "kroma-player-1.2.0-riscv64.AppImage", "kind": "appimage",
         "arch": "riscv64", "size": 1, "sha256": DIGEST},
    ]))

    assert rel.fetch_latest_release() is None


def test_a_tag_that_is_not_a_version_is_ignored(monkeypatch):
    _both(monkeypatch, api=_api(tag="BETA-v0.1.0"))
    assert rel.fetch_latest_release() is None


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("no route to host"),
    urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, None),
    urllib.error.HTTPError("https://api.github.com", 403, "rate limit exceeded", {}, None),
    TimeoutError("timed out"),
    OSError("connection reset"),
])
def test_every_network_failure_is_silent(monkeypatch, failure):
    """Offline is the ordinary state of a laptop, not an error worth a dialog.

    This runs on a background thread at launch; an exception escaping it takes
    the window with it.
    """
    _serve(monkeypatch, {"api.github.com": failure})
    assert rel.fetch_latest_release() is None


def test_a_malformed_body_is_silent(monkeypatch):
    _serve(monkeypatch, {"api.github.com": "<html>502 Bad Gateway</html>"})
    assert rel.fetch_latest_release() is None


def test_a_malformed_manifest_is_silent(monkeypatch):
    _serve(monkeypatch, {"api.github.com": _api(), "release.json": "not json"})
    assert rel.fetch_latest_release() is None


def test_a_manifest_that_fails_to_download_is_silent(monkeypatch):
    _serve(monkeypatch, {
        "api.github.com": _api(),
        "release.json": urllib.error.URLError("gone"),
    })
    assert rel.fetch_latest_release() is None


def test_an_answer_that_is_not_an_object_is_silent(monkeypatch):
    _serve(monkeypatch, {"api.github.com": ["not", "a", "release"]})
    assert rel.fetch_latest_release() is None


def test_a_huge_body_is_truncated_rather_than_read_forever(monkeypatch):
    """The cap is not tuning — it is what stops a wrong or hostile URL streaming
    into memory until the process dies. Truncated JSON does not parse, so this
    lands on the same silent None as everything else."""
    _serve(monkeypatch, {"api.github.com": b'{"tag_name": "v1.2.0", "pad": "'
                         + b"x" * (rel.MAX_JSON_BYTES + 4096)})
    assert rel.fetch_latest_release() is None


def test_the_request_names_the_application(monkeypatch):
    """GitHub rejects a request with no User-Agent, and the version in it is
    what lets them see which builds are still asking."""
    seen = {}

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        seen.update(request.headers)
        return _Response(json.dumps(_api()).encode("utf-8"))

    monkeypatch.setattr(rel.urllib.request, "urlopen", fake_urlopen)
    rel.fetch_latest_release()

    # urllib title-cases header names on the way in.
    assert seen.get("User-agent", "").startswith("KromaPlayer/")
    assert seen.get("X-github-api-version") == "2022-11-28"
