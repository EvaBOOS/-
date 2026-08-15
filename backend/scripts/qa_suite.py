"""QA suite for VideoGen Stage 1–4 features."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import traceback
from datetime import datetime

from app.services.ai.edge_tts_service import EdgeTTSService
from app.services.ai.openai_service import OpenAIService
from app.services.ai.whisper_service import WhisperService
from app.services.assets.library_service import AssetLibraryService
from app.services.assets.music_service import MusicLibraryService
from app.services.video.edit_styles import get_style
from app.services.video.ffmpeg_service import FFmpegService


REPORT = {"ts": datetime.now().isoformat(timespec="seconds"), "tests": []}


def ok(name: str, **extra):
    item = {"name": name, "ok": True, **extra}
    REPORT["tests"].append(item)
    print("OK ", name, {k: v for k, v in extra.items() if k != "clips"})


def fail(name: str, err, **extra):
    item = {"name": name, "ok": False, "error": str(err)[:500], **extra}
    REPORT["tests"].append(item)
    print("FAIL", name, err)


async def main():
    ff = FFmpegService()
    work = os.path.join("media", "generated", "qa_suite")
    os.makedirs(work, exist_ok=True)

    # 1) assets
    try:
        lib = AssetLibraryService()
        st = lib.status()
        picked = await lib.auto_pick_for_video("Чемпионат мира по футболу финал гол")
        photos = await lib.search_photos("football stadium night", 2)
        photo_path = None
        if photos:
            photo_path = await lib.download_photo(photos[0]["url"], photos[0]["id"])
        music = await MusicLibraryService().ensure_mood_track("energetic")
        ok(
            "assets",
            photos_ready=st["photos"]["ready"],
            theme=picked.get("theme"),
            stickers=len(picked.get("stickers") or []),
            font=picked.get("font_family"),
            photos=len(photos),
            photo=bool(photo_path),
            music=bool(music),
        )
    except Exception as e:
        fail("assets", e)

    # 2) source clip
    try:
        audio = await EdgeTTSService().synthesize_speech(
            "Всем привет. Сегодня разберём самую большую ошибку новичков в футболе. Смотри внимательно до конца.",
            "",
            work,
            language="ru",
        )
        src = os.path.join(work, "source_ru.mp4")
        cmd = [
            ff.ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=#0B1220:s=720x1280:d=14",
            "-i", audio, "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise Exception(r.stderr[-400:])
        ok("source_build", size=os.path.getsize(src), dur=round(ff.get_video_duration(src), 2))
    except Exception as e:
        fail("source_build", e)
        _write(work)
        return

    # 3) whisper
    try:
        speech = os.path.join(work, "speech.mp3")
        ff.extract_audio_track(src, speech)
        tr = await WhisperService().transcribe(speech, "ru")
        ok(
            "whisper",
            text=(tr.get("text") or "")[:140],
            words=len(tr.get("words") or []),
            dur=tr.get("duration"),
            provider=tr.get("provider"),
        )
    except Exception as e:
        fail("whisper", e)
        tr = {"text": "", "words": []}

    text = tr.get("text") or ""
    words = tr.get("words") or []
    dur = float(tr.get("duration") or ff.get_video_duration(src))
    llm = OpenAIService()

    # 4) LLM
    try:
        plan = await llm.plan_viral_edit(text, words, dur, "ru", "beast")
        ok(
            "plan_beast",
            mood=plan.get("mood"),
            effects=len(plan.get("effects") or []),
            broll=len(plan.get("broll") or []),
            hook=plan.get("hook"),
        )
    except Exception as e:
        fail("plan_beast", e)
        plan = {
            "effects": [
                {"effect": "zoom", "time": 1.2, "duration": 0.7, "scale": 1.15},
                {"effect": "zoom", "time": 3.5, "duration": 0.6, "scale": 1.12},
            ],
            "broll": [{"time": 2.0, "duration": 1.5, "query": "football stadium"}],
            "hook": {"use": True, "text": "Смотри до конца"},
            "mood": "energetic",
        }

    try:
        score = await llm.score_virality(
            text, dur, "ru", "beast", has_hook=True, broll_count=1, zoom_count=3
        )
        ok(
            "virality",
            score=score.get("score"),
            summary=(score.get("summary") or "")[:120],
            tips=score.get("tips") or [],
        )
    except Exception as e:
        fail("virality", e)

    try:
        en = await llm.translate_script(text or "Ошибка новичков.", "en", "ru")
        ok("translate", text=en[:160])
    except Exception as e:
        fail("translate", e)
        en = "This is the biggest beginner mistake in football. Watch carefully."

    # 5) viral beast + EN dub chain
    try:
        cut = os.path.join(work, "cut.mp4")
        sil = ff.detect_silence_intervals(src)
        ff.keep_speech_segments(src, cut, sil)
        framed = os.path.join(work, "framed_916.mp4")
        ff.scale_to_format(cut, framed, 1080, 1920)

        dub_audio = await EdgeTTSService().synthesize_speech(en, "", work, language="en")
        dubbed = os.path.join(work, "dubbed.mp4")
        ff.combine_audio_video(framed, dub_audio, dubbed)
        en_words = WhisperService._approximate_words(en, ff.get_video_duration(dubbed))

        zooms = [
            e for e in (plan.get("effects") or [])
            if isinstance(e, dict) and e.get("effect") == "zoom"
        ][:6]
        if not zooms:
            zooms = [
                {"time": 1.2, "duration": 0.7, "scale": 1.15},
                {"time": 3.5, "duration": 0.6, "scale": 1.12},
            ]
        zoomed = os.path.join(work, "zoomed.mp4")
        ff.apply_zoom_moments(dubbed, zoomed, zooms)

        inserts = []
        lib = AssetLibraryService()
        for i, cue in enumerate((plan.get("broll") or [])[:2]):
            q = str(cue.get("query") or "football")
            photos = await lib.search_photos(q, 1)
            if photos:
                p = await lib.download_photo(photos[0]["url"], photos[0]["id"])
                if p:
                    inserts.append({
                        "path": p,
                        "time": float(cue.get("time") or 1 + i * 2),
                        "duration": float(cue.get("duration") or 1.5),
                        "opacity": 0.9,
                    })
        broll = os.path.join(work, "broll.mp4")
        ff.overlay_broll_images(zoomed, broll, inserts, 1080, 1920)

        highlights = [
            str(e.get("word"))
            for e in (plan.get("effects") or [])
            if isinstance(e, dict) and e.get("effect") == "highlight_word"
        ]
        hook = None
        if (plan.get("hook") or {}).get("use"):
            hook = (plan.get("hook") or {}).get("text")
        hook = hook or "Watch till the end"
        ass = os.path.join(work, "k.ass")
        cfg = get_style("beast")
        ff.write_karaoke_ass(
            en_words, ass, highlights, "Arial", 1080, 1920, hook,
            float(cfg["hook_duration"]), int(cfg["font_size"]), int(cfg["hot_font_size"]),
        )
        subtitled = os.path.join(work, "subtitled.mp4")
        try:
            ff.burn_ass_subtitles(broll, ass, subtitled)
            ass_ok = True
        except Exception as e:
            subtitled = broll
            ass_ok = False
            fail("burn_ass", e)

        stickers = (await lib.auto_pick_for_video(en)).get("stickers") or []
        stickered = os.path.join(work, "stickered.mp4")
        if stickers:
            ff.overlay_stickers(subtitled, stickered, stickers=stickers[:3])
        else:
            import shutil
            shutil.copy2(subtitled, stickered)

        music = await MusicLibraryService().ensure_mood_track(plan.get("mood") or "energetic")
        final = os.path.join(work, "final_beast_en.mp4")
        ff.mix_background_music(stickered, music, final, float(cfg["music_vol"]))
        ok(
            "viral_beast_en",
            path=final.replace("\\", "/"),
            size=os.path.getsize(final),
            dur=round(ff.get_video_duration(final), 2),
            size_wh=ff.get_video_size(final),
            silences=len(sil),
            broll=len(inserts),
            zooms=len(zooms),
            stickers=min(3, len(stickers)),
            music=bool(music),
            ass_ok=ass_ok,
        )
    except Exception as e:
        fail("viral_beast_en", e, tb=traceback.format_exc()[-600:])

    # 6) formats
    try:
        sq = os.path.join(work, "fmt_1x1.mp4")
        yd = os.path.join(work, "fmt_16x9.mp4")
        ff.scale_to_format(src, sq, 1080, 1080)
        ff.scale_to_format(src, yd, 1920, 1080)
        ok("formats", square=ff.get_video_size(sq), youtube=ff.get_video_size(yd))
    except Exception as e:
        fail("formats", e)

    # 7) ai clips
    try:
        longsrc = os.path.join(work, "longish.mp4")
        cmd = [
            ff.ffmpeg_bin, "-y", "-stream_loop", "2", "-i", src,
            "-c:v", "libx264", "-c:a", "aac", "-t", "36", longsrc,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise Exception(r.stderr[-400:])
        speech2 = os.path.join(work, "long_speech.mp3")
        ff.extract_audio_track(longsrc, speech2)
        tr2 = await WhisperService().transcribe(speech2, "ru")
        plan_clips = await llm.plan_ai_clips(
            tr2.get("text") or text,
            tr2.get("segments") or [],
            float(tr2.get("duration") or 36),
            "ru",
            3,
        )
        clip_paths = []
        for i, c in enumerate(plan_clips[:3]):
            raw = os.path.join(work, f"clip_raw_{i+1}.mp4")
            out = os.path.join(work, f"clip_{i+1}.mp4")
            ff.cut_clip(longsrc, raw, c["start"], c["end"])
            ff.scale_to_format(raw, out, 1080, 1920)
            clip_paths.append({
                "title": c.get("title"),
                "score": c.get("score"),
                "start": c.get("start"),
                "end": c.get("end"),
                "dur": round(ff.get_video_duration(out), 1),
                "size": os.path.getsize(out),
            })
        ok("ai_clips", count=len(clip_paths), clips=clip_paths)
    except Exception as e:
        fail("ai_clips", e, tb=traceback.format_exc()[-600:])

    # 8) API
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("http://127.0.0.1:8000/api/v1/openapi.json")
            paths = list((r.json().get("paths") or {}).keys()) if r.status_code == 200 else []
        need = [
            "/api/v1/client/viral-edit",
            "/api/v1/client/clips",
            "/api/v1/client/assets/status",
            "/api/v1/client/generate",
        ]
        ok("api_routes", status=r.status_code, present={n: (n in paths) for n in need})
    except Exception as e:
        fail("api_routes", e)

    _write(work)


def _write(work: str):
    path = os.path.join(work, "report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)
    passed = sum(1 for t in REPORT["tests"] if t.get("ok"))
    total = len(REPORT["tests"])
    print(f"\nSUMMARY {passed}/{total} passed")
    print("REPORT", path)
    print(json.dumps(REPORT, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
