"""Shared helpers for rendering grouped run results.

Used by the Runs tab on a single scenario and by the Dashboard's recent-runs
section. The two surfaces show the same hierarchy (run → row → field rows +
screenshot), so the grouping and HTML rendering live here.
"""
from __future__ import annotations

import base64
import html
import os
from collections import OrderedDict

import streamlit as st


def group_runs(rows: list[dict]) -> "OrderedDict[str, dict]":
    """Collapse a flat list of field-result rows (as returned by aggregate_runs)
    into one bucket per run, each containing one bucket per row_label.

    The input is expected to already be sorted newest-first; the OrderedDict
    iteration order preserves that.

    Falls back to timestamp as the run key for legacy rows that predate run_id
    being written, so old data still groups as one entry per run instead of
    showing every field as its own run.
    """
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for r in rows:
        key = r.get("run_id") or r.get("timestamp") or "unknown"
        bucket = groups.get(key)
        if bucket is None:
            bucket = {
                "run_id": r.get("run_id", ""),
                "timestamp": r.get("timestamp", ""),
                "url": r.get("url", ""),
                "test_case_name": r.get("test_case_name", ""),
                "rows": OrderedDict(),
            }
            groups[key] = bucket
        row_label = r.get("row_label") or "—"
        row_bucket = bucket["rows"].setdefault(
            row_label,
            {"label": row_label, "fields": [], "screenshot": ""},
        )
        row_bucket["fields"].append(r)
        if not row_bucket["screenshot"] and r.get("screenshot"):
            row_bucket["screenshot"] = r["screenshot"]
    return groups


def run_status(row_buckets: dict) -> tuple[str, int, int]:
    """Compute (icon, passed_rows, total_rows) for a run. A row passes only if
    every field in it is PASS — same shape as the live result render."""
    total = len(row_buckets)
    passed = 0
    for rb in row_buckets.values():
        if rb["fields"] and all(f.get("status") == "PASS" for f in rb["fields"]):
            passed += 1
    icon = "✓" if passed == total and total > 0 else "✗"
    return icon, passed, total


def _row_status(fields: list[dict]) -> tuple[str, str]:
    if fields and all(f.get("status") == "PASS" for f in fields):
        return "✓", "PASS"
    return "✗", "FAIL"


def _image_data_uri(path: str) -> str | None:
    """Encode an image as a base64 data URI so it can sit inside the same HTML
    block as the <details> markup. Streamlit's st.image can't be placed inside
    a markdown(unsafe_allow_html=True) block, so we inline the bytes."""
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{b64}"


def render_run_body(bucket: dict) -> None:
    """Render all rows of a single run as one HTML block.

    Streamlit forbids nested st.expander, so the row tier uses native <details>
    inside an unsafe_allow_html markdown block. That also lets us drop the
    per-row screenshot in-place via an <img data URI>.
    """
    parts: list[str] = ["<div class='runs-row-list'>"]
    for row_label, rb in bucket["rows"].items():
        r_icon, r_status = _row_status(rb["fields"])
        open_attr = "" if r_status == "PASS" else " open"
        summary = html.escape(f"{r_icon} {row_label} — {r_status}")
        parts.append(f"<details{open_attr} style='margin:6px 0;'>")
        parts.append(
            f"<summary style='cursor:pointer; padding:6px 8px; "
            f"background:rgba(255,255,255,0.04); border-radius:4px;'>"
            f"{summary}</summary>"
        )
        parts.append("<div style='padding:8px 0 8px 18px;'>")
        for fr in rb["fields"]:
            line = (
                f"[{html.escape(fr.get('status', ''))}] "
                f"{html.escape(fr.get('element_name', ''))}: "
                f"expected={html.escape(repr(fr.get('expected_value', '')))} "
                f"actual={html.escape(repr(fr.get('actual_value', '')))}"
            )
            parts.append(
                f"<div style='font-family:monospace; font-size:0.9em; "
                f"padding:2px 0;'>{line}</div>"
            )
        shot = rb.get("screenshot")
        if shot and os.path.exists(shot):
            uri = _image_data_uri(shot)
            if uri:
                parts.append(
                    f"<div style='margin-top:8px;'>"
                    f"<img src='{uri}' alt='submitted form' "
                    f"style='max-width:100%; border:1px solid rgba(255,255,255,0.1); "
                    f"border-radius:4px;' />"
                    f"<div style='font-size:0.85em; opacity:0.7; margin-top:4px;'>"
                    f"{html.escape(row_label)} — submitted form"
                    f"</div></div>"
                )
        parts.append("</div></details>")
    parts.append("</div>")
    st.markdown("\n".join(parts), unsafe_allow_html=True)
