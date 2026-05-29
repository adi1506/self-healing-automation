import time
from unittest.mock import patch
from core.ai_service import AIService, reset_ai_service
from core.recording import Recording, Step, ElementFingerprint


def _step(i, fp_id, value, locked=False):
    el = ElementFingerprint(id=fp_id, primary_locator={}, fallback_locators=[],
                            attributes={"aria_label": fp_id}, page_context={})
    s = Step(index=i, action="fill", element=el, value=value)
    s.locked_value = locked
    return s


def _rec(steps):
    return Recording(id="r1", name="n", kind="scenario", application_id="a",
                     created_at="", start_url="https://app/x", steps=steps)


def _svc(tmp_path):
    reset_ai_service()
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    return svc


def test_locked_step_is_never_overridable(tmp_path):
    svc = _svc(tmp_path)
    rec = _rec([_step(0, "user", "alice", locked=True),
                _step(1, "city", "Pune"),
                _step(2, "income", "500000")])
    # Overridable list is [city, income]; the model overrides indices 0 and 1 of THAT list.
    cases = {"cases": [{"name": "v1", "expected_outcome": "success",
                        "overrides": [{"step_index": 0, "value": "Mumbai"},
                                      {"step_index": 1, "value": "750000"}],
                        "rationale": "r"}]}
    with patch.object(svc, "generate_json", return_value=cases):
        out = svc.suggest_test_cases_for_recording(rec, 1, ["Regression Testing"])
    assert out is not None and len(out) == 1
    assert out[0]["overrides"] == {"city": "Mumbai", "income": "750000"}
    assert "user" not in out[0]["overrides"]


def test_regression_drops_failure_cases(tmp_path):
    svc = _svc(tmp_path)
    rec = _rec([_step(0, "city", "Pune")])
    cases = {"cases": [
        {"name": "ok", "expected_outcome": "success",
         "overrides": [{"step_index": 0, "value": "Goa"}], "rationale": ""},
        {"name": "bad", "expected_outcome": "failure",
         "overrides": [{"step_index": 0, "value": ""}], "rationale": ""}]}
    with patch.object(svc, "generate_json", return_value=cases):
        out = svc.suggest_test_cases_for_recording(rec, 2, ["Regression Testing"])
    assert [c["name"] for c in out] == ["ok"]


def test_non_regression_keeps_failure_cases(tmp_path):
    svc = _svc(tmp_path)
    rec = _rec([_step(0, "city", "Pune")])
    cases = {"cases": [
        {"name": "bad", "expected_outcome": "failure",
         "overrides": [{"step_index": 0, "value": ""}], "rationale": ""}]}
    with patch.object(svc, "generate_json", return_value=cases):
        out = svc.suggest_test_cases_for_recording(rec, 1, ["Negative"])
    assert [c["name"] for c in out] == ["bad"]


def test_all_steps_locked_returns_empty(tmp_path):
    svc = _svc(tmp_path)
    rec = _rec([_step(0, "user", "alice", locked=True)])
    out = svc.suggest_test_cases_for_recording(rec, 3, ["Regression Testing"])
    assert out == []
