"""The import control: one button, one dropdown, one catalog behind both.

Import sources are available through one ``ImportButton`` on the Import page,
whose sources come from ``ImportSources.js`` —
the built-in ones, plus whatever the enabled plugins contribute, plus CSV once
something can fetch what a CSV lists.

That catalog is the claim these tests are really about. "Adding a source is an
entry in a list" is only true if the menu is genuinely built from the list, so
the last test appends one and watches the menu grow. Everything else here guards
the two ways this particular shape fails quietly:

* **A Repeater inside a Menu builds nothing.** It parents its items to the
  internal list instead of registering them as entries, so the menu comes up
  empty with no warning at all. Counting the rows is what catches a slip back to
  one.
* **A composite Item with no implicit size collapses.** ``ViewHeader`` drops its
  children into a plain ``Row``, which asks each one how wide it is; answer zero
  and the button is simply not there — and a zero-width control is invisible in
  a screenshot too, so only a measurement finds it.

Driven through the real GUI rather than in isolation: the bindings under test
are the ones that only exist once the component is inside a header inside the
window, which is exactly where a standalone load would not put them.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QUrl
from PySide6.QtQml import QJSValue, QQmlApplicationEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.gui.gui_paths import bundled_gui_root  # noqa: E402
from app.main import CONTEXT_PROPERTIES, _register_qml_types  # noqa: E402


# What ImportSources.js declares, restated here on purpose: a test that read the
# catalog to check the catalog would agree with itself however wrong both were.
# A plain install: no plugin on, so nothing online to import from and no CSV.
PRIMARY = ("files", "Import files", "note")
ALTERNATES = [
    ("folder", "Import folder", "folder"),
]
# The same menu with a plugin on that can fetch audio and contributes a source.
WITH_PLUGIN = [
    ("folder", "Import folder", "folder"),
    ("plugin:test.fake/link", "From a fake link", "external"),
    ("csv", "Import CSV", "download"),
]


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The whole interface, loaded offscreen over a throwaway vault."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("buttonvault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())

    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load:\n" + "\n".join(
        w.toString() for w in warnings
    )
    qt_app.processEvents()

    yield roots[0], registry, qt_app, warnings

    del engine


def _pump_until(app, predicate, timeout: float = 3.0) -> bool:
    """Run the event loop until *predicate* holds.

    Needed for anything behind a `Behavior`: the opacity of a disabled button
    animates over `Theme.fast`, so the value one `processEvents` after the
    change is still the old one on its way down.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _js(value):
    """A QML `var` property as plain Python.

    QML hands JavaScript values across as `QJSValue`, which is neither a dict
    nor iterable — so every list or object property has to come back through
    here before it can be compared with anything.
    """
    return value.toVariant() if isinstance(value, QJSValue) else value


def _walk(obj):
    yield obj
    for child in obj.children():
        yield from _walk(child)


def _import_buttons(window):
    """Every ImportButton on screen, by the API only it has."""
    return [
        o for o in _walk(window)
        if o.property("arrowWidth") is not None
        and o.property("primary") is not None
    ]


def _app_button_labels(window):
    """Every plain AppButton's label — the control the old three sources were."""
    return {
        str(o.property("text"))
        for o in _walk(window)
        if o.metaObject().className().startswith("AppButton")
    }


def _menu_of(button):
    """The button's dropdown.

    Found by QML type name rather than by `isinstance`: a type declared in QML
    comes back as a bare QObject wrapper, so there is no Python class to test
    against and its QML members are reachable only through `property()` and
    `QMetaObject.invokeMethod`.
    """
    for o in _walk(button):
        if o.metaObject().className().startswith("NocturneMenu"):
            return o
    raise AssertionError("the button has no menu")


def _menu_rows(button):
    """The dropdown's rows, in menu order.

    Read off the object tree rather than through `Menu.itemAt`, which is not
    callable on a QML-declared type from Python. Child order is insertion
    order, and the Instantiator inserts in model order — so this *is* menu
    order, and a mis-ordered catalog would still show up.
    """
    return [
        o for o in _walk(button)
        if o.metaObject().className().startswith("MenuRow")
    ]


# ======================================================================
# The collapse
# ======================================================================


def test_import_control_is_available_only_on_the_import_page(gui):
    """Import stays reachable after removing its shortcut from Songs."""
    window, _, _, _ = gui

    buttons = _import_buttons(window)
    assert len(buttons) == 1, "one import control, on the Import page"

    labels = _app_button_labels(window)
    for gone in ("Import files", "Import folder", "Import CSV"):
        assert gone not in labels, f"{gone!r} is part of the split button now"


def test_the_button_has_a_size_to_be_laid_out_with(gui):
    """ViewHeader's Row asks; a composite that answers zero is simply not there."""
    window, _, _, _ = gui

    for button in _import_buttons(window):
        assert button.property("implicitHeight") == 36, "matches AppButton"
        assert button.property("implicitWidth") > 0
        assert button.property("width") > button.property("arrowWidth"), \
            "the label half has room of its own"


def test_the_primary_half_is_the_catalogs_first_entry(gui):
    window, _, _, _ = gui
    kind, label, icon = PRIMARY

    primary = _js(_import_buttons(window)[0].property("primary"))
    assert (primary["kind"], primary["label"], primary["icon"]) == (kind, label, icon)


