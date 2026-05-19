# App-grouped scenarios flow — design

Date: 2026-05-19
Status: design approved, awaiting plan

## Goal

Restructure the Recordings page so the user navigates **Application → Scenarios → Test cases** instead of seeing a flat list of applications with all scenario actions cross-linked. Keep the existing `/scenarios` page as a flat view across all scenario kinds, but sort it by most-recent run.

The user-facing label everywhere is **"Scenario"** (matches the data model). The word "Recording" is reserved for the sub-level inside a scenario — a single captured browser session under `scenario.recordings[]`.

## Non-goals

- No data-model changes. `Application`, `Scenario(kind="recorded")`, and the existing `recordings[]` / `ai_test_cases[]` lists stay as they are.
- No renaming of `data/scenarios/`, `core/scenarios.py`, or `ui/scenarios/`.
- No changes to scenario detail (steps/dataset/runs/settings tabs, AI generator, Excel uploader, replay flow).
- No cleanup of orphaned screenshots under `data/replay_runs/`. Cascade delete leaves them on disk; the confirm dialog says so.
- No changes to single-page or multi-page scenario authoring.

## User-facing screens

### A. `/recordings` — application list (default mode)

Same as today, with two additions:

1. Each application row shows a **scenario count**: `· N scenarios` next to the existing `login ✓ / state 🟢` indicators.
2. The existing **Delete** button on each row triggers a **cascade-confirm dialog** instead of deleting immediately.

The "New application" form below the list is unchanged. The login-recording subprocess flow (modes `done`, `recording`, `list`) is unchanged.

### B. `/recordings` — application detail (new mode, `view_app_id` set)

Triggered by an **Open** button added to each application row. Shows:

