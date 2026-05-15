from core.recording import (
    ElementFingerprint,
    Step, NetworkCapture, SuccessSignal, Recording,
    save_recording, load_recording,
)


def test_element_fingerprint_round_trip_dict():
    fp = ElementFingerprint(
        id="el-001",
        primary_locator={"strategy": "id", "value": "pan"},
        fallback_locators=[
            {"strategy": "name", "value": "pan"},
            {"strategy": "css", "value": "input[name='pan']"},
        ],
        attributes={
            "tag": "input",
            "type": "text",
            "id": "pan",
            "nearest_label_text": "PAN",
            "html5_constraints": {"required": True, "maxlength": 10},
        },
        page_context={"url": "https://example.com/kyc", "section_label": "KYC"},
    )
    d = fp.to_dict()
    fp2 = ElementFingerprint.from_dict(d)
    assert fp2 == fp


def test_element_fingerprint_minimum_required_fields():
    fp = ElementFingerprint(
        id="el-002",
        primary_locator={"strategy": "css", "value": "button.submit"},
        fallback_locators=[],
        attributes={"tag": "button"},
        page_context={"url": "https://example.com"},
    )
    assert fp.id == "el-002"
    assert fp.fallback_locators == []


def _fp(id_: str, name: str) -> ElementFingerprint:
    return ElementFingerprint(
        id=id_,
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input", "name": name},
        page_context={"url": "https://example.com"},
    )


def test_step_with_action_fill():
    s = Step(
        index=0,
        action="fill",
        element=_fp("el-001", "pan"),
        value="ABCDE1234F",
        timestamp_ms=1500,
    )
    assert s.action == "fill"
    assert s.value == "ABCDE1234F"
    assert s.revealed_elements == []
    assert s.network == []


def test_step_with_action_navigate_has_no_element():
    s = Step(index=0, action="navigate", element=None, value="https://example.com/next")
    assert s.element is None


def test_recording_yaml_round_trip(tmp_path):
    rec = Recording(
        id="rec-001",
        name="Happy path: KYC",
        kind="scenario",
        application_id="app-001",
        created_at="2026-05-15T10:30:00",
        start_url="https://example.com/kyc",
        steps=[
            Step(index=0, action="fill", element=_fp("el-001", "pan"), value="ABCDE1234F"),
            Step(index=1, action="click", element=_fp("el-002", "submit"), value=None),
        ],
        success_signal=None,
    )
    path = tmp_path / "rec-001.yaml"
    save_recording(str(path), rec)
    loaded = load_recording(str(path))
    assert loaded == rec


def test_login_recording_with_success_signal():
    sig = SuccessSignal(
        url_pattern="/dashboard",
        required_elements=[_fp("el-100", "user-menu")],
        forbidden_elements=[_fp("el-101", "username")],
        captured_at="2026-05-15T10:31:00",
    )
    rec = Recording(
        id="login-001",
        name="FinnOne login",
        kind="login",
        application_id="app-001",
        created_at="2026-05-15T10:30:00",
        start_url="https://10.0.42.28:7256/login",
        steps=[],
        success_signal=sig,
    )
    assert rec.kind == "login"
    assert rec.success_signal.url_pattern == "/dashboard"
