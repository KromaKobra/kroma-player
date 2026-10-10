"""A plugin's terms: agreed to on purpose, against the text that was shown.

The download agreement used to be one boolean and a version number the app
bumped by hand. It is a plugin's now — any plugin with a ``consent`` file — and
the rules carried over and tightened:

* **What is recorded is a hash of the text**, with the moment it was agreed. A
  plugin upgrade that changes the terms changes the hash, and the next launch
  asks again; one that changes nothing else does not.
* **Agreement is flushed at once.** A consent a crash forgot would be asked for
  again, and one recorded but never saved could be relied on by a running
  download that the next launch disowns.
* **Nothing a GUI can reach writes it.** The ``plugins`` scope has no path from
  ``settingsVM`` — see also ``test_settings_documents``.
* **Three gates, not one.** The host will not start the plugin without
  consent; the audio provider checks again immediately before ``audio.get``;
  and the SDK refuses ``audio.get`` unless ``initialize`` said so. Each is
  tested on its own here, because the point of three is that any one of them
  would still hold if the other two were rearranged away.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.plugins.host import KEY_CONSENT, consent_hash, default_command
from app.plugins.process import ERROR_CONSENT_REQUIRED, PluginError, PluginProcess
from app.plugins.providers import AudioSource, ConsentRequired, ProviderRegistry
from app.settings.store import SettingsStore
from app.viewmodels.plugins_view_model import PluginsViewModel
from app.viewmodels.settings_view_model import SettingsViewModel
from tests.fakes.plugin_env import ECHO, copy_fixture, dev_host, isolate

pytestmark = pytest.mark.plugin_process


# -- the record --------------------------------------------------------------


def test_agreeing_records_the_hash_of_the_text_and_when(tmp_path, monkeypatch):
    store = SettingsStore({}, {})
    host, checkout = dev_host(tmp_path, monkeypatch, store=store, enable=False, consent=False)

    assert host.accept_consent(ECHO) is True

    recorded = store.plugin_get(ECHO, KEY_CONSENT)
    assert recorded["sha256"] == consent_hash((checkout / "agreement.md").read_bytes())
    assert recorded["at"], "and when"
    assert host.has_consent(ECHO)


def test_agreeing_is_written_to_disk_at_once(tmp_path, monkeypatch):
    from app.settings.file import settings_path

    store = SettingsStore({}, {})
    host, _ = dev_host(tmp_path, monkeypatch, store=store, enable=False, consent=False)

    host.accept_consent(ECHO)

    saved = json.loads(settings_path().read_text(encoding="utf-8"))
    assert saved["plugins"][ECHO][KEY_CONSENT]["sha256"]


def test_changed_terms_ask_again(tmp_path, monkeypatch):
    host, checkout = dev_host(tmp_path, monkeypatch)
    assert host.usable(ECHO)

    (checkout / "agreement.md").write_text("New terms.\n", encoding="utf-8")
    host.discover()

    assert host.needs_consent(ECHO)
    assert not host.usable(ECHO), "a plugin with unagreed terms contributes nothing"


def test_unchanged_terms_do_not_ask_again(tmp_path, monkeypatch):
    host, checkout = dev_host(tmp_path, monkeypatch)
    manifest = json.loads((checkout / "plugin.json").read_text())
    manifest["version"] = "1.0.1"
    (checkout / "plugin.json").write_text(json.dumps(manifest))
    host.discover()

    assert host.has_consent(ECHO)


def test_missing_terms_cannot_be_agreed_to(tmp_path, monkeypatch):
    """A manifest that promises terms the folder lacks stays off."""
    host, checkout = dev_host(tmp_path, monkeypatch, enable=False, consent=False)
    (checkout / "agreement.md").unlink()
    host.discover()

    assert host.accept_consent(ECHO) is False
    assert host.set_enabled(ECHO, True) != ""


# -- who can write it ----------------------------------------------------------


def test_turning_a_plugin_on_asks_for_its_terms_first(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, enable=False, consent=False)

    assert host.set_enabled(ECHO, True) == "consent"
    assert not host.is_enabled(ECHO)


def test_the_gui_is_asked_and_its_answer_is_what_enables(tmp_path, monkeypatch):
    store = SettingsStore({}, {})
    host, _ = dev_host(tmp_path, monkeypatch, store=store, enable=False, consent=False)
    vm = PluginsViewModel(host, ProviderRegistry(host), store)
    asked: list = []
    vm.consent_required.connect(lambda pid, name, text: asked.append((pid, name, text)))

    vm.setEnabled(ECHO, True)

    assert [a[0] for a in asked] == [ECHO]
    assert "agrees to nothing" in asked[0][2], "the plugin's own text, shown"
    assert not host.is_enabled(ECHO)

    vm.acceptConsent(ECHO)

    assert host.is_enabled(ECHO) and host.has_consent(ECHO)


@pytest.mark.parametrize("key", [
    "plugins", f"plugins.{ECHO}.consent", f"../plugins/{ECHO}/consent", KEY_CONSENT,
])
def test_settings_vm_cannot_write_the_plugins_scope(qt_app, key):
    store = SettingsStore({}, {})
    vm = SettingsViewModel(store, "nocturne")

    vm.setValue(key, {"sha256": "forged"})

    assert store.plugin_get(ECHO, KEY_CONSENT) is None
    assert store.plugin_ids() == []


# -- gate one: the host will not start it ---------------------------------------


def test_without_consent_the_process_is_never_started(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, consent=False, enable=False)
    # Enabled behind the host's back, as a hand-edited settings file might.
    host._store.plugin_set(ECHO, "enabled", True)

    with pytest.raises(PluginError, match="agree to its terms"):
        host.call(ECHO, "echo", {"value": 1})

    assert not host.running(ECHO)


# -- gate two: checked again before audio.get ----------------------------------


def test_the_audio_source_checks_again_before_sending(tmp_path, monkeypatch):
    host, checkout = dev_host(tmp_path, monkeypatch)
    source = AudioSource(host, host.record(ECHO).manifest)
    try:
        host.call(ECHO, "ping")
        assert host.running(ECHO)

        # The terms change under a running plugin — an upgrade in place.
        (checkout / "agreement.md").write_text("Changed.\n", encoding="utf-8")
        host._changed()

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises((ConsentRequired, PluginError)):
            source.get({"external_ids": {"echo:video": "Xq7Lm2Pz9Ka"}}, out)
        assert list(out.iterdir()) == [], "nothing was fetched"
    finally:
        host.shutdown()


# -- gate three: the SDK refuses on its own -------------------------------------


def test_the_sdk_refuses_audio_without_consent_from_initialize(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    checkout = copy_fixture(tmp_path)
    process = PluginProcess(
        ECHO, default_command(checkout), cwd=str(checkout), guard_main_thread=False,
    )
    process.start()
    try:
        process.call("initialize", {"api": 13, "consented": False, "settings": {}}, timeout=10)
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(PluginError) as caught:
            process.call("audio.get", {"row": {}, "outputDir": str(out)}, timeout=10)
        assert caught.value.code == ERROR_CONSENT_REQUIRED
        assert list(out.iterdir()) == []
    finally:
        process.shutdown()


def test_with_consent_audio_is_fetched(tmp_path, monkeypatch):
    """The other side of the three gates: agreed to, it simply works."""
    host, _ = dev_host(tmp_path, monkeypatch)
    source = AudioSource(host, host.record(ECHO).manifest)
    out = tmp_path / "out"
    out.mkdir()
    try:
        path, row = source.get({"title": "x", "external_ids": {"echo:video": "Xq7Lm2Pz9Ka"}}, out)
    finally:
        host.shutdown()

    assert path.read_bytes() == b"fixture audio"
    assert row["title"] == "Echoed"
