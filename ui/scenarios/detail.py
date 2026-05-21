import asyncio
import os
import sys
import uuid
from datetime import datetime
import streamlit as st
from playwright.async_api import async_playwright
from core.scenarios import load_scenario, save_scenario, delete_scenario
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
from ui.scenarios import recording_editor

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"

# Actions that require a non-empty `target` element to be runnable. A step with
# action="fill" and target="" is a leftover from the new-scenario seed row and
# should be skipped, not allowed to crash the whole scenario.
TARGET_REQUIRED_ACTIONS = {"fill", "click", "select", "check"}


class _PageView:
    """Tiny shim that lets Steps/Dataset widgets work against either a
    Scenario (single-page) or a single page entry inside Scenario.pages
    (multi-page). It exposes id, name, base_url, steps, dataset — the
    four attributes the existing widgets read."""

    def __init__(self, sc, page_idx: int | None):
        self._sc = sc
        self._idx = page_idx

    @property
    def id(self) -> str:
        if self._idx is None:
            return self._sc.id
        return f"{self._sc.id}__p{self._idx}"

    @property
    def name(self) -> str:
        return self._sc.name

    @property
    def base_url(self) -> str:
        if self._idx is None:
            return self._sc.base_url
        return self._sc.pages[self._idx].get("base_url", "")

    @property
    def steps(self) -> list[dict]:
        if self._idx is None:
            return self._sc.steps or []
        return self._sc.pages[self._idx].get("steps") or []

    @property
    def dataset(self) -> list[dict]:
        if self._idx is None:
            return self._sc.dataset or []
        return self._sc.pages[self._idx].get("dataset") or []


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
    if mode not in ("dataset", "steps", "multi-page"):
        return
    if mode != "multi-page" and not sc.base_url:
        return

    em = ExcelManager(data_dir=DATA_SCANS)
    run_id = result.get("run_id") or uuid.uuid4().hex[:8]
    ts = datetime.now().isoformat(timespec="seconds")
    common = {"run_id": run_id, "timestamp": ts, "test_case_name": sc.name}

    if mode == "multi-page":
        for po in result.get("page_outcomes", []):
            url = po["base_url"]
            page_idx = po["page_index"]
            row_label = f"Page {page_idx + 1}: {url}"
            for fr in po.get("field_results", []) or []:
                em.append_run_result(url, {
                    **common, "row_label": row_label,
                    "element_name": fr.get("element_name", ""),
                    "expected_value": fr.get("expected_value", ""),
                    "actual_value": fr.get("actual_value", ""),
                    "status": fr.get("status", ""),
                    "screenshot": po.get("screenshot", ""),
                    "page_index": page_idx,
                })
            for sr_idx, sr in enumerate(po.get("step_results", []) or []):
                em.append_run_result(url, {
                    **common, "row_label": row_label,
                    "element_name": f"step{sr_idx}",
                    "expected_value": "",
                    "actual_value": sr.get("error", "") if sr.get("status") != "PASS" else "",
                    "status": sr.get("status", ""),
                    "screenshot": po.get("screenshot", ""),
                    "page_index": page_idx,
                })
            if po["page_status"] == "SKIPPED" and not po.get("field_results") and not po.get("step_results"):
                em.append_run_result(url, {
                    **common, "row_label": row_label,
                    "element_name": "(page skipped)",
                    "expected_value": "", "actual_value": po.get("transition_error", ""),
                    "status": "SKIPPED", "screenshot": "",
                    "page_index": page_idx,
                })
        return

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

    if mode == "multi-page":
        st.info(result["summary"])
        for po in result["page_outcomes"]:
            status = po["page_status"]
            icon = "✓" if status == "PASS" else ("⏭" if status == "SKIPPED" else "✗")
            label = f"Page {po['page_index'] + 1}: {po['base_url']} — {status}"
            with st.expander(f"{icon} {label}", expanded=status != "PASS"):
                for fr in po.get("field_results", []) or []:
                    fr_icon = fr["status"]
                    st.text(
                        f"[{fr_icon}] {fr['element_name']}: "
                        f"expected={fr['expected_value']!r} actual={fr['actual_value']!r}"
                    )
                for sr_idx, sr in enumerate(po.get("step_results", []) or []):
                    icon2 = "PASS" if sr["status"] == "PASS" else "FAIL"
                    err = f" — {sr['error']}" if sr.get("error") else ""
                    st.text(f"[{icon2}] step{sr_idx}{err}")
                if po["transition_status"] == "FAIL":
                    st.error(f"Transition failed: {po['transition_error']}")
                elif po["transition_status"] == "PASS":
                    st.caption("→ transition succeeded")
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


