import streamlit as st
import pandas as pd
from core.dataset_io import parse_csv_bytes, parse_xlsx_bytes, dataset_to_xlsx_bytes
from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator
from core.field_rules import FieldRulesStore
from core.runner_utils import is_blank_dataset_row

DATA_SCANS = "data/scans"


def _editable_fields(sc) -> list[str]:
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    return [e["element_name"] for e in elements if e["element_type"] != "button"]


def render(sc, on_save):
    st.caption(
        "ⓘ **Test data** = the values each run feeds into the form fields. "
        "One row = one execution. Each row has a **Test name** that the run "
        "history will refer to. Steps describe *what* to do; test data is the "
        "*values* used. Generate with regex for speed, or with AI for "
        "realistic / scenario-specific rows."
    )
    # When the view is bound to a multi-page page entry, the id has a __p<n>
    # suffix added by _PageView. That's our signal to surface the caveat.
    if "__p" in sc.id:
        st.caption(
            "⚠ This is a **multi-page** scenario. Only the **first non-blank "
            "row** of each page's dataset is used during a run. Additional "
            "rows are kept for authoring/testing convenience."
        )

    fields = _editable_fields(sc)
    if not fields and not sc.dataset:
        st.info("No fields available. Set a base URL with a scanned page in Settings, "
                "or upload a CSV/XLSX to define dataset columns.")
        return

    c1, c2, c3, c4, c5 = st.columns([1.2, 1.4, 1.2, 1.2, 1])
    if c1.button("⚡ Generate via regex", key=f"regex_data_{sc.id}",
                 help="Fast, deterministic generation from field patterns / "
                      "autocomplete hints. No AI involved. Appends new rows; "
                      "skips ones that duplicate existing rows."):
        _generate_via_regex(sc, fields, on_save)

    if c2.button("✨ Generate / Refine with AI", key=f"ai_toggle_{sc.id}",
                 help="Open the AI assist panel to add or refine rows using "
                      "natural-language context."):
        st.session_state[f"ai_panel_open_{sc.id}"] = not st.session_state.get(
            f"ai_panel_open_{sc.id}", False
        )

    upload = c3.file_uploader("⬆ Upload CSV/XLSX", type=["csv", "xlsx"], key=f"up_{sc.id}",
                              label_visibility="collapsed")
    if upload is not None:
        blob = upload.read()
        rows = parse_xlsx_bytes(blob) if upload.name.endswith(".xlsx") else parse_csv_bytes(blob)
        if rows:
            on_save(rows)
            st.success(f"Imported {len(rows)} rows.")
            st.rerun()

    if sc.dataset:
        c4.download_button(
            "⬇ Download Excel",
            data=dataset_to_xlsx_bytes(sc.dataset),
            file_name=f"{sc.id}_dataset.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_data_{sc.id}",
        )
    if c5.button("+ Add empty row", key=f"add_{sc.id}"):
        on_save(sc.dataset + [{"__test_name": "", "__expected_outcome": "success",
                                **{f: "" for f in fields}}])
        st.rerun()

    # AI panel goes ABOVE the editor — that way the user composes intent
    # first, sees the new rows appear in the table below, and edits inline.
    if st.session_state.get(f"ai_panel_open_{sc.id}", False):
        _render_ai_assist_panel(sc, on_save)

    df = _dataset_to_dataframe(sc.dataset, fields)
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key=f"data_editor_{sc.id}",
        column_order=["__test_name", "__expected_outcome", *fields],
        column_config={
            "__test_name": st.column_config.TextColumn(
                "Test name",
                help="Label the run history will use to refer to this case. "
                     "Auto-filled by regex/AI generation; you can edit freely.",
            ),
            "__expected_outcome": st.column_config.SelectboxColumn(
                "Expected outcome",
                options=["success", "failure"],
                help="What this row should do: `success` = the form should "
                     "accept these values; `failure` = the form should reject "
                     "them. Drives the PASS/FAIL classification in run "
                     "history. Blank is treated as `success`.",
            ),
        },
    )
    if st.button("Save dataset", type="primary", key=f"save_data_{sc.id}"):
        records = [r for r in edited.to_dict(orient="records")
                   if not is_blank_dataset_row(r)]
        on_save(records)
        st.success("Dataset saved.")

    st.caption(f"Scenario will run {len(sc.dataset) or 1} time(s) when executed."
               + (" Empty dataset = single run with literal step values." if not sc.dataset else ""))


def _dataset_to_dataframe(dataset, fields):
    """Build a DataFrame for the editor that always exposes __test_name +
    every field column, even when the dataset is empty or rows are partial.
    """
    columns = ["__test_name", "__expected_outcome", *fields]
    if not dataset:
        df = pd.DataFrame({c: pd.Series(dtype="string") for c in columns})
    else:
        base = pd.DataFrame(dataset)
        extra = [c for c in base.columns if c not in columns]
        df = base.reindex(columns=columns + extra)
    # Coerce every column to string — reindex fills missing columns with NaN
    # which pandas types as float64, and st.data_editor's TextColumn rejects
    # FLOAT-backed data. fillna("") keeps blanks visually empty.
    return df.fillna("").astype(str)


