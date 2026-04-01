import pytest
from unittest.mock import patch, MagicMock
from core.ai_matcher import AIMatcher


@pytest.fixture
def matcher():
    return AIMatcher(api_key="test-key")


class TestAIMatcher:
    def test_initializes_with_api_key(self, matcher):
        assert matcher.api_key == "test-key"

    def test_is_available_returns_false_without_key(self):
        m = AIMatcher(api_key="")
        assert m.is_available() is False

    def test_is_available_returns_true_with_key(self, matcher):
        assert matcher.is_available() is True

    @patch("core.ai_matcher.genai")
    def test_match_element_returns_best_match(self, mock_genai, matcher):
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_response = MagicMock()
        mock_response.text = '{"match_index": 1, "confidence": 0.92, "reasoning": "Same field renamed"}'
        mock_model.generate_content.return_value = mock_response

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

    @patch("core.ai_matcher.genai")
    def test_match_element_returns_none_on_low_confidence(self, mock_genai, matcher):
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_response = MagicMock()
        mock_response.text = '{"match_index": -1, "confidence": 0.0, "reasoning": "No match found"}'
        mock_model.generate_content.return_value = mock_response

        old_element = {"element_name": "Fax Number", "element_type": "input-text", "placeholder": "", "locator_label": "Fax"}
        candidates = [
            {"element_name": "Email", "element_type": "input-email", "placeholder": "Enter email", "locator_label": "Email"},
        ]

        result = matcher.match_element(old_element, candidates)
        assert result["match_index"] == -1

    @patch("core.ai_matcher.genai")
    def test_graceful_degradation_on_api_error(self, mock_genai, matcher):
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_model.generate_content.side_effect = Exception("API unavailable")

        old_element = {"element_name": "Test", "element_type": "input-text", "placeholder": "", "locator_label": ""}
        candidates = [{"element_name": "Test2", "element_type": "input-text", "placeholder": "", "locator_label": ""}]

        result = matcher.match_element(old_element, candidates)
        assert result is None
