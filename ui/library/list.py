import streamlit as st
from core.excel_manager import ExcelManager
from core.scenarios import list_scenarios

DATA_SCANS = "data/scans"
DATA_SCENARIOS = "data/scenarios"


def _scenarios_using(url: str) -> list[str]:
    return [s.name for s in list_scenarios(DATA_SCENARIOS) if s.base_url == url]


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
            if d2.button("Rescan", key=f"rescan_{url}"):
                st.session_state["_rescan_url"] = url
                st.rerun()
