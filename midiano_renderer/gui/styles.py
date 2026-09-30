"""Classic, clean old-school light desktop application styling.

No dark neon or synthetic AI themes. Professional, crisp white-and-gray desktop palette
with high-contrast typography, classic borders, and standard engineering UI elements.
"""

from __future__ import annotations

# Clean Old-School Light Palette
BG_WINDOW = "#eceff1"          # Classic application window background (soft light gray)
BG_PANEL = "#ffffff"           # Clean crisp white card surfaces
BG_PANEL_ALT = "#f8fafc"       # Alternate subtle panel background
BG_INPUT = "#ffffff"           # Text entry fields
BORDER_COLOR = "#cbd5e1"       # Subtle, crisp gray borders
BORDER_ACTIVE = "#94a3b8"

# High-Contrast Typography Colors
TEXT_PRIMARY = "#0f172a"       # Solid readable dark charcoal/black
TEXT_SECONDARY = "#334155"     # Dark slate
TEXT_MUTED = "#64748b"         # Muted gray label text
TEXT_LIGHT = "#ffffff"         # White text on colored buttons

# Classic Functional Button Accents
BTN_START = "#16a34a"          # Classic green
BTN_START_HOVER = "#15803d"
BTN_STOP = "#dc2626"           # Classic red
BTN_STOP_HOVER = "#b91c1c"
BTN_PRIMARY = "#2563eb"        # Classic royal blue
BTN_PRIMARY_HOVER = "#1d4ed8"
BTN_SECONDARY = "#e2e8f0"      # Classic light tool button
BTN_SECONDARY_HOVER = "#cbd5e1"
BTN_PURPLE = "#7c3aed"

# Telemetry Status Colors
STATUS_ONLINE = "#16a34a"
STATUS_OFFLINE = "#dc2626"
STATUS_PENDING = "#d97706"

# Monospace Log Syntax Colors (Optimized for White Background)
LOG_BG = "#ffffff"
LOG_FG = "#0f172a"
LOG_COLORS = {
    "INFO": "#0f172a",          # Sharp dark text
    "SUCCESS": "#15803d",       # Deep forest green
    "WARNING": "#b45309",       # Deep amber
    "ERROR": "#b91c1c",         # Deep crimson
    "PROGRESS": "#0284c7",      # Sharp blue
    "DEBUG": "#64748b",         # Slate gray
    "TIMESTAMP": "#64748b",     # Clean gray timestamp
}

# Typography
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"
