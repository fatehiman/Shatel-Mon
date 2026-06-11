"""Generates the ShatelMon tray icon: orange "SH" on a dark-blue solid triangle.

- ``alert=True``      -> red triangle (a low-traffic / expiry warning is active).
- ``show_text=False`` -> draw the triangle only (used for the "processing" blink).

The "SH" is sized to be as large as possible while still fitting inside the
triangle, so it stays readable when Windows scales the icon down to ~16 px.
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


def make_icon(size: int = 64, alert: bool = False, show_text: bool = True) -> Image.Image:
    # Render at high resolution then downscale, so the text edges stay smooth.
    SS = 8 if size <= 64 else 4
    W = size * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Big triangle: tiny padding so it nearly fills the canvas.
    pad = max(1, round(W * 0.02))
    apex_y = pad
    base_y = W - pad
    cx = W / 2
    half_base = (W - 2 * pad) / 2.0
    height = base_y - apex_y
    d.polygon([(cx, apex_y), (pad, base_y), (W - pad, base_y)],
              fill=ALERT_RED if alert else DARK_BLUE)

    if show_text:
        text = "SH"
        gap = max(1, round(W * 0.035))     # gap between the text and the base
        margin = 0.95                      # use up to 95% of the available width
        best = None
        for fs in range(8, W):
            font = _load_font(fs)
            b = d.textbbox((0, 0), text, font=font)
            tw, th = b[2] - b[0], b[3] - b[1]
            cy = base_y - gap - th / 2.0           # sit the text low, near the base
            top = cy - th / 2.0
            if top <= apex_y or th > height * 0.82:
                break
            # measure available width a little below the very top (negligible overhang)
            y_eval = top + th * 0.18
            half_w = half_base * (y_eval - apex_y) / height
            if tw <= 2 * half_w * margin:
                best = (fs, cy, b)
            else:
                break                              # wider fonts won't fit either
        if best:
            fs, cy, b = best
            font = _load_font(fs)
            tw, th = b[2] - b[0], b[3] - b[1]
            pos = (cx - tw / 2.0 - b[0], cy - th / 2.0 - b[1])
            d.text(pos, text, font=font, fill=ORANGE)

    return img.resize((size, size), Image.LANCZOS)


def ensure_ico(path: str) -> str:
    """Write a multi-size .ico (used as the EXE icon) and return its path."""
    if not os.path.exists(path):
        base = make_icon(256)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    for s in (16, 24, 32, 64):
        make_icon(s).save(f"icon_preview_{s}.png")
    make_icon(64, alert=True).save("icon_preview_alert.png")
    make_icon(64, show_text=False).save("icon_preview_blink.png")
    make_icon(256).save("ShatelMon.png")
    if os.path.exists("ShatelMon.ico"):
        os.remove("ShatelMon.ico")
    ensure_ico("ShatelMon.ico")
    print("wrote previews (16/24/32/64) + alert + blink + ShatelMon.png + ShatelMon.ico")
