import streamlit as st
import pandas as pd
from core.reports import aggregate_heal_events
from core.dataset_io import dataset_to_xlsx_bytes

DATA_SCANS = "data/scans"


def render():
    events = aggregate_heal_events(DATA_SCANS)
    if not events:
        st.info("No healing events yet.")
        return
    q = st.text_input("Filter (element / change type / healer)", "", key="heal_q").strip().lower()
    filtered = [e for e in events if not q or any(q in str(v).lower() for v in e.values())]
    st.dataframe(pd.DataFrame(filtered), use_container_width=True)
    st.download_button(
        "⬇ Export Excel",
        data=dataset_to_xlsx_bytes(filtered),
        file_name="healing_log.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
