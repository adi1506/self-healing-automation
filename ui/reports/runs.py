import streamlit as st
import pandas as pd
from core.reports import aggregate_runs
from core.dataset_io import dataset_to_xlsx_bytes

DATA_SCANS = "data/scans"


def render():
    runs = aggregate_runs(DATA_SCANS)
    if not runs:
        st.info("No runs yet.")
        return
    q = st.text_input("Filter by scenario / status", "").strip().lower()
    filtered = [r for r in runs if not q or q in (r.get("test_case_name", "").lower()
                                                  + r.get("status", "").lower())]
    df = pd.DataFrame(filtered)
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "⬇ Export Excel",
        data=dataset_to_xlsx_bytes(filtered),
        file_name="run_history.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
