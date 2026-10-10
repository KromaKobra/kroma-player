"""Giving a vault a name of its own.

Every audio file in a vault is named by its track id and nothing else, so
``mix/0001-0100/0042.opus`` exists in every vault that has ever held 66 tracks.
Two unrelated libraries collide from top to bottom. Without something that says
*which* library a folder came from, a sync cannot tell "your backup, 50 tracks
behind" from "someone else's library, about to be overwritten with yours".

So a vault carries an id:

    <vault>/.data/vault.json   {"version": 1, "vault_id": "...", "created_at": ...,
                                "vault_version": 1, "last_opened_by": "1.0.1"}

A file rather than a column in ``tracks.db``. When this was written
:mod:`app.database.schema` was ``create_all()`` and nothing else, so a new column
could not reach a database that already existed; there is a migration step now,
so the constraint has lifted, but the file is still the better home. The id has
to be readable *before* deciding whether a folder is a library worth opening —
which is exactly the moment its database has not been opened yet.
:mod:`app.services.backing_track_service` keeps availability on the filesystem
for a related reason.

The id is copied into the backup along with everything else, so a backup
carries its source's lineage. Two copies of one vault legitimately share an id;
that is precisely what makes them the same library rather than two libraries
that happen to look alike.

The file carries two version numbers and they are not the same thing.
``version`` is the format of *this document*; ``vault_version`` is the layout of
*the whole vault around it*, which is what :mod:`app.vault.vault_version`
checks before the vault is opened at all. This module writes both and
interprets neither — the stamp belongs to the module that owns the migrations.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.utils.library_paths import data_root
from app.versions import APP_VERSION, VAULT_VERSION

logger = logging.getLogger(__name__)

IDENTITY_FILENAME = "vault.json"
IDENTITY_VERSION = 1


def identity_path(vault: Path | None = None) -> Path:
    """Where the identity file lives for *vault*, or for the current library."""
    root = (vault / ".data") if vault is not None else data_root()
    return root / IDENTITY_FILENAME


def utc_now() -> str:
    """An ISO-8601 timestamp in UTC, used for every date this package records."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_document(vault: Path | None = None) -> dict:
    """Return ``vault.json``'s contents verbatim, or ``{}``.

    Uninterpreted, unlike ``read_identity`` below: a vault that has been stamped
    with a layout version but has not been given an id yet is a real state — a
    vault created moments ago, mid-startup — and the version reader has to be
    able to see it. Never raises.
    """
    path = identity_path(vault)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read vault identity at %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def read_identity(vault: Path | None = None) -> dict:
    """Return the identity document, or ``{}`` if there is not a usable one.

    Never raises. A vault with no id, an unreadable id or a corrupt one all mean
    the same thing to every caller — "this folder cannot vouch for itself" —
    and that is a case the scan already has to handle properly.
    """
    data = read_document(vault)
    return data if isinstance(data.get("vault_id"), str) else {}


def update_document(vault: Path | None, **fields) -> bool:
    """Merge *fields* into ``vault.json``, writing only if something changed.

    The one writer for everything that lives in this file after creation, so a
    caller adding a key cannot drop the ones it did not know about — which is
    the failure mode that matters here, because the layout stamp and the vault
    id are written at different moments by different modules.

    Returns True when the file was written. Never raises: a vault that cannot be
    written to is a real situation (a backup mounted read-only) and no caller
    here should fail to start over it.
    """
    document = read_document(vault)
    if all(document.get(key) == value for key, value in fields.items()):
        return False
    document.update(fields)
    path = identity_path(vault)
    try:
        write_json_atomic(path, document)
    except OSError as exc:
        logger.warning("Could not update %s: %s", path, exc)
        return False
    return True


def vault_id(vault: Path | None = None) -> str:
    """The vault's id, or ``""`` when it does not have one."""
    return read_identity(vault).get("vault_id", "")


def ensure_vault_id(vault: Path | None = None) -> str:
    """Return the vault's id, creating one if it has none.

    Called once at startup. Writing the file eagerly rather than on first backup
    matters: a vault that gets its id now passes it on to every copy made later,
    whereas two copies that were both made before the id existed would each
    invent a different one and then refuse to sync with each other.

    A vault that cannot be written to still returns an id — an ephemeral one, so
    the caller keeps working — but it is not persisted and a warning is logged.

    ``last_opened_by`` is refreshed here too. It is the answer to "what wrote
    this?" when a vault turns up on another machine, and it costs a write only
    when the application version has actually moved.
    """
    existing = read_document(vault)
    if isinstance(existing.get("vault_id"), str) and existing["vault_id"]:
        update_document(vault, last_opened_by=APP_VERSION)
        return existing["vault_id"]

    # Merged onto whatever is already there rather than replacing it: a vault
    # created a moment ago has already been stamped with its layout version by
    # app.vault.vault_version, and that stamp must survive being given an id.
    document = {
        **existing,
        "version": IDENTITY_VERSION,
        "vault_id": uuid.uuid4().hex,
        "created_at": existing.get("created_at") or utc_now(),
        "vault_version": existing.get("vault_version", VAULT_VERSION),
        "last_opened_by": APP_VERSION,
    }
    path = identity_path(vault)
    try:
        write_json_atomic(path, document)
        logger.info("Assigned vault id %s to %s", document["vault_id"], path.parent.parent)
    except OSError as exc:
        # A read-only vault is a real situation (a backup mounted read-only, a
        # permissions mishap). The app has no business failing to start over it.
        logger.warning("Could not record a vault id at %s: %s", path, exc)
    return document["vault_id"]


def write_json_atomic(path: Path, document: dict) -> None:
    """Write *document* to *path* so a reader never sees a half-written file.

    ``session.json`` and the kord config both write in place and swallow the
    error, which is fine for state that can be rebuilt by using the app. It is
    not fine here: a truncated ``backup.json`` is a backup that has lost its
    index, and a truncated ``vault.json`` is a vault that has lost its name.
    ``settings.json`` comes through here for the same reason — it carries the
    download consent record and the backup destination, neither of which the
    app rebuilds by being used.

    The temporary file is made in the destination folder so the ``os.replace``
    is a same-filesystem rename, which is what makes it atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2)

    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".part", delete=False,
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
