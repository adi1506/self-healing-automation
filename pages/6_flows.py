import os
import asyncio
import streamlit as st
import pandas as pd
import yaml
from playwright.async_api import async_playwright

from core.excel_manager import ExcelManager
from core.site_manager import SiteManager
from core.ai_matcher import AIMatcher
from core.recipes import (
    save_recipe, load_recipe, save_flow, load_flow,
    RecipeExecutor, RecipeValidationError, VALID_ACTIONS,
)
from core.scanner import _run_async

st.set_page_config(page_title="Flows", layout="wide")
st.title("Flows & Recipes")

DATA_SCANS = "data/scans"
DATA_SITES = "data/sites"
DATA_RECIPES = "data/recipes"
DATA_FLOWS = "data/flows"
os.makedirs(DATA_RECIPES, exist_ok=True)
os.makedirs(DATA_FLOWS, exist_ok=True)

excel_manager = ExcelManager(data_dir=DATA_SCANS)
site_manager = SiteManager(data_dir=DATA_SITES)

if "draft_steps" not in st.session_state:
    st.session_state.draft_steps = []
if "tested_ok" not in st.session_state:
    st.session_state.tested_ok = False

tab_recipes, tab_flows, tab_run = st.tabs(["Recipes", "Flows", "Run"])


# ---------------- RECIPES TAB ----------------
with tab_recipes:
    st.subheader("Author a recipe")

    sites = site_manager.list_sites()
    all_pages = []
    if sites:
        site = st.selectbox("Site", sites, key="recipe_site")
        all_pages = site_manager.get_site_pages(site)
    if not all_pages:
        all_pages = excel_manager.list_scanned_urls()

    if not all_pages:
        st.info("Scan a page or crawl a site first.")
    else:
        page_url = st.selectbox("Page", all_pages, key="recipe_page")
        elements = excel_manager.read_element_map(page_url)
        st.caption(f"{len(elements)} elements available on this page")

        recipe_name = st.text_input("Recipe name", key="recipe_name", placeholder="login_valid")
        goal = st.text_input("Goal", key="recipe_goal", placeholder="log in successfully")
        outcome = st.selectbox("Expected outcome", ["success", "failure"], key="recipe_outcome")

        col_ai, col_blank = st.columns(2)
        with col_ai:
            if st.button("Suggest with AI"):
                ollama_host = os.environ.get("OLLAMA_HOST", "")
                ollama_model = os.environ.get("OLLAMA_MODEL", "mistral")
                matcher = AIMatcher(host=ollama_host, model=ollama_model)
                suggestion = matcher.suggest_recipe(page_url, elements, goal or "complete the form")
                if suggestion is None:
                    st.error("Ollama unavailable or returned an unparseable response.")
                else:
                    st.session_state.draft_steps = suggestion["steps"]
                    st.session_state.tested_ok = False
                    st.success(f"Drafted {len(suggestion['steps'])} steps. Review below.")
        with col_blank:
            if st.button("Start blank"):
                st.session_state.draft_steps = []
                st.session_state.tested_ok = False

        st.markdown("**Steps**")
        target_options = [e["element_name"] for e in elements]
        edited = st.data_editor(
            pd.DataFrame(st.session_state.draft_steps or [{"action": "fill", "target": "", "value": ""}]),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "action": st.column_config.SelectboxColumn(options=sorted(VALID_ACTIONS)),
                "target": st.column_config.SelectboxColumn(options=[""] + target_options),
            },
            key="step_editor",
        )
        st.session_state.draft_steps = edited.to_dict(orient="records")

        st.markdown("**Assertions** (optional)")
        assertions_df = st.data_editor(
            pd.DataFrame([{"type": "url_contains", "value": "", "selector": ""}]),
            num_rows="dynamic", use_container_width=True,
            column_config={
                "type": st.column_config.SelectboxColumn(
                    options=["url_contains", "element_visible", "element_contains_text"]
                ),
            },
            key="assertion_editor",
        )

        if st.button("Test live (visible browser)"):
            recipe = {
                "name": recipe_name or "draft",
                "goal": goal,
                "start_url": page_url,
                "steps": [s for s in st.session_state.draft_steps if s.get("action")],
                "assertions": [
                    {k: v for k, v in a.items() if v} for a in assertions_df.to_dict(orient="records")
                    if a.get("type") and (a.get("value") or a.get("selector"))
                ],
                "expected_outcome": outcome,
            }
            try:
                async def _run():
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=False)
                        page = await browser.new_page()
                        await page.goto(page_url)
                        executor = RecipeExecutor(elements_by_page={page_url: elements})
                        result = await executor.execute(page, recipe)
                        await page.wait_for_timeout(1500)
                        await browser.close()
                        return result
                result = _run_async(_run())
                for s, r in zip(recipe["steps"], result["step_results"]):
                    icon = "PASS" if r["status"] == "PASS" else "FAIL"
                    err = f" — {r['error']}" if r["error"] else ""
                    st.text(f"[{icon}] {s.get('action')} {s.get('target', '')}{err}")
                for a, ar in zip(recipe["assertions"], result["assertion_results"]):
                    icon = "PASS" if ar["status"] == "PASS" else "FAIL"
                    st.text(f"[{icon}] assert {a.get('type')} — {ar['detail']}")
                st.info(
                    f"Outcome match: {result['outcome_match']} "
                    f"(expected={result['expected_outcome']}, actual={result['actual_outcome']})"
                )
                st.session_state.tested_ok = result["outcome_match"]
            except RecipeValidationError as exc:
                st.error(f"Validation error: {exc}")
            except Exception as exc:
                st.error(f"Run failed: {exc}")

        save_disabled = not (recipe_name and st.session_state.tested_ok)
        if st.button("Save recipe", type="primary", disabled=save_disabled):
            recipe = {
                "name": recipe_name,
                "goal": goal,
                "start_url": page_url,
                "steps": [s for s in st.session_state.draft_steps if s.get("action")],
                "assertions": [
                    {k: v for k, v in a.items() if v} for a in assertions_df.to_dict(orient="records")
                    if a.get("type") and (a.get("value") or a.get("selector"))
                ],
                "expected_outcome": outcome,
            }
            try:
                save_recipe(os.path.join(DATA_RECIPES, f"{recipe_name}.yaml"), recipe)
                st.success(f"Saved {recipe_name}.yaml")
            except RecipeValidationError as exc:
                st.error(f"Validation error: {exc}")

    st.divider()
    st.subheader("Saved recipes")
    if os.path.isdir(DATA_RECIPES):
        for fname in sorted(os.listdir(DATA_RECIPES)):
            if not fname.endswith(".yaml"):
                continue
            with st.expander(fname):
                with open(os.path.join(DATA_RECIPES, fname), encoding="utf-8") as f:
                    st.code(f.read(), language="yaml")


