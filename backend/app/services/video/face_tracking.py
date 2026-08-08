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
    y = max(_SAFE_MARGIN_TOP, min(y, video_height - _SAFE_MARGIN_BOTTOM))

    return (x, y)
