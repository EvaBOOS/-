"""Platform / intensity / genre presets for short-form targeting.

Existing edit styles (dynamic, beast, …) stay the source of montage knobs.
These layers only refine targeting and UX — they do not replace STYLE_PRESETS.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Moment labels for AI clips (long → short batch)
MOMENT_LABELS = ("hook", "punchline", "story", "cta", "debate", "tip", "other")

PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "auto": {
        "label": "Авто",
        "format": None,
        "clip_min_sec": 20.0,
        "clip_max_sec": 60.0,
        "clip_target_sec": 35.0,
        "caption_safe_hint": "Keep captions in the vertical safe zone (avoid top 12% / bottom 18%).",
        "brain_tags": ["shorts_viral", "reels", "tiktok"],
    },
    "shorts": {
        "label": "YouTube Shorts",
        "format": "9:16",
        "clip_min_sec": 15.0,
        "clip_max_sec": 60.0,
        "clip_target_sec": 35.0,
        "caption_safe_hint": "Shorts: punchy first 1s, captions mid-frame, hard end before 60s.",
        "brain_tags": ["shorts_viral", "youtube_shorts"],
    },
    "reels": {
        "label": "Instagram Reels",
        "format": "9:16",
        "clip_min_sec": 15.0,
        "clip_max_sec": 90.0,
        "clip_target_sec": 40.0,
        "caption_safe_hint": "Reels: hook in 1–2s, leave bottom clear for IG UI chrome.",
        "brain_tags": ["reels", "instagram", "retention"],
    },
    "tiktok": {
        "label": "TikTok",
        "format": "9:16",
        "clip_min_sec": 15.0,
        "clip_max_sec": 60.0,
        "clip_target_sec": 30.0,
        "caption_safe_hint": "TikTok: native pace, pattern interrupt early, CTA in last 3s.",
        "brain_tags": ["tiktok", "shorts_viral", "retention"],
    },
}

INTENSITY_PRESETS: Dict[str, Dict[str, Any]] = {
    "full": {
        "label": "Полный монтаж",
        "hint": "Zooms, B-roll, stickers, hook, music — full VideoGen stack.",
    },
    "lite": {
        "label": "Лёгкий монтаж",
        "hint": "Vertical crop + captions + hook + light music. No B-roll/stickers, fewer zooms.",
    },
}

GENRE_PRESETS: Dict[str, Dict[str, Any]] = {
    "default": {
        "label": "Обычный",
        "hint": "Balanced short-form storytelling.",
        "brain_tags": ["shorts_viral", "retention"],
        "force_hook": False,
    },
    "ugc": {
        "label": "UGC / отзыв",
        "hint": (
            "Face-to-camera UGC: casual iPhone energy, first-person recommendation, "
            "natural speech, soft social proof, end with soft CTA."
        ),
        "brain_tags": ["ugc", "hooks", "cta"],
        "force_hook": True,
    },
    "tutorial": {
        "label": "Туториал",
        "hint": (
            "Step-by-step tutorial: numbered beats, clear demo moments, "
            "zoom on actions, end with 'try this' CTA."
        ),
        "brain_tags": ["tutorial", "structure", "retention"],
        "force_hook": True,
    },
    "hook_battle": {
        "label": "Хук-спор",
        "hint": (
            "Controversy / debate hook: bold claim in first 2s, tension, "
            "punchline payoff, invite comments."
        ),
        "brain_tags": ["hooks", "debate", "retention"],
        "force_hook": True,
    },
    "review": {
        "label": "Обзор",
        "hint": (
            "Product/review format: problem → demo → verdict, highlight claims with zooms, "
            "honest tone, clear takeaway."
        ),
        "brain_tags": ["review", "storytelling", "cta"],
        "force_hook": True,
    },
    "unboxing": {
        "label": "Анбоксинг",
        "hint": (
            "Unboxing / first reaction: reveal energy, tactile moments, "
            "surprise beats, short reaction hooks."
        ),
        "brain_tags": ["unboxing", "hooks", "retention"],
        "force_hook": True,
    },
}

VALID_PLATFORMS = set(PLATFORM_PRESETS.keys())
VALID_INTENSITIES = set(INTENSITY_PRESETS.keys())
VALID_GENRES = set(GENRE_PRESETS.keys())


def normalize_platform(value: Optional[str]) -> str:
    key = (value or "auto").strip().lower()
    return key if key in VALID_PLATFORMS else "auto"


def normalize_intensity(value: Optional[str]) -> str:
    key = (value or "full").strip().lower()
    if key in {"clipper", "simple", "basic"}:
        return "lite"
    return key if key in VALID_INTENSITIES else "full"


def normalize_genre(value: Optional[str]) -> str:
    key = (value or "default").strip().lower()
    aliases = {
        "": "default",
        "none": "default",
        "normal": "default",
        "talking_head": "ugc",
        "howto": "tutorial",
        "debate": "hook_battle",
        "product_review": "review",
    }
    key = aliases.get(key, key)
    return key if key in VALID_GENRES else "default"


def get_platform(platform: str) -> Dict[str, Any]:
    return PLATFORM_PRESETS[normalize_platform(platform)]


def get_genre(genre: str) -> Dict[str, Any]:
    return GENRE_PRESETS[normalize_genre(genre)]


def get_intensity(intensity: str) -> Dict[str, Any]:
    return INTENSITY_PRESETS[normalize_intensity(intensity)]


def resolve_format(edit_format: str, platform: str) -> str:
    """Keep explicit non-9:16 user choice; otherwise apply platform default."""
    fmt = (edit_format or "9:16").strip()
    if fmt not in {"9:16", "1:1", "16:9"}:
        fmt = "9:16"
    plat = get_platform(platform)
    preferred = plat.get("format")
    if preferred and fmt == "9:16":
        return preferred
    if preferred and platform != "auto" and fmt in {"9:16", "1:1", "16:9"}:
        # Platform selected → prefer vertical unless user picked square/landscape deliberately.
        # If user picked 1:1 or 16:9, keep it.
        if fmt != "9:16":
            return fmt
        return preferred
    return fmt


def apply_intensity_to_style(style_cfg: Dict[str, Any], intensity: str) -> Dict[str, Any]:
    """Return a shallow copy of style knobs adjusted for lite/full."""
    cfg = dict(style_cfg or {})
    if normalize_intensity(intensity) != "lite":
        return cfg
    cfg["broll_max"] = 0
    cfg["stickers"] = False
    cfg["sticker_limit"] = 0
    cfg["zoom_cap"] = min(int(cfg.get("zoom_cap") or 5), 2)
    cfg["min_zooms"] = 0
    cfg["zoom_scale_max"] = min(float(cfg.get("zoom_scale_max") or 1.18), 1.14)
    cfg["force_hook"] = True
    cfg["music_vol"] = min(float(cfg.get("music_vol") or 0.14), 0.12)
    cfg["hint"] = (
        f"{cfg.get('hint') or ''} Lite mode: captions + light zooms + hook only; "
        "no B-roll or stickers."
    ).strip()
    return cfg


def genre_style_overrides(genre: str) -> Dict[str, Any]:
    g = get_genre(genre)
    out: Dict[str, Any] = {}
    if g.get("force_hook"):
        out["force_hook"] = True
    return out


def merge_style_layers(
    style_cfg: Dict[str, Any],
    *,
    intensity: str = "full",
    genre: str = "default",
) -> Dict[str, Any]:
    cfg = apply_intensity_to_style(style_cfg, intensity)
    cfg.update(genre_style_overrides(genre))
    genre_hint = get_genre(genre).get("hint") or ""
    if genre_hint and normalize_genre(genre) != "default":
        base_hint = cfg.get("hint") or ""
        cfg["hint"] = f"{base_hint} Genre: {genre_hint}".strip()
    return cfg


def clip_duration_bounds(platform: str) -> Tuple[float, float, float]:
    p = get_platform(platform)
    return (
        float(p["clip_min_sec"]),
        float(p["clip_max_sec"]),
        float(p["clip_target_sec"]),
    )


def normalize_moment_label(value: Optional[str]) -> str:
    key = (value or "other").strip().lower().replace(" ", "_")
    aliases = {
        "hook_open": "hook",
        "opening": "hook",
        "joke": "punchline",
        "payoff": "punchline",
        "story_beat": "story",
        "call_to_action": "cta",
        "argument": "debate",
        "howto": "tip",
        "tip_trick": "tip",
    }
    key = aliases.get(key, key)
    return key if key in MOMENT_LABELS else "other"


def list_presets_public() -> Dict[str, List[Dict[str, str]]]:
    """Small payload for client UI / docs."""
    return {
        "platforms": [
            {"id": k, "label": v["label"]} for k, v in PLATFORM_PRESETS.items()
        ],
        "intensities": [
            {"id": k, "label": v["label"]} for k, v in INTENSITY_PRESETS.items()
        ],
        "genres": [
            {"id": k, "label": v["label"]} for k, v in GENRE_PRESETS.items()
        ],
        "moment_labels": list(MOMENT_LABELS),
    }
