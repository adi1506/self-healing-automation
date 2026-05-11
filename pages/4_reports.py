import streamlit as st
from ui.reports.runs import render as render_runs
from ui.reports.healing import render as render_healing
from ui.reports.activity import render as render_activity

st.set_page_config(page_title="Reports", layout="wide")
st.title("Reports")

tab1, tab2, tab3 = st.tabs(["Run history", "Healing log", "Activity"])
with tab1: render_runs()
with tab2: render_healing()
with tab3: render_activity()
