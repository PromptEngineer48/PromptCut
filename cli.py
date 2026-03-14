#!/usr/bin/env python3
"""
Silence Remover — CLI
Process videos directly from the terminal.

Usage:
    python cli.py input.mp4 output.mp4
    python cli.py input.mp4 output.mp4 --threshold -40 --min-silence 0.5
    python cli.py input.mp4 --preview          # dry run, no export
    python cli.py input.mp4 --stream-copy      # fast mode, no re-encode
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from detector import DetectionConfig
from pipeline import SilenceRemover


def main():
    parser = argparse.ArgumentParser(
        description="Remove silences from video files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py talk.mp4 clean.mp4
  python cli.py podcast.mp4 podcast_cut.mp4 --threshold -40 --min-silence 0.6
  python cli.py interview.mp4 --preview
  python cli.py video.mp4 out.mp4 --stream-copy --padding 0.1
        """
    )

    default_config = DetectionConfig()

    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", nargs="?", help="Output video file (required unless --preview)")

    parser.add_argument(
        "--threshold", type=float, default=default_config.silence_threshold_db,
        help=f"Silence threshold in dB (default: {default_config.silence_threshold_db}). Lower = more aggressive cutting."
    )
    parser.add_argument(
        "--min-silence", type=float, default=default_config.min_silence_duration,
        help=f"Minimum silence duration to cut (seconds, default: {default_config.min_silence_duration})"
    )
    parser.add_argument(
        "--padding", type=float, default=default_config.padding,
        help=f"Seconds of silence to keep at cut edges (default: {default_config.padding})"
    )
    parser.add_argument(
        "--stream-copy", action="store_true",
        help="Copy streams without re-encoding (faster, may glitch at cuts)"
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Detect only, don't export. Shows what would be cut."
    )
    parser.add_argument(
        "--enhance-audio", "-ea", action="store_true",
        help="Apply CapCut-style audio enhancements (compression, EQ, normalization)"
    )

    args = parser.parse_args()

    # Validate
    if not args.preview and not args.output:
        parser.error("output path is required unless using --preview")

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    # Build config
    config = DetectionConfig(
        silence_threshold_db=args.threshold,
        min_silence_duration=args.min_silence,
        padding=args.padding
    )

    remover = SilenceRemover(config=config)

    print(f"Settings:")
    print(f"  Threshold     : {args.threshold} dB")
    print(f"  Min silence   : {args.min_silence}s")
    print(f"  Padding       : {args.padding}s")
    print(f"  Stream copy   : {args.stream_copy}")
    print(f"  Enhance audio : {args.enhance_audio}")

    if args.preview:
        result = remover.preview(args.input)
    else:
        result = remover.process(
            input_path=args.input,
            output_path=args.output,
            stream_copy=args.stream_copy,
            enhance_audio=args.enhance_audio
        )

    # Show silence intervals
    print(f"\nSilence intervals detected:")
    for i, s in enumerate(result.silence_intervals, 1):
        print(f"  {i:3d}. {s.start:7.2f}s -> {s.end:7.2f}s  ({s.duration:.2f}s, {s.db_level:.1f}dB)")


if __name__ == "__main__":
    main()
