# App-grouped Scenarios Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the `/recordings` page into a hierarchical Application → Scenarios browse view, add cascade-delete-with-confirm on applications, and sort the `/scenarios` flat list by most-recent run.

**Architecture:** No data-model changes. Two new pure-Python helpers (`list_scenarios_for_app`, `delete_application_cascade`) sit on top of existing `core/applications.py` and `core/scenarios.py`. UI work is restricted to `pages/6_recordings.py` (new app-detail mode) and `ui/scenarios/list.py` (sort + app caption). All UI label changes use "Scenario" — never "Recording" — for the top-level entity.

**Tech Stack:** Python, Streamlit, PyYAML, pytest.

**Spec:** [docs/superpowers/specs/2026-05-19-app-grouped-scenarios-flow-design.md](../specs/2026-05-19-app-grouped-scenarios-flow-design.md)

---

## File Structure

| File | Responsibility | Change type |
|---|---|---|
| [core/scenarios.py](../../../core/scenarios.py) | Scenario persistence; add `list_scenarios_for_app` filter. | Modify (add one function) |
| [core/applications.py](../../../core/applications.py) | Application persistence; add `delete_application_cascade`. | Modify (add one function) |
| [ui/scenarios/list.py](../../../ui/scenarios/list.py) | Flat scenarios list rendering; reorder by last-run, add app caption, never-run divider. | Modify (rewrite `render`) |
| [pages/6_recordings.py](../../../pages/6_recordings.py) | Recordings page; add app-detail mode, "Open" button, scenario counts, cascade-confirm. | Modify (add app-detail branch + refactor list mode) |
| `tests/test_scenarios.py` | Add coverage for `list_scenarios_for_app`. | Modify (add tests) |
| `tests/test_applications_cascade.py` | Cover `delete_application_cascade` behavior. | Create |
| `tests/test_scenarios_list_render.py` | Cover the pure-Python sort/partition helper extracted from `ui/scenarios/list.py`. | Create |

Each task below produces a self-contained commit.

---

## Task 1: Add `list_scenarios_for_app` helper

**Files:**
- Modify: [core/scenarios.py](../../../core/scenarios.py)
- Test: [tests/test_scenarios.py](../../../tests/test_scenarios.py)

- [ ] **Step 1: Write the failing tests**

Append these tests to the end of `tests/test_scenarios.py`:

```python
def test_list_scenarios_for_app_filters_by_application_id(tmp_path):
    from core.scenarios import list_scenarios_for_app

    sc1 = Scenario(
        id="rec_a", name="A", kind="recorded",
        base_url="", steps=[], dataset=[], expected_outcome="success",
        application_id="app-1",
        recordings=[{"id": "r1", "name": "n", "steps": [], "start_url": ""}],
    )
    sc2 = Scenario(
        id="rec_b", name="B", kind="recorded",
        base_url="", steps=[], dataset=[], expected_outcome="success",
        application_id="app-2",
        recordings=[{"id": "r1", "name": "n", "steps": [], "start_url": ""}],
    )
    sc3 = Scenario(
        id="sp", name="SP", kind="single-page",
        base_url="https://e.com",
        steps=[{"action": "click", "target": "btn"}],
        dataset=[], expected_outcome="success",
    )
    save_scenario(str(tmp_path), sc1)
    save_scenario(str(tmp_path), sc2)
    save_scenario(str(tmp_path), sc3)

    out = list_scenarios_for_app(str(tmp_path), "app-1")
    assert [s.id for s in out] == ["rec_a"]


def test_list_scenarios_for_app_returns_empty_when_no_match(tmp_path):
    from core.scenarios import list_scenarios_for_app

    sc = Scenario(
        id="rec_a", name="A", kind="recorded",
        base_url="", steps=[], dataset=[], expected_outcome="success",
        application_id="app-1",
        recordings=[{"id": "r1", "name": "n", "steps": [], "start_url": ""}],
    )
    save_scenario(str(tmp_path), sc)
    assert list_scenarios_for_app(str(tmp_path), "app-other") == []


def test_list_scenarios_for_app_returns_empty_for_missing_dir():
    from core.scenarios import list_scenarios_for_app
    assert list_scenarios_for_app("/nonexistent/path", "app-x") == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_scenarios.py::test_list_scenarios_for_app_filters_by_application_id tests/test_scenarios.py::test_list_scenarios_for_app_returns_empty_when_no_match tests/test_scenarios.py::test_list_scenarios_for_app_returns_empty_for_missing_dir -v
```

Expected: 3 FAILED, ImportError / AttributeError on `list_scenarios_for_app`.

- [ ] **Step 3: Implement the helper**

Append to `core/scenarios.py`:

