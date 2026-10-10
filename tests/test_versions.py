"""The rules the version registry is supposed to follow.

Two of them, and they are the whole model:

- a contract's supported range is never empty and never inverted, so
  ``MIN_X <= X`` for every one of them;
- a GUI is loadable exactly while its ``api_version`` is inside that range, and
  outside it the reason says which way it went wrong and what to do.

Both are the kind of thing that is obviously true right up until somebody bumps
one constant and not the other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.gui.manifest import GuiManifest  # noqa: E402
from app.versions import (  # noqa: E402
    APP_VERSION,
    GUI_API_VERSION,
    KPI_FORMAT_VERSION,
    MIN_GUI_API_VERSION,
    MIN_KPI_FORMAT_VERSION,
    MIN_VAULT_VERSION,
    VAULT_VERSION,
    version_report,
)

CONTRACTS = (
    ("GUI API", MIN_GUI_API_VERSION, GUI_API_VERSION),
    ("vault layout", MIN_VAULT_VERSION, VAULT_VERSION),
    (".kpi format", MIN_KPI_FORMAT_VERSION, KPI_FORMAT_VERSION),
)


@pytest.mark.parametrize("label,minimum,current", CONTRACTS)
def test_every_contract_supports_a_non_empty_range(label, minimum, current):
    assert minimum <= current, f"{label}: MIN ({minimum}) is above current ({current})"


@pytest.mark.parametrize("label,minimum,current", CONTRACTS)
def test_every_contract_is_a_positive_integer(label, minimum, current):
    """Contract versions are monotonic ints, never strings and never zero."""
    for value in (minimum, current):
        assert isinstance(value, int) and not isinstance(value, bool), label
        assert value >= 1, label


def test_the_app_version_is_a_semver_string():
    """Identity versions are strings and are only ever ordered, never compared
    against a supported range."""
    from app.gui.manifest import version_tuple

    assert isinstance(APP_VERSION, str)
    assert len(version_tuple(APP_VERSION)) >= 2


CHANGELOG_DIR = REPO_ROOT / "docs" / "changelog"


def _released_versions() -> list[str]:
    """Every version ``docs/changelog/`` has an entry for, newest last."""
    from app.gui.manifest import version_tuple

    names = [path.stem.lstrip("v") for path in CHANGELOG_DIR.glob("v*.md")]
    return sorted(names, key=version_tuple)


def test_the_app_version_has_a_changelog():
    """``APP_VERSION`` names a release, and a release has an entry.

    The rule this enforces: ``APP_VERSION`` is bumped in the same commit that
    adds ``docs/changelog/v<APP_VERSION>.md``, and the tag goes on that commit.
    """
    entry = CHANGELOG_DIR / f"v{APP_VERSION}.md"
    assert entry.is_file(), (
        f"APP_VERSION is {APP_VERSION} but {entry.relative_to(REPO_ROOT)} does "
        "not exist — bump the version and write the changelog together"
    )


def test_no_changelog_is_newer_than_the_app_version():
    """``APP_VERSION`` names the *most recent* release, not merely a real one.

    This is the half that catches the drift: a changelog written for the next
    release and left in the tree while the constant trails behind reads, to the
    updater, as a build older than its own tag — so every user would be offered
    the version they are already running, forever.
    """
    from app.gui.manifest import version_tuple

    released = _released_versions()
    assert released, "docs/changelog/ has no entries at all"
    assert version_tuple(released[-1]) <= version_tuple(APP_VERSION), (
        f"docs/changelog/v{released[-1]}.md is newer than APP_VERSION "
        f"({APP_VERSION})"
    )


def _gui(api_version: int) -> GuiManifest:
    return GuiManifest(
        name="probe", title="Probe", version="1.0.0",
        api_version=api_version, entry="main.qml",
    )


def test_a_gui_inside_the_range_is_compatible_and_silent():
    for api_version in (MIN_GUI_API_VERSION, GUI_API_VERSION):
        manifest = _gui(api_version)
        assert manifest.is_compatible
        assert manifest.incompatibility_reason() == ""


def test_a_gui_from_the_future_says_to_update_the_app():
    manifest = _gui(GUI_API_VERSION + 1)
    reason = manifest.incompatibility_reason()

    assert not manifest.is_compatible
    assert str(GUI_API_VERSION + 1) in reason
    assert str(GUI_API_VERSION) in reason
    assert "update kroma player" in reason.lower()


def test_a_gui_from_the_past_says_it_is_no_longer_supported():
    manifest = _gui(MIN_GUI_API_VERSION - 1)
    reason = manifest.incompatibility_reason()

    assert not manifest.is_compatible
    assert str(MIN_GUI_API_VERSION - 1) in reason
    assert "no longer supports" in reason
    # Never the "update the app" advice — a newer build will not help.
    assert "update Kroma Player" not in reason


def test_the_report_names_every_version_in_the_system():
    """The registry has to keep gathering the versions that live elsewhere.

    A file format that grows a version constant and is never added here is
    invisible to diagnostics and to the backup manifest, which is exactly when
    somebody needs to see it.
    """
    report = version_report()

    for key in (
        "app", "gui_api", "min_gui_api", "vault", "min_vault",
        "kpi_format", "min_kpi_format", "schema", "session", "settings",
        "config", "identity", "backup_manifest",
        "plugin_api", "min_plugin_api", "kpp_format", "min_kpp_format",
    ):
        assert key in report, f"version_report() is missing '{key}'"

    assert report["app"] == APP_VERSION
    assert report["vault"] == VAULT_VERSION
    assert all(
        isinstance(value, (int, str)) for value in report.values()
    ), "the report has to be JSON-serialisable — it is stamped into backups"


def test_plugins_share_the_guis_api_level():
    """One contract seen from two sides: a slot a plugin fills is a member a GUI binds."""
    from app.versions import (
        MIN_PLUGIN_API_VERSION,
        PLUGIN_API_VERSION,
    )

    assert PLUGIN_API_VERSION == GUI_API_VERSION
    assert MIN_PLUGIN_API_VERSION == MIN_GUI_API_VERSION


def test_the_bundled_gui_targets_this_api_level():
    """13 removed members, so the GUI shipped beside it must say it knows that."""
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "gui" / "nocturne" / "gui.json").read_text()
    )
    assert MIN_GUI_API_VERSION <= manifest["api_version"] <= GUI_API_VERSION