def _render_recorded_scenario(sc) -> None:
    """UI for a recorded scenario: shows existing recordings + a Start button
    that launches the recorder CLI as a subprocess, polling for its output."""
    import json, subprocess
    from pathlib import Path
    from core.applications import load_application
    from core.auth_session import load_storage_state, is_storage_state_valid
    from core.recording import load_recording

    app = load_application("data/applications", sc.application_id)
    if not is_storage_state_valid(app):
        st.error(
            "This application's login session is expired or missing. "
            "Refresh it on the Recordings page first."
        )
        return

    work_dir = "data/recorder_work"
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    state_in_path = os.path.join(work_dir, f"{sc.id}_state_in.json")

    # ── Scenario-level actions ──────────────────────────────────────────
    _render_scenario_actions(sc)

    st.subheader("Recordings")
    real_recs = [r for r in sc.recordings if r.get("id") and r["id"] != "placeholder"]
    if not real_recs:
        st.info("No recordings yet. Start one below.")
    else:
        for r in real_recs:
            _render_recording_row(sc, r)

    # Transient flows. Test-case authoring (manual / AI / Excel) renders ABOVE
    # the replay output so the form sits above the recorded Step-by-step list
    # when a replay is also on screen.
    addtc_target = st.session_state.get(f"_addtc_target_{sc.id}")
    if addtc_target:
        _render_test_case_editor(sc, addtc_target)

    gen_target = st.session_state.get(f"_gen_target_{sc.id}")
    if gen_target:
        _render_ai_generator(sc, gen_target)

    xls_target = st.session_state.get(f"_xls_target_{sc.id}")
    if xls_target:
        _render_excel_test_cases_uploader(sc, xls_target)

    replay_target = st.session_state.get(f"_replay_target_{sc.id}")
    if replay_target:
        overrides = st.session_state.get(f"_replay_overrides_{sc.id}")
        label = st.session_state.get(f"_replay_label_{sc.id}")
        _render_replay(sc, replay_target, overrides=overrides, label=label)

    rec_out = os.path.join(work_dir, f"{sc.id}_rec.yaml")
    cand_out = os.path.join(work_dir, f"{sc.id}_cand.json")
    proc_key = f"rec_proc_{sc.id}"
    pending_key = f"_pending_recording_{sc.id}"

    if pending_key in st.session_state:
        _render_pending_recording(sc, rec_out, cand_out, pending_key, proc_key)
        return

    if proc_key in st.session_state:
        if not os.path.exists(rec_out):
            st.warning(
                "Recording in progress. Close the browser window when done, "
                "then click Refresh."
            )
            cols = st.columns([2, 2, 6])
            if cols[0].button("Refresh", key=f"rref_{sc.id}"):
                st.rerun()
            if cols[1].button("Cancel recording", key=f"rcancel_{sc.id}"):
                # Doesn't kill the orphaned recorder subprocess — the user is
                # told to close the browser window, which ends it naturally.
                # We just clear our session state and stale files so the UI
                # is usable again.
                for p in (rec_out, cand_out):
                    if os.path.exists(p):
                        os.remove(p)
                st.session_state.pop(proc_key, None)
                st.rerun()
        else:
            # Recording file landed. DO NOT persist yet — stage it under
            # _pending_recording and let the user explicitly Save or Discard.
            st.session_state[pending_key] = rec_out
            st.session_state.pop(proc_key, None)
            st.rerun()
    elif not real_recs:
        # Only offer to start a new recording when this scenario has none yet.
        # Once a recording exists, additional ones should be a new scenario.
        st.divider()
        st.markdown("#### New recording")
        start_url = st.text_input(
            "Start URL", value=app.base_url_pattern, key=f"surl_{sc.id}",
        )
        name = st.text_input(
            "Recording name", value="Happy path", key=f"rname_{sc.id}",
        )
        if st.button("Start recording", key=f"rstart_{sc.id}") and start_url and name:
            for p in (rec_out, cand_out):
                if os.path.exists(p):
                    os.remove(p)
            state = load_storage_state("data/storage_states", sc.application_id)
            Path(state_in_path).write_text(json.dumps(state), encoding="utf-8")
            proc = subprocess.Popen([
                sys.executable, "-m", "core.recorder_cli",
                "--app-id", sc.application_id,
                "--start-url", start_url,
                "--output-recording", rec_out,
                "--output-candidates", cand_out,
                "--storage-state-path", state_in_path,
                "--name", name,
                "--headless", "false",
            ])
            st.session_state[proc_key] = proc.pid
            st.rerun()


