"""All Claude API calls: flow transcription (vision) and speech generation.

Model: claude-opus-5-5 with adaptive thinking, streamed so long speeches never hit an
HTTP timeout. Refusal fallbacks are on by default (`fallbacks="default"`), so a rare
safety-classifier decline is retried server-side on another model instead of failing.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

import anthropic

from .flow import Flow, flow_from_dict
from .formats import DebateFormat, Speech

MODEL = os.environ.get("MASTERDEBATER_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Spoken delivery rate used to turn a time limit into a word target. Competitive LD/Policy
# debaters routinely exceed 250 wpm; adjust in the UI or with MASTERDEBATER_WPM.
DEFAULT_WPM = int(os.environ.get("MASTERDEBATER_WPM", "200"))


def make_client() -> anthropic.Anthropic:
    # Resolves ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# 1. Vision: screenshot -> Flow
# ---------------------------------------------------------------------------

FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_type": {
            "type": "string",
            "enum": ["LD Traditional Flow", "LD Plan Flow", "Policy Flow", "PF Flow"],
        },
        "first_speaker": {"type": "string", "enum": ["pro", "con"]},
        "sheets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["name", "columns", "rows"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["flow_type", "first_speaker", "sheets"],
    "additionalProperties": False,
}

TRANSCRIBE_SYSTEM = """You transcribe screenshots of a debate flow from the Flexcel flowing app into structured data.

The flow is a grid. Each column is a speech (its header is at the top: AC/1AC, 1NR or 1NC, 1AR, 2NR, 2AR for Lincoln-Douglas; 1AC, 1NC, 2AC, 2NC/1NR, 1AR, 2NR, 2AR for Policy; "Pro Constructive", "Con Rebuttal" etc. for Public Forum). Each row is one argument thread: the cell in the first column is the original argument, and cells to its right are the responses to it in later speeches. Red text is affirmative/pro, blue text is negative/con.

Rules:
- Copy the cell text exactly as written, including debater shorthand and abbreviations. Do not expand, interpret, or correct it.
- Keep rows aligned: a response must stay in the same row as the argument it answers. Use "" for empty cells so every row has one entry per column.
- Only the visible sheet (tab) can be transcribed. Name it from the highlighted tab (e.g. "1AC" or "1NC").
- Column headers: use them exactly as shown.
- If a cell is unreadable, write "[unreadable]" rather than guessing.
- flow_type: infer from the column headers. first_speaker only matters for Public Forum; otherwise use "pro"."""


def transcribe_flow_image(
    image_path: str | Path, client: anthropic.Anthropic | None = None
) -> Flow:
    """Send a screenshot of Flexcel to Claude and get a structured Flow back."""
    client = client or make_client()
    image_path = Path(image_path)
    media_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": FLOW_SCHEMA},
        },
        system=TRANSCRIBE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    },
                    {"type": "text", "text": "Transcribe this flow."},
                ],
            }
        ],
    ) as stream:
        msg = stream.get_final_message()

    _raise_if_refused(msg)
    text = next(b.text for b in msg.content if b.type == "text")
    d = json.loads(text)
    flow = flow_from_dict(d)
    flow.source = "screenshot"
    return flow


# ---------------------------------------------------------------------------
# 2. Speech generation
# ---------------------------------------------------------------------------

SPEECH_SYSTEM = """You are an elite high-school debate coach ghost-writing a speech for your debater, who will read it word-for-word in round. You write in first person, in the debater's voice, ready to be read aloud.

