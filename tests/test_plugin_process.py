"""One plugin process, and the host that starts, restarts and stops it.

Every test here starts the real fixture plugin through the real ``host_entry``
— ``python -I -S``, the SDK, the pipes — because what is under test is mostly
what happens *between* two processes: replies arriving out of order, a cancel
crossing a request still in flight, a process dying with calls outstanding, a
library printing where the protocol lives. A stand-in for the other process
could only ever agree with itself.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.plugins import host as host_module
from app.plugins.host import PluginHost, default_command, safe_mode_requested
from app.plugins.process import (
    MainThreadCall,
    PluginCancelled,
    PluginCrashed,
    PluginError,
    PluginProcess,
    PluginTimeout,
)
from app.settings.store import SettingsStore
from tests.fakes.plugin_env import ECHO, copy_fixture, dev_host, isolate

pytestmark = pytest.mark.plugin_process

TIMEOUT = 10.0


@pytest.fixture
def process(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    checkout = copy_fixture(tmp_path)
    proc = PluginProcess(ECHO, default_command(checkout), cwd=str(checkout),
                         guard_main_thread=False)
    proc.start()
    proc.call("initialize", {"api": 13, "settings": {}, "consented": True}, timeout=TIMEOUT)
    yield proc
    proc.shutdown(grace=0.5)


def _in_thread(fn):
    """Run *fn* on a worker thread; return (thread, box holding result or error)."""
    box: dict = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — the test inspects it
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


# -- the protocol --------------------------------------------------------------


def test_a_request_gets_its_answer(process):
    assert process.call("echo", {"value": "hi"}, timeout=TIMEOUT)["echo"] == "hi"
    assert process.call("ping", timeout=TIMEOUT) == {"pong": True}


def test_replies_arrive_out_of_order_and_each_finds_its_caller(process):
    """A thirty-second download must not hold up an echo sent after it."""
    slow, slow_box = _in_thread(lambda: process.call("sleep", {"seconds": 1.0}, timeout=TIMEOUT))
    time.sleep(0.1)
    started = time.monotonic()
    assert process.call("echo", {"value": 2}, timeout=TIMEOUT)["echo"] == 2
    assert time.monotonic() - started < 0.8, "the echo waited for the sleep"

    slow.join(TIMEOUT)
    assert slow_box.get("result") == {"slept": True}


def test_many_concurrent_calls_are_matched_up(process):
    results: dict = {}

    def call(n):
        results[n] = process.call("echo", {"value": n}, timeout=TIMEOUT)["echo"]

    threads = [threading.Thread(target=call, args=(n,)) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)

    assert results == {n: n for n in range(20)}


def test_the_plugins_own_error_comes_back_with_its_code(process):
    with pytest.raises(PluginError) as caught:
        process.call("fail", timeout=TIMEOUT)

    assert caught.value.code == -32050
    assert caught.value.data == {"why": "test"}
    assert "asked to fail" in str(caught.value)


def test_an_unknown_method_is_an_error_not_a_hang(process):
    with pytest.raises(PluginError, match="Method not found"):
        process.call("no.such.method", timeout=TIMEOUT)


def test_a_huge_answer_arrives_whole(process):
    assert len(process.call("huge", {"size": 2_000_000}, timeout=TIMEOUT)["data"]) == 2_000_000


def test_a_plugin_that_prints_does_not_corrupt_the_protocol(process):
    """stdout belongs to the protocol; the SDK points everything else at stderr."""
    assert process.call("print", timeout=TIMEOUT) == {"ok": True}
    assert process.call("echo", {"value": "still fine"}, timeout=TIMEOUT)["echo"] == "still fine"


def test_progress_reaches_the_caller(process):
    seen: list = []
    thread, box = _in_thread(lambda: process.call(
        "sleep", {"seconds": 0.3}, timeout=TIMEOUT, on_progress=seen.append,
    ))
    thread.join(TIMEOUT)

    assert box.get("result") == {"slept": True}
    assert seen and seen[0]["stage"] == "sleeping"


# -- stopping a request ----------------------------------------------------------


def test_should_stop_cancels_the_request_in_the_plugin(process):
    stop = threading.Event()
    thread, box = _in_thread(lambda: process.call(
        "sleep", {"seconds": 30}, timeout=TIMEOUT, should_stop=stop.is_set,
    ))
    time.sleep(0.2)
    stop.set()
    thread.join(TIMEOUT)

    assert isinstance(box.get("error"), PluginCancelled)
    # The plugin got `cancel` and stopped, rather than sleeping on: it answers
    # the next request at once.
    assert process.call("echo", {"value": 1}, timeout=2)["echo"] == 1


def test_a_timeout_cancels_rather_than_abandoning(process):
    with pytest.raises(PluginTimeout):
        process.call("sleep", {"seconds": 30}, timeout=0.3)
    assert process.pending_count == 0
    assert process.call("echo", {"value": 1}, timeout=2)["echo"] == 1


# -- the process going away ----------------------------------------------------


def test_a_crash_fails_every_pending_call(process):
    waiting, box = _in_thread(lambda: process.call("sleep", {"seconds": 30}, timeout=TIMEOUT))
    time.sleep(0.2)

    with pytest.raises(PluginCrashed):
        process.call("crash", timeout=TIMEOUT)
    waiting.join(TIMEOUT)

    assert isinstance(box.get("error"), PluginCrashed), "the sleeper woke with an error"
    assert process.wait_exit(TIMEOUT)
    assert process.exit_code == 7


def test_shutdown_unblocks_a_waiting_worker(process):
    waiting, box = _in_thread(lambda: process.call("sleep", {"seconds": 30}, timeout=TIMEOUT))
    time.sleep(0.2)

    started = time.monotonic()
    process.shutdown(grace=0.5)
    waiting.join(TIMEOUT)

    assert isinstance(box.get("error"), PluginCrashed)
    assert time.monotonic() - started < 3
    assert not process.running


def test_closing_stdin_ends_the_plugin(process):
    """A plugin never outlives its host: EOF on stdin is the end of it."""
    process._outbox.put(None)

    assert process.wait_exit(TIMEOUT)
    assert process.exit_code == 0


def test_a_blocking_call_from_the_main_thread_is_refused(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    checkout = copy_fixture(tmp_path)
    proc = PluginProcess(ECHO, default_command(checkout), cwd=str(checkout))
    proc.start()
    try:
        with pytest.raises(MainThreadCall):
            proc.call("ping")
        thread, box = _in_thread(lambda: proc.call(
            "initialize", {"api": 13, "settings": {}}, timeout=TIMEOUT,
        ))
        thread.join(TIMEOUT)
        assert "result" in box, box.get("error")
    finally:
        proc.shutdown(grace=0.5)


# -- the host ----------------------------------------------------------------------


def test_the_host_starts_a_plugin_lazily_and_once(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch)
    try:
        assert not host.running(ECHO), "nothing starts until something asks"

        pids = set()
        threads = [threading.Thread(target=lambda: pids.add(
            host.call(ECHO, "echo", {"value": 1})["pid"])) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)

        assert len(pids) == 1, "two workers arriving together started two processes"
        assert host.running(ECHO)
    finally:
        host.shutdown()


def test_initialize_carries_settings_programs_and_consent(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, settings={"mode": "normal"})
    try:
        answer = host.call(ECHO, "settings")
    finally:
        host.shutdown()

    assert answer["settings"] == {"mode": "normal"}
    assert answer["consented"] is True
    # Looked for, and not found — the plugin is told so rather than left to search.
    assert answer["programs"] == {"definitely-not-installed-kroma-test": None}
    assert answer["dataDir"].endswith(ECHO)


def test_a_crash_is_restarted_by_the_next_call(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch)
    try:
        first = host.call(ECHO, "echo", {"value": 1})["pid"]
        with pytest.raises(PluginCrashed):
            host.call(ECHO, "crash")
        assert host.record(ECHO).process.wait_exit(TIMEOUT)

        second = host.call(ECHO, "echo", {"value": 2})["pid"]
        assert second != first
    finally:
        host.shutdown()


def test_a_plugin_that_keeps_crashing_is_backed_off(tmp_path, monkeypatch):
    """Crash, restart, crash: the next call fails fast instead of restarting.

    A plugin that answers between crashes is not "keeps crashing" — a call that
    succeeds resets the count — so the two crashes here come back to back.
    """
    host, _ = dev_host(tmp_path, monkeypatch)
    try:
        for expected in (1, 2):
            with pytest.raises(PluginCrashed):
                host.call(ECHO, "crash")
            deadline = time.monotonic() + TIMEOUT
            while host.record(ECHO).crashes < expected and time.monotonic() < deadline:
                time.sleep(0.01)
            assert host.record(ECHO).crashes == expected

        started = time.monotonic()
        with pytest.raises(PluginCrashed, match="keeps stopping"):
            host.call(ECHO, "echo", {"value": 3})
        assert time.monotonic() - started < 0.5, "a backed-off call fails fast"
    finally:
        host.shutdown()


def test_three_startup_crashes_switch_a_plugin_off(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, settings={"mode": "crash-on-start"})
    monkeypatch.setattr(host_module.PluginHost, "_backoff", staticmethod(lambda crashes: 0.0))
    crashed: list = []
    host.plugin_crashed.connect(lambda pid, reason: crashed.append(pid))
    try:
        for _ in range(3):
            with pytest.raises(PluginCrashed):
                host.call(ECHO, "echo", {"value": 1})
    finally:
        host.shutdown()

    assert crashed == [ECHO] * 3
    assert host.is_enabled(ECHO) is False
    assert "failing to start 3 times" in host.disabled_reason(ECHO)


def test_a_plugin_that_never_answers_initialize_counts_as_a_crash(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, settings={"mode": "silent-start"})
    monkeypatch.setattr(host_module, "INITIALIZE_TIMEOUT", 0.5)
    try:
        with pytest.raises(PluginCrashed, match="did not start in time"):
            host.call(ECHO, "echo", {"value": 1})
        assert host.record(ECHO).startup_crashes == 1
        assert not host.running(ECHO), "and it was not left running"
    finally:
        host.shutdown()


def test_an_idle_plugin_is_stopped(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, idle_seconds=0.0)
    try:
        host.call(ECHO, "ping")
        assert host.running(ECHO)

        host.stop_idle()

        assert not host.running(ECHO)
    finally:
        host.shutdown()


def test_a_busy_plugin_is_not_idle(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, idle_seconds=0.0)
    try:
        host.call(ECHO, "ping")
        thread, box = _in_thread(lambda: host.call(ECHO, "sleep", {"seconds": 0.5},
                                                   job_label="Sleeping"))
        time.sleep(0.15)

        host.stop_idle()
        assert host.running(ECHO), "stopped mid-request"
        assert [job.label for job in host.jobs()] == ["Sleeping"]

        thread.join(TIMEOUT)
        assert box.get("result") == {"slept": True}
        assert host.jobs() == []
    finally:
        host.shutdown()


def test_turning_a_plugin_off_stops_it(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch)
    try:
        host.call(ECHO, "ping")
        host.set_enabled(ECHO, False)

        assert not host.running(ECHO)
        with pytest.raises(PluginError, match="turned off"):
            host.call(ECHO, "ping")
    finally:
        host.shutdown()


def test_settings_reach_a_running_plugin(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch)
    try:
        host.call(ECHO, "ping")
        host.set_setting(ECHO, "mode", "silent-start")
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if host.call(ECHO, "settings")["settings"].get("mode") == "silent-start":
                break
            time.sleep(0.02)
        assert host.call(ECHO, "settings")["settings"]["mode"] == "silent-start"
    finally:
        host.shutdown()


# -- safe mode -----------------------------------------------------------------------


def test_safe_mode_starts_nothing(tmp_path, monkeypatch):
    host, _ = dev_host(tmp_path, monkeypatch, safe_mode=True)
    try:
        assert host.active() == []
        with pytest.raises(PluginError, match="safe mode"):
            host.call(ECHO, "ping")
        assert not host.running(ECHO)
        assert host.is_enabled(ECHO), "safe mode switches nothing off for good"
    finally:
        host.shutdown()


@pytest.mark.parametrize("argv, env, expected", [
    ([], "", False),
    (["kroma", "--safe-mode"], "", True),
    ([], "1", True),
    ([], "0", False),
])
def test_safe_mode_is_asked_for_by_flag_or_environment(monkeypatch, argv, env, expected):
    monkeypatch.setenv("KROMA_SAFE_MODE", env)
    assert safe_mode_requested(argv) is expected


def test_an_incompatible_plugin_is_listed_with_a_reason(tmp_path, monkeypatch):
    import json

    host, checkout = dev_host(tmp_path, monkeypatch, enable=False)
    manifest = json.loads((checkout / "plugin.json").read_text())
    manifest["api"] = 999
    (checkout / "plugin.json").write_text(json.dumps(manifest))
    host.discover()

    record = host.record(ECHO)
    assert record.manifest is not None
    assert "update Kroma Player" in record.error
    assert host.set_enabled(ECHO, True) == record.error


def test_a_plugin_enabled_here_but_not_installed_is_listed_as_missing(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    store = SettingsStore({}, {})
    store.plugin_set("someone.elses", "enabled", True)

    host = PluginHost(store, guard_main_thread=False)

    record = host.record("someone.elses")
    assert record is not None and record.source == "missing"
    assert not host.usable("someone.elses")
