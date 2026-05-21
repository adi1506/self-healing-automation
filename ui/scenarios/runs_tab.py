import streamlit as st

from core.reports import aggregate_runs
from ui.runs_view import group_runs, render_run_body, run_status

DATA_SCANS = "data/scans"


def render(sc):
    st.caption(
        "ⓘ **Runs** is the execution history for this scenario — every time "
        "the Steps were executed against a Dataset row, with PASS/FAIL per "
        "field. Steps + Dataset define *what* runs; this tab shows *what "
        "happened*."
    )

    rows = [r for r in aggregate_runs(DATA_SCANS) if r.get("test_case_name") == sc.name]
    if not rows:
        st.info("No runs yet for this scenario.")
        return

    groups = group_runs(rows)
    # Cap at the 50 most recent runs to keep the page responsive on long
    # histories. The aggregate is already newest-first, so the first 50 keys
    # are the right ones.
    for idx, (run_key, bucket) in enumerate(list(groups.items())[:50]):
        icon, passed, total = run_status(bucket["rows"])
        ts = bucket["timestamp"] or "—"
        header = f"{icon} {ts} — {passed}/{total} rows passed"
        with st.expander(header, expanded=(idx == 0 and passed != total)):
            render_run_body(bucket)
