"""MIDI file parsing and duration inspection.

Maps directly to Notebook Cell 1 and Cell 3:
  - Notebook Cell 1: import mido
  - Notebook Cell 3:
      def fmt_hms(seconds: float) -> str:
      def midi_duration_ms(path: Path) -> float:
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union


def fmt_hms(seconds: float) -> str:
    """Format seconds into 'Xm Ys' or 'Xh Ym Zs' string.
    
    Mapped directly from Notebook Cell 3:
      def fmt_hms(seconds: float) -> str:
    """
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def midi_duration_ms(path: Union[Path, str]) -> float:
    """Calculate MIDI duration in milliseconds using mido.
    
    Mapped directly from Notebook Cell 3:
      def midi_duration_ms(path: Path) -> float:
          import mido
          return max(mido.MidiFile(str(path)).length, 0.1) * 1000.0
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"MIDI file does not exist: {path}")

    try:
        import mido
        mid = mido.MidiFile(str(p))
        length = getattr(mid, "length", 0.0)
        return max(float(length), 0.1) * 1000.0
    except Exception as e:
        # Fallback duration parser if mido encounters unconventional headers
        return _fallback_midi_duration_ms(p, fallback_error=e)


def get_midi_duration_seconds(path: Union[Path, str]) -> float:
    """Return MIDI duration in seconds."""
    return midi_duration_ms(path) / 1000.0


def scan_midi_folder(folder: Union[Path, str]) -> List[Path]:
    """Scan folder for valid .mid and .midi files, sorted alphabetically.
    
    Mapped from Notebook Cell 3 (run_batch):
      midis = sorted(
          p for p in MIDI_DIR.iterdir()
          if p.is_file() and p.suffix.lower() in {".mid", ".midi"}
      )
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return []
    return sorted(
        p for p in folder_path.iterdir()
        if p.is_file() and p.suffix.lower() in {".mid", ".midi"}
    )


def probe_midi_details(path: Union[Path, str]) -> Dict[str, Union[str, int, float]]:
    """Return detailed metadata for the given MIDI file."""
    p = Path(path)
    info: Dict[str, Union[str, int, float]] = {
        "filename": p.name,
        "size_bytes": p.stat().st_size if p.exists() else 0,
        "duration_sec": 0.0,
        "formatted_duration": "0m 00s",
        "tracks": 0,
        "valid": False,
        "error": "",
    }
    try:
        import mido
        mid = mido.MidiFile(str(p))
        dur_s = max(float(getattr(mid, "length", 0.0)), 0.1)
        info["duration_sec"] = dur_s
        info["formatted_duration"] = fmt_hms(dur_s)
        info["tracks"] = len(mid.tracks)
        info["ticks_per_beat"] = getattr(mid, "ticks_per_beat", 480)
        info["valid"] = True
    except Exception as exc:
        info["error"] = str(exc)
        try:
            dur_s = _fallback_midi_duration_ms(p, fallback_error=exc) / 1000.0
            info["duration_sec"] = dur_s
            info["formatted_duration"] = fmt_hms(dur_s)
            info["valid"] = True
        except Exception:
            pass
    return info


def _fallback_midi_duration_ms(path: Path, fallback_error: Exception) -> float:
    """Basic fallback parser in case mido failed on a non-standard chunk."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if not data.startswith(b"MThd"):
            raise ValueError(f"Invalid MIDI header: {fallback_error}")
        # Default fallback estimate if parse fails completely
        return 180.0 * 1000.0
    except Exception:
        raise ValueError(f"Cannot parse MIDI file '{path.name}': {fallback_error}")
