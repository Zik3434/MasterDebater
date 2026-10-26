"""Self-update: download the latest code from GitHub and replace the app's files.

Keeps data/ (your rounds and case) and .venv untouched. Only the files that ship in the
repository are overwritten.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ZIP_URL = "https://github.com/zik3434/masterdebater/archive/refs/heads/main.zip"
KEEP = {"data", ".venv", "captures", "speeches", "__pycache__"}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def update_from_github(url: str = ZIP_URL) -> list[str]:
    """Download the main branch and copy its files over this install. Returns changed paths."""
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (fixed, https, our repo)
        payload = resp.read()
    changed: list[str] = []
    root = project_root()
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            zf.extractall(tmp)
        extracted = next(p for p in Path(tmp).iterdir() if p.is_dir())
        for src in extracted.rglob("*"):
            rel = src.relative_to(extracted)
            if rel.parts and rel.parts[0] in KEEP:
                continue
            dst = root / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            if dst.exists() and dst.read_bytes() == src.read_bytes():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(str(rel))
    return changed
