import os
from unittest.mock import MagicMock, patch
import pytest
from core.ai_test_data import AITestData
from core.ai_service import reset_ai_service

@pytest.fixture(autouse=True)
def _reset_ai_singleton():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def ai():
    a = AITestData(host="http://localhost:11434", model="mistral")
    a._available = True
    return a


def _field(**overrides):
    base = {
        "element_name": "Email", "element_type": "input-email",
        "locator_label": "Email", "placeholder": "",
        "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
        "min": "", "max": "", "autocomplete": "", "inputmode": "",
        "required": False, "helper_text": "",
    }
    base.update(overrides)
    return base


class TestAITestData:
    def test_returns_value_from_valid_json(self, ai):
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.return_value = {"response": '{"value": "alice@gmail.com"}'}
            v = ai.generate_value(
                field=_field(),
                page_context={"title": "Reg", "h1": "Sign up", "first_paragraph": ""},
                per_field_rule="Use Gmail addresses",
                ai_context="Senior citizen",
            )
            assert v == "alice@gmail.com"

    def test_retries_on_invalid_json_then_returns_value(self, ai):
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.side_effect = [
                {"response": "not json at all"},
                {"response": '{"value": "bob@gmail.com"}'},
            ]
            v = ai.generate_value(field=_field(), page_context={},
                                  per_field_rule="", ai_context="")
            assert v == "bob@gmail.com"

    def test_returns_none_when_value_violates_constraints_twice(self, ai):
        # Field has a strict pattern; LLM returns non-matching strings both times
        f = _field(element_type="input-text", pattern="[A-Z]{4}[0-9]{4}")
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.side_effect = [
                {"response": '{"value": "lowercase"}'},
                {"response": '{"value": "stillbad"}'},
            ]
            v = ai.generate_value(field=f, page_context={}, per_field_rule="", ai_context="")
            assert v is None

    def test_returns_none_when_unavailable(self):
        a = AITestData(host="http://localhost:11434", model="mistral")
        a._available = False
        v = a.generate_value(field=_field(), page_context={}, per_field_rule="", ai_context="")
        assert v is None

    def test_respects_pattern_validates_correct_value(self, ai):
        f = _field(element_type="input-text", pattern="[A-Z]{4}[0-9]{4}")
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.return_value = {"response": '{"value": "FINN0316"}'}
            v = ai.generate_value(field=f, page_context={}, per_field_rule="", ai_context="")
            assert v == "FINN0316"
