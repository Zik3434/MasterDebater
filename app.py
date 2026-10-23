"""MasterDebater desktop UI (Streamlit).

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import streamlit as st

from masterdebater import claude_client as cc
from masterdebater.capture import capture_screen, list_monitors
from masterdebater.docx_out import write_speech_docx
from masterdebater.flow import Flow, next_speech, parse_flexcel
from masterdebater.materials import bundle_materials, read_material_bytes

st.set_page_config(page_title="MasterDebater", page_icon="🎤", layout="wide")
st.title("MasterDebater")
st.caption(f"Flexcel flow → your next speech. Model: `{cc.MODEL}`. Evidence comes only from the files you upload.")

if "flow" not in st.session_state:
    st.session_state.flow = None
if "result" not in st.session_state:
    st.session_state.result = None

# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("1. Who am I?")
    side = st.radio("Side", ["aff", "neg"], format_func=lambda s: {"aff": "Affirmative / Pro", "neg": "Negative / Con"}[s], horizontal=True)
    wpm = st.slider("Speaking rate (words per minute)", 120, 350, cc.DEFAULT_WPM, 10)

    st.header("2. My case & blocks")
    st.caption("The only evidence the speech may use. Word, PDF, txt or md.")
    uploads = st.file_uploader("Upload files", type=["docx", "pdf", "txt", "md"], accept_multiple_files=True)

    st.header("3. The flow")
    mode = st.radio("Get the flow from", ["Flexcel .json (Ctrl+S in Flexcel)", "Screenshot upload", "Capture my screen"], index=0)

# --------------------------------------------------------------------------- flow input
col_in, col_flow = st.columns([1, 1.4])

with col_in:
    if mode.startswith("Flexcel"):
        f = st.file_uploader("Flexcel save file", type=["json"], key="flowjson")
        if f is not None:
            try:
                st.session_state.flow = parse_flexcel(json.load(f))
                st.success(f"Loaded {st.session_state.flow.flow_type} with {len(st.session_state.flow.sheets)} sheet(s).")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read that file: {e}")

    elif mode.startswith("Screenshot"):
        img = st.file_uploader("Screenshot of Flexcel", type=["png", "jpg", "jpeg", "webp"], key="flowimg")
        if img is not None:
            st.image(img, caption="Uploaded flow", use_container_width=True)
            if st.button("Transcribe with Claude", type="primary"):
                suffix = Path(img.name).suffix or ".png"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(img.getvalue())
                    tmp_path = tmp.name
                with st.spinner("Reading the flow..."):
                    try:
                        st.session_state.flow = cc.transcribe_flow_image(tmp_path)
                        st.success("Flow transcribed. Check it on the right before generating.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Transcription failed: {e}")

    else:
        mons = []
        try:
            mons = list_monitors()
        except Exception as e:  # noqa: BLE001
            st.error(f"Screen capture is unavailable here: {e}")
        if mons:
            choice = st.selectbox("Monitor", list(range(1, len(mons))) or [1], format_func=lambda i: f"Monitor {i} ({mons[i]['width']}x{mons[i]['height']})")
            st.caption("Put Flexcel on that monitor with the sheet you want visible, then click.")
            if st.button("Capture + transcribe", type="primary"):
                shot = capture_screen("captures/flow.png", choice)
                st.image(str(shot), caption="Captured", use_container_width=True)
                with st.spinner("Reading the flow..."):
                    try:
                        st.session_state.flow = cc.transcribe_flow_image(shot)
                        st.success("Flow transcribed. Check it on the right before generating.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Transcription failed: {e}")

with col_flow:
    flow: Flow | None = st.session_state.flow
    if flow is None:
        st.info("Load a flow to continue.")
    else:
        fmt = flow.format
        st.subheader(f"{fmt.label} flow")
        tabs = st.tabs([s.name for s in flow.sheets] or ["(no sheets)"])
        for tab, sheet in zip(tabs, flow.sheets):
            with tab:
                rows = [r for r in sheet.rows if any(c.strip() for c in r)]
                if rows:
                    st.dataframe([dict(zip(sheet.columns, r)) for r in rows], use_container_width=True, hide_index=True)
                else:
                    st.caption("Empty sheet")

# --------------------------------------------------------------------------- generate
st.divider()
flow = st.session_state.flow
if flow is not None:
    fmt = flow.format
    mine = [s.name for s in fmt.speeches_for(side)]
    suggested = next_speech(flow, side)
    c1, c2 = st.columns([1, 2])
    with c1:
        speech = st.selectbox("Speech to write", mine, index=mine.index(suggested) if suggested in mine else 0,
                              help="Auto-picked from what's already on the flow. Override if you want.")
        sp = fmt.speech(speech)
        st.caption(f"{sp.minutes:g} min ≈ {int(sp.minutes * wpm)} words at {wpm} wpm")
    with c2:
        extra = st.text_area("Extra instructions (optional)", placeholder="e.g. Go for the framework + contention 1 only. Kick the theory shell.", height=90)

    materials = []
    for u in uploads or []:
        try:
            materials.append((u.name, read_material_bytes(u.name, u.getvalue())))
        except Exception as e:  # noqa: BLE001
            st.warning(f"Skipped {u.name}: {e}")
    if not materials:
        st.warning("No case/blocks uploaded. The speech will be purely analytic (no cards).")

    if st.button(f"Write my {speech}", type="primary", use_container_width=True):
        box = st.empty()
        buf: list[str] = []

        def on_text(chunk: str) -> None:
            buf.append(chunk)
            box.markdown("".join(buf))

        try:
            with st.spinner("Writing..."):
                st.session_state.result = cc.generate_speech(
                    flow, side, speech, bundle_materials(materials), wpm=wpm,
                    extra_instructions=extra, on_text=on_text,
                )
            box.empty()
        except Exception as e:  # noqa: BLE001
            st.session_state.result = None
            st.error(f"Generation failed: {e}")

    res = st.session_state.result
    if res is not None and res.speech.name == speech:
        over = res.word_count > res.word_target * 1.1
        st.metric("Words", res.word_count, delta=f"target {res.word_target}", delta_color="inverse" if over else "off")
        if res.served_by_fallback:
            st.info(f"Served by fallback model {res.model}.")
        st.markdown(res.text)
        if res.notes:
            with st.expander("Coach notes"):
                st.markdown(res.notes)

        out = Path(tempfile.gettempdir()) / f"{speech.replace('/', '-')}_{side}.docx"
        write_speech_docx(out, res.speech.name, fmt.side_labels[side], res.text, res.notes,
                          word_count=res.word_count, minutes=res.speech.minutes)
        st.download_button("Download Word doc", data=out.read_bytes(), file_name=out.name,
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           use_container_width=True)
