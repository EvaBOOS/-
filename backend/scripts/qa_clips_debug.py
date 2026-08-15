"""Debug AI clips planner + API routes after qa_suite."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

from app.services.ai.openai_service import OpenAIService
from app.services.ai.whisper_service import WhisperService
from app.services.video.ffmpeg_service import FFmpegService


async def main():
    ff = FFmpegService()
    work = os.path.join("media", "generated", "qa_suite")
    print("=== FILES ===")
    for name in sorted(os.listdir(work)):
        path = os.path.join(work, name)
        size = os.path.getsize(path)
        extra = ""
        if name.endswith(".mp4"):
            try:
                d = ff.get_video_duration(path)
                wh = ff.get_video_size(path)
                extra = f" dur={d:.2f} {wh[0]}x{wh[1]}"
            except Exception as e:
                extra = f" probe_err={e}"
        print(f"{name}\t{size}{extra}")

    llm = OpenAIService()
    src = os.path.join(work, "source_ru.mp4")
    longish = os.path.join(work, "longish.mp4")
    if os.path.exists(longish):
        speech = os.path.join(work, "long_speech.mp3")
        if not os.path.exists(speech):
            ff.extract_audio_track(longish, speech)
        tr = await WhisperService().transcribe(speech, "ru")
        dur = float(tr.get("duration") or ff.get_video_duration(longish))
        print("WHISPER_LONG", (tr.get("text") or "")[:160])
        print("DUR", dur, "segments", len(tr.get("segments") or []))
        plan = await llm.plan_ai_clips(
            tr.get("text") or "",
            tr.get("segments") or [],
            dur,
            "ru",
            3,
        )
        print("PLAN_36s", plan)

    # 90s synthetic — enough for 20-60s clip windows
    long90 = os.path.join(work, "long_90.mp4")
    cmd = [
        ff.ffmpeg_bin, "-y", "-stream_loop", "8", "-i", src,
        "-c:v", "libx264", "-c:a", "aac", "-t", "90", long90,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("LONG90_BUILD", r.returncode, os.path.getsize(long90) if os.path.exists(long90) else 0)
    speech90 = os.path.join(work, "long90_speech.mp3")
    ff.extract_audio_track(long90, speech90)
    tr90 = await WhisperService().transcribe(speech90, "ru")
    dur90 = float(tr90.get("duration") or 90)
    plan90 = await llm.plan_ai_clips(
        tr90.get("text") or "",
        tr90.get("segments") or [],
        dur90,
        "ru",
        3,
    )
    print("PLAN_90s", plan90)
    outs = []
    for i, c in enumerate(plan90[:3]):
        raw = os.path.join(work, f"clip90_raw_{i+1}.mp4")
        out = os.path.join(work, f"clip90_{i+1}.mp4")
        ff.cut_clip(long90, raw, c["start"], c["end"])
        ff.scale_to_format(raw, out, 1080, 1920)
        outs.append({
            "title": c.get("title"),
            "score": c.get("score"),
            "start": c["start"],
            "end": c["end"],
            "dur": round(ff.get_video_duration(out), 1),
            "size": os.path.getsize(out),
            "wh": ff.get_video_size(out),
        })
    print("CLIPS90", json.dumps(outs, ensure_ascii=False))

    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        for url in (
            "http://127.0.0.1:8000/openapi.json",
            "http://127.0.0.1:8000/api/v1/openapi.json",
        ):
            try:
                resp = await client.get(url)
                paths = list((resp.json().get("paths") or {}).keys()) if resp.status_code == 200 else []
                print("OPENAPI", url, resp.status_code, "n=", len(paths))
                for n in (
                    "/api/v1/client/viral-edit",
                    "/api/v1/client/clips",
                    "/api/v1/client/assets/status",
                    "/api/v1/client/generate",
                ):
                    print(" ", n, n in paths)
            except Exception as e:
                print("OPENAPI", url, e)


if __name__ == "__main__":
    asyncio.run(main())
