"""
Edit Decision List (EDL) Builder
Converts silence intervals into a list of "keep" segments.
This is the bridge between detection and ffmpeg cutting.
"""

from dataclasses import dataclass
from typing import List
from .detector import SilenceInterval, DetectionConfig


@dataclass
class Segment:
    start: float    # seconds
    end: float      # seconds
    label: str      # "speech" or "silence"

    @property
    def duration(self) -> float:
        return self.end - self.start

    def __repr__(self):
        return f"Segment({self.label}, {self.start:.2f}s → {self.end:.2f}s, {self.duration:.2f}s)"


class EDLBuilder:
    """
    Converts silence intervals into a clean EDL (Edit Decision List).

    The EDL tells FFmpeg exactly which portions to KEEP.
    Optionally pads silence edges so cuts don't feel abrupt.

    Example output:
        [
          Segment(speech, 0.00 → 3.15),
          Segment(speech, 5.30 → 12.40),
          Segment(speech, 14.10 → 22.00),
        ]
    """

    def __init__(self, config: DetectionConfig = None):
        self.config = config or DetectionConfig()

    def build(
        self,
        silence_intervals: List[SilenceInterval],
        total_duration: float,
        padding: float = None
    ) -> List[Segment]:
        """
        Build list of segments to KEEP (speech segments).

        Args:
            silence_intervals: detected silences from SilenceDetector
            total_duration: total video duration in seconds
            padding: seconds of silence to preserve at edges (natural breath feel)
        """
        pad = padding if padding is not None else self.config.padding
        min_keep = self.config.min_keep_duration

        if not silence_intervals:
            return [Segment(0.0, total_duration, "speech")]

        # Sort by start time
        silences = sorted(silence_intervals, key=lambda x: x.start)

        # Apply padding — shrink each silence interval inward
        padded_silences = []
        for s in silences:
            new_start = s.start + pad
            new_end = s.end - pad
            if new_end > new_start:  # only if silence is long enough after padding
                padded_silences.append((new_start, new_end))

        # Build keep segments from the gaps
        keep_segments = []
        cursor = 0.0

        for (cut_start, cut_end) in padded_silences:
            if cut_start > cursor:
                seg_duration = cut_start - cursor
                if seg_duration >= min_keep:
                    keep_segments.append(Segment(cursor, cut_start, "speech"))
            cursor = cut_end

        # Add final segment if there's remaining content
        if cursor < total_duration:
            seg_duration = total_duration - cursor
            if seg_duration >= min_keep:
                keep_segments.append(Segment(cursor, total_duration, "speech"))

        print(f"[EDL] Built {len(keep_segments)} keep segments")
        print(f"[EDL] Original duration: {total_duration:.1f}s")
        print(f"[EDL] Kept duration: {sum(s.duration for s in keep_segments):.1f}s")
        time_saved = total_duration - sum(s.duration for s in keep_segments)
        print(f"[EDL] Time removed: {time_saved:.1f}s ({100*time_saved/total_duration:.1f}%)")

        return keep_segments

    def to_ffmpeg_select_filter(self, segments: List[Segment]) -> str:
        """
        Convert segments to an FFmpeg select filter expression.
        Used for frame-accurate cutting.

        Returns something like:
            "between(t,0,3.15)+between(t,5.30,12.40)+between(t,14.10,22.00)"
        """
        parts = [f"between(t,{s.start:.4f},{s.end:.4f})" for s in segments]
        return "+".join(parts)

    def to_timestamps(self, segments: List[Segment]) -> List[dict]:
        """Return segments as plain dicts — useful for APIs and frontends."""
        return [{"start": s.start, "end": s.end, "duration": s.duration} for s in segments]
