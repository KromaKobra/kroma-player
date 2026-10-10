"""Filesystem layout for GUI packages.

A GUI is one file. ``<vault>/.data/gui/nocturne.kpi`` is the whole package —
every QML file, every asset, the manifest — compiled into a binary Qt resource
and mounted into the QML engine's virtual filesystem at load. See
:mod:`app.gui.bundle` for what a ``.kpi`` is and :mod:`app.gui.compiler` for how
one is made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.utils.library_paths import data_root

# The API contract a GUI declares itself against. Defined in app/versions.py
# with every other version in the system and the rules they follow; re-exported
# here because this is where the rest of the application already imports it
# from, and because a GUI's layout and its contract are read together.
from app.versions import GUI_API_VERSION, MIN_GUI_API_VERSION  # noqa: F401

# The GUI that ships with the application: installed into the user's gui folder
# on first run, used as the default when nothing else is selected, and used as
# the in-bundle fallback when everything else fails to load.
OFFICIAL_GUI_NAME = "nocturne"

# Kroma Player Interface. A public format — every GUI, official or third party,
# is distributed as one of these.
BUNDLE_SUFFIX = ".kpi"

# Files that live *inside* a bundle.
MANIFEST_FILENAME = "gui.json"
BUILD_RECORD_FILENAME = "build.json"

# The build record, written beside a shipped bundle. Its whole purpose is that
# the staleness check at startup never has to open the bundle itself.
SIDECAR_SUFFIX = BUNDLE_SUFFIX + ".json"

# What is installed, and what it looked like when it was installed.
INDEX_FILENAME = "index.json"

# Compiled-QML bytecode, one folder per build id.
CACHE_DIRNAME = ".cache"

# Bundles compiled on the fly from a source checkout named by $KROMA_GUI_DIR.
# A dot-folder so it sits outside the scan that finds installed GUIs: these are
# build products of somebody's working copy, not things to offer in a picker.
DEV_DIRNAME = ".dev"

# Suffix used when an install that can no longer be loaded is set aside.
BACKUP_SUFFIX = ".bak"


def gui_root() -> Path:
    """Return the folder that holds installed GUI bundles."""
    return data_root() / "gui"


def gui_bundle_path(name: str) -> Path:
    """Return the installed bundle for the GUI called *name*."""
    return gui_root() / f"{name}{BUNDLE_SUFFIX}"


def gui_index_path() -> Path:
    """Return the index of what is installed in ``gui_root()``."""
    return gui_root() / INDEX_FILENAME


def gui_cache_root() -> Path:
    """Return the folder holding compiled-QML caches."""
    return gui_root() / CACHE_DIRNAME


def gui_cache_dir(build_id: str) -> Path:
    """Return the QML disk cache folder for *build_id*.

    Keyed by build id rather than by GUI name because Qt validates a cache entry
    against the URL it was compiled from, and a rebuilt bundle mounts at a new
    URL. Without the key the folder would accumulate the bytecode of every build
    that had ever run; with it, the installer can delete exactly the folder
    belonging to the build it just replaced.
    """
    return gui_cache_root() / build_id


def gui_dev_bundle_path(label: str) -> Path:
    """Return where a checkout loaded through ``$KROMA_GUI_DIR`` is compiled to."""
    return gui_root() / DEV_DIRNAME / f"{label}{BUNDLE_SUFFIX}"


def bundled_gui_root() -> Path:
    """Return the folder holding GUIs shipped inside the application.

    Frozen builds unpack data files under ``sys._MEIPASS``; running from source
    it is the ``gui/`` folder at the repository root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "gui"
    # app/gui/gui_paths.py → app/gui → app → repo root
    return Path(__file__).resolve().parents[2] / "gui"


def bundled_gui_bundle(name: str = OFFICIAL_GUI_NAME) -> Path:
    """Return the bundle shipped with the application, whether or not it exists."""
    return bundled_gui_root() / f"{name}{BUNDLE_SUFFIX}"


def bundled_gui_sidecar(name: str = OFFICIAL_GUI_NAME) -> Path:
    """Return the build record shipped beside ``bundled_gui_bundle(name)``."""
    return bundled_gui_root() / f"{name}{SIDECAR_SUFFIX}"


def bundled_gui_source(name: str = OFFICIAL_GUI_NAME) -> Path:
    """Return the GUI's source tree, which only exists in a checkout.

    A frozen build ships the compiled bundle and no QML at all, so this path is
    absent there and every caller has to cope with that. It is what the
    development loop recompiles from — see :mod:`app.gui.compiler`.
    """
    return bundled_gui_root() / name


def is_valid_gui_name(name: str) -> bool:
    """True when *name* is safe to join onto ``gui_root()``.

    GUI names arrive from the environment and from session.json, so they are
    untrusted input: anything that could escape the gui folder is rejected.
    """
    if not name or name in (".", ".."):
        return False
    if name != name.strip():
        return False
    return not any(c in name for c in ("/", "\\", ":", "\0"))
