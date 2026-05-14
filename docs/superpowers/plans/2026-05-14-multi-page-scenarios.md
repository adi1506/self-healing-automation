# Multi-Page Scenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire multi-page testing into the existing Scenario flow via an inline `pages[]` schema, reusing the single-page Steps/Dataset/Settings UI per page through a segmented page selector.

**Architecture:** Replace the unused `recipe_refs` requirement with an inline `pages[]` list. A new dispatcher in [ui/scenarios/detail.py](ui/scenarios/detail.py) branches on `kind` and, for `multi-page`, walks pages in one Playwright session — calling either `Setter.set_fields_on_page` (new helper extracted from `set_fields`) for dataset rows, or `RecipeExecutor.execute` for steps — running an explicit per-page-boundary transition between pages. The same `core/healer.py` user-triggered Library heal continues to work per page; no runtime healing changes.

**Tech Stack:** Python 3, Streamlit, Playwright (async), PyYAML, openpyxl, pytest.

**Spec:** [docs/superpowers/specs/2026-05-14-multi-page-scenarios-design.md](../specs/2026-05-14-multi-page-scenarios-design.md)

---

## File map

**Modify:**
- [core/scenarios.py](core/scenarios.py) — add `pages` field, validation for multi-page
- [core/setter.py](core/setter.py) — extract `set_fields_on_page(page, ...)` helper
- [core/excel_manager.py](core/excel_manager.py) — add `Page Index` column to Run Results sheet
- [ui/scenarios/detail.py](ui/scenarios/detail.py) — multi-page dispatch, runner, persistence, rendering, page-selector wiring
- [pages/3_scenarios.py](pages/3_scenarios.py) — Kind radio + ordered page picker on New scenario
- [ui/scenarios/steps_tab.py](ui/scenarios/steps_tab.py) — accept `(steps, base_url, save_fn)` so multi-page binds per-page
- [ui/scenarios/dataset_tab.py](ui/scenarios/dataset_tab.py) — same, plus the multi-page caption
- [ui/scenarios/settings_tab.py](ui/scenarios/settings_tab.py) — add Transitions subsection for multi-page
- [tests/test_scenarios.py](tests/test_scenarios.py) — extend with multi-page tests

**Create:**
- [tests/test_setter_on_page.py](tests/test_setter_on_page.py) — verify `set_fields_on_page` works against an externally-opened page; verify `set_fields(url, ...)` regression
- [tests/test_multi_page_runner.py](tests/test_multi_page_runner.py) — end-to-end multi-page run against local fixtures
- [test_form/page_a.html](test_form/page_a.html), [test_form/page_b.html](test_form/page_b.html) — two-page fixture with an HTML link transition

**Touch (none):** `core/healer.py`, `core/recipes.py`, `core/scenario_migration.py` (the back-compat warning in §4.3 of the spec is implicit — legacy `recipe_refs` scenarios load fine because the field is still on the dataclass).

---

## Task 1: Schema — add `pages` field and multi-page validation

**Files:**
- Modify: [core/scenarios.py](core/scenarios.py)
- Modify: [tests/test_scenarios.py](tests/test_scenarios.py)

- [ ] **Step 1.1: Write the failing tests for the new schema**

Append to [tests/test_scenarios.py](tests/test_scenarios.py):

```python
def test_multi_page_scenario_with_pages_round_trips(tmp_path):
    sc = Scenario(
        id="journey", name="Login + checkout", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {
                "base_url": "https://e.com/login",
                "steps": [{"action": "fill", "target": "email", "value": "a@b.co"}],
                "dataset": [],
                "transition": {
                    "target": "submit_button", "wait_for": "url_contains",
                    "value": "/profile", "timeout_ms": 30000,
                },
            },
            {
                "base_url": "https://e.com/profile",
                "steps": [{"action": "click", "target": "logout"}],
                "dataset": [],
            },
        ],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "journey")
    assert loaded.kind == "multi-page"
    assert len(loaded.pages) == 2
    assert loaded.pages[0]["transition"]["value"] == "/profile"
    assert "transition" not in loaded.pages[1]


def test_multi_page_rejects_empty_pages(tmp_path):
    sc = Scenario(
        id="bad", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success", pages=[],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for empty pages")


def test_multi_page_rejects_non_last_page_without_transition(tmp_path):
    sc = Scenario(
        id="bad2", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {"base_url": "https://e.com/a", "steps": [], "dataset": []},
            {"base_url": "https://e.com/b", "steps": [], "dataset": []},
        ],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for missing transition")


def test_multi_page_rejects_page_without_base_url(tmp_path):
    sc = Scenario(
        id="bad3", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[{"base_url": "", "steps": [], "dataset": []}],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for missing base_url")


def test_legacy_multi_page_with_recipe_refs_still_loads(tmp_path):
    """Back-compat: old multi-page scenarios that only have recipe_refs must
    still load (validation passes when pages[] is empty AND recipe_refs is
    populated). They simply won't run via the new runner."""
    sc = Scenario(
        id="legacy", name="Legacy journey", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success", recipe_refs=["A", "B"],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "legacy")
    assert loaded.recipe_refs == ["A", "B"]
    assert loaded.pages == []
```

