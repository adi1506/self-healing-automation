import streamlit as st
from datetime import datetime
from core.excel_manager import ExcelManager
from core.scanner import Scanner
from core.scenarios import list_scenarios

DATA_SCANS = "data/scans"
DATA_SCENARIOS = "data/scenarios"

# Fields the scanner controls — used to detect whether a re-found element actually changed.
_DIFF_FIELDS = [
    "element_type",
    "locator_id", "locator_name", "locator_css", "locator_xpath",
    "locator_data_testid", "locator_label",
    "placeholder", "available_options",
    "pattern", "title_attr", "minlength", "maxlength",
    "min", "max", "required", "autocomplete", "helper_text",
]


def _scenarios_using(url: str) -> list[str]:
    return [s.name for s in list_scenarios(DATA_SCENARIOS) if s.base_url == url]


def _identity_keys(elem: dict) -> set[tuple[str, str]]:
    """Stable locator signatures that survive a label/name change."""
    keys: set[tuple[str, str]] = set()
    for f in ("locator_id", "locator_data_testid", "locator_name"):
        v = elem.get(f)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            keys.add((f, s))
    return keys


def _field_diffs(old_e: dict, new_e: dict) -> list[tuple[str, str, str]]:
    out = []
    for f in _DIFF_FIELDS:
        ov = "" if old_e.get(f) is None else str(old_e.get(f))
        nv = "" if new_e.get(f) is None else str(new_e.get(f))
        if ov != nv:
            out.append((f, ov, nv))
    return out


def _diff_elements(old: list[dict], new: list[dict]) -> dict:
    old_by_name = {e.get("element_name", ""): e for e in old if e.get("element_name")}
    new_by_name = {e.get("element_name", ""): e for e in new if e.get("element_name")}

    added = set(new_by_name) - set(old_by_name)
    removed = set(old_by_name) - set(new_by_name)
    changed: list[dict] = []
    unchanged: list[str] = []
    renamed: list[dict] = []

    # Pair up removed↔added by stable locator signature → rename, not remove+add.
    new_keys = {name: _identity_keys(new_by_name[name]) for name in added}
    for old_name in list(removed):
        old_e = old_by_name[old_name]
        old_keys = _identity_keys(old_e)
        if not old_keys:
            continue
        match = next(
            (n for n in added if new_keys.get(n) and old_keys & new_keys[n]),
            None,
        )
        if match is None:
            continue
        new_e = new_by_name[match]
        extra = _field_diffs(old_e, new_e)
        renamed.append({"old_name": old_name, "new_name": match, "fields": extra})
        removed.discard(old_name)
        added.discard(match)

    for name in sorted(set(old_by_name) & set(new_by_name)):
        old_e, new_e = old_by_name[name], new_by_name[name]
        field_diffs = _field_diffs(old_e, new_e)
        if field_diffs:
            changed.append({"name": name, "fields": field_diffs})
        else:
            unchanged.append(name)

    return {
        "new": sorted(added),
        "removed": sorted(removed),
        "renamed": sorted(renamed, key=lambda r: r["new_name"]),
        "changed": changed,
        "unchanged": unchanged,
    }


def _render_rescan_summary(url: str) -> None:
    pending = st.session_state.get("_rescan_diff")
    if not pending or pending.get("url") != url:
        return
    diff = pending["diff"]
    n_new = len(diff["new"])
    n_chg = len(diff["changed"])
    n_rem = len(diff["removed"])
    n_ren = len(diff.get("renamed", []))
    n_unc = len(diff["unchanged"])
    total_changes = n_new + n_chg + n_rem + n_ren
    if total_changes == 0:
        st.success(f"Rescan complete — no changes ({n_unc} unchanged).")
    else:
        parts = []
        if n_new: parts.append(f"{n_new} new")
        if n_chg: parts.append(f"{n_chg} changed")
        if n_ren: parts.append(f"{n_ren} renamed")
        if n_rem: parts.append(f"{n_rem} removed")
        parts.append(f"{n_unc} unchanged")
        st.success("Rescan complete — " + ", ".join(parts) + ".")
    with st.expander("What changed?", expanded=total_changes > 0):
        if diff.get("renamed"):
            st.markdown("**Renamed fields:**")
            for item in diff["renamed"]:
                st.markdown(f"- `{item['old_name']}` → `{item['new_name']}`")
                for field, old_v, new_v in item["fields"]:
                    st.caption(f"    {field}: `{old_v or '∅'}` → `{new_v or '∅'}`")
        if diff["new"]:
            st.markdown("**New fields:**")
            for name in diff["new"]:
                st.markdown(f"- `{name}`")
        if diff["removed"]:
            st.markdown("**Removed fields:**")
            for name in diff["removed"]:
                st.markdown(f"- `{name}`")
        if diff["changed"]:
            st.markdown("**Changed fields:**")
            for item in diff["changed"]:
                st.markdown(f"- `{item['name']}`")
                for field, old_v, new_v in item["fields"]:
                    st.caption(f"    {field}: `{old_v or '∅'}` → `{new_v or '∅'}`")
        if total_changes == 0:
            st.caption("All fields matched the previous scan.")
    if st.button("Dismiss", key=f"dismiss_rescan_{url}"):
        st.session_state.pop("_rescan_diff", None)
        st.rerun()


def render():
    em = ExcelManager(data_dir=DATA_SCANS)
    urls = em.list_scanned_urls()
    if not urls:
        st.info("No scans yet. Use the Scan form above to add one.")
        return

    filter_q = st.text_input("Filter by URL", "").strip().lower()
    for url in urls:
        if filter_q and filter_q not in url.lower():
            continue
        elements = em.read_element_map(url)
        using = _scenarios_using(url)
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{url}**")
            c1.caption(f"{len(elements)} fields"
                       + (f" · used in {len(using)} scenarios" if using else " · not used yet"))
            if c2.button("Delete", key=f"del_{url}"):
                em.delete_url(url)
                st.rerun()

            with st.expander("View fields"):
                from ui.library.fields_view import render as render_fields
                render_fields(url)

            d1, d2 = st.columns(2)
            with open(em.get_excel_path(url), "rb") as f:
                d1.download_button(
                    "Download Excel", data=f.read(),
                    file_name=f"scan_{em.sanitize_url(url)}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_{url}",
                )
            _render_rescan_summary(url)

            if d2.button("Rescan", key=f"rescan_{url}"):
                with st.spinner(f"Rescanning {url}..."):
                    scanner = Scanner()
                    result = scanner.scan_with_context(url)
                    new_elements = result["elements"]
                    page_context = result["page_context"]
                if new_elements:
                    old_elements = em.read_element_map(url)
                    diff = _diff_elements(old_elements, new_elements)
                    em.save_element_map(url, new_elements)
                    em.save_page_context(url, page_context)
                    em.append_scan_history(url, {
                        "scan_id": f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "total_elements": len(new_elements),
                        "new": len(diff["new"]),
                        "changed": len(diff["changed"]) + len(diff.get("renamed", [])),
                        "removed": len(diff["removed"]),
                        "unchanged": len(diff["unchanged"]),
                    })
                    st.session_state["_rescan_diff"] = {"url": url, "diff": diff}
                    st.rerun()
                else:
                    st.warning("Rescan returned no interactive elements.")
