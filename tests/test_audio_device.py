"""Which output device playback lands on, and how it survives a reconnect.

The resolver is deliberately a plain function over a list of devices rather than
a method that reaches for ``QMediaDevices``, so the cases that matter — a
Bluetooth pair coming back under a different profile, a pinned device that is
simply not here — are testable without an audio stack. These tests run headless
and never open a stream.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.audio.qt_multimedia_backend import (  # noqa: E402
    SYSTEM_DEFAULT_DEVICE,
    resolve_output_device,
)
from app.settings.playback import DEFAULT_OUTPUT_DEVICE, PlaybackSettings  # noqa: E402
from app.settings.store import SettingsStore  # noqa: E402


class _StubDevice:
    """Just enough QAudioDevice to resolve against: an id and a description."""

    def __init__(self, device_id: str, description: str) -> None:
        self._id = device_id.encode()
        self._description = description

    def id(self) -> bytes:
        return self._id

    def description(self) -> str:
        return self._description


SPEAKER = _StubDevice("alsa_output.pci-0000_00_1f.3.HiFi__Speaker__sink", "Speaker")
# The trailing index is the Bluetooth profile the device connected under, and it
# is the thing that moves when the headphones are switched off and on again.
HEADSET_1 = _StubDevice("bluez_output.66_78_14_B3_3A_24.1", "S58")
HEADSET_2 = _StubDevice("bluez_output.66_78_14_B3_3A_24.2", "S58")


def test_nothing_pinned_resolves_to_the_system_default():
    assert resolve_output_device([SPEAKER, HEADSET_1], SYSTEM_DEFAULT_DEVICE, "") is None


def test_a_pinned_device_present_by_id_wins():
    found = resolve_output_device(
        [SPEAKER, HEADSET_1], "bluez_output.66_78_14_B3_3A_24.1", "S58"
    )
    assert found is HEADSET_1


def test_a_reconnected_bluetooth_device_is_found_by_name():
    """The whole reason the description is stored beside the id.

    The saved pin names profile 1; the headphones came back on profile 2. Same
    physical device, and playback has to go back to it.
    """
    found = resolve_output_device(
        [SPEAKER, HEADSET_2], "bluez_output.66_78_14_B3_3A_24.1", "S58"
    )
    assert found is HEADSET_2


def test_the_id_is_preferred_over_a_shared_name():
    """Two devices can describe themselves identically; an exact id still wins."""
    twin = _StubDevice("bluez_output.66_78_14_B3_3A_24.9", "S58")
    found = resolve_output_device(
        [twin, HEADSET_1], "bluez_output.66_78_14_B3_3A_24.1", "S58"
    )
    assert found is HEADSET_1


def test_a_disconnected_pin_falls_back_rather_than_failing():
    """Headphones switched off mid-track: keep playing, on the default."""
    assert resolve_output_device(
        [SPEAKER], "bluez_output.66_78_14_B3_3A_24.1", "S58"
    ) is None


def test_an_unknown_pin_with_no_name_falls_back():
    assert resolve_output_device([SPEAKER], "some-device-that-left", "") is None


def test_the_device_defaults_to_the_system_default():
    settings = PlaybackSettings(SettingsStore({}, {}))
    assert settings.output_device == DEFAULT_OUTPUT_DEVICE == ""
    assert settings.output_device_name == ""


def test_the_device_round_trips_through_the_store():
    store = SettingsStore({}, {})
    settings = PlaybackSettings(store)

    settings.output_device = "bluez_output.66_78_14_B3_3A_24.1"
    settings.output_device_name = "S58"

    reread = PlaybackSettings(store)
    assert reread.output_device == "bluez_output.66_78_14_B3_3A_24.1"
    assert reread.output_device_name == "S58"


def test_settings_without_a_store_serve_the_default():
    """A PlayerViewModel built without a session still has a device to push."""
    settings = PlaybackSettings(None)
    assert settings.output_device == ""
    assert settings.output_device_name == ""
