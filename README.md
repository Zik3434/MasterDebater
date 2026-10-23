# MasterDebater

Turns your **Flexcel** debate flow into your next speech, written by Claude Opus 5.5, using
**only** your own case, blocks and cards as evidence. Pick Aff or Neg, and it works out which
speech comes next from what is already on the flow.

Supports Lincoln-Douglas (traditional and plan flows), Policy, and Public Forum, matching the
four flow templates Flexcel ships.

## How it works

1. **Flow in.** Two ways:
   * **Flexcel save file (recommended).** Press `Ctrl+S` in Flexcel and save the flow as `.json`.
     That file holds every sheet and every cell exactly as you typed it, so nothing is lost.
   * **Screenshot.** Upload a screenshot of Flexcel, or let the app capture your screen. Claude
     reads the grid into the same structure. Only the sheet that is visible gets read, so the
     `.json` route is better once the round has more than one sheet.
2. **Materials in.** Upload your case / blocks / cards as Word, PDF, txt or Markdown. These are
   the only sources the speech may quote. The writer is told to never invent a card, author,
   statistic or year, and to tag anything it cannot support with `[NO CARD - analytic]`.
3. **Side + speech.** Choose Aff/Pro or Neg/Con. The app finds the last speech with anything
   flowed and picks your next one (for example 1AC and 1NC flowed and you are Neg, so it writes
   the 2NR). You can override the speech.
4. **Speech out.** The speech streams onto the page as it is written, then you download it as a
   `.docx`. A separate "Coach notes" section lists what was answered, extended, kicked or dropped,
   plus any analytic-only answers, so you can sanity-check coverage before you stand up.

## Setup (Windows, macOS or Linux)

```bash
git clone <this repo>
cd masterdebater
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

Give it your Anthropic API key (get one at https://platform.claude.com):

```bash
set ANTHROPIC_API_KEY=sk-ant-...        # Windows cmd
$env:ANTHROPIC_API_KEY="sk-ant-..."     # PowerShell
export ANTHROPIC_API_KEY=sk-ant-...     # macOS/Linux
```

## Run the app

```bash
streamlit run app.py
```

A browser tab opens. Keep it next to Flexcel.

## Command line

```bash
# Flexcel .json, you are Neg, next speech auto-picked, materials attached
python -m masterdebater.cli --flow round1.json --side neg --materials nc.docx blocks.docx

# Screenshot instead
python -m masterdebater.cli --image flow.png --side aff --speech 1AR --materials case.docx

# Capture the primary monitor right now, then generate
python -m masterdebater.cli --capture --side aff --materials case.docx

# Just show how the flow was parsed
python -m masterdebater.cli --flow round1.json --side aff --print-flow
```

Output lands in `speeches/<speech>_<side>.docx`.

## Try it without a round

`examples/sample_flow.json` is a Flexcel LD save with a 1AC and 1NC flowed, and
`examples/sample_case.md` is a matching case with 1AR blocks:

```bash
python -m masterdebater.cli --flow examples/sample_flow.json --side aff --materials examples/sample_case.md
```

## Settings

| Setting | Where | Default |
|---|---|---|
| Model | `MASTERDEBATER_MODEL` env var | `claude-opus-5-5` |
| Speaking rate (sets the word target from the time limit) | sidebar slider or `MASTERDEBATER_WPM` | 200 wpm |
| Time limits per speech | `masterdebater/formats.py` | NSDA standard |

Claude is called with adaptive thinking at high effort, streamed, and with server-side refusal
fallbacks on (`fallbacks="default"`), so a rare safety-classifier decline is retried on another
model instead of failing the speech. The app tells you when a fallback model served the request.

## Flexcel file format (for reference)

Flexcel stores a flow as:

```json
{"flow_type": "LD Traditional Flow",
 "flow-data": [[["AC cell", "1NC cell", "1AR cell", "2NR cell", "2AR cell"], ...], [...]],
 "boldElements": [...]}
```

Column headers are not saved, so `masterdebater/flow.py` rebuilds them from the flow type and
tab position the same way Flexcel's own templates do (`AC, 1NR, 1AR, 2NR, 2AR` on the AC sheet,
`1NC, 1AR, 2NR, 2AR` on the NC sheet, 7 columns for Policy, and the first row for PF). Flexcel
labels the neg's answers on the AC sheet "1NR" even in LD; this tool treats that column as the 1NC.

## Tests

```bash
pip install pytest
python -m pytest tests
```

## Limits worth knowing

* Nothing here reads your speech doc tabs inside Flexcel; evidence has to come from the files you
  upload. That is deliberate.
* The screenshot path depends on legible text. Zoom Flexcel in and keep the sheet you need on
  screen. Cells the model cannot read come through as `[unreadable]`.
* The speech is a draft from your flow and your file. Read the coach notes, then make it yours.
