"""Thread-safe logging subsystem for Midiano Batch Renderer.

Supports dual logging:
  1. Thread-safe GUI log view dispatch with color-coded levels.
  2. Rotating disk log file in ~/.midiano_renderer/logs/.
  3. Standard stdout/stderr fallback.

Formats messages with exact second timestamps:
  [14:32:07] Chromium launched (headless=False, SwiftShader)
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

from midiano_renderer.config import get_default_app_dir


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    PROGRESS = "PROGRESS"


@dataclass
class LogEntry:
    timestamp: str
    level: LogLevel
    message: str

    def format_line(self) -> str:
        return f"[{self.timestamp}] {self.message}"


class AppLogger:
    """Central application logger with thread-safe UI subscriber dispatch."""

    def __init__(self, name: str = "midiano_renderer"):
        self.name = name
        self._subscribers: list[Callable[[LogEntry], None]] = []
        self._lock = threading.Lock()
        self._log_queue: queue.Queue[LogEntry] = queue.Queue()
        self.min_level: LogLevel = LogLevel.INFO

        # Configure file logging
        log_dir = get_default_app_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        self.log_file = log_dir / f"midiano_render_{today}.log"

        self._py_logger = logging.getLogger(name)
        self._py_logger.setLevel(logging.DEBUG)
        if not self._py_logger.handlers:
            handler = RotatingFileHandler(
                str(self.log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            self._py_logger.addHandler(handler)

    def subscribe(self, callback: Callable[[LogEntry], None]) -> None:
        """Register a GUI listener callback that receives LogEntry objects."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[LogEntry], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Emit a log message with second-precision timestamp."""
        ts = time.strftime("%H:%M:%S")
        entry = LogEntry(timestamp=ts, level=level, message=message)

        # Write to python rotating file handler
        if level == LogLevel.ERROR:
            self._py_logger.error(message)
        elif level == LogLevel.WARNING:
            self._py_logger.warning(message)
        elif level == LogLevel.DEBUG:
            self._py_logger.debug(message)
        else:
            self._py_logger.info(message)

        # Stdout print (retains notebook behavior)
        line = entry.format_line()
        print(line, flush=True)

        # Dispatch to GUI subscribers
        with self._lock:
            subscribers = list(self._subscribers)

        for sub in subscribers:
            try:
                sub(entry)
            except Exception:
                pass

    def debug(self, msg: str) -> None:
        self.log(msg, LogLevel.DEBUG)

    def info(self, msg: str) -> None:
        self.log(msg, LogLevel.INFO)

    def success(self, msg: str) -> None:
        self.log(msg, LogLevel.SUCCESS)

    def warning(self, msg: str) -> None:
        self.log(msg, LogLevel.WARNING)

    def error(self, msg: str) -> None:
        self.log(msg, LogLevel.ERROR)

    def progress(self, msg: str) -> None:
        self.log(msg, LogLevel.PROGRESS)


# Global singleton logger instance
logger = AppLogger()
