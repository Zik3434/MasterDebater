"""Command-line entry point.

Examples:
  # From a Flexcel save file, as the neg, auto-pick the next speech, write a .docx
  python -m masterdebater.cli --flow round1.json --side neg --materials my_nc.docx blocks.docx

  # From a screenshot instead of the .json
  python -m masterdebater.cli --image flow.png --side aff --speech 1AR --materials case.docx

  # Grab the screen right now (switch to Flexcel first), then generate
  python -m masterdebater.cli --capture --side aff --materials case.docx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import claude_client as cc
from .capture import capture_screen
from .docx_out import write_speech_docx
from .flow import load_flexcel, next_speech
from .materials import bundle_materials, read_material


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="masterdebater", description="Flexcel flow -> next speech")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--flow", help="Flexcel .json save file")
    src.add_argument("--image", help="Screenshot of the Flexcel flow")
    src.add_argument("--capture", action="store_true", help="Screenshot the primary monitor now")
    p.add_argument("--side", choices=["aff", "neg"], required=True, help="aff/pro or neg/con")
    p.add_argument("--speech", help="Speech to write (default: next one for your side)")
    p.add_argument("--materials", nargs="*", default=[], help="Your case/blocks (.docx .pdf .txt .md)")
    p.add_argument("--wpm", type=int, default=cc.DEFAULT_WPM, help="Speaking rate for the word target")
    p.add_argument("--notes", default="", help="Extra instructions for the writer")
    p.add_argument("--out", help="Output .docx path (default: speeches/<speech>.docx)")
    p.add_argument("--print-flow", action="store_true", help="Show the parsed flow and exit")
    p.add_argument("--monitor", type=int, default=1, help="Monitor index for --capture")
    args = p.parse_args(argv)

    if args.flow:
        flow = load_flexcel(args.flow)
    else:
        image = Path(args.image) if args.image else capture_screen("captures/flow.png", args.monitor)
        print(f"Transcribing {image} with {cc.MODEL} ...", file=sys.stderr)
        flow = cc.transcribe_flow_image(image)

    if args.print_flow:
        print(flow.to_text())
        return 0

    speech = args.speech or next_speech(flow, args.side)
    fmt = flow.format
    print(f"Format: {fmt.label} | Side: {fmt.side_labels[args.side]} | Writing: {speech}", file=sys.stderr)

    mats = [(Path(m).name, read_material(m)) for m in args.materials]
    materials_text = bundle_materials(mats)

    def echo(chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    result = cc.generate_speech(
        flow, args.side, speech, materials_text, wpm=args.wpm,
        extra_instructions=args.notes, on_text=echo,
    )
    print("\n", file=sys.stderr)

    out = Path(args.out) if args.out else Path("speeches") / f"{speech.replace('/', '-')}_{args.side}.docx"
    write_speech_docx(
        out, result.speech.name, fmt.side_labels[args.side], result.text, result.notes,
        word_count=result.word_count, minutes=result.speech.minutes,
    )
    print(
        f"Saved {out}  ({result.word_count} words, target {result.word_target}; "
        f"model {result.model}{' via fallback' if result.served_by_fallback else ''})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