# ---------------- FLOWS TAB ----------------
with tab_flows:
    st.subheader("Build a flow")
    available_recipes = [
        f[:-5] for f in os.listdir(DATA_RECIPES) if f.endswith(".yaml")
    ] if os.path.isdir(DATA_RECIPES) else []

    if not available_recipes:
        st.info("Save some recipes first.")
    else:
        flow_name = st.text_input("Flow name", placeholder="full_login_journey")
        chosen = st.multiselect("Recipes (in order)", options=available_recipes)
        flow_outcome = st.selectbox("Expected overall outcome", ["success", "failure"], key="flow_outcome")

        if st.button("Save flow", type="primary", disabled=not (flow_name and chosen)):
            flow = {
                "name": flow_name,
                "recipes": chosen,
                "expected_outcome": flow_outcome,
            }
            try:
                save_flow(os.path.join(DATA_FLOWS, f"{flow_name}.yaml"), flow)
                st.success(f"Saved {flow_name}.yaml")
            except RecipeValidationError as exc:
                st.error(f"Validation error: {exc}")

    st.divider()
    st.subheader("Saved flows")
    if os.path.isdir(DATA_FLOWS):
        for fname in sorted(os.listdir(DATA_FLOWS)):
            if not fname.endswith(".yaml"):
                continue
            with st.expander(fname):
                with open(os.path.join(DATA_FLOWS, fname), encoding="utf-8") as f:
                    st.code(f.read(), language="yaml")


# ---------------- RUN TAB ----------------
with tab_run:
    st.subheader("Run a flow")
    flow_files = [
        f for f in os.listdir(DATA_FLOWS) if f.endswith(".yaml")
    ] if os.path.isdir(DATA_FLOWS) else []

    if not flow_files:
        st.info("Save a flow first.")
    else:
        choice = st.selectbox("Flow", flow_files)
        flow = load_flow(os.path.join(DATA_FLOWS, choice))

        st.markdown(f"**Recipes:** {' → '.join(flow['recipes'])}")

        recipe_objs = []
        user_fills_keys = []
        for rname in flow["recipes"]:
            r = load_recipe(os.path.join(DATA_RECIPES, f"{rname}.yaml"))
            recipe_objs.append(r)
            for i, step in enumerate(r["steps"]):
                if step.get("value") == "<USER_FILLS>":
                    user_fills_keys.append((rname, i, step.get("target")))

        overrides = {}
        if user_fills_keys:
            st.markdown("**Provide sensitive values**")
            for rname, i, target in user_fills_keys:
                k = f"{rname}__{i}__{target}"
                overrides[k] = st.text_input(f"{rname} → {target}", type="password", key=k)

        if st.button("Run flow", type="primary"):
            for rname, i, _ in user_fills_keys:
                key = f"{rname}__{i}__{recipe_objs[flow['recipes'].index(rname)]['steps'][i].get('target')}"
                if not overrides.get(key):
                    st.error(f"Missing value for {rname} step {i}")
                    st.stop()
                recipe_objs[flow["recipes"].index(rname)]["steps"][i]["value"] = overrides[key]

            elements_by_page = {}
            for r in recipe_objs:
                if r["start_url"] not in elements_by_page:
                    elements_by_page[r["start_url"]] = excel_manager.read_element_map(r["start_url"])

            async def _run_flow():
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()
                    executor = RecipeExecutor(elements_by_page=elements_by_page)
                    per_recipe = []
                    for r in recipe_objs:
                        await page.goto(r["start_url"])
                        result = await executor.execute(page, r)
                        per_recipe.append((r["name"], result))
                    await browser.close()
                    return per_recipe

            with st.spinner("Running flow..."):
                per_recipe = _run_async(_run_flow())

            overall_actual = "success"
            for rname, res in per_recipe:
                st.markdown(f"### {rname} — actual: `{res['actual_outcome']}`, match: `{res['outcome_match']}`")
                for s, sr in zip(load_recipe(os.path.join(DATA_RECIPES, f"{rname}.yaml"))["steps"], res["step_results"]):
                    icon = "PASS" if sr["status"] == "PASS" else "FAIL"
                    err = f" — {sr['error']}" if sr["error"] else ""
                    st.text(f"[{icon}] {s.get('action')} {s.get('target', '')}{err}")
                if not res["outcome_match"]:
                    overall_actual = "failure"

            if overall_actual == flow["expected_outcome"]:
                st.success(f"Flow PASSED (expected {flow['expected_outcome']}, got {overall_actual})")
            else:
                st.error(f"Flow FAILED (expected {flow['expected_outcome']}, got {overall_actual})")
