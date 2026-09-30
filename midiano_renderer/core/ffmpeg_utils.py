"""FFmpeg and FFprobe process execution, hardware acceleration, and retiming utilities.

Maps directly to Notebook Cell 1 and Cell 3:
  - Notebook Cell 1:
      _ffmpeg_has_nvenc(ffmpeg_bin: str) -> bool
      Static build download fallback logic
      GPU check (nvidia-smi query)
      FFPROBE_BIN resolution
  - Notebook Cell 3:
      start_ffmpeg_x11grab(...)
      _ffmpeg_error_text(proc)
      stop_ffmpeg_gracefully(proc, timeout)
      probe_duration_seconds(path)
      retime_to_duration(src, dst, target_seconds, fps)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from midiano_renderer.config import RenderConfig, get_default_app_dir
from midiano_renderer.utils.logging import logger


class FFmpegManager:
    """Manages FFmpeg/FFprobe binaries, GPU/NVENC hardware capabilities, and process lifecycles."""

    def __init__(self, config: Optional[RenderConfig] = None):
        self.config = config or RenderConfig()
        self._cached_ffmpeg_bin: Optional[str] = None
        self._cached_ffprobe_bin: Optional[str] = None
        self._has_nvenc: Optional[bool] = None
        self._gpu_info: Optional[Dict[str, str]] = None

    def get_ffmpeg_binary(self) -> str:
        """Find the most suitable ffmpeg executable."""
        if self._cached_ffmpeg_bin and Path(self._cached_ffmpeg_bin).exists():
            return self._cached_ffmpeg_bin

        # 1. Custom configured path
        if self.config.ffmpeg_path and Path(self.config.ffmpeg_path).exists():
            self._cached_ffmpeg_bin = self.config.ffmpeg_path
            return self._cached_ffmpeg_bin

        # 2. Local app data bin dir (~/.midiano_renderer/bin/ffmpeg or ffmpeg.exe)
        app_bin_dir = get_default_app_dir() / "bin"
        ext = ".exe" if sys.platform == "win32" else ""
        local_bin = app_bin_dir / f"ffmpeg{ext}"
        if local_bin.exists():
            self._cached_ffmpeg_bin = str(local_bin)
            return self._cached_ffmpeg_bin

        # 3. Static builds folder search (e.g. extracted BtbN builds)
        for cand in app_bin_dir.glob(f"**/ffmpeg{ext}"):
            if cand.is_file():
                self._cached_ffmpeg_bin = str(cand)
                return self._cached_ffmpeg_bin

        # 4. System PATH
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            self._cached_ffmpeg_bin = system_ffmpeg
            return self._cached_ffmpeg_bin

        return "ffmpeg"

    def get_ffprobe_binary(self) -> str:
        """Find the corresponding ffprobe executable.
        
        Mapped from Notebook Cell 1:
          FFPROBE_BIN = "ffprobe" if FFMPEG_BIN == "ffmpeg" else str(Path(FFMPEG_BIN).parent / "ffprobe")
        """
        if self._cached_ffprobe_bin and Path(self._cached_ffprobe_bin).exists():
            return self._cached_ffprobe_bin

        if self.config.ffprobe_path and Path(self.config.ffprobe_path).exists():
            self._cached_ffprobe_bin = self.config.ffprobe_path
            return self._cached_ffprobe_bin

        ffmpeg_bin = self.get_ffmpeg_binary()
        ext = ".exe" if sys.platform == "win32" else ""
        if ffmpeg_bin != "ffmpeg" and Path(ffmpeg_bin).exists():
            sibling = Path(ffmpeg_bin).parent / f"ffprobe{ext}"
            if sibling.exists():
                self._cached_ffprobe_bin = str(sibling)
                return self._cached_ffprobe_bin

        system_ffprobe = shutil.which("ffprobe")
        if system_ffprobe:
            self._cached_ffprobe_bin = system_ffprobe
            return self._cached_ffprobe_bin

        return "ffprobe"

    def is_ffmpeg_installed(self) -> bool:
        """Check if a valid working ffmpeg binary is resolved."""
        bin_path = self.get_ffmpeg_binary()
        if bin_path != "ffmpeg" and Path(bin_path).exists():
            return True
        return shutil.which("ffmpeg") is not None

    def download_static_ffmpeg(self, progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Download static GPL build of FFmpeg with NVENC (BtbN releases) and extract to ~/.midiano_renderer/bin.
        
        Mapped from Notebook Cell 1 lines 36-47:
          wget -q https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz ...
        """
        import urllib.request
        import zipfile
        import tarfile

        bin_dir = get_default_app_dir() / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        if sys.platform == "win32":
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
            archive_path = bin_dir / "ffmpeg-nvenc.zip"
        else:
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
            archive_path = bin_dir / "ffmpeg-nvenc.tar.xz"

        msg = f"Downloading static FFmpeg build with NVENC from {url}..."
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

        try:
            urllib.request.urlretrieve(url, archive_path)
            extract_msg = "Extracting FFmpeg binaries..."
            logger.info(extract_msg)
            if progress_callback:
                progress_callback(extract_msg)

            if sys.platform == "win32":
                with zipfile.ZipFile(archive_path, "r") as zf:
                    for member in zf.namelist():
                        if member.endswith("ffmpeg.exe") or member.endswith("ffprobe.exe"):
                            filename = os.path.basename(member)
                            with zf.open(member) as src, open(bin_dir / filename, "wb") as dst:
                                dst.write(src.read())
            else:
                with tarfile.open(archive_path, "r:*") as tf:
                    for member in tf.getmembers():
                        if member.name.endswith("/ffmpeg") or member.name.endswith("/ffprobe"):
                            filename = os.path.basename(member.name)
                            fobj = tf.extractfile(member)
                            if fobj:
                                target = bin_dir / filename
                                with open(target, "wb") as dst:
                                    dst.write(fobj.read())
                                os.chmod(target, 0o755)

            archive_path.unlink(missing_ok=True)
            self._cached_ffmpeg_bin = None
            self._cached_ffprobe_bin = None
            self._has_nvenc = None
            logger.success(f"Static FFmpeg installed successfully to {bin_dir}")
            return True
        except Exception as e:
            logger.error(f"Failed to download/extract static FFmpeg: {e}")
            return False

    def has_nvenc(self) -> bool:
        """Check if the resolved ffmpeg binary supports h264_nvenc encoder.
        
        Mapped directly from Notebook Cell 1:
          def _ffmpeg_has_nvenc(ffmpeg_bin: str) -> bool:
              out = subprocess.run([ffmpeg_bin, "-hide_banner", "-encoders"], ...)
              return "h264_nvenc" in out.stdout
        """
        if self._has_nvenc is not None:
            return self._has_nvenc

        ffmpeg_bin = self.get_ffmpeg_binary()
        try:
            out = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._has_nvenc = "h264_nvenc" in out.stdout
            return self._has_nvenc
        except Exception:
            self._has_nvenc = False
            return False

    def query_gpu_info(self) -> Dict[str, str]:
        """Detect NVIDIA GPU hardware status.
        
        Mapped directly from Notebook Cell 1:
          _gpu_check = subprocess.run(
              ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], ...
          )
        """
        if self._gpu_info is not None:
            return self._gpu_info

        info = {"available": "False", "name": "No GPU Detected", "driver": "N/A", "raw": ""}
        if not shutil.which("nvidia-smi"):
            self._gpu_info = info
            return info

        try:
            check = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if check.returncode == 0 and check.stdout.strip():
                parts = [p.strip() for p in check.stdout.strip().split(",")]
                info["available"] = "True"
                info["name"] = parts[0] if len(parts) > 0 else "NVIDIA GPU"
                info["driver"] = parts[1] if len(parts) > 1 else ""
                info["memory"] = parts[2] if len(parts) > 2 else ""
                info["raw"] = check.stdout.strip()
        except Exception:
            pass

        self._gpu_info = info
        return info

    def is_gpu_available(self) -> bool:
        return self.query_gpu_info().get("available") == "True"

    def determine_active_encoder(self) -> str:
        """Determine whether to use h264_nvenc or libx264 based on settings and hardware."""
        mode = self.config.encoder_mode.lower()
        if mode == "h264_nvenc":
            return "h264_nvenc"
        elif mode == "libx264":
            return "libx264"
        else:  # "auto"
            if self.is_gpu_available() and self.has_nvenc():
                return "h264_nvenc"
            return "libx264"

    def start_screen_recorder(
        self,
        output_path: Path,
        display: str,
        width: int,
        height: int,
        fps: int,
    ) -> subprocess.Popen:
        """Start FFmpeg real-time screen capture.
        
        On Linux/WSL: uses x11grab against the virtual display (:99.0).
        On Windows: uses gdigrab against the desktop/window.
        
        Mapped directly from Notebook Cell 3:
          def start_ffmpeg_x11grab(output_path: Path, display: str, width: int, height: int, fps: int) -> subprocess.Popen:
            cmd = [
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "x11grab", "-video_size", f"{width}x{height}",
                "-framerate", str(fps), "-i", f"{display}.0",
                "-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-tune", NVENC_TUNE,
                "-b:v", NVENC_BITRATE,
                str(output_path),
            ]
        """
        ffmpeg_bin = self.get_ffmpeg_binary()
        active_encoder = self.determine_active_encoder()

        # Build video input source
        if sys.platform.startswith("linux"):
            input_args = [
                "-f", "x11grab",
                "-video_size", f"{width}x{height}",
                "-framerate", str(fps),
                "-draw_mouse", "0",
                "-i", f"{display}.0",
            ]
        elif sys.platform == "win32":
            # Windows screen capture via gdigrab
            input_args = [
                "-f", "gdigrab",
                "-framerate", str(fps),
                "-offset_x", "0",
                "-offset_y", "0",
                "-video_size", f"{width}x{height}",
                "-draw_mouse", "0",
                "-i", "desktop",
            ]
        else:
            # macOS / other fallback
            input_args = [
                "-f", "avfoundation",
                "-framerate", str(fps),
                "-i", "1:none",
            ]

        # Build encoder options
        if active_encoder == "h264_nvenc":
            encode_args = [
                "-c:v", "h264_nvenc",
                "-preset", self.config.nvenc_preset,
                "-tune", self.config.nvenc_tune,
            ]
            if self.config.rate_control == "crf":
                encode_args += ["-cq", str(self.config.crf)]
            else:
                encode_args += ["-b:v", self.config.nvenc_bitrate]
        else:
            # CPU fallback (libx264)
            encode_args = [
                "-c:v", "libx264",
                "-preset", self.config.cpu_preset,
                "-crf", str(self.config.crf),
                "-pix_fmt", "yuv420p",
            ]

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            *input_args,
            *encode_args,
            str(output_path),
        ]

        if not self.is_ffmpeg_installed():
            raise FileNotFoundError(
                f"FFmpeg executable '{ffmpeg_bin}' is not installed or not in PATH on this computer.\n"
                f"-> RECOMMENDED: Connect your app to GitHub Codespaces (at the top of the GUI) to utilize "
                f"the 4-core cloud CPU and RAM with zero local resource usage.\n"
                f"-> OR download a local static build via: python main.py --download-ffmpeg"
            )

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        proc._stderr_buf = []

        def _drain():
            try:
                proc._stderr_buf.append(proc.stderr.read() if proc.stderr else b"")
            except Exception:
                pass

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        proc._stderr_thread = t
        return proc

    def read_error_text(self, proc: subprocess.Popen) -> str:
        """Drain and return FFmpeg stderr buffer.
        
        Mapped directly from Notebook Cell 3:
          def _ffmpeg_error_text(proc: subprocess.Popen) -> str:
        """
        t = getattr(proc, "_stderr_thread", None)
        if t is not None:
            t.join(timeout=1.0)
        return b"".join(getattr(proc, "_stderr_buf", [])).decode("utf-8", "replace")

    def stop_gracefully(self, proc: subprocess.Popen, timeout: Optional[float] = None) -> int:
        """Send 'q' to stdin for clean MP4 moov atom finalization.
        
        Mapped directly from Notebook Cell 3:
          def stop_ffmpeg_gracefully(proc: subprocess.Popen, timeout: float = FFMPEG_STOP_TIMEOUT) -> int:
        """
        timeout = timeout or self.config.ffmpeg_stop_timeout
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
        except Exception:
            pass

        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg didn't stop on 'q' in time — sending terminate()")
            proc.terminate()
            try:
                return proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg still alive — killing")
                proc.kill()
                return proc.wait()

    def probe_duration_seconds(self, path: Path) -> float:
        """Probe video file duration in seconds using ffprobe.
        
        Mapped directly from Notebook Cell 3:
          def probe_duration_seconds(path: Path) -> float:
              out = subprocess.run([
                  FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)
              ], capture_output=True, text=True)
              return float(out.stdout.strip())
        """
        ffprobe_bin = self.get_ffprobe_binary()
        if not self.is_ffmpeg_installed():
            raise FileNotFoundError(f"FFprobe executable '{ffprobe_bin}' was not found.")
        out = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            raise RuntimeError(f"ffprobe failed on {path}:\n{out.stderr}")
        return float(out.stdout.strip())

    def retime_to_duration(
        self,
        src: Path,
        dst: Path,
        target_seconds: float,
        fps: int,
    ) -> None:
        """Retime raw capture video to exact MIDI duration using setpts and fps filter.
        
        Mapped directly from Notebook Cell 3:
          def retime_to_duration(src: Path, dst: Path, target_seconds: float, fps: int) -> None:
            actual_seconds = probe_duration_seconds(src)
            ratio = actual_seconds / target_seconds if target_seconds > 0 else 1.0
            ...
            "-filter:v", f"setpts={1/ratio:.6f}*PTS,fps={fps}"
        """
        actual_seconds = self.probe_duration_seconds(src)
        ratio = actual_seconds / target_seconds if target_seconds > 0 else 1.0
        active_encoder = self.determine_active_encoder()

        logger.info(
            f"Raw capture: {actual_seconds:.2f}s vs MIDI target {target_seconds:.2f}s "
            f"(raw playback ran at ~{1/ratio:.2f}x real-time) — retiming to exact 1x @ {fps}fps…"
        )

        ffmpeg_bin = self.get_ffmpeg_binary()

        # Build encoder flags
        if active_encoder == "h264_nvenc":
            encode_args = [
                "-c:v", "h264_nvenc",
                "-preset", self.config.nvenc_preset,
                "-tune", self.config.nvenc_tune,
            ]
            if self.config.rate_control == "crf":
                encode_args += ["-cq", str(self.config.crf)]
            else:
                encode_args += ["-b:v", self.config.nvenc_bitrate]
        else:
            encode_args = [
                "-c:v", "libx264",
                "-preset", self.config.cpu_preset,
                "-crf", str(self.config.crf),
                "-pix_fmt", "yuv420p",
            ]

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(src),
            "-filter:v", f"setpts={1/ratio:.6f}*PTS,fps={fps}",
            *encode_args,
            str(dst),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            raise RuntimeError(f"Retime pass failed (rc={result.returncode}):\n{result.stderr}")


# Global default manager
ffmpeg_manager = FFmpegManager()
