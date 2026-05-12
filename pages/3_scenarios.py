import os
import re
import streamlit as st
from datetime import datetime
from core.scenarios import Scenario, save_scenario
from ui.scenarios.list import render as render_list
from ui.scenarios.detail import render as render_detail
from core.excel_manager import ExcelManager

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"

st.set_page_config(page_title="Scenarios", layout="wide")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "scenario"


def _unique_slug(base: str) -> str:
    existing = {f[:-5] for f in os.listdir(DATA_SCENARIOS)
                if f.endswith(".yaml")} if os.path.isdir(DATA_SCENARIOS) else set()
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _create_scenario_from_suggestion(name: str, base_url: str, ai_context: str) -> str:
    sid = _unique_slug(_slugify(name))
    sc = Scenario(
        id=sid, name=name, kind="single-page", base_url=base_url,
        steps=[{"action": "fill", "target": "", "value": ""}],
        dataset=[{"__ai_context": ai_context, "__expected_outcome": "success"}],
        expected_outcome="success",
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    save_scenario(DATA_SCENARIOS, sc)
    return sid


open_id = st.session_state.get("_open_scenario")

if open_id == "__new__":
    st.title("New scenario")
    em = ExcelManager(data_dir=DATA_SCANS)
    urls = [""] + em.list_scanned_urls()
    name = st.text_input("Name", placeholder="Login valid")
    base_url = st.selectbox("Base URL (scanned page)", urls)

    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.expander("✨ Suggest scenarios with AI", expanded=False):
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")
        elif not base_url:
            st.caption("Pick a scanned page above to enable suggestions.")
        else:
            if st.button("Suggest", key="suggest_btn"):
                elements = em.read_element_map(base_url)
                page_ctx = em.read_page_context(base_url) or {}
                page = {
                    "url": base_url,
                    "title": page_ctx.get("title", ""),
                    "elements": elements,
                }
                with st.spinner("Asking the model for scenario ideas…"):
                    st.session_state["scenario_suggestions"] = svc.suggest_scenarios(page)

            suggestions = st.session_state.get("scenario_suggestions") or []
            for i, s in enumerate(suggestions):
                with st.container(border=True):
                    st.markdown(f"**{s['name']}** — {s['rationale']}")
                    st.caption(f"AI Context: _{s['ai_context']}_")
                    if st.button("Add as scenario", key=f"add_sugg_{i}"):
                        sid = _create_scenario_from_suggestion(
                            name=s["name"], base_url=base_url,
                            ai_context=s["ai_context"],
                        )
                        st.session_state["_open_scenario"] = sid
                        st.session_state.pop("scenario_suggestions", None)
                        st.rerun()

    if st.button("Create", type="primary", disabled=not name):
        sid = _unique_slug(_slugify(name))
        sc = Scenario(
            id=sid, name=name, kind="single-page", base_url=base_url,
            steps=[{"action": "fill", "target": "", "value": ""}],
            dataset=[], expected_outcome="success",
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        save_scenario(DATA_SCENARIOS, sc)
        st.session_state["_open_scenario"] = sid
        st.rerun()
    if st.button("Cancel"):
        st.session_state.pop("_open_scenario", None)
        st.rerun()
elif open_id:
    render_detail(open_id)
else:
    st.title("Scenarios")
    render_list()
