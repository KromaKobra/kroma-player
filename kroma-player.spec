# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "Kroma Player"
EXE_NAME = "kroma-player"   # no spaces — used for the .exe filename and dist folder
APP_PUBLISHER = "Kroma Kobra"   # matches AppPublisher in packaging/kroma-player.iss

# The version the build is, read from the one place it is declared. The spec is
# run from the repository root, and app/versions.py imports nothing, so this is
# safe before anything else here has run.
sys.path.insert(0, SPECPATH)  # noqa: F821 — defined by PyInstaller
from app.versions import APP_VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Windows version resource
# ---------------------------------------------------------------------------
# What Explorer shows under Properties → Details, and — the reason it matters —
# what the Restart Manager calls the process when an update has to close it:
# without a FileDescription it falls back to the bare file name. Windows only;
# the EXE() calls below take None anywhere else.


def windows_version_info(description, filename):
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = [int(part) for part in APP_VERSION.split(".")[:4]]
    numbers += [0] * (4 - len(numbers))
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tuple(numbers),
            prodvers=tuple(numbers),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,      # VOS_NT_WINDOWS32
            fileType=0x1,    # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", APP_PUBLISHER),
                    StringStruct("FileDescription", description),
                    StringStruct("FileVersion", APP_VERSION),
                    StringStruct("InternalName", filename),
                    StringStruct("OriginalFilename", f"{filename}.exe"),
                    StringStruct("ProductName", APP_NAME),
                    StringStruct("ProductVersion", APP_VERSION),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


# ---------------------------------------------------------------------------
# External executables
# ---------------------------------------------------------------------------
# ffmpeg and ffprobe (audio extraction, transcoding) are programs, not Python
# packages, so nothing collects them automatically. A normal release does not
# ship them: the user installs them once and app/utils/binaries.py finds them on
# PATH — see the README for the instructions that go with a release. Programs a
# *plugin* needs are that plugin's to declare and the user's to install; none of
# them is ever bundled here (ADR 0015).
#
# Dropping copies in vendor/ produces a self-contained build instead, with no
# edit here. Only vendor/ is consulted, never PATH: bundling whatever happened
# to be installed on the build machine would make the contents of a release
# depend on who built it.
VENDOR_DIR = Path("vendor")


def external_binary(stem):
    """Return (source, dest) for a vendored executable, or None if absent."""
    vendored = VENDOR_DIR / f"{stem}.exe"
    return (str(vendored), ".") if vendored.is_file() else None


# ffprobe must land beside ffmpeg: tools that probe media look for it there.
external_binaries = [
    entry
    for entry in map(external_binary, ("ffmpeg", "ffprobe"))
    if entry
]
print(
    "External binaries bundled: "
    + (", ".join(Path(src).name for src, _ in external_binaries) or "none (PATH at runtime)")
)

