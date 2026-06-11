"""Tray icon for ShatelMon — the Shatel logo (fetched favicon: navy "A" with an
orange circle), loaded from ``shatel_logo.png``.

- ``alert=True``       -> the navy mark is tinted red (a warning is active).
- ``show_circle=False`` -> the orange circle is hidden (used for the "processing"
  blink: the circle is shown/hidden repeatedly while a fetch runs).
"""

from __future__ import annotations

import os
import sys
from PIL import Image, ImageDraw

DARK_BLUE = (22, 43, 94, 255)
ORANGE = (242, 99, 34, 255)
ALERT_RED = (200, 40, 40)

_LOGO_FILE = "shatel_logo.png"
_base_cache: Image.Image | None = None
_variant_cache: dict[tuple[bool, bool], Image.Image] = {}


def _logo_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, _LOGO_FILE)


def _fallback_logo(side: int = 256) -> Image.Image:
    """Drawn approximation of the Shatel mark, used only if shatel_logo.png is missing."""
    img = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    pad = round(side * 0.08)
    d.polygon([(side / 2, pad), (pad, side - pad), (side - pad, side - pad)], fill=DARK_BLUE)
    # white inner cut-out
    s = side
    d.polygon([(s / 2, s * 0.34), (s * 0.30, s * 0.80), (s * 0.70, s * 0.80)], fill=(255, 255, 255, 255))
    r = side * 0.11
    cx, cy = s / 2, s * 0.70
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ORANGE)
    return img


def _load_base() -> Image.Image:
    global _base_cache
    if _base_cache is None:
        try:
            _base_cache = Image.open(_logo_path()).convert("RGBA")
        except Exception:  # noqa: BLE001
            _base_cache = _fallback_logo()
    return _base_cache


def _is_warm(r, g, b):
    """Orange circle (and its anti-aliased edges) — warmer than it is blue."""
    return r > 140 and r >= g and r > b + 25


def _is_white(r, g, b):
    return min(r, g, b) >= 236


def _variant(alert: bool, show_circle: bool) -> Image.Image:
    key = (alert, show_circle)
    cached = _variant_cache.get(key)
    if cached is not None:
        return cached

    base = _load_base()
    out = []
    for (r, g, b, a) in base.getdata():
        if a == 0:
            out.append((r, g, b, a))
            continue
        if _is_warm(r, g, b):                       # the orange circle
            if not show_circle:
                out.append((255, 255, 255, a))      # hide it into the white cut-out
            else:
                out.append((r, g, b, a))
            continue
        if alert and not _is_white(r, g, b):        # navy mark -> red, keeping edges smooth
            cov = max(0.0, min(1.0, (255 - r) / (255 - DARK_BLUE[0])))
            out.append((round(255 * (1 - cov) + ALERT_RED[0] * cov),
                        round(255 * (1 - cov) + ALERT_RED[1] * cov),
                        round(255 * (1 - cov) + ALERT_RED[2] * cov), a))
            continue
        out.append((r, g, b, a))

    img = Image.new("RGBA", base.size)
    img.putdata(out)
    _variant_cache[key] = img
    return img


def make_icon(size: int = 64, alert: bool = False, show_circle: bool = True) -> Image.Image:
    return _variant(alert, show_circle).resize((size, size), Image.LANCZOS)


def ensure_ico(path: str) -> str:
    if not os.path.exists(path):
        make_icon(256).save(path, format="ICO",
                            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    for s in (16, 24, 32, 64):
        make_icon(s).save(f"icon_preview_{s}.png")
    make_icon(64, alert=True).save("icon_preview_alert.png")
    make_icon(64, show_circle=False).save("icon_preview_nocircle.png")
    make_icon(256).save("ShatelMon.png")
    if os.path.exists("ShatelMon.ico"):
        os.remove("ShatelMon.ico")
    ensure_ico("ShatelMon.ico")
    print("wrote previews + ShatelMon.png + ShatelMon.ico")
