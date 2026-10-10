"""The fixture plugin: every behaviour a plugin might show, on request.

Started by the host's tests through the real ``host_entry`` — so a test that
passes here has passed through the same interpreter flags, the same SDK and the
same pipes a user's plugin would.

``mode`` (a setting, sent in ``initialize``) chooses how it starts:
``normal``, ``crash-on-start`` (exits before answering), or ``silent-start``
(never answers, so ``initialize`` times out).
"""
import os
import time
from pathlib import Path

from kroma_plugin import Plugin, PluginError

plugin = Plugin()


@plugin.on_initialize
def _start(p):
    mode = p.settings.get("mode")
    if mode == "crash-on-start":
        os._exit(3)
    if mode == "silent-start":
        time.sleep(3600)


@plugin.handler("echo")
def echo(request):
    return {"echo": request.params.get("value"), "pid": os.getpid()}


@plugin.handler("sleep")
def sleep(request):
    """Sleeps until told to stop, or for ``seconds``. Reports progress."""
    deadline = time.monotonic() + float(request.params.get("seconds") or 60)
    request.progress("sleeping", 10)
    while time.monotonic() < deadline:
        request.check()
        time.sleep(0.01)
    return {"slept": True}


@plugin.handler("imports")
def imports(request):
    """Which of ``modules`` this interpreter cannot import — the frozen host's
    standard library is collected by hand, so the release smoke test asks."""
    import importlib

    missing = []
    for name in request.params.get("modules") or []:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — every failure is the answer
            missing.append(f"{name}: {exc}")
    return {"missing": missing}


@plugin.handler("crash")
def crash(request):
    os._exit(7)


@plugin.handler("print")
def noisy(request):
    """A library that prints — to stdout, and through a child process too."""
    print("this would corrupt the protocol if it reached it")
    os.system("echo and so would this")
    return {"ok": True}


@plugin.handler("huge")
def huge(request):
    return {"data": "x" * int(request.params.get("size") or 1_000_000)}


@plugin.handler("fail")
def fail(request):
    raise PluginError("asked to fail", code=-32050, data={"why": "test"})


@plugin.handler("settings")
def settings(request):
    return {"settings": plugin.settings, "consented": plugin.consented,
            "programs": plugin.programs, "dataDir": plugin.data_dir}


@plugin.handler("audio.get")
def audio_get(request):
    """Writes a file where it was told to — or, asked to misbehave, elsewhere."""
    out = Path(request.params["outputDir"])
    if request.params.get("row", {}).get("title") == "outside":
        target = out.parent / "escaped.opus"
    else:
        target = out / "track.opus"
    target.write_bytes(b"fixture audio")
    return {"file": str(target), "row": {"title": "Echoed", "artists": ["Echo"]}}


if __name__ == "__main__":
    plugin.run()