```python
def list_scenarios_for_app(data_dir: str, app_id: str) -> list[Scenario]:
    """Return scenarios whose application_id matches.

    Useful for the app-detail view in /recordings. Filters list_scenarios so
    callers don't have to know the field name. Returns an empty list for
    unknown apps or missing directories — same forgiving contract as
    list_scenarios.
    """
    return [s for s in list_scenarios(data_dir) if s.application_id == app_id]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_scenarios.py -v
```

Expected: all tests in the file PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```
git add core/scenarios.py tests/test_scenarios.py
git commit -m "feat(scenarios): add list_scenarios_for_app filter"
```

---

## Task 2: Add `delete_application_cascade` helper

**Files:**
- Modify: [core/applications.py](../../../core/applications.py)
- Create: `tests/test_applications_cascade.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_applications_cascade.py`:

```python
import os
from pathlib import Path

from core.applications import (
    Application, save_application, delete_application_cascade,
)
from core.scenarios import Scenario, save_scenario, list_scenarios


def _make_recorded(app_id: str, sid: str, *, test_case_count: int = 0) -> Scenario:
    return Scenario(
        id=sid, name=sid, kind="recorded",
        base_url="", steps=[], dataset=[], expected_outcome="success",
        application_id=app_id,
        recordings=[{"id": "r1", "name": "n", "steps": [], "start_url": ""}],
        ai_test_cases=[
            {"id": f"tc{i}", "name": f"case {i}", "recording_id": "r1",
             "expected_outcome": "success", "overrides": {}}
            for i in range(test_case_count)
        ],
    )


def test_cascade_deletes_app_and_its_scenarios(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    for d in (apps_dir, scns_dir, states_dir, work_dir):
        d.mkdir()

    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))
    save_application(str(apps_dir), Application(
        id="app-2", name="B", base_url_pattern="b.com",
    ))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s1", test_case_count=2))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s2", test_case_count=3))
    save_scenario(str(scns_dir), _make_recorded("app-2", "s3", test_case_count=1))
    (states_dir / "app-1.enc").write_bytes(b"state")
    (work_dir / "app-1_login.yaml").write_text("k: v")
    (work_dir / "app-1_candidates.json").write_text("{}")
    (work_dir / "app-2_login.yaml").write_text("k: v")

    summary = delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )

    assert summary["scenarios_deleted"] == 2
    assert summary["test_cases_deleted"] == 5
    assert not (apps_dir / "app-1.yaml").exists()
    assert not (states_dir / "app-1.enc").exists()
    assert not (work_dir / "app-1_login.yaml").exists()
    assert not (work_dir / "app-1_candidates.json").exists()
    # Other app's data is untouched
    assert (apps_dir / "app-2.yaml").exists()
    assert (work_dir / "app-2_login.yaml").exists()
    remaining = [s.id for s in list_scenarios(str(scns_dir))]
    assert remaining == ["s3"]


def test_cascade_is_idempotent(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    for d in (apps_dir, scns_dir, states_dir, work_dir):
        d.mkdir()
    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))

    delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    # Second call must not raise
    summary = delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    assert summary["scenarios_deleted"] == 0
    assert summary["test_cases_deleted"] == 0


def test_cascade_leaves_replay_runs_alone(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    replay_dir = tmp_path / "replay_runs"
    for d in (apps_dir, scns_dir, states_dir, work_dir, replay_dir):
        d.mkdir()
    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s1"))
    # Pretend a replay produced a screenshot under recording id "r1"
    rec_dir = replay_dir / "r1"
    rec_dir.mkdir()
    (rec_dir / "step0.png").write_bytes(b"png")

    delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    # The cascade must not touch data/replay_runs
    assert (replay_dir / "r1" / "step0.png").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_applications_cascade.py -v
```

Expected: 3 FAILED, ImportError on `delete_application_cascade`.

- [ ] **Step 3: Implement the helper**

Append to `core/applications.py`:

```python
def delete_application_cascade(
    apps_dir: str,
    scenarios_dir: str,
    storage_states_dir: str,
    work_dir: str,
    app_id: str,
) -> dict:
    """Delete an application together with the scenarios that reference it,
    its storage-state blob, and its recorder_work scratch files.

    Idempotent: calling it twice for the same app_id returns
    {"scenarios_deleted": 0, "test_cases_deleted": 0, "files_removed": []}
    on the second call.

    Returns a summary dict so the UI can show counts in a confirmation toast.
    Replay screenshots under data/replay_runs/ are intentionally NOT removed —
    there is no back-reference index from recording_id to scenario_id and the
    disk cost is low.
    """
    from core.scenarios import list_scenarios_for_app, delete_scenario

    files_removed: list[str] = []

    matching = list_scenarios_for_app(scenarios_dir, app_id)
    test_cases_deleted = sum(len(s.ai_test_cases or []) for s in matching)
    for sc in matching:
        delete_scenario(scenarios_dir, sc.id)
        files_removed.append(os.path.join(scenarios_dir, f"{sc.id}.yaml"))

    state_path = os.path.join(storage_states_dir, f"{app_id}.enc")
    if os.path.exists(state_path):
        os.remove(state_path)
        files_removed.append(state_path)

    if os.path.isdir(work_dir):
        for fname in os.listdir(work_dir):
            if fname.startswith(f"{app_id}_"):
                fpath = os.path.join(work_dir, fname)
                try:
                    os.remove(fpath)
                    files_removed.append(fpath)
                except FileNotFoundError:
                    pass

    delete_application(apps_dir, app_id)
    files_removed.append(os.path.join(apps_dir, f"{app_id}.yaml"))

    return {
        "scenarios_deleted": len(matching),
        "test_cases_deleted": test_cases_deleted,
        "files_removed": files_removed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_applications_cascade.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```
