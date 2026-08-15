"""Smoke-test content presets: offline ffmpeg exports (no deploy needed)."""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.whisper_service import WhisperService
from app.services.video.content_presets import (
    clip_duration_bounds,
    list_presets_public,
    merge_style_layers,
    normalize_moment_label,
)
from app.services.video.edit_styles import get_style
from app.services.video.ffmpeg_service import FFmpegService


def main() -> None:
    ff = FFmpegService()
    src_dir = os.path.join("media", "generated", "qa_suite")
    out_dir = os.path.join("media", "generated", "qa_presets_smoke")
    os.makedirs(out_dir, exist_ok=True)

    source = os.path.join(src_dir, "framed_916.mp4")
    if not os.path.isfile(source):
        source = os.path.join(src_dir, "source_ru.mp4")
    assert os.path.isfile(source), source

    long_src = os.path.join(src_dir, "long_90.mp4")
    if not os.path.isfile(long_src):
        long_src = source

    presets = list_presets_public()
    lite_cfg = merge_style_layers(get_style("beast"), intensity="lite", genre="ugc")
    full_cfg = merge_style_layers(get_style("beast"), intensity="full", genre="hook_battle")

    duration = float(ff.get_video_duration(source) or 8)
    words = WhisperService._approximate_words(
        "Слушай внимательно Это простая ошибка новичков в коротких роликах "
        "Сделай хук в первую секунду и держи ритм до конца",
        duration,
    )

    # Lite export: captions + hook, few zooms
    lite_ass = os.path.join(out_dir, "lite_ugc.ass")
    ff.write_karaoke_ass(
        words,
        lite_ass,
        ["ошибка", "хук", "ритм"],
        "Arial",
        1080,
        1920,
        "Стоп. Это меняет удержание",
        float(lite_cfg["hook_duration"]),
        int(lite_cfg["font_size"]),
        int(lite_cfg["hot_font_size"]),
        words_per_chunk=int(lite_cfg["words_per_chunk"]),
        caption_pop=bool(lite_cfg.get("caption_pop")),
        outline=int(lite_cfg["outline"]),
    )
    lite_zoomed = os.path.join(out_dir, "lite_zoomed.mp4")
    ff.apply_zoom_moments(
        source,
        lite_zoomed,
        [
            {"time": 0.6, "duration": 0.45, "scale": 1.12},
            {"time": max(1.5, duration * 0.45), "duration": 0.4, "scale": 1.1},
        ][: int(lite_cfg["zoom_cap"])],
    )
    lite_final = os.path.join(out_dir, "lite_ugc_final.mp4")
    ff.burn_ass_subtitles(lite_zoomed, lite_ass, lite_final)

    # A/B hook variants on same base
    hook_variants = [
        "Стоп. Это меняет удержание",
        "Не листай — ошибка в первой секунде",
        "Хук за 1 секунду: вот как",
    ]
    hook_exports = []
    for i, hook in enumerate(hook_variants, start=1):
        ass = os.path.join(out_dir, f"hook_{i}.ass")
        ff.write_karaoke_ass(
            words,
            ass,
            ["ошибка", "хук"],
            "Arial",
            1080,
            1920,
            hook,
            float(full_cfg["hook_duration"]),
            int(full_cfg["font_size"]),
            int(full_cfg["hot_font_size"]),
            words_per_chunk=int(full_cfg["words_per_chunk"]),
            caption_pop=True,
            outline=int(full_cfg["outline"]),
        )
        out = os.path.join(out_dir, f"hook_ab_{i}.mp4")
        ff.burn_ass_subtitles(source, ass, out)
        hook_exports.append(
            {
                "index": i,
                "hook": hook,
                "file": os.path.basename(out),
                "bytes": ff.get_file_size(out),
                "duration": round(ff.get_video_duration(out), 2),
            }
        )

    # Batch cuts with moment labels (platform=tiktok bounds)
    clip_min, clip_max, clip_target = clip_duration_bounds("tiktok")
    long_dur = float(ff.get_video_duration(long_src) or duration)
    plan = [
        {"start": 2.0, "end": min(2.0 + clip_target, long_dur), "title": "Открытие", "moment": "hook", "score": 92},
        {
            "start": max(0.0, long_dur * 0.35),
            "end": min(long_dur, long_dur * 0.35 + clip_target),
            "title": "Панч",
            "moment": "punchline",
            "score": 88,
        },
        {
            "start": max(0.0, long_dur - clip_target - 1),
            "end": long_dur,
            "title": "CTA",
            "moment": "cta",
            "score": 80,
        },
    ]
    clips_out = []
    for i, clip in enumerate(plan, start=1):
        raw = os.path.join(out_dir, f"clip_raw_{i}.mp4")
        framed = os.path.join(out_dir, f"clip_{i}_{clip['moment']}.mp4")
        start, end = float(clip["start"]), float(clip["end"])
        if end - start < clip_min and long_dur >= clip_min:
            end = min(long_dur, start + clip_target)
        ff.cut_clip(long_src, raw, start, end)
        ff.scale_to_format(raw, framed, 1080, 1920)
        clips_out.append(
            {
                "index": i,
                "title": clip["title"],
                "moment": normalize_moment_label(clip["moment"]),
                "score": clip["score"],
                "file": os.path.basename(framed),
                "bytes": ff.get_file_size(framed),
                "duration": round(ff.get_video_duration(framed), 2),
                "window": [round(start, 2), round(end, 2)],
            }
        )

    report = {
        "presets": {k: len(v) for k, v in presets.items()},
        "lite_knobs": {
            "broll_max": lite_cfg["broll_max"],
            "stickers": lite_cfg["stickers"],
            "zoom_cap": lite_cfg["zoom_cap"],
            "force_hook": lite_cfg["force_hook"],
            "music_vol": lite_cfg["music_vol"],
        },
        "tiktok_bounds": {"min": clip_min, "max": clip_max, "target": clip_target},
        "lite_final": {
            "file": os.path.basename(lite_final),
            "bytes": ff.get_file_size(lite_final),
            "duration": round(ff.get_video_duration(lite_final), 2),
        },
        "hook_exports": hook_exports,
        "clips": clips_out,
        "out_dir": os.path.abspath(out_dir),
    }
    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
