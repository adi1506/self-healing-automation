from core.ai_prompts import build_test_cases_for_recording_prompt


def _step(label, value, ctx=None, attrs=None):
    a = {"aria_label": label, "type": ""}
    a.update(attrs or {})
    return {"action": "fill", "value": value, "attributes": a, "field_context": ctx}


def test_includes_app_and_screen_context():
    p = build_test_cases_for_recording_prompt(
        [_step("City", "Pune")], 3, ["Negative"],
        app_context="mCAS — Indian banking KYC", screen_context="Personal Details (/kyc)")
    assert "mCAS — Indian banking KYC" in p
    assert "Personal Details (/kyc)" in p
    assert "[0]" in p and "City" in p


def test_includes_fixed_fields_block():
    p = build_test_cases_for_recording_prompt(
        [_step("City", "Pune")], 2, ["Negative"],
        fixed_fields=[{"label": "Username", "value": "alice"}])
    assert "Username" in p
    assert "FIXED" in p


def test_includes_per_field_context():
    p = build_test_cases_for_recording_prompt(
        [_step("PAN", "AAAPL1234C", ctx="PAN = AAAAA9999A, uppercase")], 1, ["Negative"])
    assert "PAN = AAAAA9999A, uppercase" in p


def test_regression_branch_forces_success():
    p = build_test_cases_for_recording_prompt(
        [_step("City", "Pune")], 4, ["Regression Testing"])
    assert "REGRESSION" in p
    assert "success" in p
    assert "Vary EVERY" in p


def test_non_regression_lists_focus():
    p = build_test_cases_for_recording_prompt([_step("City", "Pune")], 2, ["Boundary"])
    assert "Boundary" in p
