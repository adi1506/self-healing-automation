"""Unit tests for the per-step skippability rule used by the replay loop
to decide whether a missing-element failure should be skipped (warning)
or treated as a blocker (fail the run)."""
from __future__ import annotations
from core.recording import Step, ElementFingerprint
from core.replay import _is_step_skippable


def _step(action: str, *, required: bool = False, value: str | None = None) -> Step:
    return Step(
        index=0,
        action=action,
        value=value,
        element=ElementFingerprint(
            id="el-1",
            primary_locator={"strategy": "css", "value": "input"},
            fallback_locators=[],
            attributes={
                "tag": "input",
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
