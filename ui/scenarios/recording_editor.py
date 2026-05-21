"""Inline editor for a recording's steps.

Renders one row per step with action, target, and editable value. Tasks
D3 (insert/delete) and D4 (revert popover) will build on top of this
read-only baseline.
"""
import streamlit as st
from core.recording import Recording, Step


def render(scenario, recording_id: str, on_save) -> None:
    """Render an inline editor for a recording's steps.

    `on_save(rec: Recording)` persists changes back to the scenario.
    `recording_id` selects which of the scenario's recordings to edit.
    """
    rec_dict = next(
        (r for r in scenario.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        st.error(f"Recording {recording_id!r} not found on this scenario.")
        return
    rec = Recording.from_dict(rec_dict)

    st.caption(
        "ⓘ **Recording steps** — captured from real interactions. You can "
        "edit step values inline. Inserts, deletes, and revert from history "
        "ship in a follow-up."
    )

    if not rec.steps:
        st.info("This recording has no steps yet.")
        return

    # Render each step as a row of inputs
    for i, s in enumerate(rec.steps):
        cols = st.columns([0.5, 1.5, 3, 3, 0.7])
        cols[0].write(f"**{i}**")
        cols[1].write(f"`{s.action}`")
        cols[2].markdown(_target_label(s) or ":gray[(no target)]")
        if s.action in ("fill", "select", "press"):
            new_val = cols[3].text_input(
                "Value",
                value=s.value or "",
                label_visibility="collapsed",
                key=f"rec_val_{recording_id}_{i}",
            )
            s.value = new_val or None
        else:
            cols[3].markdown(f":gray[{s.value or ''}]")
        # Source/history marker
        marker_parts = []
        if s.inserted_by:
            marker_parts.append(f":gray[{s.inserted_by}]")
        if s.element and s.element.fingerprint_history:
            marker_parts.append(f":gray[↶{len(s.element.fingerprint_history)}]")
        cols[4].markdown(" ".join(marker_parts) or "")

    if st.button("Save changes", type="primary", key=f"rec_save_{recording_id}"):
        on_save(rec)
        st.success("Recording saved.")
        st.rerun()


def _target_label(step: Step) -> str:
    if step.element is None:
        return ""
    a = step.element.attributes
    return (
        a.get("nearest_label_text")
        or a.get("id")
        or a.get("name")
        or step.element.primary_locator.get("value", "")
        or ""
    )