# certifi's CA bundle is required for HTTPS requests in frozen apps.
certifi_datas = collect_data_files("certifi")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=external_binaries,
    datas=[
        # The official GUI, compiled. `scripts/build-gui` must have been run
        # before packaging: this ships the .kpi bundle, not the QML source, and
        # a frozen build never compiles anything. The sidecar beside it is the
        # build record, which is what lets a launch decide whether the copy in
        # the vault is stale without opening either file.
        #
        # It is installed into the user's gui folder on first run (see
        # app/gui/installer.py) and also serves as the in-bundle fallback if
        # every installed GUI fails to load.
        ("gui/nocturne.kpi", "gui"),
        ("gui/nocturne.kpi.json", "gui"),
        ("assets",    "assets"),
        # The first-run vault picker. Built into the app, not part of a GUI
        # package — it runs before any GUI has been resolved.
        ("app/vault/VaultSetup.qml", "app/vault"),
        # The vocal-removal sidecar. Shipped as a data file rather than as
        # frozen code because it is handed to the *user's* Python interpreter as
        # a path — a frozen build has no source tree to point that child at.
        ("app/separation/sidecar/separate.py", "app/separation/sidecar"),
        *certifi_datas,
    ],
    hiddenimports=[
        # PySide6 modules not always detected by static analysis
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
        "PySide6.QtOpenGL",
        # MPRIS. Imported lazily behind a try/except in app/system/mpris.py, so
        # PyInstaller's static analysis cannot see it. Windows has no session
        # bus and the service simply never starts there, but the module still
        # has to import for the app to boot.
        "PySide6.QtDBus",
        # Pillow's AVIF codec. app/artwork/image_ops.py imports it lazily inside
        # a try, so analysis does not see it — and every cover is AVIF, so its
        # absence surfaces at the first artwork encode rather than at startup.
        "PIL.AvifImagePlugin",
        # SQLAlchemy dialect loaded at runtime
        "sqlalchemy.dialects.sqlite",
        # mutagen codec sub-modules
        "mutagen.mp3",
        "mutagen.mp4",
        "mutagen.flac",
        "mutagen.id3",
        "mutagen.oggvorbis",
        "mutagen.oggopus",
        "mutagen.wave",
    ],
    hookspath=["hooks"],
    hooksconfig={},
    runtime_hooks=["hooks/rthook_set_cwd.py", "hooks/rthook_certifi.py"],
    # Keep the test runner, tkinter, and the bindings for Qt modules the app
    # never imports out of the bundle. This drops the .pyd files only — the Qt
    # DLLs behind them need the prune below.
    excludes=[
        "pytest", "_pytest", "tkinter", "_tkinter",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebView",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
        "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors",
        "PySide6.QtVirtualKeyboard", "PySide6.QtTextToSpeech",
        "PySide6.QtScxml", "PySide6.QtRemoteObjects",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
        "PySide6.QtDesigner", "PySide6.QtUiTools",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    ],
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Trimming Qt
# ---------------------------------------------------------------------------
# PySide6's hook collects the *whole* Qt runtime — every DLL, every QML plugin
# tree — no matter which bindings are imported, so excluding a module above
# removes its .pyd and nothing else. Left alone the bundle is 450 MB, of which
# Qt6WebEngineCore.dll is 195 MB for a browser engine the app never opens.
#
# Named by module family, matched against the Qt6<Family>.dll at the bundle root
# and against the qml/<Family>/ tree each one installs. Deliberately a deny
# list, not a keep list: an unknown Qt DLL is far more likely to be something
# another one links against than something safe to drop.
#
# This is also why the build needs the full PySide6 rather than
# PySide6-Essentials: QtMultimedia — the entire playback backend — ships in
# PySide6-Addons. Without it the Qt hook fails silently and still exits zero,
# leaving a bundle with no shiboken6, no platform plugin and no QML.
UNUSED_QT_MODULES = (
    "WebEngine", "WebView", "WebChannel",
    "3D",                        # Qt63DCore, Qt63DRender, Qt6Quick3D*, …
    "Charts", "DataVisualization", "Graphs",
    "Pdf", "Location", "Positioning",
    "Sensors", "VirtualKeyboard", "TextToSpeech",
    "Scxml", "StateMachine", "RemoteObjects",
    "Test", "Sql",
)


def unused_qt(dest):
    """True for a collected file belonging to a Qt module the app never loads."""
    path = dest.replace("\\", "/")
    if not path.startswith("PySide6/"):
        return False
    tail = path[len("PySide6/"):]

    # Qt's own UI translations, for a UI that ships in English and never builds
    # a QTranslator; and QtWebEngine's resource packs, one of which is a 75 MB
    # devtools debug file.
    if tail.startswith(("translations/", "resources/")):
        return True

    # "Qt6WebEngineCore.dll" -> "Qt6WebEngineCore"; "qml/QtQuick3D/…" -> "QtQuick3D".
    parts = tail.split("/")
    if tail.startswith("Qt6"):
        subject = parts[0]
    elif parts[0] == "qml" and len(parts) > 1:
        subject = parts[1]
    else:
        return False

    return any(module in subject for module in UNUSED_QT_MODULES)


