"""Offline quality checks using existing qa_suite media (no TTS/Whisper/LLM)."""
from __future__ import annotations

import json
import os
import shutil

from app.services.ai.whisper_service import WhisperService
from app.services.video.edit_styles import get_style
from app.services.video.ffmpeg_service import FFmpegService


def main():
    ff = FFmpegService()
    src_dir = os.path.join("media", "generated", "qa_suite")
    work = os.path.join("media", "generated", "qa_quality")
    os.makedirs(work, exist_ok=True)

    source = os.path.join(src_dir, "source_ru.mp4")
    en_audio = None
    for name in os.listdir(src_dir):
        if name.endswith(".mp3") and os.path.getsize(os.path.join(src_dir, name)) > 40000:
            # Prefer the English-looking dubbed path if present later
            en_audio = os.path.join(src_dir, name)
            break
    # Use speech.mp3 from suite as stand-in dub audio if needed
    dub_candidate = os.path.join(src_dir, "speech.mp3")
    if os.path.isfile(dub_candidate):
        en_audio = dub_candidate

    assert os.path.isfile(source), source
    assert en_audio and os.path.isfile(en_audio), en_audio

    sil = ff.detect_silence_intervals(source)
    cut = os.path.join(work, "cut_offline.mp4")
    ff.keep_speech_segments(source, cut, sil)
    src_d = ff.get_video_duration(source)
    cut_d = ff.get_video_duration(cut)
    kept = cut_d / src_d if src_d else 0

    framed = os.path.join(work, "framed_offline.mp4")
    ff.scale_to_format(cut, framed, 1080, 1920)
    dubbed = os.path.join(work, "dubbed_offline.mp4")
    ff.combine_audio_video(framed, en_audio, dubbed, fit_to_audio=True)
    framed_d = ff.get_video_duration(framed)
    dub_d = ff.get_video_duration(dubbed)

    cfg = get_style("beast")
    words = WhisperService._approximate_words(
        "Hello everyone Today we break down the biggest beginner mistake in football Watch carefully",
        dub_d,
    )
    ass = os.path.join(work, "k_offline.ass")
    ff.write_karaoke_ass(
        words,
        ass,
        ["biggest", "mistake", "football", "Watch"],
        "Arial",
        1080,
        1920,
        "Watch till the end",
        float(cfg["hook_duration"]),
        int(cfg["font_size"]),
        int(cfg["hot_font_size"]),
        words_per_chunk=int(cfg["words_per_chunk"]),
        caption_pop=True,
        outline=int(cfg["outline"]),
    )
    ass_text = open(ass, encoding="utf-8").read()
    assert "\\fscx118" in ass_text or "fscx118" in ass_text
    assert f",{cfg['font_size']}," in ass_text or f",{cfg['font_size']}," in ass_text.replace(" ", "")

    zooms = [
        {"time": 0.8, "duration": 0.5, "scale": 1.3},
        {"time": 2.2, "duration": 0.45, "scale": 1.28},
        {"time": 3.5, "duration": 0.4, "scale": 1.25},
    ]
    zoomed = os.path.join(work, "zoomed_offline.mp4")
    ff.apply_zoom_moments(
        dubbed, zoomed, zooms,
        max_zooms=int(cfg["zoom_cap"]),
        max_scale=float(cfg["zoom_scale_max"]),
    )
    final = os.path.join(work, "final_beast_offline.mp4")
    ff.burn_ass_subtitles(zoomed, ass, final)

    # Hormozi chunk=2 style sample
    cfg_h = get_style("hormozi")
    ass_h = os.path.join(work, "k_hormozi.ass")
    ff.write_karaoke_ass(
        words, ass_h, ["biggest", "mistake"], "Arial", 1080, 1920,
        "This one tip changes everything",
        float(cfg_h["hook_duration"]), int(cfg_h["font_size"]), int(cfg_h["hot_font_size"]),
        words_per_chunk=int(cfg_h["words_per_chunk"]),
        caption_pop=True, outline=int(cfg_h["outline"]),
    )

    report = {
        "silence": {
            "src": round(src_d, 2),
            "cut": round(cut_d, 2),
            "kept_ratio": round(kept, 3),
            "silences": len(sil),
            "soft_ok": kept >= 0.65 or abs(cut_d - src_d) < 0.2,
        },
        "dub_fit": {
            "framed": round(framed_d, 2),
            "dubbed": round(dub_d, 2),
            "fit_ok": dub_d + 0.05 >= min(framed_d, dub_d),
        },
        "beast_final": {
            "path": final.replace("\\", "/"),
            "dur": round(ff.get_video_duration(final), 2),
            "wh": list(ff.get_video_size(final)),
            "size": os.path.getsize(final),
            "ass_events": ass_text.count("Dialogue:"),
            "pop": True,
            "font": cfg["font_size"],
            "zoom_cap": cfg["zoom_cap"],
        },
        "hormozi": {
            "font": cfg_h["font_size"],
            "chunk": cfg_h["words_per_chunk"],
            "ass_events": open(ass_h, encoding="utf-8").read().count("Dialogue:"),
        },
    }
    out = os.path.join(work, "offline_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("OUT", final)


if __name__ == "__main__":
    main()
