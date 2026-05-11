import streamlit as st
from core.reports import counters, aggregate_runs
from core.scenarios import list_scenarios

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
runs = aggregate_runs(DATA_SCANS)[:10]
if not runs:
    st.info("No runs yet — create a scenario and click ▶ Run now to get started.")
else:
    for r in runs:
        icon = "✓" if r["status"] == "PASS" else "✗"
        color = "green" if r["status"] == "PASS" else "red"
        st.markdown(f":{color}[{icon}] **{r['timestamp']}** · {r['test_case_name']} "
                    f"· {r['element_name']} · {r['url']}")

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