git add core/applications.py tests/test_applications_cascade.py
git commit -m "feat(applications): cascade delete for app + its scenarios"
```

---

## Task 3: Extract sort/partition helper for /scenarios list

This task moves the future sort logic into a pure function so it can be unit-tested without Streamlit. The actual `render()` call site is wired in Task 4.

**Files:**
- Modify: [ui/scenarios/list.py](../../../ui/scenarios/list.py)
- Create: `tests/test_scenarios_list_render.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenarios_list_render.py`:

```python
from types import SimpleNamespace

from ui.scenarios.list import partition_and_sort_scenarios


def _sc(id_: str, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=id_, name=name or id_)


def test_partition_separates_run_from_never_run():
    scs = [_sc("a"), _sc("b"), _sc("c")]
    last_status_by_name = {
        "a": ("PASS", "2026-05-18 10:00:00"),
        "b": ("", ""),
        "c": ("FAIL", "2026-05-19 09:00:00"),
    }
    run_group, never_run = partition_and_sort_scenarios(scs, last_status_by_name)
    assert [s.id for s in run_group] == ["c", "a"]  # newest first
    assert [s.id for s in never_run] == ["b"]


def test_partition_name_breaks_timestamp_tie():
    scs = [_sc("z"), _sc("a")]
    last_status_by_name = {
        "z": ("PASS", "2026-05-18 10:00:00"),
        "a": ("PASS", "2026-05-18 10:00:00"),
    }
    run_group, never_run = partition_and_sort_scenarios(scs, last_status_by_name)
    assert [s.id for s in run_group] == ["a", "z"]
    assert never_run == []


def test_partition_handles_all_never_run():
    scs = [_sc("b"), _sc("a")]
    run_group, never_run = partition_and_sort_scenarios(scs, {})
    assert run_group == []
    assert [s.id for s in never_run] == ["a", "b"]  # alphabetical
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_scenarios_list_render.py -v
```

Expected: 3 FAILED, ImportError on `partition_and_sort_scenarios`.

- [ ] **Step 3: Add the helper to `ui/scenarios/list.py`**

Modify `ui/scenarios/list.py` to add this function near the top (above `_last_status`):

```python
def partition_and_sort_scenarios(scs, last_status_by_name):
    """Split scenarios into (has_run_sorted_desc, never_run_sorted_az).

    last_status_by_name maps scenario.name -> (status, timestamp). A scenario
    is "has run" iff its timestamp is non-empty. The run group is sorted by
    timestamp descending, with name ascending as the tiebreaker so the order
    is stable when two runs share a timestamp.
    """
    has_run = []
    never_run = []
    for sc in scs:
        _status, ts = last_status_by_name.get(sc.name, ("", ""))
        if ts:
            has_run.append((ts, sc.name, sc))
        else:
            never_run.append((sc.name, sc))
    has_run.sort(key=lambda t: (t[0], t[1]), reverse=False)
    # We want timestamp DESC but name ASC — sort twice for stable composite.
    has_run.sort(key=lambda t: t[0], reverse=True)
    never_run.sort(key=lambda t: t[0])
    return [t[-1] for t in has_run], [t[-1] for t in never_run]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_scenarios_list_render.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```
git add ui/scenarios/list.py tests/test_scenarios_list_render.py
git commit -m "feat(ui): partition_and_sort_scenarios helper for /scenarios"
```

---

## Task 4: Wire sort + app caption into `/scenarios` list rendering

**Files:**
- Modify: [ui/scenarios/list.py](../../../ui/scenarios/list.py)

- [ ] **Step 1: Replace the `render` function in `ui/scenarios/list.py`**

Open `ui/scenarios/list.py`. Replace the entire `render()` function with the version below. Keep imports, `_last_status`, and `partition_and_sort_scenarios` as they are. Add the new imports shown at the top.

