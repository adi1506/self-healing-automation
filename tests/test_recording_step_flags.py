from core.recording import Step, ElementFingerprint


def _el():
    return ElementFingerprint(id="el-1", primary_locator={}, fallback_locators=[],
                              attributes={}, page_context={})


def test_locked_value_defaults_false_and_roundtrips():
    s = Step(index=0, action="fill", element=_el(), value="x")
    assert s.locked_value is False
    assert s.to_dict()["locked_value"] is False
    s.locked_value = True
    assert Step.from_dict(s.to_dict()).locked_value is True


def test_field_context_defaults_none_and_roundtrips():
    s = Step(index=0, action="fill", element=_el(), value="x")
    assert s.field_context is None
    s.field_context = "PAN = AAAAA9999A"
    assert Step.from_dict(s.to_dict()).field_context == "PAN = AAAAA9999A"


def test_backward_compat_missing_keys():
    s = Step.from_dict({"index": 0, "action": "fill", "element": None, "value": "x"})
    assert s.locked_value is False
    assert s.field_context is None
