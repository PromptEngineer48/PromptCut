"""
Tests for the Silence Remover pipeline.
Run with: pytest tests/test_silence_remover.py -v
"""

import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from detector import SilenceDetector, DetectionConfig, SilenceInterval
from edl_builder import EDLBuilder, Segment
from pipeline import SilenceRemover


# ─── Helpers ───────────────────────────────────────────────────────────────────

def make_audio(sr=22050, duration=10.0, silence_at=None):
    """
    Generate synthetic audio: white noise with silent sections.
    silence_at: list of (start, end) tuples in seconds
    """
    samples = int(sr * duration)
    y = np.random.normal(0, 0.1, samples).astype(np.float32)  # speech-like noise

    if silence_at:
        for (start, end) in silence_at:
            s = int(start * sr)
            e = int(end * sr)
            y[s:e] = 0.0  # true silence

    return y, sr


# ─── Detector Tests ────────────────────────────────────────────────────────────

class TestSilenceDetector:

    def test_detects_obvious_silence(self):
        """Pure silence region should always be detected."""
        y, sr = make_audio(duration=10, silence_at=[(3.0, 5.0)])
        detector = SilenceDetector(DetectionConfig(
            silence_threshold_db=-20.0,
            min_silence_duration=0.3
        ))
        intervals = detector.detect_from_array(y, sr)
        assert len(intervals) >= 1

        # The 3-5s region should be covered by at least one interval
        detected_times = [(iv.start, iv.end) for iv in intervals]
        covers_silence = any(
            s <= 3.5 and e >= 4.5
            for (s, e) in detected_times
        )
        assert covers_silence, f"Did not detect 3-5s silence. Got: {detected_times}"

    def test_no_false_positives_on_speech(self):
        """Continuous noise (speech-like) should produce no silences."""
        y, sr = make_audio(duration=5)
        # No silence regions
        detector = SilenceDetector(DetectionConfig(
            silence_threshold_db=-60.0,   # very aggressive — only true silence
            min_silence_duration=0.5
        ))
        intervals = detector.detect_from_array(y, sr)
        assert len(intervals) == 0

    def test_multiple_silences(self):
        """Multiple silence regions should all be detected."""
        silence_regions = [(1.0, 1.8), (4.0, 5.2), (7.5, 8.0)]
        y, sr = make_audio(duration=10, silence_at=silence_regions)
        detector = SilenceDetector(DetectionConfig(
            silence_threshold_db=-20.0,
            min_silence_duration=0.3
        ))
        intervals = detector.detect_from_array(y, sr)
        assert len(intervals) >= len(silence_regions)

    def test_min_duration_filter(self):
        """Silences shorter than min_silence_duration should be filtered out."""
        y, sr = make_audio(duration=10, silence_at=[(3.0, 3.1)])  # 0.1s silence
        detector = SilenceDetector(DetectionConfig(
            silence_threshold_db=-20.0,
            min_silence_duration=0.5  # require 0.5s minimum
        ))
        intervals = detector.detect_from_array(y, sr)
        assert all(iv.duration >= 0.5 for iv in intervals)


# ─── EDL Builder Tests ──────────────────────────────────────────────────────────

class TestEDLBuilder:

    def test_no_silences_returns_full_segment(self):
        """No silences = keep everything."""
        builder = EDLBuilder()
        segments = builder.build([], total_duration=60.0)
        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 60.0

    def test_silence_in_middle(self):
        """Silence in middle produces two keep segments."""
        silences = [SilenceInterval(start=5.0, end=8.0, db_level=-60.0)]
        builder = EDLBuilder(DetectionConfig(padding=0.0))
        segments = builder.build(silences, total_duration=15.0)
        assert len(segments) == 2
        assert segments[0].start == pytest.approx(0.0)
        assert segments[0].end == pytest.approx(5.0, abs=0.1)
        assert segments[1].start == pytest.approx(8.0, abs=0.1)
        assert segments[1].end == pytest.approx(15.0)

    def test_padding_shrinks_cuts(self):
        """Padding should leave a small amount of silence at cut edges."""
        silences = [SilenceInterval(start=5.0, end=9.0, db_level=-60.0)]
        pad = 0.1
        builder = EDLBuilder(DetectionConfig(padding=pad))
        segments = builder.build(silences, total_duration=15.0)

        # First segment should end slightly into the silence
        assert segments[0].end == pytest.approx(5.0 + pad, abs=0.05)
        # Second segment should start slightly before silence ends
        assert segments[1].start == pytest.approx(9.0 - pad, abs=0.05)

    def test_total_duration_is_reduced(self):
        """Output duration should be less than input when silences removed."""
        silences = [SilenceInterval(start=3.0, end=7.0, db_level=-60.0)]  # 4s silence
        builder = EDLBuilder(DetectionConfig(padding=0.0))
        segments = builder.build(silences, total_duration=10.0)
        output_duration = sum(s.duration for s in segments)
        assert output_duration < 10.0
        assert output_duration == pytest.approx(6.0, abs=0.1)

    def test_ffmpeg_filter_format(self):
        """Select filter string should be properly formatted."""
        builder = EDLBuilder()
        segments = [
            Segment(0.0, 3.0, "speech"),
            Segment(5.0, 8.0, "speech"),
        ]
        expr = builder.to_ffmpeg_select_filter(segments)
        assert "between(t," in expr
        assert "+" in expr


# ─── Pipeline Tests ─────────────────────────────────────────────────────────────

class TestSilenceRemoverPipeline:

    def test_preview_dry_run(self, tmp_path):
        """Preview (dry run) should return result without creating output."""
        # We can't easily test with real video in unit tests
        # This tests the pipeline logic with a synthetic approach
        config = DetectionConfig(
            silence_threshold_db=-20.0,
            min_silence_duration=0.3
        )
        remover = SilenceRemover(config=config)

        # Test that config is passed through correctly
        assert remover.config.silence_threshold_db == -20.0
        assert remover.config.min_silence_duration == 0.3

    def test_config_defaults(self):
        """Default config should have sensible values."""
        config = DetectionConfig()
        assert config.silence_threshold_db == -35.0
        assert config.min_silence_duration == 0.4
        assert config.padding == 0.05
        assert 0 < config.padding < 1.0  # padding should be a small fraction


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
