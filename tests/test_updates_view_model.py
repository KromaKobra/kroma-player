"""The view model's phase machine, its gate, and its refusals.

Every worker here is driven synchronously — the workers themselves are tested in
:mod:`tests.test_update_download` and :mod:`tests.test_update_release`, and what
is left to check is the *decisions*: when a check is allowed to run, what counts
as an update worth offering, and what happens at each of the three points where
this feature could do something the user did not ask for.

Note the guide's warning about the alternative: a test that drops a view model
with a live thread under it **crashes the runner rather than failing**. The
threads here are joined by ``_drive``, which never leaves one running.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database.schema import SCHEMA_VERSION  # noqa: E402
from app.settings.updates import UpdateSettings  # noqa: E402
from app.update.install_kind import InstallKind  # noqa: E402
from app.update.release import Asset, Release  # noqa: E402
from app.versions import APP_VERSION, VAULT_VERSION  # noqa: E402
from app.viewmodels import updates_view_model as uvm  # noqa: E402


def _asset(kind: str = "appimage", size: int = 231735296) -> Asset:
    return Asset(
        name=f"kroma-player-9.9.9-x86_64.{kind}",
        url="https://example.invalid/asset",
        kind=kind,
        arch="x86_64",
        size=size,
        sha256="a" * 64,
    )


def _release(version: str = "9.9.9", *, vault_version: int = VAULT_VERSION,
             schema_version: int = SCHEMA_VERSION, assets=None,
             notes: str = "## Highlights\n\nSomething new.") -> Release:
    return Release(
        version=version,
        tag=f"v{version}",
        notes=notes,
        published_at="2026-09-08T12:00:00Z",
        url="https://example.invalid/release",
        vault_version=vault_version,
        assets=tuple(assets if assets is not None else (_asset(),)),
        schema_version=schema_version,
    )


class _Store:
    """The two methods UpdateSettings uses, over a dict."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def app_get(self, key, default=None):
        return self.values.get(key, default)

    def app_set(self, key, value):
        self.values[key] = value


class _Timer:
    """Records the exit watchdog instead of arming it.

    Autouse below, and not optional: the real one calls ``os._exit`` sixty
    seconds after a handoff, which would end the test run mid-flight.
    """

    armed: list["_Timer"] = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False

    def start(self):
        self.started = True
        _Timer.armed.append(self)


@pytest.fixture(autouse=True)
def no_watchdog(monkeypatch):
    _Timer.armed = []
    monkeypatch.setattr(uvm.threading, "Timer", _Timer)
    return _Timer.armed


@pytest.fixture
def model(monkeypatch):
    """A view model on an eligible machine, with no vault check in the way."""
    monkeypatch.setattr(uvm, "update_eligibility", lambda: "")
    monkeypatch.setattr(uvm, "install_kind", lambda: InstallKind.APPIMAGE)
    return uvm.UpdatesViewModel(UpdateSettings(_Store()))


def _finish_check(model, release, *, automatic: bool = False):
    """Run the check's result handlers the way the real signals would.

    ``_on_checked`` reads the release off the worker and records a pending
    phase; ``_on_stopped`` is what actually moves it, once the thread is gone.
    That split is the whole reason the crash in
    ``app.viewmodels.worker_threads`` does not happen here, so the tests go
    through both rather than around them.

    *automatic* is which kind of check it was — the launch check, or the
    button — which decides whether a skipped version is silenced.
    """
    model._checking_automatic = automatic
    model._worker = type("W", (), {"release": release})()
    model._on_checked()
    model._worker = None
    model._on_stopped()


def _finish_download(model, path):
    """The download worker's result, delivered the same two-step way."""
    model._worker = type("W", (), {"downloaded_path": path})()
    model._on_downloaded()
    model._worker = None
    model._on_stopped()


def _fail_download(model, message="the server went away"):
    model._on_failed(message)
    model._worker = None
    model._on_stopped()


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["source", "managed", "portable", "disabled"])
def test_an_ineligible_copy_says_why_in_a_full_sentence(monkeypatch, reason):
    """The pane shows this instead of a button. A reason beats a control that
    cannot work."""
    monkeypatch.setattr(uvm, "update_eligibility", lambda: reason)
    model = uvm.UpdatesViewModel(UpdateSettings(None))

    assert not model.eligible
    assert model.ineligibleReason.endswith(".")
    assert len(model.ineligibleReason) > 20


