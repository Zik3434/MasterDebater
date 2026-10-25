"""On-disk persistence for the in-app flow, case/blocks, and generated speeches.

Everything lives under ./data so a browser refresh, a crash, or restarting the app
never loses a round. One JSON file for the round, one for the materials.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .flow import Flow, Sheet
from .formats import LD, POLICY, DebateFormat, public_forum

DATA_DIR = Path("data")
ROUND_FILE = DATA_DIR / "round.json"
MATERIALS_FILE = DATA_DIR / "materials.json"
SPEECH_DIR = DATA_DIR / "speeches"

DEFAULT_ROWS = 30

LD_SHEETS = [
    ("1AC", ["1AC", "1NC", "1AR", "2NR", "2AR"]),
    ("1NC", ["1NC", "1AR", "2NR", "2AR"]),
]
POLICY_SHEETS = [("Case", ["1AC", "1NC", "2AC", "2NC/1NR", "1AR", "2NR", "2AR"])]


def _blank_rows(ncols: int, n: int = DEFAULT_ROWS) -> list[list[str]]:
    return [[""] * ncols for _ in range(n)]


def format_by_key(key: str, first_speaker: str = "pro") -> DebateFormat:
    if key == "policy":
        return POLICY
    if key == "pf":
        return public_forum(first_speaker)
    return LD


def default_sheets(fmt_key: str, first_speaker: str = "pro") -> list[dict]:
    if fmt_key == "policy":
        spec = POLICY_SHEETS
    elif fmt_key == "pf":
        fmt = public_forum(first_speaker)
        order = fmt.order()
        first = order[0].split()[0]
        second = order[1].split()[0]
        # Both cases get every speech after the constructives, like Flexcel.
        later = [s for s in order if not s.endswith("Constructive")]
        spec = [
            (f"{first} case", [f"{first} Constructive"] + later),
            (f"{second} case", [f"{second} Constructive"] + later),
        ]
    else:
        spec = LD_SHEETS
    return [{"name": n, "columns": c, "rows": _blank_rows(len(c))} for n, c in spec]


def new_round(fmt_key: str = "ld", first_speaker: str = "pro", name: str = "") -> dict:
    return {
        "name": name or datetime.now().strftime("Round %Y-%m-%d %H%M"),
        "format": fmt_key,
        "first_speaker": first_speaker,
        "side": "aff",
        "sheets": default_sheets(fmt_key, first_speaker),
        "cx": {"ask": "", "answers": ""},
        "notes": "",
        "speeches": [],   # [{"speech": "1AR", "text": ..., "notes": ..., "words": n, "at": iso}]
    }


def load_round() -> dict:
    if ROUND_FILE.exists():
        try:
            return json.loads(ROUND_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return new_round()


def save_round(rnd: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROUND_FILE.write_text(json.dumps(rnd, indent=1), encoding="utf-8")


def load_materials() -> dict:
    if MATERIALS_FILE.exists():
        try:
            return json.loads(MATERIALS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"pasted": "", "files": []}   # files: [{"name": ..., "text": ...}]


def save_materials(m: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_FILE.write_text(json.dumps(m, indent=1), encoding="utf-8")


def round_to_flow(rnd: dict) -> Flow:
    fmt_key = rnd.get("format", "ld")
    flow_type = {"ld": "LD Traditional Flow", "policy": "Policy Flow", "pf": "PF Flow"}[fmt_key]
    sheets = []
    for s in rnd["sheets"]:
        cols = list(s["columns"])
        rows = [[str(c or "") for c in r][: len(cols)] + [""] * max(0, len(cols) - len(r)) for r in s["rows"]]
        rows = [r for r in rows if any(c.strip() for c in r)]
        sheets.append(Sheet(s["name"], cols, rows))
    return Flow(flow_type, sheets, rnd.get("first_speaker", "pro"), source="masterdebater")


def round_context(rnd: dict) -> str:
    parts = []
    cx = rnd.get("cx", {})
    if cx.get("ask", "").strip():
        parts.append("CROSS-EX QUESTIONS I PLANNED/ASKED:\n" + cx["ask"].strip())
    if cx.get("answers", "").strip():
        parts.append("WHAT THEY SAID IN CROSS-EX:\n" + cx["answers"].strip())
    if rnd.get("notes", "").strip():
        parts.append("MY NOTES:\n" + rnd["notes"].strip())
    for sp in rnd.get("speeches", []):
        parts.append(f"MY {sp['speech']} AS DELIVERED (from earlier this round):\n{sp['text'].strip()}")
    return "\n\n".join(parts)


def sheets_from_flow(flow: Flow) -> list[dict]:
    """Convert an imported Flexcel flow into in-app sheets (padded with blank rows)."""
    out = []
    for s in flow.sheets:
        rows = [list(r) for r in s.rows]
        rows += _blank_rows(len(s.columns), max(10, DEFAULT_ROWS - len(rows)))
        out.append({"name": s.name, "columns": list(s.columns), "rows": rows})
    return out