def _render_scenario_actions(sc) -> None:
    """Top-of-page action row: delete scenario (with confirm)."""
    confirm_key = f"_confirm_del_scn_{sc.id}"
    if st.session_state.get(confirm_key):
        st.warning(
            f"Delete scenario **{sc.name}** and all its recordings? "
            "This cannot be undone."
        )
        c1, c2, _ = st.columns([2, 2, 6])
        if c1.button("Yes, delete", type="primary", key=f"_confirm_del_yes_{sc.id}"):
            delete_scenario(DATA_SCENARIOS, sc.id)
            st.session_state.pop(confirm_key, None)
            st.session_state.pop("_open_scenario", None)
            st.rerun()
        if c2.button("Cancel", key=f"_confirm_del_no_{sc.id}"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
        return
    cols = st.columns([3, 7])
    if cols[0].button("🗑 Delete scenario", key=f"_del_scn_{sc.id}"):
        st.session_state[confirm_key] = True
        st.rerun()


def _render_recording_row(sc, rec: dict) -> None:
    """One recording shown as a row with Replay / Add test case (submenu) /
    Delete, plus a saved-test-cases picker if any exist."""
    rec_id = rec["id"]
    name = rec.get("name", rec_id)
    n_steps = len(rec.get("steps", []))
    picker_key = f"_addtc_picker_{sc.id}_{rec_id}"
    st.markdown(f"**{name}** — {n_steps} steps")
    cols = st.columns([2, 2, 2, 4])
    if cols[0].button("▶ Replay recording", key=f"row_replay_{sc.id}_{rec_id}",
                      type="primary"):
        st.session_state[f"_replay_target_{sc.id}"] = rec_id
        st.session_state.pop(f"_replay_overrides_{sc.id}", None)
        st.session_state.pop(f"_replay_label_{sc.id}", None)
        st.session_state.pop(f"_replay_outcome_{sc.id}", None)
        st.rerun()
    if cols[1].button("➕ Add test case", key=f"row_addtc_{sc.id}_{rec_id}"):
        st.session_state[picker_key] = not st.session_state.get(picker_key, False)
        st.rerun()
    if cols[2].button("🗑 Delete", key=f"row_del_{sc.id}_{rec_id}"):
        sc.recordings = [r for r in sc.recordings if r.get("id") != rec_id]
        # Drop test cases that referenced this recording — they're now orphans.
        sc.ai_test_cases = [
            tc for tc in (sc.ai_test_cases or [])
            if tc.get("recording_id") != rec_id
        ]
        save_scenario(DATA_SCENARIOS, sc)
        st.rerun()

    if st.session_state.get(picker_key):
        with st.container(border=True):
            st.caption("How do you want to add the test case?")
            sub = st.columns([2, 2, 2, 2])
            if sub[0].button("🤖 AI generated", key=f"addtc_ai_{sc.id}_{rec_id}"):
                st.session_state[f"_gen_target_{sc.id}"] = rec_id
                st.session_state.pop(picker_key, None)
                st.rerun()
            if sub[1].button("📄 Upload Excel", key=f"addtc_xls_{sc.id}_{rec_id}"):
                st.session_state[f"_xls_target_{sc.id}"] = rec_id
                st.session_state.pop(picker_key, None)
                st.rerun()
            if sub[2].button("🧪 Add manually", key=f"addtc_man_{sc.id}_{rec_id}"):
                st.session_state[f"_addtc_target_{sc.id}"] = rec_id
                st.session_state.pop(picker_key, None)
                st.rerun()
            if sub[3].button("Cancel", key=f"addtc_cancel_{sc.id}_{rec_id}"):
                st.session_state.pop(picker_key, None)
                st.rerun()

    # Per-recording test-case selector: only render if cases exist.
    rec_cases = [
        tc for tc in (sc.ai_test_cases or [])
        if tc.get("recording_id") == rec_id
    ]
    if rec_cases:
        labels = [
            f"{tc['name']} ({tc.get('expected_outcome', 'success')})"
            for tc in rec_cases
        ]
        pick_key = f"_tc_pick_{sc.id}_{rec_id}"
        st.selectbox(
            "Test case", options=labels, key=pick_key,
            label_visibility="collapsed",
        )
        if st.button(
            "▶ Run with selected test case",
            key=f"row_runtc_{sc.id}_{rec_id}",
        ):
            picked_label = st.session_state.get(pick_key)
            tc = next((c for c, lbl in zip(rec_cases, labels) if lbl == picked_label), None)
            if tc is not None:
                st.session_state[f"_replay_target_{sc.id}"] = rec_id
                st.session_state[f"_replay_overrides_{sc.id}"] = dict(tc.get("overrides", {}))
                st.session_state[f"_replay_label_{sc.id}"] = f"{name} · {tc['name']}"
                st.rerun()
    st.divider()


def _render_pending_recording(sc, rec_out: str, cand_out: str,
                              pending_key: str, proc_key: str) -> None:
    """Save / Discard gate after a recording file lands but before it's
    appended to the scenario. Lets the user reject a bad take."""
    from core.recording import load_recording

    try:
        preview = load_recording(rec_out)
    except Exception as e:
        st.error(f"Could not load recorded file: {e}")
        if st.button("Discard", key=f"pend_discard_err_{sc.id}"):
            for p in (rec_out, cand_out):
                if os.path.exists(p):
                    os.remove(p)
            st.session_state.pop(pending_key, None)
            st.rerun()
        return

    st.success(
        f"Recording finished: **{preview.name}** — {len(preview.steps)} steps, "
        f"start_url `{preview.start_url}`"
    )
    st.caption("Review and choose: save, save & replay, or discard.")
    cols = st.columns([2, 2, 2, 4])
    save_clicked = cols[0].button("💾 Save recording", type="primary",
                                  key=f"pend_save_{sc.id}")
    save_replay_clicked = cols[1].button("▶ Save & Replay",
                                         key=f"pend_save_replay_{sc.id}")
    discard_clicked = cols[2].button("🗑 Discard", key=f"pend_discard_{sc.id}")

    if save_clicked or save_replay_clicked:
        cleaned = [r for r in sc.recordings if r.get("id") != "placeholder"]
        cleaned.append(preview.to_dict())
        sc.recordings = cleaned
        save_scenario(DATA_SCENARIOS, sc)
        for p in (rec_out, cand_out):
            if os.path.exists(p):
                os.remove(p)
        st.session_state.pop(pending_key, None)
        if save_replay_clicked:
            st.session_state[f"_replay_target_{sc.id}"] = preview.id
            st.session_state.pop(f"_replay_overrides_{sc.id}", None)
            st.session_state.pop(f"_replay_label_{sc.id}", None)
            st.session_state.pop(f"_replay_outcome_{sc.id}", None)
        st.rerun()
    if discard_clicked:
        for p in (rec_out, cand_out):
            if os.path.exists(p):
                os.remove(p)
        st.session_state.pop(pending_key, None)
        st.rerun()


def _render_replay(sc, recording_id: str, *, overrides: dict[str, str] | None = None,
                   label: str | None = None) -> None:
    """Replay the named recording against the saved storage state and render
    a step-by-step report with screenshots.

    The outcome is cached in session_state, keyed by (recording_id, overrides,
    label), so that subsequent reruns (caused by clicking unrelated buttons)
    re-render the same result instead of launching another browser session.
    Use 'Run again' to invalidate the cache and re-execute.

    Headed by default so the user can watch; set SCANNER_HEADLESS=1 to flip
    (e.g. on the EC2 box where there's no display)."""
    from core.auth_session import load_storage_state
    from core.recording import Recording
    from core.replay import replay_recording_with_auto_fill

    rec_dict = next(
        (r for r in sc.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        st.error(f"Recording {recording_id!r} not found on this scenario.")
        st.session_state.pop(f"_replay_target_{sc.id}", None)
        return

    force_runner_up = st.session_state.pop(f"_force_runner_up_{sc.id}", None)

    cache_key = f"_replay_outcome_{sc.id}"
    sig = (recording_id,
           tuple(sorted((overrides or {}).items())),
           label or "",
           tuple(sorted((force_runner_up or {}).items())))
    cached = st.session_state.get(cache_key)
    if cached is not None and cached.get("sig") == sig:
        recording = cached["recording"]
        outcome = cached["outcome"]
    else:
        from core.recording import save_recording, load_recording
        from core.scenarios import save_scenario

        recording = Recording.from_dict(rec_dict)
        state = load_storage_state("data/storage_states", sc.application_id)
        raw = os.environ.get("SCANNER_HEADLESS", "")
        headless = raw.strip().lower() in ("1", "true", "yes", "on")
        title = label or recording.name

        # Side-write the recording to a temp file the replay can promote into.
        # After replay, we'll merge the (possibly-updated) recording back into
        # the scenario's recordings list.
        work_dir = os.path.join("data/replay_runs", recording.id)
        os.makedirs(work_dir, exist_ok=True)
        side_path = os.path.join(work_dir, "_live_recording.yaml")
        save_recording(side_path, recording)

        with st.spinner(f"Replaying {title}…"):
            outcome = _run_async(
                replay_recording_with_auto_fill(
                    recording,
                    data_overrides=overrides,
                    storage_state=state,
                    headless=headless,
                    screenshot_dir=work_dir,
                    recording_path=side_path,
                    force_runner_up=force_runner_up,
                ),
            )

        # If heals were promoted, reload the side-recording and merge it back
        # into the scenario's `recordings` list, then persist the scenario.
        if outcome.promoted_heals:
            promoted_rec = load_recording(side_path)
            for i, rdict in enumerate(sc.recordings):
                if rdict.get("id") == recording_id:
                    sc.recordings[i] = promoted_rec.to_dict()
                    save_scenario("data/scenarios", sc)
                    break

        st.session_state[cache_key] = {
            "sig": sig, "recording": recording, "outcome": outcome,
        }

    healed_n = getattr(outcome, "healed_steps", 0)
    healed_suffix = f" · {healed_n} healed 🩹" if healed_n else ""
    if outcome.error:
        st.error(
            f"Replay failed at step {outcome.failed_step_index} after "
            f"{outcome.completed_steps} successful step(s){healed_suffix}: {outcome.error}"
        )
        # If the blocker step failed because its target looks removed,
        # show a manual-fix CTA.
        failed_idx = outcome.failed_step_index
        failed_result = next(
            (r for r in outcome.step_results if r.get("step_index") == failed_idx),
            None,
        )
        if failed_result and failed_result.get("removal_diagnostics"):
            with st.container(border=True):
                st.markdown(
                    "**The step that failed targets a field that appears to "
                    "have been removed, and it's not a step we can safely "
                    "skip (e.g. a click/submit, or a required fill).**"
                )
                st.caption(failed_result["removal_diagnostics"])
                cta_key = f"manual_fix_{sc.id}_{recording.id}_{failed_idx}"
                if st.button(
                    "✏ Add a step manually here",
                    key=cta_key,
                    type="primary",
                ):
                    st.session_state[f"_manual_fix_recording_{sc.id}"] = recording.id
                    st.session_state[f"_manual_fix_step_idx_{sc.id}"] = failed_idx
                    st.session_state.pop(f"_replay_outcome_{sc.id}", None)
                    st.rerun()
        # Identify heals that fired in the steps BEFORE the failure
        upstream_heals = []
        for sr in outcome.step_results:
            if sr.get("step_index", -1) >= (outcome.failed_step_index or 0):
                break
            if sr.get("healed"):
                upstream_heals.append(sr)
        if upstream_heals:
            st.warning(
                f"**{len(upstream_heals)} heal"
                f"{'' if len(upstream_heals) == 1 else 's'} "
                f"happened before this failure. Could one of them be wrong?**"
            )
            for sr in upstream_heals:
                heal = sr["healed"]
                old = heal.get("old_primary_locator") or {}
                new = heal.get("new_primary_locator") or {}
                candidates = heal.get("top_k_candidates") or []
                # The chosen heal is candidates[0]; offer retry with each of [1:]
                runners = candidates[1:3] if len(candidates) > 1 else []
                with st.container(border=True):
                    st.markdown(
                        f"**Step {sr['step_index']}** healed "
                        f"`{old.get('strategy')}:{old.get('value')}` → "
                        f"`{new.get('strategy')}:{new.get('value')}`  \n"
                        f":gray[confidence {heal.get('confidence', 0):.0%}]"
                    )
                    for idx, runner in enumerate(runners, start=1):
                        rl = runner.get("primary_locator", {})
                        rkey = f"retry_{sc.id}_{recording.id}_{sr['step_index']}_{idx}"
                        if st.button(
                            f"↻ Retry with `{rl.get('strategy')}:{rl.get('value')}` "
                            f"(score {runner.get('score', 0):.0%})",
                            key=rkey,
                        ):
                            fp_id = heal.get("fingerprint_id") or ""
                            st.session_state[f"_replay_overrides_{sc.id}"] = overrides
                            st.session_state[f"_force_runner_up_{sc.id}"] = {fp_id: idx}
                            st.session_state[f"_replay_target_{sc.id}"] = recording.id
                            st.session_state.pop(f"_replay_outcome_{sc.id}", None)
                            st.rerun()
    else:
        st.success(
            f"Replay completed all {outcome.completed_steps} steps{healed_suffix}. "
            f"Final URL: {outcome.final_url}"
        )
        if outcome.skipped_steps:
            n = len(outcome.skipped_steps)
            st.warning(
                f"⏭ **{n} step{'' if n == 1 else 's'} skipped because the "
                f"target field appears to have been removed from the page.** "
                f"The scenario completed without them."
            )
            with st.expander(f"View {n} skipped step{'' if n == 1 else 's'}"):
                for sk in outcome.skipped_steps:
                    st.markdown(
                        f"- **Step {sk['step_index']}** (`{sk['action']}`) — "
                        f"field `{sk['field_label'] or '(unnamed)'}` not found  \n"
                        f"  :gray[{sk['diagnostics']}]"
                    )
        if outcome.promoted_heals:
            n = len(outcome.promoted_heals)
            st.info(
                f"🩹 Recording updated to new baseline — {n} heal"
                f"{'' if n == 1 else 's'} promoted."
            )
            with st.expander(f"View {n} change{'' if n == 1 else 's'} / revert"):
                for ph in outcome.promoted_heals:
                    cols = st.columns([4, 1])
                    old = ph["old_primary_locator"]
                    new = ph["new_primary_locator"]
                    cols[0].markdown(
                        f"**Step {ph['step_index']}** — "
                        f"`{old['strategy']}:{old['value']}` "
                        f"→ `{new['strategy']}:{new['value']}`  \n"
                        f":gray[confidence {ph['confidence']:.0%} · method {ph['method']}]"
                    )
                    revert_key = f"revert_{sc.id}_{recording.id}_{ph['fingerprint_id']}"
                    if cols[1].button("↶ Revert", key=revert_key):
                        from core.replay import _revert_last_heal
                        _revert_last_heal(
                            scenario=sc,
                            recording_id=recording.id,
                            fingerprint_id=ph["fingerprint_id"],
                        )
                        # Invalidate cached outcome so banner clears on rerun
                        st.session_state.pop(f"_replay_outcome_{sc.id}", None)
                        st.rerun()
    if outcome.auto_filled_fields:
        if outcome.failed_step_index is None:
            # Auto-retry passed
            st.info(
                "**Submit was blocked by a new required field. We filled it "
                "in automatically and the scenario completed.**"
            )
            for af in outcome.auto_filled_fields:
                attrs = af["attributes"]
                label = attrs.get("nearest_label_text") or attrs.get("id") or "(unnamed)"
                st.markdown(
                    f"- **`{label}`** (`{attrs.get('tag')}`) — AI suggested value: "
                    f"`{af['value']}`"
                )
            cols = st.columns([2, 2, 6])
            if cols[0].button("💾 Save these steps to the recording",
                              key=f"save_n2_{sc.id}_{recording.id}",
                              type="primary"):
                from core.replay import _save_auto_filled_steps
                insert_before = (outcome.original_failure or {}).get("failed_step_index", 0)
                _save_auto_filled_steps(
                    scenario=sc,
                    recording_id=recording.id,
                    auto_filled=outcome.auto_filled_fields,
                    insert_before_step_index=insert_before,
                )
                st.success("Saved.")
                st.session_state.pop(f"_replay_outcome_{sc.id}", None)
                st.rerun()
            if cols[1].button("Discard", key=f"discard_n2_{sc.id}_{recording.id}"):
                st.session_state.pop(f"_replay_outcome_{sc.id}", None)
                st.rerun()
        else:
            # Auto-retry also failed
            st.error(
                "**A new required field was detected, but auto-filling it "
                "didn't make the scenario pass. The failure may not be from "
                "the missing field.**"
            )
            for af in outcome.auto_filled_fields:
                attrs = af["attributes"]
                label = attrs.get("nearest_label_text") or attrs.get("id") or "(unnamed)"
                st.markdown(f"- We tried `{label}` = `{af['value']}` — no luck.")
    _render_step_report(outcome, recording)
    btn_cols = st.columns([2, 2, 6])
    if btn_cols[0].button("↻ Run again", key=f"rerun_replay_{sc.id}"):
        st.session_state.pop(cache_key, None)
        st.rerun()
    if btn_cols[1].button("Close replay result", key=f"close_replay_{sc.id}"):
        st.session_state.pop(f"_replay_target_{sc.id}", None)
        st.session_state.pop(f"_replay_overrides_{sc.id}", None)
        st.session_state.pop(f"_replay_label_{sc.id}", None)
        st.session_state.pop(cache_key, None)
        st.rerun()


def _render_step_report(outcome, recording) -> None:
    """Render per-step pass/fail with inline screenshots.

    A step that ran the healer renders with a 🩹 icon and an expanded
    `Healed via …` block showing old → new locator, confidence, and the
    attributes that matched. A failed step where the healer ran but
    couldn't confidently match shows the healer's candidate diagnostics
    in the error block."""
    results = getattr(outcome, "step_results", None) or []
    if not results:
        return
    st.markdown("#### Step-by-step")
    by_index = {s.index: s for s in recording.steps}
    for r in results:
        step = by_index.get(r["step_index"])
        healed = r.get("healed")
        status = r["status"]
        if healed and status == "passed":
            icon = "🩹"
        else:
            icon = {
                "passed": "✅",
                "failed": "❌",
                "skipped": "⏭",
                "skipped_removed": "⏭",
            }.get(status, "•")
        label_parts = [f"Step {r['step_index']}", r.get("action", "")]
        if step is not None and step.element is not None:
            attrs = step.element.attributes or {}
            field = (
                attrs.get("aria_label") or attrs.get("name")
                or step.element.primary_locator.get("value")
            )
            if field:
                label_parts.append(field)
        if healed and status == "passed":
            label_parts.append(f"healed · {healed['confidence']:.0%}")
        if status == "skipped_removed":
            label_parts.append("field removed — skipped")
        header = f"{icon} " + " · ".join(p for p in label_parts if p)
        with st.expander(
            header,
            expanded=(status in ("failed", "skipped_removed") or bool(healed)),
        ):
            if r.get("value") not in (None, ""):
                st.caption(f"value: `{r['value']}`")
            if healed and status == "passed":
                _render_heal_block(healed)
            if r.get("error"):
                st.error(r["error"])
                if r.get("heal_diagnostics"):
                    st.caption(f"healer: {r['heal_diagnostics']}")
            if status == "skipped_removed" and r.get("removal_diagnostics"):
                st.info(
                    "**Field appears removed from the live page.** This step "
                    "was safely skipped because it's not required to complete "
                    "the scenario."
                )
                st.caption(f"healer: {r['removal_diagnostics']}")
            shot = r.get("screenshot_path")
            if shot and os.path.exists(shot):
                st.image(shot, use_container_width=True)
            elif shot:
                st.caption(f"(screenshot missing: {shot})")


def _render_heal_block(healed: dict) -> None:
    """Inline panel inside a healed step's expander.

    Shows old → new locator, confidence, the method (auto vs AI-confirmed),
    and which attributes matched. Keep it terse — the user is scanning
    many steps; deep diff lives behind a second expander."""
    method_label = {
        "auto": "automatic (heuristic match)",
        "ai-confirmed": "AI-confirmed (gray-zone)",
    }.get(healed.get("method", ""), healed.get("method", ""))
    old = healed.get("old_primary_locator") or {}
    new = healed.get("new_primary_locator") or {}
    st.markdown(
        f"**Healed via {method_label}** — confidence "
        f"`{healed.get('confidence', 0):.0%}`"
        + (f" (runner-up `{healed.get('runner_up_score', 0):.0%}`)"
           if healed.get('runner_up_score') else "")
    )
    st.markdown(
        f"locator: `{old.get('strategy', '?')}={old.get('value', '?')}` "
        f"→ `{new.get('strategy', '?')}={new.get('value', '?')}`"
    )
    matched_by = healed.get("matched_by") or []
    if matched_by:
        st.caption("matched on: " + ", ".join(matched_by))
    with st.expander("Candidate attributes (audit)"):
        st.json(healed.get("candidate_attrs") or {})


def _render_test_case_editor(sc, recording_id: str) -> None:
    """Minimal editor for creating a named test-case variant of a recording.

    A test case = (name, expected_outcome, per-fingerprint value overrides).
    Saved into sc.ai_test_cases. Execution wiring is intentionally deferred —
    this surface exists so the user can author variants the moment a recording
    lands, without bouncing through another screen."""
    rec_dict = next(
        (r for r in sc.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        st.error(f"Recording {recording_id!r} not found on this scenario.")
        st.session_state.pop(f"_addtc_target_{sc.id}", None)
        return

    st.subheader(f"New test case for *{rec_dict.get('name', recording_id)}*")
    # Only fill/select steps are data-bearing — clicks/navigations have no
    # value to override, so exclude them from the editor to keep the form
    # focused on what the user can actually vary.
    overridable = [
        s for s in rec_dict.get("steps", [])
        if s.get("action") in ("fill", "select") and s.get("element")
    ]
    if not overridable:
        st.info("This recording has no fill/select steps to vary.")
        if st.button("Back", key=f"back_addtc_{sc.id}"):
            st.session_state.pop(f"_addtc_target_{sc.id}", None)
            st.rerun()
        return

    with st.form(f"addtc_form_{sc.id}"):
        tc_name = st.text_input("Test case name", value="", key=f"tc_name_{sc.id}")
        tc_expected = st.selectbox(
            "Expected outcome", ["success", "failure"], key=f"tc_exp_{sc.id}",
        )
        st.caption("Override the recorded value per field (leave unchanged to reuse the recording's value):")
        overrides: dict[str, str] = {}
        for step in overridable:
            elem = step["element"]
            label = (
                elem.get("attributes", {}).get("aria_label")
                or elem.get("attributes", {}).get("name")
                or elem.get("primary_locator", {}).get("value")
                or elem["id"]
            )
            new_val = st.text_input(
                f"{step['action']}: {label}",
                value=step.get("value") or "",
                key=f"tc_ovr_{sc.id}_{step['index']}",
            )
            overrides[elem["id"]] = new_val
        col_save, col_cancel = st.columns([1, 1])
        save_clicked = col_save.form_submit_button("Save test case", type="primary")
        cancel_clicked = col_cancel.form_submit_button("Cancel")

    if cancel_clicked:
        st.session_state.pop(f"_addtc_target_{sc.id}", None)
        st.rerun()
    if save_clicked:
        if not tc_name.strip():
            st.error("Give the test case a name before saving.")
            return
        sc.ai_test_cases = list(sc.ai_test_cases) + [{
            "id": uuid.uuid4().hex[:8],
            "name": tc_name.strip(),
            "recording_id": recording_id,
            "expected_outcome": tc_expected,
            "overrides": overrides,
        }]
        save_scenario(DATA_SCENARIOS, sc)
        st.success(f"Saved test case *{tc_name.strip()}*.")
        st.session_state.pop(f"_addtc_target_{sc.id}", None)
        st.rerun()


def _overridable_steps(rec_dict: dict) -> list[dict]:
    return [
        s for s in rec_dict.get("steps", []) or []
        if s.get("action") in ("fill", "select") and s.get("element")
    ]


def _friendly_label(elem: dict) -> str:
    attrs = elem.get("attributes") or {}
    return (
        attrs.get("aria_label")
        or attrs.get("placeholder")
        or attrs.get("name")
        or attrs.get("id")
        or elem.get("primary_locator", {}).get("value")
        or elem["id"]
    )


def _render_excel_test_cases_uploader(sc, recording_id: str) -> None:
    """Bulk-create test cases from an Excel/CSV file.

    Expected columns: `name`, `expected_outcome`, plus one column per fill/select
    step whose header matches the step's friendly label (aria-label / placeholder
    / name / id). Missing columns simply leave that field at the recording's
    value. Unknown columns are reported but otherwise ignored."""
    import pandas as pd

    rec_dict = next(
        (r for r in sc.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        st.error(f"Recording {recording_id!r} not found on this scenario.")
        st.session_state.pop(f"_xls_target_{sc.id}", None)
        return

    st.subheader(f"Upload Excel test cases for *{rec_dict.get('name', recording_id)}*")
    overridable = _overridable_steps(rec_dict)
    if not overridable:
        st.info("This recording has no fill/select steps to vary.")
        if st.button("Back", key=f"xls_back_empty_{sc.id}"):
            st.session_state.pop(f"_xls_target_{sc.id}", None)
            st.rerun()
        return

    # Build label → fingerprint id map. If two steps share a label, the last one
    # wins, which is fine for the common case where the user only varies one of
    # the duplicates. The caption surfaces the column names they should use.
    label_to_fp: dict[str, str] = {}
    for step in overridable:
        label_to_fp[_friendly_label(step["element"])] = step["element"]["id"]

    expected_cols = ["name", "expected_outcome"] + list(label_to_fp.keys())
    st.caption("Expected columns:")
    st.code(", ".join(expected_cols), language="text")

    uploaded = st.file_uploader(
        "Excel or CSV", type=["xlsx", "xls", "csv"],
        key=f"xls_upload_{sc.id}_{recording_id}",
    )
    if uploaded is None:
        if st.button("Cancel", key=f"xls_cancel_pre_{sc.id}"):
            st.session_state.pop(f"_xls_target_{sc.id}", None)
            st.rerun()
        return

    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        if st.button("Cancel", key=f"xls_cancel_err_{sc.id}"):
            st.session_state.pop(f"_xls_target_{sc.id}", None)
            st.rerun()
        return

    cols = list(df.columns)
    unknown = [c for c in cols if c not in expected_cols]
    if unknown:
        st.warning(f"Ignoring unrecognized columns: {unknown}")
    if "name" not in cols:
        st.error("Required column `name` is missing.")
        return

    st.markdown(f"**Preview** — {len(df)} row(s)")
    st.dataframe(df, use_container_width=True)

    c1, c2, _ = st.columns([2, 2, 6])
    if c1.button(f"Save {len(df)} test case(s)", type="primary",
                 key=f"xls_save_{sc.id}", disabled=len(df) == 0):
        new_cases = []
        for idx, row in df.iterrows():
            tc_name = str(row.get("name", "") or "").strip() or f"Row {idx + 1}"
            exp = str(row.get("expected_outcome", "success") or "success").strip().lower()
            if exp not in ("success", "failure"):
                exp = "success"
            overrides: dict[str, str] = {}
            for label, fp_id in label_to_fp.items():
                if label not in cols:
                    continue
                val = row.get(label, "")
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    val = ""
                overrides[fp_id] = str(val)
            new_cases.append({
                "id": uuid.uuid4().hex[:8],
                "name": tc_name,
                "recording_id": recording_id,
                "expected_outcome": exp,
                "overrides": overrides,
                "source": "excel",
            })
        sc.ai_test_cases = list(sc.ai_test_cases) + new_cases
        save_scenario(DATA_SCENARIOS, sc)
        st.success(f"Saved {len(new_cases)} test case(s).")
        st.session_state.pop(f"_xls_target_{sc.id}", None)
        st.rerun()
    if c2.button("Cancel", key=f"xls_cancel_{sc.id}"):
        st.session_state.pop(f"_xls_target_{sc.id}", None)
        st.rerun()


def _render_ai_generator(sc, recording_id: str) -> None:
    """Card-review UX for AI-generated test case variants.

    Two phases in one function:
      1. controls (count + focus) → on Generate, call AIService and stash
         results in session state keyed by recording_id.
      2. review cards → checkboxes per case, with an inline expander for
         per-case edits. Save selected pushes accepted cases into
         sc.ai_test_cases; Regenerate clears and reruns; Cancel exits.
    """
    from core.ai_service import get_ai_service
    from core.recording import Recording

    rec_dict = next(
        (r for r in sc.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        st.error(f"Recording {recording_id!r} not found on this scenario.")
        st.session_state.pop(f"_gen_target_{sc.id}", None)
        return

    st.subheader(f"🤖 Generate test cases for *{rec_dict.get('name', recording_id)}*")
    svc = get_ai_service()
    if not svc.is_available():
        st.error(
            "AI service unavailable. Check Ollama is running and the model "
            f"({svc.model}) is pulled."
        )
        if svc.last_error:
            st.caption(f"Last error: {svc.last_error}")
        if st.button("Back", key=f"gen_back_unavail_{sc.id}"):
            st.session_state.pop(f"_gen_target_{sc.id}", None)
            st.rerun()
        return

    suggestions_key = f"_gen_suggestions_{sc.id}"
    selection_key = f"_gen_selection_{sc.id}"
    suggestions = st.session_state.get(suggestions_key)

    # ── Phase 1: controls ────────────────────────────────────────────────
    if suggestions is None:
        c1, c2 = st.columns([1, 3])
        count = c1.number_input(
            "Count", min_value=1, max_value=20, value=5, key=f"gen_count_{sc.id}",
        )
        focus = c2.multiselect(
            "Focus areas",
            ["Negative", "Boundary", "Invalid format", "Happy path variants"],
            default=["Negative", "Boundary"],
            key=f"gen_focus_{sc.id}",
        )
        col_go, col_cancel = st.columns([1, 1])
        if col_go.button("Generate", type="primary", key=f"gen_go_{sc.id}"):
            recording = Recording.from_dict(rec_dict)
            with st.spinner(f"Asking {svc.model} for {count} variants…"):
                result = svc.suggest_test_cases_for_recording(
                    recording, int(count), focus,
                )
            if result is None:
                st.error("AI returned an unparseable response.")
                if svc.last_error:
                    st.caption(f"Last error: {svc.last_error}")
                return
            if not result:
                st.warning(
                    "No variants were generated. The recording may have no "
                    "fill/select steps the AI could vary."
                )
                return
            st.session_state[suggestions_key] = result
            st.session_state[selection_key] = {i: True for i in range(len(result))}
            st.rerun()
        if col_cancel.button("Cancel", key=f"gen_cancel_p1_{sc.id}"):
            st.session_state.pop(f"_gen_target_{sc.id}", None)
            st.rerun()
        return

    # ── Phase 2: review cards ────────────────────────────────────────────
    st.caption(
        f"AI suggested {len(suggestions)} variants. Toggle the ones to save, "
        "expand to inspect or edit."
    )
    selection = st.session_state.setdefault(
        selection_key, {i: True for i in range(len(suggestions))},
    )
    # Build a label map fingerprint_id → human label, sourced from the
    # recording, so the card view never shows raw "el-7" identifiers.
    label_by_fp: dict[str, str] = {}
    for step in rec_dict.get("steps", []) or []:
        elem = step.get("element") or {}
        if not elem:
            continue
        attrs = elem.get("attributes") or {}
        label_by_fp[elem["id"]] = (
            attrs.get("aria_label")
            or attrs.get("placeholder")
            or attrs.get("name")
            or attrs.get("id")
            or elem["id"]
        )

    for i, case in enumerate(suggestions):
        outcome_icon = "✗" if case["expected_outcome"] == "failure" else "✓"
        header_cols = st.columns([0.5, 9])
        selection[i] = header_cols[0].checkbox(
            "keep", value=selection.get(i, True),
            key=f"gen_keep_{sc.id}_{i}", label_visibility="collapsed",
        )
        with header_cols[1].expander(
            f"{outcome_icon} **{case['name']}** — expected {case['expected_outcome']}",
            expanded=False,
        ):
            if case.get("rationale"):
                st.caption(case["rationale"])
            for fp_id, val in case["overrides"].items():
                label = label_by_fp.get(fp_id, fp_id)
                new_val = st.text_input(
                    label, value=val,
                    key=f"gen_ovr_{sc.id}_{i}_{fp_id}",
                )
                case["overrides"][fp_id] = new_val
            new_name = st.text_input(
                "name", value=case["name"], key=f"gen_name_{sc.id}_{i}",
            )
            new_outcome = st.selectbox(
                "expected", ["success", "failure"],
                index=0 if case["expected_outcome"] == "success" else 1,
                key=f"gen_outc_{sc.id}_{i}",
            )
            case["name"] = new_name
            case["expected_outcome"] = new_outcome

    selected_count = sum(1 for v in selection.values() if v)
    cs1, cs2, cs3 = st.columns([2, 2, 2])
    if cs1.button(
        f"Save {selected_count} selected", type="primary",
        disabled=selected_count == 0, key=f"gen_save_{sc.id}",
    ):
        accepted = [
            {
                "id": uuid.uuid4().hex[:8],
                "name": suggestions[i]["name"],
                "recording_id": recording_id,
                "expected_outcome": suggestions[i]["expected_outcome"],
                "overrides": suggestions[i]["overrides"],
                "source": "ai",
                "rationale": suggestions[i].get("rationale", ""),
            }
            for i, keep in selection.items() if keep
        ]
        sc.ai_test_cases = list(sc.ai_test_cases) + accepted
        save_scenario(DATA_SCENARIOS, sc)
        st.success(f"Saved {len(accepted)} test case(s).")
        for k in (suggestions_key, selection_key, f"_gen_target_{sc.id}"):
            st.session_state.pop(k, None)
        st.rerun()
    if cs2.button("Regenerate", key=f"gen_regen_{sc.id}"):
        for k in (suggestions_key, selection_key):
            st.session_state.pop(k, None)
        st.rerun()
    if cs3.button("Cancel", key=f"gen_cancel_p2_{sc.id}"):
        for k in (suggestions_key, selection_key, f"_gen_target_{sc.id}"):
            st.session_state.pop(k, None)
        st.rerun()


def render(scenario_id: str):
    sc = load_scenario(DATA_SCENARIOS, scenario_id)

    if st.button("← Back to list", key=f"back_{sc.id}"):
        st.session_state.pop("_open_scenario", None)
        st.rerun()

    st.title(sc.name)
    if sc.kind == "recorded":
        from core.applications import load_application
        try:
            app = load_application("data/applications", sc.application_id)
            st.caption(f"Application: {app.name} · {app.base_url_pattern}")
        except Exception:
            st.caption(f"Application: {sc.application_id}")
        _render_recorded_scenario(sc)
        return

    if sc.base_url:
        st.caption(f"Target page: {sc.base_url}")
    else:
        st.caption("No base URL set — pick a scanned page in Settings.")

    if st.button(f"▶ Run scenario", type="primary", key=f"run_{sc.id}",
                 disabled=not (sc.base_url or sc.kind == "multi-page")):
        if sc.kind == "single-page" and not sc.base_url:
            st.error("Set a base URL in Settings before running.")
        elif sc.kind == "multi-page" and not (sc.pages or []):
            st.error("Add at least one page in Settings before running.")
        else:
            with st.spinner(f"Running {sc.name}..."):
                result = _run_scenario(sc)
            _persist_run(sc, result)
            _render_run_result(sc, result)

    if sc.kind == "multi-page":
        page_labels = [
            f"{i + 1}. {p.get('base_url') or '(unset)'}"
            for i, p in enumerate(sc.pages or [])
        ]
        if not page_labels:
            st.warning("This multi-page scenario has no pages yet. "
                       "Use the Settings tab to add some.")
            page_labels = ["(no pages)"]
        active_label = st.segmented_control(
            "Page", options=page_labels,
            default=page_labels[0],
            key=f"_active_page_{sc.id}",
        )
        active_idx = page_labels.index(active_label) if active_label in page_labels else 0
        view = _PageView(sc, active_idx if sc.pages else None)

        def _save_view_steps(new_steps):
            if sc.pages:
                sc.pages[active_idx]["steps"] = new_steps
                save_scenario(DATA_SCENARIOS, sc)

        def _save_view_dataset(new_rows):
            if sc.pages:
                sc.pages[active_idx]["dataset"] = new_rows
                save_scenario(DATA_SCENARIOS, sc)
    else:
        view = _PageView(sc, None)
        _save_view_steps = lambda s: _save_steps(sc, s)
        _save_view_dataset = lambda d: _save_dataset(sc, d)

    real_recordings = [
        r for r in (sc.recordings or [])
        if isinstance(r, dict) and r.get("id") and r.get("id") != "placeholder"
    ]
    has_rec_tab = bool(real_recordings)

    if has_rec_tab:
        tab1, tab2, tab3, tab4, tab_rec = st.tabs(
            ["Steps", "Dataset", "Runs", "Settings", "Recording steps"]
        )
    else:
        tab1, tab2, tab3, tab4 = st.tabs(["Steps", "Dataset", "Runs", "Settings"])

    with tab1: render_steps(view, _save_view_steps)
    with tab2: render_dataset(view, _save_view_dataset)
    with tab3: render_runs(sc)
    with tab4: render_settings(sc)

    if has_rec_tab:
        with tab_rec:
            if len(real_recordings) == 1:
                rec_id = real_recordings[0]["id"]
            else:
                names = {r["id"]: r.get("name", r["id"]) for r in real_recordings}
                rec_id = st.selectbox(
                    "Recording",
                    options=list(names.keys()),
                    format_func=lambda x: names[x],
                    key=f"rec_picker_{sc.id}",
                )

            def _on_save_rec(updated_rec):
                for i, r in enumerate(sc.recordings):
                    if r.get("id") == rec_id:
                        sc.recordings[i] = updated_rec.to_dict()
                        break
                save_scenario(DATA_SCENARIOS, sc)

            scroll_idx = st.session_state.get(f"_manual_fix_step_idx_{sc.id}")
            target_rec = st.session_state.get(f"_manual_fix_recording_{sc.id}")
            recording_editor.render(
                sc, rec_id, _on_save_rec,
                scroll_to_step_index=(
                    scroll_idx if target_rec == rec_id else None
                ),
            )
            if scroll_idx is not None and target_rec == rec_id:
                # Show once, then clear so the highlight clears on next rerun.
                st.session_state.pop(f"_manual_fix_recording_{sc.id}", None)
                st.session_state.pop(f"_manual_fix_step_idx_{sc.id}", None)