def test_an_eligible_copy_has_no_reason_to_show(model):
    assert model.eligible
    assert model.ineligibleReason == ""


def test_an_ineligible_copy_never_reaches_the_network(monkeypatch):
    monkeypatch.setattr(uvm, "update_eligibility", lambda: "managed")
    model = uvm.UpdatesViewModel(UpdateSettings(_Store()))
    monkeypatch.setattr(
        model, "_start", lambda _worker: pytest.fail("started a worker anyway")
    )

    model.check()
    assert model.phase == uvm.PHASE_IDLE


# ---------------------------------------------------------------------------
# The 24 h gate
# ---------------------------------------------------------------------------


def test_a_first_launch_is_due_for_a_check():
    settings = UpdateSettings(_Store())
    assert settings.due_for_check()


def test_a_recent_check_is_not_repeated():
    """Anonymous GitHub allows 60 requests an hour per IP. Without the gate a
    shared-IP office trips the limit and every user there gets a silent
    failure — which looks exactly like "there is no update"."""
    settings = UpdateSettings(_Store())
    settings.stamp_checked()
    assert not settings.due_for_check()


def test_a_day_later_it_is_due_again():
    settings = UpdateSettings(_Store())
    settings.stamp_checked(datetime.now(timezone.utc) - timedelta(hours=25))
    assert settings.due_for_check()


def test_the_preference_switches_the_automatic_check_off():
    settings = UpdateSettings(_Store({"updates_check_on_launch": False}))
    assert not settings.due_for_check()


def test_an_unreadable_stamp_fails_open():
    """One corrupt value must not silently disable update checks forever. The
    cost of failing open is a single extra request."""
    settings = UpdateSettings(_Store({"updates_last_checked": "last Tuesday"}))
    assert settings.due_for_check()


def test_a_manual_check_ignores_the_gate(model, monkeypatch):
    """Somebody who pressed the button is owed an answer, and one request is not
    what trips a rate limit."""
    model._settings.stamp_checked()
    started: list = []
    monkeypatch.setattr(model, "_start", lambda worker: started.append(worker) or True)

    model.check()
    assert started, "a manual check must run even inside the gate"


def test_the_launch_check_honours_the_gate(model, monkeypatch):
    model._settings.stamp_checked()
    monkeypatch.setattr(
        model, "_start", lambda _worker: pytest.fail("checked inside the gate")
    )

    model.checkOnStartup()


# ---------------------------------------------------------------------------
# What is worth offering
# ---------------------------------------------------------------------------


def test_a_newer_release_becomes_available(model):
    announced: list[str] = []
    model.update_available.connect(announced.append)

    _finish_check(model, _release("9.9.9"))

    assert model.phase == uvm.PHASE_AVAILABLE
    assert model.updateAvailable
    assert model.latestVersion == "9.9.9"
    assert announced == ["9.9.9"]
    assert model.downloadMb > 200


def test_the_version_this_build_already_is_is_not_offered(model):
    _finish_check(model, _release(APP_VERSION))

    assert model.phase == uvm.PHASE_UP_TO_DATE
    assert not model.updateAvailable


def test_an_older_release_is_never_offered(model):
    """Strictly greater, with no version picker: a vault migrated by a newer
    build is refused outright by check_vault(), so a downgrade is a door that
    does not open from this side."""
    _finish_check(model, _release("0.0.1"))

    assert model.phase == uvm.PHASE_UP_TO_DATE
    assert not model.updateAvailable


def test_nothing_at_all_reads_as_up_to_date(model):
    """`fetch_latest_release()` returns None for every uninteresting outcome —
    offline, rate-limited, malformed. None of them is something the user can act
    on, and a check with nothing to report should say nothing."""
    _finish_check(model, None)

    assert model.phase == uvm.PHASE_UP_TO_DATE


def test_a_skipped_version_is_not_announced_again(model):
    _finish_check(model, _release("9.9.9"))
    model.skipThisVersion()

    assert model._settings.skipped_version == "9.9.9"
    assert not model.updateAvailable

    announced: list[str] = []
    model.update_available.connect(announced.append)
    _finish_check(model, _release("9.9.9"), automatic=True)
    assert model.phase == uvm.PHASE_UP_TO_DATE
    assert announced == []


