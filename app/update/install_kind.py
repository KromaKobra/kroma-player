"""How this copy of Kroma Player got onto the machine.

Everything else in :mod:`app.update` asks this first, because the answer decides
both whether updating is possible and *how* it would be done. There is no single
mechanism that works everywhere: a Windows installer replaces its own files
through the Restart Manager, an AppImage is one file that can be renamed over
while it runs, and a copy that some package manager put there must be left
alone because something else already owns keeping it current.

The kinds, and what each one means for the button in Settings → System:

``SOURCE``
    Running from a checkout. ``git pull`` is the update, and it is not this
    module's business.
``MANAGED``
    Under ``/nix/store``, ``/usr``, ``/opt``, ``/snap``, a Flatpak root. Nix,
    apt, dnf or Flatpak owns the files; writing into them would either fail or —
    worse, on Nix — be silently discarded at the next rebuild. ADR 0012 pushed
    a dependency out to the tool that owns it; this is the same move, and this
    is the branch that makes it.
``WINDOWS_INSTALLER``
    Installed by ``packaging/kroma-player.iss``. Updatable: run the new
    installer over the top.
``APPIMAGE``
    Updatable: replace the file ``$APPIMAGE`` points at and re-exec.
``PORTABLE``
    A frozen tree that nothing owns — an unzipped release folder. Technically
    replaceable, and deliberately not attempted: swapping a directory out from
    under a running process is the fragile mechanism this whole design exists to
    avoid. The pane says so and links to the releases page.

Detection is most-specific-first and the order is load-bearing in one place: an
AppImage the user dropped in ``/opt`` is still an AppImage, and ``$APPIMAGE``
has to be consulted before the path is. The rest of the order is only
readability.

Cached for the life of the process, in the idiom of
:mod:`app.utils.binaries` — none of these answers can change while the
application is running, and the Windows arm reads the registry.
"""

from __future__ import annotations

import logging
import ntpath
import os
import sys
from enum import StrEnum
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Set to anything non-empty to switch the feature off entirely. Mirrors
# ``KROMA_NO_GUI_BUILD`` in :mod:`app.gui.compiler`: one line for a packager to
# put in a derivation, so a distribution that ships Kroma does not have to patch
# Python to stop the app offering updates it cannot install.
ENV_NO_UPDATE = "KROMA_NO_UPDATE"

# Prefixes that mean something else installed this. ``/app`` is where Flatpak
# mounts the application inside its sandbox, and ``/var/lib/flatpak`` is where
# the host keeps the installation.
MANAGED_PREFIXES = (
    "/nix/store",
    "/usr",
    "/opt",
    "/snap",
    "/var/lib/flatpak",
    "/app",
)

# The Inno Setup uninstall key, derived from ``AppId`` in
# ``packaging/kroma-player.iss``. Inno appends ``_is1`` to the AppId; the two
# must stay in step, and changing the AppId is forbidden for exactly this kind
# of reason.
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{CC0544F7-D2E1-41E7-AE17-1219E3363D15}_is1"
)

# Where an uninstall entry says the copy it describes lives. Inno writes both;
# the first is its own and is always the bare folder, the second is the
# standard one Windows shows and may carry a trailing backslash.
LOCATION_VALUES = ("Inno Setup: App Path", "InstallLocation")

# The two scopes an install can have, as installer_scope() reports them. They
# are also what decides the switch apply.installer_argv() adds, so an update
# lands where the copy being updated is rather than where the installer would
# default to.
SCOPE_USER = "user"
SCOPE_MACHINE = "machine"


class InstallKind(StrEnum):
    """How this copy was installed. The values are the ``kind`` strings in
    ``release.json``, so an asset can be matched to an install without a
    translation table in between."""

    SOURCE = "source"
    MANAGED = "managed"
    WINDOWS_INSTALLER = "windows_installer"
    APPIMAGE = "appimage"
    PORTABLE = "portable"


