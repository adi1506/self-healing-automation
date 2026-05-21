import os
import streamlit as st
import pandas as pd
from core.recipes import VALID_ACTIONS
from core.excel_manager import ExcelManager
from core.ai_matcher import AIMatcher
from core.ai_service import get_ai_service

DATA_SCANS = "data/scans"


def render(sc, on_save):
    """sc is a Scenario; on_save(steps_list) persists changes."""
    st.caption(
        "ⓘ **Steps** describe the *actions* the run performs (fill, click, "
        "select, assert). They're the recipe. The actual *values* used for "
        "each run live on the **Dataset** tab — one row = one run."
    )

    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    target_options = [e["element_name"] for e in elements]

    seed_key = f"steps_seed_{sc.id}"
    if seed_key not in st.session_state:
        st.session_state[seed_key] = sc.steps or [{"action": "fill", "target": "", "value": ""}]

    nonce_key = f"steps_nonce_{sc.id}"
    if nonce_key not in st.session_state:
        st.session_state[nonce_key] = 0

    edited = st.data_editor(
        pd.DataFrame(st.session_state[seed_key]),
        num_rows="dynamic", use_container_width=True,
        column_config={
            "action": st.column_config.SelectboxColumn(options=sorted(VALID_ACTIONS)),
            "target": st.column_config.SelectboxColumn(options=[""] + target_options),
        },
        key=f"step_editor_{sc.id}_{st.session_state[nonce_key]}",
    )

    c1, c2 = st.columns([1, 1])
    if c1.button("✨ Suggest with AI", key=f"ai_steps_{sc.id}"):
        if not sc.base_url:
            st.error("Set a base URL in Settings first.")
        else:
            matcher = AIMatcher()
            svc = get_ai_service()
            goal = st.session_state.get(f"goal_{sc.id}", "complete the form")
            with st.spinner("Asking the model to draft steps…"):
                suggestion = matcher.suggest_recipe(sc.base_url, elements, goal)
            if suggestion is None:
                st.error("Ollama unavailable or returned an unparseable response.")
                if svc.last_error:
                    st.caption(f"Last error: {svc.last_error}")
            else:
                st.session_state[seed_key] = suggestion["steps"] or st.session_state[seed_key]
                st.session_state[nonce_key] += 1
                st.success(f"Drafted {len(suggestion['steps'])} steps.")
                st.rerun()

    if c2.button("Save steps", type="primary", key=f"save_steps_{sc.id}"):
        new_steps = [s for s in edited.to_dict(orient="records") if s.get("action")]
        on_save(new_steps)
        st.session_state[seed_key] = new_steps
        st.success("Steps saved.")
        # Synergy nudge: steps describe *what* to do, but a run also needs the
        # *values*. If the dataset is empty, point the user at the next step.
        if not sc.dataset and any(
            (s.get("action") or "") in ("fill", "select", "check") for s in new_steps
        ):
            st.info(
                "Next: head to the **Dataset** tab to create the test data "
                "those steps will run against. Each dataset row = one run."
            )
