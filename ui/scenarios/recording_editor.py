"""Inline editor for a recording's steps.

Renders one row per step with action, target, editable value, delete,
insert-above, and revert-from-history buttons.
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
        "edit step values inline, insert a fill step above any row, delete "
        "a step, or revert a locator to an earlier heal-history state."
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
        cols = st.columns([0.5, 1.5, 3, 3, 0.5, 0.5, 0.5, 0.6])
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
        # Revert (only when there's history)
        if s.element and s.element.fingerprint_history:
            if cols[6].button("↶", key=f"rec_rev_{recording_id}_{i}",
                              help="Revert from heal history"):
                st.session_state[f"_rev_popup_{recording_id}_{i}"] = True
                st.rerun()
        else:
            cols[6].write("")
        # Marker (now at index 7)
        marker_parts = []
        if s.inserted_by:
            marker_parts.append(f":gray[{s.inserted_by}]")
        if s.element and s.element.fingerprint_history:
            marker_parts.append(f":gray[↶{len(s.element.fingerprint_history)}]")
        cols[7].markdown(" ".join(marker_parts) or "")

        # Revert popover — rendered beneath the row when its flag is set
        if st.session_state.get(f"_rev_popup_{recording_id}_{i}", False):
            with st.container(border=True):
                st.markdown(f"**Revert step {i}**")
                if not (s.element and s.element.fingerprint_history):
                    st.info("No history to revert from.")
                else:
                    history = s.element.fingerprint_history
                    for hidx in range(len(history) - 1, -1, -1):
                        h = history[hidx]
                        old = h.previous_primary_locator or {}
                        st.markdown(
                            f"- `{old.get('strategy', '')}:{old.get('value', '')}` "
                            f":gray[— {h.timestamp} · run `{h.run_id}` · "
                            f"conf {(h.confidence or 0):.0%}]"
                        )
                        if st.button(
                            "Revert to this state",
                            key=f"rec_revchoose_{recording_id}_{i}_{hidx}",
                        ):
                            _apply_revert(s, hidx)
                            on_save(rec)
                            st.session_state.pop(f"_rev_popup_{recording_id}_{i}", None)
                            st.rerun()
                if st.button("Cancel", key=f"rec_revcancel_{recording_id}_{i}"):
                    st.session_state.pop(f"_rev_popup_{recording_id}_{i}", None)
                    st.rerun()

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


def _apply_revert(step: Step, target_hidx: int) -> None:
    """Revert the given step's element to the state captured at history
    index `target_hidx`. Pushes the current state onto the history first
    so the revert is itself revertable. Truncates history above the
    chosen index.

    History dance (example: history=[h0,h1,h2,h3], revert to h1):
      1. Append h_now (previous_*=current active state) → [h0,h1,h2,h3,h_now]
      2. Restore active state to h1.previous_*
      3. Truncate → history[:1] + [h_now] = [h0, h_now]
    After: active = h1's previous values; clicking revert on h_now
    restores the pre-revert state. h1/h2/h3 are discarded (superseded).
    """
    from datetime import datetime, timezone
    from core.recording import HistoryEntry

    if step.element is None or not step.element.fingerprint_history:
        return
    history = step.element.fingerprint_history
    if not (0 <= target_hidx < len(history)):
        return
    target = history[target_hidx]
    now = datetime.now(timezone.utc).isoformat()
    # 1. Push current state onto history so the revert is itself revertable.
    history.append(HistoryEntry(
        timestamp=now,
        run_id="<revert>",
        source="manual_edit",
        confidence=None,
        previous_primary_locator=dict(step.element.primary_locator),
        previous_fallback_locators=[dict(x) for x in step.element.fallback_locators],
        previous_attributes=dict(step.element.attributes),
    ))
    # 2. Restore the chosen previous state.
    step.element.primary_locator = dict(target.previous_primary_locator)
    step.element.fallback_locators = [dict(x) for x in target.previous_fallback_locators]
    step.element.attributes = dict(target.previous_attributes)
    # 3. Truncate: keep entries older than target, plus the just-appended entry.
    step.element.fingerprint_history = history[:target_hidx] + [history[-1]]
