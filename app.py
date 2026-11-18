"""MasterDebater: flow the round here, keep your case here, get the speech here.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from masterdebater import claude_client as cc
from masterdebater import cx_job, store
from masterdebater.docx_out import write_speech_docx
from masterdebater.flow import next_speech, parse_flexcel
from masterdebater.materials import bundle_materials, read_material_bytes
from masterdebater.updater import update_from_github

st.set_page_config(page_title="MasterDebater", page_icon="🎤", layout="wide")

# --------------------------------------------------------------------------- state
ss = st.session_state
ss.setdefault("round", store.load_round())
ss.setdefault("materials", store.load_materials())
ss.setdefault("grid_version", 0)
ss.setdefault("wpm", cc.DEFAULT_WPM)
ss.setdefault("focus", None)   # None | "flow" | "speech" | "read"  -> full-screen mode
ss.setdefault("read_speech", None)
if "read" in st.query_params:          # a click on the speech text lands here
    ss["focus"] = "read"
    ss["read_speech"] = st.query_params["read"]

# Streamlit's grid turns off multi-cell selection (drag to select, the fill handle, column
# resizing) when the browser says its main pointer is a touchscreen, which touchscreen laptops
# do even when a mouse or trackpad is in use. Tell the page the pointer is precise so the grid
# keeps those features.
_FINE_POINTER_JS = """
(function () {
  var w = window;
  try { if (window.parent && window.parent !== window && window.parent.document) { w = window.parent; } } catch (e) {}
  if (w.__mdFinePointer || !w.matchMedia) { return; }
  w.__mdFinePointer = true;
  var orig = w.matchMedia.bind(w);
  w.matchMedia = function (q) {
    if (typeof q === "string" && q.replace(/\\s/g, "") === "(pointer:coarse)") {
      return {matches: false, media: q, onchange: null,
              addListener: function () {}, removeListener: function () {},
              addEventListener: function () {}, removeEventListener: function () {},
              dispatchEvent: function () { return false; }};
    }
    return orig(q);
  };
})();
"""


def enable_multiselect_on_touch() -> None:
    import inspect

    if "unsafe_allow_javascript" in inspect.signature(st.html).parameters:
        st.html(f"<script>{_FINE_POINTER_JS}</script>", unsafe_allow_javascript=True)
    else:   # older Streamlit: run it from a zero-height frame that reaches up to the page
        import streamlit.components.v1 as components

        components.html(f"<script>{_FINE_POINTER_JS}</script>", height=0)


if not ss.get("pointer_ready"):
    # The grid reads the pointer type once, when it first appears. On a fresh page load, apply
    # the setting first, give the browser a moment, then draw the app.
    ss["pointer_ready"] = True
    enable_multiselect_on_touch()
    time.sleep(0.5)
    st.rerun()

rnd: dict = ss["round"]
mats: dict = ss["materials"]
fmt = store.format_by_key(rnd.get("format", "ld"), rnd.get("first_speaker", "pro"))
side: str = rnd.get("side", "aff")
focus: str | None = ss["focus"]


def bump_grids() -> None:
    """Force every data editor to reload from `rnd` (after import / reset / add sheet)."""
    ss["grid_version"] += 1


def enter_focus(view: str) -> None:
    ss["focus"] = view
    bump_grids()   # the grid is rebuilt at a new size; re-snapshot it from the saved rows


def exit_focus() -> None:
    ss["focus"] = None
    st.query_params.clear()
    bump_grids()


def enter_read(speech_name: str) -> None:
    ss["focus"] = "read"
    ss["read_speech"] = speech_name
    st.query_params["read"] = speech_name


def reset_flow() -> None:
    """Blank every sheet (keeping the sheet layout), then let the pre-flow go back in."""
    for sh in rnd["sheets"]:
        sh["rows"] = [[""] * len(sh["columns"]) for _ in range(store.DEFAULT_ROWS)]
    rnd.pop("auto_preflow", None)
    rnd["cx"]["suggested"], rnd["cx"]["suggested_for"] = [], ""
    rnd["cx"]["pending"], rnd["cx"]["pending_for"] = [], ""
    store.save_round(rnd)
    bump_grids()


def toggle_labels() -> None:
    rnd["show_labels"] = not rnd.get("show_labels", True)
    store.save_round(rnd)


READING_CSS = """
<style>
.md-speech p { font-size: 1.2rem; line-height: 1.65; margin: 0 0 0.9rem 0; }
.md-speech.md-big p { font-size: 1.45rem; line-height: 1.7; margin-bottom: 1.1rem; }
.md-speech a.md-link { color: inherit !important; text-decoration: none !important; display: block; cursor: pointer; }
.cx-q { border: 1px solid rgba(128,128,128,0.35); border-radius: 14px; margin: 0.45rem 0;
        background: rgba(128,128,128,0.07); }
.cx-q > summary { list-style: none; cursor: pointer; padding: 0.6rem 0.9rem; display: flex; gap: 0.7rem;
                  align-items: baseline; font-weight: 600; }
