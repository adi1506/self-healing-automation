import streamlit as st
from core.scenarios import list_scenarios
from core.reports import aggregate_runs

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"


def _last_status(name: str, runs: list[dict]) -> tuple[str, str]:
    for r in runs:
        if r.get("test_case_name") == name:
            return r["status"], r["timestamp"]
    return ("", "")


def render():
    runs = aggregate_runs(DATA_SCANS)
    scs = list_scenarios(DATA_SCENARIOS)

    c1, c2 = st.columns([4, 1])
    c1.subheader("Scenarios")
    if c2.button("+ New scenario", type="primary"):
        st.session_state["_open_scenario"] = "__new__"
        st.rerun()

    if not scs:
        st.info("No scenarios yet. Click + New scenario to create one.")
        return

    for sc in scs:
        status, when = _last_status(sc.name, runs)
        pill = {"PASS": ":green[● passing]", "FAIL": ":red[● failing]"}.get(status, ":gray[○ never run]")
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.markdown(f"**{sc.name}**  \n{pill} {('· ' + when) if when else ''}")
            c2.caption(f"{sc.kind} · {len(sc.dataset)} dataset rows · {len(sc.steps) or len(sc.recipe_refs)} steps")
            if c3.button("Open", key=f"open_{sc.id}"):
                st.session_state["_open_scenario"] = sc.id
                st.rerun()
