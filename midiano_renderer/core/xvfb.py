"""Virtual X Framebuffer (Xvfb) lifecycle management.

Maps directly to Notebook Cell 3:
  - start_xvfb(display: str, width: int, height: int, depth: int = 24) -> subprocess.Popen
  - Notebook Cell 3 lines 210-256 and lines 655-661 (teardown).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from midiano_renderer.utils.logging import logger


def is_xvfb_available() -> bool:
    """Check if the Xvfb binary is available on the current system PATH."""
    return shutil.which("Xvfb") is not None


def is_linux_or_wsl() -> bool:
    """Return True if running on Linux or WSL."""
    return sys.platform.startswith("linux")


def start_xvfb(
    display: str = ":99",
    width: int = 1920,
    height: int = 1200,
    depth: int = 24,
) -> subprocess.Popen:
    """Start an Xvfb virtual display server.
    
    Mapped directly from Notebook Cell 3:
      def start_xvfb(display: str, width: int, height: int, depth: int = 24) -> subprocess.Popen:
        try:
            subprocess.run(["pkill", "-f", f"Xvfb {display}"], ...)
        proc = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{width}x{height}x{depth}", "-nolisten", "tcp"])
        ...
    """
    if not is_linux_or_wsl() and not is_xvfb_available():
        raise RuntimeError(
            f"Xvfb is an X11 virtual display server for Linux / WSL / Codespaces.\n"
            f"Current platform: {sys.platform}.\n"
            f"For Windows: Use the built-in Codespaces runner, run inside WSL2, "
            f"or use native desktop window capture mode."
        )

    if not is_xvfb_available():
        raise RuntimeError(
            "Xvfb binary not found on PATH. Install it via:\n"
            "  sudo apt-get update && sudo apt-get install -y xvfb"
        )

    # Defensive: kill any stale Xvfb left over from a previous failed run on the same display
    try:
        if shutil.which("pkill"):
            subprocess.run(
                ["pkill", "-f", f"Xvfb {display}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
    time.sleep(0.2)

    proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{width}x{height}x{depth}", "-nolisten", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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

    # Check for X server socket to appear
    display_num = display.lstrip(":")
    sock_path = Path(f"/tmp/.X11-unix/X{display_num}")
    for _ in range(50):  # up to ~5s
        if proc.poll() is not None:
            break
        if sock_path.exists():
            break
        time.sleep(0.1)

    if not sock_path.exists() or proc.poll() is not None:
        t.join(timeout=1.0)
        err = b"".join(getattr(proc, "_stderr_buf", [])).decode("utf-8", "replace")
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Xvfb failed to start on {display}:\n{err}")

    os.environ["DISPLAY"] = display
    logger.info(f"Xvfb started on {display} ({width}x{height}x{depth})")
    return proc


def stop_xvfb(proc: Optional[subprocess.Popen], display: str = ":99") -> None:
    """Tear down Xvfb cleanly.
    
    Mapped directly from Notebook Cell 3 lines 655-661:
      xvfb.terminate()
      try:
          xvfb.wait(timeout=5)
      except Exception:
          xvfb.kill()
    """
    if proc is None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    logger.debug(f"Xvfb {display} stopped.")
