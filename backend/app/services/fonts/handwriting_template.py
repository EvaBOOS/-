"""Layout for the printable handwriting-font template: which character goes
in which grid cell, and the shared geometry (fiducial corner markers, grid
bounds) that both the template generator and the photo-parsing side
(`handwriting_font_service.py`) must agree on."""
import io
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

# Uppercase only — the same drawn glyph gets mapped to both upper- and
# lowercase Unicode codepoints when the font is built (see
# handwriting_font_service.build_font_from_photo).
CYRILLIC_LETTERS = list("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
DIGITS = list("0123456789")
TEMPLATE_CHARACTERS: List[str] = CYRILLIC_LETTERS + DIGITS

CANVAS_W = 1240
CANVAS_H = 1600

FIDUCIAL_SIZE = 60
FIDUCIAL_MARGIN = 30

GRID_MARGIN_X = 90
GRID_TOP = 170
GRID_BOTTOM_MARGIN = 170

COLS = 6
ROWS = -(-len(TEMPLATE_CHARACTERS) // COLS)  # ceil

GRID_W = CANVAS_W - 2 * GRID_MARGIN_X
GRID_H = CANVAS_H - GRID_TOP - GRID_BOTTOM_MARGIN
CELL_W = GRID_W / COLS
CELL_H = GRID_H / ROWS

# The four fiducial-marker centers in the *canonical* (un-distorted)
# template's own coordinate space — the perspective-correction step in
# handwriting_font_service warps a photographed sheet so these same four
# points land here.
FIDUCIAL_CENTERS: Dict[str, Tuple[float, float]] = {
    "top_left": (FIDUCIAL_MARGIN + FIDUCIAL_SIZE / 2, FIDUCIAL_MARGIN + FIDUCIAL_SIZE / 2),
    "top_right": (CANVAS_W - FIDUCIAL_MARGIN - FIDUCIAL_SIZE / 2, FIDUCIAL_MARGIN + FIDUCIAL_SIZE / 2),
    "bottom_left": (FIDUCIAL_MARGIN + FIDUCIAL_SIZE / 2, CANVAS_H - FIDUCIAL_MARGIN - FIDUCIAL_SIZE / 2),
    "bottom_right": (CANVAS_W - FIDUCIAL_MARGIN - FIDUCIAL_SIZE / 2, CANVAS_H - FIDUCIAL_MARGIN - FIDUCIAL_SIZE / 2),
}


def cell_bounds(index: int) -> Tuple[int, int, int, int]:
    """Pixel bounds (x0, y0, x1, y1) of the `index`-th character's cell in
    the canonical template coordinate space."""
    row, col = divmod(index, COLS)
    x0 = GRID_MARGIN_X + col * CELL_W
    y0 = GRID_TOP + row * CELL_H
    return (int(x0), int(y0), int(x0 + CELL_W), int(y0 + CELL_H))


async def _load_cyrillic_font(size: int) -> ImageFont.FreeTypeFont:
    """The template's guide text is Cyrillic — PIL's built-in default font
    has no Cyrillic glyphs (renders as tofu), and no font file is
    guaranteed to exist on a fresh Docker image. Reuse the project's
    existing Google-Fonts cache/download mechanism (same one the subtitle
    pipeline already relies on) to get a real, Cyrillic-capable TTF."""
    from app.services.assets.library_service import AssetLibraryService

    # Not "inter"/"montserrat"/etc: those catalog entries are Latin-only
    # subsets fetched from the Google Fonts CDN (no Cyrillic glyphs at
    # all). "perun-bold" is a bundled "local" font with real Cyrillic
    # coverage — it's also this same library's own default fallback font.
    _, _, font_file = await AssetLibraryService().ensure_font_file("perun-bold")
    return ImageFont.truetype(font_file, size)


async def generate_template_image() -> bytes:
    """Printable PNG: a labeled grid (one cell per character to hand-write)
    plus four corner fiducial markers used to auto-align a photographed
    copy before slicing it back into cells."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
    draw = ImageDraw.Draw(img)

    title_font = await _load_cyrillic_font(28)
    body_font = await _load_cyrillic_font(20)
    label_font = await _load_cyrillic_font(22)

    draw.text((GRID_MARGIN_X, 45), "LoudCut — шаблон почерка", fill="black", font=title_font)
    draw.text(
        (GRID_MARGIN_X, 85),
        "Впишите каждую букву/цифру в свою клетку чёрным маркером или ручкой.",
        fill="black",
        font=body_font,
    )

    for i, ch in enumerate(TEMPLATE_CHARACTERS):
        x0, y0, x1, y1 = cell_bounds(i)
        draw.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=2)
        # Faint guide letter in the corner — doesn't interfere with the
        # binarization threshold used later since it's light gray.
        draw.text((x0 + 6, y0 + 4), ch, fill=(200, 200, 200), font=label_font)

    for cx, cy in FIDUCIAL_CENTERS.values():
        half = FIDUCIAL_SIZE / 2
        draw.rectangle(
            [cx - half, cy - half, cx + half, cy + half], fill=(0, 0, 0)
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
