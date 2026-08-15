"""Cheap face-position tracking for kinetic subtitles.

Uses OpenCV's bundled Haar cascade rather than a DNN/mediapipe model — lower
accuracy, but zero extra downloads or system dependencies beyond
opencv-python-headless itself. Good enough for "subtitles noticeably follow
the speaker", not meant to be frame-perfect.
"""
import os
import shutil
import tempfile
from typing import Dict, List, Optional

import cv2

# Keep the subtitle anchor comfortably inside the frame.
_SAFE_MARGIN_X = 80
_SAFE_MARGIN_TOP = 120
_SAFE_MARGIN_BOTTOM = 60


def _ascii_safe_cascade_path() -> str:
    """OpenCV's C++ file loader (cv::CascadeClassifier::load) can't open
    paths containing non-ASCII characters on Windows (fails with an opaque
    "!empty()" assertion, no mention of the real cause) — a real risk any
    time the app is installed under a non-ASCII path. Copy the bundled
    cascade into the OS temp dir (always ASCII in practice) once, and load
    from there instead of cv2.data.haarcascades directly."""
    src = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if src.isascii():
        return src

    dst = os.path.join(tempfile.gettempdir(), "videogen_haarcascade_frontalface_default.xml")
    if not os.path.isfile(dst):
        shutil.copyfile(src, dst)
    return dst


class FaceTrackingService:
    def __init__(self):
        cascade_path = _ascii_safe_cascade_path()
        self._cascade = cv2.CascadeClassifier(cascade_path)

    def track_face_centers(self, video_path: str, sample_fps: float = 4.0) -> List[Dict]:
        """Sample ~sample_fps frames/sec, detect the largest face per sampled
        frame. Returns a time-ordered list of {"t", "x", "y", "w", "h"}
        (pixel coords in the video's own frame). Frames with no detected
        face are simply skipped — the caller interpolates across gaps."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_interval = max(1, round(fps / max(sample_fps, 0.1)))

        points: List[Dict] = []
        idx = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % frame_interval == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = self._cascade.detectMultiScale(
                        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
                    )
                    if len(faces):
                        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                        points.append({
                            "t": idx / fps,
                            "x": float(x + w / 2.0),
                            "y": float(y + h / 2.0),
                            "w": float(w),
                            "h": float(h),
                        })
                idx += 1
        finally:
            cap.release()

        return points


def smooth_face_centers(
    points: List[Dict],
    duration: float,
    video_w: int,
    video_h: int,
    alpha: float = 0.35,
    max_speed_frac: float = 0.08,
) -> List[Dict]:
    """EMA + pan-speed cap (~8% of frame width per second)."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p["t"])
    ema: List[Dict] = []
    px = py = pw = ph = None
    for p in pts:
        if px is None:
            px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        else:
            px = alpha * p["x"] + (1.0 - alpha) * px
            py = alpha * p["y"] + (1.0 - alpha) * py
            pw = alpha * p["w"] + (1.0 - alpha) * pw
            ph = alpha * p["h"] + (1.0 - alpha) * ph
        ema.append({"t": p["t"], "x": px, "y": py, "w": pw, "h": ph})

    max_px_s = max(8.0, max_speed_frac * float(video_w or 1080))
    out = [ema[0]]
    for p in ema[1:]:
        prev = out[-1]
        dt = max(p["t"] - prev["t"], 1e-3)
        dx = p["x"] - prev["x"]
        dy = p["y"] - prev["y"]
        dist = (dx * dx + dy * dy) ** 0.5
        cap = max_px_s * dt
        if dist > cap and dist > 0:
            s = cap / dist
            p = {
                **p,
                "x": prev["x"] + dx * s,
                "y": prev["y"] + dy * s,
            }
        out.append(p)
    if duration and out[-1]["t"] < duration:
        last = dict(out[-1])
        last["t"] = duration
        out.append(last)
    return out


