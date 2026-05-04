import os
import streamlit as st
from datetime import datetime
from core.setter import Setter
from core.healer import Healer
from core.excel_manager import ExcelManager

st.set_page_config(page_title="Runner", layout="wide")
st.title("Run Tests")

DATA_DIR = "data/scans"
SCREENSHOT_DIR = "screenshots"
excel_manager = ExcelManager(data_dir=DATA_DIR)
setter = Setter()

run_mode = st.radio("Run mode", ["Single page", "Flow"], horizontal=True)

if run_mode == "Flow":
    import os as _os
    from playwright.async_api import async_playwright
    from core.recipes import load_recipe, load_flow, RecipeExecutor
    from core.scanner import _run_async as _run_async_flow

    DATA_FLOWS = "data/flows"
    DATA_RECIPES = "data/recipes"

    flow_files = [
        f for f in _os.listdir(DATA_FLOWS) if f.endswith(".yaml")
    ] if _os.path.isdir(DATA_FLOWS) else []

    if not flow_files:
        st.info("No flows yet — create one on the Flows page.")
        st.stop()

    chosen = st.selectbox("Flow", flow_files)
    flow = load_flow(_os.path.join(DATA_FLOWS, chosen))
    recipe_objs = [load_recipe(_os.path.join(DATA_RECIPES, f"{r}.yaml")) for r in flow["recipes"]]

    user_fills = []
    for ri, r in enumerate(recipe_objs):
        for si, step in enumerate(r["steps"]):
            if step.get("value") == "<USER_FILLS>":
                user_fills.append((ri, si, r["name"], step.get("target")))

    overrides = {}
    for ri, si, rname, target in user_fills:
        overrides[(ri, si)] = st.text_input(
            f"{rname} → {target}", type="password", key=f"f_{ri}_{si}"
        )

    if st.button("Run flow", type="primary"):
        for (ri, si), val in overrides.items():
            if not val:
                st.error("Fill all sensitive values.")
                st.stop()
            recipe_objs[ri]["steps"][si]["value"] = val

        elements_by_page = {}
        for r in recipe_objs:
            if r["start_url"] not in elements_by_page:
                elements_by_page[r["start_url"]] = excel_manager.read_element_map(r["start_url"])

        async def _run_flow():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                executor = RecipeExecutor(elements_by_page=elements_by_page)
                results = []
                for r in recipe_objs:
                    await page.goto(r["start_url"])
                    results.append((r["name"], await executor.execute(page, r)))
                await browser.close()
                return results

        with st.spinner("Running flow..."):
            results = _run_async_flow(_run_flow())

        overall = "success"
        for rname, res in results:
            ic = "PASS" if res["outcome_match"] else "FAIL"
            st.markdown(f"### [{ic}] {rname}")
            st.text(f"expected={res['expected_outcome']} actual={res['actual_outcome']}")
            for sr in res["step_results"]:
                lab = "PASS" if sr["status"] == "PASS" else "FAIL"
                err = f" — {sr['error']}" if sr["error"] else ""
                st.text(f"  [{lab}] step {sr['step_idx']}{err}")
            if not res["outcome_match"]:
                overall = "failure"

        if overall == flow["expected_outcome"]:
            st.success(f"Flow PASSED (expected {flow['expected_outcome']}, actual {overall})")
        else:
            st.error(f"Flow FAILED (expected {flow['expected_outcome']}, actual {overall})")

    st.stop()

scanned_urls = excel_manager.list_scanned_urls()

if not scanned_urls:
    st.info("No scanned URLs found. Go to the Scanner page first.")
    st.stop()

url = st.selectbox("Target URL", scanned_urls)

if url:
    excel_path = excel_manager.get_excel_path(url)
    with open(excel_path, "rb") as f:
        st.download_button(
            label="Download Excel",
            data=f.read(),
            file_name=f"scan_{excel_manager.sanitize_url(url)}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    test_data_rows = excel_manager.read_test_data(url)

    if not test_data_rows:
        st.warning("No test data found. Go to the Test Data page to add test cases.")
        st.stop()

    test_case_options = {
        f"{td.get('S.No', i+1)} - {td.get('Test Case Name', 'Unnamed')}": i
        for i, td in enumerate(test_data_rows)
    }

    run_all = st.checkbox("Run all test cases")

    if not run_all:
        selected_case = st.selectbox("Select Test Case", list(test_case_options.keys()))
    else:
        selected_case = None

    col1, col2 = st.columns(2)
    with col1:
        click_submit = st.checkbox("Click Submit after fill")
    with col2:
        take_screenshot = st.checkbox("Take screenshot", value=True)

    if st.button("Run Setter", type="primary"):
        ollama_host = os.environ.get("OLLAMA_HOST", "")
        ollama_model = os.environ.get("OLLAMA_MODEL", "mistral")
        healer = Healer(ai_host=ollama_host, ai_model=ollama_model)

        if run_all:
            cases_to_run = list(range(len(test_data_rows)))
        else:
            cases_to_run = [test_case_options[selected_case]]

        for case_idx in cases_to_run:
            td = test_data_rows[case_idx]
            case_name = td.get("Test Case Name", f"Case {case_idx + 1}")
            run_id = f"RUN-{datetime.now().strftime('%Y%m%d%H%M%S')}-{case_idx}"

            st.subheader(f"Test Case: {case_name}")

            with st.spinner("Running self-heal check..."):
                heal_report = healer.heal(url, excel_manager)

            if heal_report["changed"] > 0 or heal_report["new"] > 0 or heal_report["removed"] > 0:
                st.info(f"Self-heal: {heal_report['changed']} changed, {heal_report['new']} new, {heal_report['removed']} removed")

            element_map = excel_manager.read_element_map(url)

            test_values = {
                k: v for k, v in td.items()
                if k not in ("S.No", "Test Case Name", "sno", "test_case_name") and v
            }

            with st.spinner("Populating fields and verifying..."):
                results = setter.set_fields(
                    url, element_map, test_values,
                    screenshot_dir=SCREENSHOT_DIR if take_screenshot else None,
                    run_id=run_id,
                    click_submit=click_submit,
                )

            pass_count = sum(1 for r in results if r["status"] == "PASS")
            fail_count = sum(1 for r in results if r["status"] == "FAIL")
            total = len(results)

            if fail_count == 0:
                st.success(f"Result: {pass_count}/{total} passed")
            else:
                st.error(f"Result: {pass_count}/{total} passed, {fail_count} failed")

            for r in results:
                marker = "PASS" if r["status"] == "PASS" else "FAIL"
                st.text(f"[{marker}] {r['element_name']} - Expected: {r['expected_value']} | Actual: {r['actual_value']}")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for r in results:
                excel_manager.append_run_result(url, {
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "test_case_name": case_name,
                    "element_name": r["element_name"],
                    "expected_value": r["expected_value"],
                    "actual_value": r["actual_value"],
                    "status": r["status"],
                    "screenshot": f"{SCREENSHOT_DIR}/{run_id}.png" if take_screenshot else "",
                })

            if take_screenshot:
                screenshot_path = os.path.join(SCREENSHOT_DIR, f"{run_id}.png")
                if os.path.exists(screenshot_path):
                    st.image(screenshot_path, caption=f"Screenshot: {case_name}")

            st.divider()
