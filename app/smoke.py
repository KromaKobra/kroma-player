"""``--smoke-test``: start the real application against a throwaway vault, and say
whether it worked.

    kroma-player.exe --smoke-test=C:\\path\\to\\report.json [--smoke-linger=15]

For CI, and for the frozen build above all. A PyInstaller bundle fails in ways a
source checkout cannot: a module static analysis did not see, a Qt plugin the
spec pruned, a QML import that is only resolved when a file is first compiled.
Each of those surfaces as a user's first launch going wrong, so this goes
through every step a launch takes and writes down how each one went:

``imports``
    The modules a frozen build is known to lose: QtMultimedia, the AVIF codec,
    the SQLite dialect, certifi's CA bundle.
``vault``
    :func:`app.vault.startup.open_problem` on a vault whose database is one
    version behind, so the snapshot and the migration both run for real.
``registry``
    :func:`app.bootstrap.create_service_registry`.
``qml_types``
    The types and the settings singleton a GUI imports.
``gui``
    The resolved GUI, loaded into the same engine a launch builds. Any warning
    that a module "is not installed" or a name "is not a type" fails it — those
    are how a missing QML plugin shows itself, and the window may still appear.
``updates``
    What :mod:`app.update.install_kind` makes of this copy: the CI install test
    reads ``install_kind`` and ``installer_scope`` out of the report.
``shutdown``
    :func:`app.bootstrap.persist_app_state`, as quitting runs it.

The report is JSON, with ``ok`` true only if every stage passed, and the exit
code says the same. **The user's vault is never touched**: the vault is made in
the temp directory and ``remember_vault`` is never called, so the kord config
still points wherever it did.

``--smoke-linger`` keeps the process alive for that many seconds once the report
is written, holding the installer mutex the whole time. That is how the CI
update test stands in for a running copy the installer has to wait out.

**Pass an absolute path for the report.** A frozen build changes into its own
bundle folder before any of this runs (``hooks/rthook_set_cwd.py``), so a
relative one would land inside the installation.
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SMOKE_FLAG = "--smoke-test"
LINGER_FLAG = "--smoke-linger"
DEFAULT_REPORT = "kroma-player-smoke.json"

# Fragments of a QML warning that mean a module or a type did not resolve. The
# frozen build's characteristic failure, and one that can leave a window on
# screen, so a loaded root object is not enough to pass.
FATAL_QML_WARNINGS = ("is not installed", "is not a type")

# How long the GUI is given to settle after loading, so a Loader or a deferred
# component that fails a moment later is still caught.
SETTLE_SECONDS = 1.5


@dataclass(frozen=True)
class SmokeArgs:
    report: Path
    linger: float


def smoke_args(argv: list[str]) -> SmokeArgs | None:
    """The smoke-test request on *argv*, or ``None`` if there is none.

    ``--smoke-test=<path>`` names the report; a bare ``--smoke-test`` writes it
    to the temp directory. ``--smoke-linger=<seconds>`` is ignored without it.
    """
    report: Path | None = None
    linger = 0.0
    for arg in argv[1:]:
        if arg == SMOKE_FLAG:
            report = Path(tempfile.gettempdir()) / DEFAULT_REPORT
        elif arg.startswith(SMOKE_FLAG + "="):
            value = arg.split("=", 1)[1].strip().strip('"')
            report = Path(value) if value else Path(tempfile.gettempdir()) / DEFAULT_REPORT
        elif arg.startswith(LINGER_FLAG + "="):
            try:
                linger = max(0.0, float(arg.split("=", 1)[1]))
            except ValueError:
                linger = 0.0
    if report is None:
        return None
    return SmokeArgs(report=report, linger=linger)


class _Run:
    """The stages so far, and the report they add up to."""

    def __init__(self) -> None:
        self.stages: list[dict] = []
        self.facts: dict = {}

    def stage(self, name: str, step: Callable[[], object]) -> bool:
        started = time.monotonic()
        try:
            detail = step()
        except Exception as exc:  # noqa: BLE001 — every failure is a result here
            logger.exception("Smoke stage %s failed", name)
            self.stages.append({
                "name": name,
                "ok": False,
                "seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return False
        self.stages.append({
            "name": name,
            "ok": True,
            "seconds": round(time.monotonic() - started, 3),
            "detail": detail if isinstance(detail, (str, int, float, bool, list, dict)) else None,
        })
        return True

    @property
    def ok(self) -> bool:
        return bool(self.stages) and all(stage["ok"] for stage in self.stages)


def _check_imports() -> list[str]:
    import certifi
    import sqlalchemy.dialects.sqlite  # noqa: F401
    from PIL import AvifImagePlugin  # noqa: F401
    from PySide6 import QtMultimedia  # noqa: F401

    bundle = Path(certifi.where())
    if not bundle.is_file():
        raise FileNotFoundError(f"certifi's CA bundle is missing: {bundle}")
    return ["PySide6.QtMultimedia", "PIL.AvifImagePlugin", "sqlalchemy.dialects.sqlite", "certifi"]


def _open_vault(vault: Path) -> dict:
    """Open *vault* through the real startup path, with an upgrade pending.

    A brand-new vault would migrate nothing and snapshot nothing, which tests
    neither. So the database is built first and stamped one version behind:
    every migration is idempotent, so the rerun is a no-op that restamps, and
    the snapshot before it is a real one.
    """
    import sqlite3

    from app.database import schema
    from app.database.connection import migrate_database
    from app.utils.library_paths import set_library_root, tracks_db_path
    from app.vault.startup import open_problem, snapshot_root

    set_library_root(vault)
    database = tracks_db_path(vault)
    migrate_database(database)
    conn = sqlite3.connect(str(database))
    try:
        conn.execute(f"PRAGMA user_version = {int(schema.SCHEMA_VERSION) - 1}")
        conn.commit()
    finally:
        conn.close()

    problem = open_problem(vault)
    if problem:
        raise RuntimeError(problem)
    snapshots = [p.name for p in snapshot_root(vault).iterdir()]
    if not snapshots:
        raise RuntimeError("no upgrade snapshot was taken")
    version = schema.read_schema_version(database)
    if version != schema.SCHEMA_VERSION:
        raise RuntimeError(f"the database ended on {version}, not {schema.SCHEMA_VERSION}")
    return {"snapshots": snapshots, "schema": version}


def run_smoke_test(app, report: Path, linger: float = 0.0) -> int:
    """Run every stage, write the report, and return the exit code.

    *app* is the ``QGuiApplication`` ``main`` built. Never raises: a stage that
    fails is a line in the report and a non-zero exit, which is what CI needs.
    """
    from PySide6.QtCore import QTimer

    run = _Run()
    vault = Path(tempfile.mkdtemp(prefix="kroma-smoke-")) / "venomvault"
    (vault / ".data").mkdir(parents=True)
    warnings: list[str] = []
    registry = None
    engine = None

    # A stage hands back only JSON-able detail, so the objects a later stage
    # needs come out through these.
    registry_box: list = []
    engine_box: list = []

    run.stage("imports", _check_imports)
    if run.stage("vault", lambda: _open_vault(vault)):
        if run.stage("registry", lambda: _build_registry(registry_box)):
            registry = registry_box[0]
            run.stage("qml_types", lambda: _register_types(registry))
            run.stage("gui", lambda: _load(app, registry, warnings, engine_box))
            engine = engine_box[0] if engine_box else None
    run.stage("updates", lambda: _update_facts(run))

    if engine is not None:
        try:
            for window in engine.rootObjects():
                window.close()
        except Exception:  # noqa: BLE001
            pass
    if registry is not None:
        from app.bootstrap import persist_app_state

        run.stage("shutdown", lambda: persist_app_state(registry))
    # Every reference, so the engine and its windows are really gone before the
    # linger: the process should hold the mutex and nothing else.
    engine_box.clear()
    engine = None
    app.processEvents()

    document = {
        "ok": run.ok,
        "version": _app_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "vault": str(vault),
        "stages": run.stages,
        "qml_warnings": warnings,
        **run.facts,
    }
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.error("Could not write the smoke report to %s: %s", report, exc)
        return 2
    logger.info("Smoke test %s — report at %s", "passed" if run.ok else "FAILED", report)

    if linger > 0:
        # In the event loop rather than asleep, so the process answers the
        # window messages the Restart Manager may send it while it waits.
        QTimer.singleShot(int(linger * 1000), app.quit)
        app.exec()

    shutil.rmtree(vault.parent, ignore_errors=True)
    return 0 if run.ok else 1


def _build_registry(registry_box: list) -> str:
    from app.bootstrap import create_service_registry

    registry_box.append(create_service_registry())
    return "built"


def _register_types(registry) -> str:
    from app.main import _register_qml_types

    _register_qml_types(registry)
    return "registered"


def _load(app, registry, warnings: list[str], engine_box: list) -> dict:
    from app.main import _build_engine, _load_gui

    resolved = registry.get_service("resolved_gui")
    if resolved is None:
        raise RuntimeError("no GUI resolved")

    engine = _build_engine(registry)
    engine_box.append(engine)
    engine.warnings.connect(
        lambda errors: warnings.extend(error.toString() for error in errors)
    )
    if not _load_gui(engine, resolved):
        raise RuntimeError(f"the GUI {resolved.manifest.name!r} did not load")

    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)

    fatal = [w for w in warnings if any(marker in w for marker in FATAL_QML_WARNINGS)]
    if fatal:
        raise RuntimeError("; ".join(fatal[:5]))
    return {"gui": resolved.manifest.name, "warnings": len(warnings)}


def _update_facts(run: _Run) -> dict:
    from app.update.install_kind import install_kind, installer_scope, update_eligibility

    facts = {
        "install_kind": install_kind().value,
        "installer_scope": installer_scope(),
        "update_eligibility": update_eligibility(),
    }
    run.facts.update(facts)
    return facts


def _app_version() -> str:
    from app.versions import APP_VERSION

    return APP_VERSION
