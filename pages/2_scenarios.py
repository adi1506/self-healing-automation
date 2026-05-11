import streamlit as st
from datetime import datetime
from core.scenarios import Scenario, save_scenario
from ui.scenarios.list import render as render_list
from ui.scenarios.detail import render as render_detail
from core.excel_manager import ExcelManager

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"

st.set_page_config(page_title="Scenarios", layout="wide")

open_id = st.session_state.get("_open_scenario")

if open_id == "__new__":
    st.title("New scenario")
    em = ExcelManager(data_dir=DATA_SCANS)
    urls = [""] + em.list_scanned_urls()
    sid = st.text_input("ID (slug)", placeholder="login_valid")
    name = st.text_input("Name", placeholder="Login valid")
    base_url = st.selectbox("Base URL (scanned page)", urls)
    if st.button("Create", type="primary", disabled=not (sid and name)):
        sc = Scenario(
            id=sid, name=name, kind="single-page", base_url=base_url,
            steps=[{"action": "fill", "target": "", "value": ""}],
            dataset=[], expected_outcome="success",
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        save_scenario(DATA_SCENARIOS, sc)
        st.session_state["_open_scenario"] = sid
        st.rerun()
    if st.button("Cancel"):
        st.session_state.pop("_open_scenario", None)
        st.rerun()
elif open_id:
    render_detail(open_id)
else:
    st.title("Scenarios")
    render_list()
