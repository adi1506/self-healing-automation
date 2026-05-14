import asyncio
import os
import sys
import uuid
from datetime import datetime
import streamlit as st
from playwright.async_api import async_playwright
from core.scenarios import load_scenario, save_scenario
from core.recipes import RecipeExecutor
from core.scanner import _run_async
from core.setter import Setter
from core.browser_launch import launch_browser_and_page
from core.runner_utils import classify_case_outcome, is_blank_dataset_row
from core.excel_manager import ExcelManager
from ui.scenarios.steps_tab import render as render_steps
from ui.scenarios.dataset_tab import render as render_dataset
from ui.scenarios.runs_tab import render as render_runs
from ui.scenarios.settings_tab import render as render_settings

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"

# Actions that require a non-empty `target` element to be runnable. A step with
# action="fill" and target="" is a leftover from the new-scenario seed row and
# should be skipped, not allowed to crash the whole scenario.
TARGET_REQUIRED_ACTIONS = {"fill", "click", "select", "check"}


def _save_steps(sc, new_steps):
    sc.steps = new_steps
    save_scenario(DATA_SCENARIOS, sc)


def _save_dataset(sc, rows):
    sc.dataset = rows
    save_scenario(DATA_SCENARIOS, sc)


def _step_is_runnable(step: dict) -> bool:
    action = (step.get("action") or "").strip()
    if not action:
        return False
    if action in TARGET_REQUIRED_ACTIONS and not (step.get("target") or "").strip():
        return False
    return True


def _run_scenario(sc):
    """Execute a scenario and return a uniform result envelope.

    Two execution modes:
      - "dataset": the scenario has dataset rows. Each row is set field-by-field
        via Setter.set_fields (the same path used by the legacy runner). This
        honors the dataset tab's "Scenario will run N time(s)" promise and lets
        users who filled in the dataset but never authored steps actually run.
      - "steps": classic recipe-driven execution via RecipeExecutor, with empty
        or otherwise unrunnable steps filtered out so a leftover seed row
        doesn't fail the whole scenario.

    Envelope: {"mode": "dataset"|"steps"|"empty", "run_id": str, ...}

    The run_id is generated up front so screenshot filenames captured by the
    Setter line up with the rows persisted by _persist_run.
    """
    if sc.kind == "multi-page":
        return _run_multi_page_scenario(sc)
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    headed_ok = sys.platform != "linux" or bool(os.environ.get("DISPLAY"))

    if sc.dataset and any(not is_blank_dataset_row(r) for r in sc.dataset):
        return _run_dataset(sc, elements)

    valid_steps = [s for s in (sc.steps or []) if _step_is_runnable(s)]
    if not valid_steps:
        return {"mode": "empty", "message": (
            "Scenario has no runnable steps and no dataset rows. Add steps in the Steps tab "
            "or generate a dataset in the Dataset tab."
        )}

    recipe = {
        "name": sc.name, "start_url": sc.base_url,
        "steps": valid_steps, "assertions": sc.assertions or [],
        "expected_outcome": sc.expected_outcome,
    }

    async def _run():
        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p, headless=not headed_ok)
            await page.goto(sc.base_url)
            executor = RecipeExecutor(elements_by_page={sc.base_url: elements})
            result = await executor.execute(page, recipe)
            await browser.close()
            return result

    raw = _run_async(_run())
    return {"mode": "steps", "steps": valid_steps, **raw}


