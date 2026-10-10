"""A real plugin host over a scratch folder, for the tests that start processes.

The plugin code folder is per machine — under ``kord_dir()`` — so it is moved
into the test's own temp directory through ``XDG_CONFIG_HOME``, which every
path in :mod:`app.plugins.paths` resolves through. Nothing a test installs or
starts can reach the developer's own plugins.

The fixture plugin is copied, not pointed at, so a test can edit its manifest or
its terms without touching the checked-in copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.plugins.host import PluginHost
from app.settings.store import SettingsStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo"
ECHO = "test.echo"


def isolate(tmp_path: Path, monkeypatch) -> Path:
    """Point the per-machine plugin folders at *tmp_path*. Returns the config root."""
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.delenv("KROMA_PLUGIN_DIR", raising=False)
    monkeypatch.delenv("KROMA_SAFE_MODE", raising=False)
    return config


def copy_fixture(tmp_path: Path, name: str = "echo") -> Path:
    target = tmp_path / "checkouts" / name
    shutil.copytree(FIXTURE, target)
    return target


def dev_host(tmp_path: Path, monkeypatch, *, store=None, enable=True, consent=True,
             settings=None, **kwargs) -> tuple[PluginHost, Path]:
    """A host with the fixture plugin loaded through ``$KROMA_PLUGIN_DIR``.

    Calls are allowed from the test's own (main) thread — the guard has its own
    test. Enabled and agreed to unless told otherwise.
    """
    isolate(tmp_path, monkeypatch)
    checkout = copy_fixture(tmp_path)
    monkeypatch.setenv("KROMA_PLUGIN_DIR", str(checkout))
    store = store if store is not None else SettingsStore({}, {})
    if settings:
        store.plugin_set(ECHO, "settings", dict(settings))
    kwargs.setdefault("guard_main_thread", False)
    host = PluginHost(store, **kwargs)
    if consent:
        host.accept_consent(ECHO)
    if enable:
        assert host.set_enabled(ECHO, True) == "", host.record(ECHO).error
    return host, checkout
