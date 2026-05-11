import streamlit as st
import pandas as pd
from core.excel_manager import ExcelManager
from core.field_rules import FieldRulesStore

DATA_DIR = "data/scans"


def render(url: str):
    excel_manager = ExcelManager(data_dir=DATA_DIR)
    rules_store = FieldRulesStore(data_dir=DATA_DIR)
    elements = excel_manager.read_element_map(url)
    if not elements:
        st.info("No elements scanned for this URL.")
        return

    st.caption(f"{len(elements)} elements on {url}")
    rows = [{
        "S.No": e["sno"], "Field": e["element_name"], "Type": e["element_type"],
        "Available Options": e.get("available_options", ""),
        "Per-field rule": rules_store.read(url).get(e["element_name"], ""),
    } for e in elements if e["element_type"] != "button"]
    df = pd.DataFrame(rows)
    edited = st.data_editor(
        df, use_container_width=True,
        disabled=["S.No", "Field", "Type", "Available Options"],
        key=f"fields_editor_{url}",
    )
    if st.button("Save per-field rules", key=f"save_rules_{url}"):
        new_rules = {
            r["Field"]: (r["Per-field rule"] or "").strip()
            for _, r in edited.iterrows()
            if (r["Per-field rule"] or "").strip()
        }
        rules_store.save(url, new_rules)
        st.success("Saved.")
        st.rerun()
