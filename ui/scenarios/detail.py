import asyncio
import os
import sys
import streamlit as st
from playwright.async_api import async_playwright
from core.scenarios import load_scenario, save_scenario
from core.recipes import RecipeExecutor
from core.scanner import _run_async
from core.excel_manager import ExcelManager
from ui.scenarios.steps_tab import render as render_steps
from ui.scenarios.dataset_tab import render as render_dataset
from ui.scenarios.runs_tab import render as render_runs
from ui.scenarios.settings_tab import render as render_settings

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"


def _save_steps(sc, new_steps):
    sc.steps = new_steps
    save_scenario(DATA_SCENARIOS, sc)


def _save_dataset(sc, rows):
    sc.dataset = rows
    save_scenario(DATA_SCENARIOS, sc)


def _run_scenario(sc):
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    headed_ok = sys.platform != "linux" or bool(os.environ.get("DISPLAY"))
    recipe = {
        "name": sc.name, "start_url": sc.base_url,
        "steps": sc.steps, "assertions": sc.assertions or [],
        "expected_outcome": sc.expected_outcome,
    }

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not headed_ok)
            page = await browser.new_page()
            await page.goto(sc.base_url)
            executor = RecipeExecutor(elements_by_page={sc.base_url: elements})
            result = await executor.execute(page, recipe)
            await browser.close()
            return result

    return _run_async(_run())


def render(scenario_id: str):
    sc = load_scenario(DATA_SCENARIOS, scenario_id)
    c1, c2 = st.columns([4, 1])
    c1.title(sc.name)
    if c2.button("▶ Run now", type="primary", key=f"run_{sc.id}"):
        if sc.kind != "single-page" or not sc.base_url:
            st.error("Multi-page run not wired in this tab yet; use the Run dialog.")
        else:
            with st.spinner("Running..."):
                result = _run_scenario(sc)
            for s, r in zip(sc.steps, result["step_results"]):
                icon = "PASS" if r["status"] == "PASS" else "FAIL"
                err = f" — {r['error']}" if r["error"] else ""
                st.text(f"[{icon}] {s.get('action')} {s.get('target', '')}{err}")
            st.info(f"Outcome match: {result['outcome_match']}")

    if st.button("← Back to list", key=f"back_{sc.id}"):
        st.session_state.pop("_open_scenario", None)
        st.rerun()

    tab1, tab2, tab3, tab4 = st.tabs(["Steps", "Dataset", "Runs", "Settings"])
    with tab1: render_steps(sc, lambda s: _save_steps(sc, s))
    with tab2: render_dataset(sc, lambda d: _save_dataset(sc, d))
    with tab3: render_runs(sc)
    with tab4: render_settings(sc)
