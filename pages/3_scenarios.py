import os
import re
import streamlit as st
from datetime import datetime
from core.scenarios import Scenario, save_scenario
from ui.scenarios.list import render as render_list
from ui.scenarios.detail import render as render_detail
from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator
from core.ai_test_data import AITestData
from core.field_rules import FieldRulesStore

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


def _seed_happy_row(base_url: str) -> list[dict]:
    """Generate a single happy-path row from the scanned page, so new scenarios
    land in a populated editor instead of an empty canvas. Best-effort: returns
    [] if the page hasn't been scanned or generation fails."""
    if not base_url:
        return []
    try:
        em = ExcelManager(data_dir=DATA_SCANS)
        elements = em.read_element_map(base_url)
        if not elements:
            return []
        page_context = em.read_page_context(base_url)
        rules = FieldRulesStore(data_dir=DATA_SCANS).read(base_url)
        ai = AITestData()
        gen = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        rows = gen.generate(
            fields=elements, page_context=page_context, mode="compact",
            per_field_rules=rules,
        )
        if not rows:
            return []
        editable = [e["element_name"] for e in elements if e["element_type"] != "button"]
        happy = rows[0]
        rec = {name: happy["values"].get(name, "") for name in editable}
        rec["__expected_outcome"] = happy["expected_outcome"]
        return [rec]
    except Exception:
        return []


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
    scanned_urls = em.list_scanned_urls()

    kind = st.radio(
        "Kind", options=["single-page", "multi-page"], horizontal=True,
        key="_new_kind",
        help="single-page: one scanned URL, dataset rows iterate. "
             "multi-page: a sequence of scanned URLs walked in one browser "
             "session with explicit transitions between them.",
    )
    name = st.text_input("Name", placeholder="Login valid")

    if kind == "single-page":
        url_options = [""] + scanned_urls
        base_url = st.selectbox("Base URL (scanned page)", url_options)

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
                    page = {"url": base_url, "title": page_ctx.get("title", ""),
                            "elements": elements}
                    with st.spinner("Asking the model for scenario ideas…"):
                        st.session_state["scenario_suggestions"] = svc.suggest_scenarios(page)
                    st.session_state["scenario_suggest_attempted"] = True

                suggestions = st.session_state.get("scenario_suggestions") or []
                if st.session_state.get("scenario_suggest_attempted") and not suggestions:
                    st.warning(
                        "The model didn't return any scenarios. "
                        "Try again, or check Settings to confirm the model is reachable."
                    )
                    if svc.last_error:
                        st.caption(f"Last error: {svc.last_error}")
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
                            st.session_state.pop("scenario_suggest_attempted", None)
                            st.rerun()

        if st.button("Create", type="primary", disabled=not name):
            sid = _unique_slug(_slugify(name))
            sc = Scenario(
                id=sid, name=name, kind="single-page", base_url=base_url,
                steps=[{"action": "fill", "target": "", "value": ""}],
                dataset=_seed_happy_row(base_url), expected_outcome="success",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            save_scenario(DATA_SCENARIOS, sc)
            st.session_state["_open_scenario"] = sid
            st.session_state.pop("scenario_suggestions", None)
            st.session_state.pop("scenario_suggest_attempted", None)
            st.rerun()

    else:  # multi-page
        st.caption(
            "Add scanned pages in the order the user journey visits them. "
            "Transitions between pages are configured per page in the "
            "Settings tab after creation."
        )
        picked = st.session_state.setdefault("_new_mp_pages", [""])

        for i, current in enumerate(picked):
            cols = st.columns([6, 1, 1, 1])
            picked[i] = cols[0].selectbox(
                f"Page {i+1}", options=[""] + scanned_urls,
                index=([""] + scanned_urls).index(current) if current in scanned_urls else 0,
                key=f"_new_mp_page_{i}", label_visibility="collapsed",
            )
            if cols[1].button("↑", key=f"_new_mp_up_{i}", disabled=(i == 0)):
                picked[i - 1], picked[i] = picked[i], picked[i - 1]
                st.rerun()
            if cols[2].button("↓", key=f"_new_mp_dn_{i}", disabled=(i == len(picked) - 1)):
                picked[i], picked[i + 1] = picked[i + 1], picked[i]
                st.rerun()
            if cols[3].button("✕", key=f"_new_mp_rm_{i}", disabled=(len(picked) <= 1)):
                picked.pop(i)
                st.rerun()

        if st.button("+ Add page", key="_new_mp_add"):
            picked.append("")
            st.rerun()

        clean = [u for u in picked if u]
        can_create = bool(name and len(clean) >= 1 and len(set(clean)) == len(clean))
        if not can_create and name and clean:
            st.caption("Each page URL must be unique.")
        if st.button("Create", type="primary", disabled=not can_create):
            sid = _unique_slug(_slugify(name))
            pages_payload = []
            for j, url in enumerate(clean):
                entry = {"base_url": url, "steps": [], "dataset": []}
                if j < len(clean) - 1:
                    entry["transition"] = {
                        "target": "", "wait_for": "url_contains",
                        "value": "", "timeout_ms": 30000,
                    }
                pages_payload.append(entry)
            sc = Scenario(
                id=sid, name=name, kind="multi-page", base_url="",
                steps=[], dataset=[], expected_outcome="success",
                pages=pages_payload,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            save_scenario(DATA_SCENARIOS, sc)
            st.session_state["_open_scenario"] = sid
            st.session_state.pop("_new_mp_pages", None)
            st.rerun()

    if st.button("Cancel"):
        st.session_state.pop("_open_scenario", None)
        st.session_state.pop("scenario_suggestions", None)
        st.session_state.pop("scenario_suggest_attempted", None)
        st.session_state.pop("_new_mp_pages", None)
        st.rerun()
elif open_id:
    render_detail(open_id)
else:
    st.title("Scenarios")
    render_list()
