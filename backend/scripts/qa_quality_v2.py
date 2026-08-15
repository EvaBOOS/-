"""Quick quality regen: softer silence + beast captions + dub retiming."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

from app.services.ai.edge_tts_service import EdgeTTSService
from app.services.ai.openai_service import OpenAIService
from app.services.ai.whisper_service import WhisperService
from app.services.assets.library_service import AssetLibraryService
from app.services.assets.music_service import MusicLibraryService
from app.services.video.edit_styles import get_style
from app.services.video.ffmpeg_service import FFmpegService


async def main():
    ff = FFmpegService()
    work = os.path.join("media", "generated", "qa_quality")
    os.makedirs(work, exist_ok=True)
    report = {}

    # Build talky source with intentional pauses (to test soft silence)
    audio = await EdgeTTSService().synthesize_speech(
        "Всем привет. ... Сегодня разберём самую большую ошибку новичков в футболе. ... "
        "Смотри внимательно до конца — это важно.",
        "",
        work,
        language="ru",
    )
    # Insert real silence gaps via anullsrc concat-ish: pad audio with delays in video
    src = os.path.join(work, "source_ru.mp4")
    cmd = [
        ff.ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "color=c=#0B1220:s=720x1280:d=16",
        "-i", audio,
        "-filter_complex",
        "[1:a]adelay=800|800,apad=pad_dur=16[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-t", "16",
        src,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr[-500:])

    sil = ff.detect_silence_intervals(src, noise_db=-30.0, min_silence=0.7)
    cut = os.path.join(work, "cut.mp4")
    ff.keep_speech_segments(src, cut, sil, pad=0.2, max_cut_ratio=0.35, min_silence_len=0.55)
    src_dur = ff.get_video_duration(src)
    cut_dur = ff.get_video_duration(cut)
    report["silence"] = {
        "src": round(src_dur, 2),
        "cut": round(cut_dur, 2),
        "detected": len(sil),
        "kept_ratio": round(cut_dur / src_dur, 3) if src_dur else 0,
    }

    framed = os.path.join(work, "framed.mp4")
    ff.scale_to_format(cut, framed, 1080, 1920)

    speech = os.path.join(work, "speech.mp3")
    ff.extract_audio_track(framed, speech)
    tr = await WhisperService().transcribe(speech, "ru")
    text = tr.get("text") or ""
    words = tr.get("words") or []
    dur = float(tr.get("duration") or ff.get_video_duration(framed))

    llm = OpenAIService()
    translated = await llm.translate_script(text, "en", "ru")
    dub_audio = await EdgeTTSService().synthesize_speech(translated, "", work, language="en")
    dubbed = os.path.join(work, "dubbed.mp4")
    ff.combine_audio_video(framed, dub_audio, dubbed, fit_to_audio=True)
    dub_dur = ff.get_video_duration(dubbed)

    dub_speech = os.path.join(work, "dub_speech.mp3")
    ff.extract_audio_track(dubbed, dub_speech)
    tr2 = await WhisperService().transcribe(dub_speech, "en")
    en_words = tr2.get("words") or WhisperService._approximate_words(translated, dub_dur)
    report["dub"] = {
        "ok": True,
        "framed": round(dur, 2),
        "dubbed": round(dub_dur, 2),
        "timing": "whisper" if tr2.get("words") else "approx",
        "words": len(en_words),
        "text": (translated or "")[:120],
    }

    plan = await llm.plan_viral_edit(translated, en_words, dub_dur, "en", "beast")
    cfg = get_style("beast")
    zooms = [e for e in (plan.get("effects") or []) if e.get("effect") == "zoom"][: cfg["zoom_cap"]]
    highlights = [
        str(e.get("word"))
        for e in (plan.get("effects") or [])
        if e.get("effect") == "highlight_word" and e.get("word")
    ]
    zoomed = os.path.join(work, "zoomed.mp4")
    ff.apply_zoom_moments(
        dubbed, zoomed, zooms,
        max_zooms=cfg["zoom_cap"], max_scale=cfg["zoom_scale_max"],
    )

    lib = AssetLibraryService()
    inserts = []
    for i, cue in enumerate((plan.get("broll") or [])[:2]):
        photos = await lib.search_photos(str(cue.get("query") or "football"), 1)
        if photos:
            p = await lib.download_photo(photos[0]["url"], photos[0]["id"])
            if p:
                inserts.append({
                    "path": p,
                    "time": float(cue.get("time") or 1 + i * 2),
                    "duration": float(cue.get("duration") or 1.5),
                    "opacity": cfg["broll_opacity"],
                })
    broll = os.path.join(work, "broll.mp4")
    if inserts:
        ff.overlay_broll_images(zoomed, broll, inserts, 1080, 1920)
    else:
        shutil.copy2(zoomed, broll)

    hook = (plan.get("hook") or {}).get("text") if (plan.get("hook") or {}).get("use") else "Watch till the end"
    ass = os.path.join(work, "k.ass")
    ff.write_karaoke_ass(
        en_words, ass, highlights, "Arial", 1080, 1920, hook,
        float(cfg["hook_duration"]), int(cfg["font_size"]), int(cfg["hot_font_size"]),
        words_per_chunk=int(cfg["words_per_chunk"]),
        caption_pop=bool(cfg["caption_pop"]),
        outline=int(cfg["outline"]),
    )
    subtitled = os.path.join(work, "subtitled.mp4")
    ff.burn_ass_subtitles(broll, ass, subtitled)

    stickers = (await lib.auto_pick_for_video(translated)).get("stickers") or []
    stickered = os.path.join(work, "stickered.mp4")
    if stickers:
        ff.overlay_stickers(subtitled, stickered, stickers=stickers[:3])
    else:
        shutil.copy2(subtitled, stickered)

    music = await MusicLibraryService().ensure_mood_track(plan.get("mood") or "energetic")
    final = os.path.join(work, "final_beast_en_v2.mp4")
    ff.mix_background_music(stickered, music, final, float(cfg["music_vol"]))

    report["final"] = {
        "path": final.replace("\\", "/"),
        "dur": round(ff.get_video_duration(final), 2),
        "size": os.path.getsize(final),
        "wh": ff.get_video_size(final),
        "zooms": len(zooms),
        "broll": len(inserts),
        "stickers": min(3, len(stickers)),
        "ass_events": sum(1 for line in open(ass, encoding="utf-8") if line.startswith("Dialogue:")),
    }
    out = os.path.join(work, "report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("OUT", final)


if __name__ == "__main__":
    asyncio.run(main())
