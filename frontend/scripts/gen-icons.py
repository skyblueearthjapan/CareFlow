"""Generate らく助 PWA icons (192/512 + maskable-512) + app-router favicons.

らく助リブランディング (2026-07-10): 旧テキスト "C" アイコンを廃止し、
らく助の丸みスクエアタイル (public/brand/rakusuke-icon-round.png) から合成する。

- icon-192/512: 淡ピンク地 (#FFEEF2) にタイルを 92% で中央配置
- maskable-512: アイデンティティピンク (#F8B4C6) 地にタイルを 62% で中央配置
  (Android adaptive icon の 80% セーフゾーン内に収める)
- app/icon.png (64) / app/apple-icon.png (180): Next.js app router が
  favicon / apple-touch-icon として自動配信する
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

FRONTEND = Path(__file__).resolve().parent.parent
SRC = FRONTEND / "public" / "brand" / "rakusuke-icon-round.png"
OUT_DIR = FRONTEND / "public" / "icons"
APP_DIR = FRONTEND / "app"

PALE_PINK = (255, 238, 242)  # #FFEEF2
IDENTITY_PINK = (248, 180, 198)  # #F8B4C6


def compose(size: int, bg: tuple[int, int, int], tile_pct: int) -> Image.Image:
    """Center the rakusuke tile on a solid square canvas."""
    tile = Image.open(SRC).convert("RGBA")
    target = size * tile_pct // 100
    scale = min(target / tile.width, target / tile.height)
    resized = tile.resize(
        (max(1, round(tile.width * scale)), max(1, round(tile.height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (*bg, 255))
    canvas.paste(
        resized,
        ((size - resized.width) // 2, (size - resized.height) // 2),
        resized,
    )
    return canvas.convert("RGB")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    compose(192, PALE_PINK, 92).save(OUT_DIR / "icon-192.png", "PNG")
    compose(512, PALE_PINK, 92).save(OUT_DIR / "icon-512.png", "PNG")
    compose(512, IDENTITY_PINK, 62).save(OUT_DIR / "maskable-512.png", "PNG")
    compose(64, PALE_PINK, 96).save(APP_DIR / "icon.png", "PNG")
    compose(180, PALE_PINK, 92).save(APP_DIR / "apple-icon.png", "PNG")
    for path in (
        OUT_DIR / "icon-192.png",
        OUT_DIR / "icon-512.png",
        OUT_DIR / "maskable-512.png",
        APP_DIR / "icon.png",
        APP_DIR / "apple-icon.png",
    ):
        print(f"wrote {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()