def _run_dataset(sc, elements: list[dict]) -> dict:
    """Drive the scenario from dataset rows via Setter.set_fields.

    Each row becomes one execution. Field-level results are collected per row.
    A row PASSes when every editable field's actual value matches what was
    requested AND that aligns with the row's __expected_outcome.
    """
    setter = Setter()
    row_outcomes = []

    # Skip rows that carry no field data (untouched "+ Add empty row", a
    # stray placeholder row from the data_editor, etc.) — running them would
    # vacuously PASS and inflate the run count above what the user sees.
    runnable_rows = [r for r in sc.dataset if not is_blank_dataset_row(r)]

    # run_id is the umbrella id for this whole scenario execution; the Setter
    # writes one screenshot per row, named "<run_id>_row<idx>.png", so the
    # filenames are predictable when _persist_run links them.
    run_id = uuid.uuid4().hex[:8]
    em = ExcelManager(data_dir=DATA_SCANS)
    slug = em.sanitize_url(sc.base_url) if sc.base_url else ""
    screenshot_dir = os.path.join(DATA_SCANS, slug, "screenshots") if slug else None

    for idx, raw_row in enumerate(runnable_rows):
        row = dict(raw_row)
        expected_outcome = (row.pop("__expected_outcome", None)
                            or sc.expected_outcome
                            or "success").lower()
        test_name = (row.pop("__test_name", None) or "").strip()
        # Drop empty values — Setter treats absent keys as "skip this field".
        # Keep explicit empty strings only when the test case intentionally
        # exercises an empty value (negative cases). Heuristic: keep "" only
        # when expected_outcome=="failure", since that's the required-violation
        # signal.
        test_data = {}
        for k, v in row.items():
            if v is None:
                continue
            sv = str(v)
            if sv == "" and expected_outcome != "failure":
                continue
            test_data[k] = sv

        # click_submit=True lets us detect HTML5 validation failures that only
        # fire at submit time (malformed email, required-but-empty, pattern
        # mismatch). Setter spins up a fresh browser per row so submitting
        # doesn't leak state into subsequent rows.
        shot_id = f"{run_id}_row{idx}"
        field_results = setter.set_fields(
            sc.base_url, elements, test_data, click_submit=True,
            screenshot_dir=screenshot_dir, run_id=shot_id,
        )
        # The Setter only writes the PNG when both screenshot_dir and run_id are
        # set AND it managed to reach the screenshot step. Verify before
        # recording the path so the UI doesn't link to a missing file.
        screenshot_path = ""
        if screenshot_dir:
            candidate = os.path.join(screenshot_dir, f"{shot_id}.png")
            if os.path.exists(candidate):
                screenshot_path = candidate
        status = classify_case_outcome(
            expected_outcome=expected_outcome,
            setter_results=field_results,
            click_submit=True,
            form_was_rejected=setter.last_form_rejected,
        )
        row_label = test_name or f"Row {idx + 1}"
        row_outcomes.append({
            "row_index": idx,
            "row_label": row_label,
            "test_name": test_name,
            "expected_outcome": expected_outcome,
            "field_results": field_results,
            "row_status": status,
            "form_rejected": setter.last_form_rejected,
            "screenshot": screenshot_path,
        })

    total = len(row_outcomes)
    passed = sum(1 for r in row_outcomes if r["row_status"] == "PASS")
    unverified = sum(1 for r in row_outcomes if r["row_status"] == "UNVERIFIED")
    parts = [f"{passed}/{total} rows passed"]
    if unverified:
        parts.append(f"{unverified} unverified")
    return {
        "mode": "dataset",
        "run_id": run_id,
        "row_outcomes": row_outcomes,
        "outcome_match": passed == total,
        "summary": ", ".join(parts),
    }


def _run_multi_page_scenario(sc, data_scans_dir: str = DATA_SCANS) -> dict:
    """Drive a multi-page scenario in one Playwright session.

    Walks sc.pages in order. For each page:
      - resolves the page's element map from scans
      - if a non-blank dataset row exists, runs only the FIRST row via
        Setter.set_fields_on_page; else falls back to RecipeExecutor
        with the page's steps
      - except on the last page, clicks the configured transition target
        and waits for the configured signal before continuing

    Returns the multi-page result envelope documented in the spec.
    """
    em = ExcelManager(data_dir=data_scans_dir)
    setter = Setter()
    page_outcomes: list[dict] = []
    run_id = uuid.uuid4().hex[:8]
    headed_ok = sys.platform != "linux" or bool(os.environ.get("DISPLAY"))

    async def _drive():
        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p, headless=not headed_ok)
            try:
                for idx, page_entry in enumerate(sc.pages):
                    base_url = page_entry["base_url"]
                    elements = em.read_element_map(base_url)
                    if idx == 0:
                        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

                    page_status, field_results, step_results = await _drive_one_page(
                        page, page_entry, elements, setter, run_id, idx, data_scans_dir,
                    )

                    transition_status = "N/A"
                    transition_error = ""
                    if idx < len(sc.pages) - 1:
                        transition_status, transition_error = await _run_transition(
                            page, page_entry.get("transition") or {}, elements,
                        )
                        if transition_status == "FAIL":
                            page_outcomes.append({
                                "page_index": idx, "base_url": base_url,
                                "page_status": page_status,
                                "field_results": field_results,
                                "step_results": step_results,
                                "transition_status": transition_status,
                                "transition_error": transition_error,
                                "screenshot": "",
                            })
                            for skipped_idx in range(idx + 1, len(sc.pages)):
                                page_outcomes.append({
                                    "page_index": skipped_idx,
                                    "base_url": sc.pages[skipped_idx]["base_url"],
                                    "page_status": "SKIPPED",
                                    "field_results": [], "step_results": [],
                                    "transition_status": "N/A",
                                    "transition_error": "skipped after transition failure",
                                    "screenshot": "",
                                })
                            return

                    page_outcomes.append({
                        "page_index": idx, "base_url": base_url,
                        "page_status": page_status,
                        "field_results": field_results,
                        "step_results": step_results,
                        "transition_status": transition_status,
                        "transition_error": transition_error,
                        "screenshot": "",
                    })
            finally:
                await browser.close()

    _run_async(_drive())

    statuses = [p["page_status"] for p in page_outcomes]
    if any(s == "FAIL" for s in statuses):
        scenario_status = "FAIL"
    elif any(s == "UNVERIFIED" for s in statuses):
        scenario_status = "UNVERIFIED"
    elif all(s in ("PASS", "SKIPPED") for s in statuses) and "PASS" in statuses:
        scenario_status = "PASS"
    else:
        scenario_status = "FAIL"

    passed = sum(1 for s in statuses if s == "PASS")
    return {
        "mode": "multi-page",
        "run_id": run_id,
        "page_outcomes": page_outcomes,
        "scenario_status": scenario_status,
        "summary": f"{passed}/{len(page_outcomes)} pages passed",
    }


