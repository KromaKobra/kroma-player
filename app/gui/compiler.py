"""Compiling a GUI source tree into a ``.kpi`` bundle.

One call to ``rcc --binary`` over the GUI's files plus a generated ``build.json``
describing what was compiled. On the official GUI that is 81 files and 812 KB in,
418 KB and about 50 ms out — cheap enough that a checkout rebuilds automatically
when the QML changes, which is what makes editing a GUI still feel like editing
loose files.

The interesting part is the build id::

    build_id = sha256( KPI_FORMAT_VERSION
                     || for each source file, sorted: relpath \\0 bytes \\0
                     || qt_major.minor || rcc_options )[:16]

Paths are folded in as well as contents, so a rename registers as a change — a
digest over contents alone would call a rearranged tree identical to the old
one. The Qt major.minor is in there because both the resource format and the
``.qmlc`` bytecode cache are tied to the Qt the bundle was built against, so a
Qt upgrade has to produce a new bundle rather than reuse a stale cache. The rcc
options are in there because they change the bytes on disk.

That id is the whole staleness check. Nothing downstream ever compares trees.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

from PySide6.QtCore import qVersion

from app.gui.gui_paths import BUILD_RECORD_FILENAME, MANIFEST_FILENAME
from app.gui.manifest import GuiManifestError, load_manifest
from app.versions import APP_VERSION, KPI_FORMAT_VERSION

logger = logging.getLogger(__name__)

# Recorded in the build record and folded into the build id, because both
# change the bytes rcc writes. zlib rather than rcc's default zstd: a bundle is
# a public format that any build of the app has to be able to read, and zlib is
# the one compressor Qt is never built without.
RCC_COMPRESS_ALGO = "zlib"
RCC_COMPRESS_LEVEL = "9"
RCC_OPTIONS = f"{RCC_COMPRESS_ALGO}:{RCC_COMPRESS_LEVEL}"

# Where to find rcc, in order. It is not a Python package, so nothing installs
# it as an entry point and nothing can be imported to locate it.
ENV_RCC = "KROMA_RCC"

# Set this to skip the automatic rebuild in a checkout — for timing a cold
# start, or when working on the backend against a bundle you built on purpose.
ENV_NO_BUILD = "KROMA_NO_GUI_BUILD"


class CompilerError(Exception):
    """Raised when a source tree cannot be compiled into a bundle."""


@dataclass(frozen=True)
class Fingerprint:
    """What a source tree hashes to, and the files that went into it."""

    build_id: str
    source_digest: str
    files: tuple[str, ...]


# ---------------------------------------------------------------------------
# Hashing a source tree
# ---------------------------------------------------------------------------


def relative_files(directory: Path) -> list[str]:
    """Every file under *directory*, as sorted forward-slash relative paths."""
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )


def digest_of(directory: Path, relatives: list[str]) -> str:
    """Digest *relatives* as they currently stand under *directory*.

    Paths are folded in as well as contents so a rename registers as a change,
    and forward slashes keep the result stable across platforms. A path that has
    gone missing is recorded as such rather than skipped — a deleted file is a
    change, and skipping it would make it look like the file never existed.
    """
    digest = hashlib.sha256()
    for relative in relatives:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        path = directory / relative
        digest.update(path.read_bytes() if path.is_file() else b"\0missing")
        digest.update(b"\0")
    return digest.hexdigest()


def qt_version_key() -> str:
    """Return the Qt version to the minor, e.g. ``"6.11"``.

    Patch releases do not change the resource format or invalidate the QML
    bytecode cache, so folding the full version in would rebuild every bundle
    for a change that means nothing to it.
    """
    parts = qVersion().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else qVersion()


def source_fingerprint(directory: Path) -> Fingerprint:
    """Fingerprint the GUI source tree at *directory*."""
    files = relative_files(directory)
    source_digest = digest_of(directory, files)
    build_id = hashlib.sha256(
        "\0".join(
            (str(KPI_FORMAT_VERSION), source_digest, qt_version_key(), RCC_OPTIONS)
        ).encode("utf-8")
    ).hexdigest()[:16]
    return Fingerprint(build_id=build_id, source_digest=source_digest, files=tuple(files))


# ---------------------------------------------------------------------------
# Finding rcc
# ---------------------------------------------------------------------------


def find_rcc() -> Path:
    """Locate the Qt resource compiler, or raise ``CompilerError``.

    In order: ``$KROMA_RCC``, then either name on ``PATH``, then the pip wheel's
    private copy, then whatever ``qtpaths`` says. The last two matter because
    ``rcc`` is frequently not on ``PATH`` at all: the PySide6 wheel keeps it
    inside the package, and a distribution build keeps it in Qt's libexec.
    """
    override = os.environ.get(ENV_RCC, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise CompilerError(f"${ENV_RCC} is set to {override}, which is not an executable")

    for name in ("pyside6-rcc", "rcc"):
        found = shutil.which(name)
        if found:
            return Path(found)

    # The pip wheel layout: PySide6/Qt/libexec/rcc.
    try:
        import PySide6

        wheel = Path(PySide6.__file__).parent / "Qt" / "libexec" / "rcc"
        if wheel.is_file():
            return wheel
    except ImportError:  # pragma: no cover - PySide6 is a hard dependency
        pass

    for tool in ("qtpaths6", "qtpaths"):
        if not shutil.which(tool):
            continue
        try:
            output = subprocess.run(
                [tool, "--query", "QT_INSTALL_LIBEXECS"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if output:
            candidate = Path(output) / "rcc"
            if candidate.is_file():
                return candidate

    raise CompilerError(
        "Could not find Qt's resource compiler. Install it, put it on PATH, or "
        f"point ${ENV_RCC} at it."
    )


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_record(source: Path, fingerprint: Fingerprint) -> dict:
    """Describe what compiling *source* would produce.

    Reads the manifest for the fields a consumer wants without unpacking the
    bundle — name, version, API contract, entry point — so the sidecar beside a
    shipped bundle answers every startup question on its own.
    """
    try:
        manifest = load_manifest(source)
    except GuiManifestError as exc:
        raise CompilerError(str(exc)) from exc

    from app.backup.identity import utc_now

    return {
        "format": KPI_FORMAT_VERSION,
        "name": manifest.name,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "entry": manifest.entry,
        "build_id": fingerprint.build_id,
        "source_digest": fingerprint.source_digest,
        "built_at": utc_now(),
        "built_by": APP_VERSION,
        "qt_version": qt_version_key(),
        "rcc_options": RCC_OPTIONS,
    }


def _write_qrc(qrc: Path, source: Path, files: tuple[str, ...], record: Path) -> None:
    """Write the resource description rcc will read.

    Every path is absolute. rcc resolves ``<file>`` against the location of the
    ``.qrc`` itself, and the ``.qrc`` is generated into a temporary folder
    nowhere near the source tree, so a relative path here does not merely
    mislead — it fails outright.
    """
    lines = ['<!DOCTYPE RCC><RCC version="1.0">', '<qresource prefix="/">']
    for relative in files:
        absolute = (source / relative).resolve()
        lines.append(f"  <file alias={quoteattr(relative)}>{absolute}</file>")
    lines.append(
        f"  <file alias={quoteattr(BUILD_RECORD_FILENAME)}>{record.resolve()}</file>"
    )
    lines.append("</qresource>")
    lines.append("</RCC>")
    qrc.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_bundle(
    source: Path,
    output: Path,
    *,
    sidecar: Path | None = None,
    rcc: Path | None = None,
) -> dict:
    """Compile the GUI at *source* into the bundle at *output*.

    Returns the build record that was written into it. When *sidecar* is given
    the same record is written there as plain JSON, which is what lets the
    installer decide whether the shipped bundle is newer than the installed one
    without opening either.

    The bundle is written to a temporary file beside *output* and moved into
    place, so an interrupted or failed build cannot leave a half-written ``.kpi``
    where a working one used to be.
    """
    if not source.is_dir():
        raise CompilerError(f"{source}: no such GUI source folder")
    if not (source / MANIFEST_FILENAME).is_file():
        raise CompilerError(f"{source}: no {MANIFEST_FILENAME} — this is not a GUI package")
    if (source / BUILD_RECORD_FILENAME).is_file():
        raise CompilerError(
            f"{source}: {BUILD_RECORD_FILENAME} is written by the compiler and "
            "cannot be shipped in a source tree — remove it"
        )

    compiler = rcc if rcc is not None else find_rcc()
    fingerprint = source_fingerprint(source)
    record = build_record(source, fingerprint)

    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kpi-build-") as scratch:
        workdir = Path(scratch)
        record_path = workdir / BUILD_RECORD_FILENAME
        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        qrc = workdir / "bundle.qrc"
        _write_qrc(qrc, source, fingerprint.files, record_path)

        staged = workdir / "bundle.kpi"
        command = [
            str(compiler), "--binary",
            "--compress-algo", RCC_COMPRESS_ALGO,
            "--compress", RCC_COMPRESS_LEVEL,
            "-o", str(staged), str(qrc),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CompilerError(f"Could not run {compiler}: {exc}") from exc

        if result.returncode != 0 or not staged.is_file():
            detail = (result.stderr or result.stdout or "").strip()
            raise CompilerError(f"rcc failed for {source}: {detail or result.returncode}")

        # Same filesystem as the destination, so the move is an atomic rename.
        temporary = output.with_name(output.name + ".tmp")
        shutil.copyfile(staged, temporary)
        os.replace(temporary, output)

    if sidecar is not None:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")

    logger.info(
        "Compiled %s → %s (%s files, %.0f KB, build %s)",
        source, output, len(fingerprint.files),
        output.stat().st_size / 1024, fingerprint.build_id,
    )
    return record


def read_sidecar(path: Path) -> dict:
    """Read a ``.kpi.json`` build record, or return ``{}``.

    Never raises: an absent or unreadable sidecar means "cannot say what the
    shipped bundle is", and every caller answers that by inspecting the bundle
    itself.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_source(argument: str) -> Path:
    """Interpret a command-line argument as a GUI source folder.

    A bare name is one of this repository's own GUIs; anything that looks like a
    path is taken as one, so an author can build a package that lives outside
    the checkout without having to move it in.
    """
    from app.gui.gui_paths import bundled_gui_root

    candidate = Path(argument).expanduser()
    if os.sep in argument or (os.altsep and os.altsep in argument):
        return candidate
    if candidate.is_dir() and not (bundled_gui_root() / argument).is_dir():
        return candidate
    return bundled_gui_root() / argument


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point — see ``scripts/build-gui``."""
    from app.gui.gui_paths import BUNDLE_SUFFIX, OFFICIAL_GUI_NAME, SIDECAR_SUFFIX

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    for argument in list(argv or []) or [OFFICIAL_GUI_NAME]:
        source = resolve_source(argument)
        # Beside the source folder, which for this repository's own GUIs is
        # gui/<name>.kpi — exactly where the application and the spec file look.
        name = source.name
        try:
            build_bundle(
                source,
                source.parent / f"{name}{BUNDLE_SUFFIX}",
                sidecar=source.parent / f"{name}{SIDECAR_SUFFIX}",
            )
        except CompilerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0
