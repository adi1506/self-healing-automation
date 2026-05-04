import json
from unittest.mock import MagicMock, patch
import pytest
from core.ai_matcher import AIMatcher


def _elements():
    return [
        {"element_name": "Email", "element_type": "input-email", "placeholder": "you@x", "locator_label": "Email"},
        {"element_name": "Password", "element_type": "input-password", "placeholder": "", "locator_label": "Password"},
        {"element_name": "Sign In", "element_type": "button", "placeholder": "", "locator_label": ""},
    ]


def test_suggest_recipe_returns_steps_with_targets_resolved():
    matcher = AIMatcher()
    fake_response = {
        "response": json.dumps({
            "steps": [
                {"action": "fill", "element_index": 0, "value": "test@x.com"},
                {"action": "fill", "element_index": 1, "value": "<USER_FILLS>"},
                {"action": "click", "element_index": 2},
            ],
            "reasoning": "obvious",
        })
    }
    matcher._available = True
    matcher.client = MagicMock()
    matcher.client.generate.return_value = fake_response

    result = matcher.suggest_recipe("https://x/login", _elements(), goal="log in")
    assert result is not None
    assert result["steps"][0]["target"] == "Email"
    assert result["steps"][0]["value"] == "test@x.com"
    assert result["steps"][2]["target"] == "Sign In"
    assert result["steps"][2]["action"] == "click"


def test_suggest_recipe_returns_none_when_ollama_unavailable():
    matcher = AIMatcher()
    matcher._available = False
    assert matcher.suggest_recipe("https://x", _elements(), "log in") is None


def test_suggest_recipe_drops_steps_with_invalid_index():
    matcher = AIMatcher()
    matcher._available = True
    matcher.client = MagicMock()
    matcher.client.generate.return_value = {
        "response": json.dumps({
            "steps": [
                {"action": "fill", "element_index": 99, "value": "x"},
                {"action": "fill", "element_index": 0, "value": "ok"},
            ],
            "reasoning": "",
        })
    }
    result = matcher.suggest_recipe("https://x", _elements(), "log in")
    assert len(result["steps"]) == 1
    assert result["steps"][0]["target"] == "Email"


def test_suggest_recipe_returns_none_on_malformed_json():
    matcher = AIMatcher()
    matcher._available = True
    matcher.client = MagicMock()
    matcher.client.generate.return_value = {"response": "not json"}
    assert matcher.suggest_recipe("https://x", _elements(), "log in") is None
