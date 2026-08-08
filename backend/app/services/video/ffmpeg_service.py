import logging
import os
import uuid
import subprocess
import json
import shutil
from typing import Callable, Optional, List, Dict
from app.core.config import settings

logger = logging.getLogger(__name__)


def _resolve_binary(name: str) -> str:
    """Find ffmpeg/ffprobe on PATH or common WinGet install locations."""
    found = shutil.which(name)
    if found:
        return found

    local = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    winget_root = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget_root):
        for entry in os.listdir(winget_root):
            if "FFmpeg" not in entry and "ffmpeg" not in entry:
                continue
            pkg = os.path.join(winget_root, entry)
            for root, _dirs, files in os.walk(pkg):
                if f"{name}.exe" in files:
                    candidates.append(os.path.join(root, f"{name}.exe"))
                    break

    for path in (
        r"C:\ffmpeg\bin\{0}.exe".format(name),
        r"C:\Program Files\ffmpeg\bin\{0}.exe".format(name),
        *candidates,
    ):
        if os.path.isfile(path):
            return path

    return name  # let subprocess raise FileNotFoundError with a clear name


class FFmpegService:
    """Service for video processing with FFmpeg."""
    
    def __init__(self):
        self.output_dir = settings.GENERATED_DIR
        self.ffmpeg_bin = _resolve_binary("ffmpeg")
        self.ffprobe_bin = _resolve_binary("ffprobe")
        
    def get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            self.ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffprobe error: {result.stderr}")
        
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        return duration
    
    def get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        return self.get_video_duration(audio_path)
    
    def generate_srt_from_text(
        self,
        text: str,
        duration_seconds: float,
        output_path: str,
        words_per_subtitle: int = 5
    ) -> str:
        """
        Generate SRT subtitle file from text.
        
        Args:
            text: Full script text
            duration_seconds: Total duration in seconds
            output_path: Path to save SRT file
            words_per_subtitle: Words per subtitle segment
            
        Returns:
            Path to generated SRT file
        """
        words = text.split()
        total_words = len(words)
        
        segments = []
        for i in range(0, total_words, words_per_subtitle):
            segment_words = words[i:i + words_per_subtitle]
            segments.append(" ".join(segment_words))
        
        time_per_segment = duration_seconds / len(segments) if segments else 0
        
        srt_content = []
        for idx, segment in enumerate(segments):
            start_time = idx * time_per_segment
            end_time = (idx + 1) * time_per_segment
            
            srt_content.append(str(idx + 1))
            srt_content.append(f"{self._format_srt_time(start_time)} --> {self._format_srt_time(end_time)}")
            srt_content.append(segment)
            srt_content.append("")
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_content))
        
        return output_path

    def _ass_font_size(self, desired_px: int, video_height: int) -> int:
        """
        libass defaults PlayResY=288. Scale UI pixel size down so burn-in
        matches intended on-screen size for vertical 1080x1920 shorts.
        """
        play_res_y = 288
        height = max(video_height or 1920, 1)
        return max(8, int(round(desired_px * play_res_y / height)))

    def get_video_size(self, video_path: str) -> tuple:
        """Return (width, height) via ffprobe."""
        cmd = [
            self.ffprobe_bin,
            "-v", "quiet",
            "-select_streams", "v:0",
            "-print_format", "json",
            "-show_streams",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return (1080, 1920)
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return (1080, 1920)
        return (
            int(streams[0].get("width") or 1080),
            int(streams[0].get("height") or 1920),
        )
    
    def _format_srt_time(self, seconds: float) -> str:
        """Format seconds to SRT timestamp (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def add_subtitles_and_watermark(
        self,
        video_path: str,
        output_path: str,
        subtitle_path: Optional[str] = None,
        watermark_path: Optional[str] = None,
        watermark_position: str = "bottom_right",
        watermark_opacity: int = 80,
        watermark_scale: int = 15,
        subtitle_font: str = "Arial",
        subtitle_font_path: Optional[str] = None,
        subtitle_fonts_dir: Optional[str] = None,
        subtitle_font_size: int = 48,
        subtitle_font_color: str = "white",
        subtitle_bg_color: Optional[str] = None,
        subtitle_position: str = "bottom"
    ) -> str:
        """
        Add subtitles and watermark to video using FFmpeg.
        
        Args:
            video_path: Input video path
            output_path: Output video path
            subtitle_path: Path to SRT file
            watermark_path: Path to watermark image
            watermark_position: Position (top_left, top_right, bottom_left, bottom_right)
            watermark_opacity: Opacity percentage (0-100)
            watermark_scale: Scale as percentage of video width
            subtitle_font: Font family name (e.g. Montserrat)
            subtitle_font_path: Optional path to a .ttf/.otf file
            subtitle_fonts_dir: Optional fontsdir for libass (Google Fonts cache)
            subtitle_font_size: Font size in pixels
            subtitle_font_color: Font color (name or hex without #)
            subtitle_bg_color: Optional background color for subtitles
            subtitle_position: Position (top, center, bottom)
            
        Returns:
            Path to output video
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        filter_complex = []
        inputs = ["-i", video_path]
        
        if watermark_path and os.path.exists(watermark_path):
            inputs.extend(["-i", watermark_path])
            
            position_map = {
                "top_left": f"x=20:y=20",
                "top_right": f"x=W-w-20:y=20",
                "bottom_left": f"x=20:y=H-h-20",
                "bottom_right": f"x=W-w-20:y=H-h-20"
            }
            pos = position_map.get(watermark_position, position_map["bottom_right"])
            
            alpha = watermark_opacity / 100
            scale_expr = f"iw*{watermark_scale}/100:-1"
            
            filter_complex.append(
                f"[1:v]scale={scale_expr},format=rgba,"
                f"colorchannelmixer=aa={alpha}[watermark];"
                f"[0:v][watermark]overlay={pos}[v1]"
            )
            video_stream = "[v1]"
        else:
            video_stream = "[0:v]"
            if subtitle_path:
                filter_complex.append(f"[0:v]null[v1]")
                video_stream = "[v1]"
        
        if subtitle_path and os.path.exists(subtitle_path):
            fonts_dir = subtitle_fonts_dir
            font_name = subtitle_font
            if subtitle_font_path and os.path.isfile(subtitle_font_path):
                fonts_dir = fonts_dir or os.path.dirname(subtitle_font_path)
                # Prefer family name; path alone is unreliable for libass FontName
                if not font_name or font_name.lower() in ("arial", ""):
                    font_name = os.path.splitext(os.path.basename(subtitle_font_path))[0]

            subtitle_path_escaped = subtitle_path.replace(":", r"\:").replace("\\", "/")
            _width, height = self.get_video_size(video_path)
            # Branding stores approximate on-screen px; convert for libass PlayResY=288
            ass_size = self._ass_font_size(min(subtitle_font_size, 56), height)
            
            margin_v = {
                "top": 40,
                "center": 0,
                "bottom": 80
            }.get(subtitle_position, 80)
            
            alignment = {
                "top": 6,
                "center": 5,
                "bottom": 2
            }.get(subtitle_position, 2)
            
            force_style = (
                f"FontName={font_name},"
                f"FontSize={ass_size},"
                f"PrimaryColour=&H00{self._color_to_ass(subtitle_font_color)},"
                f"OutlineColour=&H00000000,"
                f"BackColour=&H80000000,"
                f"BorderStyle=1,"
                f"Outline=2,"
                f"Shadow=0,"
                f"MarginV={margin_v},"
                f"MarginL=40,"
                f"MarginR=40,"
                f"Alignment={alignment}"
            )
            
            if subtitle_bg_color:
                force_style += f",BorderStyle=4,BackColour=&H80{self._color_to_ass(subtitle_bg_color)}"

            sub_opts = f"force_style='{force_style}'"
            if fonts_dir and os.path.isdir(fonts_dir):
                fonts_escaped = fonts_dir.replace(":", r"\:").replace("\\", "/")
                sub_opts = f"fontsdir='{fonts_escaped}':{sub_opts}"
            
            current_stream = video_stream.strip("[]") if video_stream.startswith("[") else "0:v"
            filter_complex.append(
                f"[{current_stream}]subtitles='{subtitle_path_escaped}':{sub_opts}[vout]"
            )
            video_stream = "[vout]"
        
        cmd = [self.ffmpeg_bin, "-y"]
        cmd.extend(inputs)
        
        if filter_complex:
            full_filter = ";".join(filter_complex) if len(filter_complex) > 1 else filter_complex[0]
            if not full_filter.endswith("[vout]"):
                full_filter = full_filter.replace(video_stream, "[vout]")
            cmd.extend(["-filter_complex", full_filter])
            cmd.extend(["-map", "[vout]", "-map", "0:a?"])
        
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path
        ])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")
        
        return output_path

    def load_sticker_manifest(self, stickers_dir: str) -> List[Dict]:
        """Load stickers.json or fall back to all PNGs in the folder."""
        manifest_path = os.path.join(stickers_dir, "stickers.json")
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []

        stickers = []
        positions = ["top_left", "top_right", "bottom_left", "bottom_right"]
        pngs = sorted(
            n for n in os.listdir(stickers_dir)
            if n.lower().endswith((".png", ".webp"))
        )
        for i, name in enumerate(pngs[:4]):
            stickers.append({
                "file": name,
                "position": positions[i % len(positions)],
                "scale": 12,
                "opacity": 95,
            })
        return stickers

    def overlay_stickers(
        self,
        video_path: str,
        output_path: str,
        stickers_dir: Optional[str] = None,
        stickers: Optional[List[Dict]] = None,
    ) -> str:
        """
        Overlay PNG/WebP stickers on a video (Typiq assets, Twemoji, etc.).

        Each sticker dict: {file, position, scale (% of width), opacity (0-100)}.
        """
        stickers_dir = stickers_dir or os.path.join(settings.ASSETS_DIR, "stickers")
        if not os.path.isdir(stickers_dir):
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        items = stickers if stickers is not None else self.load_sticker_manifest(stickers_dir)
        resolved = []
        for item in items:
            path = item.get("path") or os.path.join(stickers_dir, item.get("file", ""))
            if path and os.path.isfile(path):
                resolved.append({**item, "path": path})

        if not resolved:
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        position_map = {
            "top_left": "x=36:y=36",
            "top_right": "x=W-w-36:y=36",
            "bottom_left": "x=36:y=H-h-220",
            "bottom_right": "x=W-w-36:y=H-h-220",
            "center": "x=(W-w)/2:y=(H-h)/2",
        }

        cmd = [self.ffmpeg_bin, "-y", "-i", video_path]
        filter_parts = []
        last_label = "0:v"

        for idx, item in enumerate(resolved):
            cmd.extend(["-i", item["path"]])
            scale = int(item.get("scale") or 12)
            opacity = float(item.get("opacity") or 95) / 100.0
            pos = position_map.get(item.get("position") or "top_right", position_map["top_right"])
            out_label = f"v{idx}"
            # input index: 0=video, 1..=stickers
            inp = idx + 1
            filter_parts.append(
                f"[{inp}:v]scale=iw*{scale}/100:-1,format=rgba,"
                f"colorchannelmixer=aa={opacity}[st{idx}];"
                f"[{last_label}][st{idx}]overlay={pos}[{out_label}]"
            )
            last_label = out_label

        filter_parts[-1] = filter_parts[-1].rsplit("[", 1)[0] + "[vout]"
        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vout]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg sticker overlay error: {result.stderr}")
        return output_path

    def overlay_timed_image(
        self,
        video_path: str,
        image_path: str,
        output_path: str,
        start: float,
        end: float,
        x_expr: str = "(W-w)/2",
        y_expr: str = "180",
    ) -> str:
        """Composite a (typically transparent-background) image onto the
        video, visible only during [start, end] seconds. Same overlay
        technique as overlay_stickers, just time-gated via `enable`."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [
            self.ffmpeg_bin, "-y", "-i", video_path, "-i", image_path,
            "-filter_complex",
            f"[0:v][1:v]overlay={x_expr}:{y_expr}:enable='between(t,{start},{end})'[vout]",
            "-map", "[vout]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg timed overlay error: {result.stderr}")
        return output_path

    def _color_to_ass(self, color: str) -> str:
        """Convert color name or hex to ASS format (BGR)."""
        color_map = {
            "white": "FFFFFF",
            "black": "000000",
            "red": "0000FF",
            "green": "00FF00",
            "blue": "FF0000",
            "yellow": "00FFFF",
            "cyan": "FFFF00",
            "magenta": "FF00FF"
        }
        
        if color.lower() in color_map:
            return color_map[color.lower()]
        
        if color.startswith("#"):
            color = color[1:]
        
        if len(color) == 6:
            r, g, b = color[0:2], color[2:4], color[4:6]
            return f"{b}{g}{r}"
        
        return "FFFFFF"
    
    def extract_audio(self, video_path: str, output_path: str) -> str:
        """Extract audio from video file."""
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-q:a", "2",
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")
        
        return output_path
    
    def combine_audio_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        fit_to_audio: bool = False,
    ) -> str:
        """
        Combine video with new audio track.
        If fit_to_audio=True, freeze last frame when dub is longer than picture
        (needed for translated TTS that rarely matches original duration).
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if fit_to_audio:
            v_dur = float(self.get_video_duration(video_path) or 0)
            a_dur = float(self.get_audio_duration(audio_path) or 0)
            ratio = a_dur / max(v_dur, 0.05) if v_dur > 0 else 1.0
            # Mild speed-up of dub when slightly longer — less freeze-frame, closer to “lip sync”
            atempo = 1.0
            if 1.05 < ratio <= 1.30:
                atempo = min(ratio, 1.25)
            effective_a = a_dur / atempo if atempo > 1.0 else a_dur
            pad = max(0.0, effective_a - v_dur)
            a_filt = "aresample=async=1:first_pts=0"
            if atempo > 1.001:
                a_filt = f"atempo={atempo:.4f},{a_filt}"
            # Finite pad only — infinite tpad(stop=-1) can hang ffmpeg on Windows
            if pad > 0.05:
                fc = (
                    f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f},"
                    f"setpts=PTS-STARTPTS[v];"
                    f"[1:a]{a_filt}[a]"
                )
            else:
                fc = (
                    f"[0:v]setpts=PTS-STARTPTS[v];"
                    f"[1:a]{a_filt}[a]"
                )
            cmd = [
                self.ffmpeg_bin, "-y",
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            cmd = [
                self.ffmpeg_bin, "-y",
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")

        return output_path

    def apply_watermark(
        self,
        video_path: str,
        output_path: str,
        watermark_path: str,
        watermark_position: str = "bottom_right",
        watermark_opacity: int = 80,
        watermark_scale: int = 15,
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Overlay a PNG/JPEG watermark onto video."""
        if not watermark_path or not os.path.isfile(watermark_path):
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        position_map = {
            "top_left": "20:20",
            "top_right": "W-w-20:20",
            "bottom_left": "20:H-h-20",
            "bottom_right": "W-w-20:H-h-20",
            "center": "(W-w)/2:(H-h)/2",
        }
        pos = position_map.get(watermark_position, position_map["bottom_right"])
        alpha = max(0.05, min(float(watermark_opacity or 80) / 100.0, 1.0))
        scale = max(5, min(int(watermark_scale or 15), 40))
        fc = (
            f"[1:v]scale=iw*{scale}/100:-1,format=rgba,"
            f"colorchannelmixer=aa={alpha}[wm];"
            f"[0:v][wm]overlay={pos}[vout]"
        )
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-i", watermark_path,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("apply_watermark ffmpeg failed, shipping without watermark: %s", err)
            if on_fallback:
                on_fallback(f"watermark not applied (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
        return output_path
    
    def get_file_size(self, file_path: str) -> int:
        """Get file size in bytes."""
        return os.path.getsize(file_path)

    def extract_audio_track(self, video_path: str, output_path: str) -> str:
        """Extract mono mp3 for Whisper (keeps size under API limits)."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "64k",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg extract audio error: {result.stderr}")
        return output_path

    def extract_audio_segment(
        self,
        media_path: str,
        output_path: str,
        start: float,
        duration: float,
    ) -> str:
        """Extract a time window of audio as mono mp3."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [
            self.ffmpeg_bin, "-y",
            "-ss", str(max(0.0, start)),
            "-i", media_path,
            "-t", str(max(0.5, duration)),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "48k",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg audio segment error: {result.stderr}")
        return output_path

    def cut_clip(
        self,
        video_path: str,
        output_path: str,
        start: float,
        end: float,
    ) -> str:
        """Cut a precise clip and re-encode for clean edits."""
        dur = max(0.5, float(end) - float(start))
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [
            self.ffmpeg_bin, "-y",
            "-ss", str(max(0.0, float(start))),
            "-i", video_path,
            "-t", str(dur),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg cut clip error: {result.stderr}")
        return output_path

    def detect_silence_intervals(
        self,
        media_path: str,
        noise_db: float = -30.0,
        min_silence: float = 0.7,
    ) -> List[Dict[str, float]]:
        """
        Return silence intervals [{start, end}, ...] via silencedetect.

        Defaults are intentionally soft: only longer/quieter pauses are cut,
        so natural speech breaths survive.
        """
        cmd = [
            self.ffmpeg_bin, "-i", media_path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        stderr = result.stderr or ""
        starts = []
        ends = []
        for line in stderr.splitlines():
            if "silence_start:" in line:
                try:
                    starts.append(float(line.split("silence_start:")[1].strip().split()[0]))
                except Exception:
                    pass
            if "silence_end:" in line:
                try:
                    part = line.split("silence_end:")[1].strip().split("|")[0].strip()
                    ends.append(float(part.split()[0]))
                except Exception:
                    pass
        intervals = []
        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else None
            if end is not None and end > start:
                intervals.append({"start": start, "end": end})
        return intervals

    def keep_speech_segments(
        self,
        video_path: str,
        output_path: str,
        silence_intervals: List[Dict[str, float]],
        min_keep: float = 0.35,
        pad: float = 0.2,
        max_cut_ratio: float = 0.35,
        min_silence_len: float = 0.55,
        skip_if_kept_ratio_above: float = 0.97,
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Cut arbitrary intervals (silence, or anything else — e.g. filler-word
        spans) by keeping the complementary ranges. Soft by default: pads
        speech edges, ignores very short intervals, refuses to cut more than
        max_cut_ratio of the timeline, and skips re-encoding entirely when
        barely anything would be cut.

        skip_if_kept_ratio_above governs that last bar: 0.97 (cutting <3% of
        the video isn't worth a full re-encode) makes sense for silence, but
        is too conservative for filler words, which are individually brief —
        callers cutting those should pass something much closer to 1.0.
        """
        duration = self.get_video_duration(video_path)
        if duration <= 0:
            shutil.copy2(video_path, output_path)
            return output_path

        # Ignore brief pauses (breaths / micro-gaps)
        silences = [
            s for s in silence_intervals
            if (s.get("end", 0) - s.get("start", 0)) >= min_silence_len
        ]

        keep: List[Dict[str, float]] = []
        cursor = 0.0
        for sil in silences:
            s = max(0.0, sil["start"] - pad)
            e = min(duration, sil["end"] + pad)
            if s - cursor >= min_keep:
                keep.append({"start": cursor, "end": s})
            cursor = max(cursor, e)
        if duration - cursor >= min_keep:
            keep.append({"start": cursor, "end": duration})

        kept_dur = sum(k["end"] - k["start"] for k in keep)
        cut_ratio = 1.0 - (kept_dur / duration) if duration else 0.0
        # Skip if nothing useful, almost nothing to cut, too shredded, or too aggressive
        if (
            not keep
            or kept_dur >= duration * skip_if_kept_ratio_above
            or len(keep) > 40
            or cut_ratio > max_cut_ratio
        ):
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        # filter: trim+concat
        parts = []
        labels = []
        for i, seg in enumerate(keep):
            start = seg["start"]
            dur = seg["end"] - seg["start"]
            parts.append(
                f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS[v{i}];"
                f"[0:a]atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS[a{i}]"
            )
            labels.append(f"[v{i}][a{i}]")
        filter_complex = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keep)}:v=1:a=1[outv][outa]"
        cmd = [
            self.ffmpeg_bin, "-y", "-i", video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("keep_speech_segments ffmpeg failed, keeping unedited source: %s", err)
            if on_fallback:
                on_fallback(f"silence trim skipped (ffmpeg error): {err[:150]}")
            # Soft-fail: keep original
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path

    def scale_to_vertical(
        self,
        video_path: str,
        output_path: str,
        width: int = 1080,
        height: int = 1920,
    ) -> str:
        """Center-crop / pad to 9:16."""
        return self.scale_to_format(video_path, output_path, width=width, height=height)

    def scale_to_format(
        self,
        video_path: str,
        output_path: str,
        width: int = 1080,
        height: int = 1920,
    ) -> str:
        """Center-crop / pad to target aspect (9:16, 1:1, 16:9, ...)."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )
        cmd = [
            self.ffmpeg_bin, "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg format scale error: {result.stderr}")
        return output_path

    def apply_zoom_moments(
        self,
        video_path: str,
        output_path: str,
        zooms: List[Dict],
        max_zooms: int = 5,
        max_scale: float = 1.25,
        ramp: float = 0.18,
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Overlay brief zoomed crops for retention punches.
        zooms: [{time, duration, scale}]

        Each zoom eases in and back out over `ramp` seconds at each edge of
        its window instead of snapping to full scale instantly (a hard-cut
        pop previously) — now a smooth push-in/out.

        Implementation note: this ffmpeg build's `crop` filter no longer
        re-evaluates its w/h per frame (no `eval` option at all), and while
        `scale` does support eval=frame, an INCREASING size expression
        (0->1.0 fraction rising) re-evaluates correctly per frame but a
        DECREASING one measured directly against the stream's own clock does
        not (empirically verified — it stays flat instead of easing down).
        The fix: build the ease-out portion by trimming just that edge,
        reversing it, applying the same (proven-correct) *increasing*
        formula to the reversed frames, then reversing back — a decreasing
        curve in playback order made entirely out of the increasing curve
        that actually works. Ease-in, a constant-scale hold, and this
        reversed ease-out are then concatenated into one small "zoom clip"
        per window and overlaid onto the base at that window's time offset.
        """
        if not zooms:
            shutil.copy2(video_path, output_path)
            return output_path

        width, height = self.get_video_size(video_path)
        cap = max(1, min(int(max_zooms or 5), 8))
        scale_cap = min(max(float(max_scale or 1.25), 1.08), 1.4)
        ramp = max(0.05, min(float(ramp or 0.18), 0.4))
        zooms = sorted(zooms, key=lambda z: float(z.get("time") or 0))[:cap]
        n = len(zooms)
        rebuilt = [
            f"[0:v]split={n + 1}[base]" + "".join(f"[src{i}]" for i in range(n))
        ]
        current = "base"
        for i, z in enumerate(zooms):
            t0 = float(z.get("time") or 0)
            dur = max(0.25, float(z.get("duration") or 0.6))
            t1 = t0 + dur
            scale = min(max(float(z.get("scale") or 1.12), 1.05), scale_cap)
            r = min(ramp, dur / 2.2)
            ease_expr = f"(1+({scale}-1)*max(0,min(t/{r:.3f},1)))"

            hold_dur = dur - 2 * r
            has_hold = hold_dur > 0.05
            # Only split into as many branches as are actually used below —
            # an unconsumed split output is a hard filtergraph error
            # ("output N unconnected"), so the 3-way split must not happen
            # when there's no hold segment for a short zoom window.
            rebuilt.append(
                f"[src{i}]split=3[si{i}][sh{i}][so{i}]"
                if has_hold else
                f"[src{i}]split=2[si{i}][so{i}]"
            )
            # setsar=1 on every branch: the eval=frame scale's fractional
            # pixel math can leave a slightly different (non-square) SAR on
            # the ease segments than the plain constant-scale hold segment —
            # concat refuses to join inputs whose SAR doesn't match exactly.
            rebuilt.append(
                f"[si{i}]trim=start={t0:.3f}:duration={r:.3f},setpts=PTS-STARTPTS,"
                f"scale=w='iw*{ease_expr}':h='ih*{ease_expr}':eval=frame,"
                f"crop={width}:{height}:x='(iw-{width})/2':y='(ih-{height})/2',"
                f"setsar=1[zin{i}]"
            )
            rebuilt.append(
                f"[so{i}]trim=start={t0 + dur - r:.3f}:duration={r:.3f},"
                f"setpts=PTS-STARTPTS,reverse,"
                f"scale=w='iw*{ease_expr}':h='ih*{ease_expr}':eval=frame,"
                f"crop={width}:{height}:x='(iw-{width})/2':y='(ih-{height})/2',"
                f"setsar=1,reverse[zout{i}]"
            )
            if has_hold:
                rebuilt.append(
                    f"[sh{i}]trim=start={t0 + r:.3f}:duration={hold_dur:.3f},"
                    f"setpts=PTS-STARTPTS,scale=iw*{scale}:ih*{scale},"
                    f"crop={width}:{height},setsar=1[zhold{i}]"
                )
                rebuilt.append(
                    f"[zin{i}][zhold{i}][zout{i}]concat=n=3:v=1:a=0,"
                    f"setpts=PTS+{t0:.3f}/TB[z{i}]"
                )
            else:
                rebuilt.append(
                    f"[zin{i}][zout{i}]concat=n=2:v=1:a=0,"
                    f"setpts=PTS+{t0:.3f}/TB[z{i}]"
                )

            out = "vout" if i == n - 1 else f"vz{i}"
            rebuilt.append(
                f"[{current}][z{i}]overlay=(W-w)/2:(H-h)/2:"
                f"enable='between(t\\,{t0}\\,{t1})'[{out}]"
            )
            current = out

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [
            self.ffmpeg_bin, "-y", "-i", video_path,
            "-filter_complex", ";".join(rebuilt),
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("apply_zoom_moments ffmpeg failed, shipping without zooms: %s", err)
            if on_fallback:
                on_fallback(f"zooms not applied (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path

    def write_karaoke_ass(
        self,
        words: List[Dict],
        output_path: str,
        highlight_words: Optional[List[str]] = None,
        font_name: str = "Arial",
        video_width: int = 1080,
        video_height: int = 1920,
        hook_text: Optional[str] = None,
        hook_duration: float = 2.6,
        font_size: int = 64,
        hot_font_size: int = 72,
        words_per_chunk: int = 4,
        caption_pop: bool = False,
        outline: int = 3,
        emphasis_style: str = "color",
        track_points: Optional[List[Dict]] = None,
        accent_color: Optional[str] = None,
    ) -> str:
        """
        Write ASS captions as a single non-overlapping layer.
        Highlights use inline color tags (no second Hot dialogue on top).

        Two distinct cases get two distinct tag sets:
        - "sync": the word being spoken right now (momentary karaoke pop).
        - "emphasis": a word the LLM flagged as important (`is_hot`), which
          stays highlighted for the whole chunk's on-screen duration, not
          just its own window. `emphasis_style` controls how that looks:
          "color" (cyan+bold, same as sync — legacy/default behavior),
          "glow" (soft dark blurred halo around the word), or "none" (no
          extra styling beyond the ordinary sync pop).

        `track_points`, when given (see `face_tracking.track_face_centers`),
        makes captions follow the tracked speaker instead of sitting at the
        Style's fixed Alignment/MarginV position — each Dialogue line gets
        an interpolated `\\pos(x,y)` override. Omit/None for the ordinary
        fixed-position behavior (unchanged).

        `accent_color`, when given (name or "#RRGGBB" hex), replaces the
        default cyan/yellow used for the sync pop and "color"-style
        emphasis — pick something that fits the footage instead of the
        hardcoded default.
        """
        highlight = {w.lower().strip(".,!?;:«»\"'") for w in (highlight_words or [])}
        font_size = max(40, min(int(font_size or 64), 120))
        hot_font_size = max(font_size, min(int(hot_font_size or 72), 130))
        chunk_n = max(1, min(int(words_per_chunk or 4), 6))
        outline_n = max(1, min(int(outline or 3), 8))
        # ASS BGR: white default, accent color (cyan/yellow unless overridden)
        # for the word being spoken right now
        accent_hex = self._color_to_ass(accent_color) if accent_color else "00E5FF"
        sync_c = r"{\c&H00" + accent_hex + r"&\b1}"
        sync_reset = r"{\c&H00FFFFFF&\b0}"

        if emphasis_style == "glow":
            # Soft dark blurred halo ("dark cloud") behind the word, text
            # stays white/bold — distinct from the cyan sync pop.
            emphasis_open = (
                r"{\c&H00FFFFFF&\b1\3c&H000000&\3a&H30&\bord12\blur16}"
            )
            emphasis_reset = (
                r"{\c&H00FFFFFF&\b0\3c&H000000&\3a&H00&\bord" + str(outline_n) + r"\blur0}"
            )
        elif emphasis_style == "none":
            emphasis_open = ""
            emphasis_reset = ""
        else:  # "color" (default) — legacy behavior, identical to sync
            emphasis_open = sync_c
            emphasis_reset = sync_reset

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{outline_n},1,2,60,60,180,1
Style: Hook,{font_name},{hot_font_size + 4},&H0000E5FF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,{outline_n + 2},2,8,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines: List[List[Dict]] = []
        chunk: List[Dict] = []
        for w in words:
            chunk.append(w)
            if len(chunk) >= chunk_n:
                lines.append(chunk)
                chunk = []
        if chunk:
            lines.append(chunk)

        def ts(seconds: float) -> str:
            seconds = max(0.0, seconds)
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            cs = int(round((seconds - int(seconds)) * 100))
            if cs >= 100:
                s += 1
                cs = 0
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        def escape_ass(text: str) -> str:
            return (
                str(text)
                .replace("\\", "\\\\")
                .replace("{", "(")
                .replace("}", ")")
                .replace("\n", "\\N")
            )

        def is_hot(token: str) -> bool:
            clean = token.lower().strip(".,!?;:«»\"'")
            if not clean:
                return False
            # Only explicit LLM highlights — auto-long-word punch caused double-stack noise
            return clean in highlight

        def pop_tag() -> str:
            if not caption_pop:
                return ""
            return r"{\fscx112\fscy112\t(0,120,\fscx100\fscy100)}"

        events = []
        if hook_text:
            safe = escape_ass(hook_text)
            events.append(
                f"Dialogue: 5,0:00:00.00,{ts(hook_duration)},Hook,,0,0,0,,{pop_tag()}{safe}"
            )

        for idx, group in enumerate(lines):
            start = float(group[0]["start"])
            end = max(float(group[-1]["end"]), start + 0.15)
            # Prevent overlap with next chunk
            if idx + 1 < len(lines):
                next_start = float(lines[idx + 1][0]["start"])
                end = min(end, max(start + 0.12, next_start - 0.04))

            # Per-word active-highlight windows within [start, end]: the
            # currently-spoken word pops in accent color while the rest of
            # the chunk stays visible in place — real karaoke-style sync to
            # speech, instead of one static color applied for the whole
            # chunk's on-screen duration.
            word_starts = [max(start, float(w["start"])) for w in group]
            bounds = []
            for wi in range(len(group)):
                w_start = max(start, min(word_starts[wi], end - 0.02))
                w_end = word_starts[wi + 1] if wi + 1 < len(group) else end
                w_end = min(max(w_end, w_start + 0.06), end)
                bounds.append((w_start, w_end))

            for wi, (w_start, w_end) in enumerate(bounds):
                if w_end <= w_start:
                    continue
                parts = []
                for gi, x in enumerate(group):
                    word = escape_ass(x["word"])
                    if is_hot(str(x["word"])):
                        parts.append(f"{emphasis_open}{word}{emphasis_reset}" if emphasis_open else word)
                    elif gi == wi:
                        parts.append(f"{sync_c}{word}{sync_reset}")
                    else:
                        parts.append(word)
                text = " ".join(parts)
                lead = pop_tag() if wi == 0 else ""
                if track_points:
                    from app.services.video.face_tracking import resolve_position
                    pos = resolve_position(track_points, w_start, video_width, video_height)
                    if pos:
                        lead = r"{\an5\pos(%d,%d)}" % (pos[0], pos[1]) + lead
                events.append(
                    f"Dialogue: 0,{ts(w_start)},{ts(w_end)},Default,,0,0,0,,{lead}{text}"
                )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events) + "\n")
        return output_path

    def burn_ass_subtitles(
        self,
        video_path: str,
        ass_path: str,
        output_path: str,
        fonts_dir: Optional[str] = None,
    ) -> str:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        ass_escaped = ass_path.replace("\\", "/").replace(":", r"\:")
        filt = f"ass='{ass_escaped}'"
        if fonts_dir and os.path.isdir(fonts_dir):
            fonts_escaped = fonts_dir.replace("\\", "/").replace(":", r"\:")
            filt = f"ass='{ass_escaped}':fontsdir='{fonts_escaped}'"
        cmd = [
            self.ffmpeg_bin, "-y", "-i", video_path,
            "-vf", filt,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg ASS burn error: {result.stderr}")
        return output_path

    def is_mostly_dark(self, video_path: str, samples: int = 5) -> bool:
        """
        Heuristic: sample a few frames as 1x1 RGB; if average luma is very low,
        treat as black/placeholder canvas (common for TTS demos).
        """
        if not video_path or not os.path.isfile(video_path):
            return True
        duration = float(self.get_video_duration(video_path) or 0)
        if duration <= 0:
            return True
        bright = []
        for i in range(samples):
            t = duration * (i + 0.5) / samples
            t = max(0.05, min(max(duration - 0.05, 0.05), t))
            cmd = [
                self.ffmpeg_bin, "-v", "error",
                "-ss", f"{t:.3f}", "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=1:1:flags=fast_bilinear",
                "-f", "rawvideo", "-pix_fmt", "rgb24",
                "pipe:1",
            ]
            result = subprocess.run(cmd, capture_output=True)
            raw = result.stdout or b""
            if len(raw) >= 3:
                r, g, b = raw[0], raw[1], raw[2]
                # Rec. 601 luma
                bright.append(0.299 * r + 0.587 * g + 0.114 * b)
        if not bright:
            return False
        avg = sum(bright) / len(bright)
        return avg < 28.0

    def build_photo_background(
        self,
        photo_paths: List[str],
        output_path: str,
        duration: float,
        width: int = 1080,
        height: int = 1920,
        audio_path: Optional[str] = None,
    ) -> str:
        """Ken-Burns slideshow covering full duration (for dark/empty sources)."""
        photos = [p for p in photo_paths if p and os.path.isfile(p)]
        if not photos:
            raise ValueError("No photos for background")
        duration = max(2.0, float(duration or 5.0))
        n = len(photos)
        slice_dur = duration / n
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Build segments then concat
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            segs = []
            for i, photo in enumerate(photos):
                seg = os.path.join(tmp, f"seg_{i}.mp4")
                # Mild zoompan for motion
                frames = max(int(slice_dur * 30), 15)
                vf = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},"
                    f"zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps=30,"
                    f"setsar=1"
                )
                cmd = [
                    self.ffmpeg_bin, "-y",
                    "-loop", "1", "-i", photo,
                    "-vf", vf,
                    "-t", f"{slice_dur:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    seg,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.isfile(seg):
                    segs.append(seg)
            if not segs:
                raise RuntimeError("Failed to build photo segments")
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for s in segs:
                    f.write(f"file '{s.replace(chr(92), '/')}'\n")
            silent = os.path.join(tmp, "silent.mp4")
            cmd = [
                self.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                "-c", "copy", silent,
            ]
            subprocess.run(cmd, capture_output=True, text=True)
            if audio_path and os.path.isfile(audio_path):
                cmd = [
                    self.ffmpeg_bin, "-y",
                    "-i", silent, "-i", audio_path,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    "-movflags", "+faststart",
                    output_path,
                ]
            else:
                cmd = [
                    self.ffmpeg_bin, "-y", "-i", silent,
                    "-c", "copy", "-movflags", "+faststart", output_path,
                ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-400:])
        return output_path

    @staticmethod
    def _broll_layer(
        item: Dict,
        index: int,
        width: int,
        height: int,
        inp: int,
        label: str,
        default_mode: str,
        max_opacity: float,
        t0: float = 0.0,
    ) -> tuple:
        """Builds the per-item scale/pad/fade filter piece plus its overlay
        x/y position expression, from the insert's own "size"/"position"
        (set by the LLM edit-plan per moment — see openai_service.py
        plan_viral_edit) — falling back to the legacy whole-batch `mode`
        ("pip" -> "large", anything else -> the original full-frame
        "flash" look) for callers/plans that predate per-item sizing.

        `t0` is this item's own "-itsoffset" (see the input-building loops
        in overlay_broll_images/overlay_broll_clips) — every broll input is
        itsoffset-shifted by its own insert time so its decoded pts lines up
        with the main video's absolute clock instead of starting at 0. Without
        that shift, an insert scheduled later in the video would already be
        finished decoding (images) or already past its own fade-out (looped
        clips) by the time its overlay `enable()` window opens, so it would
        render as a static frozen ghost or not appear at all. The fade
        filter's st= values below are expressed in that same absolute clock.
        Returns (filter_str, position_expr)."""
        dur = max(0.6, float(item.get("duration") or 1.5))
        opacity = float(item.get("opacity") or 0.6)

        size = str(item.get("size") or "").strip().lower()
        if size not in {"small", "medium", "large"}:
            size = "large" if (default_mode or "flash").lower() == "pip" else "full"

        if size == "full":
            # Legacy mode=flash: whole frame, dimmer so the speaker still reads through.
            opacity = min(max(opacity, 0.35), max_opacity)
            filt = (
                f"[{inp}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},format=rgba,colorchannelmixer=aa={opacity}[{label}]"
            )
            return filt, "0:0"

        if size == "large":
            # Legacy mode=pip: lower-third card, full width, nearly opaque.
            opacity = min(max(opacity, 0.35), max_opacity)
            card_h = int(height * 0.42)
            filt = (
                f"[{inp}:v]scale={width}:{card_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{card_h},format=rgba,colorchannelmixer=aa={opacity}[{label}]"
            )
            return filt, f"0:{height - card_h}"

        # small/medium: a bordered corner card that leaves the speaker fully
        # visible, with a quick fade in/out instead of popping on abruptly.
        # (colorchannelmixer's "aa" is a plain static double -- it can't
        # parse a time-varying if()/lt() expression -- so the constant base
        # opacity comes from colorchannelmixer and the in/out ramp comes
        # from the dedicated "fade" filter's alpha=1 mode instead.)
        opacity = min(max(opacity, 0.8), 0.98)
        # Bound both width AND height (force_original_aspect_ratio=decrease,
        # fit-within-box) rather than scaling by width alone: B-roll stock
        # photos/clips for a 9:16 video are frequently fetched in portrait
        # orientation too, and a width-only scale on a near-square/portrait
        # source would make even a "small" card balloon to cover half the
        # frame height -- overlapping the speaker's face, the exact thing
        # this per-item sizing was built to avoid.
        card_w = int(width * (0.32 if size == "small" else 0.5))
        card_h_max = int(height * (0.22 if size == "small" else 0.32))
        border = 5
        fade_s = min(0.2, dur / 4)
        fade_in_st = t0
        fade_out_st = t0 + max(0.0, dur - fade_s)
        filt = (
            f"[{inp}:v]scale={card_w}:{card_h_max}:force_original_aspect_ratio=decrease,setsar=1,"
            f"pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:color=white,"
            f"format=rgba,colorchannelmixer=aa={opacity},"
            f"fade=t=in:st={fade_in_st}:d={fade_s}:alpha=1,"
            f"fade=t=out:st={fade_out_st}:d={fade_s}:alpha=1[{label}]"
        )
        pad_frac = 0.05
        position = str(item.get("position") or "").strip().lower()
        x_expr = {
            "top_left": f"W*{pad_frac}",
            "bottom_left": f"W*{pad_frac}",
        }.get(position, f"W-w-W*{pad_frac}")
        y_expr = {
            "top_left": f"H*{pad_frac}",
            "top_right": f"H*{pad_frac}",
        }.get(position, f"H-h-H*{pad_frac}")
        return filt, f"{x_expr}:{y_expr}"

    def _build_broll_filter_complex(
        self,
        valid: List[Dict],
        width: int,
        height: int,
        default_mode: str,
        max_opacity: float,
    ) -> str:
        parts = []
        current = "0:v"
        n = len(valid)
        for i, item in enumerate(valid):
            t0 = float(item.get("time") or 0)
            dur = max(0.6, float(item.get("duration") or 1.5))
            t1 = t0 + dur
            inp = i + 1
            label = f"br{i}"
            filt, pos = self._broll_layer(
                item, i, width, height, inp, label, default_mode, max_opacity, t0
            )
            parts.append(filt)
            out = "vout" if i == n - 1 else f"vb{i}"
            parts.append(
                f"[{current}][{label}]overlay={pos}:enable='between(t\\,{t0}\\,{t1})'[{out}]"
            )
            current = out
        return ";".join(parts)

    def overlay_broll_images(
        self,
        video_path: str,
        output_path: str,
        inserts: List[Dict],
        width: int = 1080,
        height: int = 1920,
        mode: str = "flash",
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Photo inserts timed to speech. Each insert picks its own size/position
        (see _broll_layer) — `mode` is only the fallback for inserts that
        don't specify "size" themselves (older cached edit plans).
        """
        valid = [
            i for i in inserts
            if i.get("path") and os.path.isfile(i["path"])
        ][:6]
        if not valid:
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [self.ffmpeg_bin, "-y", "-i", video_path]
        for item in valid:
            # Shift this input's own pts to start at its insert time so it
            # lines up with the main video's clock -- see _broll_layer's
            # docstring for why this matters once the overlay fades in/out.
            cmd.extend([
                "-itsoffset", str(float(item.get("time") or 0)),
                "-loop", "1",
                "-t", str(max(0.5, float(item.get("duration") or 1.5))),
                "-i", item["path"],
            ])

        filter_complex = self._build_broll_filter_complex(
            valid, width, height, mode, max_opacity=0.92
        )
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("overlay_broll_images ffmpeg failed, shipping without B-roll: %s", err)
            if on_fallback:
                on_fallback(f"B-roll not applied (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path

    def overlay_broll_clips(
        self,
        video_path: str,
        output_path: str,
        inserts: List[Dict],
        width: int = 1080,
        height: int = 1920,
        mode: str = "flash",
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Real stock-video B-roll inserts timed to speech — a moving clip
        instead of overlay_broll_images' static photo. Each insert is decoded
        with -stream_loop -1 so it always has live (non-frozen) frames
        available whenever its enable() window activates, regardless of how
        short the source stock clip is or how late in the main video its
        window starts; -shortest on the output still bounds total runtime to
        the base video's length, exactly like the photo overlay path.
        Each insert picks its own size/position (see _broll_layer) — `mode`
        is only the fallback for inserts that don't specify "size"
        themselves (older cached edit plans).
        """
        valid = [
            i for i in inserts
            if i.get("path") and os.path.isfile(i["path"])
        ][:6]
        if not valid:
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [self.ffmpeg_bin, "-y", "-i", video_path]
        for item in valid:
            # Same pts alignment as overlay_broll_images -- see _broll_layer's
            # docstring. Without it a looped clip's fade-out (which runs on
            # its own decode-from-0 clock) can finish and sit at zero alpha
            # long before its enable() window ever opens.
            cmd.extend([
                "-itsoffset", str(float(item.get("time") or 0)),
                "-stream_loop", "-1", "-i", item["path"],
            ])

        filter_complex = self._build_broll_filter_complex(
            valid, width, height, mode, max_opacity=0.98
        )
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("overlay_broll_clips ffmpeg failed, shipping without video B-roll: %s", err)
            if on_fallback:
                on_fallback(f"video B-roll not applied (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path

    def mix_background_music(
        self,
        video_path: str,
        music_path: str,
        output_path: str,
        music_volume: float = 0.14,
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Mix bed under voice with soft ducking via sidechain when available.

        The voice track is loudness-normalized (EBU R128, single-pass) before
        mixing so output loudness is consistent across generations regardless
        of how quiet/loud the original upload was — previously music volume
        was a flat multiplier applied blindly on top of whatever the source
        loudness happened to be.
        """
        if not music_path or not os.path.isfile(music_path):
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        vol = min(max(music_volume, 0.04), 0.35)
        loudnorm = "loudnorm=I=-16:TP=-1.5:LRA=11"

        # Prefer sidechain ducking; fall back to quiet amix
        filter_duck = (
            f"[1:a]volume={vol * 2:.3f},aloop=loop=-1:size=2e+09,aformat=fltp[music];"
            f"[0:a]{loudnorm},asplit=2[voice][sc];"
            f"[music][sc]sidechaincompress=threshold=0.04:ratio=7:attack=25:release=280:makeup=1[ducked];"
            f"[voice][ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_duck,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return output_path
        duck_err = (result.stderr or "")[-300:]

        filter_simple = (
            f"[0:a]{loudnorm}[voice];"
            f"[1:a]volume={vol:.3f},aloop=loop=-1:size=2e+09[bg];"
            f"[voice][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_simple,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning(
                "mix_background_music ffmpeg failed (duck: %s | simple: %s), shipping without music",
                duck_err, err,
            )
            if on_fallback:
                on_fallback(f"music not mixed (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path

    def overlay_audio_stings(
        self,
        video_path: str,
        output_path: str,
        stings: List[Dict],
        on_fallback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Mix short one-shot SFX (whoosh on a zoom, pop on a hook…) into the
        existing audio track at given timestamps. stings: [{time, path, volume}].
        Video stream is untouched (-c:v copy); only audio is re-encoded.
        """
        valid = [
            s for s in stings
            if s.get("path") and os.path.isfile(s["path"])
        ][:8]
        if not valid:
            shutil.copy2(video_path, output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cmd = [self.ffmpeg_bin, "-y", "-i", video_path]
        for s in valid:
            cmd.extend(["-i", s["path"]])

        parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[base_a]"]
        mix_inputs = ["[base_a]"]
        for i, s in enumerate(valid):
            t0 = max(0.0, float(s.get("time") or 0))
            vol = min(max(float(s.get("volume") or 0.5), 0.05), 1.0)
            delay_ms = int(t0 * 1000)
            parts.append(
                f"[{i + 1}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"volume={vol},adelay={delay_ms}:all=1[sfx{i}]"
            )
            mix_inputs.append(f"[sfx{i}]")
        parts.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:"
            f"dropout_transition=0,alimiter=limit=0.95[aout]"
        )
        cmd.extend([
            "-filter_complex", ";".join(parts),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            output_path,
        ])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or "")[-300:]
            logger.warning("overlay_audio_stings ffmpeg failed, shipping without SFX: %s", err)
            if on_fallback:
                on_fallback(f"SFX not applied (ffmpeg error): {err[:150]}")
            shutil.copy2(video_path, output_path)
            return output_path
        return output_path
