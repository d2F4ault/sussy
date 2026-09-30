"""Main desktop application window for Midiano Cloud & Local Batch Studio.

Clean, classic, professional old-school light desktop UI with high-contrast elements,
direct GitHub Codespace 4-Core CPU/RAM saturation monitor, and real-time multi-worker table.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk
from tkinter import messagebox

from midiano_renderer.config import RenderConfig
from midiano_renderer.core.cloud_client import CloudTelemetryData, CodespaceClient
from midiano_renderer.core.ffmpeg_utils import ffmpeg_manager
from midiano_renderer.core.midi_utils import fmt_hms, probe_midi_details, scan_midi_folder
from midiano_renderer.gui.styles import (
    BG_INPUT,
    BG_PANEL,
    BG_WINDOW,
    BORDER_COLOR,
    BTN_PRIMARY,
    BTN_PRIMARY_HOVER,
    BTN_SECONDARY,
    BTN_SECONDARY_HOVER,
    BTN_START,
    BTN_START_HOVER,
    BTN_STOP,
    BTN_STOP_HOVER,
    FONT_FAMILY,
    FONT_MONO,
    TEXT_LIGHT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from midiano_renderer.gui.widgets import (
    CloudHardwareMonitor,
    CodespaceConnectionWidget,
    FolderPickerWidget,
    LogTextView,
    QueueTableWidget,
)
from midiano_renderer.utils.logging import LogEntry, LogLevel, logger

# Set classic light desktop appearance
ctk.set_appearance_mode("Light")


class MidianoApp(ctk.CTk):
    """Classic Desktop UI connecting local PC to 4-core GitHub Codespaces rendering agent."""

    def __init__(self, config: Optional[RenderConfig] = None):
        super().__init__()
        self.config = config or RenderConfig.load()
        self.cloud_client = CodespaceClient(telemetry_callback=self._on_cloud_telemetry)
        self.scanned_files: List[dict] = []
        self.is_rendering = False

        # Window setup
        self.title("Midiano Batch Renderer — GitHub Codespaces Cloud Studio")
        self.geometry("1140x880")
        self.minsize(1020, 760)
        self.configure(fg_color=BG_WINDOW)

        # Wire logger
        logger.subscribe(self._on_log_entry)

        # Build UI layout
        self._build_layout()

        # Initial folder scan if folders pre-set
        if self.config.midi_dir and Path(self.config.midi_dir).is_dir():
            self._scan_and_update_table(self.config.midi_dir)

        # Check default Codespace connection
        self.after(500, self._check_initial_connection)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # ── 1. Top Header Bar ────────────────────────────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, height=50, corner_radius=0, border_color=BORDER_COLOR, border_width=1)
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        header_title = ctk.CTkLabel(
            header_frame,
            text="MIDIANO BATCH RENDERER — CODESPACES CLOUD STUDIO",
            font=(FONT_FAMILY, 13, "bold"),
            text_color=TEXT_PRIMARY,
        )
        header_title.grid(row=0, column=0, padx=14, pady=8, sticky="w")

        sub_title = ctk.CTkLabel(
            header_frame,
            text="Utilizing 4-Core CPU & 8GB RAM in GitHub Codespaces • Zero PC Load",
            font=(FONT_FAMILY, 10),
            text_color=TEXT_MUTED,
        )
        sub_title.grid(row=0, column=1, padx=10, pady=8, sticky="w")

        # ── 2. GitHub Codespaces Cloud Bridge Bar ─────────────────────────────────
        self.conn_bar = CodespaceConnectionWidget(
            self,
            default_url="http://localhost:8000",
            on_connect=self._on_connect_clicked,
        )
        self.conn_bar.grid(row=1, column=0, padx=12, pady=(10, 4), sticky="ew")

        # ── 3. 4-Core Hardware Saturation Monitor ────────────────────────────────
        self.hardware_monitor = CloudHardwareMonitor(self)
        self.hardware_monitor.grid(row=2, column=0, padx=12, pady=4, sticky="ew")

        # ── 4. Folder Pickers & Settings Row ─────────────────────────────────────
        top_controls = ctk.CTkFrame(self, fg_color="transparent")
        top_controls.grid(row=3, column=0, padx=12, pady=4, sticky="ew")
        top_controls.grid_columnconfigure((0, 1), weight=1)

        self.midi_picker = FolderPickerWidget(
            top_controls,
            label_text="Local MIDI Input Folder",
            default_path=self.config.midi_dir,
            on_change=self._on_midi_folder_changed,
        )
        self.midi_picker.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.out_picker = FolderPickerWidget(
            top_controls,
            label_text="Local MP4 Output Folder (Auto-Downloaded from Cloud)",
            default_path=self.config.out_dir,
            on_change=self._on_out_folder_changed,
        )
        self.out_picker.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # Settings options row
        settings_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4)
        settings_frame.grid(row=4, column=0, padx=12, pady=4, sticky="ew")
        settings_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Worker count
        w_lbl = ctk.CTkLabel(settings_frame, text="Parallel Cloud Workers:", font=(FONT_FAMILY, 10, "bold"), text_color=TEXT_PRIMARY)
        w_lbl.grid(row=0, column=0, padx=8, pady=(4, 1), sticky="w")
        self.workers_var = ctk.StringVar(value="4 Workers (100% 4-Core Saturation)")
        self.workers_menu = ctk.CTkOptionMenu(
            settings_frame,
            values=[
                "4 Workers (100% 4-Core Saturation)",
                "3 Workers",
                "2 Workers",
                "1 Worker",
            ],
            variable=self.workers_var,
            fg_color=BG_INPUT,
            button_color=BTN_SECONDARY,
            text_color=TEXT_PRIMARY,
            height=26,
            font=(FONT_FAMILY, 9),
        )
        self.workers_menu.grid(row=1, column=0, padx=8, pady=(0, 6), sticky="ew")

        # Target FPS
        fps_lbl = ctk.CTkLabel(settings_frame, text="Target Framerate:", font=(FONT_FAMILY, 10, "bold"), text_color=TEXT_PRIMARY)
        fps_lbl.grid(row=0, column=1, padx=8, pady=(4, 1), sticky="w")
        self.fps_var = ctk.StringVar(value=str(self.config.fps))
        self.fps_menu = ctk.CTkOptionMenu(
            settings_frame,
            values=["60", "30"],
            variable=self.fps_var,
            fg_color=BG_INPUT,
            button_color=BTN_SECONDARY,
            text_color=TEXT_PRIMARY,
            height=26,
            font=(FONT_FAMILY, 9),
        )
        self.fps_menu.grid(row=1, column=1, padx=8, pady=(0, 6), sticky="ew")

        # Test duration
        t_lbl = ctk.CTkLabel(settings_frame, text="Test Render Mode:", font=(FONT_FAMILY, 10, "bold"), text_color=TEXT_PRIMARY)
        t_lbl.grid(row=0, column=2, padx=8, pady=(4, 1), sticky="w")
        self.test_var = ctk.StringVar(value="Full Song" if self.config.test_seconds is None else f"{int(self.config.test_seconds)}s Test")
        self.test_menu = ctk.CTkOptionMenu(
            settings_frame,
            values=["Full Song", "5s Test", "10s Test", "15s Test", "30s Test", "60s Test"],
            variable=self.test_var,
            fg_color=BG_INPUT,
            button_color=BTN_SECONDARY,
            text_color=TEXT_PRIMARY,
            height=26,
            font=(FONT_FAMILY, 9),
        )
        self.test_menu.grid(row=1, column=2, padx=8, pady=(0, 6), sticky="ew")

        # SwiftShader checkbox
        self.swiftshader_var = ctk.BooleanVar(value=True)
        self.swiftshader_cb = ctk.CTkCheckBox(
            settings_frame,
            text="SwiftShader (Cloud GL Rasterizer)",
            variable=self.swiftshader_var,
            font=(FONT_FAMILY, 10),
            text_color=TEXT_PRIMARY,
            checkbox_width=16,
            checkbox_height=16,
        )
        self.swiftshader_cb.grid(row=1, column=3, padx=8, pady=(0, 6), sticky="w")

        # ── 5. Detailed Batch Queue Table ────────────────────────────────────────
        self.queue_table = QueueTableWidget(self)
        self.queue_table.grid(row=5, column=0, padx=12, pady=4, sticky="nsew")

        # ── 6. Actions Bar ───────────────────────────────────────────────────────
        action_bar = ctk.CTkFrame(self, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4)
        action_bar.grid(row=6, column=0, padx=12, pady=4, sticky="ew")
        action_bar.grid_columnconfigure(2, weight=1)

        self.start_btn = ctk.CTkButton(
            action_bar,
            text="START CLOUD BATCH (CODESPACES)",
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=BTN_START,
            hover_color=BTN_START_HOVER,
            text_color=TEXT_LIGHT,
            height=36,
            width=260,
            command=self.start_cloud_batch,
        )
        self.start_btn.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.stop_btn = ctk.CTkButton(
            action_bar,
            text="STOP BATCH",
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=BTN_STOP,
            hover_color=BTN_STOP_HOVER,
            text_color=TEXT_LIGHT,
            height=36,
            width=130,
            state="disabled",
            command=self.stop_cloud_batch,
        )
        self.stop_btn.grid(row=0, column=1, padx=(0, 10), pady=8, sticky="w")

        tools_frame = ctk.CTkFrame(action_bar, fg_color="transparent")
        tools_frame.grid(row=0, column=3, padx=10, pady=8, sticky="e")

        open_btn = ctk.CTkButton(
            tools_frame,
            text="Open Out Folder",
            font=(FONT_FAMILY, 10),
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRIMARY,
            height=32,
            width=120,
            command=self._open_output_folder,
        )
        open_btn.pack(side="left", padx=4)

        play_btn = ctk.CTkButton(
            tools_frame,
            text="Play Latest Video",
            font=(FONT_FAMILY, 10),
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRIMARY,
            height=32,
            width=130,
            command=self._play_latest_video,
        )
        play_btn.pack(side="left", padx=4)

        # ── 7. Monospace Log Console ─────────────────────────────────────────────
        self.log_view = LogTextView(self, height=130)
        self.log_view.grid(row=7, column=0, padx=12, pady=(4, 10), sticky="ew")

    def _check_initial_connection(self):
        url = self.conn_bar.get_url()
        self.cloud_client.set_base_url(url)
        res = self.cloud_client.test_connection()
        if res.get("success"):
            data = res.get("data", {})
            self.conn_bar.set_status(True, f"{data.get('cpu_cores', 4)} Cores | {data.get('ram_total_gb', 8)}GB RAM")
            logger.success(f"Connected to GitHub Codespace ({url})")
        else:
            self.conn_bar.set_status(False, "(Run: python -m midiano_renderer.cloud_server in Codespace)")

    def _on_connect_clicked(self, url: str):
        self.cloud_client.set_base_url(url)
        res = self.cloud_client.test_connection()
        if res.get("success"):
            data = res.get("data", {})
            self.conn_bar.set_status(True, f"{data.get('cpu_cores', 4)} Cores | {data.get('ram_total_gb', 8)}GB RAM")
            messagebox.showinfo("Codespace Connected", f"Successfully connected to GitHub Codespaces!\nHardware: {data.get('cpu_cores')} vCPUs, {data.get('ram_total_gb')} GB RAM.")
        else:
            self.conn_bar.set_status(False)
            messagebox.showerror(
                "Connection Failed",
                f"Could not connect to Codespace at {url}.\n\n"
                f"Ensure the cloud server is running in Codespaces:\n"
                f"  python -m midiano_renderer.cloud_server\n"
                f"And that port 8000 is forwarded (or set to Public).\n\n"
                f"Details: {res.get('error')}",
            )

    def _on_midi_folder_changed(self, folder: str):
        self.config.midi_dir = folder
        self.config.save()
        self._scan_and_update_table(folder)

    def _on_out_folder_changed(self, folder: str):
        self.config.out_dir = folder
        self.config.save()

    def _scan_and_update_table(self, folder: str):
        midis = scan_midi_folder(folder)
        self.scanned_files.clear()
        for m in midis:
            details = probe_midi_details(m)
            self.scanned_files.append({
                "path": m,
                "filename": m.name,
                "duration_str": details.get("formatted_duration", "-"),
                "duration_s": details.get("duration_sec", 0.0),
            })
        self.queue_table.populate_local_files(self.scanned_files)
        logger.info(f"Scanned {len(self.scanned_files)} MIDI files from {folder}")

    def _on_cloud_telemetry(self, t: CloudTelemetryData):
        self.after(0, lambda: self._apply_telemetry_ui(t))

    def _apply_telemetry_ui(self, t: CloudTelemetryData):
        self.hardware_monitor.update_telemetry(t)
        if t.tasks:
            self.queue_table.update_tasks(t.tasks)

    def _on_log_entry(self, entry: LogEntry):
        self.after(0, lambda e=entry: self.log_view.append_log(e))

    def start_cloud_batch(self):
        midi_dir = self.midi_picker.get_path()
        out_dir = self.out_picker.get_path()

        if not midi_dir or not Path(midi_dir).is_dir():
            messagebox.showwarning("Missing Folder", "Please select a valid MIDI Input Folder.")
            return

        if not out_dir:
            messagebox.showwarning("Missing Folder", "Please select an MP4 Output Folder.")
            return

        if not self.scanned_files:
            messagebox.showwarning("No Files", "No .mid or .midi files found in the chosen folder.")
            return

        # Ensure cloud connected
        if not self.cloud_client.is_connected:
            res = self.cloud_client.test_connection()
            if not res.get("success"):
                messagebox.showerror(
                    "Codespace Not Connected",
                    f"Please connect to your GitHub Codespace server first.\n\n"
                    f"1. In Codespaces terminal, run:\n"
                    f"   python -m midiano_renderer.cloud_server\n"
                    f"2. Ensure port 8000 is forwarded.\n"
                    f"3. Click 'Test & Connect' above.",
                )
                return

        # Concurrency
        w_text = self.workers_var.get()
        concurrency = 4
        if "3" in w_text:
            concurrency = 3
        elif "2" in w_text:
            concurrency = 2
        elif "1" in w_text:
            concurrency = 1

        # Test mode
        t_sel = self.test_var.get()
        test_sec = None if "Full" in t_sel else float(t_sel.replace("s Test", "").strip())

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.is_rendering = True

        midi_paths = [f["path"] for f in self.scanned_files]

        def _bg_start():
            ok = self.cloud_client.submit_batch(
                midi_files=midi_paths,
                out_dir=Path(out_dir),
                concurrency=concurrency,
                fps=int(self.fps_var.get()),
                test_seconds=test_sec,
                viewport_w=self.config.viewport_w,
                viewport_h=self.config.viewport_h,
                use_swiftshader=self.swiftshader_var.get(),
            )
            if not ok:
                self.after(0, lambda: self._on_batch_ended())

        threading.Thread(target=_bg_start, daemon=True).start()

    def stop_cloud_batch(self):
        self.cloud_client.cancel_batch()
        self._on_batch_ended()

    def _on_batch_ended(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.is_rendering = False

    def _open_output_folder(self):
        out = self.out_picker.get_path()
        if out and Path(out).exists():
            if sys.platform == "win32":
                os.startfile(out)
            elif sys.platform == "darwin":
                subprocess.run(["open", out])
            else:
                subprocess.run(["xdg-open", out])

    def _play_latest_video(self):
        out = Path(self.out_picker.get_path())
        if out.is_dir():
            mp4s = sorted(out.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            if mp4s:
                latest = str(mp4s[0])
                if sys.platform == "win32":
                    os.startfile(latest)
                elif sys.platform == "darwin":
                    subprocess.run(["open", latest])
                else:
                    subprocess.run(["xdg-open", latest])
                return
        messagebox.showinfo("No Video", "No rendered MP4 video found yet.")

    def _on_close(self):
        if self.is_rendering:
            if not messagebox.askyesno("Batch in Progress", "Cloud render is running. Stop batch and exit?"):
                return
            self.cloud_client.cancel_batch()
        self.destroy()


def run_gui():
    app = MidianoApp()
    app.mainloop()
