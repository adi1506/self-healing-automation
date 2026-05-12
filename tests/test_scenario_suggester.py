from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    import time
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    s._available_at = time.monotonic()
    return s


def _page():
    return {
        "url": "https://example.com/apply",
        "title": "Fixed Deposit Application",
        "elements": [
            {"element_name": "name", "element_type": "input-text"},
            {"element_name": "age", "element_type": "input-number"},
            {"element_name": "city", "element_type": "input-text"},
        ],
    }


def test_suggest_scenarios_returns_list_with_required_fields(svc):
    fake = (
        '{"scenarios": ['
        '{"name": "Senior FD", "ai_context": "Senior citizen from Mumbai",'
        ' "rationale": "Tests senior interest tier"},'
        '{"name": "Foreign address", "ai_context": "International applicant",'
        ' "rationale": "Tests country constraint"}'
        ']}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake}
        suggestions = svc.suggest_scenarios(_page())
    assert len(suggestions) == 2
    for s in suggestions:
        assert s["name"] and s["ai_context"] and s["rationale"]


def test_suggest_scenarios_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.suggest_scenarios(_page()) == []


def test_suggest_scenarios_drops_malformed_entries(svc):
    fake = (
        '{"scenarios": ['
        '{"name": "ok", "ai_context": "ctx", "rationale": "why"},'
        '{"name": "missing_ctx"},'
        '{"ai_context": "no_name", "rationale": "x"}'
        ']}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake}
        suggestions = svc.suggest_scenarios(_page())
    assert len(suggestions) == 1
    assert suggestions[0]["name"] == "ok"