def test_a_manual_check_still_offers_a_skipped_version_quietly(model):
    """Somebody who pressed "Check for updates" is asking, whatever they said
    last week. Offered in the pane; not announced with a dialog, which for a
    version they said no to would be nagging."""
    _finish_check(model, _release("9.9.9"))
    model.skipThisVersion()

    announced: list[str] = []
    model.update_available.connect(announced.append)
    _finish_check(model, _release("9.9.9"), automatic=False)

    assert model.updateAvailable
    assert model.phase == uvm.PHASE_AVAILABLE
    assert announced == []


def test_the_launch_check_is_what_marks_a_check_automatic(model, monkeypatch):
    monkeypatch.setattr(model, "_start", lambda _worker: True)

    model.checkOnStartup()
    assert model._checking_automatic is True

    model.check()
    assert model._checking_automatic is False


def test_skipping_one_version_does_not_skip_the_next(model):
    """A single string rather than a set: skipping 1.2.0 and later being offered
    1.3.0 is what somebody skipping a release actually wants."""
    _finish_check(model, _release("9.9.9"))
    model.skipThisVersion()

    _finish_check(model, _release("9.9.10"))
    assert model.updateAvailable
    assert model.latestVersion == "9.9.10"


def test_a_release_with_no_build_for_this_system_reports_rather_than_lying(model):
    errors: list[str] = []
    model.error_occurred.connect(errors.append)

    _finish_check(model, _release("9.9.9", assets=(_asset("windows_installer"),)))

    assert not model.updateAvailable
    assert errors and "does not include a build" in errors[0]


# ---------------------------------------------------------------------------
# The vault warning
# ---------------------------------------------------------------------------


def test_an_update_that_migrates_the_vault_says_so(model):
    """A one-way door: after migrating, every older build is refused the vault.
    This is the entire reason release.json exists rather than just SHA256SUMS."""
    _finish_check(model, _release("9.9.9", vault_version=VAULT_VERSION + 1))

    assert model.willMigrateVault
    assert model.vaultVersionAfter == VAULT_VERSION + 1


def test_an_ordinary_update_does_not(model):
    _finish_check(model, _release("9.9.9"))
    assert not model.willMigrateVault


def test_a_manifest_with_no_vault_version_never_over_warns(model):
    """Zero reads as "no migration", so a release predating the field can only
    ever under-warn — never raise a warning about something that is not
    happening."""
    _finish_check(model, _release("9.9.9", vault_version=0))
    assert not model.willMigrateVault


def test_an_update_that_migrates_the_database_says_so(model):
    """The part of a vault that actually moves: SCHEMA_VERSION rose six times
    between 1.1.0 and 1.2.0 while the layout stayed put."""
    _finish_check(model, _release("9.9.9", schema_version=SCHEMA_VERSION + 1))

    assert model.willMigrate
    assert not model.willMigrateVault, "the layout warning is only about the layout"


def test_a_layout_migration_is_a_migration_too(model):
    _finish_check(model, _release("9.9.9", vault_version=VAULT_VERSION + 1))
    assert model.willMigrate


def test_an_update_that_migrates_nothing_does_not_warn(model):
    _finish_check(model, _release("9.9.9"))
    assert not model.willMigrate


def test_a_manifest_with_no_schema_version_never_over_warns(model):
    _finish_check(model, _release("9.9.9", schema_version=0))
    assert not model.willMigrate


def test_nothing_to_migrate_without_a_release(model):
    assert not model.willMigrate


# ---------------------------------------------------------------------------
# The release summary
# ---------------------------------------------------------------------------

CHANGELOG = """<div align="center">

# v9.9.9 Changelog

</div>

## Highlights

### Something new

> It does a thing.

---

## Features

- **A long list nobody reads in a dialog**
"""


def test_the_summary_is_the_highlights_without_the_html_or_the_title(model):
    _finish_check(model, _release("9.9.9", notes=CHANGELOG))

    summary = model.releaseSummary
    assert summary.startswith("## Highlights")
    assert "Something new" in summary
    assert "It does a thing." in summary
    assert "Features" not in summary, "stops at the first rule"
    assert "<div" not in summary and "</div>" not in summary
    assert "Changelog" not in summary, "the version is already the dialog's title"


