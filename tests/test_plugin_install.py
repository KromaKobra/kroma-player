"""Installing a plugin from a .kpp: safely, atomically, and reversibly.

A ``.kpp`` is a zip somebody else wrote, so every name in it is hostile until
proven otherwise — these tests build the classic attacks and check each is
refused *before anything is written*. Then the two promises an install makes:
an upgrade that fails leaves the previous version installed and working, and an
uninstall takes the code and nothing else.
"""
from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.plugins import installer
from app.plugins.installer import InstallError, inspect, install, uninstall
from app.plugins.paths import plugin_dir, plugins_root
from app.settings.store import SettingsStore
from app.versions import KPP_FORMAT_VERSION
from tests.fakes.plugin_env import ECHO, FIXTURE, isolate


@pytest.fixture(autouse=True)
def scratch_config(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _kpp(path: Path, *, files=None, record=None, manifest=None, version="1.0.0") -> Path:
    """A package of the fixture plugin, with whatever the test wants changed."""
    data = json.loads((FIXTURE / "plugin.json").read_text())
    data["version"] = version
    if manifest:
        data.update(manifest)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("kpp.json", json.dumps(
            record if record is not None else {"format": KPP_FORMAT_VERSION, "id": ECHO}
        ))
        archive.writestr("plugin.json", json.dumps(data))
        archive.writestr("main.py", (FIXTURE / "main.py").read_text())
        archive.writestr("agreement.md", (FIXTURE / "agreement.md").read_text())
        for name, payload in (files or {}).items():
            archive.writestr(name, payload)
    return path


def _nothing_written():
    root = plugins_root()
    return not root.exists() or not any(
        child for child in root.iterdir() if not child.name.startswith(".")
    )


# -- a good package ------------------------------------------------------------


def test_inspect_reads_without_writing(tmp_path):
    found = inspect(_kpp(tmp_path / "echo.kpp"))

    assert found.manifest.id == ECHO
    assert found.files == 4 and found.size > 0
    assert found.installed_version == ""
    assert _nothing_written()


def test_install_puts_the_plugin_where_the_host_looks(tmp_path):
    manifest = install(_kpp(tmp_path / "echo.kpp"))

    assert manifest.id == ECHO
    assert (plugin_dir(ECHO) / "plugin.json").is_file()
    assert not (plugin_dir(ECHO) / "kpp.json").exists(), "the container record is not plugin code"
    assert [c.name for c in plugins_root().iterdir()] == [ECHO], "no staging left behind"


def test_inspect_says_which_version_it_would_replace(tmp_path):
    install(_kpp(tmp_path / "one.kpp", version="1.0.0"))

    assert inspect(_kpp(tmp_path / "two.kpp", version="2.0.0")).installed_version == "1.0.0"


# -- hostile packages ------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "../evil.py",
    "lib/../../evil.py",
    "/etc/evil",
    "C:/evil.py",
    "lib\\evil.py",
])
def test_an_unsafe_name_is_refused_before_anything_is_written(tmp_path, name):
    package = _kpp(tmp_path / "bad.kpp", files={name: "boom"})

    with pytest.raises(InstallError):
        install(package)

    assert _nothing_written()
    assert not (tmp_path / "evil.py").exists()


def test_a_symlink_is_refused(tmp_path):
    package = _kpp(tmp_path / "bad.kpp")
    with zipfile.ZipFile(package, "a") as archive:
        info = zipfile.ZipInfo("lib/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")

    with pytest.raises(InstallError, match="symbolic link"):
        install(package)
    assert _nothing_written()


def test_a_name_twice_is_refused_even_in_different_case(tmp_path):
    with pytest.raises(InstallError, match="twice"):
        install(_kpp(tmp_path / "bad.kpp", files={"Main.py": "x"}))


def test_too_many_files_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "MAX_FILES", 5)
    with pytest.raises(InstallError, match="more files"):
        install(_kpp(tmp_path / "bad.kpp", files={f"lib/{n}.py": "" for n in range(3)}))


def test_too_many_bytes_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "MAX_TOTAL_BYTES", 1024)
    with pytest.raises(InstallError, match="larger"):
        install(_kpp(tmp_path / "bad.kpp", files={"lib/big.bin": "x" * 4096}))


def test_not_a_zip_is_refused(tmp_path):
    package = tmp_path / "bad.kpp"
    package.write_bytes(b"this is not a zip")
    with pytest.raises(InstallError, match="not a zip"):
        inspect(package)


@pytest.mark.parametrize("record, message", [
    ({}, "format"),
    ({"format": KPP_FORMAT_VERSION + 1}, "newer"),
    ({"format": 0}, "no longer"),
])
def test_the_container_format_is_checked_first(tmp_path, record, message):
    with pytest.raises(InstallError, match=message):
        install(_kpp(tmp_path / "bad.kpp", record=record))


