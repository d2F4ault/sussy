"""Core rendering engine for Midiano falling-notes visualization.

Maps directly to Notebook Cell 3 and Cell 4:
  - Notebook Cell 3 lines 142-664:
      AC_CAPTURE_SCRIPT
      wait_piano_ready
      upload_midi
      hide_top_menu
      _log_gpu_status
      render_one
      run_batch
  - Notebook Cell 4 lines 665-673:
      await run_batch()
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from midiano_renderer.config import RenderConfig, get_default_app_dir
from midiano_renderer.core.ffmpeg_utils import FFmpegManager, ffmpeg_manager
from midiano_renderer.core.midi_utils import (
    fmt_hms,
    get_midi_duration_seconds,
    midi_duration_ms,
    scan_midi_folder,
)
from midiano_renderer.core.xvfb import is_linux_or_wsl, is_xvfb_available, start_xvfb, stop_xvfb
from midiano_renderer.utils.logging import LogLevel, logger

# Notebook Cell 3 line 168: Web AudioContext capture script
AC_CAPTURE_SCRIPT = r"""
(() => {
  const NativeAC = window.AudioContext || window.webkitAudioContext;
  if (!NativeAC) return;
  window.__acInstances = [];
  function PatchedAC(...args) {
    const inst = new NativeAC(...args);
    window.__acInstances.push(inst);
    return inst;
  }
  PatchedAC.prototype = NativeAC.prototype;
  try { Object.setPrototypeOf(PatchedAC, NativeAC); } catch (e) {}
  window.AudioContext = PatchedAC;
  window.webkitAudioContext = PatchedAC;
})();
"""


@dataclass
class ProgressState:
    file_index: int = 0
    total_files: int = 0
    current_file_name: str = ""
    current_status: str = "Idle"
    file_elapsed_s: float = 0.0
    file_duration_s: float = 0.0
    file_percent: float = 0.0
    overall_percent: float = 0.0
    is_running: bool = False
    latest_rendered_path: Optional[str] = None


ProgressCallback = Callable[[ProgressState], None]


class BatchRenderer:
    """Manages the full automated batch rendering lifecycle with cancellation and GUI updates."""

    def __init__(
        self,
        config: RenderConfig,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.config = config
        self.progress_callback = progress_callback
        self.cancel_event = threading.Event()
        self.state = ProgressState()
        self.ffmpeg_mgr = FFmpegManager(config)

    def cancel(self) -> None:
        """Signal the renderer to abort current capture and stop the batch."""
        self.cancel_event.set()
        logger.warning("Cancellation requested by user. Aborting...")

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _update_progress(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        if self.progress_callback:
            try:
                self.progress_callback(self.state)
            except Exception:
                pass

    async def wait_piano_ready(self, page, timeout_ms: int = 60_000) -> None:
        """Wait until piano canvas is present and not displaying loading messages.
        
        Mapped directly from Notebook Cell 3 lines 352-363:
          await page.wait_for_selector("canvas.pianoCanvas", timeout=timeout_ms)
          await page.wait_for_function(...)
        """
        await page.wait_for_selector("canvas.pianoCanvas", timeout=timeout_ms)
        await page.wait_for_function(
            """() => {
                const t = document.body.innerText || '';
                const pianos = document.querySelectorAll('canvas.pianoCanvas');
                return pianos.length >= 1 && !/Creating Buffers|Phrasing/i.test(t);
            }""",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(1500)

    async def upload_midi(self, page, midi_path: Path) -> None:
        """Upload MIDI to Midiano and wait for buffer loading event.
        
        Mapped directly from Notebook Cell 3 lines 365-381:
          buffers = asyncio.Event()
          ...
          file_input.set_input_files(str(midi_path))
        """
        buffers = asyncio.Event()

        def on_console(msg) -> None:
            if "Buffers loaded" in msg.text or "Setting song" in msg.text:
                buffers.set()

        page.on("console", on_console)
        file_input = page.locator('input[type="file"][accept*=".mid"]').first
        await file_input.wait_for(state="attached", timeout=30_000)
        await file_input.set_input_files(str(midi_path))
        try:
            await asyncio.wait_for(buffers.wait(), timeout=180)
            logger.info("Buffers loaded / Setting song")
        except asyncio.TimeoutError:
            logger.info("  (no buffer console event — waiting on piano canvas)")

        await self.wait_piano_ready(page)

    async def hide_top_menu(self, page) -> None:
        """Minimize/hide the top chrome menu buttons so only the piano visualization renders.
        
        Mapped directly from Notebook Cell 3 lines 383-402:
          btn = page.locator('button[aria-label="Minimize/Maximize Menu"]')
          ...
          hide = ['button[aria-label="Minimize/Maximize Menu"]', ...]
        """
        btn = page.locator('button[aria-label="Minimize/Maximize Menu"]')
        if await btn.count() and await btn.first.is_visible():
            await btn.first.click(timeout=5_000)
        await page.mouse.move(self.config.viewport_w // 2, self.config.viewport_h // 2)
        await page.wait_for_timeout(4000)
        await page.evaluate(
            """() => {
                const hide = [
                  'button[aria-label="Minimize/Maximize Menu"]',
                  'button[aria-label="Open/Close zoom menu"]',
                  'button[aria-label="Open settings"]',
                  'button[aria-label="Open menu"]',
                ];
                for (const sel of hide) {
                  document.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
                }
            }"""
        )
        logger.info("Top menu hidden")

    async def _log_gpu_status(self, page) -> None:
        """Query WebGL renderer string from Chromium.
        
        Mapped directly from Notebook Cell 3 lines 404-424:
          gl = document.createElement('canvas').getContext('webgl');
          return gl ? gl.getParameter(gl.RENDERER) : 'no-webgl-context';
        """
        try:
            renderer = await page.evaluate(
                """() => {
                    const gl = document.createElement('canvas').getContext('webgl');
                    return gl ? gl.getParameter(gl.RENDERER) : 'no-webgl-context';
                }"""
            )
            logger.info(f"  GPU renderer: {renderer}")
            if any(s in str(renderer) for s in ("SwiftShader", "llvmpipe", "software")):
                logger.info("  (page rendering via software GL, as configured — NVENC encoding is unaffected)")
        except Exception as e:
            logger.debug(f"  (could not probe GPU renderer: {e})")

    def _get_temp_video_path(self, stem: str) -> Path:
        """Choose high-speed temporary RAM disk path or system temp folder.
        
        Mapped from Notebook Cell 3 line 450:
          shm_out = Path(f"/dev/shm/{midi_path.stem}_output.mp4")
        """
        if sys.platform.startswith("linux") and Path("/dev/shm").exists() and os.access("/dev/shm", os.W_OK):
            return Path(f"/dev/shm/{stem}_output.mp4")
        # System temp directory fallback (cross-platform)
        temp_dir = Path(tempfile.gettempdir()) / "midiano_cache"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / f"{stem}_output.mp4"

    async def render_one(
        self,
        context,
        midi_path: Path,
        output_path: Path,
        index: int,
        total: int,
    ) -> None:
        """Render a single MIDI file.
        
        Mapped directly from Notebook Cell 3 lines 426-545:
          duration_ms = midi_duration_ms(midi_path)
          ...
          page = await context.new_page()
          ...
          ffmpeg = start_ffmpeg_x11grab(...)
          ...
          await page.keyboard.press("Space")
          ...
          stop_ffmpeg_gracefully(...)
          retime_to_duration(...)
          shutil.move(...)
        """
        duration_ms = midi_duration_ms(midi_path)
        if self.config.test_seconds is not None and self.config.test_seconds > 0:
            duration_ms = min(duration_ms, float(self.config.test_seconds) * 1000.0)
            logger.info(f"  TEST MODE: limit set to first {duration_ms / 1000:.1f}s")

        duration_s = duration_ms / 1000.0
        logger.info(f"[{index}/{total}] {midi_path.name}")
        logger.info(
            f"  MIDI duration {duration_s:.2f}s ({fmt_hms(duration_s)}) → "
            f"capture @ {self.config.capture_fps} FPS [{self.ffmpeg_mgr.determine_active_encoder()}]"
        )

        self._update_progress(
            file_index=index,
            total_files=total,
            current_file_name=midi_path.name,
            current_status="Opening page",
            file_elapsed_s=0.0,
            file_duration_s=duration_s,
            file_percent=0.0,
        )

        page = await context.new_page()
        await page.bring_to_front()
        await page.add_init_script(AC_CAPTURE_SCRIPT)

        ffmpeg_proc = None
        raw_out = self._get_temp_video_path(midi_path.stem)
        retimed_out = raw_out.parent / f"{midi_path.stem}_retimed.mp4"

        try:
            if self.is_cancelled():
                raise asyncio.CancelledError("User stopped render")

            logger.info(f"Navigated to {self.config.app_url}")
            self._update_progress(current_status="Loading Midiano")
            await page.goto(self.config.app_url, wait_until="domcontentloaded", timeout=60_000)

            logger.info("Piano canvas ready")
            self._update_progress(current_status="Waiting for canvas")
            await self.wait_piano_ready(page)
            await self._log_gpu_status(page)

            if self.is_cancelled():
                raise asyncio.CancelledError("User stopped render")

            logger.info(f"Uploading {midi_path.name} …")
            self._update_progress(current_status="Uploading MIDI")
            await self.upload_midi(page, midi_path)

            if self.is_cancelled():
                raise asyncio.CancelledError("User stopped render")

            self._update_progress(current_status="Hiding UI chrome")
            await self.hide_top_menu(page)

            if raw_out.exists():
                raw_out.unlink()
            if retimed_out.exists():
                retimed_out.unlink()

            if self.is_cancelled():
                raise asyncio.CancelledError("User stopped render")

            # Start FFmpeg screen capture recorder
            enc_name = self.ffmpeg_mgr.determine_active_encoder()
            logger.info(f"Starting {enc_name} capture → {raw_out}")
            ffmpeg_proc = self.ffmpeg_mgr.start_screen_recorder(
                output_path=raw_out,
                display=self.config.xvfb_display,
                width=self.config.viewport_w,
                height=self.config.viewport_h,
                fps=self.config.capture_fps,
            )

            # Warmup delay for grabber initialization
            await asyncio.sleep(self.config.ffmpeg_warmup_sec)
            if ffmpeg_proc.poll() is not None:
                err = self.ffmpeg_mgr.read_error_text(ffmpeg_proc)
                raise RuntimeError(f"ffmpeg exited immediately ({ffmpeg_proc.returncode}):\n{err}")

            t0 = time.time()
            logger.info("Pressed Space – playback started")
            self._update_progress(current_status="Recording")
            await page.keyboard.press("Space")

            # Real-time capture loop
            elapsed = 0.0
            heartbeat = self.config.log_interval_sec
            while elapsed < duration_s:
                if self.is_cancelled():
                    raise asyncio.CancelledError("User cancelled capture")

                step = min(heartbeat, duration_s - elapsed)
                await asyncio.sleep(step)
                elapsed = time.time() - t0

                if ffmpeg_proc.poll() is not None:
                    err = self.ffmpeg_mgr.read_error_text(ffmpeg_proc)
                    raise RuntimeError(f"ffmpeg exited early during recording ({ffmpeg_proc.returncode}):\n{err}")

                pct = 100.0 * min(elapsed, duration_s) / duration_s
                logger.info(
                    f"Recording {fmt_hms(elapsed)} / {fmt_hms(duration_s)} ({pct:.1f}%)"
                )
                self._update_progress(
                    file_elapsed_s=elapsed,
                    file_percent=pct,
                    current_status=f"Recording ({pct:.0f}%)",
                )

            logger.info("Stopping ffmpeg (sending 'q')")
            self._update_progress(current_status="Finalizing video")
            rc = self.ffmpeg_mgr.stop_gracefully(ffmpeg_proc)
            if rc != 0:
                err = self.ffmpeg_mgr.read_error_text(ffmpeg_proc)
                raise RuntimeError(f"ffmpeg failed ({rc}):\n{err}")

            # Size sanity check (Notebook Cell 3 lines 503-509)
            min_bytes = max(20_000, int(duration_s * 5_000_000 / 8 * 0.05))
            actual_bytes = raw_out.stat().st_size if raw_out.exists() else 0
            if actual_bytes < min_bytes:
                raise RuntimeError(
                    f"Recording suspiciously small ({actual_bytes} bytes for {duration_s:.1f}s) — "
                    f"likely a blank/black capture. Check display/GPU renderer."
                )

            # Retime raw capture to exact MIDI duration (Notebook Cell 3 lines 515-517)
            self._update_progress(current_status="Retiming to 1x")
            self.ffmpeg_mgr.retime_to_duration(
                src=raw_out,
                dst=retimed_out,
                target_seconds=duration_s,
                fps=self.config.fps,
            )
            raw_out.unlink(missing_ok=True)

            # Move final retimed video to destination
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(retimed_out), str(output_path))

            total_wall = time.time() - t0
            logger.success(f"SUCCESS → {output_path} (total wall time {fmt_hms(total_wall)})")
            self._update_progress(
                current_status="Completed file",
                latest_rendered_path=str(output_path),
            )

        except Exception as exc:
            if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass
            if raw_out.exists():
                raw_out.unlink(missing_ok=True)
            if retimed_out.exists():
                retimed_out.unlink(missing_ok=True)

            # Debug screenshot on failure (Notebook Cell 3 lines 533-538)
            try:
                debug_dir = get_default_app_dir() / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                debug_path = debug_dir / f"debug_{midi_path.stem}_{int(time.time())}.png"
                await page.screenshot(path=str(debug_path))
                logger.warning(f"Saved debug screenshot → {debug_path}")
            except Exception:
                pass
            raise
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def run_batch(self) -> None:
        """Run batch processing across all discovered MIDI files.
        
        Mapped directly from Notebook Cell 3 lines 547-662.
        """
        midi_dir = Path(self.config.midi_dir)
        out_dir = Path(self.config.out_dir)

        if not midi_dir.is_dir():
            raise ValueError(f"MIDI input folder does not exist: {midi_dir}")

        out_dir.mkdir(parents=True, exist_ok=True)

        midis = scan_midi_folder(midi_dir)
        if not midis:
            logger.warning(f"No MIDI files found in {midi_dir}")
            return

        logger.info(f"Found {len(midis)} MIDI file(s)")
        if self.config.test_seconds is not None:
            logger.info(f"TEST MODE: first {self.config.test_seconds}s of each file")

        # Hardware and encoder checks
        active_encoder = self.ffmpeg_mgr.determine_active_encoder()
        gpu_info = self.ffmpeg_mgr.query_gpu_info()
        logger.info(f"Active encoder: {active_encoder}")
        if gpu_info.get("available") == "True":
            logger.info(f"GPU Hardware: {gpu_info.get('name')} | Driver: {gpu_info.get('driver')}")
        else:
            if self.config.encoder_mode == "h264_nvenc":
                raise RuntimeError(
                    "NVENC encoder explicitly requested, but no NVIDIA GPU or NVENC encoder was detected.\n"
                    "Switch Encoder Mode to 'Auto' or 'libx264 (CPU)' in Settings."
                )
            logger.info("Operating in CPU encoding mode (libx264).")

        self.cancel_event.clear()
        self._update_progress(
            is_running=True,
            total_files=len(midis),
            overall_percent=0.0,
            current_status="Initializing environment",
        )

        # Clear stale /dev/shm files if on Linux
        if sys.platform.startswith("linux") and Path("/dev/shm").exists():
            for stale in Path("/dev/shm").glob("*_output.mp4"):
                try:
                    stale.unlink()
                except Exception:
                    pass

        # Virtual display (Xvfb) lifecycle
        xvfb_proc = None
        if is_linux_or_wsl() and is_xvfb_available():
            xvfb_proc = start_xvfb(
                display=self.config.xvfb_display,
                width=self.config.viewport_w,
                height=self.config.viewport_h,
            )
        else:
            if sys.platform.startswith("linux") and not is_xvfb_available():
                logger.warning("Xvfb is not installed. Will launch browser on current desktop display.")
            else:
                logger.info(f"Running on {sys.platform} native desktop.")

        try:
            from playwright.async_api import async_playwright

            launch_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu-sandbox",
                "--enable-webgl",
                "--mute-audio",
                "--disable-extensions",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--window-position=0,0",
                f"--window-size={self.config.viewport_w},{self.config.viewport_h}",
            ]

            if self.config.use_swiftshader:
                launch_args.append("--use-gl=swiftshader")
                logger.info(
                    "Chromium launched (headless=False, SwiftShader)"
                )
            else:
                logger.info("Chromium launched (headless=False, native GL)")

            if is_linux_or_wsl():
                launch_args.append("--kiosk")

            async with async_playwright() as p:
                browser = None
                launch_candidates = [
                    {},
                    {"channel": "chrome"},
                    {"channel": "msedge"},
                ]
                # Check known system Chrome locations
                for p_cand in [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    "/usr/bin/google-chrome",
                    "/usr/bin/chromium-browser",
                ]:
                    if Path(p_cand).exists():
                        launch_candidates.append({"executable_path": p_cand})

                for kwargs in launch_candidates:
                    try:
                        browser = await p.chromium.launch(
                            headless=False,
                            args=launch_args,
                            **kwargs,
                        )
                        break
                    except Exception as b_err:
                        logger.debug(f"Browser launch attempt with {kwargs} failed: {b_err}")

                if browser is None:
                    raise RuntimeError(
                        "Could not launch Chromium or system Chrome/Edge. "
                        "Please run: python -m playwright install chromium"
                    )

                context = await browser.new_context(
                    viewport={
                        "width": self.config.viewport_w,
                        "height": self.config.viewport_h,
                    },
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                )
                await context.grant_permissions(["midi", "midi-sysex"], origin=self.config.app_url)

                try:
                    for i, midi in enumerate(midis, start=1):
                        if self.is_cancelled():
                            logger.warning("Batch cancelled by user.")
                            break

                        out = out_dir / f"{midi.stem}.mp4"
                        if self.config.skip_existing and out.exists() and out.stat().st_size > 0:
                            logger.info(f"[{i}/{len(midis)}] SKIP (exists) {out.name}")
                            self._update_progress(
                                file_index=i,
                                current_file_name=midi.name,
                                current_status="Skipped (already exists)",
                                overall_percent=100.0 * i / len(midis),
                            )
                            continue

                        try:
                            await self.render_one(context, midi, out, i, len(midis))
                        except asyncio.CancelledError:
                            logger.warning(f"[{i}/{len(midis)}] Stopped {midi.name}")
                            break
                        except Exception:
                            logger.error(f"[{i}/{len(midis)}] FAILED {midi.name}")
                            logger.error(traceback.format_exc())
                            logger.info("Continuing with next file…")

                        self._update_progress(
                            overall_percent=100.0 * i / len(midis),
                        )
                finally:
                    await context.close()
                    await browser.close()
        finally:
            if xvfb_proc is not None:
                stop_xvfb(xvfb_proc, self.config.xvfb_display)

            self._update_progress(
                is_running=False,
                current_status="Batch complete" if not self.is_cancelled() else "Batch stopped",
            )
            logger.info("Batch complete.")
