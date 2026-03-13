# Silence Remover — FireCut-style Backend

A production-ready silence removal engine built in Python.

## Architecture

```
silence_remover/
├── core/
│   ├── detector.py      # Audio analysis → silence timestamps
│   ├── edl_builder.py   # Silence timestamps → keep segments (EDL)
│   ├── exporter.py      # EDL + video → trimmed output (FFmpeg)
│   └── pipeline.py      # Orchestrates all 3 stages
├── api/
│   └── server.py        # FastAPI REST API
├── tests/
│   └── test_silence_remover.py
├── cli.py               # Command-line interface
└── requirements.txt
```

## How It Works

```
Video/Audio File
      │
      ▼
┌─────────────────┐
│  librosa.load() │  ← Extract audio waveform as numpy array
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│  SilenceDetector    │  ← Compute RMS energy per frame
│                     │     Convert to dB
│                     │     Find frames below threshold
│                     │     Group into intervals
└────────┬────────────┘
         │  List[SilenceInterval]
         │  e.g. [(3.2s→5.8s), (12.1s→13.0s)]
         ▼
┌─────────────────────┐
│  EDLBuilder         │  ← Invert: silence → keep segments
│                     │     Apply padding at edges
│                     │     Filter tiny fragments
└────────┬────────────┘
         │  List[Segment] (the parts to KEEP)
         │  e.g. [(0→3.2s), (5.8→12.1s), (13.0→end)]
         ▼
┌─────────────────────┐
│  FFmpegExporter     │  ← Write concat script
│                     │     Run FFmpeg
│                     │     Re-encode or stream copy
└────────┬────────────┘
         │
         ▼
   output_video.mp4
```

## Quick Start

### Install

```bash
pip install -r requirements.txt
sudo apt install ffmpeg  # or: brew install ffmpeg
```

### CLI

```bash
# Basic usage
python cli.py input.mp4 output.mp4

# Preview what will be cut (no export)
python cli.py input.mp4 --preview

# Aggressive cutting (more silence removed)
python cli.py talk.mp4 clean.mp4 --threshold -45 --min-silence 0.3

# Fast mode (no re-encode, may glitch at cuts)
python cli.py podcast.mp4 out.mp4 --stream-copy

# Keep more natural breathing room
python cli.py video.mp4 out.mp4 --padding 0.1
```

### Python API

```python
from core.pipeline import SilenceRemover
from core.detector import DetectionConfig

# Default settings
remover = SilenceRemover()
result = remover.process("input.mp4", "output.mp4")
print(result.summary())

# Custom config
config = DetectionConfig(
    silence_threshold_db=-40.0,   # dB — lower = more aggressive
    min_silence_duration=0.5,     # seconds — only cut silences longer than this
    padding=0.08,                 # seconds — keep this much silence at cut edges
)
remover = SilenceRemover(config=config)
result = remover.process("podcast.mp4", "podcast_clean.mp4")

# Dry run — detect only, no export
result = remover.preview("video.mp4")
print(f"Would remove {result.time_saved:.1f}s ({result.percent_removed:.0f}%)")
```

### REST API

```bash
# Start the server
uvicorn api.server:app --reload --port 8000
```

**Detect silences (no export):**
```bash
curl -X POST http://localhost:8000/detect \
  -F "file=@video.mp4" \
  -F "threshold_db=-35" \
  -F "min_silence_duration=0.4"
```

**Process video (async):**
```bash
# Submit job
JOB=$(curl -X POST http://localhost:8000/process \
  -F "file=@video.mp4" \
  -F "threshold_db=-35" | jq -r .job_id)

# Poll status
curl http://localhost:8000/jobs/$JOB

# Download when done
curl http://localhost:8000/jobs/$JOB/download -o output.mp4
```

## Config Reference

| Parameter | Default | Description |
|---|---|---|
| `silence_threshold_db` | -35.0 | dB level below which audio is "silent". -35 is good for most speech. Use -40 to -50 for noisier recordings. |
| `min_silence_duration` | 0.4 | Only cut silences longer than this (seconds). Prevents cutting natural pauses. |
| `padding` | 0.05 | Keep this much silence at each edge of a cut (seconds). Prevents abrupt cuts. |
| `min_keep_duration` | 0.1 | Don't keep speech segments shorter than this — they're likely artifacts. |

## Run Tests

```bash
pytest tests/ -v
```

## Extending This

Next features to add (in order of complexity):

1. **Remove filler words** — add WhisperX transcription, detect "um/uh/like" by word timestamps
2. **Remove repetition** — transcribe → sentence embeddings → find similar sentences
3. **Add captions** — use transcript word timestamps → render styled subtitles via FFmpeg
4. **Find B-roll keywords** — transcribe → NLP keyword extraction → query Pexels API
