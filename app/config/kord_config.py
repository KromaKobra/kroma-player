"""The kord application config — the one file that lives outside the vault.

Everything else Kroma Player writes lives *inside* the vault folder, which makes
the library a portable, self-contained directory. That leaves one thing with
nowhere to go: where the vault actually is. That answer is kept here, in the
per-user application data folder:

    Windows   %APPDATA%\\kord\\kord-config.json      (AppData/Roaming)
    macOS     ~/Library/Application Support/kord/kord-config.json
    Linux     ~/.config/kord/kord-config.json

The file is a plain JSON object. Unknown keys are preserved on write, so a newer
build adding settings here will not have them wiped by an older one.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = "kord"
CONFIG_FILENAME = "kord-config.json"
CONFIG_VERSION = 1

# Key under which the library location is stored.
VAULT_PATH_KEY = "vault_path"


def app_data_root() -> Path:
    """Return the per-user application data folder for this platform."""
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming:
            return Path(roaming)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def kord_dir() -> Path:
    """Return the ``kord`` folder inside the application data folder.

    Uses ``QGuiApplication.applicationName()`` when a Qt application instance
    exists, so it always agrees with what was passed to Qt. Falls back to the
    literal name for contexts with no QGuiApplication (tests, tooling).
    """
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        name = app.applicationName() if app is not None else ""
    except ImportError:
        name = ""
    return app_data_root() / (name or CONFIG_DIR_NAME)


def kord_config_path() -> Path:
    """Return the full path to ``kord-config.json``."""
    return kord_dir() / CONFIG_FILENAME


def load_config() -> dict:
    """Return the config dict, or ``{}`` if it is missing or unreadable.

    A corrupt config must never stop the app from starting: the worst case is
    that the user is asked for their vault again.
    """
    try:
        data = json.loads(kord_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", kord_config_path(), exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_config(values: dict) -> bool:
    """Merge *values* into the config file. Returns True on success."""
    data = load_config()
    data.update(values)
    data["version"] = CONFIG_VERSION
    path = kord_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)
        return False
    return True


def get_vault_path() -> Path | None:
    """Return the recorded vault location, or ``None`` if none is recorded.

    The path is *not* validated here — it may point at a folder that has since
    been moved or deleted. Callers check before using it.
    """
    raw = load_config().get(VAULT_PATH_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def set_vault_path(path: Path) -> bool:
    """Record *path* as the vault location. Returns True on success."""
    return save_config({VAULT_PATH_KEY: str(Path(path))})
