"""The decisions the vocal-removal sidecar makes before it touches a GPU.

Everything here runs in Kroma's own interpreter, which by design has neither
PyTorch nor any separation library in it — that is the whole reason the sidecar
is a separate process. So what is testable here is exactly the part that is
worth testing anyway: which backend gets chosen, which architecture a config
turns out to describe, and which of a model's stems is the instrumental. Those
are the three ways a run silently does the wrong thing, and all three are pure
functions of things a test can hand them.

The one end-to-end case is the sidecar refusing to run under this interpreter,
which pins the protocol and the pipe encoding at the same time.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.separation.runner import probe_devices  # noqa: E402
from app.separation.sidecar import separate as sidecar  # noqa: E402


def make_msst_root(directory: Path) -> Path:
    """Build the smallest thing that counts as an MSST checkout."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "inference.py").write_text("", encoding="utf-8")
    (directory / "utils").mkdir(exist_ok=True)
    (directory / "utils" / "settings.py").write_text("", encoding="utf-8")
    (directory / "models").mkdir(exist_ok=True)
    return directory


def config(model: dict | str, **training) -> SimpleNamespace:
    return SimpleNamespace(model=model, training=SimpleNamespace(**training))


# ======================================================================
# Which architecture is this?
# ======================================================================


def test_mel_band_roformer_recognised_by_its_band_count():
    """The shape of the model section is what names the architecture.

    A checkpoint carries no record of what built it, and the configs that ship
    with these models are not named after them either, so this is the only
    thing left to go on.
    """
    detected = sidecar.detect_model_type(config({"num_bands": 60, "dim": 256}))
    assert detected == "mel_band_roformer"


def test_conformer_variant_distinguished_from_the_transformer_one():
    """Same band splitting, different blocks — and different weights."""
    detected = sidecar.detect_model_type(
        config({"num_bands": 60, "time_conformer_depth": 1})
    )
    assert detected == "mel_band_conformer"


def test_bs_roformer_recognised_by_its_explicit_bands():
    detected = sidecar.detect_model_type(config({"freqs_per_bands": (2, 2, 4)}))
    assert detected == "bs_roformer"


def test_a_config_that_names_its_own_type_is_believed():
    """An explicit declaration always beats the heuristic.

    This is the escape hatch offered when detection fails, so it has to
    outrank a structure that would otherwise be guessed at.
    """
    detected = sidecar.detect_model_type(
        config({"num_bands": 60}, model_type="something_custom")
    )
    assert detected == "something_custom"


def test_an_unrecognisable_config_says_what_to_write():
    """A dead end is not acceptable; the error has to be actionable."""
    with pytest.raises(sidecar.SidecarError) as raised:
        sidecar.detect_model_type(config({"mystery": 1}))
    assert "model_type" in str(raised.value)


# ======================================================================
# Which stem is the instrumental?
# ======================================================================


class Signal:
    """Stands in for a waveform. Subtraction is all the picker needs."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __sub__(self, other: "Signal") -> "Signal":
        return Signal(f"{self.name}-{other.name}")


def pick(stems: dict) -> str:
    chosen = sidecar._pick_instrumental(
        {k: Signal(k) for k in stems}, Signal("mix"), lambda x: x
    )
    return chosen.name


def test_the_stem_called_other_is_the_instrumental():
    """Single-stem instrumental models name their output after MUSDB's `other`.

    Taking it by position instead would work right up until a model returned
    its stems in a different order.
    """
    assert pick({"other": ..., "vocals": ...}) == "other"


def test_a_vocal_model_is_used_by_subtracting_it():
    """What a vocals model leaves out of the mixture is what is wanted.

    Refusing these would rule out a large share of the checkpoints people
    actually have, for no reason.
    """
    assert pick({"vocals": ...}) == "mix-vocals"


def test_a_lone_unrecognised_stem_is_taken_as_the_target():
    assert pick({"accompaniment_v2": ...}) == "accompaniment_v2"


def test_stems_that_contain_no_instrumental_are_refused_by_name():
    with pytest.raises(sidecar.SidecarError) as raised:
        pick({"drums": ..., "bass": ...})
    assert "bass" in str(raised.value) and "drums" in str(raised.value)


# ======================================================================
# Which backend runs it?
# ======================================================================


def test_a_checkout_is_recognised_by_its_contents_not_its_name(tmp_path):
    root = make_msst_root(tmp_path / "whatever-they-called-it")
    assert sidecar.find_msst(str(root)) == root


def test_a_configured_path_that_is_wrong_is_reported_not_ignored(tmp_path):
    """Falling back would leave a broken setting on screen looking correct."""
    missing = tmp_path / "not-a-checkout"
    missing.mkdir()
    with pytest.raises(sidecar.SidecarError) as raised:
        sidecar.choose_backend(sidecar.BACKEND_AUTO, str(missing))
    assert str(missing) in str(raised.value)


def test_a_configured_checkout_wins_over_an_installed_package(tmp_path, monkeypatch):
    """Naming a folder is a clearer statement of intent than a pip install.

    ``audio-separator`` can arrive as somebody else's dependency; nobody
    browses to a checkout by accident.
    """
    monkeypatch.setattr(sidecar, "_has_audio_separator", lambda: True)
    root = make_msst_root(tmp_path / "msst")
    backend, found = sidecar.choose_backend(sidecar.BACKEND_AUTO, str(root))
    assert (backend, found) == (sidecar.BACKEND_MSST, root)


def test_an_environment_with_neither_backend_names_both_remedies(monkeypatch):
    monkeypatch.setattr(sidecar, "_has_audio_separator", lambda: False)
    monkeypatch.setattr(sidecar, "find_msst", lambda explicit="": None)
    with pytest.raises(sidecar.SidecarError) as raised:
        sidecar.choose_backend(sidecar.BACKEND_AUTO, "")
    message = str(raised.value)
    assert "audio-separator" in message and "Separation-Training" in message


def test_a_checkout_beside_the_interpreter_is_found_without_being_configured(
    tmp_path, monkeypatch
):
    """A checkout's virtualenv normally lives inside it, so look there first.

    This is what spares the common case any configuration at all.
    """
    project = tmp_path / "AI-Extraction"
    make_msst_root(project / "msst")
    monkeypatch.setattr(sidecar.sys, "prefix", str(project / ".venv"))
    monkeypatch.delenv(sidecar.MSST_PATH_ENV, raising=False)
    assert sidecar.find_msst("") == project / "msst"


# ======================================================================
# The protocol, over a real pipe
# ======================================================================


def test_kromas_own_interpreter_reports_that_it_cannot_do_this():
    """The sidecar has to fail cleanly in the environment it cannot run in.

    Kroma's interpreter is deliberately one of those — no PyTorch is published
    for it — so this is both the negative case and a check that the protocol
    survives the round trip through a pipe between two interpreters.
    """
    probe = probe_devices(sys.executable)
    assert probe.devices == []
    assert "PyTorch" in probe.error
