"""Inline editor for a recording's steps.

Renders one row per step with action, target, editable value, delete,
insert-above, and revert-from-history buttons.
"""
import streamlit as st
from core.recording import Recording, Step


def _is_data_entry(step) -> bool:
    """True for steps that carry a value worth locking / hinting."""
    return step.action in ("fill", "select", "press", "check", "uncheck") and step.element is not None


def render(
    scenario,
    recording_id: str,
    on_save,
    *,
    scroll_to_step_index: int | None = None,
) -> None:
    """Render an inline editor for a recording's steps.

    `on_save(rec: Recording)` persists changes back to the scenario.
    `recording_id` selects which of the scenario's recordings to edit.
    `scroll_to_step_index`, if provided, highlights that row and pre-selects
    'insert above' on it — used by the "Add step manually" CTA from a
    failed replay.
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
        "edit values inline (text for fill/press, dropdown for select with "
        "captured options, checkbox toggle for checkbox steps), insert a "
        "fill step above any row, delete a step, or revert a locator to an "
        "earlier heal-history state. Hit **Save value edits** to persist."
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
        cols = st.columns([0.5, 1.5, 3, 3, 0.7, 0.7, 0.5, 0.5, 0.5, 0.6])
        if scroll_to_step_index is not None and i == scroll_to_step_index:
            st.markdown(
                f":blue-background[**👉 Add a step here** — the failed "
                f"replay couldn't find this step's target.]"
            )
        cols[0].write(f"**{i}**")
        cols[1].write(f"`{s.action}`")
        cols[2].markdown(_target_label(s) or ":gray[(no target)]")
        _render_value_widget(cols[3], s, recording_id, i)
        # Manual-assist toggle — pause replay here, let the user act in
        # the page. Auto-detected at record time for captcha/OTP-like
        # fields; this lets the user override either way.
        new_needs_manual = cols[4].checkbox(
            "⏸",
            value=s.needs_manual,
            label_visibility="collapsed",
            key=f"rec_manual_{recording_id}_{i}",
            help=(
                "Pause replay at this step and let me solve it manually "
                "(captcha, OTP, etc.). Forces headed browser at replay."
            ),
        )
        if new_needs_manual != s.needs_manual:
            s.needs_manual = new_needs_manual
            on_save(rec)
            st.rerun()
        # Value lock — keep the recorded value; AI won't vary this field.
        if _is_data_entry(s):
            new_locked = cols[5].checkbox(
                "🔒",
                value=getattr(s, "locked_value", False),
                label_visibility="collapsed",
                key=f"rec_lock_{recording_id}_{i}",
                help=("Lock this value — the AI keeps your recorded value here "
                      "and won't generate variations for this field (e.g. "
                      "username, password, a fixed account number)."),
            )
            if new_locked != getattr(s, "locked_value", False):
                s.locked_value = new_locked
                on_save(rec)
                st.rerun()
        else:
            cols[5].write("")
        # Delete
        if cols[6].button("🗑", key=f"rec_del_{recording_id}_{i}",
                          help="Delete this step"):
            del rec.steps[i]
            for j, ss in enumerate(rec.steps):
                ss.index = j
            on_save(rec)
            st.rerun()
        # Insert above
        if cols[7].button("+", key=f"rec_ins_{recording_id}_{i}",
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
            if cols[8].button("↶", key=f"rec_rev_{recording_id}_{i}",
                              help="Revert from heal history"):
                st.session_state[f"_rev_popup_{recording_id}_{i}"] = True
                st.rerun()
        else:
            cols[8].write("")
        # Marker (now at index 9)
        marker_parts = []
        if s.inserted_by:
            marker_parts.append(f":gray[{s.inserted_by}]")
        if s.element and s.element.fingerprint_history:
            marker_parts.append(f":gray[↶{len(s.element.fingerprint_history)}]")
        if getattr(s, "locked_value", False):
            marker_parts.append(":blue[🔒]")
        cols[9].markdown(" ".join(marker_parts) or "")

        # Per-field AI hint — shown only for data-entry steps, and only when the
        # value is not locked (a fixed value needs no hint).
        if _is_data_entry(s) and not getattr(s, "locked_value", False):
            with st.expander("💬 AI hint for this field", expanded=bool(s.field_context)):
                new_ctx = st.text_area(
                    "Tell the AI how to fill this field "
                    "(used when it keeps getting this field wrong)",
                    value=s.field_context or "",
                    key=f"rec_fieldctx_{recording_id}_{i}",
                    placeholder="e.g. PAN = 5 uppercase letters, 4 digits, 1 letter (AAAAA9999A)",
                )
                normalized = new_ctx.strip() or None
                if normalized != s.field_context:
                    s.field_context = normalized
                    on_save(rec)
                    st.rerun()

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


def _render_value_widget(col, s: Step, recording_id: str, i: int) -> None:
    """Render the right widget for this step's value column.

    Dispatch matrix:
      - fill / press                → text input (free-form string)
      - select with captured opts   → dropdown of {label, value}
      - select without opts         → text input (older recordings)
      - check/uncheck on a checkbox → st.checkbox toggling action between
                                       'check' and 'uncheck' (Playwright
                                       .check()/.uncheck() ignores `value`)
      - check on a radio            → gray text (value identifies which
                                       radio; changing it does nothing on
                                       replay since .check() ignores value)
      - everything else             → gray text
    """
    attrs = (s.element.attributes if s.element else {}) or {}
    elem_type = (attrs.get("type") or "").lower()
    elem_role = (attrs.get("role") or "").lower()

    if s.action in ("fill", "press"):
        new_val = col.text_input(
            "Value",
            value=s.value or "",
            label_visibility="collapsed",
            key=f"rec_val_{recording_id}_{i}",
        )
        s.value = new_val or None
        return

    if s.action == "select":
        opts = attrs.get("select_options") or []
        if isinstance(opts, list) and opts:
            values = [str(o.get("value", "")) for o in opts]
            labels = {
                str(o.get("value", "")): (
                    str(o.get("label") or o.get("value") or "")
                )
                for o in opts
            }
            # If the current value isn't in the captured options (e.g. a
            # test-case override set a custom string, or the options changed
            # since capture), prepend it so the dropdown can still show it.
            if s.value and s.value not in values:
                values = [s.value] + values
                labels.setdefault(s.value, s.value)
            idx = values.index(s.value) if s.value in values else 0
            new_val = col.selectbox(
                "Value",
                options=values,
                index=idx,
                format_func=lambda v: labels.get(v, v),
                label_visibility="collapsed",
                key=f"rec_val_{recording_id}_{i}",
            )
            s.value = new_val or None
            return
        # Fallback for recordings made before option capture landed.
        new_val = col.text_input(
            "Value",
            value=s.value or "",
            label_visibility="collapsed",
            key=f"rec_val_{recording_id}_{i}",
        )
        s.value = new_val or None
        return

    if s.action in ("check", "uncheck") and (
        elem_type == "checkbox" or elem_role == "checkbox"
    ):
        is_checked = (s.action == "check")
        new_checked = col.checkbox(
            "Checked",
            value=is_checked,
            label_visibility="collapsed",
            key=f"rec_chk_{recording_id}_{i}",
        )
        # The action itself encodes state — Playwright's check()/uncheck()
        # ignore `value`, so we swap the action when the user toggles.
        s.action = "check" if new_checked else "uncheck"
        return

    col.markdown(f":gray[{s.value or ''}]")


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
