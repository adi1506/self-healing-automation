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
            variants_per_field=variants if variants > 1 else None,
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

    if sc.dataset:
        _render_refine_row_panel(sc, on_save)

    _render_append_rows_panel(sc, on_save)


def _render_append_rows_panel(sc, on_save):
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.container(border=True):
        st.markdown("**Add AI rows**")
        ca, cb, cc = st.columns([1, 4, 1])
        n_rows = ca.number_input("Count", min_value=1, max_value=8, value=3,
                                  key=f"add_n_{sc.id}", disabled=not ai_ok)
        batch_ctx = cb.text_input(
            "Context for the new rows", key=f"add_ctx_{sc.id}",
            placeholder="e.g. international customers from EU",
            disabled=not ai_ok,
        )
        if cc.button("Generate", key=f"add_btn_{sc.id}", disabled=not ai_ok):
            em = ExcelManager(data_dir=DATA_SCANS)
            elements = em.read_element_map(sc.base_url)
            with st.spinner(f"Generating {int(n_rows)} rows…"):
                new_rows = svc.generate_complementary_rows(
                    elements, list(sc.dataset), batch_ctx, int(n_rows),
                )
            if not new_rows:
                st.warning("No rows generated.")
            else:
                merged = list(sc.dataset) + [
                    {**r, "__expected_outcome": "success"} for r in new_rows
                ]
                on_save(merged)
                st.success(f"Added {len(new_rows)} rows.")
                st.rerun()
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")


def _render_refine_row_panel(sc, on_save):
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.container(border=True):
        st.markdown("**✏️ Refine a row with AI**")
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")
            return
        row_idx = st.number_input(
            "Row #", min_value=1, max_value=len(sc.dataset), value=1,
            key=f"refine_idx_{sc.id}",
        ) - 1
        refine_text = st.text_input(
            "Instruction", key=f"refine_text_{sc.id}",
            placeholder="e.g. change to a Bangalore customer with a Gmail address",
        )
        colp, cola, cold = st.columns([1, 1, 1])
        if colp.button("Preview", key=f"refine_preview_{sc.id}"):
            em = ExcelManager(data_dir=DATA_SCANS)
            elements = em.read_element_map(sc.base_url)
            with st.spinner("Asking the model to refine the row…"):
                proposed = svc.refine_row(elements, sc.dataset[row_idx], refine_text)
            st.session_state[f"refine_proposed_{sc.id}"] = proposed
            st.session_state[f"refine_proposed_idx_{sc.id}"] = row_idx

        proposed = st.session_state.get(f"refine_proposed_{sc.id}")
        proposed_idx = st.session_state.get(f"refine_proposed_idx_{sc.id}")
        if proposed is not None and proposed_idx is not None:
            current = sc.dataset[proposed_idx]
            diff_rows = []
            for k, new_v in proposed.items():
                old_v = current.get(k, "")
                if old_v != new_v:
                    diff_rows.append({"Field": k, "Current": old_v, "New": new_v})
            if diff_rows:
                st.dataframe(pd.DataFrame(diff_rows), hide_index=True,
                             use_container_width=True)
                if cola.button("Apply", key=f"refine_apply_{sc.id}"):
                    new_dataset = list(sc.dataset)
                    merged = dict(current)
                    merged.update(proposed)
                    new_dataset[proposed_idx] = merged
                    on_save(new_dataset)
                    st.session_state.pop(f"refine_proposed_{sc.id}", None)
                    st.session_state.pop(f"refine_proposed_idx_{sc.id}", None)
                    st.rerun()
            else:
                st.info("No changes proposed.")
            if cold.button("Cancel", key=f"refine_cancel_{sc.id}"):
                st.session_state.pop(f"refine_proposed_{sc.id}", None)
                st.session_state.pop(f"refine_proposed_idx_{sc.id}", None)
                st.rerun()
