"""MasterDebater: flow the round here, keep your case here, get the speech here.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from masterdebater import claude_client as cc
from masterdebater import store
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
ss.setdefault("focus", None)   # None | "flow" | "speech"  -> full-screen mode

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
    bump_grids()


FOCUS_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], header[data-testid="stHeader"] { display: none !important; }
.block-container { padding-top: 0.6rem !important; padding-bottom: 0 !important; max-width: 100% !important; }
</style>
"""

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
            if st.button("Switch format (starts a new round)", use_container_width=True):
                ss["round"] = store.new_round(fmt_choice, first_speaker)
                ss["round"]["side"] = side
                store.save_round(ss["round"])
                bump_grids()
                st.rerun()

        ss["wpm"] = st.slider("Speaking rate (wpm)", 120, 350, ss["wpm"], 10)

        st.divider()
        rnd["name"] = st.text_input("Round name", rnd.get("name", ""))
        if st.button("New round (clears the flow)", use_container_width=True):
            ss["round"] = store.new_round(rnd.get("format", "ld"), rnd.get("first_speaker", "pro"))
            ss["round"]["side"] = side
            store.save_round(ss["round"])
            bump_grids()
            st.rerun()

        n_files = len(mats.get("files", []))
        n_words = len(mats.get("pasted", "").split()) + sum(len(f["text"].split()) for f in mats.get("files", []))
        if n_words:
            st.success(f"Case loaded: {n_files} file(s), {n_words:,} words")
        else:
            st.warning("No case loaded yet. Open the Case & Blocks tab.")

        st.divider()
        if st.button("Update MasterDebater", use_container_width=True,
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
        c1.button(f"→ {label[other]}", use_container_width=True, on_click=enter_focus, args=(other,),
                  help=f"Switch the full-screen view to {label[other]}")
        c2.button("✕ Exit", use_container_width=True, on_click=exit_focus, help="Back to the normal layout")


def render_flow(full: bool) -> None:
    sheet_names = [s["name"] for s in rnd["sheets"]]
    sub = st.tabs(sheet_names + ["Cross-ex", "Notes", "Sheets & import"])
    v = ss["grid_version"]
    grid_height = 760 if full else 560

    for i, (tab, sheet) in enumerate(zip(sub, rnd["sheets"])):
        with tab:
            cols = sheet["columns"]
            # The editor keeps its own diff on top of the frame we pass in, so pass the SAME
            # base frame every rerun (snapshotted per grid version) and treat its output as truth.
            base_key = f"base_{v}_{i}"
            if base_key not in ss:
                ss[base_key] = pd.DataFrame(sheet["rows"], columns=cols).astype(str)
            edited = st.data_editor(
                ss[base_key],
                key=f"grid_{v}_{i}",
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                height=grid_height,
                # A small base width per column: Streamlit spreads the leftover space evenly,
                # so every speech column stays on screen at once, the way Flexcel lays it out.
                column_config={c: st.column_config.TextColumn(c, width=120) for c in cols},
            )
            rows = edited.fillna("").astype(str).values.tolist()
            if rows != sheet["rows"]:
                sheet["rows"] = rows
            if not full:
                st.caption("Click a cell and type. Enter moves down, Tab moves right. Rows are argument "
                           "threads: the first column is the argument, the columns to its right answer it.")

    with sub[len(sheet_names)]:
        c1, c2 = st.columns(2)
        h = 640 if full else 420
        with c1:
            rnd["cx"]["ask"] = st.text_area("Questions I want to ask", rnd["cx"].get("ask", ""), height=h,
                                            placeholder="One per line. The speech writer sees these.")
        with c2:
            rnd["cx"]["answers"] = st.text_area("What they said in cross-ex", rnd["cx"].get("answers", ""), height=h,
                                                placeholder="Concessions, clarifications, anything to use in the speech.")

    with sub[len(sheet_names) + 1]:
        rnd["notes"] = st.text_area("Notes for this round", rnd.get("notes", ""), height=640 if full else 420,
                                    placeholder="Judge paradigm, what to collapse to, anything else.")

    with sub[len(sheet_names) + 2]:
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
            if not mats.get("pasted", "").strip() and not mats.get("files"):
                mats["pasted"] = store.demo_case_text()
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
        go = st.button(f"Write my {speech}", type="primary", use_container_width=True)

    if flow.is_empty() and speech not in (fmt.order()[0], fmt.order()[1]):
        st.warning("The flow is empty. Fill in the Flow tab first, or the speech will have nothing to answer.")

    materials_pairs = []
    if mats.get("pasted", "").strip():
        materials_pairs.append(("pasted case and blocks", mats["pasted"]))
    materials_pairs += [(f["name"], f["text"]) for f in mats.get("files", [])]

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
            edited = st.text_area("Speech (edit freely, this is what you read)", spch["text"],
                                  height=(720 if full else 520), key=f"speech_{spch['speech']}_{spch['at']}")
            if edited != spch["text"]:
                spch["text"] = edited
                spch["words"] = len(edited.split())
                store.save_round(rnd)
            if spch.get("notes"):
                with st.expander("Coach notes: coverage, missing cards, risks"):
                    st.markdown(spch["notes"])
            out = store.SPEECH_DIR / rnd["name"].replace("/", "-") / f"{spch['speech'].replace('/', '-')}_{spch['side']}.docx"
            write_speech_docx(out, spch["speech"], fmt.side_labels[spch["side"]], spch["text"], spch.get("notes"),
                              word_count=spch["words"], minutes=fmt.speech(spch["speech"]).minutes)
            st.download_button("Download as Word", data=out.read_bytes(), file_name=out.name,
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               key=f"dl_{spch['speech']}_{spch['at']}")


def render_case() -> None:
    c1, c2 = st.columns([1.4, 1])
    with c1:
        pasted = st.text_area(
            "Paste your case, blocks and cards", mats.get("pasted", ""), height=560,
            placeholder="Open your case in Google Docs or Word, select all (Ctrl+A), copy (Ctrl+C), paste here "
                        "(Ctrl+V). Paste more files below it; headings help the writer find blocks.",
        )
        if pasted != mats.get("pasted", ""):
            mats["pasted"] = pasted
            store.save_materials(mats)
    with c2:
        st.subheader("Or upload files")
        ups = st.file_uploader("Word, PDF, txt, md", type=["docx", "pdf", "txt", "md"], accept_multiple_files=True)
        if ups and st.button("Add these files"):
            for u in ups:
                try:
                    text = read_material_bytes(u.name, u.getvalue())
                except Exception as e:  # noqa: BLE001
                    st.warning(f"Skipped {u.name}: {e}")
                    continue
                mats["files"] = [f for f in mats["files"] if f["name"] != u.name]
                mats["files"].append({"name": u.name, "text": text})
            store.save_materials(mats)
            st.rerun()
        if mats["files"]:
            st.subheader("Loaded files")
            for f in list(mats["files"]):
                a, b = st.columns([3, 1])
                a.write(f"**{f['name']}** · {len(f['text'].split()):,} words")
                if b.button("Remove", key=f"rm_{f['name']}"):
                    mats["files"] = [x for x in mats["files"] if x["name"] != f["name"]]
                    store.save_materials(mats)
                    st.rerun()


# --------------------------------------------------------------------------- layout
if focus == "flow":
    st.markdown(FOCUS_CSS, unsafe_allow_html=True)
    focus_bar("flow")
    render_flow(full=True)
elif focus == "speech":
    st.markdown(FOCUS_CSS, unsafe_allow_html=True)
    focus_bar("speech")
    render_speech(full=True)
else:
    tab_flow, tab_speech, tab_case = st.tabs(["Flow", "Speech", "Case & Blocks"])
    with tab_flow:
        st.button("⛶ Full screen", key="fs_flow", on_click=enter_focus, args=("flow",),
                  help="Hide the sidebar and tabs so the flow fills the window")
        render_flow(full=False)
    with tab_speech:
        st.button("⛶ Full screen", key="fs_speech", on_click=enter_focus, args=("speech",),
                  help="Hide the sidebar and tabs so the speech fills the window")
        render_speech(full=False)
    with tab_case:
        render_case()

# --------------------------------------------------------------------------- autosave
store.save_round(rnd)
