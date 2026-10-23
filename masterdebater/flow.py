"""Flow data model plus the Flexcel .json reader.

Flexcel (github.com/saranchockan/Flexcel) saves a flow with Ctrl+S as JSON:

    {
      "flow_type": "LD Traditional Flow" | "LD Plan Flow" | "Policy Flow" | "PF Flow",
      "flow-data": [ grid, grid, ... ],      # one Handsontable grid (rows x cols) per tab
      "boldElements": [...],                  # bold cell coordinates (ignored here)
      "tab-names": [...], "tab-types": [...], # LD Plan / Policy only
      "firstSpeaker": "Pro" | "Con"           # PF only
    }

Column headers are NOT stored in the file (except PF, where the first row of each grid
holds them), so we reconstruct them from the flow type and tab position exactly the way
Flexcel's own templates do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .formats import DebateFormat, canonical_speech, format_for_flow_type

LD_AC_COLUMNS = ["AC", "1NR", "1AR", "2NR", "2AR"]
LD_NC_COLUMNS = ["1NC", "1AR", "2NR", "2AR"]
POLICY_COLUMNS = ["1AC", "1NC", "2AC", "2NC/1NR", "1AR", "2NR", "2AR"]


@dataclass
class Sheet:
    """One tab of the flow: a grid whose columns are speeches."""

    name: str
    columns: list[str]              # canonical speech names, one per column
    rows: list[list[str]] = field(default_factory=list)

    def column_text(self, speech: str) -> list[str]:
        if speech not in self.columns:
            return []
        i = self.columns.index(speech)
        return [r[i] for r in self.rows if i < len(r) and r[i].strip()]

    def has_content(self, speech: str) -> bool:
        return bool(self.column_text(speech))


@dataclass
class Flow:
    flow_type: str
    sheets: list[Sheet]
    first_speaker: str = "pro"      # PF only
    source: str = "flexcel-json"    # or "screenshot"

    @property
    def format(self) -> DebateFormat:
        return format_for_flow_type(self.flow_type, self.first_speaker)

    def speeches_with_content(self) -> list[str]:
        fmt = self.format
        return [s.name for s in fmt.speeches if any(sh.has_content(s.name) for sh in self.sheets)]

    def is_empty(self) -> bool:
        return not self.speeches_with_content()

    def to_text(self) -> str:
        """Render the flow as plain text the model can read: sheet by sheet, row by row.

        Each row is an argument thread; cells are labelled with the speech so the model
        can see what was said, what was answered, and what was dropped.
        """
        out: list[str] = []
        for sh in self.sheets:
            out.append(f"=== SHEET: {sh.name} ===")
            out.append("Columns (speech order): " + " | ".join(sh.columns))
            n = 0
            for r in sh.rows:
                if not any(c.strip() for c in r):
                    continue
                n += 1
                out.append(f"-- row {n} --")
                for col, cell in zip(sh.columns, r):
                    cell = cell.strip()
                    out.append(f"[{col}] {cell if cell else '(empty)'}")
            if n == 0:
                out.append("(sheet is empty)")
            out.append("")
        return "\n".join(out).strip()

    def to_json(self) -> str:
        return json.dumps(
            {
                "flow_type": self.flow_type,
                "first_speaker": self.first_speaker,
                "source": self.source,
                "sheets": [
                    {"name": s.name, "columns": s.columns, "rows": s.rows} for s in self.sheets
                ],
            },
            indent=2,
        )


# ---------------------------------------------------------------------------
# Flexcel JSON reader
# ---------------------------------------------------------------------------

def _clean_grid(grid) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in grid or []:
        if row is None:
            continue
        cells = ["" if c is None else str(c) for c in row]
        rows.append(cells)
    # Drop trailing empty rows (Flexcel pads every grid to 40+ rows).
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    return rows


def _normalize(fmt: DebateFormat, headers: list[str]) -> list[str]:
    cols = []
    for h in headers:
        c = canonical_speech(fmt, h)
        cols.append(c or h.strip() or "?")
    return cols


def parse_flexcel(data: dict) -> Flow:
    """Turn a parsed Flexcel save file into a Flow."""
    if "flow-data" not in data:
        raise ValueError("Not a Flexcel flow file: missing 'flow-data'")
    flow_type = data.get("flow_type", "LD Traditional Flow")
    first_speaker = (data.get("firstSpeaker") or "Pro").lower()
    fmt = format_for_flow_type(flow_type, first_speaker)
    grids = data["flow-data"]
    tab_names = data.get("tab-names") or []
    tab_types = data.get("tab-types") or []

    sheets: list[Sheet] = []
    for i, grid in enumerate(grids):
        rows = _clean_grid(grid)
        if fmt.key == "pf":
            # Flexcel stores the PF headers in the first row of each grid.
            headers = rows[0] if rows else []
            body = rows[1:] if rows else []
            headers = [h for h in headers if h.strip()]
            body = [r[: len(headers)] for r in body]
            name = "Sheet 1 (first speaker's case)" if i == 0 else "Sheet 2 (second speaker's case)"
            sheets.append(Sheet(name, _normalize(fmt, headers), body))
            continue

        if fmt.key == "policy":
            headers = POLICY_COLUMNS
            name = tab_names[i] if i < len(tab_names) and tab_names[i] else f"Sheet {i + 1}"
        else:  # LD traditional / LD plan
            ttype = tab_types[i] if i < len(tab_types) else ("1AC" if i == 0 else "1NC")
            headers = LD_AC_COLUMNS if ttype == "1AC" else LD_NC_COLUMNS
            default = "1AC (aff case)" if ttype == "1AC" else "1NC (neg case / off-case)"
            name = tab_names[i] if i < len(tab_names) and tab_names[i] else default
        body = [r[: len(headers)] + [""] * max(0, len(headers) - len(r)) for r in rows]
        sheets.append(Sheet(name, _normalize(fmt, list(headers)), body))

    return Flow(flow_type=flow_type, sheets=sheets, first_speaker=first_speaker)


def load_flexcel(path: str | Path) -> Flow:
    with open(path, "r", encoding="utf-8") as f:
        return parse_flexcel(json.load(f))


def flow_from_dict(d: dict) -> Flow:
    """Rebuild a Flow from the JSON produced by `Flow.to_json()` or the vision transcriber."""
    first = (d.get("first_speaker") or "pro").lower()
    fmt = format_for_flow_type(d.get("flow_type", ""), first)
    sheets = []
    for s in d.get("sheets", []):
        cols = _normalize(fmt, [str(c) for c in s.get("columns", [])])
        rows = [[str(c) if c is not None else "" for c in r] for r in s.get("rows", [])]
        rows = [r[: len(cols)] + [""] * max(0, len(cols) - len(r)) for r in rows]
        sheets.append(Sheet(str(s.get("name", "Sheet")), cols, rows))
    return Flow(d.get("flow_type", "LD Traditional Flow"), sheets, first, d.get("source", "dict"))


# ---------------------------------------------------------------------------
# Which speech is next?
# ---------------------------------------------------------------------------

def next_speech(flow: Flow, side: str) -> str:
    """Pick the speech the user should give next.

    Rule: find the latest speech (in format order) that has anything flowed, then take the
    first speech after it that belongs to `side`. An empty flow means the first speech for
    that side (the 1AC / 1NC).
    """
    fmt = flow.format
    order = fmt.order()
    filled = flow.speeches_with_content()
    last_idx = max((order.index(s) for s in filled), default=-1)
    for s in fmt.speeches[last_idx + 1:]:
        if s.side == side:
            return s.name
    # Everything is flowed already (round is over) - fall back to the side's last speech.
    mine = fmt.speeches_for(side)
    return mine[-1].name
