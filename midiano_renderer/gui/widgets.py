"""Rich, detailed classic light desktop UI widgets for Midiano Batch Renderer."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from midiano_renderer.core.cloud_client import CloudTelemetryData
from midiano_renderer.gui.styles import (
    BG_INPUT,
    BG_PANEL,
    BG_PANEL_ALT,
    BG_WINDOW,
    BORDER_ACTIVE,
    BORDER_COLOR,
    BTN_PRIMARY,
    BTN_PRIMARY_HOVER,
    BTN_SECONDARY,
    BTN_SECONDARY_HOVER,
    BTN_START,
    BTN_START_HOVER,
    BTN_STOP,
    FONT_FAMILY,
    FONT_MONO,
    LOG_BG,
    LOG_COLORS,
    LOG_FG,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_PENDING,
    TEXT_LIGHT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from midiano_renderer.utils.logging import LogEntry


class FolderPickerWidget(ctk.CTkFrame):
    """Classic desktop folder selector with entry, browse button, and validation."""

    def __init__(
        self,
        master,
        label_text: str,
        default_path: str = "",
        on_change: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4, **kwargs)
        self.on_change = on_change

        self.grid_columnconfigure(1, weight=1)

        self.label = ctk.CTkLabel(
            self,
            text=label_text,
            font=(FONT_FAMILY, 11, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.label.grid(row=0, column=0, columnspan=3, padx=10, pady=(8, 2), sticky="w")

        self.entry = ctk.CTkEntry(
            self,
            placeholder_text=f"Select {label_text.lower()}...",
            fg_color=BG_INPUT,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            height=30,
            font=(FONT_FAMILY, 10),
        )
        self.entry.grid(row=1, column=0, columnspan=2, padx=(10, 6), pady=(0, 8), sticky="ew")
        if default_path:
            self.entry.insert(0, default_path)

        self.browse_btn = ctk.CTkButton(
            self,
            text="Browse...",
            width=80,
            height=30,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRIMARY,
            font=(FONT_FAMILY, 10, "bold"),
            command=self._on_browse,
        )
        self.browse_btn.grid(row=1, column=2, padx=(0, 10), pady=(0, 8), sticky="e")

    def _on_browse(self) -> None:
        initial = self.get_path() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=initial, title=f"Select {self.label.cget('text')}")
        if chosen:
            self.set_path(chosen)
            if self.on_change:
                self.on_change(chosen)

    def get_path(self) -> str:
        return self.entry.get().strip()

    def set_path(self, path: str) -> None:
        self.entry.delete(0, tk.END)
        self.entry.insert(0, path)


class CodespaceConnectionWidget(ctk.CTkFrame):
    """Dedicated GitHub Codespaces connection & URL configuration bar."""

    def __init__(
        self,
        master,
        default_url: str = "http://localhost:8000",
        on_connect: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4, **kwargs)
        self.on_connect = on_connect

        self.grid_columnconfigure(1, weight=1)

        # Title / Status LED
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, columnspan=3, padx=10, pady=(6, 2), sticky="ew")

        lbl = ctk.CTkLabel(
            title_frame,
            text="GITHUB CODESPACE CLOUD BRIDGE",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=TEXT_MUTED,
        )
        lbl.pack(side="left")

        self.status_pill = ctk.CTkLabel(
            title_frame,
            text="● DISCONNECTED",
            font=(FONT_FAMILY, 9, "bold"),
            text_color=STATUS_OFFLINE,
        )
        self.status_pill.pack(side="right")

        # URL Input & Connect Button
        self.url_label = ctk.CTkLabel(
            self,
            text="Codespace URL (Port 8000):",
            font=(FONT_FAMILY, 10),
            text_color=TEXT_SECONDARY,
        )
        self.url_label.grid(row=1, column=0, padx=(10, 6), pady=(0, 6), sticky="w")

        self.url_entry = ctk.CTkEntry(
            self,
            placeholder_text="http://localhost:8000 or https://<codespace>-8000.app.github.dev",
            fg_color=BG_INPUT,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            height=28,
            font=(FONT_MONO, 10),
        )
        self.url_entry.grid(row=1, column=1, padx=(0, 6), pady=(0, 6), sticky="ew")
        self.url_entry.insert(0, default_url)

        self.connect_btn = ctk.CTkButton(
            self,
            text="Test & Connect",
            width=110,
            height=28,
            fg_color=BTN_PRIMARY,
            hover_color=BTN_PRIMARY_HOVER,
            text_color=TEXT_LIGHT,
            font=(FONT_FAMILY, 10, "bold"),
            command=self._handle_connect,
        )
        self.connect_btn.grid(row=1, column=2, padx=(0, 10), pady=(0, 6), sticky="e")

    def _handle_connect(self):
        url = self.url_entry.get().strip()
        if self.on_connect:
            self.on_connect(url)

    def set_status(self, connected: bool, message: str = ""):
        if connected:
            self.status_pill.configure(text=f"● ONLINE — {message}", text_color=STATUS_ONLINE)
            self.connect_btn.configure(text="Connected ✓", fg_color=BTN_START)
        else:
            self.status_pill.configure(text=f"● OFFLINE {message}", text_color=STATUS_OFFLINE)
            self.connect_btn.configure(text="Test & Connect", fg_color=BTN_PRIMARY)

    def get_url(self) -> str:
        return self.url_entry.get().strip()


class CloudHardwareMonitor(ctk.CTkFrame):
    """Detailed visualizer for GitHub Codespaces 4-core CPU saturation and RAM consumption."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4, **kwargs)
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Header
        header = ctk.CTkLabel(
            self,
            text="CODESPACE RESOURCE SATURATION (4-CORE CPU & RAM MONITOR)",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        header.grid(row=0, column=0, columnspan=4, padx=10, pady=(6, 4), sticky="w")

        # Total CPU load
        self.cpu_total_lbl = ctk.CTkLabel(
            self,
            text="Total CPU Load: 0%",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.cpu_total_lbl.grid(row=1, column=0, padx=10, pady=1, sticky="w")

        self.cpu_total_bar = ctk.CTkProgressBar(self, fg_color=BG_PANEL_ALT, progress_color=BTN_PRIMARY, height=8)
        self.cpu_total_bar.grid(row=2, column=0, padx=10, pady=(0, 6), sticky="ew")
        self.cpu_total_bar.set(0.0)

        # RAM Usage
        self.ram_lbl = ctk.CTkLabel(
            self,
            text="RAM Usage: 0.0 / 8.0 GB (0%)",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.ram_lbl.grid(row=1, column=1, padx=10, pady=1, sticky="w")

        self.ram_bar = ctk.CTkProgressBar(self, fg_color=BG_PANEL_ALT, progress_color=STATUS_ONLINE, height=8)
        self.ram_bar.grid(row=2, column=1, padx=10, pady=(0, 6), sticky="ew")
        self.ram_bar.set(0.0)

        # Active Parallel Workers
        self.workers_lbl = ctk.CTkLabel(
            self,
            text="Cloud Workers: 0 / 4 Active",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.workers_lbl.grid(row=1, column=2, padx=10, pady=1, sticky="w")

        self.workers_bar = ctk.CTkProgressBar(self, fg_color=BG_PANEL_ALT, progress_color="#d97706", height=8)
        self.workers_bar.grid(row=2, column=2, padx=10, pady=(0, 6), sticky="ew")
        self.workers_bar.set(0.0)

        # Per-core breakdown preview
        self.per_core_lbl = ctk.CTkLabel(
            self,
            text="Cores: [C1: 0% | C2: 0% | C3: 0% | C4: 0%]",
            font=(FONT_MONO, 9),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.per_core_lbl.grid(row=1, column=3, rowspan=2, padx=10, pady=2, sticky="ew")

    def update_telemetry(self, t: CloudTelemetryData):
        # CPU
        pct = max(0.0, min(1.0, t.cpu_total_pct / 100.0))
        self.cpu_total_bar.set(pct)
        color = BTN_STOP if pct > 0.85 else (STATUS_PENDING if pct > 0.6 else BTN_PRIMARY)
        self.cpu_total_bar.configure(progress_color=color)
        self.cpu_total_lbl.configure(text=f"Total CPU Load: {t.cpu_total_pct:.1f}%")

        # RAM
        ram_ratio = max(0.0, min(1.0, t.ram_pct / 100.0))
        self.ram_bar.set(ram_ratio)
        self.ram_lbl.configure(text=f"RAM: {t.ram_used_gb:.1f} / {t.ram_total_gb:.1f} GB ({t.ram_pct:.0f}%)")

        # Workers
        w_ratio = max(0.0, min(1.0, t.active_workers / max(1, t.total_workers)))
        self.workers_bar.set(w_ratio)
        self.workers_lbl.configure(text=f"Cloud Workers: {t.active_workers} / {t.total_workers} Active")

        # Cores string
        if t.cpu_per_core:
            core_strs = [f"C{i+1}: {val:.0f}%" for i, val in enumerate(t.cpu_per_core[:4])]
            self.per_core_lbl.configure(text=" | ".join(core_strs))


class QueueTableWidget(ctk.CTkFrame):
    """Detailed multi-column file queue table showing MIDI filename, duration, assigned worker, and live status."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkLabel(
            self,
            text="BATCH RENDER QUEUE (MULTI-WORKER DISPATCH TABLE)",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        header.grid(row=0, column=0, padx=10, pady=(6, 2), sticky="w")

        # Classic ttk.Treeview Table
        columns = ("filename", "duration", "worker", "status", "progress")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=6)

        self.tree.heading("filename", text="MIDI File")
        self.tree.heading("duration", text="Length")
        self.tree.heading("worker", text="Assigned Worker")
        self.tree.heading("status", text="Cloud Status")
        self.tree.heading("progress", text="Progress")

        self.tree.column("filename", width=280, anchor="w")
        self.tree.column("duration", width=80, anchor="center")
        self.tree.column("worker", width=120, anchor="center")
        self.tree.column("status", width=130, anchor="center")
        self.tree.column("progress", width=90, anchor="center")

        self.tree.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")

        # Scrollbar
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, padx=(0, 6), pady=(0, 8), sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

    def populate_local_files(self, files: List[dict]):
        self.tree.delete(*self.tree.get_children())
        for f in files:
            self.tree.insert(
                "",
                "end",
                iid=f["filename"],
                values=(
                    f["filename"],
                    f.get("duration_str", "Unknown"),
                    "-",
                    "Ready",
                    "0%",
                ),
            )

    def update_tasks(self, tasks: List[dict]):
        for t in tasks:
            fn = t.get("filename")
            worker_str = f"Worker {t.get('worker_id')}" if t.get("worker_id") is not None else "In Queue"
            status = t.get("status", "Pending")
            pct_str = f"{t.get('progress_pct', 0.0):.0f}%"

            if self.tree.exists(fn):
                self.tree.set(fn, "worker", worker_str)
                self.tree.set(fn, "status", status)
                self.tree.set(fn, "progress", pct_str)
            else:
                self.tree.insert(
                    "",
                    "end",
                    iid=fn,
                    values=(fn, f"{t.get('duration_s', 0):.0f}s", worker_str, status, pct_str),
                )


class LogTextView(ctk.CTkFrame):
    """Classic clean white monospace execution console."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=BG_PANEL, border_color=BORDER_COLOR, border_width=1, corner_radius=4, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header bar
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=10, pady=(6, 2), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header_frame,
            text="EXECUTION & STREAMING LOG CONSOLE",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=TEXT_MUTED,
        )
        title.grid(row=0, column=0, sticky="w")

        ctrls = ctk.CTkFrame(header_frame, fg_color="transparent")
        ctrls.grid(row=0, column=1, sticky="e")

        self.auto_scroll_var = ctk.BooleanVar(value=True)
        self.auto_scroll_cb = ctk.CTkCheckBox(
            ctrls,
            text="Auto-scroll",
            variable=self.auto_scroll_var,
            font=(FONT_FAMILY, 10),
            text_color=TEXT_SECONDARY,
            checkbox_width=15,
            checkbox_height=15,
        )
        self.auto_scroll_cb.pack(side="left", padx=6)

        clear_btn = ctk.CTkButton(
            ctrls,
            text="Clear",
            width=50,
            height=22,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRIMARY,
            font=(FONT_FAMILY, 9),
            command=self.clear,
        )
        clear_btn.pack(side="left", padx=2)

        copy_btn = ctk.CTkButton(
            ctrls,
            text="Copy All",
            width=60,
            height=22,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRIMARY,
            font=(FONT_FAMILY, 9),
            command=self.copy_all,
        )
        copy_btn.pack(side="left", padx=2)

        # Clean text widget
        self.text_widget = tk.Text(
            self,
            wrap="word",
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=(FONT_MONO, 10),
        )
        self.text_widget.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")

        scroll = ctk.CTkScrollbar(self, command=self.text_widget.yview)
        scroll.grid(row=1, column=1, padx=(0, 6), pady=(0, 8), sticky="ns")
        self.text_widget.configure(yscrollcommand=scroll.set)

        # Configure color tags
        for level_name, color in LOG_COLORS.items():
            self.text_widget.tag_configure(level_name, foreground=color)
        self.text_widget.tag_configure("TIMESTAMP", foreground=LOG_COLORS["TIMESTAMP"])

    def append_log(self, entry: LogEntry) -> None:
        self.text_widget.configure(state="normal")
        ts_str = f"[{entry.timestamp}] "
        msg_str = f"{entry.message}\n"

        self.text_widget.insert(tk.END, ts_str, "TIMESTAMP")
        self.text_widget.insert(tk.END, msg_str, entry.level.value)

        if self.auto_scroll_var.get():
            self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")

    def clear(self) -> None:
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", tk.END)
        self.text_widget.configure(state="disabled")

    def copy_all(self) -> None:
        content = self.text_widget.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(content)
        messagebox.showinfo("Copied", "Logs copied to clipboard.")
