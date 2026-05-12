import os
import pytest
from unittest.mock import patch, MagicMock
from core.ai_matcher import AIMatcher
from core.ai_service import reset_ai_service

TEST_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


@pytest.fixture(autouse=True)
def _reset_ai_singleton():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def matcher():
    m = AIMatcher(host=TEST_OLLAMA_HOST, model="mistral")
    m._available = True  # bypass live Ollama probe in unit tests
    return m


class TestAIMatcher:
    def test_initializes_with_host_and_model(self, matcher):
        # AIService owns config; adapter exposes the singleton's resolved values.
        assert isinstance(matcher.host, str) and matcher.host
        assert isinstance(matcher.model, str) and matcher.model

    def test_is_available_returns_false_when_ollama_unreachable(self):
        m = AIMatcher(host=TEST_OLLAMA_HOST, model="mistral")
        with patch.object(m.client, "list", side_effect=Exception("connection refused")):
            assert m.is_available() is False

    def test_is_available_returns_true_when_ollama_reachable(self):
        m = AIMatcher(host=TEST_OLLAMA_HOST, model="mistral")
        with patch.object(m.client, "list", return_value={"models": []}):
            assert m.is_available() is True

    def test_match_element_returns_best_match(self, matcher):
        with patch.object(matcher.client, "generate") as mock_generate:
            mock_generate.return_value = {
                "response": '{"match_index": 1, "confidence": 0.92, "reasoning": "Same field renamed"}'
            }

            old_element = {
                "element_name": "First Name",
                "element_type": "input-text",
                "placeholder": "Enter first name",
                "locator_label": "First Name",
            }
            candidates = [
                {
                    "element_name": "Email",
                    "element_type": "input-email",
                    "placeholder": "Enter email",
                    "locator_label": "Email",
                },
                {
                    "element_name": "Given Name",
                    "element_type": "input-text",
                    "placeholder": "Enter given name",
                    "locator_label": "Given Name",
                },
            ]

            result = matcher.match_element(old_element, candidates)
            assert result["match_index"] == 1
            assert result["confidence"] >= 0.9

    def test_match_element_returns_none_on_low_confidence(self, matcher):
        with patch.object(matcher.client, "generate") as mock_generate:
            mock_generate.return_value = {
                "response": '{"match_index": -1, "confidence": 0.0, "reasoning": "No match found"}'
            }

            old_element = {"element_name": "Fax Number", "element_type": "input-text", "placeholder": "", "locator_label": "Fax"}
            candidates = [
                {"element_name": "Email", "element_type": "input-email", "placeholder": "Enter email", "locator_label": "Email"},
            ]

            result = matcher.match_element(old_element, candidates)
            assert result["match_index"] == -1

    def test_graceful_degradation_on_api_error(self, matcher):
        with patch.object(matcher.client, "generate", side_effect=Exception("API unavailable")):
            old_element = {"element_name": "Test", "element_type": "input-text", "placeholder": "", "locator_label": ""}
            candidates = [{"element_name": "Test2", "element_type": "input-text", "placeholder": "", "locator_label": ""}]

            result = matcher.match_element(old_element, candidates)
            assert result is None
