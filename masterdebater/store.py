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
    return {"sections": {k: "" for k in SECTIONS}, "files": []}


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
        for k, v in (raw.get("sections") or {}).items():
            if k in m["sections"]:
                m["sections"][k] = v or ""
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
