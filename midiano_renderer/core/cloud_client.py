"""Client synchronization layer connecting the local desktop GUI to GitHub Codespaces.

Handles:
  1. Codespace health checks & latency probing.
  2. Batch submission: uploading local .mid files.
  3. Live telemetry reception (4-core CPU saturation, RAM usage, worker states).
  4. Real-time log streaming back to the local GUI.
  5. Automated download pipeline: streams finished MP4s from Codespaces directly into local output folder.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests
import websockets

from midiano_renderer.config import RenderConfig
from midiano_renderer.core.midi_utils import probe_midi_details, scan_midi_folder
from midiano_renderer.utils.logging import LogEntry, LogLevel, logger


@dataclass
class CloudTelemetryData:
    connected: bool = False
    status_text: str = "Disconnected"
    cpu_total_pct: float = 0.0
    cpu_per_core: List[float] = None
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_pct: float = 0.0
    active_workers: int = 0
    total_workers: int = 4
    tasks_total: int = 0
    tasks_completed: int = 0
    workers: List[dict] = None
    tasks: List[dict] = None

    def __post_init__(self):
        if self.cpu_per_core is None:
            self.cpu_per_core = [0.0, 0.0, 0.0, 0.0]
        if self.workers is None:
            self.workers = []
        if self.tasks is None:
            self.tasks = []


class CodespaceClient:
    """Manages the full lifecycle of communicating with a remote GitHub Codespace rendering agent."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        auth_token: Optional[str] = None,
        telemetry_callback: Optional[Callable[[CloudTelemetryData], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.telemetry_callback = telemetry_callback
        self.telemetry = CloudTelemetryData()
        self.is_connected = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._downloaded_files: set[str] = set()

    def set_base_url(self, url: str):
        self.base_url = url.rstrip("/")

    def _get_headers(self) -> dict:
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def test_connection(self) -> dict:
        """Ping the Codespace health check endpoint."""
        url = f"{self.base_url}/api/health"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=6.0)
            if resp.status_code == 200:
                data = resp.json()
                self.is_connected = True
                self.telemetry.connected = True
                self.telemetry.status_text = f"Connected ({data.get('cpu_cores', 4)} Cores, {data.get('ram_total_gb', 8)}GB RAM)"
                return {"success": True, "data": data}
            else:
                self.is_connected = False
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            self.is_connected = False
            return {"success": False, "error": str(e)}

    def submit_batch(
        self,
        midi_files: List[Path],
        out_dir: Path,
        concurrency: int = 4,
        fps: int = 60,
        test_seconds: Optional[float] = None,
        viewport_w: int = 1920,
        viewport_h: int = 1200,
        use_swiftshader: bool = True,
    ) -> bool:
        """Upload batch of MIDIs to Codespace and start background download listener."""
        url = f"{self.base_url}/api/batch/submit"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._downloaded_files.clear()
        self._stop_event.clear()

        files_to_send = []
        opened_handles = []
        try:
            for mf in midi_files:
                fh = open(mf, "rb")
                opened_handles.append(fh)
                files_to_send.append(("files", (mf.name, fh, "audio/midi")))

            data = {
                "concurrency": concurrency,
                "fps": fps,
                "viewport_w": viewport_w,
                "viewport_h": viewport_h,
                "use_swiftshader": use_swiftshader,
            }
            if test_seconds:
                data["test_seconds"] = test_seconds

            logger.info(f"Uploading {len(midi_files)} MIDI files to Codespaces cloud server...")
            resp = requests.post(url, headers=self._get_headers(), data=data, files=files_to_send, timeout=120)
            if resp.status_code != 200:
                logger.error(f"Cloud submission failed ({resp.status_code}): {resp.text}")
                return False

            logger.success(f"Codespaces accepted batch. {concurrency} parallel cloud workers spawned!")

            # Start monitoring & downloading loop in background thread
            self._thread = threading.Thread(
                target=self._monitor_and_download_loop,
                args=(out_dir, len(midi_files)),
                daemon=True,
            )
            self._thread.start()
            return True

        except Exception as e:
            logger.error(f"Error submitting to Codespace: {e}")
            return False
        finally:
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception:
                    pass

    def cancel_batch(self) -> bool:
        """Send cancellation signal to Codespace."""
        self._stop_event.set()
        url = f"{self.base_url}/api/batch/cancel"
        try:
            requests.post(url, headers=self._get_headers(), timeout=10)
            logger.warning("Cloud batch cancellation command sent to Codespaces.")
            return True
        except Exception as e:
            logger.error(f"Failed to send cancel signal: {e}")
            return False

    def _monitor_and_download_loop(self, out_dir: Path, total_files: int):
        """Continuously polls telemetry and streams completed MP4s down to the local PC."""
        while not self._stop_event.is_set():
            try:
                # 1. Fetch telemetry & task list
                t_url = f"{self.base_url}/api/telemetry"
                resp = requests.get(t_url, headers=self._get_headers(), timeout=5)
                if resp.status_code == 200:
                    payload = resp.json()
                    raw_telem = payload.get("telemetry", {})
                    workers = payload.get("workers", [])
                    tasks = payload.get("tasks", [])

                    self.telemetry.connected = True
                    self.telemetry.cpu_total_pct = raw_telem.get("cpu_total_pct", 0.0)
                    self.telemetry.cpu_per_core = raw_telem.get("cpu_per_core", [0.0]*4)
                    self.telemetry.ram_used_gb = raw_telem.get("ram_used_gb", 0.0)
                    self.telemetry.ram_total_gb = raw_telem.get("ram_total_gb", 8.0)
                    self.telemetry.ram_pct = raw_telem.get("ram_pct", 0.0)
                    self.telemetry.active_workers = raw_telem.get("active_workers", 0)
                    self.telemetry.total_workers = raw_telem.get("total_workers", 4)
                    self.telemetry.tasks_total = raw_telem.get("tasks_total", total_files)
                    self.telemetry.tasks_completed = raw_telem.get("tasks_completed", 0)
                    self.telemetry.workers = workers
                    self.telemetry.tasks = tasks

                    if self.telemetry_callback:
                        self.telemetry_callback(self.telemetry)

                    # 2. Check for completed tasks that need downloading
                    for t in tasks:
                        if t.get("status") == "Ready":
                            mp4_name = t.get("target_mp4_name")
                            if mp4_name and mp4_name not in self._downloaded_files:
                                self._download_file(mp4_name, out_dir)
                                self._downloaded_files.add(mp4_name)

                    # Check if all files completed
                    if tasks and all(t.get("status") in ("Ready", "Failed", "Cancelled") for t in tasks):
                        logger.success(f"All {len(tasks)} files rendered and synced to {out_dir}!")
                        break

            except Exception as e:
                logger.debug(f"Telemetry loop tick error: {e}")

            time.sleep(1.0)

    def _download_file(self, filename: str, out_dir: Path):
        """Stream download finished MP4 from Codespace into local output folder."""
        dl_url = f"{self.base_url}/api/download/{filename}"
        local_dest = out_dir / filename
        logger.info(f"Downloading finished video from Codespace: {filename}...")

        try:
            with requests.get(dl_url, headers=self._get_headers(), stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(local_dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)

            size_mb = local_dest.stat().st_size / (1024 * 1024)
            logger.success(f"Downloaded: {local_dest.name} ({size_mb:.2f} MB)")
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