def _row_signature(row: dict, fields: list[str]) -> tuple:
    """Stable comparable signature for a dataset row.

    Used to dedupe regex-generated rows against rows already present in the
    dataset. Compares only the field values + expected_outcome — the test
    name is ignored so renaming a row doesn't unblock a duplicate.
    """
    return tuple(str(row.get(f, "")).strip() for f in fields) + (
        str(row.get("__expected_outcome", "")).strip(),
    )


def _generate_via_regex(sc, fields, on_save):
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url)
    page_context = em.read_page_context(sc.base_url)
    rules = FieldRulesStore(data_dir=DATA_SCANS).read(sc.base_url)
    # ai_client=None keeps this purely regex/heuristic — fast, deterministic,
    # offline. The AI path lives in the separate AI assist panel.
    gen = TestCaseGenerator(
        field_dictionary_path="data/field_dictionary.yaml",
        ai_client=None,
    )
    generated = gen.generate(
        fields=elements, page_context=page_context, mode="compact",
        per_field_rules=rules, ai_contexts_by_row={},
        variants_per_field=None,
    )

    existing = list(sc.dataset or [])
    seen = {_row_signature(r, fields) for r in existing}
    existing_names = {str(r.get("__test_name", "")).strip()
                      for r in existing if r.get("__test_name")}

    appended = 0
    skipped_dup = 0
    next_num = len(existing) + 1
    for r in generated:
        rec = {name: r["values"].get(name, "") for name in fields}
        rec["__expected_outcome"] = r["expected_outcome"]
        # Regex rows get simple sequential names (test1, test2, ...) numbered
        # from the current dataset size. _unique_name handles collisions if
        # the user has already named existing rows "test{N}".
        rec["__test_name"] = _unique_name(f"test{next_num}", existing_names)
        next_num += 1
        sig = _row_signature(rec, fields)
        if sig in seen:
            skipped_dup += 1
            continue
        seen.add(sig)
        existing_names.add(rec["__test_name"])
        existing.append(rec)
        appended += 1

    on_save(existing)
    if appended:
        msg = f"Added {appended} regex row(s)."
        if skipped_dup:
            msg += f" Skipped {skipped_dup} duplicate(s)."
        st.success(msg)
    else:
        st.info("No new rows added — every generated row duplicates an "
                "existing one.")
    st.rerun()


def _unique_name(base: str, taken: set[str]) -> str:
    """Return `base`, or `base (2)`, `base (3)`, ... so test names stay unique."""
    base = (base or "Regex case").strip() or "Regex case"
    if base not in taken:
        return base
    i = 2
    while f"{base} ({i})" in taken:
        i += 1
    return f"{base} ({i})"


def _render_ai_assist_panel(sc, on_save):
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.container(border=True):
        st.markdown("**✨ AI assist**")
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")
            return

        modes = ["Add rows"]
        if sc.dataset:
            modes.append("Refine a row")
        mode = st.radio(
            "What do you want to do?", options=modes, horizontal=True,
            key=f"ai_assist_mode_{sc.id}", label_visibility="collapsed",
        )

        if mode == "Add rows":
            _render_add_rows_controls(sc, on_save, svc)
        else:
            _render_refine_controls(sc, on_save, svc)


def _render_add_rows_controls(sc, on_save, svc):
    ca, cb, cc = st.columns([1, 4, 1])
    n_rows = ca.number_input("Count", min_value=1, max_value=8, value=3,
                              key=f"add_n_{sc.id}")
    batch_ctx = cb.text_input(
        "Context for the new rows", key=f"add_ctx_{sc.id}",
        placeholder="e.g. international customers from EU",
    )
    if cc.button("Generate", key=f"add_btn_{sc.id}"):
        em = ExcelManager(data_dir=DATA_SCANS)
        elements = em.read_element_map(sc.base_url)
        with st.spinner(f"Generating {int(n_rows)} rows…"):
            new_rows = svc.generate_complementary_rows(
                elements, list(sc.dataset), batch_ctx, int(n_rows),
            )
        if not new_rows:
            st.warning("No rows generated.")
            if svc.last_error:
                st.caption(f"Last error: {svc.last_error}")
            if svc.last_latency_ms is not None:
                st.caption(f"Last call took {svc.last_latency_ms:.0f} ms.")
        else:
            existing = list(sc.dataset or [])
            taken = {str(r.get("__test_name", "")).strip()
                     for r in existing if r.get("__test_name")}
            fallback_base = (batch_ctx.strip() or "AI case")
            for r in new_rows:
                # Prefer the model's per-row name; fall back to the batch
                # context label so we still get something readable when the
                # model omits it.
                ai_name = str(r.pop("__test_name", "")).strip()
                name = _unique_name(ai_name or fallback_base, taken)
                taken.add(name)
                existing.append({
                    "__test_name": name,
                    "__expected_outcome": "success",
                    **r,
                })
            on_save(existing)
            st.success(f"Added {len(new_rows)} rows.")
            st.rerun()


def _render_refine_controls(sc, on_save, svc):
    row_options = list(range(1, len(sc.dataset) + 1))
    row_idx = st.selectbox(
        "Row #", options=row_options, index=0,
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
