"""
Silence Detector — Core Engine
Analyzes audio and returns silence intervals with timestamps.
"""

import numpy as np
import librosa
from dataclasses import dataclass
from typing import List


@dataclass
class SilenceInterval:
    start: float   # seconds
    end: float     # seconds
    db_level: float  # average dB in this interval

    @property
    def duration(self) -> float:
        return self.end - self.start

    def __repr__(self):
        return f"SilenceInterval({self.start:.2f}s → {self.end:.2f}s, {self.duration:.2f}s, {self.db_level:.1f}dB)"


@dataclass
class DetectionConfig:
    silence_threshold_db: float = -35.0   # dB below this = silence
    min_silence_duration: float = 0.4     # seconds — shorter silences are kept
    min_keep_duration: float = 0.1        # seconds — don't keep tiny speech fragments
    frame_length: int = 2048              # FFT frame size
    hop_length: int = 512                 # hop between frames (~11ms at 44100Hz)
    padding: float = 0.05                 # seconds of silence to keep at edges of cuts


class SilenceDetector:
    """
    Detects silence intervals in an audio file.

    Usage:
        detector = SilenceDetector()
        intervals = detector.detect("video.mp4")
    """

    def __init__(self, config: DetectionConfig = None):
        self.config = config or DetectionConfig()

    def detect(self, audio_path: str) -> List[SilenceInterval]:
        """
        Load audio and return list of silence intervals.
        Works with .mp4, .mp3, .wav, .m4a — librosa handles extraction.
        """
        print(f"[Detector] Loading audio from: {audio_path}")
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        print(f"[Detector] Loaded {len(y)/sr:.1f}s of audio at {sr}Hz")

        return self._find_silences(y, sr)

    def detect_from_array(self, y: np.ndarray, sr: int) -> List[SilenceInterval]:
        """Detect silences from a pre-loaded numpy array."""
        return self._find_silences(y, sr)

    def _find_silences(self, y: np.ndarray, sr: int) -> List[SilenceInterval]:
        cfg = self.config

        # Compute RMS energy per frame
        rms = librosa.feature.rms(
            y=y,
            frame_length=cfg.frame_length,
            hop_length=cfg.hop_length
        )[0]

        # Convert to dB (avoid log(0))
        rms_db = librosa.amplitude_to_db(rms + 1e-9, ref=np.max)

        # Boolean mask: True = silent frame
        is_silent = rms_db < cfg.silence_threshold_db

        # Convert frame indices to time
        frame_times = librosa.frames_to_time(
            np.arange(len(rms)),
            sr=sr,
            hop_length=cfg.hop_length
        )

        # Group consecutive silent frames into intervals
        intervals = self._group_intervals(is_silent, frame_times, rms_db)

        # Filter by minimum silence duration
        intervals = [
            iv for iv in intervals
            if iv.duration >= cfg.min_silence_duration
        ]

        print(f"[Detector] Found {len(intervals)} silence intervals")
        return intervals

    def _group_intervals(
        self,
        is_silent: np.ndarray,
        frame_times: np.ndarray,
        rms_db: np.ndarray
    ) -> List[SilenceInterval]:
        """Group consecutive silent frames into SilenceInterval objects."""
        intervals = []
        in_silence = False
        start_idx = 0

        for i, silent in enumerate(is_silent):
            if silent and not in_silence:
                in_silence = True
                start_idx = i
            elif not silent and in_silence:
                in_silence = False
                avg_db = float(np.mean(rms_db[start_idx:i]))
                intervals.append(SilenceInterval(
                    start=float(frame_times[start_idx]),
                    end=float(frame_times[i - 1]),
                    db_level=avg_db
                ))

        # Handle silence at end of file
        if in_silence:
            avg_db = float(np.mean(rms_db[start_idx:]))
            intervals.append(SilenceInterval(
                start=float(frame_times[start_idx]),
                end=float(frame_times[-1]),
                db_level=avg_db
            ))

        return intervals