```python
import streamlit as st
from core.scenarios import list_scenarios, delete_scenario
from core.applications import list_applications
from core.reports import aggregate_runs

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"
DATA_APPS = "data/applications"


def _last_status(name: str, runs: list[dict]) -> tuple[str, str]:
    for r in runs:
        if r.get("test_case_name") == name:
            return r["status"], r["timestamp"]
    return ("", "")


def partition_and_sort_scenarios(scs, last_status_by_name):
    # (keep the implementation from Task 3 — do not delete it)
    ...


def _app_name_map() -> dict[str, str]:
    """app_id -> app.name, used for the caption under each recorded card.

    Failing to load the apps dir (e.g. running in a fresh checkout) returns
    an empty map so the list still renders — the caption just shows the raw
    application_id.
    """
    try:
        return {a.id: a.name for a in list_applications(DATA_APPS)}
    except Exception:
        return {}


def render():
    try:
        runs = aggregate_runs(DATA_SCANS)
    except Exception:
        runs = []
        st.caption("(could not load runs — list falls back to alphabetical)")

    scs = list_scenarios(DATA_SCENARIOS)
    last_by_name = {sc.name: _last_status(sc.name, runs) for sc in scs}
    app_names = _app_name_map()

    c1, c2 = st.columns([4, 1])
    c1.subheader("Scenarios")
    if c2.button("+ New scenario", type="primary"):
        st.session_state["_open_scenario"] = "__new__"
        st.rerun()

    if not scs:
        st.info("No scenarios yet. Click + New scenario to create one.")
        return

    if runs:
        has_run, never_run = partition_and_sort_scenarios(scs, last_by_name)
    else:
        has_run, never_run = [], sorted(scs, key=lambda s: s.name)

    def _render_card(sc):
        status, when = last_by_name.get(sc.name, ("", ""))
        pill = {"PASS": ":green[● passing]", "FAIL": ":red[● failing]"}.get(
            status, ":gray[○ never run]",
        )
        app_label = ""
        if sc.kind == "recorded" and sc.application_id:
            app_label = app_names.get(sc.application_id, sc.application_id)
        elif sc.kind in ("single-page", "multi-page"):
            app_label = "(no app)"
        confirm_key = f"_confirm_del_list_{sc.id}"
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            c1.markdown(f"**{sc.name}**  \n{pill} {('· ' + when) if when else ''}")
            n_steps = len(sc.steps) or len(sc.recipe_refs)
            meta = f"{sc.kind} · {len(sc.dataset)} dataset rows · {n_steps} steps"
            if app_label:
                meta = f"{app_label} · {meta}"
            c2.caption(meta)
            if c3.button("Open", key=f"open_{sc.id}"):
                st.session_state["_open_scenario"] = sc.id
                st.rerun()
            if c4.button("🗑", key=f"del_list_{sc.id}", help="Delete scenario"):
                st.session_state[confirm_key] = True
                st.rerun()
            if st.session_state.get(confirm_key):
                st.warning(f"Delete **{sc.name}**? This cannot be undone.")
                cc1, cc2, _ = st.columns([2, 2, 6])
                if cc1.button("Yes, delete", type="primary", key=f"del_yes_{sc.id}"):
                    delete_scenario(DATA_SCENARIOS, sc.id)
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
                if cc2.button("Cancel", key=f"del_no_{sc.id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

    for sc in has_run:
        _render_card(sc)
    if has_run and never_run:
        st.caption("— never run —")
    for sc in never_run:
        _render_card(sc)
```

Notes for the engineer:
- The `...` in `partition_and_sort_scenarios` above is a marker; do NOT replace the real implementation from Task 3 — leave that body intact.
- The list flips between `partition_and_sort_scenarios` (when runs loaded) and an alphabetical fallback so the page still works if `aggregate_runs` raises.

- [ ] **Step 2: Run the targeted test files to verify they still pass**

```
pytest tests/test_scenarios_list_render.py tests/test_scenarios.py -v
```

Expected: all PASS. (No new tests; this task is wiring.)

- [ ] **Step 3: Smoke the page renders in Streamlit**

```
streamlit run app.py
```

Manually verify:
- Navigate to **Scenarios** page.
- Cards are ordered with the most-recently-run at the top.
- Recorded scenarios show their application name in the caption (e.g. `Sauce Demo · recorded · 0 dataset rows · 0 steps`).
- Single-page / multi-page scenarios show `(no app) · ...` in the caption.
- A `— never run —` caption divider appears between groups when both exist.

Stop the server when done (Ctrl+C). If you can't run the UI, say so explicitly in the next step's commit message.

- [ ] **Step 4: Commit**

```
git add ui/scenarios/list.py
git commit -m "feat(ui): /scenarios sorted by last run + app caption"
```

---

## Task 5: Extract `_render_app_list_mode` from `pages/6_recordings.py`

This is a pre-refactor that splits the current "list mode" block into its own function. The block then becomes easier to extend in Task 6 and easier to skip when the new app-detail mode is active in Task 7. No behavior change in this task.

