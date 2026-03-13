"""
FFmpeg Exporter
Takes an EDL (keep segments) and produces a clean output video.
Uses concat demuxer approach — most reliable for long videos.
"""

import subprocess
import tempfile
import os
from pathlib import Path
from typing import List
from .edl_builder import Segment


class FFmpegExporter:
    """
    Applies an EDL to a video file using FFmpeg.

    Two strategies:
    1. CONCAT (default) — writes a concat list, re-encodes or copies streams
       - Most reliable, handles any format
       - Fast with stream copy (no re-encode)

    2. SELECT FILTER — uses select/aselect filters
       - Frame-accurate but slow (must decode everything)
       - Use when concat produces glitches
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path
        self._verify_ffmpeg()

    def _verify_ffmpeg(self):
        try:
            subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True, check=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "FFmpeg not found. Install with: sudo apt install ffmpeg  "
                "or brew install ffmpeg"
            )

    def export_concat(
        self,
        input_path: str,
        segments: List[Segment],
        output_path: str,
        stream_copy: bool = False,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        crf: int = 18,
        preset: str = "fast",
        on_progress=None
    ) -> str:
        """
        Export video keeping only the specified segments.

        Args:
            input_path: source video file
            segments: list of Segment objects to keep
            output_path: where to write the result
            stream_copy: if True, copy streams without re-encoding (fastest, may glitch)
            video_codec: codec for re-encode (libx264, libx265, etc.)
            audio_codec: audio codec (aac, mp3, copy)
            crf: quality 0-51, lower = better (18 is near-lossless)
            preset: encoding speed (ultrafast, fast, medium, slow)
            on_progress: optional callback(percent: float)
        """
        print(f"[Exporter] Cutting {len(segments)} segments from {input_path}")

        # Get video duration for progress tracking
        duration = self._get_duration(input_path)

        # Write FFmpeg concat script to a temp file
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False
        ) as f:
            concat_file = f.name
            for seg in segments:
                f.write(f"file '{os.path.abspath(input_path)}'\n")
                f.write(f"inpoint {seg.start:.6f}\n")
                f.write(f"outpoint {seg.end:.6f}\n")

        try:
            cmd = [
                self.ffmpeg_path,
                "-y",                          # overwrite output
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
            ]

            if stream_copy:
                cmd += ["-c", "copy"]
            else:
                cmd += [
                    "-c:v", video_codec,
                    "-crf", str(crf),
                    "-preset", preset,
                    "-c:a", audio_codec,
                    "-b:a", "192k",
                    "-movflags", "+faststart",  # good for web playback
                ]

            cmd += [output_path]

            print(f"[Exporter] Running FFmpeg...")
            print(f"[Exporter] Command: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            stdout, stderr = process.communicate()

            if process.returncode != 0:
                print(f"[Exporter] FFmpeg stderr:\n{stderr}")
                raise RuntimeError(f"FFmpeg failed with code {process.returncode}")

            output_size = Path(output_path).stat().st_size / (1024 * 1024)
            print(f"[Exporter] Done! Output: {output_path} ({output_size:.1f} MB)")
            return output_path

        finally:
            os.unlink(concat_file)

    def export_with_select_filter(
        self,
        input_path: str,
        segments: List[Segment],
        output_path: str,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        crf: int = 18,
    ) -> str:
        """
        Frame-accurate export using FFmpeg select filter.
        Slower but more precise than concat for short segments.
        """
        from .edl_builder import EDLBuilder
        edl = EDLBuilder()
        select_expr = edl.to_ffmpeg_select_filter(segments)

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", input_path,
            "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            "-af", f"aselect='{select_expr}',asetpts=N/SR/TB",
            "-c:v", video_codec,
            "-crf", str(crf),
            "-c:a", audio_codec,
            output_path
        ]

        print(f"[Exporter] Running select filter export (frame-accurate)...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed:\n{result.stderr}")

        return output_path

    def _get_duration(self, path: str) -> float:
        """Get video duration in seconds via ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0
