import streamlit as st
from core.excel_manager import ExcelManager

st.set_page_config(page_title="History", page_icon="📋", layout="wide")
st.title("📋 History")

DATA_DIR = "data/scans"
excel_manager = ExcelManager(data_dir=DATA_DIR)

scanned_urls = excel_manager.list_scanned_urls()

if not scanned_urls:
    st.info("No scanned URLs found. Go to the Scanner page first.")
    st.stop()

url = st.selectbox("Select URL", scanned_urls)

if url:
    tab1, tab2, tab3 = st.tabs(["Scan History", "Run Results", "Heal Log"])

    with tab1:
        scan_history = excel_manager.read_scan_history(url)
        if scan_history:
            display = []
            for entry in reversed(scan_history):
                display.append({
                    "Scan ID": entry.get("scan_id", ""),
                    "Timestamp": entry.get("timestamp", ""),
                    "Total Elements": entry.get("total_elements", ""),
                    "New": entry.get("new", ""),
                    "Changed": entry.get("changed", ""),
                    "Removed": entry.get("removed", ""),
                    "Unchanged": entry.get("unchanged", ""),
                })
            st.dataframe(display, use_container_width=True)
        else:
            st.info("No scan history available.")

    with tab2:
        run_results = excel_manager.read_run_results(url)
        if run_results:
            runs = {}
            for r in run_results:
                rid = r.get("run_id", "")
                if rid not in runs:
                    runs[rid] = {
                        "Run ID": rid,
                        "Timestamp": r.get("timestamp", ""),
                        "Test Case": r.get("test_case_name", ""),
                        "Total": 0,
                        "Passed": 0,
                        "Failed": 0,
                    }
                runs[rid]["Total"] += 1
                if r.get("status") == "PASS":
                    runs[rid]["Passed"] += 1
                else:
                    runs[rid]["Failed"] += 1

            summary = list(reversed(runs.values()))
            st.subheader("Run Summary")
            st.dataframe(summary, use_container_width=True)

            st.subheader("Detailed Results")
            selected_run = st.selectbox(
                "Select Run",
                [r["Run ID"] for r in summary],
            )
            if selected_run:
                details = [
                    {
                        "Element": r.get("element_name", ""),
                        "Expected": r.get("expected_value", ""),
                        "Actual": r.get("actual_value", ""),
                        "Status": r.get("status", ""),
                    }
                    for r in run_results
                    if r.get("run_id") == selected_run
                ]
                st.dataframe(details, use_container_width=True)
        else:
            st.info("No run results available.")

    with tab3:
        heal_history = excel_manager.read_heal_history(url)
        if heal_history:
            display = []
            for entry in reversed(heal_history):
                display.append({
                    "Heal ID": entry.get("heal_id", ""),
                    "Timestamp": entry.get("timestamp", ""),
                    "Element": entry.get("element_name", ""),
                    "Change Type": entry.get("change_type", ""),
                    "Change Details": entry.get("change_details", ""),
                    "Healed By": entry.get("healed_by", ""),
                })
            st.dataframe(display, use_container_width=True)
        else:
            st.info("No heal history available.")
