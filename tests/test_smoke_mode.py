"""``--smoke-test``: the frozen build's self-test, run here from source.

CI runs it against the PyInstaller bundle on Windows, where it is the only
thing between a module the spec forgot and a user's first launch. Here it runs
in-process, once, to keep the stages themselves honest: if this fails from
source, the CI run can only fail for the same reason and is not telling anyone
anything about the bundle.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import smoke  # noqa: E402


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_no_flag_is_no_smoke_test():
    assert smoke.smoke_args(["kroma-player"]) is None
    assert smoke.smoke_args(["kroma-player", "--smoke-linger=15"]) is None


def test_the_report_path_and_the_linger(tmp_path):
    report = tmp_path / "report.json"
    args = smoke.smoke_args(["kroma-player", f"--smoke-test={report}", "--smoke-linger=15"])

    assert args == smoke.SmokeArgs(report=report, linger=15.0)


def test_a_path_with_spaces_and_quotes_survives(tmp_path):
    """PowerShell hands the whole quoted argument over on some paths."""
    report = tmp_path / "kroma smoke test" / "report.json"
    args = smoke.smoke_args(["kroma-player", f'--smoke-test="{report}"'])

    assert args.report == report


def test_a_bare_flag_reports_to_the_temp_directory():
    args = smoke.smoke_args(["kroma-player", "--smoke-test"])

    assert args.report == Path(tempfile.gettempdir()) / smoke.DEFAULT_REPORT
    assert args.linger == 0.0


@pytest.mark.parametrize("value", ["soon", "-3", ""])
def test_a_nonsense_linger_is_no_linger(value):
    args = smoke.smoke_args(["kroma-player", "--smoke-test", f"--smoke-linger={value}"])
    assert args.linger == 0.0


def test_qt_arguments_around_it_are_ignored(tmp_path):
    report = tmp_path / "r.json"
    args = smoke.smoke_args(["kroma-player", "-platform", "offscreen", f"--smoke-test={report}"])
    assert args.report == report


# ---------------------------------------------------------------------------
# A real run
# ---------------------------------------------------------------------------


def test_the_whole_application_starts_and_reports(qt_app, tmp_path):
    """Every stage, from source, against a throwaway vault."""
    from app.config.kord_config import get_vault_path

    configured_before = get_vault_path()
    report = tmp_path / "out" / "report.json"

    code = smoke.run_smoke_test(qt_app, report, linger=0)

    document = json.loads(report.read_text(encoding="utf-8"))
    failures = [
        f"{stage['name']}: {stage.get('error')}\n{stage.get('traceback', '')}"
        for stage in document["stages"] if not stage["ok"]
    ]
    assert not failures, "\n\n".join(failures)
    assert code == 0
    assert document["ok"] is True
    assert [stage["name"] for stage in document["stages"]] == [
        "imports", "vault", "registry", "qml_types", "gui", "updates", "shutdown",
    ]

    vault_stage = document["stages"][1]["detail"]
    assert vault_stage["snapshots"], "the upgrade path really ran"

    assert document["install_kind"] == "source"
    assert document["installer_scope"] == ""
    assert document["update_eligibility"] == "source"
    assert document["frozen"] is False

    assert get_vault_path() == configured_before, "the user's vault setting is untouched"
    assert not Path(document["vault"]).exists(), "the throwaway vault is cleaned up"


def test_a_failing_stage_fails_the_run_and_says_which(qt_app, tmp_path, monkeypatch):
    def broken():
        raise ImportError("No module named 'PySide6.QtMultimedia'")

    monkeypatch.setattr(smoke, "_check_imports", broken)
    monkeypatch.setattr(smoke, "_open_vault", lambda _vault: {"skipped": True})
    monkeypatch.setattr(smoke, "_build_registry", lambda _box: (_ for _ in ()).throw(
        RuntimeError("registry stand-in")
    ))
    report = tmp_path / "report.json"

    code = smoke.run_smoke_test(qt_app, report)

    document = json.loads(report.read_text(encoding="utf-8"))
    assert code == 1
    assert document["ok"] is False
    imports = document["stages"][0]
    assert imports["name"] == "imports" and not imports["ok"]
    assert "QtMultimedia" in imports["error"]