**Files:**
- Modify: [pages/6_recordings.py](../../../pages/6_recordings.py)

- [ ] **Step 1: Wrap the list-mode block in a function**

In `pages/6_recordings.py`, find the section starting with `# --- Mode: list (default) ---` (around line 160) and continuing through the end of the file. Move that entire block into a new function `_render_app_list_mode()` defined ABOVE the first mode block (just under the imports). Then, at the bottom of the file, call `_render_app_list_mode()` in place of the moved code.

The wrapped function MUST be:

```python
def _render_app_list_mode() -> None:
    """Default mode for the page: list applications + the New application form.

    Splitting this out lets the new app-detail mode (Task 7) bypass it cleanly
    when view_app_id is set.
    """
    st.subheader("Applications")
    apps = list_applications(APP_DIR)
    for app in apps:
        cols = st.columns([4, 1, 1, 2, 2])
        cols[0].write(f"**{app.name}** — `{app.base_url_pattern}`")
        cols[1].write("login ✓" if app.login_recording_id else "login ✗")
        health = "🟢" if is_storage_state_valid(app) else "🔴"
        cols[2].write(f"state {health}")
        rec_label = "Re-record login" if app.login_recording_id else "Record login"
        if cols[3].button(rec_label, key=f"rec-{app.id}"):
            st.session_state["login_app_id"] = app.id
            st.session_state["login_url"] = app.base_url_pattern
            st.session_state.pop("login_proc_pid", None)
            st.rerun()
        if cols[4].button("Delete", key=f"del-{app.id}"):
            delete_application(APP_DIR, app.id)
            st.rerun()

    st.divider()
    st.subheader("New application")

    needs_login = st.checkbox(
        "This app requires a login (record it now)",
        value=True,
        key="new_app_needs_login",
        help="Uncheck for sites that don't gate behind authentication. "
        "You can still record a login later from the Re-record button.",
    )

    with st.form("new_app"):
        name = st.text_input("Name")
        base_url = st.text_input("Base URL (login URL if the app has a login)")
        submitted = st.form_submit_button(
            "Create + record login" if needs_login else "Create application"
        )

    if submitted and name and base_url:
        app = Application(
            id="app-" + uuid.uuid4().hex[:8],
            name=name,
            base_url_pattern=base_url,
        )
        save_application(APP_DIR, app)
        if needs_login:
            st.session_state["login_app_id"] = app.id
            st.session_state["login_url"] = base_url
            st.rerun()
        else:
            save_storage_state(STATE_DIR, app.id, {"cookies": [], "origins": []})
            app.storage_state_path = os.path.join(STATE_DIR, app.id + ".enc")
            now = datetime.now(timezone.utc)
            app.storage_state_captured_at = now.isoformat()
            app.storage_state_expires_at = (now + timedelta(days=3650)).isoformat()
            save_application(APP_DIR, app)
            st.success(f"Created **{app.name}** without a login recording.")
            st.rerun()
```

Then at the bottom of the file (after the existing recording-in-progress / just-recorded blocks finish with their `st.stop()` calls):

```python
# --- Mode: list (default) ----------------------------------------------
_render_app_list_mode()
```

- [ ] **Step 2: Manual smoke**

```
streamlit run app.py
```

Open the Recordings page. Confirm the existing behavior is unchanged: applications list renders, Re-record/Delete buttons work, the New application form submits, login recording flow still works. Stop the server.

- [ ] **Step 3: Commit**

```
git add pages/6_recordings.py
git commit -m "refactor(recordings): extract list-mode block into helper"
```

---

## Task 6: Add scenario count + cascade-confirm to app list

**Files:**
- Modify: [pages/6_recordings.py](../../../pages/6_recordings.py)

- [ ] **Step 1: Add the new imports**

In `pages/6_recordings.py`, replace the existing core imports block with:

```python
from core.applications import (
    Application, save_application, list_applications, delete_application,
    delete_application_cascade, load_application,
)
from core.auth_session import save_storage_state, is_storage_state_valid
from core.recording import load_recording, save_recording
from core.scenarios import list_scenarios_for_app
from ui.recording.success_signal_picker import render_picker
```

Add this constant near the existing `APP_DIR`, `STATE_DIR`, `WORK_DIR`:

```python
DATA_SCENARIOS = "data/scenarios"
```

- [ ] **Step 2: Rewrite `_render_app_list_mode`**

Replace the body of `_render_app_list_mode` (the function added in Task 5) with the version below.

