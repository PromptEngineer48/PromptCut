"""
SilenceRemover — Main Pipeline
The single entry point that glues everything together.

Usage:
    remover = SilenceRemover()
    result = remover.process("input.mp4", "output.mp4")
    print(result)
"""

import librosa
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from .detector import SilenceDetector, DetectionConfig, SilenceInterval
from .edl_builder import EDLBuilder, Segment
from .exporter import FFmpegExporter


@dataclass
class ProcessingResult:
    output_path: str
    original_duration: float
    output_duration: float
    silence_intervals: List[SilenceInterval]
    keep_segments: List[Segment]

    @property
    def time_saved(self) -> float:
        return self.original_duration - self.output_duration

    @property
    def percent_removed(self) -> float:
        if self.original_duration == 0:
            return 0
        return 100 * self.time_saved / self.original_duration

    def summary(self) -> str:
        return (
            f"\n{'='*50}\n"
            f"  Silence Removal Complete\n"
            f"{'='*50}\n"
            f"  Original duration : {self.original_duration:.1f}s\n"
            f"  Output duration   : {self.output_duration:.1f}s\n"
            f"  Time removed      : {self.time_saved:.1f}s ({self.percent_removed:.1f}%)\n"
            f"  Silences found    : {len(self.silence_intervals)}\n"
            f"  Segments kept     : {len(self.keep_segments)}\n"
            f"  Output file       : {self.output_path}\n"
            f"{'='*50}"
        )


class SilenceRemover:
    """
    Full silence removal pipeline.

    Stages:
        1. Load audio from video
        2. Detect silence intervals (SilenceDetector)
        3. Build keep segments / EDL (EDLBuilder)
        4. Export trimmed video (FFmpegExporter)

    Quick start:
        remover = SilenceRemover()
        result = remover.process("raw_podcast.mp4", "clean_podcast.mp4")

    Custom config:
        config = DetectionConfig(
            silence_threshold_db=-40.0,
            min_silence_duration=0.5,
            padding=0.08
        )
        remover = SilenceRemover(config=config)
        result = remover.process("input.mp4", "output.mp4")
    """

    def __init__(
        self,
        config: Optional[DetectionConfig] = None,
        ffmpeg_path: str = "ffmpeg"
    ):
        self.config = config or DetectionConfig()
        self.detector = SilenceDetector(self.config)
        self.edl_builder = EDLBuilder(self.config)
        self.exporter = FFmpegExporter(ffmpeg_path)

    def process(
        self,
        input_path: str,
        output_path: str,
        stream_copy: bool = False,
        on_progress: Optional[Callable[[float], None]] = None,
        dry_run: bool = False
    ) -> ProcessingResult:
        """
        Full pipeline: detect silences → build EDL → export.

        Args:
            input_path: path to input video (.mp4, .mov, .mkv, etc.)
            output_path: where to save the result
            stream_copy: skip re-encoding (faster, but may glitch at cut points)
            on_progress: optional callback(percent: float) for UI progress bars
            dry_run: if True, detect only — don't export video
        """
        print(f"\n[SilenceRemover] Processing: {input_path}")

        # Stage 1: Load audio
        if on_progress: on_progress(5)
        print("[Stage 1/3] Loading audio...")
        y, sr = librosa.load(input_path, sr=None, mono=True)
        total_duration = len(y) / sr
        print(f"           Duration: {total_duration:.1f}s")

        # Stage 2: Detect silences
        if on_progress: on_progress(20)
        print("[Stage 2/3] Detecting silences...")
        silence_intervals = self.detector.detect_from_array(y, sr)

        # Stage 3: Build EDL
        if on_progress: on_progress(40)
        print("[Stage 3/3] Building edit list...")
        keep_segments = self.edl_builder.build(silence_intervals, total_duration)

        output_duration = sum(s.duration for s in keep_segments)

        result = ProcessingResult(
            output_path=output_path,
            original_duration=total_duration,
            output_duration=output_duration,
            silence_intervals=silence_intervals,
            keep_segments=keep_segments
        )

        if dry_run:
            print("[SilenceRemover] Dry run — skipping export.")
            print(result.summary())
            return result

        # Stage 4: Export
        if on_progress: on_progress(50)
        print("[Stage 4/4] Exporting video...")
        self.exporter.export_concat(
            input_path=input_path,
            segments=keep_segments,
            output_path=output_path,
            stream_copy=stream_copy,
            on_progress=lambda p: on_progress(50 + p * 0.5) if on_progress else None
        )

        if on_progress: on_progress(100)
        print(result.summary())
        return result

    def preview(self, input_path: str) -> ProcessingResult:
        """
        Detect silences without exporting — useful for UI previews.
        Returns full result so you can show the user what will be cut.
        """
        return self.process(input_path, "", dry_run=True)
