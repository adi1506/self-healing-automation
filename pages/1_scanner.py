import streamlit as st
from datetime import datetime
from core.scanner import Scanner
from core.excel_manager import ExcelManager

st.set_page_config(page_title="Scanner", layout="wide")
st.title("Website Scanner")

DATA_DIR = "data/scans"
excel_manager = ExcelManager(data_dir=DATA_DIR)
scanner = Scanner()

url = st.text_input("Target URL", placeholder="https://your-app.com/form")

if st.button("Scan Website", type="primary", disabled=not url):
    with st.spinner("Scanning page..."):
        elements = scanner.scan(url)

    if elements:
        excel_manager.save_element_map(url, elements)

        scan_id = f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        excel_manager.append_scan_history(url, {
            "scan_id": scan_id,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_elements": len(elements),
            "new": len(elements),
            "changed": 0,
            "removed": 0,
            "unchanged": 0,
        })

        st.success(f"Found {len(elements)} elements!")

        display_data = []
        for elem in elements:
            display_data.append({
                "S.No": elem["sno"],
                "Element Name": elem["element_name"],
                "Type": elem["element_type"],
                "ID": elem.get("locator_id", ""),
                "Name": elem.get("locator_name", ""),
                "Data-TestID": elem.get("locator_data_testid", ""),
                "Status": elem["status"],
            })
        st.dataframe(display_data, use_container_width=True)

        excel_path = excel_manager.get_excel_path(url)
        with open(excel_path, "rb") as f:
            st.download_button(
                label="Download Excel",
                data=f.read(),
                file_name=f"scan_{excel_manager.sanitize_url(url)}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.warning("No interactive elements found on the page.")

scanned_urls = excel_manager.list_scanned_urls()
if scanned_urls:
    st.divider()
    st.subheader("Previously Scanned URLs")
    for scanned_url in scanned_urls:
        col1, col2 = st.columns([5, 1])
        col1.text(scanned_url)
        if col2.button("Delete", key=f"del_{scanned_url}"):
            excel_manager.delete_url(scanned_url)
            st.success(f"Deleted: {scanned_url}")
            st.rerun()
