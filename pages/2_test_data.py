import streamlit as st
import pandas as pd
from core.excel_manager import ExcelManager

st.set_page_config(page_title="Test Data", layout="wide")
st.title("Test Data Manager")

DATA_DIR = "data/scans"
excel_manager = ExcelManager(data_dir=DATA_DIR)

scanned_urls = excel_manager.list_scanned_urls()

if not scanned_urls:
    st.info("No scanned URLs found. Go to the Scanner page first.")
    st.stop()

url = st.selectbox("Select Scanned URL", scanned_urls)

if url:
    element_map = excel_manager.read_element_map(url)
    editable_names = [
        e["element_name"] for e in element_map
        if e["element_type"] not in ("button",)
    ]

    test_data = excel_manager.read_test_data(url)

    columns = ["S.No", "Test Case Name"] + editable_names
    if test_data:
        rows = []
        for td in test_data:
            row = {
                "S.No": td.get("S.No", ""),
                "Test Case Name": td.get("Test Case Name", ""),
            }
            for name in editable_names:
                row[name] = td.get(name, "")
            rows.append(row)
        df = pd.DataFrame(rows, columns=columns)
    else:
        df = pd.DataFrame([{col: "" for col in columns}], columns=columns)
        df["S.No"] = 1

    st.subheader("Test Cases")
    st.caption("Edit the table below to add or modify test data. Click Save when done.")

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "S.No": st.column_config.NumberColumn("S.No", disabled=True),
        },
    )

    if st.button("Save", type="primary"):
        save_rows = []
        for idx, row in edited_df.iterrows():
            row_dict = {"sno": idx + 1, "test_case_name": row.get("Test Case Name", "")}
            for name in editable_names:
                row_dict[name] = row.get(name, "")
            save_rows.append(row_dict)

        excel_manager.save_test_data(url, save_rows)
        st.success("Test data saved!")

    st.divider()
    st.subheader("Field Reference")
    ref_data = []
    for elem in element_map:
        if elem["element_type"] in ("button",):
            continue
        ref = {
            "Field": elem["element_name"],
            "Type": elem["element_type"],
            "Available Options": elem.get("available_options", ""),
        }
        ref_data.append(ref)
    st.dataframe(ref_data, use_container_width=True)
