"""Resolve export watermark by subscription plan."""
from __future__ import annotations

import os
import struct
import zlib
from typing import Any, Dict, Optional

from app.core.config import settings
from app.models.client import Client, SubscriptionPlan

# Tiny 5x7 uppercase glyphs for "VIDEOGEN" fallback (no Pillow required)
_GLYPHS = {
    "V": ["10001", "10001", "10001", "10001", "01010", "01010", "00100"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def _assets_root() -> str:
    root = settings.ASSETS_DIR
    if os.path.isabs(root) and os.path.isdir(root):
        return root
    candidates = [
        os.path.join(settings.MEDIA_ROOT, "assets"),
        os.path.abspath(root),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "media", "assets")),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    path = candidates[-1]
    os.makedirs(path, exist_ok=True)
    return path


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_text_watermark_png(path: str, text: str = "VIDEOGEN") -> None:
    """RGBA PNG: dark pill + white bitmap text (stdlib only)."""
    scale = 6
    pad_x, pad_y = 28, 22
    gap = 2
    chars = [c if c in _GLYPHS else " " for c in text.upper()]
    glyph_w = 5 * scale
    glyph_h = 7 * scale
    content_w = len(chars) * glyph_w + max(0, len(chars) - 1) * gap * scale
    w = content_w + pad_x * 2
    h = glyph_h + pad_y * 2
    # pixel buffer
    px = [[(0, 0, 0, 0) for _ in range(w)] for _ in range(h)]

    def setp(x: int, y: int, rgba):
        if 0 <= x < w and 0 <= y < h:
            px[y][x] = rgba

    # rounded-ish dark background
    radius = 18
    for y in range(h):
        for x in range(w):
            dx = min(x, w - 1 - x)
            dy = min(y, h - 1 - y)
            corner = dx < radius and dy < radius and (radius - dx) ** 2 + (radius - dy) ** 2 > radius ** 2
            if not corner:
                setp(x, y, (11, 18, 32, 175))

    # glyphs
    ox = pad_x
    oy = pad_y
    for ch in chars:
        rows = _GLYPHS[ch]
        for gy, row in enumerate(rows):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        setp(ox + gx * scale + sx, oy + gy * scale + sy, (255, 255, 255, 240))
        ox += glyph_w + gap * scale

    rows_out = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            row.extend(px[y][x])
        rows_out.append(bytes(row))
    raw = b"".join(rows_out)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)


def platform_watermark_path() -> str:
    """Generate/cache a LoudCut PNG mark under assets/branding."""
    out_dir = os.path.join(_assets_root(), "branding")
    os.makedirs(out_dir, exist_ok=True)
    # Renamed from videogen_watermark.png (LoudCut rebrand, 2026-08-13) so a
    # stale pre-rebrand PNG already cached on disk gets regenerated instead
    # of silently continuing to serve the old "VideoGen" mark forever.
    path = os.path.join(out_dir, "loudcut_watermark.png")
    if os.path.isfile(path) and os.path.getsize(path) > 400:
        return path

    try:
        from PIL import Image, ImageDraw, ImageFont

        w, h = 640, 160
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((8, 24, w - 8, h - 24), radius=28, fill=(11, 18, 32, 170))
        try:
            font = ImageFont.truetype("arial.ttf", 54)
        except OSError:
            font = ImageFont.load_default()
        text = "LoudCut"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((w - tw) / 2, (h - th) / 2 - 4), text, fill=(255, 255, 255, 235), font=font)
        img.save(path, "PNG")
        return path
    except Exception:
        _write_text_watermark_png(path, "LOUDCUT")
        return path


def resolve_export_watermark(client: Client) -> Optional[Dict[str, Any]]:
    """
    Basic: always watermark (brand logo if set, else LoudCut mark).
    Standard: brand if set, else light LoudCut mark.
    Premium: brand only; clean export if no logo uploaded.
    """
    branding = getattr(client, "branding", None)
    custom = None
    if branding and branding.watermark_path and os.path.isfile(branding.watermark_path):
        custom = {
            "path": branding.watermark_path,
            "position": branding.watermark_position or "bottom_right",
            "opacity": int(branding.watermark_opacity or 80),
            "scale": int(branding.watermark_scale or 15),
            "kind": "brand",
        }

    plan = client.subscription_plan
    if plan == SubscriptionPlan.PREMIUM:
        return custom

    if plan == SubscriptionPlan.STANDARD:
        if custom:
            return custom
        return {
            "path": platform_watermark_path(),
            "position": "bottom_right",
            "opacity": 42,
            "scale": 11,
            "kind": "platform",
        }

    if custom:
        return custom
    return {
        "path": platform_watermark_path(),
        "position": "bottom_right",
        "opacity": 68,
        "scale": 13,
        "kind": "platform",
    }


def plan_watermark_policy(plan: SubscriptionPlan | str) -> Dict[str, str]:
    key = plan.value if isinstance(plan, SubscriptionPlan) else str(plan or "basic").lower()
    policies = {
        "basic": {
            "label": "Basic",
            "watermark": "Всегда LoudCut или ваш логотип",
            "clean_export": "нет",
        },
        "standard": {
            "label": "Standard",
            "watermark": "Ваш логотип или лёгкий LoudCut",
            "clean_export": "нет",
        },
        "premium": {
            "label": "Premium",
            "watermark": "Только ваш логотип (или чистое видео)",
            "clean_export": "да",
        },
    }
    return policies.get(key, policies["basic"])
