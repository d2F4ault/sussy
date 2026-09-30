"""Configuration management for Midiano Batch Renderer.

Maps notebook Cell 2 configurations into a persistent dataclass with JSON serialization.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def get_default_app_dir() -> Path:
    """Return user data directory for midiano renderer settings and logs."""
    home = Path.home()
    app_dir = home / ".midiano_renderer"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "logs").mkdir(parents=True, exist_ok=True)
    (app_dir / "bin").mkdir(parents=True, exist_ok=True)
    return app_dir


@dataclass
class RenderConfig:
    """Configuration options mapped from Notebook Cell 2 & Cell 3.
    
    Notebook Cell 2 mappings:
        - MIDI_DIR -> midi_dir
        - OUT_DIR -> out_dir
        - FPS -> fps
        - VIEWPORT_W -> viewport_w
        - VIEWPORT_H -> viewport_h
        - TEST_SECONDS -> test_seconds
        - CRF -> crf
        - PRESET -> nvenc_preset / cpu_preset
        - LOG_EVERY -> log_every
        - APP_URL -> app_url
        
    Notebook Cell 3 mappings:
        - XVFB_DISPLAY -> xvfb_display
        - CAPTURE_FPS -> capture_fps
        - NVENC_PRESET -> nvenc_preset
        - NVENC_TUNE -> nvenc_tune
        - NVENC_BITRATE -> nvenc_bitrate
        - FFMPEG_WARMUP_SEC -> ffmpeg_warmup_sec
        - FFMPEG_STOP_TIMEOUT -> ffmpeg_stop_timeout
    """
    # Folders
    midi_dir: str = ""
    out_dir: str = ""

    # Dimensions & Display
    viewport_w: int = 1920
    viewport_h: int = 1200
    fps: int = 60
    capture_fps: int = 30
    xvfb_display: str = ":99"
    use_swiftshader: bool = True  # Notebook Cell 3: --use-gl=swiftshader

    # Capture limits
    test_seconds: Optional[float] = None  # None for full song, or float (e.g. 15.0)
    skip_existing: bool = True

    # Encoding options
    encoder_mode: str = "auto"  # "auto", "h264_nvenc", "libx264"
    nvenc_preset: str = "p4"    # p1 to p7 (default p4 from notebook)
    nvenc_tune: str = "hq"      # hq, ll, ull, lossless (default hq)
    nvenc_bitrate: str = "5M"   # default 5M from notebook
    cpu_preset: str = "veryfast"
    crf: int = 17               # Notebook Cell 2 default
    rate_control: str = "bitrate"  # "bitrate" or "crf"

    # Timeouts & intervals
    ffmpeg_warmup_sec: float = 0.3
    ffmpeg_stop_timeout: float = 20.0
    log_interval_sec: float = 5.0
    app_url: str = "https://app.midiano.com"

    # Custom paths
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # GUI / logging preferences
    log_verbosity: str = "INFO"
    auto_scroll_logs: bool = True
    theme: str = "dark"

    @classmethod
    def get_config_file_path(cls) -> Path:
        return get_default_app_dir() / "config.json"

    @classmethod
    def load(cls) -> RenderConfig:
        """Load configuration from disk, falling back to defaults if not found."""
        config_path = cls.get_config_file_path()
        if not config_path.exists():
            return cls()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Filter keys to match dataclass fields
            valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**filtered)
        except Exception:
            return cls()

    def save(self) -> None:
        """Save configuration to disk."""
        config_path = self.get_config_file_path()
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            print(f"Failed to save config: {e}")
