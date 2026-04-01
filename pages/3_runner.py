import asyncio
import os
import streamlit as st
from datetime import datetime
from core.setter import Setter
from core.healer import Healer
from core.excel_manager import ExcelManager

st.set_page_config(page_title="Runner", page_icon="▶", layout="wide")
st.title("▶ Run Tests")

DATA_DIR = "data/scans"
SCREENSHOT_DIR = "screenshots"
excel_manager = ExcelManager(data_dir=DATA_DIR)
setter = Setter()

api_key = os.environ.get("GEMINI_API_KEY", "")
if "gemini_api_key" not in st.session_state:
    st.session_state.gemini_api_key = api_key

scanned_urls = excel_manager.list_scanned_urls()

if not scanned_urls:
    st.info("No scanned URLs found. Go to the Scanner page first.")
    st.stop()

url = st.selectbox("Target URL", scanned_urls)

if url:
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
        healer = Healer(ai_api_key=st.session_state.gemini_api_key)

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
                heal_report = asyncio.run(healer.heal(url, excel_manager))

            if heal_report["changed"] > 0 or heal_report["new"] > 0 or heal_report["removed"] > 0:
                st.info(f"Self-heal: {heal_report['changed']} changed, {heal_report['new']} new, {heal_report['removed']} removed")

            element_map = excel_manager.read_element_map(url)

            test_values = {
                k: v for k, v in td.items()
                if k not in ("S.No", "Test Case Name", "sno", "test_case_name") and v
            }

            with st.spinner("Populating fields and verifying..."):
                results = asyncio.run(setter.set_fields(
                    url, element_map, test_values,
                    screenshot_dir=SCREENSHOT_DIR if take_screenshot else None,
                    run_id=run_id,
                    click_submit=click_submit,
                ))

            pass_count = sum(1 for r in results if r["status"] == "PASS")
            fail_count = sum(1 for r in results if r["status"] == "FAIL")
            total = len(results)

            if fail_count == 0:
                st.success(f"Result: {pass_count}/{total} PASSED")
            else:
                st.error(f"Result: {pass_count}/{total} PASSED | {fail_count} FAILED")

            for r in results:
                icon = "✅" if r["status"] == "PASS" else "❌"
                st.text(f"{icon} {r['element_name']} — Expected: {r['expected_value']} | Actual: {r['actual_value']}")

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
