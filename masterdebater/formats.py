"""Debate-format knowledge: speech order, which side gives each speech, time limits.

Everything downstream (next-speech detection, prompts, word targets) reads from here,
so adding a format or tweaking a time limit is a one-place change.
"""

from __future__ import annotations

from dataclasses import dataclass

AFF = "aff"
NEG = "neg"


@dataclass(frozen=True)
class Speech:
    name: str          # canonical name used in prompts and column matching
    side: str          # AFF or NEG
    minutes: float     # speech time limit
    kind: str          # "constructive" | "rebuttal" | "summary" | "final_focus" | "block"


@dataclass(frozen=True)
class DebateFormat:
    key: str
    label: str
    speeches: tuple[Speech, ...]
    side_labels: dict  # {"aff": "Affirmative", "neg": "Negative"}

    def speech(self, name: str) -> Speech:
        for s in self.speeches:
            if s.name == name:
                return s
        raise KeyError(f"{name!r} is not a speech in {self.label}")

    def order(self) -> list[str]:
        return [s.name for s in self.speeches]

    def speeches_for(self, side: str) -> list[Speech]:
        return [s for s in self.speeches if s.side == side]


LD = DebateFormat(
    key="ld",
    label="Lincoln-Douglas",
    speeches=(
        Speech("1AC", AFF, 6, "constructive"),
        Speech("1NC", NEG, 7, "constructive"),
        Speech("1AR", AFF, 4, "rebuttal"),
        Speech("2NR", NEG, 6, "rebuttal"),
        Speech("2AR", AFF, 3, "rebuttal"),
    ),
    side_labels={AFF: "Affirmative", NEG: "Negative"},
)

POLICY = DebateFormat(
    key="policy",
    label="Policy (CX)",
    speeches=(
        Speech("1AC", AFF, 8, "constructive"),
        Speech("1NC", NEG, 8, "constructive"),
        Speech("2AC", AFF, 8, "constructive"),
        Speech("2NC/1NR", NEG, 13, "block"),
        Speech("1AR", AFF, 5, "rebuttal"),
        Speech("2NR", NEG, 5, "rebuttal"),
        Speech("2AR", AFF, 5, "rebuttal"),
    ),
    side_labels={AFF: "Affirmative", NEG: "Negative"},
)


def public_forum(first_speaker: str = "pro") -> DebateFormat:
    """PF speech order depends on who speaks first (decided by coin flip)."""
    first, second = ("Pro", "Con") if first_speaker.lower() == "pro" else ("Con", "Pro")
    side = {"Pro": AFF, "Con": NEG}
    speeches = []
    for kind, minutes, label in (
        ("constructive", 4, "Constructive"),
        ("rebuttal", 4, "Rebuttal"),
        ("summary", 3, "Summary"),
        ("final_focus", 2, "Final Focus"),
    ):
        speeches.append(Speech(f"{first} {label}", side[first], minutes, kind))
        speeches.append(Speech(f"{second} {label}", side[second], minutes, kind))
    return DebateFormat(
        key="pf",
        label="Public Forum",
        speeches=tuple(speeches),
        side_labels={AFF: "Pro", NEG: "Con"},
    )


def format_for_flow_type(flow_type: str, first_speaker: str = "pro") -> DebateFormat:
    """Map Flexcel's `flow_type` string to a DebateFormat."""
    ft = (flow_type or "").lower()
    if "policy" in ft:
        return POLICY
    if "pf" in ft or "public forum" in ft:
        return public_forum(first_speaker)
    return LD


# Column-header aliases as they appear in Flexcel grids. Flexcel labels the neg's
# responses on the AC sheet "1NR" even in LD, where that speech is really the 1NC.
_LD_ALIASES = {
    "AC": "1AC",
    "1AC": "1AC",
    "NC": "1NC",
    "1NC": "1NC",
    "1NR": "1NC",
    "1AR": "1AR",
    "2NR": "2NR",
    "2AR": "2AR",
}

_POLICY_ALIASES = {
    "1AC": "1AC",
    "AC": "1AC",
    "1NC": "1NC",
    "NC": "1NC",
    "2AC": "2AC",
    "2NC/1NR": "2NC/1NR",
    "2NC": "2NC/1NR",
    "1NR": "2NC/1NR",
    "BLOCK": "2NC/1NR",
    "1AR": "1AR",
    "2NR": "2NR",
    "2AR": "2AR",
}


def canonical_speech(fmt: DebateFormat, header: str) -> str | None:
    """Return the canonical speech name for a column header, or None if unknown."""
    h = (header or "").strip()
    if not h:
        return None
    if fmt.key == "ld":
        return _LD_ALIASES.get(h.upper())
    if fmt.key == "policy":
        return _POLICY_ALIASES.get(h.upper())
    # Public Forum: headers are full names like "Pro Rebuttal"; match case-insensitively.
    for s in fmt.speeches:
        if s.name.lower() == h.lower():
            return s.name
    # Flexcel has one typo'd header ("Neg Final Focus") - treat Neg/Aff as Con/Pro.
    alt = h.lower().replace("neg ", "con ").replace("aff ", "pro ")
    for s in fmt.speeches:
        if s.name.lower() == alt:
            return s.name
    return None
