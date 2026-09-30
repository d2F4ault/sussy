"""Midiano Batch Renderer — Desktop GUI & CLI Application Entry Point.

Can be run via:
  python main.py                    # Launches modern CustomTkinter dark GUI
  python main.py --cli -i /m -o /o  # Runs headless CLI batch (Codespaces / servers)
  python main.py --check-deps       # Tests environment, GPU, FFmpeg, Playwright
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure package root is in sys.path
package_root = Path(__file__).resolve().parent.parent
if str(package_root) not in sys.path:
    sys.path.insert(0, str(package_root))

from midiano_renderer.config import RenderConfig
from midiano_renderer.core.ffmpeg_utils import ffmpeg_manager
from midiano_renderer.core.renderer import BatchRenderer
from midiano_renderer.core.xvfb import is_linux_or_wsl, is_xvfb_available
from midiano_renderer.utils.logging import logger


def check_dependencies() -> int:
    """Print system diagnostics and verify dependencies."""
    print("=" * 70)
    print("MIDIANO BATCH RENDERER — SYSTEM DIAGNOSTICS")
    print("=" * 70)
    print(f"Platform       : {sys.platform} ({os.name})")
    print(f"Python         : {sys.version.split()[0]}")

    # Check FFmpeg
    ffmpeg_bin = ffmpeg_manager.get_ffmpeg_binary()
    ffprobe_bin = ffmpeg_manager.get_ffprobe_binary()
    print(f"FFmpeg Binary  : {ffmpeg_bin}")
    print(f"FFprobe Binary : {ffprobe_bin}")

    has_nvenc = ffmpeg_manager.has_nvenc()
    print(f"NVENC Support  : {'YES (h264_nvenc)' if has_nvenc else 'NO (libx264 CPU fallback)'}")

    # Check GPU
    gpu_info = ffmpeg_manager.query_gpu_info()
    if gpu_info.get("available") == "True":
        print(f"NVIDIA GPU     : {gpu_info.get('name')} (Driver: {gpu_info.get('driver')})")
    else:
        print("NVIDIA GPU     : None detected")

    # Check Xvfb (Linux/WSL)
    if is_linux_or_wsl():
        xvfb_ok = is_xvfb_available()
        print(f"Xvfb Display   : {'Installed' if xvfb_ok else 'NOT FOUND (install via apt-get install xvfb)'}")
    else:
        print(f"Xvfb Display   : N/A ({sys.platform} uses native desktop window capture)")

    # Check Playwright
    try:
        import playwright
        print(f"Playwright     : Installed ({getattr(playwright, '__version__', 'ok')})")
    except ImportError:
        print("Playwright     : NOT INSTALLED (run: pip install playwright && python -m playwright install chromium)")

    # Check mido
    try:
        import mido
        print(f"mido           : Installed ({getattr(mido, '__version__', 'ok')})")
    except ImportError:
        print("mido           : NOT INSTALLED (run: pip install mido)")

    print("=" * 70)
    return 0


def run_cli_batch(args: argparse.Namespace) -> int:
    """Run batch rendering directly from terminal (ideal for Codespaces or automated pipelines)."""
    cfg = RenderConfig.load()
    if args.input:
        cfg.midi_dir = str(Path(args.input).resolve())
    if args.output:
        cfg.out_dir = str(Path(args.output).resolve())
    if args.fps:
        cfg.fps = args.fps
    if args.test is not None:
        cfg.test_seconds = args.test if args.test > 0 else None
    if args.encoder:
        cfg.encoder_mode = args.encoder
    if args.preset:
        cfg.nvenc_preset = args.preset
    if args.bitrate:
        cfg.nvenc_bitrate = args.bitrate

    if not cfg.midi_dir or not Path(cfg.midi_dir).is_dir():
        logger.error(f"Invalid MIDI directory: {cfg.midi_dir}")
        return 1
    if not cfg.out_dir:
        logger.error("Missing output directory.")
        return 1

    renderer = BatchRenderer(cfg)
    try:
        asyncio.run(renderer.run_batch())
        return 0
    except KeyboardInterrupt:
        logger.warning("Cancelled by user (SIGINT).")
        renderer.cancel()
        return 130
    except Exception as e:
        logger.error(f"CLI batch failed: {e}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Midiano Batch Renderer — Desktop GUI & CLI Studio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cli", action="store_true", help="Run in headless terminal mode without launching GUI")
    parser.add_argument("-i", "--input", type=str, help="Input directory containing .mid / .midi files")
    parser.add_argument("-o", "--output", type=str, help="Output directory for rendered MP4 files")
    parser.add_argument("--fps", type=int, choices=[30, 60], help="Target video FPS (30 or 60)")
    parser.add_argument("--test", type=float, help="Render only first N seconds (e.g. 15), or 0 for full song")
    parser.add_argument("--encoder", type=str, choices=["auto", "h264_nvenc", "libx264"], help="Encoder selection")
    parser.add_argument("--preset", type=str, help="NVENC preset (p1-p7)")
    parser.add_argument("--bitrate", type=str, help="Target bitrate (e.g. 5M, 10M)")
    parser.add_argument("--check-deps", action="store_true", help="Perform environment and dependency diagnostics")
    parser.add_argument("--download-ffmpeg", action="store_true", help="Download static GPL build of FFmpeg with NVENC enabled")
    parser.add_argument("--server", action="store_true", help="Launch the high-throughput cloud rendering agent (Codespaces)")
    parser.add_argument("--port", type=int, default=8000, help="Port for cloud rendering agent (default 8000)")

    args = parser.parse_args()

    if args.check_deps:
        sys.exit(check_dependencies())

    if args.download_ffmpeg:
        ok = ffmpeg_manager.download_static_ffmpeg()
        sys.exit(0 if ok else 1)

    if args.server:
        from midiano_renderer.cloud_server import run_server
        run_server(port=args.port)
        sys.exit(0)

    if args.cli or (args.input and args.output):
        sys.exit(run_cli_batch(args))
    else:
        from midiano_renderer.gui.app import run_gui
        run_gui()


if __name__ == "__main__":
    main()