The existing `test_multi_page_scenario_stores_recipe_refs` test at [tests/test_scenarios.py:51-59](tests/test_scenarios.py#L51-L59) covers the legacy shape and must continue to pass.

- [ ] **Step 1.2: Run the new tests to confirm they fail**

Run: `python -m pytest tests/test_scenarios.py -v`
Expected: the four new tests FAIL with `TypeError: ... unexpected keyword argument 'pages'` (or similar). `test_multi_page_scenario_stores_recipe_refs` still passes.

- [ ] **Step 1.3: Add `pages` to the Scenario dataclass and update validation**

Edit [core/scenarios.py](core/scenarios.py):

Replace the dataclass (lines ~15-29) with:

```python
@dataclass
class Scenario:
    id: str
    name: str
    kind: str
    base_url: str
    steps: list[dict]
    dataset: list[dict]
    expected_outcome: str
    recipe_refs: list[str] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
```

Replace `_validate` (lines ~36-52) with:

```python
def _validate(sc: Scenario) -> None:
    if not sc.id or not sc.id.replace("_", "").replace("-", "").isalnum():
        raise ScenarioValidationError(f"id must be alphanumeric/underscore/hyphen, got {sc.id!r}")
    if sc.kind not in VALID_KINDS:
        raise ScenarioValidationError(f"kind must be in {VALID_KINDS}, got {sc.kind!r}")
    if sc.expected_outcome not in VALID_OUTCOMES:
        raise ScenarioValidationError(
            f"expected_outcome must be in {VALID_OUTCOMES}, got {sc.expected_outcome!r}"
        )
    if sc.kind == "single-page":
        if not sc.steps:
            raise ScenarioValidationError("single-page scenarios must have at least one step")
        if not sc.base_url:
            raise ScenarioValidationError("single-page scenarios require base_url")
        return

    # multi-page: accept either the new pages[] shape OR the legacy
    # recipe_refs shape (read-only — legacy scenarios load but the new
    # runner only consumes pages[]).
    if sc.pages:
        for i, p in enumerate(sc.pages):
            if not p.get("base_url"):
                raise ScenarioValidationError(
                    f"pages[{i}]: base_url is required"
                )
            is_last = (i == len(sc.pages) - 1)
            if not is_last and not p.get("transition"):
                raise ScenarioValidationError(
                    f"pages[{i}]: transition is required (only the last page may omit it)"
                )
        return

    if sc.recipe_refs:
        return  # legacy shape — accepted but unrunnable by the new runner

    raise ScenarioValidationError(
        "multi-page scenarios require pages[] (or legacy recipe_refs)"
    )
```

- [ ] **Step 1.4: Run all scenario tests**

Run: `python -m pytest tests/test_scenarios.py -v`
Expected: all tests PASS, including the four new ones and the legacy `test_multi_page_scenario_stores_recipe_refs`.

- [ ] **Step 1.5: Commit**

```bash
git add core/scenarios.py tests/test_scenarios.py
git commit -m "feat(scenarios): add pages[] schema for multi-page

Adds an inline pages[] field to Scenario. multi-page validation now
accepts either the new pages[] shape (each page has base_url, steps,
dataset, plus transition on all-but-last) or the legacy recipe_refs
shape for back-compat. No migration of existing files is needed —
single-page scenarios are untouched and legacy multi-page scenarios
continue to load (the new runner will ignore them)."
```

---

## Task 2: Setter — extract `set_fields_on_page` helper

**Files:**
- Modify: [core/setter.py](core/setter.py)
- Create: [tests/test_setter_on_page.py](tests/test_setter_on_page.py)

Goal: a multi-page run drives several pages from one open Playwright session. Today's `Setter._set_fields_async` ([core/setter.py:114](core/setter.py#L114)) launches its own browser, which would clobber session state between pages. Extract the inner per-page logic so it can be called against a page the runner already owns.

- [ ] **Step 2.1: Write the failing test for `set_fields_on_page`**

Create [tests/test_setter_on_page.py](tests/test_setter_on_page.py):

```python
"""Verify Setter.set_fields_on_page works against an externally-opened page.

The multi-page runner walks several URLs in one browser session, so the
field-setting logic must accept an existing Playwright page rather than
launching its own browser. This test loads a local HTML fixture in an
externally-managed browser, hands the page to the new helper, and asserts
the same result shape as Setter.set_fields would return.
"""
from __future__ import annotations

import asyncio
import os
import pathlib

from playwright.async_api import async_playwright

from core.browser_launch import launch_browser_and_page
from core.scanner import Scanner
from core.setter import Setter


FIXTURE = pathlib.Path(__file__).parent.parent / "test_form" / "sample_form.html"


def _file_url(p: pathlib.Path) -> str:
    return p.absolute().as_uri()


def test_set_fields_on_page_against_externally_opened_page():
    if not FIXTURE.exists():
        import pytest
        pytest.skip(f"fixture {FIXTURE} missing")

    url = _file_url(FIXTURE)
    scanner = Scanner()
    elements = scanner.scan(url)
    assert elements, "fixture should expose at least one scannable element"

    setter = Setter()
    test_data = {e["element_name"]: "x" for e in elements
                 if e["element_type"] not in ("button",)}

    async def _run():
        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            results = await setter.set_fields_on_page(
                page, elements, test_data, click_submit=False,
            )
            await browser.close()
            return results

    results = asyncio.get_event_loop().run_until_complete(_run())
    assert isinstance(results, list)
    # Every editable field in test_data should produce a result row.
    assert len(results) == len(test_data)
    for r in results:
        assert "status" in r
        assert "element_name" in r


def test_set_fields_url_path_still_works():
    """Regression: existing Setter.set_fields(url, ...) callers must keep
    working — the runner re-implementation must not break single-page runs."""
    if not FIXTURE.exists():
        import pytest
        pytest.skip(f"fixture {FIXTURE} missing")

    url = _file_url(FIXTURE)
    scanner = Scanner()
    elements = scanner.scan(url)
    setter = Setter()
    test_data = {e["element_name"]: "y" for e in elements
                 if e["element_type"] not in ("button",)}

    results = setter.set_fields(url, elements, test_data, click_submit=False)
    assert isinstance(results, list)
    assert len(results) == len(test_data)
```

- [ ] **Step 2.2: Run the test to confirm it fails**

Run: `python -m pytest tests/test_setter_on_page.py -v`
Expected: FAIL with `AttributeError: 'Setter' object has no attribute 'set_fields_on_page'`.

- [ ] **Step 2.3: Extract `set_fields_on_page` in `core/setter.py`**

In [core/setter.py](core/setter.py), replace `_set_fields_async` (currently the body of `set_fields`'s async work, [core/setter.py:114-end-of-method](core/setter.py)) with two methods. The body that today runs after `await page.goto(...)` becomes the new `set_fields_on_page`; `_set_fields_async` shrinks to "launch browser, goto, delegate, close":

```python
async def set_fields_on_page(
    self,
    page,
    element_map: list[dict],
    test_data: dict,
    screenshot_dir: str | None = None,
    run_id: str | None = None,
    click_submit: bool = False,
) -> list[dict]:
    """Run the field-setting logic against an already-open Playwright page.

    The browser/page lifetime is the caller's responsibility. Used by the
    multi-page runner so cookies/localStorage carry across pages within
    one session. Single-page callers should keep using `set_fields(url, ...)`,
    which launches its own browser and delegates here.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    await _wait_for_interactive(page, deadline_seconds=12)

    results = []

    for elem in element_map:
        elem_name = elem["element_name"]
        elem_type = elem["element_type"]

        if elem_type in NON_EDITABLE_TYPES:
            continue
        if elem_name not in test_data:
            continue
        value = "" if test_data[elem_name] is None else str(test_data[elem_name])

        handle = await self._find_element(page, elem)
        if not handle:
            results.append({
                "element_name": elem_name,
                "expected_value": value,
                "actual_value": "ELEMENT NOT FOUND",
                "status": "FAIL",
            })
            continue

        try:
            applied = await self._set_value(page, handle, elem, value)
        except SelectValueError as e:
            results.append({
                "element_name": elem_name,
                "expected_value": value,
                "actual_value": f"VALUE NOT IN OPTIONS: {e}",
                "status": "FAIL",
            })
            continue
        except PlaywrightError as e:
            reason = str(e).splitlines()[0][:160]
            results.append({
                "element_name": elem_name,
                "expected_value": value,
                "actual_value": f"BROWSER BLOCKED: {reason}",
                "status": "BLOCKED",
            })
            continue

        actual = await self._read_value(page, handle, elem)
        results.append({
            "element_name": elem_name,
            "expected_value": value,
            "actual_value": actual,
            "status": "PASS" if actual == applied else "FAIL",
        })

    self.last_form_rejected = None
    if click_submit:
        await self._run_submit_probe(page, element_map, screenshot_dir, run_id)

    return results

async def _set_fields_async(
    self,
    url: str,
    element_map: list[dict],
    test_data: dict,
    screenshot_dir: str | None = None,
    run_id: str | None = None,
    click_submit: bool = False,
) -> list[dict]:
    async with async_playwright() as p:
        browser, page = await launch_browser_and_page(p)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            return await self.set_fields_on_page(
                page, element_map, test_data, screenshot_dir, run_id, click_submit,
            )
        finally:
            await browser.close()
```

The submit-probe section that today lives inline at the end of `_set_fields_async` (the `if click_submit:` block, lines ~203 onward, the part that installs `INSTALL_SUBMIT_PROBES_JS`, finds the submit button, clicks, reads `READ_SUBMIT_PROBES_JS`, takes the screenshot, and sets `self.last_form_rejected`) must be moved verbatim into a new method `async def _run_submit_probe(self, page, element_map, screenshot_dir, run_id)`. Cut the existing block from `_set_fields_async` and paste it into the new method, replacing the `self.last_form_rejected = None` line at the top of the probe with `# caller has reset self.last_form_rejected to None already` (the reset moves up into `set_fields_on_page`).

After the refactor, the only thing `_set_fields_async` does is: launch browser → goto → call `set_fields_on_page` → close browser. The behavior of `setter.set_fields(url, ...)` is unchanged from a caller's perspective.

- [ ] **Step 2.4: Run the new tests AND the existing setter tests**

Run: `python -m pytest tests/test_setter.py tests/test_setter_on_page.py -v`
Expected: all PASS. If `tests/test_setter.py` fails, the cut-and-paste of the submit-probe block went wrong — re-check that `self.last_form_rejected` is reset exactly once per call (in `set_fields_on_page` before the `if click_submit` branch) and set inside the probe.

- [ ] **Step 2.5: Commit**

```bash
git add core/setter.py tests/test_setter_on_page.py
git commit -m "refactor(setter): extract set_fields_on_page helper

Splits Setter._set_fields_async into (1) set_fields_on_page that drives
an externally-opened Playwright page and (2) _set_fields_async that
launches a browser and delegates. The submit-probe block becomes its
own method. No behaviour change for set_fields(url, ...) — the
multi-page runner needs the new helper to keep one browser open
across pages."
```

---

## Task 3: Excel manager — add `Page Index` column to Run Results

**Files:**
- Modify: [core/excel_manager.py](core/excel_manager.py)
- Modify: [tests/test_excel_manager.py](tests/test_excel_manager.py)

- [ ] **Step 3.1: Write the failing tests**

Append to [tests/test_excel_manager.py](tests/test_excel_manager.py):

```python
def test_run_results_round_trip_with_page_index(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    url = "https://e.com/login"
    em.append_run_result(url, {
        "run_id": "abc123", "timestamp": "2026-05-14T10:00:00",
        "test_case_name": "Multi-page login", "row_label": "Page 1",
        "element_name": "email", "expected_value": "a@b.co",
        "actual_value": "a@b.co", "status": "PASS",
        "screenshot": "", "page_index": 0,
    })
    em.append_run_result(url, {
        "run_id": "abc123", "timestamp": "2026-05-14T10:00:05",
        "test_case_name": "Multi-page login", "row_label": "Page 2",
        "element_name": "phone", "expected_value": "1234",
        "actual_value": "1234", "status": "PASS",
        "screenshot": "", "page_index": 1,
    })
    rows = em.read_run_results(url)
    assert len(rows) == 2
    assert rows[0]["page_index"] in (0, "0")
    assert rows[1]["page_index"] in (1, "1")


def test_run_results_back_compat_without_page_index(tmp_path):
    """Existing callers (single-page runs) don't pass page_index. The
    column must default to 0 / empty without crashing."""
    em = ExcelManager(data_dir=str(tmp_path))
    em.append_run_result("https://e.com/x", {
        "run_id": "r", "timestamp": "t", "test_case_name": "tc",
        "row_label": "", "element_name": "n",
        "expected_value": "e", "actual_value": "a", "status": "PASS",
        "screenshot": "",
    })
    rows = em.read_run_results("https://e.com/x")
    assert len(rows) == 1
```

- [ ] **Step 3.2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_excel_manager.py -v -k "page_index or without_page_index"`
Expected: the first test FAILs (`page_index` column not in sheet); the second may pass already.

- [ ] **Step 3.3: Add the column**

Edit [core/excel_manager.py](core/excel_manager.py). Replace the `RUN_RESULTS_HEADERS` constant (line ~45-48) with:

```python
RUN_RESULTS_HEADERS = [
    "Run ID", "Timestamp", "Test Case Name", "Element Name",
    "Expected Value", "Actual Value", "Status", "Screenshot", "Row Label",
    "Page Index",
]
```

The corresponding dict-key order is implied by `_append_to_sheet`. Find `_append_to_sheet` and verify it maps headers → dict keys via a case/space-insensitive lookup. If it does not, also update the key mapping. Specifically: if `_append_to_sheet` builds its row by iterating `headers` and calling `result.get(header_to_key(h))` with `header_to_key(h) = h.lower().replace(" ", "_")`, then dict key `"page_index"` lines up with header `"Page Index"` automatically — no further change needed.

To confirm, grep for `_append_to_sheet`:

```bash
grep -n "_append_to_sheet" core/excel_manager.py
```

Read the function and verify the mapping. If it uses an explicit key list rather than auto-derivation, add `"page_index"` to that list in position 10 (after `"row_label"`).

Default behaviour: when `append_run_result` is called without `page_index`, the value should land as `0` so old reads stay consistent. If `_append_to_sheet` writes `None` for missing keys, wrap the call: in `append_run_result` ([core/excel_manager.py:362](core/excel_manager.py#L362)), add:

```python
def append_run_result(self, url: str, result: dict):
    """Append a run result row to the Run Results sheet."""
    result = {**result, "page_index": result.get("page_index", 0)}
    self._append_to_sheet(url, "Run Results", result, RUN_RESULTS_HEADERS)
```

- [ ] **Step 3.4: Run the tests**

Run: `python -m pytest tests/test_excel_manager.py -v`
Expected: all PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/excel_manager.py tests/test_excel_manager.py
git commit -m "feat(excel): add Page Index column to Run Results

Multi-page runs need to attribute each result row to the page it came
from. append_run_result defaults page_index to 0 so single-page
callers and pre-existing workbooks read back unchanged."
```

---

## Task 4: Multi-page runner — `_run_multi_page_scenario`

**Files:**
- Modify: [ui/scenarios/detail.py](ui/scenarios/detail.py)
- Create: [test_form/page_a.html](test_form/page_a.html)
- Create: [test_form/page_b.html](test_form/page_b.html)
- Create: [tests/test_multi_page_runner.py](tests/test_multi_page_runner.py)

- [ ] **Step 4.1: Create the two-page fixture**

Create [test_form/page_a.html](test_form/page_a.html):

```html
<!doctype html>
<html><head><title>Page A — login</title></head>
<body>
<form>
  <label>Email: <input id="email" name="email" type="email" required></label>
  <button id="go_b" type="button" onclick="window.location.href='page_b.html'">
    Continue
  </button>
</form>
</body></html>
```

Create [test_form/page_b.html](test_form/page_b.html):

```html
<!doctype html>
<html><head><title>Page B — confirm</title></head>
<body>
<form>
  <label>Phone: <input id="phone" name="phone" type="tel" required></label>
  <button id="submit_b" type="submit">Submit</button>
</form>
</body></html>
```

- [ ] **Step 4.2: Write the failing runner test**

Create [tests/test_multi_page_runner.py](tests/test_multi_page_runner.py):

```python
"""End-to-end test for the multi-page runner.

Drives two local HTML pages in one Playwright session:
  Page A: fills `email`, clicks `Continue` -> navigates to Page B
  Page B: fills `phone`, run ends.

The fixture uses a button with an inline JS onclick rather than a real
<a href> so the transition target is a real scanned element with an
element_name (buttons are scanned but not editable, so they're a valid
transition target).
"""
from __future__ import annotations

import pathlib

from core.excel_manager import ExcelManager
from core.scanner import Scanner
from core.scenarios import Scenario
from ui.scenarios.detail import _run_multi_page_scenario


FIXTURE_A = pathlib.Path(__file__).parent.parent / "test_form" / "page_a.html"
FIXTURE_B = pathlib.Path(__file__).parent.parent / "test_form" / "page_b.html"


def _file_url(p: pathlib.Path) -> str:
    return p.absolute().as_uri()


def test_multi_page_runner_walks_two_pages(tmp_path, monkeypatch):
    if not FIXTURE_A.exists() or not FIXTURE_B.exists():
        import pytest
        pytest.skip("fixtures missing")

    # Use a tmp scans dir so we don't pollute real data.
    monkeypatch.chdir(tmp_path)
    scans_dir = tmp_path / "data" / "scans"
    scans_dir.mkdir(parents=True)

    # Scan both pages and persist element maps so the runner can resolve targets.
    em = ExcelManager(data_dir=str(scans_dir))
    scanner = Scanner()
    url_a = _file_url(FIXTURE_A)
    url_b = _file_url(FIXTURE_B)
    elements_a = scanner.scan(url_a)
    elements_b = scanner.scan(url_b)
    em.save_element_map(url_a, elements_a)
    em.save_element_map(url_b, elements_b)

    # Find the button element_name on page A — that's our transition target.
    button_a = next(e for e in elements_a if e["element_type"] == "button")

    sc = Scenario(
        id="two_page", name="Two-page journey", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {
                "base_url": url_a,
                "steps": [],
                "dataset": [{"__expected_outcome": "success", "email": "a@b.co"}],
                "transition": {
                    "target": button_a["element_name"],
                    "wait_for": "url_contains",
                    "value": "page_b.html",
                    "timeout_ms": 30000,
                },
            },
            {
                "base_url": url_b,
                "steps": [],
                "dataset": [{"__expected_outcome": "success", "phone": "5551234"}],
            },
        ],
    )

    result = _run_multi_page_scenario(sc, data_scans_dir=str(scans_dir))
    assert result["mode"] == "multi-page"
    assert len(result["page_outcomes"]) == 2
    assert result["page_outcomes"][0]["page_status"] == "PASS"
    assert result["page_outcomes"][0]["transition_status"] == "PASS"
    assert result["page_outcomes"][1]["page_status"] == "PASS"
    assert result["scenario_status"] == "PASS"
```

(Note: `em.save_element_map` is the function name in the codebase — verify it exists; if it's `write_element_map`, adjust the test accordingly.)

Quick verification:

```bash
grep -n "def save_element_map\|def write_element_map" core/excel_manager.py
```

Use whichever name matches.

- [ ] **Step 4.3: Run the test to confirm it fails**

Run: `python -m pytest tests/test_multi_page_runner.py -v`
Expected: FAIL with `ImportError: cannot import name '_run_multi_page_scenario'` (or similar).

- [ ] **Step 4.4: Implement `_run_multi_page_scenario` in [ui/scenarios/detail.py](ui/scenarios/detail.py)**

Add this function (place it directly after `_run_dataset`, around line 188):

```python
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
```

Add the dispatcher at the top of the existing `_run_scenario` function (around [ui/scenarios/detail.py:48](ui/scenarios/detail.py#L48)). Replace the first lines of `_run_scenario`'s body with:

```python
def _run_scenario(sc):
    if sc.kind == "multi-page":
        return _run_multi_page_scenario(sc)
    # ... existing single-page body unchanged from here
    em = ExcelManager(data_dir=DATA_SCANS)
    elements = em.read_element_map(sc.base_url) if sc.base_url else []
    # ... etc
```

Verify imports at the top of [ui/scenarios/detail.py](ui/scenarios/detail.py) already include `uuid`, `os`, `sys`, `async_playwright`, `_run_async`, `Setter`, `launch_browser_and_page`, `classify_case_outcome`, `is_blank_dataset_row`, `ExcelManager`, `RecipeExecutor`. They do per the existing imports (lines 1-18). No new imports needed.

- [ ] **Step 4.5: Run the runner test**

Run: `python -m pytest tests/test_multi_page_runner.py -v`
Expected: PASS. If it fails because element-map persistence used a different method name, fix the test's `em.save_element_map(...)` call accordingly (and only the test, not the runner).

- [ ] **Step 4.6: Run the full test suite to catch regressions**

Run: `python -m pytest tests/ -v`
Expected: all PASS (or whatever the pre-existing pass set was; nothing new should regress).

- [ ] **Step 4.7: Commit**

```bash
git add ui/scenarios/detail.py tests/test_multi_page_runner.py test_form/page_a.html test_form/page_b.html
git commit -m "feat(runner): multi-page scenario runner in one browser session

Adds _run_multi_page_scenario plus _drive_one_page / _run_transition
helpers. Dispatcher at the top of _run_scenario branches on
sc.kind == 'multi-page'. Each page runs either its dataset's first
non-blank row (via Setter.set_fields_on_page) or its steps (via
RecipeExecutor.execute), then clicks the configured transition target
and waits for url_contains or selector. Cookies/localStorage persist
across pages because the page object is reused. A failed transition
aborts the run and marks downstream pages SKIPPED."
```

---

## Task 5: Persist and render multi-page results

**Files:**
- Modify: [ui/scenarios/detail.py](ui/scenarios/detail.py)

- [ ] **Step 5.1: Extend `_persist_run` to handle `mode == "multi-page"`**

Replace `_persist_run` ([ui/scenarios/detail.py:191](ui/scenarios/detail.py#L191)) by inserting a multi-page branch before the existing dataset/steps branches:

```python
def _persist_run(sc, result: dict) -> None:
    mode = result.get("mode")
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

    # ... existing single-page body unchanged below
```

(Keep the rest of `_persist_run` exactly as it is today.)

- [ ] **Step 5.2: Extend `_render_run_result` to handle `mode == "multi-page"`**

Replace `_render_run_result` ([ui/scenarios/detail.py:234](ui/scenarios/detail.py#L234)) — insert a multi-page branch above the existing `mode == "dataset"` branch:

```python
def _render_run_result(sc, result: dict) -> None:
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

    # ... existing dataset/steps branches unchanged below
```

- [ ] **Step 5.3: Remove the "Multi-page run not wired" error**

In [ui/scenarios/detail.py:287-288](ui/scenarios/detail.py#L287-L288), replace the run button branch:

```python
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
```

The old `st.error("Multi-page run not wired in this tab yet; use the Run dialog.")` line is removed entirely.

- [ ] **Step 5.4: Manual smoke check**

Run: `python -m pytest tests/test_multi_page_runner.py tests/test_scenarios.py tests/test_setter_on_page.py tests/test_excel_manager.py -v`
Expected: all PASS.

- [ ] **Step 5.5: Commit**

```bash
git add ui/scenarios/detail.py
git commit -m "feat(runner): persist and render multi-page results

_persist_run and _render_run_result gain a 'multi-page' branch that
flattens per-page outcomes into the Run Results sheet (one row per
field/step, page_index column populated) and renders one expander per
page with nested field/step results plus transition status. The
'Multi-page run not wired in this tab yet' error is removed."
```

---

## Task 6: New Scenario page — Kind radio + ordered page picker

**Files:**
- Modify: [pages/3_scenarios.py](pages/3_scenarios.py)

- [ ] **Step 6.1: Replace the new-scenario form body**

In [pages/3_scenarios.py](pages/3_scenarios.py), replace the `if open_id == "__new__":` block (lines 83-151) with:

```python
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
```

Note: a freshly-created multi-page scenario has empty transition targets. The user fills them in the Transitions section of the Settings tab (Task 8). Until they do, the Run button will surface a validation error.

- [ ] **Step 6.2: Manual smoke test (Streamlit)**

Run: `streamlit run app.py` and click through:
- New scenario → Kind: multi-page → Name: "Two page demo" → pick 2 scanned URLs → Create
- Verify the scenario YAML at `data/scenarios/two_page_demo.yaml` has `kind: multi-page` and `pages:` with 2 entries.

Expected: scenario loads into the detail page without error (Task 7 wires the tabs).

- [ ] **Step 6.3: Commit**

```bash
git add pages/3_scenarios.py
git commit -m "feat(ui): Kind radio + ordered page picker on New scenario

Single-page branch is unchanged. Multi-page branch shows a list of
ordered URL selectboxes with up/down/remove buttons and an Add page
control. Save creates a Scenario with kind='multi-page' and a
pages[] payload; transition targets are filled in later from the
Settings tab."
```

---

## Task 7: Scenario detail — segmented page selector + tab binding

**Files:**
- Modify: [ui/scenarios/detail.py](ui/scenarios/detail.py)
- Modify: [ui/scenarios/steps_tab.py](ui/scenarios/steps_tab.py)
- Modify: [ui/scenarios/dataset_tab.py](ui/scenarios/dataset_tab.py)

The Steps and Dataset tab `render(sc, on_save)` signatures stay the same, but they currently read `sc.base_url`, `sc.steps`, `sc.dataset` directly. To support multi-page without forking the widgets, introduce a tiny **view object** that exposes the same four attributes but bound either to the whole scenario (single-page) or to a specific `pages[i]` entry (multi-page). The widgets keep their existing signatures.

- [ ] **Step 7.1: Add a `_PageView` helper class to [ui/scenarios/detail.py](ui/scenarios/detail.py)**

After the imports, before `_save_steps`:

```python
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
```

- [ ] **Step 7.2: Update the `render(scenario_id)` function**

Replace the tab section at the bottom of `render` ([ui/scenarios/detail.py:295-299](ui/scenarios/detail.py#L295-L299)) with:

```python
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

    tab1, tab2, tab3, tab4 = st.tabs(["Steps", "Dataset", "Runs", "Settings"])
    with tab1: render_steps(view, _save_view_steps)
    with tab2: render_dataset(view, _save_view_dataset)
    with tab3: render_runs(sc)
    with tab4: render_settings(sc)
```

(The Runs and Settings tabs still receive the whole `sc` — Runs needs to render across all pages, Settings edits scenario-level metadata + per-page transitions.)

- [ ] **Step 7.3: Update [ui/scenarios/dataset_tab.py](ui/scenarios/dataset_tab.py) for the multi-page caption**

At the very top of `render(sc, on_save)` ([ui/scenarios/dataset_tab.py:18](ui/scenarios/dataset_tab.py#L18)), add the multi-page caption. The widget already only sees a `sc` with `.base_url`, `.dataset`, `.id` — which `_PageView` provides — so no other changes are needed inside this file.

Find the existing caption block:

```python
    st.caption(
        "ⓘ **Test data** = the values each run feeds into the form fields. "
        ...
    )
```

Replace with:

```python
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
```

- [ ] **Step 7.4: Verify the steps tab still works**

[ui/scenarios/steps_tab.py](ui/scenarios/steps_tab.py) reads `sc.base_url`, `sc.steps`, `sc.id`, `sc.dataset` (via `if not sc.dataset and ...`). `_PageView` provides all four. No edits needed.

Verify by reading: the `target_options` come from `em.read_element_map(sc.base_url)` — which now returns the elements of the *active page* in multi-page mode. Good — exactly the intended behaviour.

- [ ] **Step 7.5: Manual smoke test**

Run: `streamlit run app.py`. Open the multi-page scenario created in Task 6.

- Verify the segmented "Page" control shows above the tabs.
- Click between pages and verify the Steps tab's `target` dropdown changes to reflect each page's scanned elements.
- Add a step on page 1, switch to page 2, add a different step, switch back to page 1, confirm page 1's step is still there.

(No assertion code — eyeball it.)

- [ ] **Step 7.6: Commit**

```bash
git add ui/scenarios/detail.py ui/scenarios/dataset_tab.py
git commit -m "feat(ui): segmented page selector binds Steps/Dataset per page

Adds a _PageView shim that exposes id/name/base_url/steps/dataset
either from the whole Scenario (single-page) or from a single
pages[i] entry (multi-page). The Steps and Dataset widgets keep
their existing signatures and now operate on whichever page is
selected via the new st.segmented_control above the tabs. The
Dataset tab gains a caveat caption noting that multi-page runs use
only the first non-blank row per page."
```

---

## Task 8: Settings tab — Transitions editor for multi-page

**Files:**
- Modify: [ui/scenarios/settings_tab.py](ui/scenarios/settings_tab.py)

- [ ] **Step 8.1: Rewrite [ui/scenarios/settings_tab.py](ui/scenarios/settings_tab.py)**

Replace the file body:

```python
import streamlit as st
from core.scenarios import save_scenario, delete_scenario
from core.excel_manager import ExcelManager

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"


def render(sc):
    st.caption(
        "ⓘ **Settings** configures the scenario itself — name, target page(s), "
        "and expected outcome. Steps and Dataset live on their own tabs."
    )

    em = ExcelManager(data_dir=DATA_SCANS)
    urls = [""] + em.list_scanned_urls()

    name = st.text_input("Name", value=sc.name, key=f"sname_{sc.id}")

    if sc.kind == "single-page":
        base_url = st.selectbox(
            "Base URL (scanned page)", options=urls,
            index=urls.index(sc.base_url) if sc.base_url in urls else 0,
            key=f"surl_{sc.id}",
        )
    else:
        base_url = sc.base_url  # unused for multi-page

    outcome = st.selectbox(
        "Expected outcome", ["success", "failure"],
        index=0 if sc.expected_outcome == "success" else 1,
        key=f"sout_{sc.id}",
    )

    if sc.kind == "multi-page":
        _render_multi_page_settings(sc, em, urls)

    c1, c2 = st.columns(2)
    if c1.button("Save settings", type="primary", key=f"savecfg_{sc.id}"):
        sc.name = name
        sc.expected_outcome = outcome
        if sc.kind == "single-page":
            sc.base_url = base_url
        save_scenario(DATA_SCENARIOS, sc)
        st.success("Settings saved.")
        st.rerun()
    if c2.button("Delete scenario", key=f"delcfg_{sc.id}"):
        delete_scenario(DATA_SCENARIOS, sc.id)
        st.session_state.pop("_open_scenario", None)
        st.rerun()


def _render_multi_page_settings(sc, em, urls):
    st.divider()
    st.subheader("Pages in this journey")
    st.caption(
        "Edit the page order, swap a page's URL, or configure how the run "
        "advances from one page to the next."
    )

    if not sc.pages:
        st.info("No pages yet. Use the Cancel button and recreate the scenario.")
        return

    # Per-page URL + reorder + remove controls (compact).
    for i, page in enumerate(sc.pages):
        with st.container(border=True):
            st.markdown(f"**Page {i + 1}**")
            cols = st.columns([5, 1, 1, 1])
            new_url = cols[0].selectbox(
                "URL", options=urls,
                index=urls.index(page["base_url"]) if page["base_url"] in urls else 0,
                key=f"mp_url_{sc.id}_{i}",
                label_visibility="collapsed",
            )
            if new_url != page["base_url"]:
                page["base_url"] = new_url
                save_scenario(DATA_SCENARIOS, sc)
                st.rerun()
            if cols[1].button("↑", key=f"mp_up_{sc.id}_{i}", disabled=(i == 0)):
                sc.pages[i - 1], sc.pages[i] = sc.pages[i], sc.pages[i - 1]
                _rebalance_transitions(sc)
                save_scenario(DATA_SCENARIOS, sc)
                st.rerun()
            if cols[2].button("↓", key=f"mp_dn_{sc.id}_{i}",
                              disabled=(i == len(sc.pages) - 1)):
                sc.pages[i], sc.pages[i + 1] = sc.pages[i + 1], sc.pages[i]
                _rebalance_transitions(sc)
                save_scenario(DATA_SCENARIOS, sc)
                st.rerun()
            if cols[3].button("✕", key=f"mp_rm_{sc.id}_{i}",
                              disabled=(len(sc.pages) <= 1)):
                sc.pages.pop(i)
                _rebalance_transitions(sc)
                save_scenario(DATA_SCENARIOS, sc)
                st.rerun()

            # Transition editor for every page except the last.
            if i < len(sc.pages) - 1:
                _render_transition_editor(sc, em, i)

    if st.button("+ Add page", key=f"mp_add_{sc.id}"):
        sc.pages.append({"base_url": "", "steps": [], "dataset": []})
        _rebalance_transitions(sc)
        save_scenario(DATA_SCENARIOS, sc)
        st.rerun()


def _rebalance_transitions(sc):
    """After reorder/add/remove: the last page must NOT have a transition;
    every other page MUST have one (default if missing)."""
    n = len(sc.pages)
    for i, page in enumerate(sc.pages):
        if i == n - 1:
            page.pop("transition", None)
        else:
            page.setdefault("transition", {
                "target": "", "wait_for": "url_contains",
                "value": "", "timeout_ms": 30000,
            })


def _render_transition_editor(sc, em, i):
    """Edit the transition that runs after page i (going to page i+1)."""
    page = sc.pages[i]
    transition = page.setdefault("transition", {
        "target": "", "wait_for": "url_contains", "value": "", "timeout_ms": 30000,
    })
    next_url = sc.pages[i + 1]["base_url"] if i + 1 < len(sc.pages) else ""

    st.markdown(f"_Transition: after page {i + 1} → page {i + 2}_")

    page_elements = em.read_element_map(page["base_url"]) if page["base_url"] else []
    target_options = [""] + [e["element_name"] for e in page_elements]
    new_target = st.selectbox(
        f"Click element on page {i + 1}",
        options=target_options,
        index=target_options.index(transition.get("target", ""))
              if transition.get("target", "") in target_options else 0,
        key=f"mp_tt_{sc.id}_{i}",
        help="The button or link on this page that, when clicked, advances "
             "the journey to the next page.",
    )

    wait_for = st.radio(
        "Then wait for", options=["url_contains", "selector"],
        index=0 if transition.get("wait_for", "url_contains") == "url_contains" else 1,
        horizontal=True, key=f"mp_wf_{sc.id}_{i}",
    )

    default_val = transition.get("value", "")
    if not default_val and wait_for == "url_contains" and next_url:
        # Helpful default: suggest a substring from the next page's URL.
        from urllib.parse import urlparse
        path = urlparse(next_url).path or next_url
        default_val = path.rsplit("/", 1)[-1] or path
    new_value = st.text_input(
        "Value (substring of URL, or CSS selector)",
        value=default_val, key=f"mp_val_{sc.id}_{i}",
    )

    new_timeout = st.number_input(
        "Timeout (ms)", min_value=1000, max_value=120000,
        value=int(transition.get("timeout_ms", 30000)),
        step=1000, key=f"mp_to_{sc.id}_{i}",
    )

    changed = (
        new_target != transition.get("target", "")
        or wait_for != transition.get("wait_for", "url_contains")
        or new_value != transition.get("value", "")
        or new_timeout != int(transition.get("timeout_ms", 30000))
    )
    if changed:
        transition["target"] = new_target
        transition["wait_for"] = wait_for
        transition["value"] = new_value
        transition["timeout_ms"] = int(new_timeout)
        save_scenario(DATA_SCENARIOS, sc)
```

- [ ] **Step 8.2: Manual smoke test**

Run: `streamlit run app.py`. Open the multi-page scenario, go to Settings:

- Each page has its own card with URL selector and ↑/↓/✕.
- The first N-1 pages show a transition editor underneath.
- Edit a transition's target dropdown — it lists the scanned elements of the *current* page (not the next page).
- Reorder pages and confirm the last page's transition is dropped and the previously-last page gains a default transition.

- [ ] **Step 8.3: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 8.4: Commit**

```bash
git add ui/scenarios/settings_tab.py
git commit -m "feat(ui): per-page transitions editor in Settings tab

Multi-page scenarios get a Pages section in the Settings tab: each
page is a card with a URL selectbox, reorder/remove buttons, and (for
all but the last page) an inline transition editor. The transition
target dropdown is populated from the *current* page's scanned
elements, with click-element + url_contains/selector wait + timeout.
A reorder rebalances which page owns the trailing transition."
```

---

## Task 9: End-to-end manual verification

**Files:** none — manual test only.

- [ ] **Step 9.1: Run the dev server**

```bash
streamlit run app.py
```

- [ ] **Step 9.2: Walk through the full multi-page flow**

1. **Library**: scan two pages — e.g. `test_form/page_a.html` and `test_form/page_b.html` (use absolute `file://` URLs).
2. **Scenarios** → **New scenario**:
   - Kind: multi-page
   - Name: "Two-page demo"
   - Add page 1 (page_a.html) and page 2 (page_b.html)
   - Create
3. **Steps tab**: select Page 1, leave steps empty.
4. **Dataset tab**: select Page 1, add a row with `email = "a@b.co"`. Select Page 2, add a row with `phone = "5551234"`.
5. **Settings tab**: configure the page-1 transition:
   - Click element: `go_b` (the Continue button from the scan)
   - Wait for: url_contains
   - Value: `page_b.html`
6. **▶ Run scenario**.
7. Verify the Run result expanders show Page 1 PASS with `email` PASS, transition succeeded, and Page 2 PASS with `phone` PASS.
8. **Runs tab**: confirm the historical run is recorded with `Page Index` column populated.

- [ ] **Step 9.3: Break the transition target and verify failure handling**

Edit the scenario YAML at `data/scenarios/two_page_demo.yaml` and change the page 1 transition `target` to `"nonexistent_button"`. Re-run from the Streamlit UI.

Expected:
- Page 1 shows as PASS (the field fill succeeded).
- Transition shows FAIL with "could not locate transition target" or similar.
- Page 2 is marked SKIPPED.
- Overall scenario status is FAIL.

- [ ] **Step 9.4: Verify single-page regression**

Open an existing single-page scenario (or create one). Confirm:
- No segmented page selector appears.
- Steps/Dataset/Runs/Settings behave exactly as before.
- A run completes and records to the Run Results sheet.

- [ ] **Step 9.5: Final commit (if any fixture or doc tweaks were made during manual testing)**

If you needed to adjust fixtures or copy:

```bash
git add -A
git commit -m "docs: notes from multi-page manual verification"
```

Otherwise skip this step.

---

## Done criteria

- All unit tests pass: `python -m pytest tests/ -v`
- A multi-page scenario can be created, configured, and run successfully end-to-end from the Streamlit UI against the two-page fixture.
- Run history records per-page outcomes with the new `Page Index` column.
- A failed transition aborts the run and marks downstream pages SKIPPED.
- Existing single-page scenarios remain fully functional with no UI regressions.
