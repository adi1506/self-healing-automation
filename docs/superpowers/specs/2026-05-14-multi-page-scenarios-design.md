# Multi-Page Scenarios — Design

**Date:** 2026-05-14
**Status:** Draft for review
**Scope:** Wire multi-page testing into the existing Scenario user flow, replacing the half-built `recipe_refs` schema with an inline `pages[]` model that reuses the single-page UI per page.

## 1. Problem

`core/scenarios.py` already accepts `kind="multi-page"` and validates a `recipe_refs` field, but no UI writes that field, no runner consumes it, and the Scenario detail page errors with *"Multi-page run not wired in this tab yet"* whenever a multi-page scenario is opened.

The current single-page flow — **scan a page in Library → create a Scenario → fill the Steps or Dataset tab → click ▶ Run** — is well understood by users. We need to support journeys that span pages (e.g. login → profile → checkout) **without** introducing a parallel mental model that forces users to learn new top-level concepts like Recipes or Flows.

## 2. Goals

- A multi-page Scenario is **one Scenario file** that lists its pages in order.
- The Scenario detail UI **reuses** the Steps and Dataset tabs unchanged; only adds a per-page selector above them.
- A multi-page run executes in **one Playwright session**, walking pages sequentially, so cookies/localStorage carry across pages naturally.
- **Self-healing behaviour per page is identical to today's single-page behaviour** — no new healing modes.
- Existing single-page scenarios continue to work without modification.

## 3. Non-goals

