import streamlit as st
from core.reports import counters, aggregate_runs
from core.scenarios import list_scenarios
from ui.runs_view import group_runs, render_run_body, run_status

DATA_SCANS = "data/scans"
DATA_SCENARIOS = "data/scenarios"

st.set_page_config(page_title="Dashboard", layout="wide")
st.title("Dashboard")

c = counters(DATA_SCANS)
m1, m2, m3 = st.columns(3)
m1.metric("Passing", c["passing"])
m2.metric("Failing", c["failing"])
m3.metric("Healed selectors (all-time)", c["healed"])

st.divider()
st.subheader("Recent runs")
rows = aggregate_runs(DATA_SCANS)
if not rows:
    st.info("No runs yet — create a scenario and click ▶ Run now to get started.")
else:
    # Show the 10 most-recent runs, grouped by run_id (timestamp fallback for
    # legacy data). Each run is collapsible and reveals the same row → field
    # hierarchy used on the Runs tab — with scenario name and URL surfaced in
    # the header so the dashboard is useful across scenarios.
    groups = group_runs(rows)
    for idx, (run_key, bucket) in enumerate(list(groups.items())[:10]):
        icon, passed, total = run_status(bucket["rows"])
        ts = bucket["timestamp"] or "—"
        sc_name = bucket.get("test_case_name") or "—"
        url = bucket.get("url") or ""
        header_bits = [f"{icon} {ts}", sc_name, f"{passed}/{total} rows passed"]
        if url:
            header_bits.append(url)
        with st.expander(" · ".join(header_bits),
                         expanded=(idx == 0 and passed != total)):
            render_run_body(bucket)

st.divider()
st.subheader("Quick actions")
q1, q2, q3 = st.columns(3)
if q1.button("Scan new page", use_container_width=True):
    st.switch_page("pages/3_library.py")
if q2.button("New scenario", use_container_width=True):
    st.session_state["_open_scenario"] = "__new__"
    st.switch_page("pages/2_scenarios.py")
if q3.button("View reports", use_container_width=True):
    st.switch_page("pages/4_reports.py")