```python
def _render_app_list_mode() -> None:
    """Default mode for the page: list applications + the New application form.

    Each app row shows: name/url, login state, scenario count, and three
    actions — Open (drills into app-detail mode in Task 7), Re-record login,
    Delete (cascade-confirm).
    """
    st.subheader("Applications")
    apps = list_applications(APP_DIR)
    for app in apps:
        n_scenarios = len(list_scenarios_for_app(DATA_SCENARIOS, app.id))
        confirm_key = f"_confirm_del_app_{app.id}"
        cols = st.columns([4, 1, 1, 1, 2, 1, 1])
        cols[0].write(f"**{app.name}** — `{app.base_url_pattern}`")
        cols[1].write("login ✓" if app.login_recording_id else "login ✗")
        health = "🟢" if is_storage_state_valid(app) else "🔴"
        cols[2].write(f"state {health}")
        cols[3].caption(
            f"{n_scenarios} scenario" + ("" if n_scenarios == 1 else "s")
        )
        rec_label = "Re-record login" if app.login_recording_id else "Record login"
        if cols[4].button(rec_label, key=f"rec-{app.id}"):
            st.session_state["login_app_id"] = app.id
            st.session_state["login_url"] = app.base_url_pattern
            st.session_state.pop("login_proc_pid", None)
            st.rerun()
        if cols[5].button("Open", key=f"openapp-{app.id}"):
            st.session_state["view_app_id"] = app.id
            st.rerun()
        if cols[6].button("Delete", key=f"del-{app.id}"):
            st.session_state[confirm_key] = True
            st.rerun()

        if st.session_state.get(confirm_key):
            tc_count = sum(
                len(s.ai_test_cases or [])
                for s in list_scenarios_for_app(DATA_SCENARIOS, app.id)
            )
            st.warning(
                f"Delete **{app.name}** and its **{n_scenarios} scenario"
                f"{'' if n_scenarios == 1 else 's'}** + **{tc_count} test case"
                f"{'' if tc_count == 1 else 's'}**? "
                "Recorded replay screenshots will be left on disk. "
                "This cannot be undone."
            )
            cc1, cc2, _ = st.columns([2, 2, 6])
            if cc1.button("Yes, delete", type="primary",
                          key=f"del_yes_{app.id}"):
                delete_application_cascade(
                    APP_DIR, DATA_SCENARIOS, STATE_DIR, WORK_DIR, app.id,
                )
                st.session_state.pop(confirm_key, None)
                st.session_state.pop("view_app_id", None)
                st.rerun()
            if cc2.button("Cancel", key=f"del_no_{app.id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

    st.divider()
    st.subheader("New application")

    needs_login = st.checkbox(
        "This app requires a login (record it now)",
        value=True,
        key="new_app_needs_login",
        help="Uncheck for sites that don't gate behind authentication. "
        "You can still record a login later from the Re-record button.",
    )

    with st.form("new_app"):
        name = st.text_input("Name")
        base_url = st.text_input("Base URL (login URL if the app has a login)")
        submitted = st.form_submit_button(
            "Create + record login" if needs_login else "Create application"
        )

    if submitted and name and base_url:
        app = Application(
            id="app-" + uuid.uuid4().hex[:8],
            name=name,
            base_url_pattern=base_url,
        )
        save_application(APP_DIR, app)
        if needs_login:
            st.session_state["login_app_id"] = app.id
            st.session_state["login_url"] = base_url
            st.rerun()
        else:
            save_storage_state(STATE_DIR, app.id, {"cookies": [], "origins": []})
            app.storage_state_path = os.path.join(STATE_DIR, app.id + ".enc")
            now = datetime.now(timezone.utc)
            app.storage_state_captured_at = now.isoformat()
            app.storage_state_expires_at = (now + timedelta(days=3650)).isoformat()
            save_application(APP_DIR, app)
            st.success(f"Created **{app.name}** without a login recording.")
            st.rerun()
```

- [ ] **Step 3: Manual smoke**

```
streamlit run app.py
```

Verify on the Recordings page:
- Each app row shows a scenario count (e.g. `2 scenarios`).
- An **Open** button appears between Re-record and Delete.
- Clicking **Delete** shows the cascade warning with the count of scenarios + test cases instead of deleting immediately.
- Clicking **Yes, delete** removes the app and all its scenarios; the row disappears and the matching `data/scenarios/*.yaml` files are gone.
- Clicking **Cancel** hides the warning and leaves files intact.
- The **Open** button currently has no effect (it sets `view_app_id` in session state but no view consumes it yet — wired in Task 7).

Stop the server.

- [ ] **Step 4: Commit**

```
git add pages/6_recordings.py
git commit -m "feat(recordings): scenario count + cascade-confirm delete"
```

---

## Task 7: Add app-detail mode to `pages/6_recordings.py`

**Files:**
- Modify: [pages/6_recordings.py](../../../pages/6_recordings.py)

- [ ] **Step 1: Add the `_render_app_detail_mode` function**

In `pages/6_recordings.py`, add the function below directly under `_render_app_list_mode` (still above the first mode block at the top of the file).