def test_a_package_without_a_record_is_not_a_plugin(tmp_path):
    package = tmp_path / "bad.kpp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("plugin.json", "{}")
    with pytest.raises(InstallError, match="kpp.json"):
        inspect(package)


def test_an_invalid_manifest_is_refused(tmp_path):
    with pytest.raises(InstallError, match="id"):
        install(_kpp(tmp_path / "bad.kpp", manifest={"id": "../escape"}))
    assert _nothing_written()


# -- upgrades --------------------------------------------------------------------


def test_an_upgrade_replaces_the_installed_copy_and_stops_it_first(tmp_path):
    install(_kpp(tmp_path / "one.kpp", version="1.0.0"))
    stopped: list[str] = []

    install(_kpp(tmp_path / "two.kpp", version="2.0.0"), stop=stopped.append)

    assert stopped == [ECHO]
    assert json.loads((plugin_dir(ECHO) / "plugin.json").read_text())["version"] == "2.0.0"


def test_a_failed_upgrade_leaves_the_old_version_working(tmp_path, monkeypatch):
    install(_kpp(tmp_path / "one.kpp", version="1.0.0"))
    real_rename = Path.rename

    def failing_rename(self, target):
        # The staging folder moving into place is the step that fails.
        if self.name.startswith(installer.STAGING_PREFIX):
            raise OSError("disk full")
        return real_rename(self, target)

    # Scoped, not undone: `monkeypatch.undo()` would also undo the isolation
    # above and point every path back at the real configuration folder.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", failing_rename)
        with pytest.raises(InstallError, match="disk full"):
            install(_kpp(tmp_path / "two.kpp", version="2.0.0"))

    assert json.loads((plugin_dir(ECHO) / "plugin.json").read_text())["version"] == "1.0.0"
    assert [c.name for c in plugins_root().iterdir()] == [ECHO], "no debris"


def test_leftovers_of_a_crashed_install_are_swept(tmp_path):
    root = plugins_root()
    (root / f"{installer.STAGING_PREFIX}{ECHO}-dead").mkdir(parents=True)
    (root / f"{installer.BACKUP_PREFIX}{ECHO}-dead").mkdir()

    installer.sweep_leftovers()

    assert list(root.iterdir()) == []


# -- uninstall -------------------------------------------------------------------


def test_uninstall_removes_code_and_keeps_the_vaults_record(tmp_path):
    from app.plugins.host import PluginHost

    install(_kpp(tmp_path / "echo.kpp"))
    store = SettingsStore({}, {})
    host = PluginHost(store, guard_main_thread=False)
    host.accept_consent(ECHO)
    host.set_enabled(ECHO, True)
    host.set_setting(ECHO, "mode", "normal")
    consent = store.plugin_get(ECHO, "consent")

    assert uninstall(ECHO, stop=host.stop) is True
    host.discover()

    assert not plugin_dir(ECHO).exists()
    assert store.plugin_get(ECHO, "enabled") is True
    assert store.plugin_get(ECHO, "consent") == consent
    assert store.plugin_get(ECHO, "settings") == {"mode": "normal"}
    assert host.record(ECHO).source == "missing"


def test_uninstalling_a_running_plugin_stops_it_first(tmp_path):
    from app.plugins.host import PluginHost

    install(_kpp(tmp_path / "echo.kpp"))
    store = SettingsStore({}, {})
    host = PluginHost(store, guard_main_thread=False)
    host.accept_consent(ECHO)
    host.set_enabled(ECHO, True)
    process = None
    try:
        host.call(ECHO, "ping")
        process = host.record(ECHO).process
        assert process.running

        uninstall(ECHO, stop=host.stop)

        assert not process.running
        assert not plugin_dir(ECHO).exists()
    finally:
        host.shutdown()


def test_uninstalling_what_is_not_there_says_so(tmp_path):
    assert uninstall("nothing.here") is False


# -- reinstalling ------------------------------------------------------------------


def test_reinstalling_brings_back_what_it_was(tmp_path):
    """The other half of "uninstall keeps your data": the same consent applies."""
    from app.plugins.host import PluginHost

    install(_kpp(tmp_path / "echo.kpp"))
    store = SettingsStore({}, {})
    host = PluginHost(store, guard_main_thread=False)
    host.accept_consent(ECHO)
    host.set_enabled(ECHO, True)
    uninstall(ECHO, stop=host.stop)
    host.discover()
    assert not host.usable(ECHO)

    install(_kpp(tmp_path / "echo.kpp"))
    host.discover()

    assert host.usable(ECHO), "enabled and agreed to, as it was"