def test_notes_without_a_rule_are_kept_whole():
    assert uvm.summarize_notes("## One\n\nTwo") == "## One\n\nTwo"
    assert uvm.summarize_notes("") == ""


def test_no_release_has_no_summary(model):
    assert model.releaseSummary == ""


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def test_install_does_nothing_unless_a_download_is_ready(model, monkeypatch):
    monkeypatch.setattr(
        uvm, "run_installer", lambda *_a, **_k: pytest.fail("installed with nothing ready")
    )
    model.installAndRestart()
    assert model.phase == uvm.PHASE_IDLE


def test_install_re_verifies_the_file_before_running_it(model, monkeypatch, tmp_path):
    """It has been on disk since the download, possibly in a world-writable temp
    directory, and what happens next cannot be undone."""
    downloaded = tmp_path / "update.AppImage"
    downloaded.write_bytes(b"tampered")
    model._release = _release("9.9.9")
    model._asset = _asset()
    model._downloaded = downloaded
    model._phase = uvm.PHASE_READY

    errors: list[str] = []
    model.error_occurred.connect(errors.append)
    monkeypatch.setattr(
        uvm, "replace_appimage", lambda *_a: pytest.fail("installed an unverified file")
    )

    model.installAndRestart()

    assert errors and "checksum" in errors[0]
    assert model.phase == uvm.PHASE_AVAILABLE


def test_the_appimage_swap_is_deferred_to_shutdown(model, monkeypatch, tmp_path):
    """execv unwinds nothing — no atexit, no Qt teardown, no database close — so
    it must not run from a slot that returns into a live event loop."""
    downloaded = tmp_path / "update.AppImage"
    downloaded.write_bytes(b"payload")
    model._release = _release("9.9.9")
    model._asset = _asset()
    model._downloaded = downloaded
    model._phase = uvm.PHASE_READY

    monkeypatch.setattr(uvm, "verify", lambda *_a: True)
    monkeypatch.setattr(model, "_quit", lambda: None)
    replaced: list[Path] = []
    monkeypatch.setattr(uvm, "replace_appimage", lambda path: replaced.append(path))

    model.installAndRestart()
    assert replaced == [], "nothing may be replaced while the event loop is live"
    assert model.phase == uvm.PHASE_APPLYING

    model.shutdown()
    assert replaced == [downloaded]


def test_a_refused_installer_falls_back_to_manual(model, monkeypatch, tmp_path):
    """A verified update the user can install by hand beats a silent failure."""
    downloaded = tmp_path / "setup.exe"
    downloaded.write_bytes(b"MZ")
    model._release = _release("9.9.9")
    model._asset = _asset("windows_installer")
    model._downloaded = downloaded
    model._phase = uvm.PHASE_READY

    monkeypatch.setattr(uvm, "verify", lambda *_a: True)
    monkeypatch.setattr(uvm, "install_kind", lambda: InstallKind.WINDOWS_INSTALLER)
    monkeypatch.setattr(uvm, "run_installer", lambda *_a, **_k: False)
    monkeypatch.setattr(uvm, "installer_scope", lambda: "user")

    errors: list[str] = []
    model.error_occurred.connect(errors.append)
    model.installAndRestart()

    assert model.phase == uvm.PHASE_MANUAL
    assert errors
    assert model._settings.pending_version == "", "nothing was handed off"


def _ready_on_windows(model, monkeypatch, tmp_path, *, scope="user"):
    downloaded = tmp_path / "KromaPlayerSetup-9.9.9-x86_64.exe"
    downloaded.write_bytes(b"MZ")
    model._release = _release("9.9.9", assets=(_asset("windows_installer"),))
    model._asset = _asset("windows_installer")
    model._downloaded = downloaded
    model._phase = uvm.PHASE_READY
    monkeypatch.setattr(uvm, "verify", lambda *_a: True)
    monkeypatch.setattr(uvm, "install_kind", lambda: InstallKind.WINDOWS_INSTALLER)
    monkeypatch.setattr(uvm, "installer_scope", lambda: scope)
    quits: list[int] = []
    monkeypatch.setattr(model, "_quit", lambda: quits.append(1))
    return downloaded, quits


