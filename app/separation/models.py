"""What vocal-removal models are installed, and putting new ones there.

Models live in ``<vault>/.data/models/``. Kroma neither downloads nor validates
them — the user brings their own — so this module's whole job is to answer
"which files here are usable" honestly.

A usable model is a ``.ckpt`` checkpoint **with a ``.yaml`` config**. The
checkpoint is only weights; the YAML describes the architecture they belong to,
and the two are useless apart. Listing a lone ``.ckpt`` as selectable would just
move the failure to the middle of a long GPU job.

Pairing does *not* require matching names. Model releases in the wild rarely
oblige — ``inst_gaboxFlowersV10.ckpt`` ships next to ``v10.yaml``, and MSST
configs are named after the architecture rather than the weights. So a same-named
config wins if there is one, and otherwise a lone unclaimed config is adopted by
a lone unpaired checkpoint. Only genuine ambiguity — several of each, none
matching — is refused, and then the reason says which file to rename.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from app.utils.library_paths import models_root

logger = logging.getLogger(__name__)

CHECKPOINT_SUFFIX = ".ckpt"
CONFIG_SUFFIX = ".yaml"
# Some model releases spell it .yml; accepted on import and when pairing.
CONFIG_SUFFIXES = (".yaml", ".yml")


def config_for(checkpoint: Path) -> Optional[Path]:
    """The identically-named config beside *checkpoint*, if there is one."""
    for suffix in CONFIG_SUFFIXES:
        candidate = checkpoint.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _configs_in(root: Path) -> list[Path]:
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in CONFIG_SUFFIXES
    )


def _pair(checkpoints: list[Path], configs: list[Path]) -> dict[Path, Path]:
    """Decide which config belongs to which checkpoint.

    Same-named pairs are settled first and take their config out of the running,
    so an exact match can never be stolen by the fallback. What is left over is
    only adopted when there is exactly one candidate on each side — with one
    checkpoint and one config in the folder there is nothing to get wrong, and
    that is overwhelmingly the common case.
    """
    pairing: dict[Path, Path] = {}
    claimed: set[Path] = set()

    for checkpoint in checkpoints:
        config = config_for(checkpoint)
        if config is not None:
            pairing[checkpoint] = config
            claimed.add(config)

    unpaired = [c for c in checkpoints if c not in pairing]
    free = [c for c in configs if c not in claimed]
    if len(unpaired) == 1 and len(free) == 1:
        pairing[unpaired[0]] = free[0]

    return pairing


def available_models() -> list[dict]:
    """Every checkpoint in the models folder, usable or not.

    Unusable ones are returned too, carrying the reason, so the UI can show a
    model the user clearly meant to install as present-but-broken rather than
    silently omitting it and leaving them wondering where it went.
    """
    root = models_root()
    if not root.is_dir():
        return []

    checkpoints = sorted(root.glob(f"*{CHECKPOINT_SUFFIX}"))
    configs = _configs_in(root)
    pairing = _pair(checkpoints, configs)

    models: list[dict] = []
    for checkpoint in checkpoints:
        config = pairing.get(checkpoint)
        models.append({
            "name": checkpoint.name,
            "path": str(checkpoint),
            "config": str(config) if config else "",
            # Shown next to the name when the config was not obvious from the
            # filenames, so it is clear which file is actually being used.
            "configName": config.name if config and config.stem != checkpoint.stem else "",
            "usable": config is not None,
            "reason": "" if config else _why_unusable(checkpoint, configs, pairing),
        })
    return models


def _why_unusable(checkpoint: Path, configs: list[Path], pairing: dict[Path, Path]) -> str:
    """A reason that says what to do, not just what is wrong."""
    spare = [c for c in configs if c not in pairing.values()]
    if not configs:
        return "No .yaml config in the models folder"
    if spare:
        return (
            "Cannot tell which config belongs to it — rename one to "
            f"{checkpoint.stem}{CONFIG_SUFFIX}"
        )
    return f"Its config is missing — add {checkpoint.stem}{CONFIG_SUFFIX}"


def usable_models() -> list[dict]:
    return [m for m in available_models() if m["usable"]]


def resolve(name: str) -> Optional[dict]:
    """Look a model up by file name, or None if it is missing or unusable."""
    if not name:
        return None
    for model in available_models():
        if model["name"] == name and model["usable"]:
            return model
    return None


def import_model(source: Path | str) -> dict:
    """Copy a model file into the vault's models folder.

    Accepts either half of the pair. Given a checkpoint it brings any
    same-named config along, because a user picking "the model" from a file
    dialog means the weights and has no reason to think the YAML matters. Given
    a config on its own it simply installs it — that is the natural second step
    after being told a checkpoint was missing one, and refusing it there would
    be a dead end.

    Returns the resulting state of the models folder as seen by
    :func:`available_models`, so the caller reports what is *now* true rather
    than what this one file was.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")

    suffix = source.suffix.lower()
    root = models_root()
    root.mkdir(parents=True, exist_ok=True)

    if suffix in CONFIG_SUFFIXES:
        shutil.copy2(source, root / source.name)
        logger.info("Imported config %s", source.name)
        # Whichever checkpoint it completes — possibly none yet.
        for model in available_models():
            if model["config"] and Path(model["config"]).name == source.name:
                return model
        return {
            "name": source.name, "path": "", "config": str(root / source.name),
            "configName": "", "usable": False,
            "reason": "Config installed, but there is no checkpoint for it yet",
        }

    if suffix != CHECKPOINT_SUFFIX:
        raise ValueError(f"Not a model checkpoint or config: {source.name}")

    shutil.copy2(source, root / source.name)
    config = config_for(source)
    if config is not None:
        shutil.copy2(config, root / config.name)

    for model in available_models():
        if model["name"] == source.name:
            return model

    # Unreachable in practice — the file was just written.
    raise RuntimeError(f"{source.name} vanished after being copied in")