a.binaries = [entry for entry in a.binaries if not unused_qt(entry[0])]
a.datas = [entry for entry in a.datas if not unused_qt(entry[0])]

pyz = PYZ(a.pure, a.zipped_data)

# ---------------------------------------------------------------------------
# kroma-plugin-host
# ---------------------------------------------------------------------------
# The interpreter every Python plugin runs under in a release — see
# app/plugins/host_entry.py, which is this executable's whole program. It ships
# beside kroma-player and shares its folder, but it is frozen from a second
# Analysis so that *none* of Kroma comes with it: no PySide6, no app package,
# nothing a plugin could lean on that a source checkout (`python -I -S`) would
# not also give it.
#
# What it does carry is the standard library — all of it that exists on the
# build platform, not just what host_entry.py imports, because the plugin's own
# vendored packages are what will import from it and static analysis cannot see
# them — and the SDK, `kroma_plugin`. A plugin's third-party packages live in
# its own lib/ and are never frozen into anything.
_NOT_FOR_PLUGINS = {
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test",
    "lib2to3", "ensurepip", "venv", "pydoc_data", "this", "antigravity",
    "msilib", "_msi", "nis", "spwd", "ossaudiodev",
}


def _stdlib_imports():
    names = []
    for name in sorted(getattr(sys, "stdlib_module_names", ())):
        if name in _NOT_FOR_PLUGINS or name.startswith("__"):
            continue
        try:
            __import__(name)
        except Exception:
            continue  # not on this platform
        names.append(name)
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "__path__"):
            names.extend(
                sub for sub in collect_submodules(name)
                if not any(part in _NOT_FOR_PLUGINS or part == "tests"
                           for part in sub.split("."))
            )
    return names


plugin_host = Analysis(
    ["app/plugins/host_entry.py"],
    pathex=["sdk/python"],
    binaries=[],
    datas=[*certifi_datas],
    hiddenimports=["kroma_plugin", *_stdlib_imports()],
    hookspath=[],
    hooksconfig={},
    # certifi, so a plugin's HTTPS verifies against the bundled roots. Not
    # set_cwd: host_entry changes into the plugin's own folder, and a hook that
    # moved it into the bundle first would only be undone.
    runtime_hooks=["hooks/rthook_certifi.py"],
    excludes=["PySide6", "shiboken6", "app", "pytest", "_pytest", *_NOT_FOR_PLUGINS],
    noarchive=False,
)
plugin_host_pyz = PYZ(plugin_host.pure, plugin_host.zipped_data)
plugin_host_exe = EXE(
    plugin_host_pyz,
    plugin_host.scripts,
    [],
    exclude_binaries=True,
    name="kroma-plugin-host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console program: it speaks JSON-RPC on stdin and stdout. Kroma starts it
    # with CREATE_NO_WINDOW (app/utils/proc.py), so no console ever appears.
    console=True,
    version=windows_version_info(f"{APP_NAME} plugin host", "kroma-plugin-host"),
)

# onedir, not onefile: onefile re-extracts the whole bundle to a temp directory
# on every launch before the window can appear, which delays the window by as
# long as it takes to write a few hundred megabytes. A folder pays none of that.
# Zip dist/kroma-player/ to hand it to someone.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is off deliberately: packed Qt DLLs are a reliable source of both
    # antivirus false positives and outright load failures.
    upx=False,
    console=False,       # windowed — no terminal window. Flip to True to see
                         # tracebacks when debugging a frozen build; on Windows
                         # they also land in %LOCALAPPDATA%\kord\logs.
    icon="assets/icon.ico",
    version=windows_version_info(APP_NAME, EXE_NAME),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    # The plugin host, in the same folder: `default_command` in
    # app/plugins/host.py looks for it beside sys.executable. Shared binaries —
    # the Python runtime above all — are collected once.
    plugin_host_exe,
    plugin_host.binaries,
    plugin_host.zipfiles,
    plugin_host.datas,
    strip=False,
    upx=False,
    name=EXE_NAME,
)
