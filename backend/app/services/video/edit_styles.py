"""Viral edit style presets (Stage: blogger styles)."""
from __future__ import annotations

from typing import Any, Dict, List

STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "dynamic": {
        "label": "Динамичный",
        "zoom_cap": 5,
        "min_zooms": 2,
        "zoom_scale_max": 1.18,
        "default_zoom_duration": 0.55,
        "force_hook": False,
        "music_vol": 0.14,
        "mood_default": "energetic",
        "stickers": True,
        "sticker_limit": 3,
        "hook_duration": 2.8,
        "broll_opacity": 0.9,
        "broll_max": 3,
        "font_size": 64,
        "hot_font_size": 72,
        "words_per_chunk": 4,
        "caption_pop": False,
        "outline": 3,
        "hint": "Fast retention edits, more zooms, punchy hook, energetic mood.",
    },
    "minimal": {
        "label": "Минималистичный",
        "zoom_cap": 2,
        "min_zooms": 0,
        "zoom_scale_max": 1.12,
        "default_zoom_duration": 0.45,
        "force_hook": False,
        "music_vol": 0.08,
        "mood_default": "calm",
        "stickers": False,
        "sticker_limit": 0,
        "hook_duration": 2.2,
        "broll_opacity": 0.75,
        "broll_max": 1,
        "font_size": 56,
        "hot_font_size": 64,
        "words_per_chunk": 5,
        "caption_pop": False,
        "outline": 2,
        "hint": "Few zooms, clean highlights, calm mood, subtle b-roll only.",
    },
    "ads": {
        "label": "Рекламный",
        "zoom_cap": 6,
        "min_zooms": 3,
        "zoom_scale_max": 1.22,
        "default_zoom_duration": 0.5,
        "force_hook": True,
        "music_vol": 0.17,
        "mood_default": "motivational",
        "stickers": True,
        "sticker_limit": 3,
        "hook_duration": 3.0,
        "broll_opacity": 0.9,
        "broll_max": 3,
        "font_size": 68,
        "hot_font_size": 82,
        "words_per_chunk": 3,
        "caption_pop": True,
        "outline": 4,
        "hint": "Always add a sales hook in first 3s, strong highlight words, motivational mood.",
    },
    "beast": {
        "label": "Как MrBeast",
        "zoom_cap": 8,
        "min_zooms": 5,
        "zoom_scale_max": 1.35,
        "default_zoom_duration": 0.42,
        "force_hook": True,
        "music_vol": 0.22,
        "mood_default": "energetic",
        "stickers": True,
        "sticker_limit": 5,
        "hook_duration": 3.6,
        "broll_opacity": 0.95,
        "broll_max": 4,
        "font_size": 78,
        "hot_font_size": 100,
        "words_per_chunk": 3,
        "caption_pop": True,
        "outline": 5,
        "hint": (
            "MrBeast-style: ultra punchy opening hook, frequent hard zooms (scale up to 1.35), "
            "many highlight words with pop captions, high energy, dramatic b-roll inserts."
        ),
    },
    "hormozi": {
        "label": "Как Hormozi",
        "zoom_cap": 7,
        "min_zooms": 4,
        "zoom_scale_max": 1.3,
        "default_zoom_duration": 0.38,
        "force_hook": True,
        "music_vol": 0.11,
        "mood_default": "motivational",
        "stickers": False,
        "sticker_limit": 0,
        "hook_duration": 2.9,
        "broll_opacity": 0.82,
        "broll_max": 2,
        "font_size": 92,
        "hot_font_size": 112,
        "words_per_chunk": 2,
        "caption_pop": True,
        "outline": 6,
        "hint": (
            "Alex Hormozi-style: huge 1–2 word captions, rapid zooms on key claims, "
            "minimal decoration, strong value-hook in first seconds, motivational tone."
        ),
    },
}

VALID_STYLES = set(STYLE_PRESETS.keys())


def get_style(style: str) -> Dict[str, Any]:
    key = (style or "dynamic").strip().lower()
    return STYLE_PRESETS.get(key, STYLE_PRESETS["dynamic"])


def ensure_min_zooms(
    zooms: List[Dict[str, Any]],
    *,
    words: List[Dict[str, Any]],
    duration: float,
    style_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pad zoom list when LLM under-delivers for punchy styles."""
    min_n = int(style_cfg.get("min_zooms") or 0)
    cap = int(style_cfg.get("zoom_cap") or 5)
    scale_max = float(style_cfg.get("zoom_scale_max") or 1.2)
    dur = float(style_cfg.get("default_zoom_duration") or 0.5)
    out = [z for z in zooms if isinstance(z, dict)][:cap]
    if len(out) >= min_n or min_n <= 0:
        return out

    candidates: List[float] = []
    for w in words or []:
        try:
            t = float(w.get("start") or 0)
            token = str(w.get("word") or "").strip(".,!?;:«»\"'")
            if len(token) >= 4 and t > 0.4:
                candidates.append(t)
        except (TypeError, ValueError):
            continue

    if not candidates and duration > 2:
        step = max(1.2, float(duration) / max(min_n + 1, 2))
        candidates = [step * (i + 1) for i in range(min_n)]

    used = {round(float(z.get("time") or 0), 1) for z in out}
    for t in candidates:
        if len(out) >= max(min_n, cap):
            break
        key = round(t, 1)
        if key in used or t >= float(duration) - 0.3:
            continue
        used.add(key)
        out.append({
            "effect": "zoom",
            "time": round(t, 2),
            "duration": dur,
            "scale": round(min(1.08 + 0.04 * len(out), scale_max), 3),
            "source": "auto",
        })
    return out[:cap]
