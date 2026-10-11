"""Cover selection, readable accents, and asynchronous track changes."""
import io
import time

import pytest
from PIL import Image
from PySide6.QtCore import QThreadPool, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlComponent, QQmlEngine

from app.artwork import THUMB_SIZE
from app.artwork.cover_store import CoverStore
from app.artwork.palette import extract_palette, luminance
from app.viewmodels.artwork_palette_view_model import ArtworkPaletteViewModel


def image_bytes(color):
    image = Image.new("RGBA", (64, 64), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def rgb(color):
    value = QColor(color)
    return value.redF(), value.greenF(), value.blueF()


@pytest.mark.parametrize("color", ["red", "blue", "yellow", "black", "white", "gray"])
def test_palette_has_readable_text_and_fill_contrast(color):
    palette = extract_palette(image_bytes(color))
    for key in ("a", "b"):
        light = luminance(rgb(palette[key]))
        assert (light + .05) / (luminance(rgb("#0A0B10")) + .05) >= 4.5
        assert (light + .05) / (luminance(rgb("#1E2331")) + .05) >= 4.5
    for key in ("la", "lb"):
        light = luminance(rgb(palette[key]))
        assert 1.05 / (light + .05) >= 4.5
        assert (luminance(rgb("#E7E9F0")) + .05) / (light + .05) >= 4.5


def test_colour_region_wins_over_white_border():
    image = Image.new("RGB", (64, 64), "white")
    image.paste("red", (16, 16, 48, 48))
    output = io.BytesIO()
    image.save(output, format="PNG")
    r, g, b = rgb(extract_palette(output.getvalue())["a"])
    assert r > g and r > b


@pytest.mark.parametrize("data", [b"broken", image_bytes((255, 0, 0, 0))])
def test_unusable_art_has_no_palette(data):
    assert extract_palette(data) == {}


def test_late_results_do_not_replace_current_cover(qt_app, tmp_path, monkeypatch):
    jobs = []

    class Pool:
        def start(self, job):
            jobs.append(job)

    monkeypatch.setattr(QThreadPool, "globalInstance", lambda: Pool())
    store = CoverStore(tmp_path / "covers.db")
    store.put("red", THUMB_SIZE, image_bytes("red"))
    store.put("blue", THUMB_SIZE, image_bytes("blue"))
    vm = ArtworkPaletteViewModel(store)
    vm.coverHash = "red"
    vm.coverHash = "blue"
    jobs[1].run()
    blue = vm.palette
    jobs[0].run()
    assert vm.palette == blue
    vm.coverHash = ""
    assert vm.palette == {}
    vm.coverHash = "red"
    assert vm.palette == extract_palette(image_bytes("red"))
    assert len(jobs) == 2  # Cached when revisiting the album.


def test_worker_delivers_palette_and_theme_toggle_restores_preset(qt_app, tmp_path):
    store = CoverStore(tmp_path / "covers.db")
    store.put("red", THUMB_SIZE, image_bytes("red"))
    vm = ArtworkPaletteViewModel(store)
    engine = QQmlEngine()
    from pathlib import Path
    engine.addImportPath(str(Path(__file__).resolve().parents[1] / "gui/nocturne"))
    engine.rootContext().setContextProperty("artworkPaletteVM", vm)
    component = QQmlComponent(engine)
    component.setData(b'''
        import QtQuick
        import Nocturne
        QtObject {
            property color accent: Theme.accent
            property var prefs: Theme.prefs
            property Binding bridge: Binding {
                target: Theme
                property: "artworkPalette"
                value: artworkPaletteVM.palette
            }
        }
    ''', QUrl())
    obj = component.create()
    assert obj is not None, component.errors()
    prefs = obj.property("prefs")
    old_mode, old_follow = prefs.property("themeMode"), prefs.property("followArtwork")
    try:
        prefs.setProperty("themeMode", "dark")
        prefs.setProperty("followArtwork", False)
        preset = obj.property("accent")
        prefs.setProperty("followArtwork", True)
        vm.coverHash = "red"
        deadline = time.monotonic() + 5
        while not vm.palette and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(.01)
        assert vm.palette
        assert obj.property("accent") == QColor(vm.palette["a"])
        prefs.setProperty("themeMode", "light")
        assert obj.property("accent") == QColor(vm.palette["la"])
        prefs.setProperty("themeMode", "dark")
        prefs.setProperty("followArtwork", False)
        assert obj.property("accent") == preset
        prefs.setProperty("followArtwork", True)
        vm.coverHash = ""
        assert obj.property("accent") == preset
    finally:
        QThreadPool.globalInstance().waitForDone()
        prefs.setProperty("themeMode", old_mode)
        prefs.setProperty("followArtwork", old_follow)
