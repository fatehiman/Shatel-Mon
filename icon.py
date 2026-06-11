"""Generates the ShatelMon tray icon: orange "SH" on a dark-blue solid triangle.

- ``alert=True``     -> red triangle (a low-traffic / expiry warning is active).
- ``show_text=False`` -> draw the triangle only (used for the "processing" blink).
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

DARK_BLUE = (16, 33, 89, 255)      # solid dark navy
ALERT_RED = (200, 40, 40, 255)
ORANGE = (255, 140, 0, 255)        # Shatel-ish orange


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "Arialbd.ttf", "seguisb.ttf", "calibrib.ttf",
                 "tahomabd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font_size(draw: ImageDraw.ImageDraw, text: str,
                   max_w: float, max_h: float) -> int:
    """Largest font size whose rendered ``text`` fits within max_w x max_h."""
    best = 6
    for fs in range(6, 220):
        font = _load_font(fs)
        b = draw.textbbox((0, 0), text, font=font)
        if (b[2] - b[0]) <= max_w and (b[3] - b[1]) <= max_h:
            best = fs
        else:
            break
    return best


def make_icon(size: int = 64, alert: bool = False, show_text: bool = True) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Bigger triangle: small padding so it nearly fills the canvas.
    pad = max(1, round(size * 0.04))
    apex = (size / 2, pad)
    bottom_left = (pad, size - pad)
    bottom_right = (size - pad, size - pad)
    d.polygon([apex, bottom_left, bottom_right], fill=ALERT_RED if alert else DARK_BLUE)

    if show_text:
        text = "SH"
        # Place the text in the wide lower band of the triangle and size it
        # to (almost) fill the triangle width available at that height.
        cy = size * 0.64
        frac = (cy - pad) / ((size - pad) - pad)            # 0..1 down the triangle
        avail_w = frac * (size - 2 * pad) * 0.90
        avail_h = size * 0.50
        font = _load_font(_fit_font_size(d, text, avail_w, avail_h))
        b = d.textbbox((0, 0), text, font=font)
        tw, th = b[2] - b[0], b[3] - b[1]
        pos = (size / 2 - tw / 2 - b[0], cy - th / 2 - b[1])
        d.text(pos, text, font=font, fill=ORANGE)
    return img


def ensure_ico(path: str) -> str:
    """Write a multi-size .ico (used as the EXE icon) and return its path."""
    if not os.path.exists(path):
        base = make_icon(256)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    make_icon(64).save("icon_preview.png")
    make_icon(64, alert=True).save("icon_preview_alert.png")
    make_icon(64, show_text=False).save("icon_preview_blink.png")
    make_icon(256).save("ShatelMon.png")
    if os.path.exists("ShatelMon.ico"):
        os.remove("ShatelMon.ico")
    ensure_ico("ShatelMon.ico")
    print("wrote previews + ShatelMon.png + ShatelMon.ico")
