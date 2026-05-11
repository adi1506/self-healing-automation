import streamlit as st
import pandas as pd
from core.dataset_io import parse_csv_bytes, parse_xlsx_bytes, dataset_to_xlsx_bytes
from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator
from core.ai_test_data import AITestData
from core.field_rules import FieldRulesStore

DATA_SCANS = "data/scans"


def _editable_fields(sc) -> list[str]:
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    return [e["element_name"] for e in elements if e["element_type"] != "button"]


def render(sc, on_save):
    fields = _editable_fields(sc)
    if not fields and not sc.dataset:
        st.info("No fields available. Set a base URL with a scanned page in Settings, "
                "or upload a CSV/XLSX to define dataset columns.")
        return

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("✨ Generate with AI", key=f"ai_data_{sc.id}"):
        em = ExcelManager(data_dir=DATA_SCANS)
        elements = em.read_element_map(sc.base_url)
        page_context = em.read_page_context(sc.base_url)
        rules = FieldRulesStore(data_dir=DATA_SCANS).read(sc.base_url)
        variants = st.session_state.get(f"variants_{sc.id}", 1)
        ai = AITestData()
        gen = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        mode = "compact" if variants <= 1 else "thorough"
        new_rows = gen.generate(
            fields=elements, page_context=page_context, mode=mode,
            per_field_rules=rules, ai_contexts_by_row={},
        )
        dataset = []
        for r in new_rows:
            rec = {name: r["values"].get(name, "") for name in fields}
            rec["__expected_outcome"] = r["expected_outcome"]
            dataset.append(rec)
        on_save(dataset)
        st.success(f"Generated {len(dataset)} rows.")
        st.rerun()

    upload = c2.file_uploader("⬆ Upload CSV/XLSX", type=["csv", "xlsx"], key=f"up_{sc.id}",
                              label_visibility="collapsed")
    if upload is not None:
        blob = upload.read()
        rows = parse_xlsx_bytes(blob) if upload.name.endswith(".xlsx") else parse_csv_bytes(blob)
        if rows:
            on_save(rows)
            st.success(f"Imported {len(rows)} rows.")
            st.rerun()

    if sc.dataset:
        c3.download_button(
            "⬇ Download Excel",
            data=dataset_to_xlsx_bytes(sc.dataset),
            file_name=f"{sc.id}_dataset.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_data_{sc.id}",
        )
    if c4.button("+ Add empty row", key=f"add_{sc.id}"):
        on_save(sc.dataset + [{f: "" for f in fields}])
        st.rerun()

    st.number_input("Variants per field (more = aggressive negatives)",
                    min_value=1, max_value=10, value=1,
                    key=f"variants_{sc.id}")

    df = pd.DataFrame(sc.dataset) if sc.dataset else pd.DataFrame(columns=fields)
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True,
                            key=f"data_editor_{sc.id}")
    if st.button("Save dataset", type="primary", key=f"save_data_{sc.id}"):
        on_save(edited.to_dict(orient="records"))
        st.success("Dataset saved.")

    st.caption(f"Scenario will run {len(sc.dataset) or 1} time(s) when executed."
               + (" Empty dataset = single run with literal step values." if not sc.dataset else ""))