HARD RULES (these are non-negotiable because the debater can be disqualified or lose for breaking them):
1. EVIDENCE: You may only cite, quote, or reference evidence that appears verbatim in the MATERIALS section (the debater's own case, blocks, and cards). Never invent a card, author, statistic, study, year, or source. Never attribute a claim to an author unless that author's card is in MATERIALS. If a response needs evidence you do not have, make the argument analytically in plain spoken words, and list that spot in the coach notes.
2. THE FLOW IS THE RECORD: Everything about what the opponent said comes from the FLOW. Do not assume arguments that are not flowed. Shorthand on the flow is the debater's own notes; interpret it the way a debater would, but do not add content.
3. Answer what was actually said. Every opponent argument that is still live and relevant to winning must be addressed, extended, or explicitly kicked. Point out drops ("they dropped X, extend it, it's conceded") when the flow shows no response.

CRAFT:
- Open with a short roadmap (order of sheets/arguments). Signpost constantly ("Off their first contention...", "Next, on the framework debate...").
- Group arguments when it saves time; prioritize what wins the round. Collapse in later rebuttals (2NR/2AR, Summary/Final Focus) - do not go for everything.
- Explicit impact calculus and framework/weighing where the format calls for it. Give the judge a clear reason to vote and "even-if" layers.
- Extensions must be real extensions: claim, warrant, impact, and why it matters, not just a tag.
- When you quote a card from MATERIALS, give the tag and cite (author/year) exactly as it appears there, and quote only what is in the file.
- Match the speech's job: constructives read the case; rebuttals answer and extend; the final speech crystallizes.
- Stay inside the word target; it is derived from the time limit. Tight sentences, no filler, no throat-clearing.

OUTPUT FORMAT - two parts, in this order:

PART 1, THE SPEECH. Every word in this part is read aloud to the judge, verbatim. So:
- Only spoken words. No brackets, no tags, no stage directions, no notes to self, no "[NO CARD]" markers, no commentary about strategy. If something is not meant to be said out loud, it does not belong here.
- Mark sections with a line that starts with '## ' and a short label (for example '## Framework', '## Their contention one', '## Voters'). These labels are shown as headings so the debater can find their place; the spoken signpost itself goes in the paragraph below.
- Plain paragraphs. No bullet lists, no bold, no numbered lists; say "One... Two... Three..." in words.

PART 2, COACH NOTES. Start it with a line that is exactly '## COACH NOTES'. It is not read aloud. Include: (a) a per-argument coverage checklist (answered / extended / kicked / dropped-by-them), (b) the places where no card was available and the argument was made analytically, (c) the two or three biggest risks in the round, (d) a one-line word count estimate."""


@dataclass
class SpeechResult:
    speech: Speech
    side: str
    text: str            # the speech body (Markdown)
    notes: str           # the coach notes section (Markdown), may be empty
    word_count: int
    word_target: int
    usage: dict
    model: str
    served_by_fallback: bool = False


def _word_target(speech: Speech, wpm: int) -> int:
    return int(speech.minutes * wpm)


def build_speech_prompt(
    flow: Flow,
    fmt: DebateFormat,
    speech: Speech,
    side: str,
    materials_text: str,
    wpm: int,
    extra_instructions: str = "",
    round_context: str = "",
) -> str:
    side_label = fmt.side_labels[side]
    opp_label = fmt.side_labels["neg" if side == "aff" else "aff"]
    order = " -> ".join(fmt.order())
    done = ", ".join(flow.speeches_with_content()) or "(nothing flowed yet)"
    target = _word_target(speech, wpm)

    parts = [
        f"FORMAT: {fmt.label}. Speech order: {order}.",
        f"I am the {side_label}. My opponent is the {opp_label}.",
        f"SPEECH TO WRITE: {speech.name} ({speech.minutes:g} minutes). "
        f"Word target: about {target} words at {wpm} wpm - do not exceed {int(target * 1.1)}.",
        f"Speeches already flowed: {done}.",
    ]
    if flow.source == "screenshot":
        parts.append(
            "NOTE: This flow was transcribed from a screenshot, so only the visible sheet is "
            "included and some cells may be marked [unreadable]."
        )
    if extra_instructions.strip():
        parts.append("EXTRA INSTRUCTIONS FROM THE DEBATER:\n" + extra_instructions.strip())

    parts.append("=== FLOW ===\n" + (flow.to_text() or "(empty flow)"))
    if round_context.strip():
        parts.append(
            "=== ROUND CONTEXT (my cross-ex questions/answers, notes, and the speeches I "
            "already gave this round; use for consistency, not as evidence) ===\n"
            + round_context.strip()
        )
    parts.append("=== MATERIALS (the only evidence you may use) ===\n" + materials_text)
    parts.append(f"Write my {speech.name} now.")
    return "\n\n".join(parts)


def _split_notes(text: str) -> tuple[str, str]:
    marker = "## COACH NOTES"
    idx = text.upper().find(marker)
    if idx == -1:
        return text.strip(), ""
    return text[:idx].strip(), text[idx + len(marker):].strip()


def _raise_if_refused(msg) -> None:
    if msg.stop_reason == "refusal":
        detail = ""
        if getattr(msg, "stop_details", None):
            detail = f" ({msg.stop_details.category}: {msg.stop_details.explanation})"
        raise RuntimeError("Claude declined this request" + detail)
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Output was cut off at max_tokens; try a shorter word target.")


def generate_speech(
    flow: Flow,
    side: str,
    speech_name: str,
    materials_text: str,
    wpm: int = DEFAULT_WPM,
    extra_instructions: str = "",
    client: anthropic.Anthropic | None = None,
    on_text=None,
    round_context: str = "",
) -> SpeechResult:
    """Write the requested speech. `on_text(chunk)` receives streamed text if given."""
    client = client or make_client()
    fmt = flow.format
    speech = fmt.speech(speech_name)
    if speech.side != side:
        raise ValueError(f"{speech_name} belongs to the {fmt.side_labels[speech.side]}, not the {fmt.side_labels[side]}")

    prompt = build_speech_prompt(
        flow, fmt, speech, side, materials_text, wpm, extra_instructions, round_context
    )

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{"type": "text", "text": SPEECH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        if on_text is not None:
            for chunk in stream.text_stream:
                on_text(chunk)
        msg = stream.get_final_message()

    _raise_if_refused(msg)
    full = "".join(b.text for b in msg.content if b.type == "text")
    body, notes = _split_notes(full)
    iterations = getattr(msg.usage, "iterations", None) or []
    served_by_fallback = any(getattr(it, "type", "") == "fallback_message" for it in iterations)

    return SpeechResult(
        speech=speech,
        side=side,
        text=body,
        notes=notes,
        word_count=len(body.split()),
        word_target=_word_target(speech, wpm),
        usage={
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
        },
        model=msg.model,
        served_by_fallback=served_by_fallback,
    )