# ======================================================================
# The dropdown
# ======================================================================


def test_the_menu_holds_the_other_sources_in_catalog_order(gui):
    """An Instantiator that failed to register would leave this empty, quietly."""
    window, _, _, _ = gui

    rows = _menu_rows(_import_buttons(window)[0])

    assert [(r.property("text"), r.property("iconName")) for r in rows] == [
        (label, icon) for _, label, icon in ALTERNATES
    ]


def test_the_primary_source_is_not_repeated_in_the_menu(gui):
    """It already has half the button; a second offer is only clutter."""
    window, _, _, _ = gui

    rows = _menu_rows(_import_buttons(window)[0])

    assert PRIMARY[1] not in [r.property("text") for r in rows]


def test_triggering_a_row_reports_that_rows_kind(gui):
    window, _, app, _ = gui
    button = _import_buttons(window)[0]
    chosen: list[str] = []
    button.sourceChosen.connect(chosen.append)

    for row in _menu_rows(button):
        row.triggered.emit()
    app.processEvents()

    assert chosen == [kind for kind, _, _ in ALTERNATES]


def test_a_plugin_brings_its_source_and_csv_with_it(gui):
    """The menu is the enabled plugins' as much as the catalog's."""
    from tests.fakes.providers import FakeAudio, FakeImportSource, FakeRegistry

    window, registry, app, _ = gui
    plugins = registry.get_service("plugins_view_model")
    button = _import_buttons(window)[0]
    real = plugins._registry
    plugins._registry = FakeRegistry(audio=FakeAudio(), import_source=FakeImportSource())
    plugins.plugins_changed.emit()
    app.processEvents()
    try:
        rows = _menu_rows(button)
        assert [(r.property("text"), r.property("iconName")) for r in rows] == [
            (label, icon) for _, label, icon in WITH_PLUGIN
        ]
        chosen: list[str] = []
        button.sourceChosen.connect(chosen.append)
        for row in rows:
            row.triggered.emit()
        app.processEvents()
        assert chosen == [kind for kind, _, _ in WITH_PLUGIN]
    finally:
        plugins._registry = real
        plugins.plugins_changed.emit()
        app.processEvents()

    assert len(_menu_rows(button)) == len(ALTERNATES), "and takes them away again"


def test_the_menu_opens_under_the_button_and_hangs_left(gui):
    """Both headers sit against the right margin, so it cannot open rightwards."""
    window, _, app, _ = gui
    button = _import_buttons(window)[0]
    menu = _menu_of(button)

    assert QMetaObject.invokeMethod(button, "openMenu")
    app.processEvents()
    try:
        assert menu.property("visible") is True
        assert menu.property("y") >= button.property("height"), "below, not over"
        assert menu.property("x") < 0, "right-aligned against a narrower button"
    finally:
        QMetaObject.invokeMethod(menu, "close")
        app.processEvents()


# ======================================================================
# Busy
# ======================================================================


def test_a_scan_in_progress_disables_the_whole_control(gui):
    """Both halves at once: half a disabled button is worse than none."""
    window, registry, app, _ = gui
    vm = registry.get_service("import_view_model")
    button = _import_buttons(window)[0]
    assert button.property("enabledAction") is True

    vm._set_is_analyzing(True)
    try:
        assert _pump_until(app, lambda: button.property("enabledAction") is False)
        assert _pump_until(
            app, lambda: button.property("opacity") == pytest.approx(0.4)
        ), "dimmed, which is how these buttons show disabled"
    finally:
        vm._set_is_analyzing(False)

    assert _pump_until(app, lambda: button.property("enabledAction") is True)
    assert _pump_until(app, lambda: button.property("opacity") == pytest.approx(1.0))


# ======================================================================
# The claim about adding sources
# ======================================================================


def test_a_new_catalog_entry_becomes_a_new_menu_row(gui):
    """The whole point of the catalog, exercised rather than asserted about.

    `sources` is what the button reads and what the Instantiator's model is
    derived from, so appending to it here is the same thing a fourth entry in
    ImportSources.js does at the next launch. If the menu were written out by
    hand instead — the state this change replaced — this would prove it by
    changing nothing.
    """
    window, _, app, _ = gui
    button = _import_buttons(window)[0]
    original = list(_js(button.property("sources")))
    before = _menu_rows(button)

    button.setProperty("sources", original + [
        {"kind": "podcast", "label": "Import podcast", "icon": "note"}
    ])
    app.processEvents()
    try:
        after = _menu_rows(button)
        assert len(after) == len(before) + 1
        assert "Import podcast" in [r.property("text") for r in after]
        # The new entry went to the dropdown, not to the button: only the first
        # of the list is ever the half you can click.
        assert _js(button.property("primary"))["kind"] == PRIMARY[0]
    finally:
        button.setProperty("sources", original)
        app.processEvents()

    assert len(_menu_rows(button)) == len(before)


def test_the_gui_reported_no_binding_errors(gui):
    """Bindings fail silently at runtime; this is the only place they surface."""
    _, _, _, warnings = gui

    fatal = [
        w.toString() for w in warnings
        if any(s in w.toString() for s in (
            "is not a function", "undefined", "Unable to assign",
            "Cannot read property", "is not available", "TypeError",
            "ReferenceError",
        ))
    ]
    assert not fatal, "\n".join(fatal)
