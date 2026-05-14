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
