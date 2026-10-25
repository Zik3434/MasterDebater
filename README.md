# MasterDebater

Turns your **Flexcel** debate flow into your next speech, written by Claude Opus 5.5, using
**only** your own case, blocks and cards as evidence. Pick Aff or Neg, and it works out which
speech comes next from what is already on the flow.

Supports Lincoln-Douglas (traditional and plan flows), Policy, and Public Forum, matching the
four flow templates Flexcel ships.

## How it works

Everything happens inside one window with three tabs:

* **Flow.** A Flexcel-style flow you type into during the round. One sheet per case (1AC and
  1NC for LD, with the same columns Flexcel uses), plus Cross-ex and Notes tabs and a way to
  add sheets for off-case positions. It autosaves on every edit, so a refresh or crash loses
  nothing. You can also import a Flexcel `.json` if you flowed there instead.
* **Full screen.** A button on the Flow and Speech tabs hides the sidebar and the tab bar so
  that one view fills the window; press F11 in the browser to hide its tabs too.
* **Speech.** Pick Aff/Neg in the sidebar; the app looks at what is flowed and picks your next
  speech (1AC and 1NC flowed and you are Aff, so it writes the 1AR). Click **Write**. The speech
  streams onto the page, stays there editable, and can be downloaded as `.docx`. Earlier
  speeches from the round stay underneath, and the writer sees them so the 2AR is consistent
  with the 1AR. A **Coach notes** panel lists what was answered, extended, kicked or dropped,
  plus anything marked `[NO CARD - analytic]`.
* **Case & Blocks.** Paste your case, blocks and cards straight from Google Docs or Word, or
  upload files. Loaded once, saved on the computer, used for every round. These are the only
  sources the speech may quote. The writer is told to never invent a card, author, statistic
  or year.

Supports Lincoln-Douglas, Policy, and Public Forum.

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

## Command line (Flexcel files or screenshots)

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