async def _drive_one_page(page, page_entry, elements, setter, run_id, page_idx, data_scans_dir):
    """Return (page_status, field_results, step_results).

    Dataset path wins over steps path when the dataset has any non-blank row.
    Only the FIRST non-blank row is used in multi-page mode.
    """
    dataset = page_entry.get("dataset") or []
    runnable_rows = [r for r in dataset if not is_blank_dataset_row(r)]

    if runnable_rows:
        row = dict(runnable_rows[0])
        expected = (row.pop("__expected_outcome", None) or "success").lower()
        row.pop("__test_name", None)
        test_data = {}
        for k, v in row.items():
            if v is None:
                continue
            sv = str(v)
            if sv == "" and expected != "failure":
                continue
            test_data[k] = sv
        field_results = await setter.set_fields_on_page(
            page, elements, test_data, click_submit=False,
        )
        status = classify_case_outcome(
            expected_outcome=expected,
            setter_results=field_results,
            click_submit=False,
            form_was_rejected=None,
        )
        # In multi-page mode click_submit=False because the transition
        # button is what advances the journey, not the form's submit.
        if status == "UNVERIFIED" and expected == "success":
            status = "PASS" if all(r.get("status") == "PASS" for r in field_results) else "FAIL"
        return status, field_results, []

    steps = [s for s in (page_entry.get("steps") or []) if _step_is_runnable(s)]
    if not steps:
        return "SKIPPED", [], []

    recipe = {
        "name": f"page_{page_idx}",
        "start_url": page_entry["base_url"],
        "steps": steps,
        "assertions": [],
        "expected_outcome": "success",
    }
    executor = RecipeExecutor(elements_by_page={page_entry["base_url"]: elements})
    result = await executor.execute(page, recipe)
    step_results = result.get("step_results", [])
    status = "PASS" if all(r.get("status") == "PASS" for r in step_results) else "FAIL"
    return status, [], step_results


async def _run_transition(page, transition: dict, elements: list[dict]) -> tuple[str, str]:
    """Click the configured target, wait for the configured signal.

    Returns ("PASS", "") on success, ("FAIL", reason) on failure.
    """
    target_name = (transition.get("target") or "").strip()
    if not target_name:
        return "FAIL", "transition target not configured"

    target_elem = next((e for e in elements if e["element_name"] == target_name), None)
    if target_elem is None:
        return "FAIL", f"transition target {target_name!r} not in scanned elements"

    setter = Setter()
    handle = await setter._find_element(page, target_elem)
    if handle is None:
        return "FAIL", f"could not locate transition target {target_name!r}"

    try:
        await handle.click()
    except Exception as exc:
        return "FAIL", f"click failed: {exc}"

    timeout_ms = int(transition.get("timeout_ms") or 30000)
    wait_for = (transition.get("wait_for") or "url_contains").strip()
    value = (transition.get("value") or "").strip()
    try:
        if wait_for == "url_contains":
            if not value:
                return "FAIL", "url_contains value is empty"
            await page.wait_for_url(f"**{value}**", timeout=timeout_ms)
        elif wait_for == "selector":
            if not value:
                return "FAIL", "selector value is empty"
            await page.wait_for_selector(value, timeout=timeout_ms)
        else:
            return "FAIL", f"unknown wait_for: {wait_for!r}"
    except Exception as exc:
        return "FAIL", f"wait condition failed: {exc}"

    return "PASS", ""


