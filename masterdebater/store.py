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
        "id": __import__("uuid").uuid4().hex,
        "cx": {"ask": "", "answers": "", "suggested": [], "suggested_for": ""},
        "notes": "",
        "speeches": [],   # [{"speech": "1AR", "text": ..., "notes": ..., "words": n, "at": iso}]
    }


def load_round() -> dict:
    if ROUND_FILE.exists():
        try:
            rnd = json.loads(ROUND_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return new_round()
        # Rounds saved by older versions lack these fields.
        rnd.setdefault("id", __import__("uuid").uuid4().hex)
        rnd.setdefault("cx", {})
        for k, v in {"ask": "", "answers": "", "suggested": [], "suggested_for": ""}.items():
            rnd["cx"].setdefault(k, v)
        return rnd
    return new_round()


def save_round(rnd: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROUND_FILE.write_text(json.dumps(rnd, indent=1), encoding="utf-8")


SECTIONS = {
    "aff_case": "Aff case (1AC)",
    "aff_blocks": "Aff blocks (1AR / 2AR answers)",
    "neg_case": "Neg case (1NC / NC)",
    "neg_blocks": "Neg blocks (2NR answers)",
    "shared": "Both sides (frameworks, generic cards, theory)",
}
SIDE_SECTIONS = {
    "aff": ["aff_case", "aff_blocks", "shared"],
    "neg": ["neg_case", "neg_blocks", "shared"],
}


def empty_materials() -> dict:
    # sections: {key: text}; files: [{"name", "text", "side": "aff"|"neg"|"both"}]
    return {
        "resolution": "",
        "sections": {k: "" for k in SECTIONS},
        "files": [],
        "cx_auto": False,   # auto-draft cross-ex questions while flowing
        # Pre-flow per side: the drafted lines and a hash of the case they were drafted from.
        "preflow": {"aff": {"text": "", "case_hash": ""}, "neg": {"text": "", "case_hash": ""}},
    }


def load_materials() -> dict:
    m = empty_materials()
    if MATERIALS_FILE.exists():
        try:
            raw = json.loads(MATERIALS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        # Older saves had one "pasted" box; keep that text as shared material.
        if raw.get("pasted", "").strip():
            m["sections"]["shared"] = raw["pasted"]
        m["resolution"] = raw.get("resolution", "") or ""
        m["cx_auto"] = bool(raw.get("cx_auto", False))
        for k, v in (raw.get("sections") or {}).items():
            if k in m["sections"]:
                m["sections"][k] = v or ""
        for sd, pf in (raw.get("preflow") or {}).items():
            if sd in m["preflow"] and isinstance(pf, dict):
                m["preflow"][sd] = {"text": pf.get("text", "") or "", "case_hash": pf.get("case_hash", "") or ""}
        for f in raw.get("files") or []:
            m["files"].append({"name": f["name"], "text": f.get("text", ""), "side": f.get("side", "both")})
    return m


def materials_for_side(m: dict, side: str) -> list[tuple[str, str]]:
    """(label, text) pairs the writer may use for this side: that side's sections, shared
    material, and uploaded files tagged with that side or 'both'. Nothing from the other side."""
    pairs = []
    for key in SIDE_SECTIONS[side]:
        text = m["sections"].get(key, "")
        if text.strip():
            pairs.append((SECTIONS[key], text))
    for f in m["files"]:
        if f.get("side", "both") in (side, "both") and f.get("text", "").strip():
            pairs.append((f["name"], f["text"]))
    return pairs


def material_words(m: dict, side: str) -> int:
    return sum(len(t.split()) for _, t in materials_for_side(m, side))


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


# ---------------------------------------------------------------------------
# Demo round for testing (matches examples/sample_case.md)
# ---------------------------------------------------------------------------

def demo_round() -> dict:
    """An LD round, flowed through the 1NC, from the Aff's seat. For trying the app."""
    rnd = new_round("ld", name="Demo round (aff)")
    rnd["side"] = "aff"
    ac = rnd["sheets"][0]
    nc = rnd["sheets"][1]
    ac_rows = [
        ["FW: V morality / VC minimizing structural violence (Galtung 69). State monopolizes force -> owes basic needs",
         "FW: util / weigh consequences. SV is non-falsifiable, everything is 'structural'; prefer clear impact calc", "", "", ""],
        ["C1: homelessness kills - 3.5x mortality, ~20k deaths/yr (Rivera 24)",
         "Rivera is correlation - alt causes: addiction + mental illness, housing doesn't fix", "", "", ""],
        ["Solvency: Finland Housing First cut long-term homelessness 70% (Pleace 22)",
         "No solvency - US has no units; a right w/o supply = paper right. Finland is 5M ppl, not comparable", "", "", ""],
        ["C2: stability -> education (+0.3 SD) + 26% employment (Okafor 23)",
         "Non-unique - vouchers + Section 8 already exist. Turn: housing mandates = rent control -> cuts supply 15% (Diamond 19)", "", "", ""],
        ["Cost offense: PSH 20k vs 35-45k street costs (Culhane 20)",
         "Cost: 100B/yr -> tradeoff w/ Medicaid + SNAP, hurts same ppl", "", "", ""],
    ]
    nc_rows = [
        ["NC FW: V justice / VC consistency w/ property rights (Nozick). Taxation for housing = forced labor", "", "", ""],
        ["NC C1: positive rights are incoherent - a right to a good = a claim on someone else's labor; only negative rights are real rights", "", "", ""],
        ["NC C2: federal guarantee crowds out local solutions - HUD track record, one-size-fits-all (Husock 21)", "", "", ""],
        ["Underview: judge should presume neg on vague plans - 'guarantee' has no mechanism", "", "", ""],
    ]
    for i, r in enumerate(ac_rows):
        ac["rows"][i] = r
    for i, r in enumerate(nc_rows):
        nc["rows"][i] = r
    rnd["cx"]["ask"] = (
        "Does a right to a fair trial require public spending? Is that incoherent?\n"
        "Does your property-rights framework say anything about what a homeless person is owed?\n"
        "Is the Diamond card about rent control or about a government housing guarantee?"
    )
    rnd["cx"]["answers"] = (
        "Conceded the fair trial example requires spending but said it's 'different'.\n"
        "Said Nozick framework owes the homeless 'nothing beyond non-interference'.\n"
        "Admitted Diamond 19 studies San Francisco rent control, said it 'still applies'."
    )
    rnd["notes"] = "Judge: flay-ish, likes clear weighing and extensions. Collapse to FW + C1 + solvency in the 2AR."
    return rnd


def demo_case_text() -> str:
    path = Path(__file__).resolve().parent.parent / "examples" / "sample_case.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""



# ---------------------------------------------------------------------------
# Pre-flow: draft once per topic, drop into the debater's own sheet
# ---------------------------------------------------------------------------

def case_text_for_side(m: dict, side: str) -> str:
    """The side's constructive case: its case box, or failing that its uploaded files."""
    text = m["sections"].get(f"{side}_case", "")
    if text.strip():
        return text
    return "\n\n".join(f["text"] for f in m["files"] if f.get("side") == side and f.get("text", "").strip())


def case_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16] if text.strip() else ""


def preflow_lines(m: dict, side: str) -> list[str]:
    return [ln.strip() for ln in m["preflow"][side]["text"].splitlines() if ln.strip()]


def own_sheet_index(rnd: dict, side: str) -> int | None:
    """The sheet whose first column is this side's constructive (1AC sheet for Aff, 1NC sheet for Neg)."""
    fmt = format_by_key(rnd.get("format", "ld"), rnd.get("first_speaker", "pro"))
    first = fmt.speeches_for(side)[0].name
    for i, sh in enumerate(rnd["sheets"]):
        if sh["columns"] and sh["columns"][0] == first:
            return i
    return None


def _col0_empty(sheet: dict) -> bool:
    return not any((r[0] if r else "").strip() for r in sheet["rows"])


def _sheet_is_exactly(sheet: dict, lines: list[str]) -> bool:
    """True when the sheet holds those lines in column 1 and nothing else (untouched auto-insert)."""
    filled = [r for r in sheet["rows"] if any(c.strip() for c in r)]
    return (len(filled) == len(lines)
            and all(r[0].strip() == ln and not any(c.strip() for c in r[1:]) for r, ln in zip(filled, lines)))


def apply_preflow(rnd: dict, m: dict, side: str) -> bool:
    """Put this side's pre-flow in column 1 of its own sheet if that column is empty.

    If an earlier automatic insert for the other side is still untouched (a mis-click on the
    side switch), take it back out first. Returns True if the round changed."""
    changed = False
    prev = rnd.get("auto_preflow")
    if prev and prev.get("side") != side:
        idx = prev.get("sheet")
        if idx is not None and idx < len(rnd["sheets"]) and _sheet_is_exactly(rnd["sheets"][idx], prev.get("lines", [])):
            sh = rnd["sheets"][idx]
            sh["rows"] = _blank_rows(len(sh["columns"]), max(DEFAULT_ROWS, len(sh["rows"])))
            changed = True
        rnd.pop("auto_preflow", None)

    lines = preflow_lines(m, side)
    idx = own_sheet_index(rnd, side)
    if not lines or idx is None or rnd.get("auto_preflow", {}).get("side") == side:
        return changed
    sh = rnd["sheets"][idx]
    if not _col0_empty(sh):
        return changed
    ncols = len(sh["columns"])
    rows = [[ln] + [""] * (ncols - 1) for ln in lines]
    rows += _blank_rows(ncols, max(10, DEFAULT_ROWS - len(rows)))
    sh["rows"] = rows
    rnd["auto_preflow"] = {"side": side, "sheet": idx, "lines": lines}
    return True



# ---------------------------------------------------------------------------
# Cross-ex: which opponent speech to question, and when the flow of it changed
# ---------------------------------------------------------------------------

def cx_target(rnd: dict, side: str) -> str | None:
    """The opponent's latest constructive that has anything flowed, or None."""
    fmt = format_by_key(rnd.get("format", "ld"), rnd.get("first_speaker", "pro"))
    opp = "neg" if side == "aff" else "aff"
    flow = round_to_flow(rnd)
    filled = set(flow.speeches_with_content())
    target = None
    for sp in fmt.speeches:
        if sp.side == opp and sp.kind in ("constructive", "block") and sp.name in filled:
            target = sp.name
    return target


def cx_signature(rnd: dict, target: str) -> str:
    """Hash of everything flowed in the target speech's columns, across all sheets."""
    import hashlib

    parts = []
    for sh in rnd["sheets"]:
        if target in sh["columns"]:
            i = sh["columns"].index(target)
            parts += [r[i].strip() for r in sh["rows"] if i < len(r) and r[i].strip()]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16] if parts else ""
