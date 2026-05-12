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


def _run():
    return {
        "id": "run-42",
        "scenario_name": "Apply for FD",
        "steps": [
            {"action": "fill", "target": "name", "outcome": "ok"},
            {"action": "submit", "target": "form",
             "outcome": "fail", "error": "Email rejected: pattern mismatch"},
        ],
        "healings": [
            {"element_name": "email", "healed_by": "Level 2 (attribute, 82%)"},
        ],
    }


def test_summarize_run_returns_summary_string(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {
            "response": '{"summary": "Run failed at submit due to a stricter '
                        'email pattern. Two selectors healed."}'
        }
        text = svc.summarize_run(_run())
    assert text.startswith("Run failed")
    assert "email" in text.lower()


def test_summarize_run_caches_per_run_and_model(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"summary": "..."}'}
        a = svc.summarize_run(_run())
        b = svc.summarize_run(_run())
    assert a == b
    assert mock_gen.call_count == 1


def test_summarize_run_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.summarize_run(_run()) == ""
