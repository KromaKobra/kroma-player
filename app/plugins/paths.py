"""Where plugins live, on this machine and in the vault.

A plugin's **code** is per machine, under the application data folder beside
``kord-config.json``. It is not kept in the vault the way a GUI is, because it
has platform-specific parts — a program found on this machine's PATH, a vendored
wheel built for this machine's Python — and a vault carried to another computer
would carry code that cannot run there. What the vault does carry is which
plugins are on, their settings and their consent (``settings.json``'s
``plugins`` scope), so a library opened elsewhere can say which plugins it
expects and that they are missing.

A plugin's **data** is per machine too — caches, cookies, whatever it keeps
between runs — and it is the plugin's own: the host creates the folder and
passes its path in ``initialize`` and never reads it.

``KROMA_PLUGIN_DIR`` points at plugin source checkouts, one or several
separated by ``os.pathsep``, and loads them straight from there — the plugin
counterpart of ``KROMA_GUI_DIR``. A folder may be a plugin itself or hold
plugins one level down.

Temporary output — the directory an ``audio.get`` writes its file into — goes
under one root per process, so a crash leaves one folder to sweep rather than a
scatter of ``tmp*`` directories nobody can attribute. :func:`sweep_stale_temp`
clears the roots of processes that are no longer running.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import List

from app.config.kord_config import kord_dir

logger = logging.getLogger(__name__)

DEV_ENV = "KROMA_PLUGIN_DIR"
PLUGINS_DIRNAME = "plugins"
DATA_DIRNAME = "plugin-data"
TEMP_PREFIX = "kroma-plugins-"


def plugins_root() -> Path:
    """The folder holding installed plugins, one subfolder per plugin id."""
    return kord_dir() / PLUGINS_DIRNAME


def plugin_dir(plugin_id: str) -> Path:
    """Where *plugin_id* is installed. May not exist."""
    return plugins_root() / plugin_id


def plugin_data_dir(plugin_id: str) -> Path:
    """The folder *plugin_id* may keep its own files in, created on demand."""
    path = kord_dir() / DATA_DIRNAME / plugin_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def dev_dirs() -> List[Path]:
    """Folders named by ``$KROMA_PLUGIN_DIR``, each either a plugin or a parent of plugins."""
    raw = os.environ.get(DEV_ENV, "")
    found: List[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        root = Path(part).expanduser()
        if (root / "plugin.json").is_file():
            found.append(root)
        elif root.is_dir():
            found.extend(
                child for child in sorted(root.iterdir())
                if (child / "plugin.json").is_file()
            )
        else:
            logger.warning("%s names %s, which is not a plugin or a folder", DEV_ENV, root)
    return found


def temp_root() -> Path:
    """This process's folder for plugin output, created on demand."""
    path = Path(tempfile.gettempdir()) / f"{TEMP_PREFIX}{os.getpid()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sweep_stale_temp() -> int:
    """Remove the temp roots of Kroma processes that have exited. Returns how many.

    Run at startup. A root belongs to a pid, and a pid that is not running
    cannot be using it; a recycled pid is the one way this keeps a folder too
    long, which costs disk until the next launch and nothing else.
    """
    base = Path(tempfile.gettempdir())
    removed = 0
    try:
        candidates = list(base.glob(f"{TEMP_PREFIX}*"))
    except OSError:
        return 0
    for candidate in candidates:
        suffix = candidate.name[len(TEMP_PREFIX):]
        if not suffix.isdigit() or _pid_alive(int(suffix)):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed
