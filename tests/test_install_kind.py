"""Which install kind each machine resolves to, and what it may do about it.

Every case here is a machine that cannot be arranged in CI, so each one is
assembled out of ``sys.frozen``, ``sys.executable``, ``$APPIMAGE`` and the
registry probe. That is the whole surface — there is nothing else the detection
reads — so faking those four *is* faking the machine.

The order is the part worth testing rather than any single answer: an AppImage
dropped in ``/opt`` is still an AppImage, and a portable tree unzipped into
``%LOCALAPPDATA%\\Programs`` is not an installed copy however much it looks like
one from the path alone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.update import install_kind as ik  # noqa: E402


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    """Both answers are cached for the life of the process, so every test has to
    start from an empty one — and leave one behind, or the next test inherits a
    machine that never existed."""
    monkeypatch.delenv(ik.ENV_NO_UPDATE, raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    ik.clear_cache()
    yield
    ik.clear_cache()


def _frozen(monkeypatch, executable: str, *, windows: bool = False, registry: bool | None = False):
    """Pretend to be a frozen build whose launcher lives at *executable*.

    *registry* stubs the whole "is there an entry for this copy" answer;
    ``None`` leaves it to the real folder comparison, for the tests that fake
    the registry's contents instead.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "platform", "win32" if windows else "linux")
    if registry is not None:
        monkeypatch.setattr(ik, "_has_uninstall_entry", lambda: registry)
    # Nothing in this file is testing the vault check; it has its own test.
    monkeypatch.setattr(ik, "vault_is_inside_install", lambda: False)


# ---------------------------------------------------------------------------
# The kinds
# ---------------------------------------------------------------------------


def test_an_unfrozen_process_is_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert ik.install_kind() is ik.InstallKind.SOURCE
    assert ik.install_root() is None
    assert ik.update_eligibility() == ik.REASON_SOURCE


def test_an_appimage_is_recognised_from_the_environment(monkeypatch, tmp_path):
    image = tmp_path / "kroma-player-1.2.0-x86_64.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    _frozen(monkeypatch, str(tmp_path / "mount" / "usr" / "bin" / "kroma-player"))

    assert ik.install_kind() is ik.InstallKind.APPIMAGE
    assert ik.appimage_path() == image
    assert ik.update_eligibility() == ""


def test_appimage_wins_over_a_managed_looking_path(monkeypatch, tmp_path):
    """The one place the detection order is load-bearing.

    An AppImage the user dropped in /opt is still an AppImage: it is one file
    they own, and replacing it is exactly as safe as it is anywhere else. Read
    the path first and it would be refused as a distribution package.
    """
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    _frozen(monkeypatch, "/opt/kroma/kroma-player")

    assert ik.install_kind() is ik.InstallKind.APPIMAGE


def test_a_stale_appimage_variable_is_ignored(monkeypatch, tmp_path):
    """$APPIMAGE is inherited by every child process, so a shell that once ran
    an AppImage would otherwise hand us a path to something that is not us."""
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "gone.AppImage"))
    _frozen(monkeypatch, str(tmp_path / "kroma-player"))

    assert ik.appimage_path() is None
    assert ik.install_kind() is ik.InstallKind.PORTABLE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX prefixes resolve under a drive on Windows")
@pytest.mark.parametrize(
    "executable",
    [
        "/nix/store/abc123-kroma-player-1.2.0/bin/kroma-player",
        "/usr/bin/kroma-player",
        "/usr/lib/kroma-player/kroma-player",
        "/opt/kroma-player/kroma-player",
        "/snap/kroma-player/current/bin/kroma-player",
        "/app/bin/kroma-player",
    ],
)
def test_a_package_managers_path_is_managed(monkeypatch, executable):
    _frozen(monkeypatch, executable)

    assert ik.install_kind() is ik.InstallKind.MANAGED
    assert ik.update_eligibility() == ik.REASON_MANAGED


def test_a_path_that_merely_starts_with_the_same_letters_is_not_managed(monkeypatch):
    """`/opt` is a prefix; `/optimism` is a different word.

    A plain ``startswith`` would call the second one managed and permanently
    disable updating for anyone who unzipped the app somewhere unlucky.
    """
    _frozen(monkeypatch, "/optional-software/kroma-player/kroma-player")

    assert ik.install_kind() is ik.InstallKind.PORTABLE


def test_a_registered_windows_install_is_updatable(monkeypatch):
    _frozen(
        monkeypatch,
        r"C:\Users\k\AppData\Local\Programs\Kroma Player\kroma-player.exe",
        windows=True,
        registry=True,
    )

    assert ik.install_kind() is ik.InstallKind.WINDOWS_INSTALLER
    assert ik.update_eligibility() == ""


def test_an_unzipped_tree_in_the_install_location_is_still_portable(monkeypatch):
    """The reason the registry is consulted rather than the path.

    A portable zip extracted into %LOCALAPPDATA%\\Programs sits exactly where an
    installed copy sits. Only the uninstall key tells them apart, and getting it
    wrong in the permissive direction runs an installer over a folder Windows
    knows nothing about.
    """
    _frozen(
        monkeypatch,
        r"C:\Users\k\AppData\Local\Programs\Kroma Player\kroma-player.exe",
        windows=True,
        registry=False,
    )

    assert ik.install_kind() is ik.InstallKind.PORTABLE
    assert ik.update_eligibility() == ik.REASON_PORTABLE


# ---------------------------------------------------------------------------
# Which installed copy this is
# ---------------------------------------------------------------------------

INSTALLED = r"C:\Users\k\AppData\Local\Programs\Kroma Player"


def _registry(monkeypatch, locations: dict[str, str]):
    """Fake what the uninstall keys say, and nothing else about the registry."""
    monkeypatch.setattr(ik, "_registered_install_locations", lambda: dict(locations))


def test_the_entry_for_this_folder_makes_it_an_installed_copy(monkeypatch):
    _frozen(monkeypatch, INSTALLED + r"\kroma-player.exe", windows=True, registry=None)
    _registry(monkeypatch, {"user": INSTALLED})

    assert ik.installer_scope() == "user"
    assert ik.install_kind() is ik.InstallKind.WINDOWS_INSTALLER
    assert ik.update_eligibility() == ""


def test_an_entry_for_another_folder_leaves_this_copy_portable(monkeypatch):
    """A portable copy on a machine that also has Kroma installed. Running the
    installer from here would update and relaunch *the installed copy*, and
    leave this one exactly as it was."""
    _frozen(monkeypatch, r"D:\Apps\kroma-player\kroma-player.exe", windows=True, registry=None)
    _registry(monkeypatch, {"user": INSTALLED})

    assert ik.installer_scope() == ""
    assert ik.install_kind() is ik.InstallKind.PORTABLE
    assert ik.update_eligibility() == ik.REASON_PORTABLE


@pytest.mark.parametrize("registered", [
    INSTALLED + "\\",                      # InstallLocation's trailing backslash
    INSTALLED.upper(),                      # Explorer's case is not the installer's
    INSTALLED.replace("\\", "/"),           # a forward-slash spelling
    '"' + INSTALLED + '"',                  # quoted, as some tools write it
])
def test_the_same_folder_spelled_differently_still_matches(monkeypatch, registered):
    _frozen(monkeypatch, INSTALLED + r"\kroma-player.exe", windows=True, registry=None)
    _registry(monkeypatch, {"user": registered})

    assert ik.installer_scope() == "user"


def test_a_machine_wide_install_reports_its_scope(monkeypatch):
    machine = r"C:\Program Files\Kroma Player"
    _frozen(monkeypatch, machine + r"\kroma-player.exe", windows=True, registry=None)
    _registry(monkeypatch, {"user": INSTALLED, "machine": machine})

    assert ik.installer_scope() == "machine"
    assert ik.install_kind() is ik.InstallKind.WINDOWS_INSTALLER


def test_no_entries_at_all_is_no_scope(monkeypatch):
    _frozen(monkeypatch, INSTALLED + r"\kroma-player.exe", windows=True, registry=None)
    _registry(monkeypatch, {})

    assert ik.installer_scope() == ""
    assert ik.install_kind() is ik.InstallKind.PORTABLE


def test_scope_is_only_asked_on_windows(monkeypatch):
    _frozen(monkeypatch, "/home/k/kroma-player/kroma-player", windows=False, registry=None)
    monkeypatch.setattr(
        ik, "_registered_install_locations", lambda: pytest.fail("read the registry off Windows")
    )

    assert ik.installer_scope() == ""


def test_scope_is_cached_and_cleared_with_the_rest(monkeypatch):
    """The registry is read once per process — and the tests, which each set up
    a different machine, need clear_cache() to forget it."""
    calls: list[int] = []
    _frozen(monkeypatch, INSTALLED + r"\kroma-player.exe", windows=True, registry=None)

    def locations():
        calls.append(1)
        return {"user": INSTALLED}

    monkeypatch.setattr(ik, "_registered_install_locations", locations)

    assert ik.installer_scope() == "user"
    assert ik.installer_scope() == "user"
    assert len(calls) == 1

    ik.clear_cache()
    _registry(monkeypatch, {})
    assert ik.installer_scope() == ""


def test_the_real_folder_comparison_sees_through_spelling(tmp_path):
    """samefile, for folders that exist: the case the CI install test meets."""
    folder = tmp_path / "Kroma Player"
    folder.mkdir()

    assert ik._same_folder(str(folder), folder)
    assert ik._same_folder(str(folder) + os.sep, folder)
    assert not ik._same_folder(str(tmp_path / "elsewhere"), folder)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_the_environment_switch_beats_everything(monkeypatch, tmp_path):
    """One line for a packager to put in a derivation.

    Mirrors KROMA_NO_GUI_BUILD: a distribution that ships Kroma should be able
    to switch the feature off without patching Python.
    """
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setenv(ik.ENV_NO_UPDATE, "1")
    _frozen(monkeypatch, str(tmp_path / "kroma-player"))

    assert ik.install_kind() is ik.InstallKind.APPIMAGE, "detection still answers honestly"
    assert ik.update_eligibility() == ik.REASON_DISABLED


def test_an_empty_switch_is_not_set(monkeypatch, tmp_path):
    """`KROMA_NO_UPDATE=` in a shell profile is an unset variable, not a request."""
    image = tmp_path / "kroma-player.AppImage"
    image.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setenv(ik.ENV_NO_UPDATE, "   ")
    _frozen(monkeypatch, str(tmp_path / "kroma-player"))

    assert ik.update_eligibility() == ""


def test_a_vault_inside_the_install_is_never_updated_in_place(monkeypatch, tmp_path):
    """Replacing the install would take the library with it.

    It should be impossible for the two supported kinds, and "should be
    impossible" is not a reason to write over somebody's music.
    """
    root = tmp_path / "Kroma Player"
    (root / "vault").mkdir(parents=True)
    _frozen(monkeypatch, str(root / "kroma-player.exe"), windows=True, registry=True)
    monkeypatch.setattr(ik, "vault_is_inside_install", lambda: True)

    assert ik.install_kind() is ik.InstallKind.WINDOWS_INSTALLER
    assert ik.update_eligibility() == ik.REASON_PORTABLE


def test_the_vault_check_reads_the_real_library_root(monkeypatch, tmp_path):
    """The check itself, rather than the stub the tests above install."""
    from app.utils import library_paths

    root = tmp_path / "Kroma Player"
    inside = root / "venomvault"
    inside.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "kroma-player"))
    monkeypatch.setattr(library_paths, "library_root", lambda: inside)
    assert ik.vault_is_inside_install()

    monkeypatch.setattr(library_paths, "library_root", lambda: tmp_path / "elsewhere")
    assert not ik.vault_is_inside_install()


def test_the_uninstall_key_matches_the_installer_script():
    """The registry key is derived from AppId in packaging/kroma-player.iss, and
    Inno appends `_is1`. If the two drift, every installed copy on Windows
    silently reports itself as portable and the button disappears."""
    script = (REPO_ROOT / "packaging" / "kroma-player.iss").read_text(encoding="utf-8")
    guid = ik.UNINSTALL_KEY.rsplit("\\", 1)[-1].removesuffix("_is1")

    assert f"AppId={{{guid}" in script, (
        f"packaging/kroma-player.iss does not declare AppId {guid}"
    )
