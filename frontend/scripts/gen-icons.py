"""Generate CareFlow PWA icons (192/512 + maskable-512).

Maskable icon centers the "C" glyph within the 80% safe zone so Android
circular masks don't clip the logo (Critic W2-E #2).
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TEAL = (13, 148, 136)  # #0D9488
WHITE = (255, 255, 255)
OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "arial.ttf",
        "Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def gen_icon(size: int, mask_safe: bool = False) -> Image.Image:
    """Render solid-teal square with centered white "C".

    When ``mask_safe`` is True the glyph is shrunk to 40% of the canvas so it
    fits inside the 80% safe zone required by Android adaptive icons.
    """
    img = Image.new("RGB", (size, size), TEAL)
    draw = ImageDraw.Draw(img)
    glyph_pct = 40 if mask_safe else 60
    font = _load_font(size * glyph_pct // 100)
    # anchor='mm' centers the glyph at (cx, cy) regardless of bbox offsets.
    cx, cy = size // 2, size // 2
    try:
        draw.text((cx, cy), "C", fill=WHITE, font=font, anchor="mm")
    except (TypeError, ValueError):
        # Fallback for bitmap fonts that don't support anchor.
        bbox = draw.textbbox((0, 0), "C", font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (size - w) // 2 - bbox[0]
        y = (size - h) // 2 - bbox[1]
        draw.text((x, y), "C", fill=WHITE, font=font)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_icon(192).save(OUT_DIR / "icon-192.png", "PNG")
    gen_icon(512).save(OUT_DIR / "icon-512.png", "PNG")
    gen_icon(512, mask_safe=True).save(OUT_DIR / "maskable-512.png", "PNG")
    for name in ("icon-192.png", "icon-512.png", "maskable-512.png"):
        path = OUT_DIR / name
        print(f"wrote {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()
