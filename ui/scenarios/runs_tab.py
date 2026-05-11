import streamlit as st
from core.reports import aggregate_runs

DATA_SCANS = "data/scans"


def render(sc):
    runs = [r for r in aggregate_runs(DATA_SCANS) if r.get("test_case_name") == sc.name]
    if not runs:
        st.info("No runs yet for this scenario.")
        return
    for r in runs[:50]:
        icon = "✓" if r["status"] == "PASS" else "✗"
        color = "green" if r["status"] == "PASS" else "red"
        st.markdown(f":{color}[{icon}] **{r['timestamp']}** — {r['element_name']} "
                    f"(expected `{r.get('Expected Value', '')}`, actual `{r.get('Actual Value', '')}`)")