.cx-q > summary::-webkit-details-marker { display: none; }
.cx-q > summary:hover { background: rgba(128,128,128,0.10); border-radius: 14px; }
.cx-q[open] { border-color: rgba(255,75,75,0.55); background: rgba(255,75,75,0.06); }
.cx-n { min-width: 1.5rem; height: 1.5rem; border-radius: 999px; background: rgba(128,128,128,0.25);
        display: inline-flex; align-items: center; justify-content: center; font-size: 0.8rem; }
.cx-q[open] .cx-n { background: #ff4b4b; color: #fff; }
.cx-body { padding: 0 0.9rem 0.7rem 3.1rem; }
.cx-purpose { opacity: 0.65; margin-bottom: 0.35rem; }
.cx-body ul { margin: 0; padding-left: 1.1rem; }
.cx-body li { margin: 0.2rem 0; }
.cx-if { opacity: 0.75; }
.md-speech h2 { font-size: 0.85rem; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.6;
                margin: 1.3rem 0 0.4rem 0; padding-bottom: 0.2rem; border-bottom: 1px solid rgba(128,128,128,0.35); }
</style>
"""


def reading_html(text: str, labels: bool = True, big: bool = False, link: str | None = None) -> str:
    """Render the speech for reading: section labels small and grey, spoken text large.

    labels=False drops the section headings entirely. link wraps the whole thing in an
    anchor so clicking the text opens read mode (?read=<speech>)."""
    import html as _html
    import re as _re

    cls = "md-speech md-big" if big else "md-speech"
    out = [f'<div class="{cls}">']
    if link:
        out.append(f'<a class="md-link" href="{_html.escape(link)}" target="_self" title="Click to read full screen">')
    for block in _re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        m = _re.match(r"^#{1,3}\s+(.*)$", block)
        if m:
            if labels:
                out.append(f"<h2>{_html.escape(m.group(1).strip())}</h2>")
        else:
            # Keep single line breaks inside a paragraph; strip stray markdown bullets/bold.
            block = _re.sub(r"^\s*[-*]\s+", "", block, flags=_re.M).replace("**", "")
            out.append("<p>" + _html.escape(block).replace("\n", "<br>") + "</p>")
    if link:
        out.append("</a>")
    out.append("</div>")
    return "".join(out)


FOCUS_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], header[data-testid="stHeader"] { display: none !important; }
.block-container { padding-top: 0.6rem !important; padding-bottom: 0 !important; max-width: 100% !important; }
</style>
"""

st.markdown(READING_CSS, unsafe_allow_html=True)

# --------------------------------------------------------------------------- sidebar
if focus is None:
    with st.sidebar:
        side = st.radio(
            "I am the", ["aff", "neg"], index=0 if side == "aff" else 1,
            format_func=lambda s: fmt.side_labels[s], horizontal=True,
        )
        rnd["side"] = side

        fmt_choice = st.selectbox(
            "Format", ["ld", "policy", "pf"], index=["ld", "policy", "pf"].index(rnd.get("format", "ld")),
            format_func=lambda k: {"ld": "Lincoln-Douglas", "policy": "Policy", "pf": "Public Forum"}[k],
        )
        first_speaker = rnd.get("first_speaker", "pro")
        if fmt_choice == "pf":
            first_speaker = st.radio("Who speaks first?", ["pro", "con"],
                                     index=0 if first_speaker == "pro" else 1, horizontal=True)
        if fmt_choice != rnd.get("format") or first_speaker != rnd.get("first_speaker"):
            if st.button("Switch format (starts a new round)", width="stretch"):
                ss["round"] = store.new_round(fmt_choice, first_speaker)
                ss["round"]["side"] = side
                store.save_round(ss["round"])
                bump_grids()
                st.rerun()

        ss["wpm"] = st.slider("Speaking rate (wpm)", 120, 350, ss["wpm"], 10)

        st.divider()
        rnd["name"] = st.text_input("Round name", rnd.get("name", ""))
        rnd["judge"] = st.text_area(
            "Judge paradigm", rnd.get("judge", ""), height=150,
            placeholder="Paste the judge's paradigm from Tabroom. Speeches and cross-ex questions are "
                        "written for this judge. Cleared when you start a new round.",
        )
        if st.button("New round (clears the flow)", width="stretch"):
            ss["round"] = store.new_round(rnd.get("format", "ld"), rnd.get("first_speaker", "pro"))
            ss["round"]["side"] = side
            store.save_round(ss["round"])
            bump_grids()
            st.rerun()

        n_words = store.material_words(mats, side)
        if n_words:
            st.success(f"{fmt.side_labels[side]} material loaded: {n_words:,} words")
        else:
            st.warning(f"No {fmt.side_labels[side]} case or blocks loaded. Open the Case & Blocks tab.")

        st.divider()
        if st.button("Update MasterDebater", width="stretch",
                     help="Download the newest version from GitHub. Your rounds and case are kept."):
            try:
                changed = update_from_github()
            except Exception as e:  # noqa: BLE001
                st.error(f"Update failed: {e}")
            else:
                if changed:
                    st.success(f"Updated {len(changed)} file(s). Press Ctrl+C in the black window, "
                               "then run `streamlit run app.py` again.")
                else:
                    st.info("Already up to date.")

wpm: int = ss["wpm"]
ss.setdefault("preflow_error", {})   # side -> (case_hash, message) so a failure isn't retried every rerun


def draft_preflow_for(sd: str) -> None:
    """Draft the pre-flow for one side from its case and save it."""
    case = store.case_text_for_side(mats, sd)
    h = store.case_hash(case)
    try:
        with st.spinner(f"Drafting your {fmt.side_labels[sd]} pre-flow from your case (one time per topic)..."):
            lines = cc.draft_preflow(case, fmt.side_labels[sd], mats.get("resolution", ""))
    except Exception as e:  # noqa: BLE001
        ss["preflow_error"][sd] = (h, str(e))
        return
    ss["preflow_error"].pop(sd, None)
    mats["preflow"][sd] = {"text": "\n".join(lines), "case_hash": h}
    store.save_materials(mats)


def absorb_grid_edits() -> None:
    """Copy edits still held by the grid widgets into the round.

    The grids are normally read further down the script. Anything that rebuilds them before that
    point (a pre-flow dropping in) would otherwise throw away the cell that was just typed."""
    v = ss["grid_version"]
    for i, sheet in enumerate(rnd["sheets"]):
        state = ss.get(f"grid_{v}_{i}")
        if not isinstance(state, dict) or state.get("deleted_rows"):
            continue
        cols = sheet["columns"]
        for r, changes in (state.get("edited_rows") or {}).items():
            r = int(r)
            if 0 <= r < len(sheet["rows"]):
                for col, val in changes.items():
                    if col in cols:
                        new = "" if val is None else str(val)
                        if sheet["rows"][r][cols.index(col)] != new:
                            sheet["rows"][r][cols.index(col)] = new
                            ss["flow_edit_at"] = time.time()   # a cell was just typed


def ensure_preflow(sd: str) -> None:
    absorb_grid_edits()
    case = store.case_text_for_side(mats, sd)
    h = store.case_hash(case)
    failed = ss["preflow_error"].get(sd)
    pf = mats["preflow"][sd]
    # Automatic drafting happens once per version of the case. If it was already drafted for this
    # exact case (even if the box was emptied since), only the Draft button drafts again.
    if h and not pf["text"].strip() and pf.get("case_hash") != h and not (failed and failed[0] == h):
        draft_preflow_for(sd)
    if store.apply_preflow(rnd, mats, sd):
        store.save_round(rnd)
        bump_grids()


ensure_preflow(side)


# --------------------------------------------------------------------------- views
def focus_bar(view: str) -> None:
    """Top strip shown only in full-screen mode."""
    left, right = st.columns([6, 1])
    with left:
        other = "speech" if view == "flow" else "flow"
        label = {"flow": "Flow", "speech": "Speech"}
        st.caption(
            f"**{label[view]}** · {fmt.side_labels[side]} · {rnd.get('name', '')} · "
            f"press F11 in the browser to hide its tabs too"
        )
    with right:
        c1, c2 = st.columns(2)
        c1.button(f"→ {label[other]}", width="stretch", on_click=enter_focus, args=(other,),
                  help=f"Switch the full-screen view to {label[other]}")
        c2.button("✕ Exit", width="stretch", on_click=exit_focus, help="Back to the normal layout")


def header_with_time(col: str) -> str:
    """'1AR' -> '1AR · 4:00', using the format's time limit. Unknown columns are left as is."""
    try:
        minutes = fmt.speech(col).minutes
    except KeyError:
        return col
    secs = round(minutes * 60)
    return f"{col} · {secs // 60}:{secs % 60:02d}"


# Cleared cells should look empty, not show a grey "None". Older Streamlit versions don't have
# the placeholder option, so only pass it where it exists.
import inspect as _inspect

GRID_EXTRAS = {"placeholder": ""} if "placeholder" in _inspect.signature(st.data_editor).parameters else {}


def render_flow(full: bool) -> None:
    sheet_names = [s["name"] for s in rnd["sheets"]]
    sub = st.tabs(sheet_names + ["Sheets & import"])
    v = ss["grid_version"]
    grid_height = 760 if full else 560

    for i, (tab, sheet) in enumerate(zip(sub, rnd["sheets"])):
        with tab:
            cols = sheet["columns"]
            # The editor keeps its own diff on top of the frame we pass in, so pass the SAME
            # base frame every rerun (snapshotted per grid version) and treat its output as truth.
            base_key = f"base_{v}_{i}"
            if base_key not in ss:
                ss[base_key] = pd.DataFrame(sheet["rows"], columns=cols).fillna("").astype(str)
            edited = st.data_editor(
                ss[base_key],
                key=f"grid_{v}_{i}",
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                height=grid_height,
                **GRID_EXTRAS,
                # A small base width per column: Streamlit spreads the leftover space evenly,
                # so every speech column stays on screen at once, the way Flexcel lays it out.
                column_config={c: st.column_config.TextColumn(header_with_time(c), width=120) for c in cols},
            )
            rows = edited.fillna("").astype(str).values.tolist()
            if rows != sheet["rows"]:
                sheet["rows"] = rows
                ss["flow_edit_at"] = time.time()
            # Checked on every run, not only when this block saw the change: edits are usually
            # copied in earlier by absorb_grid_edits().
            if store.ensure_room(sheet):          # getting close to the bottom: add rows
                store.save_round(rnd)
                bump_grids()
                st.rerun()
            if not full:
                st.caption("Click a cell and type. Enter moves down, Tab moves right. Drag across cells (or "
                           "Shift+click) to select several, then press the Delete key to clear them all. Rows are "
                           "argument threads: the first column is the argument, the columns to its right answer it.")

    with sub[len(sheet_names)]:
        st.subheader("Add a sheet")
        st.caption("For an off-case position, a second advantage, a theory shell, etc.")
        new_name = st.text_input("Sheet name", placeholder="e.g. Kant NC, Theory, Plan")
        base = st.selectbox("Columns like", [s["name"] for s in rnd["sheets"]])
        if st.button("Add sheet") and new_name.strip():
            template = next(s for s in rnd["sheets"] if s["name"] == base)
            rnd["sheets"].append({"name": new_name.strip(), "columns": list(template["columns"]),
                                  "rows": [[""] * len(template["columns"]) for _ in range(store.DEFAULT_ROWS)]})
            store.save_round(rnd)
            bump_grids()
            st.rerun()
        if len(rnd["sheets"]) > 1:
            victim = st.selectbox("Remove a sheet", ["(none)"] + [s["name"] for s in rnd["sheets"]])
            if victim != "(none)" and st.button(f"Remove '{victim}'"):
                rnd["sheets"] = [s for s in rnd["sheets"] if s["name"] != victim]
                store.save_round(rnd)
                bump_grids()
                st.rerun()

        st.divider()
        st.subheader("Try it with a demo round")
        st.caption("Loads a sample LD round from the Aff's seat, flowed through the 1NC, plus a sample case "
                   "so you can click Write and see a 1AR. Replaces the current flow.")
        if st.button("Load demo round"):
            demo = store.demo_round()
            ss["round"] = demo
            store.save_round(demo)
            if not store.materials_for_side(mats, "aff"):
                mats["sections"]["aff_case"] = store.demo_case_text()
                store.save_materials(mats)
            bump_grids()
            st.rerun()

        st.divider()
        st.subheader("Import a Flexcel save")
        fx = st.file_uploader("Flexcel .json", type=["json"], key="flexcel_import")
        if fx is not None and st.button("Replace my flow with this file"):
            try:
                flow = parse_flexcel(json.load(fx))
                rnd["sheets"] = store.sheets_from_flow(flow)
                rnd["format"] = flow.format.key
                rnd["first_speaker"] = flow.first_speaker
                store.save_round(rnd)
                bump_grids()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read that file: {e}")


CX_PANEL_HEIGHT = 430   # questions box and answers box share this height, side by side
CX_IDLE_SECONDS = 15   # auto-draft once the flow of their speech has been still this long


def start_cx_draft(manual: bool = False) -> str:
    """Kick off a background draft for the opponent's latest constructive. Returns a status word."""
    r = ss["round"]
    target = store.cx_target(r, side)
    if not target:
        return "nothing"
    sig = store.cx_signature(r, target)
    flow = store.round_to_flow(r)
    mtext = bundle_materials(store.materials_for_side(mats, side))
    res = mats.get("resolution", "")
    judge = r.get("judge", "")
    ok = cx_job.start(r["id"], sig, target,
                      lambda: cc.draft_cx_questions(flow, side, target, mtext, res, judge=judge), manual=manual)
    return "started" if ok else "busy"


def show_cx_questions(r: dict, qs: list[dict], target: str) -> None:
    r["cx"]["suggested"], r["cx"]["suggested_for"] = qs, target
    r["cx"]["pending"], r["cx"]["pending_for"] = [], ""
    store.save_round(r)


def collect_cx() -> None:
    """Copy a finished background draft into the round (main thread only).

    A new draft must not replace the list while the debater may be reading it: the open question
    would close and the list would jump. So a finished draft is shown at once only when nothing is
    shown yet, it is for a different speech, it was asked for with the button, or the flow was
    edited after the draft began (the debater is flowing, not reading). Otherwise it waits behind
    a "Show updated" button, and is shown automatically the next time the flow is edited."""
    r = ss["round"]
    cx = r["cx"]
    snap = cx_job.snapshot()
    if not (snap["running"] or snap["collected"]):
        if snap["round_id"] == r["id"]:
            qs = snap["questions"]
            if qs:
                flowing = ss.get("flow_edit_at", 0) > snap["started_at"]
                if (not cx["suggested"] or cx.get("suggested_for") != snap["target"]
                        or snap["manual"] or flowing):
                    show_cx_questions(r, qs, snap["target"])
                elif qs != cx["suggested"]:
                    cx["pending"], cx["pending_for"] = qs, snap["target"]
                    ss["cx_pending_at"] = time.time()
                    store.save_round(r)
            ss["cx_error"] = snap["error"]
        cx_job.mark_collected()
    if cx.get("pending") and ss.get("flow_edit_at", 0) > ss.get("cx_pending_at", 0):
        show_cx_questions(r, cx["pending"], cx.get("pending_for", ""))


def cx_error_text(err: str) -> str:
    if "authentication" in err.lower() or "api_key" in err.lower():
        return "Couldn't draft questions: no API key is set."
    return "Couldn't draft questions: " + err


def cx_questions_md(qs: list[dict]) -> str:
    """Each question as a collapsed bubble; click it to see the purpose and the follow-ups."""
    import html as _html

    out = ['<div class="cx-bubbles">']
    for i, q in enumerate(qs, 1):
        body = f'<div class="cx-purpose">{_html.escape(q.get("purpose", ""))}</div>'
        fus = q.get("follow_ups") or []
        if fus:
            body += "<ul>" + "".join(
                f'<li><span class="cx-if">If they say <i>{_html.escape(f.get("if_they_say", ""))}</i>:</span> '
                f'{_html.escape(f.get("ask", ""))}</li>' for f in fus) + "</ul>"
        out.append(f'<details class="cx-q"><summary><span class="cx-n">{i}</span>'
                   f'<span class="cx-text">{_html.escape(q["question"])}</span></summary>'
                   f'<div class="cx-body">{body}</div></details>')
    out.append("</div>")
    return "".join(out)


def cx_engine() -> None:
    """Runs every few seconds: collects finished drafts and, with auto on, starts new ones."""
    collect_cx()
    r = ss["round"]
    snap = cx_job.snapshot()
    running = snap["running"] and snap["round_id"] == r["id"]
    status = ""
    if mats.get("cx_auto"):
        target = store.cx_target(r, side)
        if not target:
            status = "Cross-ex auto-draft is on. Waiting for their constructive on the flow."
        else:
            sig = store.cx_signature(r, target)
            if sig != ss.get("cx_seen_sig"):
                ss["cx_seen_sig"], ss["cx_seen_at"] = sig, time.time()
            done_for_sig = snap["sig"] == sig and snap["round_id"] == r["id"]
            idle = time.time() - ss.get("cx_seen_at", 0)
            if not snap["running"] and not done_for_sig and idle >= CX_IDLE_SECONDS:
                start_cx_draft()
                running = True
            if running:
                status = f"Drafting cross-ex questions for their {target}..."
            elif r["cx"].get("pending"):
                status = f"Updated cross-ex questions are waiting on the Cross-ex tab for their {target}."
            elif r["cx"]["suggested"] and done_for_sig:
                status = f"{len(r['cx']['suggested'])} cross-ex questions ready for their {target}."
            else:
                status = "Cross-ex questions will update when you pause typing."
    elif running:
        status = "Drafting cross-ex questions..."
    if focus == "read":
        return
    if status:
        st.caption(status)
    if focus == "flow" and r["cx"]["suggested"]:
        with st.expander(f"Cross-ex questions ({len(r['cx']['suggested'])})"):
            st.markdown(cx_questions_md(r["cx"]["suggested"]), unsafe_allow_html=True)


def cx_suggestions() -> None:
    """The suggested-questions box on the Cross-ex tab; refreshes itself while drafting."""
    collect_cx()
    r = ss["round"]
    snap = cx_job.snapshot()
    if ss.get("cx_error"):
        st.error(cx_error_text(ss["cx_error"]))
    qs = r["cx"]["suggested"]
    pending = r["cx"].get("pending") or []
    # Header row is always there, so nothing below it moves when a button appears.
    h1, h2, h3 = st.columns([1.7, 1.3, 1])
    h1.markdown(f"**Questions for their {r['cx'].get('suggested_for', '')}**" if qs else "**Suggested questions**")
    if pending and h2.button(f"Show {len(pending)} updated", key="cx_show_pending", type="primary", width="stretch",
                             help="A newer set was drafted from your latest flow. It waits here so the "
                                  "question you are reading is not replaced."):
        show_cx_questions(r, pending, r["cx"].get("pending_for", ""))
        st.rerun(scope="fragment")
    if qs and h3.button("Add to mine", key="cx_add", width="stretch",
                        help="Copy these questions and follow-ups into 'Questions I want to ask'"):
        parts = []
        for q in qs:
            parts.append(q["question"])
            parts += [f"    If they say {f.get('if_they_say', '')}: {f.get('ask', '')}" for f in q.get("follow_ups") or []]
        lines = "\n".join(parts)
        r["cx"]["ask"] = (r["cx"].get("ask", "").rstrip() + "\n" + lines).strip()
        store.save_round(r)
        st.rerun(scope="app")
    with st.container(border=True, height=CX_PANEL_HEIGHT - 50):
        if qs:
            st.markdown(cx_questions_md(qs), unsafe_allow_html=True)
        elif not (snap["running"] and snap["round_id"] == r["id"]):
            st.caption("None drafted yet. Flow their constructive, then click Draft questions now, "
                       "or turn on auto-draft.")


def render_cx() -> None:
    """Cross-ex answers and round notes. Everything here goes to the speech writer."""
    t1, t2 = st.columns([3, 1])
    with t1:
        auto = st.toggle(
            "Auto-draft questions while I flow", value=mats.get("cx_auto", False),
            help=f"Drafts after you stop typing for about {CX_IDLE_SECONDS} seconds, only while their "
                 "constructive is on the flow. Each draft costs about 1 to 2 cents.")
        if auto != mats.get("cx_auto", False):
            mats["cx_auto"] = auto
            store.save_materials(mats)
            st.rerun()
    with t2:
        if st.button("Draft questions now", width="stretch", type="primary"):
            ss["cx_error"] = None
            if start_cx_draft(manual=True) == "nothing":
                st.warning("Nothing to question yet. Flow their constructive first.")
            else:
                st.rerun()
    snap = cx_job.snapshot()
    polling = snap["running"] and snap["round_id"] == ss["round"]["id"]
    left, right = st.columns([1.25, 1])
    with left:      # questions to ask
        st.fragment(cx_suggestions, run_every=(3 if polling or mats.get("cx_auto") else None))()
    with right:     # what they answer, typed while looking at the question
        rnd["cx"]["answers"] = st.text_area(
            "What they said in cross-ex", rnd["cx"].get("answers", ""), height=CX_PANEL_HEIGHT - 30,
            placeholder="One per line, in your own words.\n"
                        "e.g. Conceded their framework has no answer to structural violence.\n"
                        "e.g. Said their Diamond card is about rent control, not a housing guarantee.\n"
                        "e.g. Could not name a single enforcement mechanism for the plan.",
        )

    b1, b2 = st.columns([1.25, 1])
    with b1:
        rnd["cx"]["ask"] = st.text_area(
            "Questions I want to ask", rnd["cx"].get("ask", ""), height=200,
            placeholder="Your prep for cross-ex. The writer sees these too.",
        )
    with b2:
        rnd["notes"] = st.text_area(
            "Round notes", rnd.get("notes", ""), height=200,
            placeholder="Judge paradigm, what to collapse to, anything else the writer should know.",
        )
    st.caption("Everything on this tab is handed to the speech writer along with the flow.")


def render_speech(full: bool) -> None:
    flow = store.round_to_flow(rnd)
    mine = [s.name for s in fmt.speeches_for(side)]
    suggested = next_speech(flow, side)

    top1, top2, top3 = st.columns([1, 2, 1])
    with top1:
        speech = st.selectbox("Speech", mine, index=mine.index(suggested) if suggested in mine else 0)
        sp = fmt.speech(speech)
        st.caption(f"{sp.minutes:g} min ≈ {int(sp.minutes * wpm)} words")
    with top2:
        extra = st.text_input("Instructions (optional)", placeholder="e.g. collapse to framework + C1, kick theory")
    with top3:
        st.write("")
        st.write("")
        go = st.button(f"Write my {speech}", type="primary", width="stretch")

    if flow.is_empty() and speech not in (fmt.order()[0], fmt.order()[1]):
        st.warning("The flow is empty. Fill in the Flow tab first, or the speech will have nothing to answer.")

    materials_pairs = store.materials_for_side(mats, side)
    if not materials_pairs:
        st.warning(f"No {fmt.side_labels[side]} case or blocks are loaded, so the speech will be all analytics.")

    if go:
        store.save_round(rnd)
        box = st.empty()
        buf: list[str] = []

        def on_text(chunk: str) -> None:
            buf.append(chunk)
            box.markdown("".join(buf))

        try:
            with st.spinner("Writing..."):
                res = cc.generate_speech(
                    flow, side, speech, bundle_materials(materials_pairs), wpm=wpm,
                    extra_instructions=extra, on_text=on_text, round_context=store.round_context(rnd),
                    resolution=mats.get("resolution", ""), judge=rnd.get("judge", ""),
                )
            box.empty()
            entry = {
                "speech": res.speech.name, "side": side, "text": res.text, "notes": res.notes,
                "words": res.word_count, "target": res.word_target,
                "at": datetime.now().isoformat(timespec="minutes"),
                "model": res.model, "fallback": res.served_by_fallback,
            }
            # Replace an earlier draft of the same speech; keep the newest at the top.
            rnd["speeches"] = [s for s in rnd["speeches"] if s["speech"] != res.speech.name]
            rnd["speeches"].insert(0, entry)
            store.save_round(rnd)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "authentication" in msg.lower() or "api_key" in msg.lower():
                st.error("No API key found. Close the app (Ctrl+C in the black window), then run "
                         "`set ANTHROPIC_API_KEY=your-key` followed by `streamlit run app.py`.")
            else:
                st.error(f"Generation failed: {msg}")

    # The speech doc: newest speech open, earlier ones collapsed below it.
    if not rnd["speeches"]:
        st.info("Your speech will appear here. Flow the round, then click Write.")
    for n, spch in enumerate(rnd["speeches"]):
        title = f"{spch['speech']} — {spch['words']} words (target {spch['target']}) — {spch['at']}"
        with st.expander(title, expanded=(n == 0)):
            if spch.get("fallback"):
                st.caption(f"Served by fallback model {spch.get('model')}")
            b1, b2, b3 = st.columns([1.2, 1.2, 4])
            b1.button("⛶ Read full screen", key=f"read_{spch['speech']}_{spch['at']}",
                      on_click=enter_read, args=(spch["speech"],), width="stretch")
            b2.button("Labels: " + ("on" if rnd.get("show_labels", True) else "off"),
                      key=f"lbl_{spch['speech']}_{spch['at']}", on_click=toggle_labels, width="stretch",
                      help="Show or hide the ROADMAP / FRAMEWORK section labels")
            editing = b3.toggle("Edit", key=f"edit_{spch['speech']}_{spch['at']}",
                                help="Switch between the reading view and an editable text box")
            if editing:
                edited = st.text_area("Speech text", spch["text"], height=(720 if full else 520),
                                      key=f"speech_{spch['speech']}_{spch['at']}")
                if edited != spch["text"]:
                    spch["text"] = edited
                    spch["words"] = len(edited.split())
                    store.save_round(rnd)
            else:
                with st.container(border=True):
                    st.markdown(
                        reading_html(spch["text"], labels=rnd.get("show_labels", True),
                                     link=f"?read={spch['speech']}"),
                        unsafe_allow_html=True,
                    )
            if spch.get("notes"):
                with st.expander("Coach notes: coverage, missing cards, risks"):
                    st.markdown(spch["notes"])
            out = store.SPEECH_DIR / rnd["name"].replace("/", "-") / f"{spch['speech'].replace('/', '-')}_{spch['side']}.docx"
            write_speech_docx(out, spch["speech"], fmt.side_labels[spch["side"]], spch["text"], spch.get("notes"),
                              word_count=spch["words"], minutes=fmt.speech(spch["speech"]).minutes)
            st.download_button("Download as Word", data=out.read_bytes(), file_name=out.name,
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               key=f"dl_{spch['speech']}_{spch['at']}")


def render_read() -> None:
    """Full-screen read mode: nothing but the speech, big type."""
    want = ss.get("read_speech")
    spch = next((x for x in rnd["speeches"] if x["speech"] == want), None) or (rnd["speeches"][0] if rnd["speeches"] else None)
    left, mid, right = st.columns([5, 1.3, 1])
    with left:
        if spch:
            st.caption(f"**{spch['speech']}** · {fmt.side_labels[spch['side']]} · {spch['words']} words")
    with mid:
        st.button("Labels: " + ("on" if rnd.get("show_labels", True) else "off"), key="read_lbl",
                  on_click=toggle_labels, width="stretch")
    with right:
        st.button("✕ Exit", key="read_exit", on_click=exit_focus, width="stretch")
    if spch is None:
        st.info("No speech yet. Exit and click Write.")
        return
    st.markdown(reading_html(spch["text"], labels=rnd.get("show_labels", True), big=True),
                unsafe_allow_html=True)


def synced_text_area(label: str, key: str, current: str, **kw) -> str:
    """A text area that shows `current` (the saved value) and returns what the user typed.

    A keyed Streamlit widget remembers its own contents and ignores a changed `value`, so when the
    app itself changes the saved text (a drafted pre-flow, a loaded demo case) the stale widget
    would hand back its old contents and overwrite it. Push the saved text into the widget
    whenever it differs from what the widget last returned."""
    shadow = f"_shadow_{key}"
    if key not in ss or ss.get(shadow) != current:
        ss[key] = current
    val = st.text_area(label, key=key, **kw)
    ss[shadow] = val
    return val


def render_case() -> None:
    res = st.text_input(
        "Resolution", mats.get("resolution", ""),
        placeholder="Paste the exact wording, e.g. Resolved: The United States ought to ...",
        help="Sent with every speech, Aff and Neg. Stays saved across rounds until you change it.",
    )
    if res != mats.get("resolution", ""):
        mats["resolution"] = res
        store.save_materials(mats)
    st.caption("Only the material for the side you're on is sent to the writer. Aff speeches never see Neg "
               "material and vice versa. Everything here stays saved on this computer.")

    def box(key: str, height: int) -> None:
        val = synced_text_area(store.SECTIONS[key], f"mat_{key}", mats["sections"].get(key, ""), height=height,
                               placeholder="Open it in Google Docs or Word, Ctrl+A, Ctrl+C, then Ctrl+V here.")
        if val != mats["sections"].get(key, ""):
            mats["sections"][key] = val
            store.save_materials(mats)
            if key.endswith("_case"):
                st.rerun()   # draft the pre-flow now, while on this tab, not at the next cell edit

    aff_col, neg_col = st.columns(2)
    with aff_col:
        st.subheader("Affirmative")
        box("aff_case", 260)
        box("aff_blocks", 260)
    with neg_col:
        st.subheader("Negative")
        box("neg_case", 260)
        box("neg_blocks", 260)
    box("shared", 180)

    st.divider()
    st.subheader("Pre-flow")
    st.caption("Drafted once from your case, then dropped into column 1 of your own sheet whenever you pick "
               "that side on a fresh round. One line per row. Edit freely.")
    pa, pn = st.columns(2)
    for col, sd in ((pa, "aff"), (pn, "neg")):
        with col:
            pf = mats["preflow"][sd]
            val = synced_text_area(f"{fmt.side_labels[sd]} pre-flow", f"preflow_{sd}", pf["text"], height=260,
                                   placeholder="Appears here automatically once that side's case is loaded.")
            if val != pf["text"]:
                pf["text"] = val
                store.save_materials(mats)
            h = store.case_hash(store.case_text_for_side(mats, sd))
            err = ss["preflow_error"].get(sd)
            if err and err[0] == h:
                st.error("Couldn't draft the pre-flow: " + (
                    "no API key is set." if "authentication" in err[1].lower() or "api_key" in err[1].lower()
                    else err[1]))
            elif h and pf["text"].strip() and pf["case_hash"] and pf["case_hash"] != h:
                st.info("Your case changed since this was drafted.")
            if h and st.button(("Redraft" if pf["text"].strip() else "Draft") + " from my case",
                               key=f"redraft_{sd}"):
                draft_preflow_for(sd)
                st.rerun()

    st.divider()
    u1, u2 = st.columns([1.4, 1])
    with u1:
        st.subheader("Or upload files")
        up_side = st.radio("These files are for", ["aff", "neg", "both"], horizontal=True,
                           format_func=lambda k: {"aff": "Aff", "neg": "Neg", "both": "Both sides"}[k])
        ups = st.file_uploader("Word, PDF, txt, md", type=["docx", "pdf", "txt", "md"], accept_multiple_files=True)
        if ups and st.button("Add these files"):
            for u in ups:
                try:
                    text = read_material_bytes(u.name, u.getvalue())
                except Exception as e:  # noqa: BLE001
                    st.warning(f"Skipped {u.name}: {e}")
                    continue
                mats["files"] = [f for f in mats["files"] if f["name"] != u.name]
                mats["files"].append({"name": u.name, "text": text, "side": up_side})
            store.save_materials(mats)
            st.rerun()
    with u2:
        if mats["files"]:
            st.subheader("Loaded files")
            for f in list(mats["files"]):
                a, b = st.columns([3, 1])
                tag = {"aff": "Aff", "neg": "Neg", "both": "Both"}.get(f.get("side", "both"), "Both")
                a.write(f"**{f['name']}** · {tag} · {len(f['text'].split()):,} words")
                if b.button("Remove", key=f"rm_{f['name']}"):
                    mats["files"] = [x for x in mats["files"] if x["name"] != f["name"]]
                    store.save_materials(mats)
                    st.rerun()


# --------------------------------------------------------------------------- layout
_snap = cx_job.snapshot()
_poll = mats.get("cx_auto") or (_snap["running"] or not _snap["collected"])
st.fragment(cx_engine, run_every=(3 if _poll else None))()

if focus == "flow":
    st.markdown(FOCUS_CSS, unsafe_allow_html=True)
    focus_bar("flow")
    render_flow(full=True)
elif focus == "speech":
    st.markdown(FOCUS_CSS, unsafe_allow_html=True)
    focus_bar("speech")
    render_speech(full=True)
elif focus == "read":
    st.markdown(FOCUS_CSS, unsafe_allow_html=True)
    render_read()
else:
    tab_flow, tab_cx, tab_speech, tab_case = st.tabs(["Flow", "Cross-ex", "Speech", "Case & Blocks"])
    with tab_flow:
        b_full, b_reset, _ = st.columns([1, 1, 5])
        b_full.button("⛶ Full screen", key="fs_flow", on_click=enter_focus, args=("flow",), width="stretch",
                      help="Hide the sidebar and tabs so the flow fills the window")
        with b_reset.popover("↺ Reset flow", width="stretch"):
            st.write("Clear every sheet on the flow? Your cross-ex notes and speeches stay. "
                     "Your pre-flow goes back in.")
            st.button("Yes, clear the flow", key="reset_flow_yes", type="primary", on_click=reset_flow)
        render_flow(full=False)
    with tab_cx:
        render_cx()
    with tab_speech:
        st.button("⛶ Full screen", key="fs_speech", on_click=enter_focus, args=("speech",),
                  help="Hide the sidebar and tabs so the speech fills the window")
        render_speech(full=False)
    with tab_case:
        render_case()

enable_multiselect_on_touch()

# --------------------------------------------------------------------------- autosave
store.save_round(rnd)