def _persist_run(sc, result: dict) -> None:
    """Append run outcomes to the page's Run Results sheet so the Runs tab
    and Dashboard see them. Without this, the in-tab render is the only
    surface that knows the run happened."""
    mode = result.get("mode")
    if mode not in ("dataset", "steps") or not sc.base_url:
        return

    em = ExcelManager(data_dir=DATA_SCANS)
    run_id = result.get("run_id") or uuid.uuid4().hex[:8]
    ts = datetime.now().isoformat(timespec="seconds")
    common = {"run_id": run_id, "timestamp": ts, "test_case_name": sc.name}

    if mode == "dataset":
        for row in result.get("row_outcomes", []):
            row_label = row.get("row_label") or row.get("test_name") or f"Row {row['row_index'] + 1}"
            screenshot = row.get("screenshot", "")
            for fr in row.get("field_results", []):
                em.append_run_result(sc.base_url, {
                    **common,
                    "row_label": row_label,
                    "element_name": fr.get("element_name", ""),
                    "expected_value": fr.get("expected_value", ""),
                    "actual_value": fr.get("actual_value", ""),
                    "status": fr.get("status", ""),
                    "screenshot": screenshot,
                })
        return

    steps = result.get("steps") or sc.steps or []
    for s, r in zip(steps, result.get("step_results", [])):
        status = r.get("status", "")
        em.append_run_result(sc.base_url, {
            **common,
            "row_label": "",
            "element_name": (s.get("target") or s.get("action") or ""),
            "expected_value": s.get("value", ""),
            "actual_value": r.get("error", "") if status != "PASS" else "",
            "status": status,
            "screenshot": "",
        })


def _render_run_result(sc, result: dict) -> None:
    """Render a run envelope. Branches on `mode` because dataset-driven runs
    have per-row field results while step-driven runs have per-step outcomes."""
    mode = result.get("mode")

    if mode == "empty":
        st.warning(result.get("message", "Scenario has no runnable steps."))
        return

    if mode == "dataset":
        st.info(result["summary"])
        for row in result["row_outcomes"]:
            name = row.get("row_label") or row.get("test_name") or f"Row {row['row_index'] + 1}"
            label = f"{name} (expected {row['expected_outcome']})"
            icon = "✓" if row["row_status"] == "PASS" else "✗"
            with st.expander(f"{icon} {label} — {row['row_status']}",
                             expanded=row["row_status"] != "PASS"):
                for fr in row["field_results"]:
                    fr_icon = "PASS" if fr["status"] == "PASS" else fr["status"]
                    st.text(
                        f"[{fr_icon}] {fr['element_name']}: "
                        f"expected={fr['expected_value']!r} actual={fr['actual_value']!r}"
                    )
                shot = row.get("screenshot")
                if shot and os.path.exists(shot):
                    st.image(shot, caption="Submitted form", use_container_width=True)
        return

    # mode == "steps" (recipe path)
    steps = result.get("steps") or sc.steps
    for s, r in zip(steps, result.get("step_results", [])):
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        err = f" — {r['error']}" if r.get("error") else ""
        st.text(f"[{icon}] {s.get('action')} {s.get('target', '')}{err}")
    if "outcome_match" in result:
        st.info(f"Outcome match: {result['outcome_match']}")


def render(scenario_id: str):
    sc = load_scenario(DATA_SCENARIOS, scenario_id)

    if st.button("← Back to list", key=f"back_{sc.id}"):
        st.session_state.pop("_open_scenario", None)
        st.rerun()

    st.title(sc.name)
    if sc.base_url:
        st.caption(f"Target page: {sc.base_url}")
    else:
        st.caption("No base URL set — pick a scanned page in Settings.")

    if st.button(f"▶ Run scenario", type="primary", key=f"run_{sc.id}",
                 disabled=not sc.base_url):
        if sc.kind != "single-page" or not sc.base_url:
            st.error("Multi-page run not wired in this tab yet; use the Run dialog.")
        else:
            with st.spinner(f"Running on {sc.base_url}..."):
                result = _run_scenario(sc)
            _persist_run(sc, result)
            _render_run_result(sc, result)

    tab1, tab2, tab3, tab4 = st.tabs(["Steps", "Dataset", "Runs", "Settings"])
    with tab1: render_steps(sc, lambda s: _save_steps(sc, s))
    with tab2: render_dataset(sc, lambda d: _save_dataset(sc, d))
    with tab3: render_runs(sc)
    with tab4: render_settings(sc)
