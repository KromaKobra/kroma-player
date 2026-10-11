"""Exercise reusable editors through keyboard input and external value changes.

A control must keep observing its owner after the first edit, including when
that owner rejects or normalises a proposed value.
"""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


@pytest.fixture
def controls(qt_app):
    directory = Path(__file__).resolve().parents[1] / "gui" / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.loadData(b'''
import QtQuick
import Nocturne
import "components/settings"
Window {
    id: window
    width: 640; height: 520; visible: true
    property real backendNumber: 20
    property real backendGain: 0
    property bool backendToggle: false
    property int backendChoice: 0
    property bool rejectChanges: false
    Column {
        width: parent.width - 40
        x: 20
        NumberSetting {
            objectName: "number"
            width: parent.width
            label: "History limit"
            minimum: 10; maximum: 100; step: 10
            value: window.backendNumber
            onCommitted: function(v) { if (!window.rejectChanges) window.backendNumber = v }
        }
        SliderSetting {
            objectName: "gain"
            width: parent.width
            label: "Instrumental level"
            minimum: -12; maximum: 12; step: 0.5; decimals: 1
            value: window.backendGain
            onCommitted: function(v) { if (!window.rejectChanges) window.backendGain = v }
        }
        ToggleSwitch {
            objectName: "toggle"
            width: parent.width
            label: "Follow artwork"
            checked: window.backendToggle
            onToggled: function(v) { if (!window.rejectChanges) window.backendToggle = v }
        }
        ChoiceSetting {
            objectName: "choice"
            width: parent.width
            label: "Missing instrumental"
            segmented: true
            options: ["Play", "Skip"]
            currentIndex: window.backendChoice
            onSelected: function(i) { if (!window.rejectChanges) window.backendChoice = i }
        }
    }
}
''', QUrl.fromLocalFile(str(directory / "settings-control-test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    yield window, qt_app, warnings
    window.close()
    del engine


def test_number_can_be_typed_and_still_tracks_external_updates(controls):
    window, app, warnings = controls
    number = window.findChild(QObject, "number")
    field = number.findChild(QQuickItem, "numberInput")
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(window, Qt.Key_4)
    QTest.keyClick(window, Qt.Key_5)
    QTest.keyClick(window, Qt.Key_Return)
    app.processEvents()
    assert window.property("backendNumber") == 45
    window.setProperty("backendNumber", 70)
    app.processEvents()
    assert number.property("value") == 70
    assert field.property("text") == "70"
    window.setProperty("rejectChanges", True)
    QTest.keyClick(window, Qt.Key_Up)
    app.processEvents()
    assert number.property("value") == 70
    assert field.property("text") == "70"
    assert not warnings


def test_number_bounds_and_escape_do_not_store_invalid_edits(controls):
    window, app, warnings = controls
    number = window.findChild(QObject, "number")
    field = number.findChild(QQuickItem, "numberInput")
    window.setProperty("backendNumber", 100)
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Up)
    assert window.property("backendNumber") == 100
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(window, Qt.Key_Backspace)
    QTest.keyClick(window, Qt.Key_Return)
    assert window.property("backendNumber") == 100
    QTest.keyClick(window, Qt.Key_Escape)
    app.processEvents()
    assert field.property("text") == "100"
    assert not warnings


def test_toggle_keyboard_activation_keeps_backend_authoritative(controls):
    window, app, warnings = controls
    toggle = window.findChild(QQuickItem, "toggle")
    button = next(item for item in toggle.findChildren(QQuickItem) if item.metaObject().indexOfSignal("clicked()") >= 0)
    button.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    app.processEvents()
    assert window.property("backendToggle") is True
    window.setProperty("backendToggle", False)
    app.processEvents()
    assert toggle.property("checked") is False
    window.setProperty("rejectChanges", True)
    QTest.keyClick(window, Qt.Key_Space)
    app.processEvents()
    assert button.property("checked") is False
    assert not warnings


def test_choice_remains_bound_after_keyboard_selection(controls):
    window, app, warnings = controls
    choice = window.findChild(QQuickItem, "choice")
    def descendants(item):
        for child in item.childItems():
            yield child
            yield from descendants(child)

    skip = next(item for item in descendants(choice) if item.property("text") == "Skip" and item.metaObject().indexOfSignal("clicked()") >= 0)
    skip.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    app.processEvents()
    assert window.property("backendChoice") == 1
    window.setProperty("backendChoice", 0)
    app.processEvents()
    assert choice.property("currentIndex") == 0
    assert skip.property("highlighted") is False
    assert not warnings


def test_slider_keyboard_adjustment_and_external_value_changes(controls):
    window, app, warnings = controls
    gain = window.findChild(QQuickItem, "gain")
    slider = next(item for item in gain.findChildren(QQuickItem) if item.metaObject().indexOfSignal("moved()") >= 0)
    slider.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Right)
    app.processEvents()
    assert window.property("backendGain") == 0.5
    window.setProperty("backendGain", -3)
    app.processEvents()
    assert slider.property("value") == -3
    window.setProperty("rejectChanges", True)
    QTest.keyClick(window, Qt.Key_Right)
    app.processEvents()
    assert slider.property("value") == -3
    window.setProperty("rejectChanges", False)
    QTest.keyClick(window, Qt.Key_End)
    app.processEvents()
    assert window.property("backendGain") == 12
    assert not warnings
