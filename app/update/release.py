"""What exists upstream: the newest published release, and what it ships.

Two requests, both anonymous and both small. The first asks GitHub for the
latest release; the second fetches the ``release.json`` published beside its
assets, because the API describes an asset's name, size and URL but **not its
hash**, and a download that cannot be verified is not one this application will
offer. A release without that manifest is treated as no release at all.

**Stable channel only.** A draft or a prerelease is skipped, and that single
condition *is* the whole channel policy — a tag pushed for testing and published
as a prerelease is invisible to every copy of the application, with nothing to
configure and no way for a user to end up on a channel they did not choose.

**Anonymous, and rate-limited accordingly.** GitHub allows 60 requests an hour
per IP without a token. Automatic checks are gated to once a day by the view
model; a manual click bypasses that gate, because somebody who pressed the
button is owed an answer. Without the gate a shared-IP office would trip the
limit and every user there would get a silent failure.

Every failure path returns ``None`` and logs at debug, per
``docs/architecture/cross-cutting/degradation.md``: no network is the ordinary
state of a laptop, not an error worth a dialog.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.gui.manifest import version_tuple
from app.update.install_kind import InstallKind
from app.versions import APP_VERSION

logger = logging.getLogger(__name__)

# The repository releases are published from. It must be the repository the
# release workflow runs in — the guard job in .github/workflows/release.yml
# compares it with $GITHUB_REPOSITORY and refuses to publish if they differ,
# because a build pointed at any other repository asks the wrong one for
# updates for the rest of its life. packaging/kroma-player.iss defaults its
# AppRepo to the same string, and tests/test_update_apply.py holds them equal.
REPO = "KromaKobra/kroma-player"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

# Matches app/importer/stash.py, which is the app's other network read.
TIMEOUT = 15

# A release's JSON is a few kilobytes and its manifest a few hundred bytes. The
# cap is not tuning — it is what stops a wrong or hostile URL streaming into
# memory until the process dies.
MAX_JSON_BYTES = 1 << 20

MANIFEST_ASSET = "release.json"

# A tag this application will consider: optional `v`, then two or three dotted
# numbers, and nothing else. Everything outside that shape is refused rather
# than parsed — see `normalise_tag`.
TAG_PATTERN = re.compile(r"^v?(\d+(?:\.\d+){1,2})$")


@dataclass(frozen=True)
class Asset:
    """One downloadable file from a release, with the hash to check it against."""

    name: str
    url: str
    kind: str
    arch: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    """A published release, merged from the API and its own ``release.json``."""

    version: str
    tag: str
    notes: str
    published_at: str
    url: str
    vault_version: int
    assets: tuple[Asset, ...]
    # The library database's schema version the release ships. Zero when the
    # manifest predates the field, which — like vault_version — can only ever
    # under-warn: the comparison in the view model is `>`.
    schema_version: int = 0

    def asset_for(self, kind: InstallKind) -> Asset | None:
        """The artifact matching how this copy was installed, if it shipped one.

        Matched on the ``kind`` recorded in the manifest rather than inferred
        from a file extension: ``.exe`` says nothing about whether a file is an
        installer, and a release that gains a second artifact of some new shape
        should not start being mistaken for one of these two.
        """
        return next((asset for asset in self.assets if asset.kind == kind.value), None)


def normalise_tag(tag: str) -> str:
    """Return the bare dotted version in *tag*, or ``""`` if it is not one.

    Strict on purpose. ``version_tuple()`` — the comparator this repository
    already has — strips non-digits per segment, so it will happily turn
    ``"BETA-v0.1.0"`` into ``(0, 1, 0)`` and ``"1.2.0-rc1"`` into
    ``(1, 2, 0, 1)``, which sorts *above* the final ``1.2.0``. This repository
    carries ``BETA-v0.0.0`` and ``BETA-v0.1.0`` tags today, so that is not a
    hypothetical. Refusing anything that is not plain digits and dots is what
    makes the comparison below safe to leave in the existing comparator's hands.
    """
    match = TAG_PATTERN.match((tag or "").strip())
    return match.group(1) if match else ""


def _padded(version: str) -> tuple[int, ...]:
    """*version* as a three-component tuple.

    ``version_tuple()`` pads nothing, so ``"1.2"`` becomes ``(1, 2)`` and sorts
    *below* ``(1, 2, 0)`` — the same release comparing as older than itself.
    Padding here rather than changing ``version_tuple()`` keeps one comparator
    in the codebase: that function is what orders GUI packages, and widening its
    contract to suit tags would be changing the wrong thing.
    """
    parts = list(version_tuple(version))[:3]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """Whether *candidate* is a strictly later version than *current*.

    Strictly greater, with no version picker anywhere in the feature: the
    updater never offers a downgrade. It could not honour one safely — a vault
    migrated by a newer build is refused outright by :mod:`app.vault.startup`,
    so "install the older one" is a door that does not open from this side.
    """
    if not candidate or not current:
        return False
    return _padded(candidate) > _padded(current)


def _get(url: str, accept: str) -> bytes | None:
    """Fetch at most ``MAX_JSON_BYTES`` from *url*, or ``None`` on any failure.

    The User-Agent is not optional: GitHub rejects a request without one, and
    naming the application and its version is what lets them see which builds
    are still asking.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"KromaPlayer/{APP_VERSION}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            # One byte over the cap, so a body sitting exactly on it is not
            # mistaken for a truncated read.
            return response.read(MAX_JSON_BYTES + 1)[:MAX_JSON_BYTES]
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Could not read %s: %s", url, exc)
        return None