def crop_holds_from_track(
    smoothed: List[Dict],
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    duration: float,
    hysteresis: float = 0.15,
) -> List[Dict]:
    """Stable crop windows. Camera only jumps when the face leaves the dead zone.

    Coordinates are in the *scaled* cover-buffer (before the 1080x1920 crop).
    """
    if src_w <= 0 or src_h <= 0 or out_w <= 0 or out_h <= 0:
        return []
    sf = max(out_w / src_w, out_h / src_h)
    sw, sh = src_w * sf, src_h * sf
    max_x = max(0.0, sw - out_w)
    max_y = max(0.0, sh - out_h)

    def crop_at(p: Dict) -> tuple:
        eye_x = p["x"] * sf
        eye_y = (p["y"] - p.get("h", 0) * 0.15) * sf
        cx = max(0.0, min(eye_x - out_w / 2.0, max_x))
        cy = max(0.0, min(eye_y - out_h / 3.0, max_y))
        return cx, cy

    if not smoothed:
        return []

    hyst_x = out_w * hysteresis
    hyst_y = out_h * hysteresis
    cur_x, cur_y = crop_at(smoothed[0])
    t0 = 0.0
    holds: List[Dict] = []
    for p in smoothed:
        nx, ny = crop_at(p)
        face_x = p["x"] * sf - cur_x
        face_y = p["y"] * sf - cur_y
        escaped = (
            face_x < hyst_x
            or face_x > out_w - hyst_x
            or face_y < hyst_y
            or face_y > out_h - hyst_y
        )
        if escaped and (p["t"] - t0) >= 0.45:
            holds.append({"start": t0, "end": p["t"], "x": cur_x, "y": cur_y})
            t0 = p["t"]
            cur_x, cur_y = nx, ny
    end_t = max(duration or 0.0, smoothed[-1]["t"], t0 + 0.12)
    holds.append({"start": t0, "end": end_t, "x": cur_x, "y": cur_y})

    # Collapse micro-holds — a calm operator, not a music-video whip-pan.
    merged: List[Dict] = []
    for h in holds:
        if merged and (h["end"] - h["start"]) < 0.5:
            merged[-1]["end"] = h["end"]
            continue
        if merged and abs(h["x"] - merged[-1]["x"]) < 12 and abs(h["y"] - merged[-1]["y"]) < 12:
            merged[-1]["end"] = h["end"]
            continue
        merged.append(dict(h))
    if len(merged) > 12:
        # Too jumpy — one median crop for the whole clip.
        xs = [h["x"] for h in merged]
        ys = [h["y"] for h in merged]
        xs.sort()
        ys.sort()
        mid = len(xs) // 2
        return [{"start": 0.0, "end": end_t, "x": xs[mid], "y": ys[mid]}]
    return merged


def resolve_position(
    track_points: List[Dict],
    t: float,
    video_width: int,
    video_height: int,
) -> Optional[tuple]:
    """Interpolate the tracked face position at time `t` and convert it into
    a subtitle anchor point (below the face, offset scaled to face size).
    Returns None if there's nothing to track (caller keeps the default
    fixed-position behavior)."""
    if not track_points:
        return None

    pts = sorted(track_points, key=lambda p: p["t"])

    if t <= pts[0]["t"]:
        p = pts[0]
    elif t >= pts[-1]["t"]:
        p = pts[-1]
    else:
        p = pts[-1]
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if a["t"] <= t <= b["t"]:
                span = max(b["t"] - a["t"], 1e-6)
                frac = (t - a["t"]) / span
                p = {
                    "x": a["x"] + (b["x"] - a["x"]) * frac,
                    "y": a["y"] + (b["y"] - a["y"]) * frac,
                    "w": a["w"] + (b["w"] - a["w"]) * frac,
                    "h": a["h"] + (b["h"] - a["h"]) * frac,
                }
                break

    x = p["x"]
    y = p["y"] + p["h"] * 0.9  # anchor just below the chin

    x = max(_SAFE_MARGIN_X, min(x, video_width - _SAFE_MARGIN_X))
    y_lo = video_height * 0.25
    y_hi = video_height * 0.75
    y = max(y_lo, min(y, y_hi))
    y = max(_SAFE_MARGIN_TOP, min(y, video_height - _SAFE_MARGIN_BOTTOM))

    return (x, y)
