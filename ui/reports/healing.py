import streamlit as st
import pandas as pd
from core.reports import aggregate_heal_events
from core.dataset_io import dataset_to_xlsx_bytes

DATA_SCANS = "data/scans"


def render():
    events = aggregate_heal_events(DATA_SCANS)
    if not events:
        st.info("No healing events yet.")
        return
    q = st.text_input("Filter (element / change type / healer)", "", key="heal_q").strip().lower()
    filtered = [e for e in events if not q or any(q in str(v).lower() for v in e.values())]

    rows = []
    for rec in filtered:
        row = {
            "url": rec.get("url", ""),
            "heal_id": rec.get("heal_id", ""),
            "timestamp": rec.get("timestamp", ""),
            "element_name": rec.get("element_name", ""),
            "change_type": rec.get("change_type", ""),
            "change_details": rec.get("change_details", ""),
            "healed_by": rec.get("healed_by", ""),
            "Why": (rec.get("rationale") or "")[:80],
        }
        conf = rec.get("confidence")
        row["Confidence"] = f"{float(conf):.0%}" if conf is not None else "—"
        rows.append(row)

    st.dataframe(pd.DataFrame(rows), use_container_width=True)
    st.download_button(
        "⬇ Export Excel",
        data=dataset_to_xlsx_bytes(filtered),
        file_name="healing_log.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
