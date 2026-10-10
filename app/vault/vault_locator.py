"""What a vault is, how to recognise one, and how to make a new one.

Pure filesystem logic with no Qt dependency — the setup dialog in
``vault_setup`` is a thin shell around these functions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config.kord_config import get_vault_path, set_vault_path

logger = logging.getLogger(__name__)

# The conventional folder name. Used when creating a vault, and when looking one
# level down from a folder the user picked.
VAULT_DIR_NAME = "venomvault"

# A vault is identified by its metadata folder. Audio folders (mix/, ...) are
# created lazily on the first download, so they cannot be the marker.
DATA_DIR_NAME = ".data"


def default_vault_path() -> Path:
    """Return the conventional vault location: ``~/Music/venomvault``."""
    return music_folder() / VAULT_DIR_NAME


def music_folder() -> Path:
    """Return the user's Music folder, falling back to the home folder."""
    music = Path.home() / "Music"
    return music if music.is_dir() else Path.home()


def is_vault(path: Path) -> bool:
    """True when *path* is a directory that holds a vault's metadata folder."""
    try:
        return path.is_dir() and (path / DATA_DIR_NAME).is_dir()
    except OSError:
        return False


def resolve_vault_folder(path: Path) -> Path | None:
    """Interpret a user-picked folder as a vault, or return ``None``.

    Accepts either the vault itself or a folder containing one, so picking
    ``~/Music`` when the vault is ``~/Music/venomvault`` does the right thing
    rather than failing on a technicality.
    """
    if is_vault(path):
        return path
    child = path / VAULT_DIR_NAME
    if is_vault(child):
        return child
    return None


def create_vault(location: Path) -> Path:
    """Create a vault at or inside *location* and return its root.

    *location* is the folder the user chose to put the vault in, so the vault
    is created as ``location/venomvault``. If they instead picked a folder that
    already is a vault — or one already named ``venomvault`` — that folder is
    used directly rather than nesting a second one inside it.

    Raises ``OSError`` if the folders cannot be created.
    """
    root = location if (location.name == VAULT_DIR_NAME or is_vault(location)) else location / VAULT_DIR_NAME
    (root / DATA_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return root


def configured_vault() -> Path | None:
    """Return the vault recorded in the kord config, if it still exists."""
    recorded = get_vault_path()
    if recorded is None:
        return None
    if is_vault(recorded):
        return recorded
    logger.warning("Configured vault %s is missing or no longer a vault", recorded)
    return None


def remember_vault(path: Path) -> None:
    """Record *path* in the kord config as the vault to use from now on."""
    if set_vault_path(path):
        logger.info("Vault location saved: %s", path)