- Back button → returns to app list.
- App header: name, URL, login state badge, "🗑 Delete app" button (same cascade confirm).
- **Scenarios** section with `+ New scenario` button.
  - List of `Scenario(kind="recorded")` filtered by `application_id == view_app_id`.
  - Each row: name, last-run status pill + timestamp, recording count, test-case count, `[Open] [▶ Run] [🗑]`.
  - `+ New scenario` deep-links into the existing scenario-detail recorder flow (the same one [pages/6_recordings.py:37-44](pages/6_recordings.py#L37-L44) already implements via `_start_scenario_recording`).
  - `Open` deep-links to `/scenarios` with `_open_scenario` set, same as the flat list does today.
  - `▶ Run` executes `_run_scenario` for that scenario in place and renders the result inline.
  - `🗑` triggers a per-scenario delete confirm (matches today's per-row confirm in [ui/scenarios/list.py](ui/scenarios/list.py)).

### C. `/scenarios` — flat list (existing, with sorting change)

- Cards rendered in **most-recently-run-first** order; never-run scenarios pushed below a soft `— never run —` divider.
- Each card gains a small caption showing the application name (for `kind="recorded"`) or `(no app)` otherwise.
- All other behavior unchanged.

### D. Scenario detail — unchanged

The recordings list, AI-generated / Excel / manual test case submenu, replay flow, runs tab, etc., already exist in [ui/scenarios/detail.py](ui/scenarios/detail.py). No work here.

## Cascade-delete behavior

When the user clicks delete on an application (from the list or from the app-detail header):

1. Compute affected counts: load all scenarios, count those with `application_id == app_id`, sum their `ai_test_cases` length.
2. Render a confirm warning:

   > Delete **{app.name}** and its **{N} scenarios** + **{M} test cases**? Recorded replay screenshots under `data/replay_runs/` will be left on disk. This cannot be undone.

3. On confirm:
   - For each matching scenario, call `delete_scenario(DATA_SCENARIOS, sc.id)`.
   - Call `delete_application(APP_DIR, app_id)`.
   - Remove `data/storage_states/{app_id}.enc` if present.
   - Remove `data/recorder_work/{app_id}_*` artifacts (login.yaml, candidates.json) if present.
   - Clear any related `st.session_state` keys (`view_app_id`, `login_app_id`, etc.) and `st.rerun()`.

If the user clicks Cancel, no files change.

## Sort-by-latest-run logic for `/scenarios`

`aggregate_runs(DATA_SCANS)` already returns rows sorted descending by timestamp. Reuse it:

1. Build a map `scenario_name → latest_timestamp` by iterating `aggregate_runs` and taking the first (newest) hit per `test_case_name`.
2. Partition scenarios into `has_run` (sorted by latest_timestamp desc) and `never_run` (sorted alphabetically by name for stability).
3. Render `has_run` first, then a centered caption divider `— never run —`, then `never_run`.

If `has_run` is empty, skip the divider and render only the never-run group. If `never_run` is empty, skip the divider and render only the run group.

Tie-breaker: scenarios with identical timestamps fall back to name-ascending.

## Components and files touched

**No new files** for core logic. New rendering helpers live alongside existing ones.

| File | Change |
|---|---|
| [pages/6_recordings.py](pages/6_recordings.py) | Add app-detail mode (`view_app_id`). Add "Open" button to each app row. Wire cascade-confirm for delete. Add scenario count to each row. |
| [ui/scenarios/list.py](ui/scenarios/list.py) | Sort by last-run desc + divider for never-run group. Add app-name caption per card. |
| [core/applications.py](core/applications.py) | Add `delete_application_cascade(data_dir, scenarios_dir, states_dir, work_dir, app_id) -> dict` that performs the full cascade and returns a summary `{"scenarios_deleted": N, "test_cases_deleted": M, "files_removed": [...]}`. The existing `delete_application` stays as-is (used by callers that don't want cascade). |
| [core/scenarios.py](core/scenarios.py) | Add helper `list_scenarios_for_app(data_dir, app_id) -> list[Scenario]`. Thin filter over `list_scenarios`. |

No tests are deleted. New tests are added (see Testing).

## Data flow

```
User clicks Open on app row
   ↓
session_state["view_app_id"] = app.id
   ↓
6_recordings.py reroutes to app-detail render
   ↓
list_scenarios_for_app(DATA_SCENARIOS, app.id) → [Scenario, ...]
   ↓
For each: aggregate_runs lookup for last-status pill
   ↓
Render rows; +New scenario button reuses _start_scenario_recording
```

```
User clicks Delete on app
   ↓
session_state["_confirm_del_app_<id>"] = True
   ↓
Show cascade warning with counts
   ↓
On confirm: delete_application_cascade(...)
   ↓
Clear session_state; rerun
```

## Error handling

- **Missing application file** during cascade: `delete_application_cascade` swallows `FileNotFoundError` on the per-scenario unlink and on optional state/work files — the cascade is idempotent. Returns the partial summary.
- **Scenario load failure** during count (corrupt YAML): the scenario is excluded from the count but the cascade still attempts to delete its file by listing the directory. (Same behavior as the existing list helpers.)
- **Run aggregation failure** on `/scenarios` sort: if `aggregate_runs` raises, fall back to alphabetical sort (current behavior). Render a small caption noting the fallback so the user knows the sort didn't apply.
- **Session-state staleness**: if `view_app_id` points to a deleted app, app-detail mode renders an inline error + a "Back to applications" button and clears the key.

## Testing

New tests live under `tests/`:

- `tests/test_applications_cascade.py`
  - `delete_application_cascade` removes the app YAML, every scenario whose `application_id` matches, the storage-state file, and the `recorder_work/{app_id}_*` artifacts. Returns the right counts.
  - It does NOT remove scenarios belonging to other apps.
  - It does NOT touch `data/replay_runs/`.
  - It is idempotent when called twice for the same app.
- `tests/test_scenarios_filter.py`
  - `list_scenarios_for_app` returns only scenarios with matching `application_id`.
  - Returns empty list when no scenarios match.
  - Skips corrupt YAMLs without raising.

UI logic is exercised through the existing Streamlit smoke pattern (page imports + `st.session_state` setup) where practical — but UI tests are not a hard requirement here because the surface is mostly plumbing over already-tested core functions.

## Open questions

None remaining. Confirmed in brainstorming:
- Label is "Scenario" (not "Recording") in the new app-drill view.
- Cascade delete is gated by a count-showing confirm.
- Orphan replay screenshots are accepted (no cleanup).
