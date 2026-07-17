import os
import uuid
import subprocess
import json
from typing import Optional, List, Dict
from app.core.config import settings


class FFmpegService:
    """Service for video processing with FFmpeg."""
    
    def __init__(self):
        self.output_dir = settings.GENERATED_DIR
        
    def get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            "ffprobe",
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
        words_per_subtitle: int = 8
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
            subtitle_font: Font name or path
            subtitle_font_path: Optional path to custom font file
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
            font_spec = subtitle_font_path if subtitle_font_path else subtitle_font
            
            subtitle_path_escaped = subtitle_path.replace(":", r"\:").replace("\\", "/")
            
            margin_v = {
                "top": 50,
                "center": 0,
                "bottom": 100
            }.get(subtitle_position, 100)
            
            alignment = {
                "top": 6,
                "center": 5,
                "bottom": 2
            }.get(subtitle_position, 2)
            
            force_style = (
                f"FontName={font_spec},"
                f"FontSize={subtitle_font_size},"
                f"PrimaryColour=&H00{self._color_to_ass(subtitle_font_color)},"
                f"OutlineColour=&H80000000,"
                f"BackColour=&H80000000,"
                f"Outline=2,"
                f"Shadow=1,"
                f"MarginV={margin_v},"
                f"Alignment={alignment}"
            )
            
            if subtitle_bg_color:
                force_style += f",BorderStyle=4,BackColour=&H80{self._color_to_ass(subtitle_bg_color)}"
            
            current_stream = video_stream.strip("[]") if video_stream.startswith("[") else "0:v"
            filter_complex.append(
                f"[{current_stream}]subtitles='{subtitle_path_escaped}':force_style='{force_style}'[vout]"
            )
            video_stream = "[vout]"
        
        cmd = ["ffmpeg", "-y"]
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
            "ffmpeg", "-y",
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
        output_path: str
    ) -> str:
        """Combine video with new audio track."""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")
        
        return output_path
    
    def get_file_size(self, file_path: str) -> int:
        """Get file size in bytes."""
        return os.path.getsize(file_path)
