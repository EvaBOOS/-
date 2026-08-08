"""Pseudo-3D ("extruded") hook-title rendering — a stack of offset text
layers darkening toward the back plus a crisp front face, drawn once as a
transparent PNG and composited onto the video via
FFmpegService.overlay_timed_image. No real 3D geometry, no new rendering
engine — just Pillow, reusing the same overlay technique as
FFmpegService.overlay_stickers.
"""
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _lerp(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


async def _resolve_font_path(font_path: Optional[str]) -> str:
    if font_path:
        import os
        if os.path.isfile(font_path):
            return font_path
    from app.services.assets.library_service import AssetLibraryService
    _, _, font_file = await AssetLibraryService().ensure_font_file("perun-bold")
    return font_file


async def render_3d_text_image(
    text: str,
    font_path: Optional[str] = None,
    font_size: int = 90,
    depth: int = 10,
    base_color: Tuple[int, int, int] = (255, 214, 0),
    shadow_color: Tuple[int, int, int] = (40, 30, 0),
    max_width: Optional[int] = None,
) -> bytes:
    """Returns a tightly-cropped RGBA PNG with the extruded-text effect.

    `max_width`, when given (pass the target video's width), shrinks
    `font_size` as needed so the rendered text — plus extrusion depth —
    fits within it, so the overlay never runs off narrower frames
    (font_size that looks right at 1080px can easily overflow a 720px
    vertical phone recording otherwise)."""
    resolved_path = await _resolve_font_path(font_path)

    depth = max(2, min(int(depth), 30))
    pad = depth + 12

    def _measure(size: int):
        f = ImageFont.truetype(resolved_path, size)
        probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=f, stroke_width=3)
        return f, bbox

    font, bbox = _measure(font_size)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    if max_width and (text_w + pad * 2) > max_width:
        scale = (max_width - pad * 2) / max(text_w, 1)
        font_size = max(18, int(font_size * scale))
        depth = max(2, int(depth * scale))
        pad = depth + 12
        font, bbox = _measure(font_size)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    canvas_w = text_w + pad * 2
    canvas_h = text_h + pad * 2
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    origin = (pad - bbox[0], pad - bbox[1])

    # Back-to-front extrusion layers.
    for i in range(depth, 0, -1):
        t = i / depth
        color = _lerp(base_color, shadow_color, t * 0.85)
        draw.text((origin[0] + i, origin[1] + i), text, font=font, fill=(*color, 255))

    # Front face, crisp dark outline for readability on any background.
    draw.text(
        origin, text, font=font, fill=(*base_color, 255),
        stroke_width=3, stroke_fill=(20, 15, 0, 255),
    )

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
