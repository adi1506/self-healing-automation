"""Unit tests for the per-step skippability rule used by the replay loop
to decide whether a missing-element failure should be skipped (warning)
or treated as a blocker (fail the run)."""
from __future__ import annotations
from core.recording import Step, ElementFingerprint
from core.replay import _is_step_skippable


def _step(
    action: str,
    *,
    required: bool = False,
    value: str | None = None,
    fp_id: str = "el-1",
    tag: str = "input",
) -> Step:
    return Step(
        index=0,
        action=action,
        value=value,
        element=ElementFingerprint(
            id=fp_id,
            primary_locator={"strategy": "css", "value": tag},
            fallback_locators=[],
            attributes={
                "tag": tag,
                "type": "text",
                "html5_constraints": {"required": required, "pattern": "", "maxlength": "", "minlength": "", "min": "", "max": ""},
            },
            page_context={},
        ),
    )


def test_fill_on_optional_field_is_skippable():
    assert _is_step_skippable(_step("fill", required=False, value="hello")) is True


def test_fill_on_required_field_with_value_is_blocker():
    assert _is_step_skippable(_step("fill", required=True, value="hello")) is False


def test_fill_on_required_field_with_empty_value_is_skippable():
    """Recording had nothing to put in this field anyway — safe to skip
    even though it's marked required, because the recorder never filled it."""
    assert _is_step_skippable(_step("fill", required=True, value="")) is True
    assert _is_step_skippable(_step("fill", required=True, value=None)) is True


def test_click_is_blocker():
    """Clicks advance the flow — skipping puts later steps on the wrong page."""
    assert _is_step_skippable(_step("click")) is False


def test_click_on_input_is_blocker_without_lookahead():
    """A bare click on an input with no follow-up is still a flow action."""
    assert _is_step_skippable(_step("click", tag="input"), None) is False


def test_click_precursor_to_same_field_fill_is_skippable():
    """Recorder artefact: tab/click into a text input before typing.
    If the input is gone, the click is safe to drop — the fill that carries
    the real intent will itself be evaluated on the next iteration."""
    click = _step("click", tag="input", fp_id="el-7")
    fill = _step("fill", value="hello", fp_id="el-7")
    assert _is_step_skippable(click, fill) is True


def test_click_precursor_to_same_field_select_is_skippable():
    click = _step("click", tag="select", fp_id="el-7")
    sel = _step("select", value="opt-a", fp_id="el-7")
    assert _is_step_skippable(click, sel) is True


def test_click_precursor_to_same_field_check_is_skippable():
    click = _step("click", tag="input", fp_id="el-7")
    chk = _step("check", fp_id="el-7")
    assert _is_step_skippable(click, chk) is True


def test_click_on_button_with_fill_follow_up_is_blocker():
    """A click on a button is flow-advancing even if the next step
    happens to share the fingerprint id (shouldn't happen, but be strict)."""
    click = _step("click", tag="button", fp_id="el-7")
    fill = _step("fill", value="hello", fp_id="el-7")
    assert _is_step_skippable(click, fill) is False


def test_click_on_anchor_is_blocker():
    click = _step("click", tag="a", fp_id="el-7")
    fill = _step("fill", value="hello", fp_id="el-7")
    assert _is_step_skippable(click, fill) is False


def test_click_followed_by_fill_on_different_id_is_blocker():
    """Different fingerprint id means the click was its own action,
    not a focus precursor to the next fill."""
    click = _step("click", tag="input", fp_id="el-7")
    fill = _step("fill", value="hello", fp_id="el-8")
    assert _is_step_skippable(click, fill) is False


def test_click_followed_by_another_click_is_blocker():
    """Two clicks in a row are two flow actions — no precursor pattern."""
    a = _step("click", tag="input", fp_id="el-7")
    b = _step("click", tag="button", fp_id="el-7")
    assert _is_step_skippable(a, b) is False


def test_click_at_end_of_recording_is_blocker():
    """No lookahead step → cannot be a focus precursor → blocker."""
    assert _is_step_skippable(_step("click", tag="input"), None) is False


def test_click_precursor_skip_works_when_next_fill_is_blocker_on_required():
    """The click is dropped on its own merits; the next required-fill step
    gets evaluated separately on its own iteration. This test only
    asserts the click predicate — the fill step's own blocker handling
    is covered by test_fill_on_required_field_with_value_is_blocker."""
    click = _step("click", tag="input", fp_id="el-7")
    required_fill = _step("fill", required=True, value="real-value", fp_id="el-7")
    assert _is_step_skippable(click, required_fill) is True


def test_submit_is_blocker():
    assert _is_step_skippable(_step("submit")) is False


def test_check_uncheck_on_optional_toggle_is_skippable():
    assert _is_step_skippable(_step("check", required=False)) is True
    assert _is_step_skippable(_step("uncheck", required=False)) is True


def test_check_on_required_toggle_is_blocker():
    """Required checkboxes (T&Cs, etc.) are blockers."""
    assert _is_step_skippable(_step("check", required=True)) is False


def test_select_on_required_is_blocker():
    assert _is_step_skippable(_step("select", required=True, value="opt-a")) is False


def test_select_on_optional_is_skippable():
    assert _is_step_skippable(_step("select", required=False, value="opt-a")) is True


def test_navigate_and_wait_have_no_element_and_are_not_skippable_targets():
    """navigate/wait never miss an element — but if asked, treat as blocker
    so we don't accidentally skip a URL load."""
    nav = Step(index=0, action="navigate", value="https://example.com", element=None)
    wait = Step(index=0, action="wait", value="500", element=None)
    assert _is_step_skippable(nav) is False
    assert _is_step_skippable(wait) is False


def test_press_is_blocker():
    """Press is often a Tab/Enter that advances form state — treat as blocker."""
    assert _is_step_skippable(_step("press", value="Enter")) is False
