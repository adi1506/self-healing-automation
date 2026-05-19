import streamlit as st
from core.scenarios import list_scenarios, delete_scenario
from core.applications import list_applications
from core.reports import aggregate_runs

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"
DATA_APPS = "data/applications"


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


def _last_status(name: str, runs: list[dict]) -> tuple[str, str]:
    for r in runs:
        if r.get("test_case_name") == name:
            return r["status"], r["timestamp"]
    return ("", "")


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
