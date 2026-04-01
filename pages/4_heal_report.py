import asyncio
import os
import streamlit as st
from datetime import datetime
from core.healer import Healer
from core.excel_manager import ExcelManager

st.set_page_config(page_title="Heal Report", page_icon="🔄", layout="wide")
st.title("🔄 Self-Heal Report")

DATA_DIR = "data/scans"
excel_manager = ExcelManager(data_dir=DATA_DIR)

api_key = os.environ.get("GEMINI_API_KEY", "")
if "gemini_api_key" not in st.session_state:
    st.session_state.gemini_api_key = api_key

scanned_urls = excel_manager.list_scanned_urls()

if not scanned_urls:
    st.info("No scanned URLs found. Go to the Scanner page first.")
    st.stop()

url = st.selectbox("Select URL", scanned_urls)

if url:
    heal_history = excel_manager.read_heal_history(url)

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Re-Scan Now", type="primary"):
            healer = Healer(ai_api_key=st.session_state.gemini_api_key)
            with st.spinner("Running self-heal scan..."):
                report = asyncio.run(healer.heal(url, excel_manager))

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            heal_id = f"HEAL-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            for change in report.get("changes", []):
                excel_manager.append_heal_history(url, {
                    "heal_id": heal_id,
                    "timestamp": timestamp,
                    "element_name": change["element_name"],
                    "change_type": "CHANGED" if change["healed_by"] else "NEW" if "NEW" in change["change_details"] else "REMOVED",
                    "change_details": change["change_details"],
                    "healed_by": change["healed_by"],
                })

            scan_id = f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            excel_manager.append_scan_history(url, {
                "scan_id": scan_id,
                "timestamp": timestamp,
                "total_elements": report["total_elements"],
                "new": report["new"],
                "changed": report["changed"],
                "removed": report["removed"],
                "unchanged": report["unchanged"],
            })

            st.subheader(f"Heal Report — {timestamp}")

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Total", report["total_elements"])
            m2.metric("Unchanged", report["unchanged"])
            m3.metric("Changed", report["changed"])
            m4.metric("New", report["new"])
            m5.metric("Removed", report["removed"])

            changes = report.get("changes", [])
            if changes:
                st.subheader("Changes Detected")
                change_data = []
                for c in changes:
                    change_data.append({
                        "Element": c["element_name"],
                        "What Changed": c["change_details"],
                        "Healed By": c["healed_by"] or "—",
                    })
                st.dataframe(change_data, use_container_width=True)
            else:
                st.success("No changes detected — all elements are stable.")

    st.divider()
    st.subheader("Current Element Map")
    element_map = excel_manager.read_element_map(url)
    if element_map:
        map_data = []
        for elem in element_map:
            map_data.append({
                "S.No": elem["sno"],
                "Element Name": elem["element_name"],
                "Type": elem["element_type"],
                "Status": elem["status"],
                "Change Details": elem.get("change_details", ""),
                "Healed By": elem.get("healed_by", ""),
            })
        st.dataframe(map_data, use_container_width=True)
