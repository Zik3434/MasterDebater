import json
from pathlib import Path

from masterdebater.flow import flow_from_dict, load_flexcel, next_speech, parse_flexcel
from masterdebater.formats import LD, POLICY, canonical_speech, public_forum

ROOT = Path(__file__).resolve().parent.parent


def test_parse_sample_flow():
    flow = load_flexcel(ROOT / "examples" / "sample_flow.json")
    assert flow.format is LD
    assert [s.columns for s in flow.sheets] == [
        ["1AC", "1NC", "1AR", "2NR", "2AR"],
        ["1NC", "1AR", "2NR", "2AR"],
    ]
    assert flow.speeches_with_content() == ["1AC", "1NC"]
    text = flow.to_text()
    assert "[1NC] Turn: policy increases black market" in text
    assert "(sheet is empty)" not in text


def test_next_speech_logic():
    flow = load_flexcel(ROOT / "examples" / "sample_flow.json")
    assert next_speech(flow, "aff") == "1AR"
    assert next_speech(flow, "neg") == "2NR"
    empty = parse_flexcel({"flow_type": "LD Traditional Flow", "flow-data": [[[None] * 5], [[None] * 4]]})
    assert empty.is_empty()
    assert next_speech(empty, "aff") == "1AC"
    assert next_speech(empty, "neg") == "1NC"


def test_policy_and_pf():
    pol = parse_flexcel({
        "flow_type": "Policy Flow",
        "flow-data": [[["plan", "T", "we meet", "", "", "", ""]]],
        "tab-names": ["Case"], "tab-types": ["1AC"],
    })
    assert pol.format is POLICY
    assert pol.sheets[0].name == "Case"
    assert next_speech(pol, "neg") == "2NC/1NR"

    pf = parse_flexcel({
        "flow_type": "PF Flow", "firstSpeaker": "Con",
        "flow-data": [[
            ["Con Constructive", "Pro Rebuttal", "Con Summary", "Pro Summary", "Con Final Focus", "Pro Final Focus", "", ""],
            ["C1 jobs", "no link", None, None, None, None, None, None],
        ]],
    })
    assert pf.format.order()[0] == "Con Constructive"
    assert pf.sheets[0].columns[:2] == ["Con Constructive", "Pro Rebuttal"]
    assert next_speech(pf, "neg") == "Con Summary"
    assert canonical_speech(public_forum("pro"), "Neg Final Focus") == "Con Final Focus"


def test_roundtrip_dict():
    flow = load_flexcel(ROOT / "examples" / "sample_flow.json")
    again = flow_from_dict(json.loads(flow.to_json()))
    assert again.to_text() == flow.to_text()


def test_preflow_insert_rules():
    from masterdebater import store

    m = store.empty_materials()
    m["preflow"]["aff"]["text"] = "V: morality\nC1: deaths"
    m["preflow"]["neg"]["text"] = "NC: Kant"

    r = store.new_round("ld")
    assert store.apply_preflow(r, m, "aff")
    assert [x[0] for x in r["sheets"][0]["rows"][:2]] == ["V: morality", "C1: deaths"]
    assert not store.apply_preflow(r, m, "aff")            # idempotent

    # Switching side right after an untouched auto-insert undoes it and fills the other sheet.
    assert store.apply_preflow(r, m, "neg")
    assert r["sheets"][0]["rows"][0][0] == ""
    assert r["sheets"][1]["rows"][0][0] == "NC: Kant"

    # Anything already flowed is never overwritten or removed.
    r2 = store.new_round("ld")
    store.apply_preflow(r2, m, "aff")
    r2["sheets"][0]["rows"][0][1] = "their answer"
    store.apply_preflow(r2, m, "neg")
    assert r2["sheets"][0]["rows"][0][:2] == ["V: morality", "their answer"]