- Cross-domain journeys (each page must be in the user's Library of scanned pages).
- Reusable cross-scenario recipes (the existing `core/recipes.py` flow/recipe YAML format becomes unused and will be left in place but unwired — removing it is out of scope here).
- Combinatorial dataset expansion across pages — see §6.
- Parallel multi-page runs.
- Pre-emptive healing of downstream pages before the run reaches them — see §8.

## 4. Schema changes

### 4.1 New `Scenario.pages` field

`core/scenarios.py` `Scenario` dataclass gains an optional field:

```python
pages: list[dict] = field(default_factory=list)
```

Each entry has the shape:

```yaml
base_url: <scanned URL>          # required, must be in Library
steps: [...]                     # same shape as today's Scenario.steps
dataset: [...]                   # same shape as today's Scenario.dataset
transition:                      # optional; required on all but the last page
  target: <element_name>         # button/link to click to leave this page
  wait_for: url_contains | selector
  value: <substring or selector>
  timeout_ms: 30000              # default
```

The top-level `Scenario.base_url`, `Scenario.steps`, and `Scenario.dataset` remain authoritative for `kind="single-page"`. For `kind="multi-page"`, they are ignored at runtime and their UI is hidden — but they stay in the schema to keep single-page YAMLs untouched and to allow round-tripping if a user toggles `kind`.

### 4.2 Validation

`_validate` (currently at [core/scenarios.py:36](core/scenarios.py#L36)) is updated:

- `single-page` branch unchanged.
- `multi-page` branch:
  - `pages` must be non-empty.
  - Every `pages[i].base_url` must be non-empty.
  - Every `pages[i]` except the last must have a `transition` block.
  - The legacy `recipe_refs` requirement is **removed** — `recipe_refs` becomes an ignored field, kept for back-compat read but not written.

### 4.3 Migration

`core/scenario_migration.py` gets one more idempotent pass that, on any scenario with `kind="multi-page"` and a populated `recipe_refs`, leaves the file alone but logs a warning. No legacy multi-page scenarios exist in production (the UI never wrote one), so this is defensive only.

No migration is needed for single-page scenarios.

## 5. UI changes

### 5.1 New Scenario page ([pages/3_scenarios.py](pages/3_scenarios.py))

The "New scenario" form gains a `Kind` radio above the name field:

```
Kind:  (•) Single page    ( ) Multi-page
Name:  [____________________]
```

**Single-page branch**: current UI unchanged.

**Multi-page branch**: the "Base URL" dropdown is replaced by an ordered page picker:

```
Pages in this journey (in order):
  1. [▼ select scanned URL ]   [↑] [↓] [✕]
  2. [▼ select scanned URL ]   [↑] [↓] [✕]
  [+ Add page]
```

Reorder via up/down buttons; remove via ✕. The AI scenario suggester (currently scoped to a single `base_url`) is **disabled** for multi-page creation in v1 — it can be revisited as a follow-up.

On Create, the scenario is saved with `kind="multi-page"`, `pages=[{base_url: u1, steps: [], dataset: []}, ...]`, and `expected_outcome="success"` by default. The user is dropped into the scenario detail page.

### 5.2 Scenario detail page ([ui/scenarios/detail.py](ui/scenarios/detail.py))

When `sc.kind == "multi-page"`, the detail view adds **one new widget** above the existing tabs:

```
Page:  [ 1. /login | 2. /profile | 3. /checkout ]  ← st.segmented_control
       ────────────┴───────────────────────────────
       Active page index stored in st.session_state[f"_active_page_{sc.id}"]

┌─────┬─────────┬──────┬───────────┐
│Steps│ Dataset │ Runs │ Settings  │      ← existing tabs, repurposed
└─────┴─────────┴──────┴───────────┘
```

- **Steps tab** ([ui/scenarios/steps_tab.py](ui/scenarios/steps_tab.py)): when multi-page, reads from and writes to `sc.pages[active_idx]["steps"]` instead of `sc.steps`. The widget itself is unchanged.
- **Dataset tab** ([ui/scenarios/dataset_tab.py](ui/scenarios/dataset_tab.py)): when multi-page, reads/writes `sc.pages[active_idx]["dataset"]`. A caption is added: *"During a multi-page run, only the first non-blank row of each page's dataset is used. To iterate many rows, use a single-page scenario."*
- **Settings tab** ([ui/scenarios/settings_tab.py](ui/scenarios/settings_tab.py)): adds a **Transitions** subsection (multi-page only) showing one editor per page boundary (between page i and page i+1):

  ```
  After page 1 (/login):
    Click element:    [▼ submit_button (from /login scan)]
    Then wait for:    (•) URL contains   ( ) Selector visible
    Value:            [/profile_________________________]
    Timeout (ms):     [30000__]
  ```

  The "Click element" dropdown is populated from the scanned elements of page i.

- **Runs tab** ([ui/scenarios/runs_tab.py](ui/scenarios/runs_tab.py)): no widget changes; rendering of historical runs gains a `page_index` grouping so rows from page 1 appear under a "Page 1: /login" header, etc. See §7 for the data-shape change.

### 5.3 Run button

The existing `▶ Run scenario` button stays. The hard-coded error at [ui/scenarios/detail.py:288](ui/scenarios/detail.py#L288) (*"Multi-page run not wired in this tab yet"*) is removed; multi-page runs dispatch into the new runner described in §6.

## 6. Runner

A new function in [ui/scenarios/detail.py](ui/scenarios/detail.py) — `_run_multi_page_scenario(sc)` — sits beside the existing `_run_scenario` / `_run_dataset`. Dispatch happens at the top of `_run_scenario`:

```python
if sc.kind == "multi-page":
    return _run_multi_page_scenario(sc)
# ... existing single-page logic unchanged
```

### 6.1 Execution model

One Playwright browser + page is opened for the whole scenario. For each `page_entry` in `sc.pages` in order:

1. **Navigate** to `page_entry["base_url"]` (only for index 0 — subsequent pages arrive via the previous page's transition).
2. **Resolve elements** for this page via `ExcelManager.read_element_map(page_entry["base_url"])`.
3. **Drive the page**:
   - If `page_entry["dataset"]` has at least one non-blank row: take the **first non-blank row** and call `Setter.set_fields(...)` against the live page (analogous to the single-page dataset path, but reusing the existing browser instead of opening a new one).
   - Else if `page_entry["steps"]` has runnable steps: build an in-memory recipe and call `RecipeExecutor.execute(page, recipe)` against the live page.
   - Else: record this page as `SKIPPED` and continue.
4. **Transition** (all pages except the last):
   - Look up the transition target in this page's scanned elements, click it via the existing locator chain.
   - Wait for the configured signal (`url_contains` or `selector`) with the configured timeout (default 30s).
   - If the wait fails, abort the run — remaining pages are marked `SKIPPED`.
5. After the last page, classify the overall outcome (see §7) and close the browser.

The Setter is reused via a new helper `Setter.set_fields_on_page(page, elements, test_data, ...)` that accepts an already-open Playwright page rather than launching its own browser. The existing `Setter.set_fields(url, ...)` is kept and re-implemented in terms of the new helper, so single-page behaviour is unchanged.

### 6.2 Dataset semantics — locked-in

- The Dataset tab still allows authoring multiple rows per page (so a user can develop and test rows there in single-page-style iteration).
- A multi-page run executes **only the first non-blank row** of each page's dataset.
- If a page has no dataset rows, it falls back to `steps`.
- Rationale: combinatorial expansion across pages (row × row × row) is rarely what users want and explodes run time. Iterating rows is best done in a single-page scenario targeting just that page.

### 6.3 Transitions — locked-in

- Explicit per-page-boundary `transition` block, authored in the Settings tab.
- Target is selected from that page's scanned elements (avoids typos and free-form selector authoring).
- Wait condition: `url_contains` (default) or `selector` (for SPA navigations that don't change the URL).
- Default timeout 30s; user-editable.
- Failure to satisfy the wait condition aborts the run with a clear "Transition after page N failed" error.

## 7. Result shape and persistence

The result envelope returned by `_run_multi_page_scenario` is:

```python
{
  "mode": "multi-page",
  "run_id": "<hex>",
  "page_outcomes": [
    {
      "page_index": 0,
      "base_url": "...",
      "page_status": "PASS" | "FAIL" | "SKIPPED" | "UNVERIFIED",
      "field_results": [...],          # from Setter, if dataset-driven
      "step_results": [...],           # from RecipeExecutor, if steps-driven
      "transition_status": "PASS" | "FAIL" | "N/A",
      "transition_error": "...",
      "screenshot": "<path or ''>",
    },
    ...
  ],
  "scenario_status": "PASS" | "FAIL" | "UNVERIFIED",
  "summary": "3/3 pages passed",
}
```

`scenario_status` = `PASS` iff every page is `PASS` (or `UNVERIFIED` rolls up to `UNVERIFIED` if any page is `UNVERIFIED` and none are `FAIL`).

`_persist_run` ([ui/scenarios/detail.py:191](ui/scenarios/detail.py#L191)) gains a multi-page branch that appends one row per page-per-field/step to the same Run Results sheet used today, with a new `page_index` column added to the sheet schema. `ExcelManager.append_run_result` is extended to accept the new key; existing single-page calls pass `page_index=0` implicitly.

`_render_run_result` gains a `mode == "multi-page"` branch that renders one expander per page, with the existing per-row/per-step rendering nested inside.

## 8. Self-healing — locked-in

Policy: **per-page, on-demand, identical to today's behaviour.**

When a locator fails on the currently active page during a multi-page run, the existing `Healer.heal(url, ...)` is invoked against that page's URL — exactly as it would be in a single-page run. No pre-emptive healing of downstream pages. No cross-page cascade.

Concretely: no changes to `core/healer.py` are required. The hook point is wherever the single-page runner currently invokes the healer; the multi-page runner uses the same hook unchanged because each page's execution path (`set_fields_on_page` or `RecipeExecutor.execute`) sees an open page and a URL just like single-page does today.

## 9. Components and isolation

| Unit | Responsibility | Depends on |
|---|---|---|
| `core/scenarios.py` | Schema, validation, persistence | yaml |
| `core/recipes.py::RecipeExecutor` | Runs steps against an open page (already page-agnostic) | `Setter` |
| `core/setter.py::Setter` | Field-level setting. **New** `set_fields_on_page(page, ...)` helper; existing `set_fields(url, ...)` reimplemented on top of it. | Playwright |
| `ui/scenarios/detail.py` | Dispatches single-page vs. multi-page runs, renders results | core/* |
| `ui/scenarios/steps_tab.py`, `dataset_tab.py`, `settings_tab.py` | Edit active page's data when multi-page; unchanged otherwise | scenarios |
| `core/excel_manager.py` | Run-result persistence; gains `page_index` column | — |
| `core/scenario_migration.py` | Defensive warning for legacy `recipe_refs` populated scenarios | — |

The Steps and Dataset tab widgets are **deliberately unchanged** — they remain bound to a `(steps, dataset)` pair, only the binding source moves from `sc` to `sc.pages[active_idx]`.

## 10. Testing strategy

- **Unit tests** ([tests/](tests/)):
  - `tests/test_scenarios.py`: validation accepts new `pages[]` shape; rejects empty pages, missing transitions.
  - `tests/test_setter.py`: `set_fields_on_page` works against an externally-supplied page; `set_fields(url, ...)` still works (regression).
  - `tests/test_runner_utils.py` (existing): unchanged.
  - New `tests/test_multi_page_runner.py`: end-to-end test using local `test_form/*.html` fixtures across two pages with an HTML link transition.
- **Manual test plan**:
  - Scan two local fixture pages in Library.
  - Create a multi-page scenario with both, configure transition.
  - Run; verify cookies/localStorage persist across pages (use a local form that sets `document.cookie` on page 1 and reads it on page 2).
  - Break a locator on page 2; verify the healer heals just page 2.

## 11. Rollout

One PR, behind no flag — the feature is purely additive in the UI (new Kind radio, new segmented control only when `kind=multi-page`) and existing single-page scenarios go through the same code path they do today. No env var or setting needed.

## 12. Out of scope / follow-ups

- AI-suggested multi-page scenarios (the suggester is single-page-only in v1).
- Removing the now-unused `core/recipes.py::save_flow`/`load_flow` and the unused `recipe_refs` field.
- Parallel runs.
- Multi-row dataset iteration across pages (combinatorial). If a user wants this they can stay in single-page mode for the page in question.