def test_the_windows_handoff_passes_scope_and_log(model, monkeypatch, tmp_path):
    """The scope keeps the update on the copy being updated; the log is what
    the user is shown if it does not take."""
    downloaded, quits = _ready_on_windows(model, monkeypatch, tmp_path, scope="machine")
    calls: list[tuple] = []
    monkeypatch.setattr(
        uvm, "run_installer",
        lambda path, **kwargs: calls.append((path, kwargs)) or True,
    )

    model.installAndRestart()

    assert calls == [(downloaded, {"scope": "machine", "log": uvm.install_log_path()})]
    assert quits == [1]


def test_the_handoff_arms_the_exit_watchdog(model, monkeypatch, tmp_path, no_watchdog):
    """A shutdown that hangs must not hold the mutex past the installer's 90 s
    wait. Armed at the handoff, not at the end of shutdown, so a hang anywhere
    in it is covered."""
    _ready_on_windows(model, monkeypatch, tmp_path)
    monkeypatch.setattr(uvm, "run_installer", lambda *_a, **_k: True)

    model.installAndRestart()

    assert len(no_watchdog) == 1
    timer = no_watchdog[0]
    assert timer.interval == uvm.EXIT_WATCHDOG_SECONDS
    assert timer.function is uvm._force_exit
    assert timer.daemon, "a daemon, so a clean exit takes it along"
    assert timer.started


def test_no_watchdog_without_a_handoff(model, monkeypatch, tmp_path, no_watchdog):
    _ready_on_windows(model, monkeypatch, tmp_path)
    monkeypatch.setattr(uvm, "run_installer", lambda *_a, **_k: False)

    model.installAndRestart()
    model.shutdown()

    assert no_watchdog == []


