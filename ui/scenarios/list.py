import streamlit as st
from core.scenarios import list_scenarios, delete_scenario
from core.reports import aggregate_runs

DATA_SCENARIOS = "data/scenarios"
DATA_SCANS = "data/scans"


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


def render():
    runs = aggregate_runs(DATA_SCANS)
    scs = list_scenarios(DATA_SCENARIOS)

    c1, c2 = st.columns([4, 1])
    c1.subheader("Scenarios")
    if c2.button("+ New scenario", type="primary"):
        st.session_state["_open_scenario"] = "__new__"
        st.rerun()

    if not scs:
        st.info("No scenarios yet. Click + New scenario to create one.")
        return

    for sc in scs:
        status, when = _last_status(sc.name, runs)
        pill = {"PASS": ":green[● passing]", "FAIL": ":red[● failing]"}.get(status, ":gray[○ never run]")
        confirm_key = f"_confirm_del_list_{sc.id}"
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            c1.markdown(f"**{sc.name}**  \n{pill} {('· ' + when) if when else ''}")
            c2.caption(f"{sc.kind} · {len(sc.dataset)} dataset rows · {len(sc.steps) or len(sc.recipe_refs)} steps")
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
