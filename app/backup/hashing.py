"""Content hashing for the diff, and for the copy that follows it.

BLAKE2b at 16 bytes, which is what :func:`app.artwork.image_ops.content_hash`
already uses to content-address cover art. Same reasoning as there — fast, and
collision-safe at this scale — with one addition: that function takes ``bytes``,
and a 6 MB Opus file has no business being read into memory to be compared. So
these stream.

The important one is :func:`copy_and_hash`. A backup copies a file and then
wants to record its digest, and doing that as two operations reads every byte
twice — which on a USB 2 drive is the difference between a five minute sync and
a ten minute one. Hashing the bytes as they pass through costs nothing.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

# 16 bytes of BLAKE2b, matching app.artwork.image_ops.content_hash.
DIGEST_SIZE = 16

# Large enough that the per-read overhead disappears against the copy itself,
# small enough not to matter on any machine that can run Qt.
CHUNK_BYTES = 1024 * 1024


def hash_file(path: Path) -> str:
    """Return the hex digest of a file's contents."""
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_and_hash(source: Path, destination: Path) -> tuple[int, str]:
    """Copy *source* to *destination*, returning ``(bytes_written, digest)``.

    Hashes in the same pass as the copy, so the source is read once.

    Metadata is copied afterwards the way :func:`shutil.copy2` would, because
    the modification time is not decoration here — it is the cheap signal the
    next scan uses to decide it can skip this file without hashing anything.
    """
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    written = 0

    with open(source, "rb") as reader, open(destination, "wb") as writer:
        for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
            digest.update(chunk)
            writer.write(chunk)
            written += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())

    shutil.copystat(source, destination)
    return written, digest.hexdigest()
