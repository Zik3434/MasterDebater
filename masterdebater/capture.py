"""Screen capture for the screenshot path (when you'd rather not save the Flexcel .json).

Uses `mss`, which works on Windows, macOS and Linux without extra system packages.
"""

from __future__ import annotations

from pathlib import Path


def list_monitors() -> list[dict]:
    import mss

    with mss.mss() as sct:
        # monitors[0] is the virtual "all screens" rectangle; 1..n are physical displays.
        return [dict(m) for m in sct.monitors]


def capture_screen(out_path: str | Path, monitor: int = 1) -> Path:
    """Save a PNG of the given monitor (1 = primary) and return its path."""
    import mss
    import mss.tools

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            monitor = 1
        shot = sct.grab(sct.monitors[monitor])
        mss.tools.to_png(shot.rgb, shot.size, output=str(out_path))
    return out_path
