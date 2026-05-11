import streamlit as st
from core.scenarios import save_scenario, delete_scenario
from core.excel_manager import ExcelManager

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"


def render(sc):
    em = ExcelManager(data_dir=DATA_SCANS)
    urls = [""] + em.list_scanned_urls()

    name = st.text_input("Name", value=sc.name, key=f"sname_{sc.id}")
    base_url = st.selectbox(
        "Base URL (scanned page)", options=urls,
        index=urls.index(sc.base_url) if sc.base_url in urls else 0,
        key=f"surl_{sc.id}",
    )
    outcome = st.selectbox(
        "Expected outcome", ["success", "failure"],
        index=0 if sc.expected_outcome == "success" else 1,
        key=f"sout_{sc.id}",
    )

    c1, c2 = st.columns(2)
    if c1.button("Save settings", type="primary", key=f"savecfg_{sc.id}"):
        sc.name = name; sc.base_url = base_url; sc.expected_outcome = outcome
        save_scenario(DATA_SCENARIOS, sc)
        st.success("Settings saved.")
        st.rerun()
    if c2.button("Delete scenario", key=f"delcfg_{sc.id}"):
        delete_scenario(DATA_SCENARIOS, sc.id)
        st.session_state.pop("_open_scenario", None)
        st.rerun()