```python
def _render_app_detail_mode(app_id: str) -> None:
    """Drill-down view: a single application's scenarios.

    Shows app header (with cascade-delete + back) and the list of recorded
    scenarios for this app. The "+ New scenario" button reuses the existing
    deep-link helper that's already routed by /scenarios."""
    from core.reports import aggregate_runs
    from core.scenarios import delete_scenario

    try:
        app = load_application(APP_DIR, app_id)
    except Exception:
        st.error(
            "That application no longer exists. It may have been deleted "
            "from another tab."
        )
        if st.button("← Back to applications"):
            st.session_state.pop("view_app_id", None)
            st.rerun()
        return

    if st.button("← Back to applications", key=f"back_app_{app.id}"):
        st.session_state.pop("view_app_id", None)
        st.rerun()

    st.title(app.name)
    health = "🟢" if is_storage_state_valid(app) else "🔴"
    st.caption(
        f"{app.base_url_pattern} · "
        f"{'login ✓' if app.login_recording_id else 'login ✗'} · state {health}"
    )

    confirm_key = f"_confirm_del_app_detail_{app.id}"
    if st.button("🗑 Delete app", key=f"del_app_detail_{app.id}"):
        st.session_state[confirm_key] = True
        st.rerun()
    if st.session_state.get(confirm_key):
        scs_here = list_scenarios_for_app(DATA_SCENARIOS, app.id)
        tc_count = sum(len(s.ai_test_cases or []) for s in scs_here)
        st.warning(
            f"Delete **{app.name}** and its **{len(scs_here)} scenario"
            f"{'' if len(scs_here) == 1 else 's'}** + **{tc_count} test case"
            f"{'' if tc_count == 1 else 's'}**? "
            "Recorded replay screenshots will be left on disk. "
            "This cannot be undone."
        )
        cc1, cc2, _ = st.columns([2, 2, 6])
        if cc1.button("Yes, delete", type="primary",
                      key=f"del_app_detail_yes_{app.id}"):
            delete_application_cascade(
                APP_DIR, DATA_SCENARIOS, STATE_DIR, WORK_DIR, app.id,
            )
            st.session_state.pop(confirm_key, None)
            st.session_state.pop("view_app_id", None)
            st.rerun()
        if cc2.button("Cancel", key=f"del_app_detail_no_{app.id}"):
            st.session_state.pop(confirm_key, None)
            st.rerun()

    st.divider()
    c1, c2 = st.columns([4, 1])
    c1.subheader("Scenarios")
    if c2.button("+ New scenario", key=f"newscn_{app.id}", type="primary"):
        st.session_state["_open_scenario"] = "__new__"
        st.session_state["_new_kind"] = "recorded"
        st.session_state["rec_scn_app"] = app.id
        st.session_state.pop("view_app_id", None)
        st.switch_page("pages/3_scenarios.py")

    scs = list_scenarios_for_app(DATA_SCENARIOS, app.id)
    if not scs:
        st.info(
            "No scenarios for this app yet. Click + New scenario to record one."
        )
        return

    try:
        runs = aggregate_runs("data/scans")
    except Exception:
        runs = []
    last_status_by_name: dict[str, tuple[str, str]] = {}
    for r in runs:
        name = r.get("test_case_name", "")
        if name and name not in last_status_by_name:
            last_status_by_name[name] = (r.get("status", ""), r.get("timestamp", ""))

    for sc in scs:
        status, when = last_status_by_name.get(sc.name, ("", ""))
        pill = {"PASS": ":green[● passing]", "FAIL": ":red[● failing]"}.get(
            status, ":gray[○ never run]",
        )
        n_recordings = len([
            r for r in (sc.recordings or [])
            if r.get("id") and r["id"] != "placeholder"
        ])
        n_test_cases = len(sc.ai_test_cases or [])
        del_key = f"_confirm_del_scn_in_app_{sc.id}"
        with st.container(border=True):
            cc = st.columns([3, 2, 1, 1])
            cc[0].markdown(
                f"**{sc.name}**  \n{pill} {('· ' + when) if when else ''}"
            )
            cc[1].caption(
                f"{n_recordings} recording"
                f"{'' if n_recordings == 1 else 's'} · "
                f"{n_test_cases} test case"
                f"{'' if n_test_cases == 1 else 's'}"
            )
            if cc[2].button("Open", key=f"openscn_{sc.id}"):
                st.session_state["_open_scenario"] = sc.id
                st.session_state.pop("view_app_id", None)
                st.switch_page("pages/3_scenarios.py")
            if cc[3].button("🗑", key=f"delscn_in_app_{sc.id}",
                            help="Delete scenario"):
                st.session_state[del_key] = True
                st.rerun()
            if st.session_state.get(del_key):
                st.warning(
                    f"Delete **{sc.name}**? This cannot be undone."
                )
                dc1, dc2, _ = st.columns([2, 2, 6])
                if dc1.button("Yes, delete", type="primary",
                              key=f"delscn_yes_{sc.id}"):
                    delete_scenario(DATA_SCENARIOS, sc.id)
                    st.session_state.pop(del_key, None)
                    st.rerun()
                if dc2.button("Cancel", key=f"delscn_no_{sc.id}"):
                    st.session_state.pop(del_key, None)
                    st.rerun()
```

