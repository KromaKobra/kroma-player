"""Extract a cover's dominant colour and make readable theme accents."""
from __future__ import annotations

import colorsys
import io

from PIL import Image


def luminance(rgb: tuple[float, float, float]) -> float:
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
              for c in rgb]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _accent(hue: float, saturation: float, target: float) -> str:
    # Keep the artwork's hue; solve for lightness at a readable luminance.
    low, high = 0.0, 1.0
    for _ in range(24):
        lightness = (low + high) / 2
        rgb = colorsys.hls_to_rgb(hue, lightness, saturation)
        if luminance(rgb) < target:
            low = lightness
        else:
            high = lightness
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def extract_palette(data: bytes) -> dict[str, str]:
    """Quantize a small image, preferring sizeable colourful regions.

    Transparent pixels are excluded. Neutral covers retain a neutral accent;
    empty or unreadable art returns no palette so the GUI can use its preset.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.thumbnail((64, 64))
            pixels = [rgb[:3] for rgb in source.convert("RGBA").get_flattened_data()
                      if rgb[3] >= 128]
        if not pixels:
            return {}
        sample = Image.new("RGB", (len(pixels), 1))
        sample.putdata(pixels)
        quantized = sample.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette()
        candidates = []
        for count, index in quantized.getcolors():
            rgb = tuple(c / 255 for c in palette[index * 3:index * 3 + 3])
            hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
            # Black/white borders should not bury the cover's colourful areas.
            score = count * (0.15 + saturation) * (0.25 if lightness < .08 or lightness > .92 else 1)
            candidates.append((score, hue, saturation))
        _, hue, saturation = max(candidates)
        return {
            "a": _accent(hue, saturation, .26),
            "b": _accent(hue, saturation, .40),
            "la": _accent(hue, saturation, .10),
            "lb": _accent(hue, saturation, .135),
        }
    except (OSError, ValueError):
        return {}
