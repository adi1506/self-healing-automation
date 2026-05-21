"""Inline editor for a recording's steps.

Renders one row per step with action, target, editable value, delete, and
insert-above buttons. Task D4 (revert popover) will build on top of this.
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
        "edit step values inline, insert a fill step above any row, or "
        "delete a step. Revert from heal history ships in a follow-up."
    )

    if not rec.steps:
        st.info("This recording has no steps yet. Use the + button below to add one.")
        if st.button("+ Add a fill step", key=f"rec_add_first_{recording_id}"):
            rec.steps.append(Step(index=0, action="fill", value="",
                                  element=None, inserted_by="user_edit"))
            on_save(rec)
            st.rerun()
        return

    # Render each step as a row
    for i, s in enumerate(rec.steps):
        cols = st.columns([0.5, 1.5, 3, 3, 0.5, 0.5, 0.6])
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
        # Delete
        if cols[4].button("🗑", key=f"rec_del_{recording_id}_{i}",
                          help="Delete this step"):
            del rec.steps[i]
            for j, ss in enumerate(rec.steps):
                ss.index = j
            on_save(rec)
            st.rerun()
        # Insert above
        if cols[5].button("+", key=f"rec_ins_{recording_id}_{i}",
                          help="Insert empty fill step above"):
            new_step = Step(index=i, action="fill", value="",
                            element=None, inserted_by="user_edit")
            rec.steps.insert(i, new_step)
            for j, ss in enumerate(rec.steps):
                ss.index = j
            on_save(rec)
            st.rerun()
        # Marker
        marker_parts = []
        if s.inserted_by:
            marker_parts.append(f":gray[{s.inserted_by}]")
        if s.element and s.element.fingerprint_history:
            marker_parts.append(f":gray[↶{len(s.element.fingerprint_history)}]")
        cols[6].markdown(" ".join(marker_parts) or "")

    # Save (for value edits)
    if st.button("Save value edits", type="primary",
                 key=f"rec_save_{recording_id}"):
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
