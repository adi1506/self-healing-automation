import streamlit as st
import pandas as pd
from core.reports import aggregate_runs
from core.dataset_io import dataset_to_xlsx_bytes

DATA_SCANS = "data/scans"


def render():
    runs = aggregate_runs(DATA_SCANS)
    if not runs:
        st.info("No runs yet.")
        return

    _render_ai_summary_for_latest_failure(runs)

    q = st.text_input("Filter by scenario / status", "").strip().lower()
    filtered = [r for r in runs if not q or q in (r.get("test_case_name", "").lower()
                                                  + r.get("status", "").lower())]
    df = pd.DataFrame(filtered)
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "⬇ Export Excel",
        data=dataset_to_xlsx_bytes(filtered),
        file_name="run_history.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _render_ai_summary_for_latest_failure(runs: list[dict]) -> None:
    failed = [r for r in runs if (r.get("status") or "").upper() in ("FAIL", "FAILED", "ERROR")]
    if not failed:
        return
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    if not svc.is_available():
        return

    latest = failed[0]  # aggregate_runs returns newest-first
    run_record = _record_from_aggregate_row(latest, runs)
    with st.spinner("Summarizing the most recent failure…"):
        summary = svc.summarize_run(run_record)
    if summary:
        st.warning(f"**AI summary — {latest.get('test_case_name', '')}** — {summary}")


def _record_from_aggregate_row(row: dict, all_runs: list[dict]) -> dict:
    """Synthesize a run_record from the aggregated row + sibling rows in the same run."""
    run_id = f"{row.get('timestamp', '')}::{row.get('test_case_name', '')}"
    siblings = [r for r in all_runs
                if r.get("timestamp") == row.get("timestamp")
                and r.get("test_case_name") == row.get("test_case_name")]
    steps = [{
        "action": "verify",
        "target": s.get("element_name", ""),
        "outcome": (s.get("status") or "").lower(),
        "error": "" if (s.get("status") or "").upper() == "PASS"
                 else f"expected '{s.get('Expected Value', '')}', got '{s.get('Actual Value', '')}'",
    } for s in siblings]
    return {
        "id": run_id,
        "scenario_name": row.get("test_case_name", ""),
        "steps": steps,
        "healings": [],
    }