def install_root() -> Path | None:
    """The folder holding the application, or ``None`` when running from source.

    Follows the :mod:`app.utils.binaries` idiom — ``sys.frozen`` then
    ``Path(sys.executable).parent``. Deliberately **not** ``sys._MEIPASS``: this
    is a onedir build, where that resolves to the ``_internal/`` subfolder
    rather than to the install.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).parent


def appimage_path() -> Path | None:
    """The ``.AppImage`` file this process is running from, if it is one.

    ``$APPIMAGE`` is exported by the AppImage runtime and holds the path of the
    image itself — not the temporary mount, which is ``$APPDIR``. Checked for
    existence rather than trusted: the variable survives into any child process,
    so a shell that once ran an AppImage and then started Kroma some other way
    would otherwise hand us a path to something that is not us.
    """
    value = os.environ.get("APPIMAGE", "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def _is_managed(root: Path) -> bool:
    """Whether *root* sits under a prefix some package manager owns."""
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    text = resolved.as_posix()
    return any(
        text == prefix or text.startswith(prefix + "/")
        for prefix in MANAGED_PREFIXES
    )


def _registered_install_locations() -> dict[str, str]:
    """Every installed copy Windows knows about, as ``{scope: folder}``.

    ``HKCU`` is the per-user install, which is the default; ``HKLM`` the
    machine-wide one a user can ask for instead. The machine key is read from
    the 64-bit view, because the installer runs in 64-bit mode and that is where
    it writes. Either can be absent, and on anything but Windows both are.
    """
    try:
        import winreg  # noqa: PLC0415 — Windows only, and this module is imported everywhere
    except ImportError:
        return {}

    hives = (
        (SCOPE_USER, winreg.HKEY_CURRENT_USER, 0),
        (SCOPE_MACHINE, winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
    )
    found: dict[str, str] = {}
    for scope, hive, view in hives:
        try:
            with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                for name in LOCATION_VALUES:
                    try:
                        value, _type = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if isinstance(value, str) and value.strip():
                        found[scope] = value.strip()
                        break
        except OSError:
            continue
    return found


def _same_folder(registered: str, root: Path | str) -> bool:
    """Whether the folder an uninstall entry names is *root*.

    ``samefile`` when both exist, which sees through 8.3 names, junctions and
    case. Otherwise a comparison of the normalised Windows spellings — Windows
    rules on purpose, whatever this module is running on, because these are
    always Windows paths: ``InstallLocation`` ends in a backslash that
    ``Inno Setup: App Path`` does not, and Explorer's capitalisation is not the
    installer's.
    """
    try:
        if os.path.samefile(registered, root):
            return True
    except OSError:
        pass

    def spelling(path: str) -> str:
        return ntpath.normcase(ntpath.normpath(path.strip().strip('"')))

    return spelling(registered) == spelling(str(root))


@cache
def installer_scope() -> str:
    """Which installed copy *this* is: ``"user"``, ``"machine"``, or ``""``.

    ``""`` means no uninstall entry names this folder, and so this copy is not
    an installed one — whatever else is installed on the machine. That is the
    point of comparing folders rather than only asking whether the key exists:
    a portable copy unzipped onto a machine that also has Kroma installed would
    otherwise "update" by running the installer, which updates and relaunches
    *the installed copy* and leaves this one exactly as it was.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return ""
    # install_root(), split by Windows rules — this is the Windows branch, and
    # ntpath says so whatever the tests happen to be running on.
    root = ntpath.dirname(sys.executable)
    for scope, folder in _registered_install_locations().items():
        if _same_folder(folder, root):
            return scope
    return ""


def _has_uninstall_entry() -> bool:
    """Whether Windows has an uninstall entry for **this** copy of the application.

    The registry rather than the install path, and that is the whole point of
    checking it: a portable tree unzipped into ``%LOCALAPPDATA%\\Programs`` sits
    exactly where an installed copy sits, and only the uninstall key tells them
    apart. Getting this wrong in the permissive direction would run an installer
    over a folder Windows knows nothing about. The entry has to name this folder,
    too — see :func:`installer_scope`.
    """
    return installer_scope() != ""


@cache
def install_kind() -> InstallKind:
    """Return how this copy was installed. See the module docstring for order."""
    if not getattr(sys, "frozen", False):
        return InstallKind.SOURCE

    # Before the path check: an AppImage is an AppImage wherever it was put,
    # including somewhere that looks managed.
    if appimage_path() is not None:
        return InstallKind.APPIMAGE

    root = install_root()
    if root is not None and _is_managed(root):
        return InstallKind.MANAGED

    if sys.platform == "win32" and _has_uninstall_entry():
        return InstallKind.WINDOWS_INSTALLER

    return InstallKind.PORTABLE


# Reason keys returned by ``update_eligibility()``. Prose lives in the view
# model — a reason a user reads is a presentation decision, and this module has
# no business holding four sentences.
REASON_DISABLED = "disabled"
REASON_SOURCE = "source"
REASON_MANAGED = "managed"
REASON_PORTABLE = "portable"


@cache
def update_eligibility() -> str:
    """Return ``""`` when this copy can update itself, else a reason key.

    The one condition that is not simply the install kind: the user's vault
    living *inside* the install directory. It should be impossible for the two
    supported kinds — an installer's ``{app}`` folder holds nothing the user
    owns, and an AppImage's install root is a temporary mount — but "should be
    impossible" is not a reason to write over somebody's library if it turns out
    otherwise, and the check is two filesystem calls. It resolves to the
    ``portable`` reason, which is the one that already says "replace this by
    hand", so it costs one condition rather than a fifth key.
    """
    if os.environ.get(ENV_NO_UPDATE, "").strip():
        return REASON_DISABLED

    kind = install_kind()
    if kind is InstallKind.SOURCE:
        return REASON_SOURCE
    if kind is InstallKind.MANAGED:
        return REASON_MANAGED
    if kind is InstallKind.PORTABLE:
        return REASON_PORTABLE

    if vault_is_inside_install():
        logger.warning(
            "Refusing to update in place: the vault is inside %s", install_root()
        )
        return REASON_PORTABLE

    return ""


def vault_is_inside_install() -> bool:
    """Whether the user's vault lives under the install directory.

    Read separately from :func:`update_eligibility` so the view model can say
    *which* portable situation it is looking at, and so the check can be tested
    on its own. Import is local: :mod:`app.utils.library_paths` raises rather
    than guessing when no root has been set, and this module is imported long
    before a vault has been chosen.
    """
    root = install_root()
    if root is None:
        return False
    try:
        from app.utils.library_paths import library_root

        vault = library_root()
    except Exception:  # noqa: BLE001 — no vault yet is not an error here
        return False
    try:
        return vault.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def clear_cache() -> None:
    """Forget the cached answers.

    Only the tests need this — nothing about how the application was installed
    can change while it is running — but they need it badly, since each one sets
    up a different machine.
    """
    install_kind.cache_clear()
    update_eligibility.cache_clear()
    installer_scope.cache_clear()
