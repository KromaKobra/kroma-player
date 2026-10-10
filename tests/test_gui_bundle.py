"""Compiling, fingerprinting, mounting and installing a ``.kpi``.

Two things are load-bearing here and neither is obvious from reading the code.

**The build id is the only staleness check there is.** Nothing compares trees
any more, so a change the fingerprint does not see is a change that never
reaches the user — and a fingerprint that moves when nothing has changed
reinstalls the GUI on every launch. Both directions are pinned below.

**A bundle mounted twice must unmount once.** The settings picker inspects
candidate bundles while a GUI is loaded, and the loaded one is frequently the
same file. If unregistering the inspection root took the live mount with it, the
running interface would lose its QML mid-session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QFile, QIODevice  # noqa: E402

from app.gui import bundle as bundle_mod  # noqa: E402
from app.gui import compiler as compiler_mod  # noqa: E402
from app.gui import installer as installer_mod  # noqa: E402
from app.gui.bundle import BundleError, mount, mount_root, read_records, unmount  # noqa: E402
from app.gui.compiler import (  # noqa: E402
    CompilerError,
    build_bundle,
    find_rcc,
    source_fingerprint,
)
from app.gui.gui_paths import (  # noqa: E402
    OFFICIAL_GUI_NAME,
    gui_bundle_path,
    gui_cache_dir,
    gui_index_path,
    gui_root,
)
from app.gui.installer import ensure_official_gui_installed, read_index  # noqa: E402


@pytest.fixture(scope="session")
def rcc() -> Path:
    """Qt's resource compiler, or skip.

    A checkout without it can still run everything else in the suite; what would
    be under test here is the toolchain rather than the application.
    """
    try:
        return find_rcc()
    except CompilerError as exc:
        pytest.skip(str(exc))


def make_source(root: Path, name: str = "probe", version: str = "1.0.0",
                api_version: int = 2) -> Path:
    """Write a minimal but real GUI package: a manifest, an entry, a module."""
    directory = root / name
    (directory / "Probe").mkdir(parents=True)
    (directory / "gui.json").write_text(json.dumps({
        "name": name,
        "title": name.title(),
        "version": version,
        "api_version": api_version,
        "entry": "main.qml",
    }), encoding="utf-8")
    (directory / "main.qml").write_text(
        'import QtQuick\nimport QtQuick.Window\n'
        'Window { visible: false; width: 10; height: 10 }\n',
        encoding="utf-8",
    )
    (directory / "Probe" / "qmldir").write_text("module Probe\n", encoding="utf-8")
    return directory


# -- fingerprinting ------------------------------------------------------


def test_the_same_tree_fingerprints_the_same_way_twice(tmp_path):
    source = make_source(tmp_path)
    assert source_fingerprint(source).build_id == source_fingerprint(source).build_id


def test_editing_a_file_changes_the_build_id(tmp_path):
    source = make_source(tmp_path)
    before = source_fingerprint(source).build_id

    (source / "main.qml").write_text(
        'import QtQuick\nItem { property int changed: 1 }\n', encoding="utf-8"
    )

    assert source_fingerprint(source).build_id != before


def test_renaming_a_file_changes_the_build_id(tmp_path):
    """Paths are folded in as well as contents. A digest over contents alone
    calls a rearranged tree identical to the one it replaced."""
    source = make_source(tmp_path)
    before = source_fingerprint(source).build_id

    (source / "Probe" / "qmldir").rename(source / "Probe" / "qmldir.txt")

    assert source_fingerprint(source).build_id != before


def test_a_new_qt_changes_the_build_id(tmp_path, monkeypatch):
    """The resource format and the .qmlc cache are both tied to the Qt the
    bundle was built against, so a Qt upgrade has to produce a new bundle."""
    source = make_source(tmp_path)
    before = source_fingerprint(source).build_id

    monkeypatch.setattr(compiler_mod, "qt_version_key", lambda: "9.9")

    assert source_fingerprint(source).build_id != before


def test_different_rcc_options_change_the_build_id(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    before = source_fingerprint(source).build_id

    monkeypatch.setattr(compiler_mod, "RCC_OPTIONS", "none:0")

    assert source_fingerprint(source).build_id != before


# -- compiling -----------------------------------------------------------


def test_a_bundle_carries_the_tree_and_both_records(tmp_path, rcc):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"

    record = build_bundle(source, output, rcc=rcc)
    contents = read_records(output)

    assert output.is_file()
    assert record["build_id"] == source_fingerprint(source).build_id
    assert record["format"] == 1
    assert record["entry"] == "main.qml"
    assert contents.build == record
    assert set(contents.files) >= {"gui.json", "main.qml", "Probe/qmldir", "build.json"}
    assert json.loads(contents.manifest_json)["name"] == "probe"


def test_the_sidecar_matches_what_went_into_the_bundle(tmp_path, rcc):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    sidecar = tmp_path / "probe.kpi.json"

    record = build_bundle(source, output, sidecar=sidecar, rcc=rcc)

    assert json.loads(sidecar.read_text(encoding="utf-8")) == record


def test_a_tree_with_no_manifest_will_not_compile(tmp_path, rcc):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CompilerError, match="gui.json"):
        build_bundle(tmp_path / "empty", tmp_path / "empty.kpi", rcc=rcc)


def test_a_source_tree_may_not_ship_a_build_record(tmp_path, rcc):
    """build.json is the compiler's, and a shipped one would be shadowed by it
    without anybody being told."""
    source = make_source(tmp_path)
    (source / "build.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CompilerError, match="build.json"):
        build_bundle(source, tmp_path / "probe.kpi", rcc=rcc)


def test_a_failed_build_leaves_the_previous_bundle_alone(tmp_path, rcc):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    build_bundle(source, output, rcc=rcc)
    good = output.read_bytes()

    (source / "gui.json").write_text("not json", encoding="utf-8")
    with pytest.raises(CompilerError):
        build_bundle(source, output, rcc=rcc)

    assert output.read_bytes() == good


# -- mounting ------------------------------------------------------------


def test_two_mounts_of_one_bundle_are_independent(tmp_path, rcc):
    """What lets the settings picker inspect a bundle that is already loaded."""
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    build_bundle(source, output, rcc=rcc)

    live = mount_root("probe", "aaaa")
    scratch = mount_root("probe", "bbbb")
    mount(output, live)
    mount(output, scratch)

    def readable(root: str) -> bool:
        handle = QFile(f":{root}/main.qml")
        opened = handle.open(QIODevice.OpenModeFlag.ReadOnly)
        handle.close()
        return opened

    try:
        assert readable(live) and readable(scratch)

        unmount(output, scratch)

        assert not readable(scratch)
        assert readable(live), "unmounting one root must not disturb the other"
    finally:
        unmount(output, live)
        unmount(output, scratch)


def test_mounting_twice_only_has_to_be_unmounted_once(tmp_path, rcc):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    build_bundle(source, output, rcc=rcc)
    root = mount_root("probe", "cccc")

    mount(output, root)
    mount(output, root)
    unmount(output, root)

    assert not bundle_mod.is_mounted(output, root)
    handle = QFile(f":{root}/main.qml")
    assert not handle.open(QIODevice.OpenModeFlag.ReadOnly)


def test_a_truncated_bundle_will_not_mount(tmp_path, rcc):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    build_bundle(source, output, rcc=rcc)
    output.write_bytes(output.read_bytes()[:64])

    with pytest.raises(BundleError):
        read_records(output)


def test_a_bundle_from_the_future_is_rejected_readably(tmp_path, rcc, monkeypatch):
    source = make_source(tmp_path)
    output = tmp_path / "probe.kpi"
    monkeypatch.setattr(compiler_mod, "KPI_FORMAT_VERSION", 99)
    build_bundle(source, output, rcc=rcc)

    with pytest.raises(BundleError) as caught:
        read_records(output)

    reason = str(caught.value)
    assert "99" in reason
    assert "newer Kroma Player" in reason


# -- installing ----------------------------------------------------------


@pytest.fixture
def no_autobuild(monkeypatch):
    """Install from the bundle that is already there rather than recompiling.

    The rebuild is exercised by its own test; everything else wants the
    installer's decision, not the compiler's.
    """
    monkeypatch.setenv(compiler_mod.ENV_NO_BUILD, "1")


@pytest.fixture
def shipped(no_autobuild):
    """Skip unless the repository has a compiled official GUI to install."""
    from app.gui.gui_paths import bundled_gui_bundle

    if not bundled_gui_bundle().is_file():
        pytest.skip("run scripts/build-gui first")
    return bundled_gui_bundle()


def test_a_fresh_vault_gets_the_bundle_and_an_index(shipped):
    installed = ensure_official_gui_installed()

    assert installed == gui_bundle_path(OFFICIAL_GUI_NAME)
    assert installed.is_file()
    assert not (gui_root() / OFFICIAL_GUI_NAME).exists(), "nothing loose on disk"

    entry = read_index()["guis"][OFFICIAL_GUI_NAME]
    assert entry["build_id"] == read_records(shipped).build_id
    assert entry["size"] == installed.stat().st_size
    assert entry["mtime_ns"] == installed.stat().st_mtime_ns


def _watch_copies(monkeypatch) -> list[str]:
    """Record every bundle copy the installer makes, and let it happen."""
    copied: list[str] = []
    real = installer_mod.shutil.copyfile

    def spy(src, dst):
        copied.append(str(dst))
        return real(src, dst)

    monkeypatch.setattr(installer_mod.shutil, "copyfile", spy)
    return copied


def test_a_second_launch_copies_nothing(shipped, monkeypatch):
    """The whole point of the index: two small JSON reads instead of 2 MB of
    hashing, and no write at all when nothing has moved."""
    ensure_official_gui_installed()
    copied = _watch_copies(monkeypatch)

    assert ensure_official_gui_installed() == gui_bundle_path(OFFICIAL_GUI_NAME)
    assert copied == [], "an up-to-date install must not be rewritten"


def test_a_stale_install_is_replaced_and_its_cache_swept(shipped, tmp_path, rcc):
    """A different bundle sitting where the official one goes is an upgrade."""
    target = gui_bundle_path(OFFICIAL_GUI_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)

    stale_source = make_source(tmp_path, name=OFFICIAL_GUI_NAME)
    build_bundle(stale_source, target, rcc=rcc)
    stale_id = read_records(target).build_id

    # Record it as installed, and give it a compiled-QML cache to be swept.
    index = read_index()
    index["guis"][OFFICIAL_GUI_NAME] = installer_mod.index_entry(
        target, {"build_id": stale_id, "version": "1.0.0", "api_version": 2}
    )
    installer_mod.write_index(index)
    stale_cache = gui_cache_dir(stale_id)
    stale_cache.mkdir(parents=True)

    installed = ensure_official_gui_installed()

    shipped_id = read_records(shipped).build_id
    assert read_records(installed).build_id == shipped_id
    assert read_index()["guis"][OFFICIAL_GUI_NAME]["build_id"] == shipped_id
    assert not stale_cache.exists(), "the replaced build's bytecode is swept"


def test_a_lost_index_is_rebuilt_by_inspection(shipped, monkeypatch):
    """Failure path: delete index.json and the app still starts — and does not
    pay for a needless copy on the way."""
    ensure_official_gui_installed()
    gui_index_path().unlink()
    copied = _watch_copies(monkeypatch)

    installed = ensure_official_gui_installed()

    assert installed.is_file()
    assert copied == [], "the installed bundle already says what build it is"
    assert read_index()["guis"][OFFICIAL_GUI_NAME]["build_id"] == read_records(shipped).build_id


def test_an_old_loose_install_is_set_aside(shipped):
    """The one-time migration. A pre-.kpi package cannot be loaded either way,
    but it may hold somebody's edits, so it is moved rather than deleted."""
    legacy = gui_root() / OFFICIAL_GUI_NAME
    legacy.mkdir(parents=True)
    (legacy / "main.qml").write_text("// mine\n", encoding="utf-8")

    installed = ensure_official_gui_installed()

    assert not legacy.exists()
    backup = gui_root() / f"{OFFICIAL_GUI_NAME}.bak"
    assert (backup / "main.qml").read_text(encoding="utf-8") == "// mine\n"
    assert installed.is_file()


