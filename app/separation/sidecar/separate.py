"""Vocal-removal sidecar. Runs under the *user's* interpreter, not Kroma's.

This file is executed as a script by :mod:`app.separation.runner` with a
different Python than the one running the app, so it must import nothing from
``app`` and nothing Kroma ships — only the standard library and whatever the
user installed alongside their PyTorch build.

It speaks one line-oriented protocol on stdout so the caller never has to parse
a third party's log formatting:

    KROMA_DEVICES cuda,cpu      the accelerators this environment can use
    KROMA_BACKEND msst          the separation code found here, if any
    KROMA_BACKEND_ERROR <text>  why there is none — not fatal to a probe
    KROMA_STAGE <text>          human-readable phase, for the UI
    KROMA_PROGRESS <0-100>      progress within the current file
    KROMA_OUTPUT <path>         absolute path to the instrumental that was written
    KROMA_ERROR <text>          fatal; the process exits non-zero after this

Anything else printed here (and everything on stderr, which is where the model
libraries do their logging) is captured and logged by the caller, not parsed.

Two backends
------------

There is no one way to run a separation model, so this supports the two that
people actually have:

``audio-separator``
    The pip package. Installing it is one command, and it owns model loading
    end to end.

``msst``
    A source checkout of ZFTurbo's *Music-Source-Separation-Training* — the
    repository the RoFormer instrumental checkpoints are trained and released
    with. It is not on PyPI, so it is used by pointing at the directory rather
    than by importing a package name.

The second exists because the first cannot always be installed: PyTorch built
for ROCm, or for a CUDA version, comes from a vendor index, and letting pip
resolve ``audio-separator``'s dependency tree against that index is how a
working GPU build turns into a CPU-only one. Someone in that position already
has an MSST checkout, because that is what they used to get the model running
in the first place, and asking them to risk their environment to use Kroma
would be the wrong trade.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

BACKEND_AUTO = "auto"
BACKEND_AUDIO_SEPARATOR = "audio-separator"
BACKEND_MSST = "msst"
BACKENDS = (BACKEND_AUTO, BACKEND_AUDIO_SEPARATOR, BACKEND_MSST)

# Directory names an MSST checkout is plausibly called, checked beside and
# inside the interpreter's environment. Auto-discovery is a convenience only —
# `--msst-path` is authoritative, and the error when nothing is found says so.
MSST_DIR_NAMES = ("msst", "MSST", "Music-Source-Separation-Training")

# Set by the caller to override discovery without a command-line argument.
MSST_PATH_ENV = "KROMA_MSST_PATH"


class SidecarError(RuntimeError):
    """Anything that should reach the user as a KROMA_ERROR line."""


def say(tag: str, value: str = "") -> None:
    """Emit one protocol line and flush.

    Flushing matters: the caller reads this pipe live to drive a progress bar,
    and Python block-buffers stdout when it is not a terminal.
    """
    sys.stdout.write(f"KROMA_{tag} {value}\n".rstrip() + "\n")
    sys.stdout.flush()


def probe(msst_path: str = "") -> int:
    """Report what this environment can do — devices, and which backend.

    The backend is reported here rather than discovered at generation time so
    that "there is no code to run the model with" surfaces in Settings, where
    it can be fixed, instead of two percent into a job the user just started.
    """
    devices = []
    try:
        import torch
    except ImportError:
        say("ERROR", "PyTorch is not installed in this interpreter")
        return 1

    try:
        if torch.cuda.is_available():
            # ROCm builds report through the CUDA API, so the distinction is in
            # the build string rather than the device query.
            version = getattr(torch, "version", None)
            if getattr(version, "hip", None):
                devices.append("rocm")
            else:
                devices.append("cuda")
    except Exception:  # noqa: BLE001 - a broken driver must not stop CPU working
        pass

    devices.append("cpu")
    say("DEVICES", ",".join(devices))

    try:
        backend, _root = choose_backend(BACKEND_AUTO, msst_path)
        say("BACKEND", backend)
    except SidecarError as exc:
        # Not an ERROR: the question asked was answerable and was answered.
        say("BACKEND_ERROR", str(exc))
    return 0


# ======================================================================
# Backend selection
# ======================================================================


def _is_msst_root(path: Path) -> bool:
    """Whether *path* looks like a Music-Source-Separation-Training checkout.

    Checked by contents rather than by name: the directory is whatever the user
    called it when they cloned it.
    """
    try:
        return (
            (path / "inference.py").is_file()
            and (path / "utils" / "settings.py").is_file()
            and (path / "models").is_dir()
        )
    except OSError:
        return False


def find_msst(explicit: str = "") -> Path | None:
    """Locate an MSST checkout, or None.

    An explicit path is taken at face value and never second-guessed — if it is
    wrong the user gets an error naming it, which is more useful than silently
    separating with something else. Discovery only runs when nothing was given,
    and looks where the interpreter itself is: an MSST user's virtualenv almost
    always lives inside, or beside, the checkout it was made for.
    """
    if explicit:
        candidate = Path(explicit)
        return candidate if _is_msst_root(candidate) else None

    from_env = os.environ.get(MSST_PATH_ENV, "").strip()
    if from_env:
        candidate = Path(from_env)
        return candidate if _is_msst_root(candidate) else None

    prefix = Path(sys.prefix)
    for start in (prefix.parent, prefix):
        if _is_msst_root(start):
            return start
        for name in MSST_DIR_NAMES:
            if _is_msst_root(start / name):
                return start / name
    return None


def _has_audio_separator() -> bool:
    from importlib.util import find_spec
    try:
        return find_spec("audio_separator") is not None
    except (ImportError, ValueError):
        return False


def choose_backend(requested: str, msst_path: str) -> tuple[str, Path | None]:
    """Resolve ``auto`` to a backend that is actually present here.

    A configured MSST path wins over an installed ``audio-separator``: the user
    went out of their way to name a checkout, and that is a clearer statement of
    intent than a package that may have arrived as somebody else's dependency.
    """
    found = find_msst(msst_path)

    # A path that was set by hand and does not check out is a mistake to
    # report, not a preference to fall back from — quietly separating with
    # something else would leave the wrong path sitting in Settings looking
    # like it works.
    if msst_path and found is None:
        raise SidecarError(_msst_not_found_message(msst_path))

    if requested == BACKEND_MSST:
        if found is None:
            raise SidecarError(_msst_not_found_message(msst_path))
        return (BACKEND_MSST, found)

    if requested == BACKEND_AUDIO_SEPARATOR:
        if not _has_audio_separator():
            raise SidecarError(
                "audio-separator is not installed in this interpreter "
                "(pip install audio-separator)"
            )
        return (BACKEND_AUDIO_SEPARATOR, None)

    if msst_path and found is not None:
        return (BACKEND_MSST, found)
    if _has_audio_separator():
        return (BACKEND_AUDIO_SEPARATOR, None)
    if found is not None:
        return (BACKEND_MSST, found)

    raise SidecarError(
        "This interpreter has no separation backend. Either install one with "
        "`pip install audio-separator`, or point Kroma at a "
        "Music-Source-Separation-Training checkout in Settings."
    )


def _msst_not_found_message(msst_path: str) -> str:
    if msst_path:
        return (
            f"{msst_path} is not a Music-Source-Separation-Training checkout — "
            "it should contain inference.py, utils/ and models/"
        )
    return (
        "No Music-Source-Separation-Training checkout was found near this "
        "interpreter — choose its folder in Settings"
    )


# ======================================================================
# Backend: audio-separator
# ======================================================================


def stage_model(model: str, config: str, workdir: str) -> str:
    """Return a checkpoint path whose config sits beside it under the same name.

    Separation backends find a checkpoint's config by name, but the configs
    people actually download are rarely named after the weights
    (``inst_gaboxFlowersV10.ckpt`` ships with ``v10.yaml``). Rather than make
    the user rename files, the pair is staged into a scratch directory with the
    names the backend expects.

    The checkpoint is hard-linked when the filesystem allows it, so this costs
    nothing for a 500 MB file; a copy is the fallback across volumes.
    """
    if not config:
        return model

    stem = os.path.splitext(os.path.basename(model))[0]
    config_ext = os.path.splitext(config)[1]
    if os.path.splitext(os.path.basename(config))[0] == stem:
        return model            # already named correctly; nothing to do

    staged_model = os.path.join(workdir, os.path.basename(model))
    staged_config = os.path.join(workdir, stem + config_ext)

    try:
        os.link(model, staged_model)
    except OSError:
        say("STAGE", "Copying checkpoint")
        shutil.copy2(model, staged_model)
    shutil.copy2(config, staged_config)
    return staged_model


def run_audio_separator(args: argparse.Namespace) -> str:
    """Separate with the ``audio-separator`` package. Returns the output path."""
    from audio_separator.separator import Separator

    use_autocast = False
    if args.device in ("cuda", "rocm", "auto"):
        try:
            import torch
            if torch.cuda.is_available():
                use_autocast = True
            elif args.device != "auto":
                say("STAGE", f"{args.device} unavailable, falling back to CPU")
        except ImportError:
            pass

    say("STAGE", "Loading model")
    say("PROGRESS", "8")

    with tempfile.TemporaryDirectory(prefix="kroma-model-") as staging:
        try:
            model = stage_model(args.model, args.config or "", staging)
        except OSError as exc:
            raise SidecarError(f"Could not prepare the model files: {exc}") from exc

        separator = Separator(
            output_dir=args.output_dir,
            output_format="wav",
            model_file_dir=os.path.dirname(os.path.abspath(model)),
            use_autocast=use_autocast,
            output_single_stem="Instrumental",
        )
        separator.load_model(model_filename=os.path.basename(model))

        say("STAGE", "Separating")
        say("PROGRESS", "20")

        outputs = separator.separate(args.input)

    if not outputs:
        raise SidecarError("The model produced no output")

    # `output_single_stem` should leave exactly one file, but a model that
    # ignores it would leave several — pick the instrumental explicitly rather
    # than trusting position.
    chosen = next(
        (n for n in outputs if "instrumental" in os.path.basename(n).lower()),
        outputs[0],
    )
    if not os.path.isabs(chosen):
        chosen = os.path.join(args.output_dir, chosen)
    return chosen


# ======================================================================
# Backend: Music-Source-Separation-Training
# ======================================================================

# MSST needs to be told which architecture a checkpoint is, and a checkpoint on
# its own does not say. Its config does, indirectly: each architecture takes
# constructor keywords no other one does, so the presence of a single key
# identifies the family. Anything ambiguous or unlisted is not guessed at — the
# user is told to write `model_type:` under `training:`, which MSST itself reads
# and which then settles it permanently for that model.
_MODEL_TYPE_MARKERS = (
    # (marker key under `model:`, model_type, model_type when it is a conformer)
    ("num_bands", "mel_band_roformer", "mel_band_conformer"),
    ("freqs_per_bands", "bs_roformer", "bs_conformer"),
    ("num_subbands", "mdx23c", ""),
    ("band_SR", "scnet", ""),
)

# Stem names that mean "the instrumental", most specific first. `other` is on
# the list because single-stem instrumental models conventionally name their
# one output after the MUSDB stem they were derived from.
_INSTRUMENTAL_NAMES = (
    "instrumental", "instrum", "inst", "other",
    "accompaniment", "no_vocals", "music", "backing",
)

# tqdm description MSST gives its per-chunk bar. Matching on it is how the bar
# becomes a progress percentage; if a future MSST renames it, progress simply
# stops moving between "Separating" and "Writing" rather than breaking.
_CHUNK_BAR_DESC = "Processing audio chunks"

# The slice of the overall 0-100 that chunk processing owns.
_SEPARATION_START = 20
_SEPARATION_END = 92


class _Sink:
    """Stands in for a stream so tqdm renders its bar into nothing.

    The bar is redundant here — its numbers are being forwarded as
    KROMA_PROGRESS — and letting it draw would put tens of thousands of
    carriage-returned lines into the caller's stderr log.
    """

    def write(self, _text: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _install_chunk_progress(model_utils) -> None:
    """Make MSST's per-chunk progress bar report through the protocol.

    ``demix`` drives a tqdm it creates from its own module global, so replacing
    that global is enough to see every update without touching MSST's source or
    reimplementing its chunking — which is where the actual audio quality lives
    and is the last thing worth duplicating.
    """
    base = model_utils.tqdm
    last = [-1]

    class _Reporting(base):
        def __init__(self, *args, **kwargs):
            kwargs["file"] = _Sink()
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            result = super().update(n)
            if self.total and str(self.desc or "").startswith(_CHUNK_BAR_DESC):
                span = _SEPARATION_END - _SEPARATION_START
                percent = _SEPARATION_START + int(
                    span * min(1.0, self.n / self.total)
                )
                if percent != last[0]:
                    last[0] = percent
                    say("PROGRESS", str(percent))
            return result

    model_utils.tqdm = _Reporting


def detect_model_type(config, override: str = "") -> str:
    """Work out which MSST architecture a config describes."""
    if override:
        return override

    training = getattr(config, "training", None)
    declared = getattr(training, "model_type", "") if training is not None else ""
    if declared:
        return str(declared)

    model_section = getattr(config, "model", None)
    if isinstance(model_section, str):
        # htdemucs configs put the name where every other config puts a mapping.
        return model_section

    keys = set(model_section.keys()) if model_section is not None else set()
    conformer = any(k.endswith("conformer_depth") for k in keys)
    for marker, transformer_type, conformer_type in _MODEL_TYPE_MARKERS:
        if marker in keys:
            if conformer and conformer_type:
                return conformer_type
            return transformer_type

    raise SidecarError(
        "Could not tell which architecture this model is. Add a line "
        "`model_type: mel_band_roformer` (or whichever it is) under "
        "`training:` in its .yaml config."
    )


def _pick_instrumental(waveforms: dict, mix, restore) -> "object":
    """Return the instrumental from whatever stems the model produced.

    Models are not consistent about naming, and some are trained on vocals
    rather than on the instrumental. Both are usable: what a vocal model leaves
    out of the mixture is exactly what is wanted, so the residual is taken
    rather than refusing the model.
    """
    named = {str(k).lower(): k for k in waveforms}

    for wanted in _INSTRUMENTAL_NAMES:
        if wanted in named:
            return restore(waveforms[named[wanted]])

    if len(named) == 1:
        only = next(iter(named))
        if only != "vocals":
            # A single-stem model's one output is its target, whatever it is
            # called.
            return restore(waveforms[named[only]])

    if "vocals" in named:
        return mix - restore(waveforms[named["vocals"]])

    raise SidecarError(
        "The model produced no instrumental stem — it returned: "
        + (", ".join(sorted(named)) or "nothing")
    )


def run_msst(args: argparse.Namespace, root: Path) -> str:
    """Separate with an MSST checkout. Returns the output path."""
    if not args.config:
        raise SidecarError(
            "This backend needs the model's .yaml config, and none was paired "
            "with the checkpoint"
        )

    import numpy as np
    import torch

    # Ahead of MSST's own imports: its modules are found as top-level `utils`
    # and `models` packages, exactly as inference.py arranges it.
    sys.path.insert(0, str(root))
    try:
        from utils.audio_utils import denormalize_audio, normalize_audio
        from utils.model_utils import demix, prefer_target_instrument
        from utils.settings import get_model_from_config
        from utils import model_utils
    except ImportError as exc:
        raise SidecarError(
            f"Could not load the separation code from {root}: {exc}"
        ) from exc

    _install_chunk_progress(model_utils)

    device = "cpu"
    if args.device != "cpu" and torch.cuda.is_available():
        # ROCm presents itself through the CUDA API, so this is the GPU path for
        # both. A named device that is not there falls back rather than failing:
        # a slow instrumental beats no instrumental.
        device = "cuda:0"
    elif args.device in ("cuda", "rocm"):
        say("STAGE", f"{args.device} unavailable, falling back to CPU")

    say("STAGE", "Loading model")
    say("PROGRESS", "8")

    torch.backends.cudnn.benchmark = True
    model_type = detect_model_type(_load_config_only(args.config), args.model_type)
    model, config = get_model_from_config(model_type, args.config)
    # get_model_from_config honours a model_type written into the config, so
    # read back what it actually built rather than what was asked for.
    model_type = str(getattr(config.training, "model_type", model_type))

    say("STAGE", "Loading weights")
    say("PROGRESS", "14")
    _load_checkpoint(model, args.model, torch)

    model = model.to(device)
    model.eval()

    say("STAGE", "Reading audio")
    say("PROGRESS", "18")
    import librosa
    sample_rate = int(getattr(config.audio, "sample_rate", 44100))
    mix, _sr = librosa.load(args.input, sr=sample_rate, mono=False)
    if mix.ndim == 1:
        mix = np.expand_dims(mix, axis=0)
        if int(config.audio.get("num_channels", 2)) == 2:
            mix = np.concatenate([mix, mix], axis=0)

    mix_original = mix.copy()
    norm_params = None
    if config.inference.get("normalize", False):
        mix, norm_params = normalize_audio(mix)

    say("STAGE", "Separating")
    say("PROGRESS", str(_SEPARATION_START))

    waveforms = demix(config, model, mix, device, model_type=model_type, pbar=True)
    if not isinstance(waveforms, dict):
        # Demucs' single-source path returns a bare array; name it after the one
        # instrument the config declares.
        waveforms = {prefer_target_instrument(config)[0]: waveforms}

    def restore(stem):
        return denormalize_audio(stem, norm_params) if norm_params else stem

    estimate = _pick_instrumental(waveforms, mix_original, restore)

    say("STAGE", "Writing")
    say("PROGRESS", "94")

    # Mask-based separation regularly reconstructs samples above full scale —
    # the sum of the parts overshoots the mixture it came from. Float WAV holds
    # that, but the Opus encode downstream would clip it, so the one place it
    # can be fixed losslessly is here, with a single global attenuation.
    peak = float(np.abs(estimate).max())
    if peak > 1.0:
        estimate = estimate / peak
        say("STAGE", f"Attenuated {20 * np.log10(peak):.1f} dB to avoid clipping")

    import soundfile as sf
    os.makedirs(args.output_dir, exist_ok=True)
    output = os.path.join(
        args.output_dir,
        f"{Path(args.input).stem}_Instrumental.wav",
    )
    sf.write(output, estimate.T, sample_rate, subtype="FLOAT")
    return output


def _load_config_only(config_path: str):
    """Parse a config without building the model it describes.

    Needed because working out the architecture is what tells
    ``get_model_from_config`` which model to build in the first place.
    """
    from utils.settings import load_config
    return load_config("", config_path)


def _load_checkpoint(model, checkpoint_path: str, torch) -> None:
    """Load weights, unwrapping the shapes trainers save them in."""
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    for key in ("state", "state_dict", "model_state_dict"):
        if isinstance(state, dict) and key in state:
            state = state[key]
    model.load_state_dict(state)


# ======================================================================


def separate(args: argparse.Namespace) -> int:
    say("STAGE", "Loading PyTorch")
    say("PROGRESS", "2")

    try:
        backend, msst_root = choose_backend(args.backend, args.msst_path)
    except SidecarError as exc:
        say("ERROR", str(exc))
        return 1

    try:
        if backend == BACKEND_MSST:
            assert msst_root is not None
            chosen = run_msst(args, msst_root)
        else:
            chosen = run_audio_separator(args)
    except SidecarError as exc:
        say("ERROR", str(exc))
        traceback.print_exc(file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - anything here is fatal for this file
        say("ERROR", f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        return 1

    if not chosen or not os.path.exists(chosen):
        say("ERROR", f"Model reported a file that is not there: {chosen}")
        return 1

    say("PROGRESS", "100")
    say("OUTPUT", os.path.abspath(chosen))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kroma Player vocal-removal sidecar")
    parser.add_argument("--probe", action="store_true",
                        help="report available devices and exit")
    parser.add_argument("--model", help="path to the .ckpt checkpoint")
    parser.add_argument("--config", default="",
                        help="path to the checkpoint's .yaml config; it need not "
                             "share the checkpoint's name")
    parser.add_argument("--input", help="audio file to strip vocals from")
    parser.add_argument("--output-dir", help="directory to write the instrumental into")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cuda", "rocm", "cpu"])
    parser.add_argument("--backend", default=BACKEND_AUTO, choices=list(BACKENDS),
                        help="which separation code to run the checkpoint with")
    parser.add_argument("--msst-path", default="",
                        help="directory of a Music-Source-Separation-Training "
                             "checkout, for the msst backend")
    parser.add_argument("--model-type", default="",
                        help="MSST architecture name, when the config does not "
                             "make it obvious")
    args = parser.parse_args(argv)

    if args.probe:
        return probe(args.msst_path)

    missing = [n for n in ("model", "input", "output_dir") if not getattr(args, n)]
    if missing:
        say("ERROR", "Missing required arguments: " + ", ".join(missing))
        return 2

    return separate(args)


if __name__ == "__main__":
    sys.exit(main())
