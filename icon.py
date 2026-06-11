"""Generates the ShatelMon tray icon: orange "SH" on a dark-blue solid triangle."""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

DARK_BLUE = (16, 33, 89, 255)      # solid dark navy
ORANGE = (255, 138, 0, 255)        # Shatel-ish orange


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "Arialbd.ttf", "seguisb.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(size: int = 64, alert: bool = False) -> Image.Image:
    """Return a PIL RGBA icon. When ``alert`` is True the triangle gets a red tint
    so a low-traffic / expiry warning is visible at a glance in the tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    apex = (size / 2, pad)
    bottom_left = (pad, size - pad)
    bottom_right = (size - pad, size - pad)
    triangle_color = (176, 32, 32, 255) if alert else DARK_BLUE
    d.polygon([apex, bottom_left, bottom_right], fill=triangle_color)

    # "SH" sits in the wide lower half of the triangle
    text = "SH"
    font = _load_font(int(size * 0.34))
    cx, cy = size / 2, size * 0.66
    try:
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pos = (cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1])
    except Exception:
        tw, th = d.textsize(text, font=font)
        pos = (cx - tw / 2, cy - th / 2)
    d.text(pos, text, font=font, fill=ORANGE)
    return img


def ensure_ico(path: str) -> str:
    """Write a multi-size .ico (used by Windows toast notifications) and return its path."""
    if not os.path.exists(path):
        base = make_icon(256)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    make_icon(64).save("icon_preview.png")
    make_icon(64, alert=True).save("icon_preview_alert.png")
    ensure_ico("ShatelMon.ico")
    print("wrote icon_preview.png, icon_preview_alert.png, ShatelMon.ico")