def test_a_checkout_rebuilds_when_its_source_changes(tmp_path, monkeypatch, rcc):
    """The development loop: edit QML, run the app, see the edit."""
    from app.gui import gui_paths

    shipped_root = tmp_path / "shipped"
    source = make_source(shipped_root, name=OFFICIAL_GUI_NAME)
    monkeypatch.setattr(gui_paths, "bundled_gui_root", lambda: shipped_root)
    monkeypatch.setattr(installer_mod, "bundled_gui_source", lambda name: source)
    monkeypatch.setattr(
        installer_mod, "bundled_gui_bundle",
        lambda name=OFFICIAL_GUI_NAME: shipped_root / f"{name}.kpi",
    )
    monkeypatch.setattr(
        installer_mod, "bundled_gui_sidecar",
        lambda name=OFFICIAL_GUI_NAME: shipped_root / f"{name}.kpi.json",
    )

    first = ensure_official_gui_installed()
    assert first is not None
    before = read_records(first).build_id

    (source / "main.qml").write_text(
        'import QtQuick\nimport QtQuick.Window\n'
        'Window { visible: false; width: 11; height: 11 }\n',
        encoding="utf-8",
    )

    second = ensure_official_gui_installed()

    assert second is not None
    assert read_records(second).build_id != before
    assert read_records(second).build_id == source_fingerprint(source).build_id


def test_the_rebuild_can_be_switched_off(tmp_path, monkeypatch, rcc):
    from app.gui import gui_paths

    shipped_root = tmp_path / "shipped"
    source = make_source(shipped_root, name=OFFICIAL_GUI_NAME)
    monkeypatch.setattr(gui_paths, "bundled_gui_root", lambda: shipped_root)
    monkeypatch.setattr(installer_mod, "bundled_gui_source", lambda name: source)
    monkeypatch.setattr(
        installer_mod, "bundled_gui_bundle",
        lambda name=OFFICIAL_GUI_NAME: shipped_root / f"{name}.kpi",
    )
    monkeypatch.setattr(
        installer_mod, "bundled_gui_sidecar",
        lambda name=OFFICIAL_GUI_NAME: shipped_root / f"{name}.kpi.json",
    )

    first = ensure_official_gui_installed()
    before = read_records(first).build_id

    monkeypatch.setenv(compiler_mod.ENV_NO_BUILD, "1")
    (source / "main.qml").write_text('import QtQuick\nItem { }\n', encoding="utf-8")

    assert read_records(ensure_official_gui_installed()).build_id == before
