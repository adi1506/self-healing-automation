from ui.scenarios.recording_editor import _is_data_entry
from core.recording import Step, ElementFingerprint


def _el():
    return ElementFingerprint(id="e", primary_locator={}, fallback_locators=[],
                              attributes={}, page_context={})


def test_fill_and_select_are_data_entry():
    assert _is_data_entry(Step(index=0, action="fill", element=_el(), value="x")) is True
    assert _is_data_entry(Step(index=0, action="select", element=_el(), value="x")) is True


def test_click_and_navigate_are_not():
    assert _is_data_entry(Step(index=0, action="click", element=_el())) is False
    assert _is_data_entry(Step(index=0, action="navigate", element=None)) is False


def test_no_element_is_not_data_entry():
    assert _is_data_entry(Step(index=0, action="fill", element=None, value="x")) is False
