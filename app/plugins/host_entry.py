"""Run one Python plugin: ``host_entry.py <plugin dir>``.

The interpreter a Python plugin runs under, and the reason it is a separate
entry point rather than ``python main.py``. It **imports nothing from app** —
not by convention but because it cannot: from source it is started as

    python -I -S app/plugins/host_entry.py <dir>

where ``-I`` keeps the user's environment and the script's own folder off
``sys.path`` and ``-S`` keeps site-packages off it too. A frozen build starts
the ``kroma-plugin-host`` executable instead, which is this file frozen with the
standard library and the SDK and nothing else of Kroma's.

So a plugin sees exactly three things: the standard library, the SDK
(``kroma_plugin``), and its own ``lib/`` folder, where its build vendors the
pure-Python packages it needs. That is the same in a source checkout and in a
release, which is the property that matters — a plugin that only worked because
the developer's interpreter happened to have ``requests`` installed would fail
for every user.

Then it changes into the plugin's folder and runs ``main.py`` (or whatever
``backend.main`` names) as ``__main__``, which is what lets a plugin be written
as an ordinary script that ends in ``plugin.run()``.
"""
from __future__ import annotations

import json
import os
import runpy
import sys


def _sdk_dir() -> str:
    """Where ``kroma_plugin`` lives when this runs from a source checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, os.pardir, "sdk", "python"))


def _entry(plugin_dir: str) -> str:
    """The backend's entry file, from the manifest; ``main.py`` if it says none."""
    try:
        with open(os.path.join(plugin_dir, "plugin.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        main = (manifest.get("backend") or {}).get("main") or "main.py"
    except (OSError, ValueError, AttributeError):
        main = "main.py"
    return os.path.join(plugin_dir, main)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: host_entry.py <plugin dir>\n")
        return 2
    plugin_dir = os.path.abspath(argv[1])
    entry = _entry(plugin_dir)
    if not os.path.isfile(entry):
        sys.stderr.write(f"no backend entry at {entry}\n")
        return 2

    # The plugin's own packages first, so a plugin may pin a newer copy of
    # something the SDK or the standard library also offers. Then the SDK, when
    # it is not already importable because this is the frozen host.
    sys.path.insert(0, os.path.join(plugin_dir, "lib"))
    sys.path.insert(0, plugin_dir)
    sdk = _sdk_dir()
    if os.path.isdir(sdk) and sdk not in sys.path:
        sys.path.append(sdk)

    os.chdir(plugin_dir)
    sys.argv = [entry, *argv[2:]]
    runpy.run_path(entry, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
