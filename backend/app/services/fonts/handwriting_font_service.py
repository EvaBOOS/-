"""Build a private TTF font from a photo of the filled-in handwriting
template (handwriting_template.py). No potrace/fontforge — glyph outlines
come from cv2 contour tracing, the font is assembled with fontTools.

Deliberately simple: straight-line (non-bezier) contours are perfectly
valid TrueType outlines and read as organic/handwritten rather than
imperfect, so no curve-fitting step is needed.
"""
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from app.services.fonts.handwriting_template import (
    CANVAS_H,
    CANVAS_W,
    FIDUCIAL_CENTERS,
    FIDUCIAL_SIZE,
    TEMPLATE_CHARACTERS,
    cell_bounds,
)

UPM = 1000
_CELL_INSET_FRAC = 0.12  # skip the printed cell border when tracing ink
_MIN_INK_FRACTION = 0.004  # below this, treat the cell as "not filled in"
_MAX_INK_FRACTION = 0.35  # above this, likely a bad crop/bleed, not real ink
_MIN_CONTOUR_AREA_FRAC = 0.0015  # strip JPEG/paper-texture noise specks


class HandwritingFontError(ValueError):
    """Raised when the photo can't be parsed into a usable font — the
    message is meant to be shown to the user as-is."""


def _find_fiducials(gray: np.ndarray) -> Dict[str, Tuple[float, float]]:
    h, w = gray.shape
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    min_area = 0.0004 * w * h
    max_area = 0.03 * w * h
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if ch == 0:
            continue
        aspect = cw / ch
        if not (0.6 <= aspect <= 1.6):
            continue
        cx, cy = x + cw / 2.0, y + ch / 2.0
        candidates.append((area, cx, cy))

    quadrants = {"top_left": None, "top_right": None, "bottom_left": None, "bottom_right": None}
    for area, cx, cy in candidates:
        key = ("top" if cy < h / 2 else "bottom") + "_" + ("left" if cx < w / 2 else "right")
        best = quadrants[key]
        if best is None or area > best[0]:
            quadrants[key] = (area, cx, cy)

    missing = [k for k, v in quadrants.items() if v is None]
    if missing:
        raise HandwritingFontError(
            "Не удалось распознать метки по углам шаблона — переснимите при "
            "хорошем освещении, весь лист (включая углы) должен быть в кадре."
        )

    return {k: (v[1], v[2]) for k, v in quadrants.items()}


def _warp_to_canonical(gray: np.ndarray) -> np.ndarray:
    fiducials = _find_fiducials(gray)
    src = np.float32([
        fiducials["top_left"], fiducials["top_right"],
        fiducials["bottom_left"], fiducials["bottom_right"],
    ])
    dst = np.float32([
        FIDUCIAL_CENTERS["top_left"], FIDUCIAL_CENTERS["top_right"],
        FIDUCIAL_CENTERS["bottom_left"], FIDUCIAL_CENTERS["bottom_right"],
    ])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, matrix, (CANVAS_W, CANVAS_H))


def _cell_glyph(canonical: np.ndarray, index: int) -> Optional[object]:
    x0, y0, x1, y1 = cell_bounds(index)
    dx, dy = int((x1 - x0) * _CELL_INSET_FRAC), int((y1 - y0) * _CELL_INSET_FRAC)
    cell = canonical[y0 + dy: y1 - dy, x0 + dx: x1 - dx]
    if cell.size == 0:
        return None

    _, mask = cv2.threshold(cell, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink_fraction = float(np.count_nonzero(mask)) / mask.size
    if ink_fraction < _MIN_INK_FRACTION:
        return None
    if ink_fraction > _MAX_INK_FRACTION:
        # Almost certainly bleed from a neighboring cell or a misaligned
        # crop, not real dense ink — skip rather than emit a near-solid
        # block glyph (confirmed against a real handwriting photo: a
        # slightly-misaligned cell produced exactly this failure mode).
        return None

    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    pen = TTGlyphPen(None)
    h, w = mask.shape
    scale = UPM / max(w, h) * 0.82  # leave a little breathing room inside the em box
    min_area = _MIN_CONTOUR_AREA_FRAC * w * h
    wrote_any = False
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue  # noise speck (JPEG artifact, paper texture)
        approx = cv2.approxPolyDP(cnt, 1.5, True)
        pts = approx.reshape(-1, 2)
        if len(pts) < 3:
            continue
        font_pts = [(float(px) * scale, float(h - py) * scale) for px, py in pts]
        pen.moveTo(font_pts[0])
        for p in font_pts[1:]:
            pen.lineTo(p)
        pen.closePath()
        wrote_any = True

    return pen.glyph() if wrote_any else None


def build_font_from_photo(image_bytes: bytes, family_name: str = "Мой почерк") -> bytes:
    """Returns TTF bytes, or raises HandwritingFontError with a
    user-facing message if the photo can't be parsed."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise HandwritingFontError("Не удалось прочитать файл как изображение.")

    canonical = _warp_to_canonical(img)

    glyph_order: List[str] = [".notdef", "space"]
    cmap: Dict[int, str] = {ord(" "): "space"}
    glyphs = {}
    metrics = {}

    notdef_pen = TTGlyphPen(None)
    glyphs[".notdef"] = notdef_pen.glyph()
    metrics[".notdef"] = (500, 0)
    glyphs["space"] = TTGlyphPen(None).glyph()
    metrics["space"] = (int(UPM * 0.45), 0)

    filled_count = 0
    for i, ch in enumerate(TEMPLATE_CHARACTERS):
        glyph = _cell_glyph(canonical, i)
        if glyph is None:
            continue
        glyph_name = f"g{i}_{ord(ch)}"
        glyph_order.append(glyph_name)
        glyphs[glyph_name] = glyph
        metrics[glyph_name] = (int(UPM * 0.72), 0)
        cmap[ord(ch)] = glyph_name
        lower = ch.lower()
        if lower != ch:
            cmap[ord(lower)] = glyph_name
        filled_count += 1

    if filled_count < 5:
        raise HandwritingFontError(
            "Распозналось слишком мало заполненных букв — проверьте, что клетки "
            "заполнены контрастным маркером/ручкой, и переснимите фото."
        )

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=UPM, descent=-int(UPM * 0.2))
    fb.setupNameTable({"familyName": family_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    import io
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()
