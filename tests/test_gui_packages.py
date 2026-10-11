"""Load each shipped GUI bundle against the real backend.

QML binding errors are runtime-only — a renamed property fails silently at
load, not at import. So the only way to know a GUI still works against the API
is to build the real registry and load the package's entry file, which is what
this does, offscreen.

It loads the compiled ``.kpi`` rather than the source folder, because that is
what a user runs. Compiling first is part of the test: a GUI that cannot be
built is as broken as one that cannot be loaded, and the ``qrc:`` URLs a bundle
resolves through are not the ``file:`` URLs a folder resolved through — a
missing import path or a ``qmldir`` that does not survive compilation shows up
here and nowhere else.

It will not catch a visual regression. It does catch every "GUI referenced
something the backend no longer has", which is the class of break a backend
refactor actually causes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent  # noqa: E402

from conftest import make_track

from app.gui.bundle import mount, unmount  # noqa: E402
from app.gui.compiler import CompilerError, build_bundle  # noqa: E402
from app.gui.gui_paths import (  # noqa: E402
    BUNDLE_SUFFIX,
    SIDECAR_SUFFIX,
    bundled_gui_root,
    bundled_gui_source,
)
from app.gui.resolver import ResolvedGui, inspect_bundle  # noqa: E402
from app.main import CONTEXT_PROPERTIES, _register_qml_types  # noqa: E402
from app.models.track import Artist, Track  # noqa: E402

GUI_NAMES = ["nocturne"]


@pytest.fixture(scope="module")
def backend(qt_app, tmp_path_factory):
    """The real registry over a throwaway vault with a few tracks in it."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("guivault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")
    artist = Artist(name="Someone")
    session.add(artist)
    session.flush()
    for i in range(30):
        session.add(
            make_track(
                session,
                title=f"Track {i}",
                album="An Album",
                duration_seconds=200,
                bpm=100,
                artists=[artist],
            )
        )
    session.commit()

    _register_qml_types(registry)
    return registry


def _bundle_for(gui_name: str) -> Path:
    """Compile the GUI, or fall back to a bundle that is already there.

    Skips rather than fails when there is neither, because a checkout without
    Qt's rcc on PATH can still run every other test in the suite — and the thing
    that would be under test here is the GUI, not the toolchain.
    """
    root = bundled_gui_root()
    bundle = root / f"{gui_name}{BUNDLE_SUFFIX}"
    try:
        build_bundle(
            bundled_gui_source(gui_name),
            bundle,
            sidecar=root / f"{gui_name}{SIDECAR_SUFFIX}",
        )
    except CompilerError as exc:
        if not bundle.is_file():
            pytest.skip(f"cannot compile {gui_name} and no bundle is present: {exc}")
    return bundle


@pytest.mark.parametrize("gui_name", GUI_NAMES)
@pytest.mark.parametrize("storage", ["source", "bundle"])
def test_every_qml_file_resolves_dependencies(qt_app, backend, gui_name, storage):
    """Compile even files behind inactive loaders, in both supported layouts."""
    source = bundled_gui_source(gui_name)
    engine = QQmlApplicationEngine()
    bundle = resolved = None
    try:
        if storage == "bundle":
            bundle = _bundle_for(gui_name)
            candidate = inspect_bundle(bundle)
            assert candidate.loadable, candidate.error
            resolved = ResolvedGui(candidate.manifest, bundle, candidate.build_id, gui_name)
            mount(bundle, resolved.mount_root)
            engine.addImportPath(resolved.import_path)
        else:
            engine.addImportPath(str(source))

        # Other GUI tests can write temporary __*Harness.qml files here while
        # pytest-xdist runs. They are not part of the shipped component tree.
        paths = sorted(path for path in source.rglob("*.qml")
                       if not path.name.startswith("__"))
        for path in paths:
            relative = path.relative_to(source).as_posix()
            url = (QUrl(f"qrc:{resolved.mount_root}/{relative}") if resolved
                   else QUrl.fromLocalFile(str(path)))
            component = QQmlComponent(engine, url)
            assert component.status() == QQmlComponent.Status.Ready, (
                f"{storage}/{relative}: {component.errorString()}"
            )
            del component
    finally:
        del engine
        if resolved is not None:
            unmount(bundle, resolved.mount_root)


@pytest.mark.parametrize("gui_name", GUI_NAMES)
def test_gui_package_loads_against_the_api(qt_app, backend, gui_name):
    bundle = _bundle_for(gui_name)

    candidate = inspect_bundle(bundle)
    assert candidate.manifest is not None, f"{gui_name}: {candidate.error}"
    assert candidate.loadable, f"{gui_name}: {candidate.error}"

    resolved = ResolvedGui(candidate.manifest, bundle, candidate.build_id, gui_name)

    engine = QQmlApplicationEngine()

    # The engine takes ownership of an image provider and destroys it with
    # itself, so each engine gets its own rather than sharing the registry's.
    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider

    engine.addImageProvider("cover", CoverImageProvider(backend.get_service("cover_store")))
    engine.addImageProvider("localart", LocalArtImageProvider())

    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    mount(bundle, resolved.mount_root)
    try:
        engine.addImportPath(resolved.import_path)
        engine.load(resolved.entry_url)

        assert engine.rootObjects(), (
            f"{gui_name} failed to load from {resolved.entry_url.toString()}:\n"
            + "\n".join(w.toString() for w in warnings)
        )

        # Let bindings settle and queries resolve, then check nothing complained.
        qt_app.processEvents()

        fatal = [
            w.toString()
            for w in warnings
            if "is not a function" in w.toString()
            or "undefined" in w.toString()
            or "Unable to assign" in w.toString()
            or "Cannot read property" in w.toString()
            or "is not available" in w.toString()
        ]
        assert not fatal, f"{gui_name} binding errors:\n" + "\n".join(fatal)
    finally:
        del engine
        unmount(bundle, resolved.mount_root)