def test_the_pending_version_is_written_before_the_handoff(model, monkeypatch, tmp_path):
    """Written first: the next launch compares it with its own version."""
    _ready_on_windows(model, monkeypatch, tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(
        uvm, "run_installer",
        lambda *_a, **_k: seen.append(model._settings.pending_version) or True,
    )

    model.installAndRestart()

    assert seen == ["9.9.9"]


# ---------------------------------------------------------------------------
# One click
# ---------------------------------------------------------------------------


def test_update_now_from_available_downloads_and_remembers(model, monkeypatch):
    _finish_check(model, _release("9.9.9"))
    started: list = []

    def start(worker):
        started.append(worker)
        return True

    monkeypatch.setattr(model, "_start", start)

    model.updateNow()

    assert started, "the download began"
    assert model.phase == uvm.PHASE_DOWNLOADING
    assert model._install_when_ready
    assert model.installWhenReady


def test_a_verified_download_then_installs_by_itself(model, monkeypatch, tmp_path):
    _finish_check(model, _release("9.9.9"))
    monkeypatch.setattr(model, "_start", lambda _worker: True)
    model.updateNow()

    installs: list[int] = []
    monkeypatch.setattr(model, "installAndRestart", lambda: installs.append(1))
    _finish_download(model, tmp_path / "update.AppImage")

    assert installs == [1]
    assert model.phase == uvm.PHASE_READY
    assert not model._install_when_ready, "spent"


def test_update_now_from_ready_installs_at_once(model, monkeypatch, tmp_path):
    model._release = _release("9.9.9")
    model._asset = _asset()
    model._downloaded = tmp_path / "update.AppImage"
    model._phase = uvm.PHASE_READY
    installs: list[int] = []
    monkeypatch.setattr(model, "installAndRestart", lambda: installs.append(1))

    model.updateNow()

    assert installs == [1]


def test_update_now_during_a_download_attaches_to_it(model):
    model._phase = uvm.PHASE_DOWNLOADING

    model.updateNow()

    assert model._install_when_ready


def test_a_failed_download_drops_the_decision(model, monkeypatch):
    _finish_check(model, _release("9.9.9"))
    monkeypatch.setattr(model, "_start", lambda _worker: True)
    model.updateNow()
    monkeypatch.setattr(
        model, "installAndRestart", lambda: pytest.fail("installed after a failure")
    )

    _fail_download(model)

    assert model.phase == uvm.PHASE_AVAILABLE
    assert not model._install_when_ready


def test_a_cancelled_download_drops_the_decision(model, monkeypatch):
    _finish_check(model, _release("9.9.9"))
    monkeypatch.setattr(model, "_start", lambda _worker: True)
    model.updateNow()
    monkeypatch.setattr(
        model, "installAndRestart", lambda: pytest.fail("installed after a cancel")
    )

    model._on_cancelled()
    model._worker = None
    model._on_stopped()

    assert model.phase == uvm.PHASE_AVAILABLE
    assert not model._install_when_ready


def test_a_refused_download_drops_the_decision(model, monkeypatch):
    """A worker already running: nothing started, so nothing to carry."""
    _finish_check(model, _release("9.9.9"))
    monkeypatch.setattr(model, "_start", lambda _worker: False)

    model.updateNow()

    assert model.phase == uvm.PHASE_AVAILABLE
    assert not model._install_when_ready


def test_update_now_with_nothing_offered_does_nothing(model, monkeypatch):
    monkeypatch.setattr(model, "_start", lambda _w: pytest.fail("started something"))
    model.updateNow()
    assert model.phase == uvm.PHASE_IDLE


# ---------------------------------------------------------------------------
# An update that did not take
# ---------------------------------------------------------------------------


@pytest.fixture
def eligible(monkeypatch):
    monkeypatch.setattr(uvm, "update_eligibility", lambda: "")
    monkeypatch.setattr(uvm, "install_kind", lambda: InstallKind.WINDOWS_INSTALLER)


def test_still_older_than_the_handed_off_version_means_it_failed(eligible):
    store = _Store({"updates_pending_version": "9.9.9"})
    model = uvm.UpdatesViewModel(UpdateSettings(store))

    assert model.lastUpdateFailed
    assert model.failedVersion == "9.9.9"
    assert store.values["updates_pending_version"] == "9.9.9", "kept until dismissed"


def test_reaching_the_handed_off_version_clears_it(eligible):
    store = _Store({"updates_pending_version": APP_VERSION})
    model = uvm.UpdatesViewModel(UpdateSettings(store))

    assert not model.lastUpdateFailed
    assert store.values["updates_pending_version"] == ""


def test_dismissing_the_failure_forgets_it(eligible):
    store = _Store({"updates_pending_version": "9.9.9"})
    model = uvm.UpdatesViewModel(UpdateSettings(store))

    model.dismissUpdateFailure()

    assert not model.lastUpdateFailed
    assert store.values["updates_pending_version"] == ""


def test_the_pending_version_is_written_through(monkeypatch):
    """The app is about to quit, and the shutdown that would otherwise save it is
    the part an update most often cuts short."""
    flushed: list[int] = []

    class FlushingStore(_Store):
        def flush(self):
            flushed.append(1)

    settings = UpdateSettings(FlushingStore())
    settings.pending_version = "9.9.9"

    assert flushed == [1]


def test_the_install_log_is_revealed_or_its_folder(model, monkeypatch, tmp_path):
    opened: list[Path] = []
    monkeypatch.setattr(
        uvm.QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile()))
    )
    log = tmp_path / "kroma-update" / "install.log"
    monkeypatch.setattr(uvm, "install_log_path", lambda: log)

    model.revealInstallLog()
    log.parent.mkdir()
    log.write_text("Log closed.")
    model.revealInstallLog()

    assert opened == [log.parent, log]


def test_a_refused_replacement_at_shutdown_is_logged_not_raised(model, monkeypatch, tmp_path):
    """The window is already closing; there is nothing left to show a message
    with, and the next launch will offer the update again."""
    def refuse(_path):
        raise PermissionError("Read-only file system")

    monkeypatch.setattr(uvm, "replace_appimage", refuse)
    model._pending_replacement = tmp_path / "update.AppImage"

    model.shutdown()   # must not raise
    assert model._pending_replacement is None


def test_shutdown_with_nothing_pending_is_a_no_op(model):
    model.shutdown()
    assert model.phase == uvm.PHASE_IDLE


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_is_a_percentage_and_is_clamped(model):
    model._on_progress(50, 200)
    assert model.progress == 25

    # A server that sends more than the manifest promised must not produce a bar
    # past its own end.
    model._on_progress(300, 200)
    assert model.progress == 100


def test_progress_with_no_total_is_zero_rather_than_a_division_error(model):
    model._on_progress(1000, 0)
    assert model.progress == 0
