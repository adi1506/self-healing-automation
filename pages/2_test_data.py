import streamlit as st
import pandas as pd
from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator
from core.ai_test_data import AITestData
from core.field_rules import FieldRulesStore

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
    excel_path = excel_manager.get_excel_path(url)
    with open(excel_path, "rb") as f:
        st.download_button(
            label="Download Excel",
            data=f.read(),
            file_name=f"scan_{excel_manager.sanitize_url(url)}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    element_map = excel_manager.read_element_map(url)
    editable_names = [
        e["element_name"] for e in element_map
        if e["element_type"] not in ("button",)
    ]

    test_data = excel_manager.read_test_data(url)

    columns = ["S.No", "Test Case Name", "AI Context"] + editable_names
    if test_data:
        rows = []
        for td in test_data:
            row = {
                "S.No": td.get("S.No", ""),
                "Test Case Name": td.get("Test Case Name", ""),
                "AI Context": td.get("AI Context", ""),
            }
            for name in editable_names:
                row[name] = td.get(name, "")
            rows.append(row)
        df = pd.DataFrame(rows, columns=columns)
    else:
        df = pd.DataFrame([{col: "" for col in columns}], columns=columns)
        df["S.No"] = 1

    rules_store = FieldRulesStore(data_dir=DATA_DIR)
    field_rules = rules_store.read(url)
    page_context = excel_manager.read_page_context(url)

    col_btn, col_toggle = st.columns([1, 2])
    with col_btn:
        do_generate = st.button("AI Generate Test Cases", type="secondary")
    with col_toggle:
        compact = st.checkbox("Compact negatives (one per field)", value=True)
        overwrite = st.checkbox("Overwrite existing values", value=False)

    if do_generate:
        ai = AITestData()
        generator = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        # Pull AI context from any rows the user already typed
        existing_rows = excel_manager.read_test_data(url) or []
        ai_contexts_by_row = {
            i: (r.get("AI Context") or "") for i, r in enumerate(existing_rows)
        }

        rows = generator.generate(
            fields=element_map,
            page_context=page_context,
            mode="compact" if compact else "thorough",
            per_field_rules=field_rules,
            ai_contexts_by_row=ai_contexts_by_row,
        )

        # Merge with existing user-entered values unless overwrite is checked
        save_rows = []
        for i, generated in enumerate(rows):
            existing = existing_rows[i] if i < len(existing_rows) else {}
            row_dict = {
                "sno": i + 1,
                "test_case_name": existing.get("Test Case Name") or generated["test_case_name"],
                "ai_context": existing.get("AI Context") or generated["ai_context"],
            }
            for name in editable_names:
                user_val = (existing.get(name) or "").strip()
                gen_val = generated["values"].get(name, "")
                if overwrite or not user_val:
                    row_dict[name] = gen_val
                else:
                    row_dict[name] = user_val
            save_rows.append(row_dict)

        excel_manager.save_test_data(url, save_rows)
        st.success(f"Generated {len(save_rows)} test cases.")
        st.rerun()

        if not ai.is_available():
            st.info("Ollama not reachable — used heuristic generation only. "
                    "AI Context columns were ignored. "
                    "Start `ollama serve` to enable AI enrichment.")

    st.divider()
    st.caption("Regenerate one row using its current AI Context (preserves manual values).")
    col_pick, col_regen = st.columns([1, 1])
    with col_pick:
        existing_rows = excel_manager.read_test_data(url) or []
        row_options = [f"Row {i+1}: {r.get('Test Case Name', '(unnamed)')}"
                       for i, r in enumerate(existing_rows)]
        chosen = st.selectbox("Row to regenerate", options=row_options) if row_options else None
    with col_regen:
        do_regen = st.button("🔄 Regenerate this row", disabled=not row_options)

    if do_regen and chosen:
        row_idx = row_options.index(chosen)
        ai = AITestData()
        generator = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        target = existing_rows[row_idx]
        ai_ctx = target.get("AI Context", "")
        # Resolve a value per editable field, preserving manual entries
        new_row = dict(target)
        for f in element_map:
            if f.get("element_type") == "button":
                continue
            name = f["element_name"]
            existing_val = (target.get(name) or "").strip()
            if existing_val:
                continue  # preserve manual value
            new_row[name] = generator._resolve_value(
                field=f, page_context=page_context,
                per_field_rule=field_rules.get(name, ""), ai_context=ai_ctx,
            )

        # Save back: rebuild full save_rows list, replacing only this index
        save_rows = []
        for i, r in enumerate(existing_rows):
            source = new_row if i == row_idx else r
            save_rows.append({
                "sno": i + 1,
                "test_case_name": source.get("Test Case Name", ""),
                "ai_context": source.get("AI Context", ""),
                **{name: source.get(name, "") for name in editable_names},
            })
        excel_manager.save_test_data(url, save_rows)
        st.success(f"Regenerated row {row_idx + 1}.")
        st.rerun()

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
            row_dict = {
                "sno": idx + 1,
                "test_case_name": row.get("Test Case Name", ""),
                "ai_context": row.get("AI Context", ""),
            }
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
