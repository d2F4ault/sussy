"""GitHub Codespaces High-Throughput Multi-Worker Cloud Rendering Server.

Runs inside GitHub Codespaces to leverage its 4-Core CPU and RAM to 100% capacity.
Manages N parallel virtual Xvfb displays (:99, :100, :101, :102) with concurrent Playwright
instances and multi-threaded FFmpeg pipelines.

Exposes REST and WebSocket endpoints for local desktop GUI communication:
  - GET  /api/health       -> Server hardware & capabilities
  - GET  /api/telemetry    -> Live per-core CPU %, RAM usage, worker states
  - POST /api/batch/submit -> Upload batch of .mid files & start cloud render
  - GET  /api/batch/status -> Real-time status of all queue items
  - GET  /api/download/{f} -> Download completed MP4
  - POST /api/batch/cancel -> Abort active batch and stop workers
  - WS   /api/ws/telemetry -> Live WebSocket event stream
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import psutil
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# Ensure package is on sys.path
pkg_root = Path(__file__).resolve().parent.parent
if str(pkg_root) not in sys.path:
    sys.path.insert(0, str(pkg_root))

from midiano_renderer.config import RenderConfig
from midiano_renderer.core.ffmpeg_utils import FFmpegManager
from midiano_renderer.core.midi_utils import fmt_hms, get_midi_duration_seconds
from midiano_renderer.core.renderer import AC_CAPTURE_SCRIPT
from midiano_renderer.core.xvfb import start_xvfb, stop_xvfb
from midiano_renderer.utils.logging import LogEntry, LogLevel, logger

app = FastAPI(title="Midiano Cloud Rendering Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@dataclass
class CloudFileTask:
    filename: str
    midi_path: str
    target_mp4_name: str
    duration_s: float
    status: str = "Pending"  # Pending, Uploaded, Rendering, Retiming, Ready, Downloading, Failed
    worker_id: Optional[int] = None
    progress_pct: float = 0.0
    elapsed_s: float = 0.0
    error_msg: str = ""
    output_path: Optional[str] = None


@dataclass
class CloudWorkerState:
    worker_id: int
    display: str
    status: str = "Idle"  # Idle, Preparing, Rendering, Retiming, Stopped
    current_file: Optional[str] = None
    progress_pct: float = 0.0
    elapsed_s: float = 0.0
    duration_s: float = 0.0


class CloudBatchManager:
    """Orchestrates 4-core parallel rendering across independent virtual Xvfb displays."""

    def __init__(self):
        self.config = RenderConfig()
        self.tasks: Dict[str, CloudFileTask] = {}
        self.queue: asyncio.Queue[CloudFileTask] = asyncio.Queue()
        self.workers: Dict[int, CloudWorkerState] = {}
        self.worker_tasks: List[asyncio.Task] = []
        self.cancel_event = asyncio.Event()
        self.is_running = False
        self.active_websockets: Set[WebSocket] = set()
        self.work_dir = Path("/dev/shm/midiano_cloud" if Path("/dev/shm").exists() else tempfile.gettempdir()) / "cloud_render"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir = self.work_dir / "completed"
        self.completed_dir.mkdir(parents=True, exist_ok=True)
        self.num_workers = min(4, max(1, os.cpu_count() or 4))

    def broadcast_log(self, msg: str, level: str = "INFO"):
        ts = time.strftime("%H:%M:%S")
        entry = {"timestamp": ts, "level": level, "message": msg}
        asyncio.create_task(self._send_ws({"type": "log", "data": entry}))
        print(f"[{ts}] [{level}] {msg}", flush=True)

    async def _send_ws(self, payload: dict):
        dead = set()
        for ws in self.active_websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self.active_websockets.difference_update(dead)

    def get_hardware_telemetry(self) -> dict:
        """Query detailed hardware saturation stats (per-core CPU % and RAM)."""
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        total_cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()

        return {
            "cpu_total_pct": total_cpu,
            "cpu_per_core": per_cpu,
            "cores_count": len(per_cpu),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_pct": mem.percent,
            "active_workers": sum(1 for w in self.workers.values() if w.status != "Idle"),
            "total_workers": self.num_workers,
            "queue_pending": self.queue.qsize(),
            "tasks_total": len(self.tasks),
            "tasks_completed": sum(1 for t in self.tasks.values() if t.status == "Ready"),
        }

    async def start_workers(self, concurrency: int = 4):
        self.num_workers = concurrency
        self.cancel_event.clear()
        self.is_running = True
        self.broadcast_log(f"Spawning {concurrency} parallel cloud rendering workers to maximize 4-core CPU & RAM...", "INFO")

        # Initialize worker states
        self.workers.clear()
        for i in range(concurrency):
            display = f":{99 + i}"
            self.workers[i] = CloudWorkerState(worker_id=i, display=display)
            t = asyncio.create_task(self._worker_loop(i, display))
            self.worker_tasks.append(t)

    async def stop_workers(self):
        self.cancel_event.set()
        self.is_running = False
        self.broadcast_log("Cancellation requested. Stopping all active cloud workers...", "WARNING")
        for t in self.worker_tasks:
            t.cancel()
        self.worker_tasks.clear()
        for w in self.workers.values():
            w.status = "Stopped"

    async def _worker_loop(self, worker_id: int, display: str):
        """Worker executing real-time headless Chromium capture on an isolated Xvfb display."""
        w_state = self.workers[worker_id]
        xvfb_proc = None
        w_state.status = "Starting Xvfb"
        self.broadcast_log(f"[Worker {worker_id}] Starting Xvfb on display {display}", "INFO")

        try:
            xvfb_proc = start_xvfb(
                display=display,
                width=self.config.viewport_w,
                height=self.config.viewport_h,
            )
        except Exception as e:
            self.broadcast_log(f"[Worker {worker_id}] Xvfb startup warning: {e}", "WARNING")

        os.environ["DISPLAY"] = display
        w_state.status = "Idle"

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
        if sys.platform.startswith("linux"):
            launch_args.append("--kiosk")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False, args=launch_args)
                context = await browser.new_context(
                    viewport={"width": self.config.viewport_w, "height": self.config.viewport_h},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                )
                await context.grant_permissions(["midi", "midi-sysex"], origin=self.config.app_url)

                while not self.cancel_event.is_set():
                    try:
                        task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    w_state.current_file = task.filename
                    w_state.status = "Rendering"
                    task.worker_id = worker_id
                    task.status = "Rendering"
                    self.broadcast_log(f"[Worker {worker_id}] Picked up {task.filename} ({fmt_hms(task.duration_s)})", "INFO")

                    try:
                        out_file = await self._render_file_on_worker(
                            worker_id=worker_id,
                            context=context,
                            display=display,
                            task=task,
                            w_state=w_state,
                        )
                        task.status = "Ready"
                        task.output_path = str(out_file)
                        task.progress_pct = 100.0
                        self.broadcast_log(f"[Worker {worker_id}] SUCCESS: {task.filename} ready for download!", "SUCCESS")
                    except asyncio.CancelledError:
                        task.status = "Cancelled"
                        break
                    except Exception as exc:
                        task.status = "Failed"
                        task.error_msg = str(exc)
                        self.broadcast_log(f"[Worker {worker_id}] FAILED {task.filename}: {exc}", "ERROR")
                    finally:
                        self.queue.task_done()
                        w_state.status = "Idle"
                        w_state.current_file = None
                        w_state.progress_pct = 0.0

                await context.close()
                await browser.close()
        finally:
            if xvfb_proc:
                stop_xvfb(xvfb_proc, display)
            w_state.status = "Stopped"

    async def _render_file_on_worker(
        self,
        worker_id: int,
        context,
        display: str,
        task: CloudFileTask,
        w_state: CloudWorkerState,
    ) -> Path:
        """Render a single file using Playwright on this worker's isolated display."""
        page = await context.new_page()
        await page.bring_to_front()
        await page.add_init_script(AC_CAPTURE_SCRIPT)

        raw_out = self.work_dir / f"w{worker_id}_{Path(task.filename).stem}_raw.mp4"
        final_out = self.completed_dir / task.target_mp4_name

        ffmpeg_mgr = FFmpegManager(self.config)
        ffmpeg_proc = None

        try:
            # 1. Load Midiano
            await page.goto(self.config.app_url, wait_until="domcontentloaded", timeout=60_000)

            # 2. Wait piano canvas ready
            await page.wait_for_selector("canvas.pianoCanvas", timeout=60_000)
            await page.wait_for_function(
                """() => {
                    const t = document.body.innerText || '';
                    const pianos = document.querySelectorAll('canvas.pianoCanvas');
                    return pianos.length >= 1 && !/Creating Buffers|Phrasing/i.test(t);
                }""",
                timeout=60_000,
            )
            await page.wait_for_timeout(1500)

            # 3. Upload MIDI
            buffers = asyncio.Event()

            def on_console(msg):
                if "Buffers loaded" in msg.text or "Setting song" in msg.text:
                    buffers.set()

            page.on("console", on_console)
            file_input = page.locator('input[type="file"][accept*=".mid"]').first
            await file_input.wait_for(state="attached", timeout=30_000)
            await file_input.set_input_files(task.midi_path)
            try:
                await asyncio.wait_for(buffers.wait(), timeout=180)
            except asyncio.TimeoutError:
                pass

            # 4. Hide Menu
            btn = page.locator('button[aria-label="Minimize/Maximize Menu"]')
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click(timeout=5000)
            await page.mouse.move(self.config.viewport_w // 2, self.config.viewport_h // 2)
            await page.wait_for_timeout(3500)
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

            if raw_out.exists():
                raw_out.unlink()

            # 5. Start FFmpeg screen recorder with multi-threading
            ffmpeg_proc = ffmpeg_mgr.start_screen_recorder(
                output_path=raw_out,
                display=display,
                width=self.config.viewport_w,
                height=self.config.viewport_h,
                fps=self.config.capture_fps,
            )
            await asyncio.sleep(self.config.ffmpeg_warmup_sec)

            # 6. Play song
            t0 = time.time()
            await page.keyboard.press("Space")
            duration_s = task.duration_s

            elapsed = 0.0
            heartbeat = 5.0
            while elapsed < duration_s:
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError()

                step = min(heartbeat, duration_s - elapsed)
                await asyncio.sleep(step)
                elapsed = time.time() - t0

                if ffmpeg_proc.poll() is not None:
                    err = ffmpeg_mgr.read_error_text(ffmpeg_proc)
                    raise RuntimeError(f"FFmpeg exited early ({ffmpeg_proc.returncode}): {err}")

                pct = 100.0 * min(elapsed, duration_s) / duration_s
                task.progress_pct = pct
                task.elapsed_s = elapsed
                w_state.progress_pct = pct
                w_state.elapsed_s = elapsed
                w_state.duration_s = duration_s

                self.broadcast_log(
                    f"[Worker {worker_id}] Recording {task.filename}: {fmt_hms(elapsed)} / {fmt_hms(duration_s)} ({pct:.1f}%)",
                    "PROGRESS",
                )

            # 7. Stop FFmpeg gracefully
            rc = ffmpeg_mgr.stop_gracefully(ffmpeg_proc)
            if rc != 0:
                raise RuntimeError(f"FFmpeg stop failed with returncode {rc}")

            # 8. Retiming pass (setpts filter to exact MIDI duration)
            w_state.status = "Retiming"
            task.status = "Retiming"
            self.broadcast_log(f"[Worker {worker_id}] Retiming raw capture of {task.filename} to exact 1× sync @ {self.config.fps}fps...", "INFO")
            ffmpeg_mgr.retime_to_duration(
                src=raw_out,
                dst=final_out,
                target_seconds=duration_s,
                fps=self.config.fps,
            )
            raw_out.unlink(missing_ok=True)
            return final_out

        finally:
            if ffmpeg_proc and ffmpeg_proc.poll() is None:
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass
            await page.close()


manager = CloudBatchManager()


# ── Periodic Telemetry Background Task ──────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    async def _telemetry_loop():
        while True:
            await asyncio.sleep(1.0)
            if manager.active_websockets:
                telemetry = manager.get_hardware_telemetry()
                workers_data = [asdict(w) for w in manager.workers.values()]
                tasks_data = [asdict(t) for t in manager.tasks.values()]
                await manager._send_ws({
                    "type": "telemetry",
                    "data": {
                        "telemetry": telemetry,
                        "workers": workers_data,
                        "tasks": tasks_data,
                    },
                })

    asyncio.create_task(_telemetry_loop())


# ── REST Endpoints ─────────────────────────────────────────────────────────────
@app.get("/api/health")
def get_health():
    """Return Codespace hardware specs and status."""
    has_nv = FFmpegManager().has_nvenc()
    cpu_count = os.cpu_count() or 4
    mem = psutil.virtual_memory()
    return {
        "status": "online",
        "service": "Midiano Cloud Batch Renderer",
        "platform": sys.platform,
        "cpu_cores": cpu_count,
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "has_nvenc": has_nv,
        "recommended_workers": min(4, cpu_count),
    }


@app.get("/api/telemetry")
def get_telemetry():
    return {
        "telemetry": manager.get_hardware_telemetry(),
        "workers": [asdict(w) for w in manager.workers.values()],
        "tasks": [asdict(t) for t in manager.tasks.values()],
    }


@app.post("/api/batch/submit")
async def submit_batch(
    files: List[UploadFile] = File(...),
    concurrency: int = Form(4),
    fps: int = Form(60),
    test_seconds: Optional[float] = Form(None),
    viewport_w: int = Form(1920),
    viewport_h: int = Form(1200),
    use_swiftshader: bool = Form(True),
):
    """Receive a batch of MIDI files and launch the multi-worker cloud pipeline."""
    manager.config.fps = fps
    manager.config.viewport_w = viewport_w
    manager.config.viewport_h = viewport_h
    manager.config.use_swiftshader = use_swiftshader

    manager.tasks.clear()
    while not manager.queue.empty():
        try:
            manager.queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    upload_dir = manager.work_dir / "midis"
    upload_dir.mkdir(parents=True, exist_ok=True)

    for up_file in files:
        if not up_file.filename.lower().endswith((".mid", ".midi")):
            continue

        dest_path = upload_dir / up_file.filename
        with open(dest_path, "wb") as f:
            content = await up_file.read()
            f.write(content)

        dur_s = get_midi_duration_seconds(dest_path)
        if test_seconds is not None and test_seconds > 0:
            dur_s = min(dur_s, float(test_seconds))

        task = CloudFileTask(
            filename=up_file.filename,
            midi_path=str(dest_path),
            target_mp4_name=f"{Path(up_file.filename).stem}.mp4",
            duration_s=dur_s,
            status="Queued",
        )
        manager.tasks[task.filename] = task
        await manager.queue.put(task)

    manager.broadcast_log(f"Received batch of {len(manager.tasks)} MIDI files. Starting {concurrency} parallel workers.", "SUCCESS")
    await manager.start_workers(concurrency=concurrency)
    return {"status": "started", "files_queued": len(manager.tasks), "workers": concurrency}


@app.post("/api/batch/cancel")
async def cancel_batch():
    await manager.stop_workers()
    return {"status": "cancelled"}


@app.get("/api/download/{filename}")
def download_rendered_mp4(filename: str):
    file_path = manager.completed_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Rendered video not found or not yet finished.")
    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=filename,
    )


@app.websocket("/api/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    manager.active_websockets.add(websocket)
    manager.broadcast_log("Local Desktop GUI connected to Codespace WebSocket telemetry.", "INFO")
    try:
        while True:
            # Keep-alive
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.active_websockets.discard(websocket)
    except Exception:
        manager.active_websockets.discard(websocket)


def run_server(port: int = 8000, host: str = "0.0.0.0"):
    import uvicorn
    print("=" * 70)
    print("MIDIANO CLOUD RENDERING AGENT (GitHub Codespaces)")
    print("=" * 70)
    print(f"Listening on http://{host}:{port}")
    print(f"Detected CPU Cores: {os.cpu_count() or 4}")
    print("Forward this port in Codespaces to connect your Desktop GUI.")
    print("=" * 70)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface")
    args = parser.parse_args()
    run_server(port=args.port, host=args.host)