- [ ] **Step 2: Route the new mode at the bottom of the file**

Replace the final block `# --- Mode: list (default) ---` and the call `_render_app_list_mode()` with:

```python
# --- Mode: app-detail (view_app_id set) -------------------------------
view_app_id = st.session_state.get("view_app_id")
if view_app_id:
    _render_app_detail_mode(view_app_id)
    st.stop()

# --- Mode: list (default) ---------------------------------------------
_render_app_list_mode()
```

This block goes AFTER the existing recording-in-progress / just-recorded blocks (which already call `st.stop()`), so the order is:

1. `# --- Mode: just-recorded ---` (existing)
2. `# --- Mode: recording in progress ---` (existing)
3. `# --- Mode: app-detail ---` (NEW)
4. `# --- Mode: list (default) ---` (existing call)

- [ ] **Step 3: Manual smoke**

```
streamlit run app.py
```

Verify the full flow:
- Open Recordings page; click **Open** on an app that has scenarios.
- The app-detail view appears with: title, caption, Delete app button, "+ New scenario" button, and a scenario card list.
- Each scenario card shows the last-run pill, the recordings + test-case count, an Open button (jumps to `/scenarios` detail), and a 🗑 button (per-scenario confirm).
- **+ New scenario** lands in `/scenarios` with the new-recorded-scenario form populated for this app — i.e. the same flow as the existing "Record a scenario" CTA on the post-login screen.
- **🗑 Delete app** from inside detail mode shows the cascade warning and, on confirm, returns to the Applications list with the app gone.
- **← Back to applications** clears `view_app_id` and returns to the list.
- If you delete the currently-viewed app from another tab (or its YAML disappears), the detail view shows an error + a back button instead of crashing.

Stop the server.

- [ ] **Step 4: Commit**

```
git add pages/6_recordings.py
git commit -m "feat(recordings): app-detail drill-in with scenarios + cascade delete"
```

---

## Task 8: Full regression

**Files:** none modified.

- [ ] **Step 1: Run the full test suite**

```
pytest -q
```

Expected: all tests pass. The new tests from Tasks 1-3 should be among them.

- [ ] **Step 2: Sanity-check the existing scenarios YAMLs still load**

```
python -c "from core.scenarios import list_scenarios; print(len(list_scenarios('data/scenarios')))"
```

Expected: a non-zero count (matches what's in `data/scenarios/` minus `_migrated.yaml`).

- [ ] **Step 3: Final manual walk-through**

`streamlit run app.py` and walk through the user-facing flow end-to-end:

1. Applications list shows scenario counts and the new Open button.
2. Drill into an app → see its scenarios.
3. Click "+ New scenario" → land on the new-recorded-scenario form for that app.
4. Cancel back → list a scenario, click Open → land on the scenario detail page with its recordings + test cases (unchanged).
5. Go to the standalone Scenarios page → cards are sorted by last-run timestamp with never-run sinking below the divider, and recorded cards show their app name.
6. Cascade-delete an app from either the list or detail view → confirm dialog shows scenario + test-case counts → confirm → app and its scenarios are gone.

- [ ] **Step 4: Final commit (only if any cleanup was needed)**

If the walk-through revealed nothing to change, this step is a no-op. Otherwise commit the cleanup.

---

## Self-review notes

**Spec coverage:**
- `/recordings` list mode (count + Open + cascade confirm) → Task 6.
- `/recordings` app-detail mode → Task 7.
- `+ New scenario` deep-link from app detail → Task 7, Step 1 (`_open_scenario = "__new__"`, `_new_kind = "recorded"`, `rec_scn_app = app.id`, then `switch_page`).
- Cascade delete with confirm → Tasks 2, 6, 7.
- `/scenarios` flat list sort + app caption + never-run divider → Tasks 3, 4.
- Idempotent cascade + replay-runs left alone → Task 2 tests.
- "Missing app YAML" handling for the app-detail mode → Task 7, Step 1.

**No placeholders:** every step shows the actual code to write, the exact commands to run, and the expected outcome. The `...` in Task 4's `partition_and_sort_scenarios` is explicitly annotated as a "do-not-replace" marker.

**Type consistency:** `delete_application_cascade` signature, return dict shape, and session-state keys (`view_app_id`, `_confirm_del_app_<id>`, `_confirm_del_app_detail_<id>`, `_confirm_del_scn_in_app_<id>`) are consistent across Tasks 2, 6, 7.
