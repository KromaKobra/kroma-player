"""GUI bundle discovery, compilation, installation, and resolution.

Kroma Player's interface is a *swappable* package loaded at startup rather than
code compiled into the application. A package is one file — a ``.kpi``, built by
:mod:`app.gui.compiler`, mounted into the QML engine's resource tree by
:mod:`app.gui.bundle` — and this package owns everything about building,
finding, validating and choosing one.

See ``docs/GUI_API.md`` for the contract GUI packages are written against.
"""

from app.gui.bundle import BundleError, mount, mount_root, read_records, unmount
from app.gui.compiler import CompilerError, build_bundle, source_fingerprint
from app.gui.gui_paths import (
    BUNDLE_SUFFIX,
    GUI_API_VERSION,
    MANIFEST_FILENAME,
    MIN_GUI_API_VERSION,
    OFFICIAL_GUI_NAME,
    bundled_gui_bundle,
    gui_bundle_path,
    gui_root,
)
from app.gui.installer import ensure_official_gui_installed
from app.gui.manifest import GuiManifest, GuiManifestError, from_bytes, load_manifest
from app.gui.resolver import ResolvedGui, available_guis, resolve_gui

__all__ = [
    "BUNDLE_SUFFIX",
    "GUI_API_VERSION",
    "MANIFEST_FILENAME",
    "MIN_GUI_API_VERSION",
    "OFFICIAL_GUI_NAME",
    "BundleError",
    "CompilerError",
    "GuiManifest",
    "GuiManifestError",
    "ResolvedGui",
    "available_guis",
    "build_bundle",
    "bundled_gui_bundle",
    "ensure_official_gui_installed",
    "from_bytes",
    "gui_bundle_path",
    "gui_root",
    "load_manifest",
    "mount",
    "mount_root",
    "read_records",
    "resolve_gui",
    "source_fingerprint",
    "unmount",
]
