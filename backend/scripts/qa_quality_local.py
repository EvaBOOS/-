"""Local-only quality checks (no LLM / Whisper / Pexels)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

from app.services.ai.edge_tts_service import EdgeTTSService
from app.services.ai.whisper_service import WhisperService
from app.services.video.edit_styles import get_style
from app.services.video.ffmpeg_service import FFmpegService


async def main():
    ff = FFmpegService()
    work = os.path.join("media", "generated", "qa_quality")
    os.makedirs(work, exist_ok=True)

    audio = await EdgeTTSService().synthesize_speech(
        "Привет. Сегодня важный совет про футбол. Смотри до конца.",
        "",
        work,
        language="ru",
    )
    src = os.path.join(work, "source_local.mp4")
    cmd = [
        ff.ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "color=c=0x111111:s=720x1280:d=12",
        "-i", audio,
        "-filter_complex", "[1:a]adelay=1200|1200,apad=pad_dur=12[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "12",
        src,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]

    sil = ff.detect_silence_intervals(src)
    cut = os.path.join(work, "cut_local.mp4")
    ff.keep_speech_segments(src, cut, sil)
    src_d, cut_d = ff.get_video_duration(src), ff.get_video_duration(cut)
    kept = cut_d / src_d if src_d else 0

    # EN dub fit
    en = await EdgeTTSService().synthesize_speech(
        "Hello. Today an important football tip. Watch until the end.",
        "",
        work,
        language="en",
    )
    framed = os.path.join(work, "framed_local.mp4")
    ff.scale_to_format(cut, framed, 1080, 1920)
    dubbed = os.path.join(work, "dubbed_local.mp4")
    ff.combine_audio_video(framed, en, dubbed, fit_to_audio=True)
    dub_d = ff.get_video_duration(dubbed)
    en_d = ff.get_audio_duration(en) if hasattr(ff, "get_audio_duration") else dub_d

    cfg = get_style("beast")
    words = WhisperService._approximate_words(
        "Hello. Today an important football tip. Watch until the end.",
        dub_d,
    )
    ass = os.path.join(work, "k_local.ass")
    ff.write_karaoke_ass(
        words, ass, ["important", "football", "Watch"], "Arial", 1080, 1920,
        "Watch till the end", cfg["hook_duration"], cfg["font_size"], cfg["hot_font_size"],
        words_per_chunk=cfg["words_per_chunk"], caption_pop=True, outline=cfg["outline"],
    )
    zooms = [{"time": 1.0, "duration": 0.5, "scale": 1.3}, {"time": 3.0, "duration": 0.45, "scale": 1.25}]
    zoomed = os.path.join(work, "zoomed_local.mp4")
    ff.apply_zoom_moments(dubbed, zoomed, zooms, max_zooms=cfg["zoom_cap"], max_scale=cfg["zoom_scale_max"])
    subtitled = os.path.join(work, "final_local_beast.mp4")
    ff.burn_ass_subtitles(zoomed, ass, subtitled)

    report = {
        "silence": {
            "src": round(src_d, 2),
            "cut": round(cut_d, 2),
            "kept_ratio": round(kept, 3),
            "silences": len(sil),
            "soft_ok": kept >= 0.65,
        },
        "dub_fit": {
            "framed": round(ff.get_video_duration(framed), 2),
            "dubbed": round(dub_d, 2),
            "audio": round(en_d, 2) if isinstance(en_d, float) else en_d,
            "extended_or_fit": dub_d >= ff.get_video_duration(framed) - 0.15,
        },
        "beast": {
            "font": cfg["font_size"],
            "hot": cfg["hot_font_size"],
            "chunk": cfg["words_per_chunk"],
            "ass_events": sum(1 for line in open(ass, encoding="utf-8") if line.startswith("Dialogue:")),
            "final": subtitled.replace("\\", "/"),
            "wh": ff.get_video_size(subtitled),
            "dur": round(ff.get_video_duration(subtitled), 2),
            "size": os.path.getsize(subtitled),
        },
    }
    path = os.path.join(work, "local_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