def _json(url: str, accept: str) -> Any | None:
    raw = _get(url, accept)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.debug("Malformed JSON from %s: %s", url, exc)
        return None


def _as_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _merge_assets(api_assets: Any, manifest_assets: Any) -> tuple[Asset, ...]:
    """Join the API's download URLs to the manifest's hashes, by filename.

    The filename is the only key the two sides share, and it is a good one: it
    is unique within a release by construction, since they are all files in one
    directory. An asset in one list and not the other is dropped — a URL with no
    hash cannot be verified, and a hash with no URL cannot be fetched.
    """
    urls: dict[str, str] = {}
    sizes: dict[str, int] = {}
    if isinstance(api_assets, list):
        for entry in api_assets:
            if not isinstance(entry, dict):
                continue
            name = _as_text(entry.get("name"))
            url = _as_text(entry.get("browser_download_url"))
            if name and url:
                urls[name] = url
                sizes[name] = _as_int(entry.get("size"))

    assets: list[Asset] = []
    if isinstance(manifest_assets, list):
        for entry in manifest_assets:
            if not isinstance(entry, dict):
                continue
            name = _as_text(entry.get("name"))
            digest = _as_text(entry.get("sha256"))
            if not name or not digest or name not in urls:
                continue
            assets.append(
                Asset(
                    name=name,
                    url=urls[name],
                    kind=_as_text(entry.get("kind")),
                    arch=_as_text(entry.get("arch")),
                    # The manifest's size is authoritative: it was measured over
                    # the same bytes as the hash beside it.
                    size=_as_int(entry.get("size")) or sizes.get(name, 0),
                    sha256=digest.lower(),
                )
            )
    return tuple(assets)


def fetch_latest_release() -> Release | None:
    """Return the newest stable release, or ``None``.

    ``None`` covers every uninteresting outcome and they are deliberately not
    distinguished: offline, rate-limited, a malformed body, a release with no
    manifest, a tag that is not a version. None of them is something the user
    can act on, and a check that has nothing to report should say nothing.
    """
    payload = _json(API_URL, "application/vnd.github+json")
    if not isinstance(payload, dict):
        return None

    # The stable-channel policy, entire. `/releases/latest` already excludes
    # drafts, but it is checked anyway: the endpoint's behaviour is not this
    # application's to depend on, and the cost is one condition.
    if payload.get("draft") or payload.get("prerelease"):
        logger.debug("Newest release is a draft or prerelease — ignoring it")
        return None

    tag = _as_text(payload.get("tag_name"))
    version = normalise_tag(tag)
    if not version:
        logger.debug("Ignoring release tagged %r — not a plain version", tag)
        return None

    manifest_url = next(
        (
            _as_text(entry.get("browser_download_url"))
            for entry in payload.get("assets", []) or []
            if isinstance(entry, dict) and entry.get("name") == MANIFEST_ASSET
        ),
        "",
    )
    if not manifest_url:
        logger.debug("Release %s publishes no %s — not offering it", tag, MANIFEST_ASSET)
        return None

    manifest = _json(manifest_url, "application/json")
    if not isinstance(manifest, dict):
        return None

    assets = _merge_assets(payload.get("assets"), manifest.get("assets"))
    if not assets:
        logger.debug("Release %s has no verifiable assets", tag)
        return None

    return Release(
        version=version,
        tag=tag,
        notes=_as_text(payload.get("body")),
        published_at=_as_text(payload.get("published_at")),
        url=_as_text(payload.get("html_url")) or RELEASES_PAGE,
        # Zero when the manifest omits it, which reads as "no migration" —
        # ``willMigrateVault`` compares it against this build's VAULT_VERSION
        # with `>`, so an absent value can only ever under-warn about a release
        # that predates the field, never over-warn about one that has it.
        vault_version=_as_int(manifest.get("vault_version")),
        assets=assets,
        schema_version=_schema_version(manifest),
    )


def _schema_version(manifest: dict) -> int:
    """The schema version a manifest declares, or ``0``.

    The top-level field first. A manifest written before it existed still
    carries the whole version registry under ``versions``, which has always
    included ``schema``, so a release from then is not mistaken for one that
    migrates nothing.
    """
    declared = _as_int(manifest.get("schema_version"))
    if declared:
        return declared
    registry = manifest.get("versions")
    return _as_int(registry.get("schema")) if isinstance(registry, dict) else 0
