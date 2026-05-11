import streamlit as st
import pandas as pd
from core.reports import aggregate_activity

DATA_SCANS = "data/scans"


def render():
    acts = aggregate_activity(DATA_SCANS)
    if not acts:
        st.info("No activity yet.")
        return
    st.dataframe(pd.DataFrame(acts), use_container_width=True)
