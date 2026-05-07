import re
import pytest
from core.test_case_generator import TestCaseGenerator


@pytest.fixture
def gen():
    return TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml")


def _field(**overrides):
    base = {
        "element_name": "Field", "element_type": "input-text",
        "locator_label": "", "placeholder": "", "locator_name": "",
        "locator_id": "", "available_options": "",
        "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
        "min": "", "max": "", "autocomplete": "", "inputmode": "",
        "required": False, "helper_text": "",
    }
    base.update(overrides)
    return base


class TestL1DOMConstraints:
    def test_pattern_generates_matching_value(self, gen):
        f = _field(pattern="[A-Z]{4}[0-9]{4}")
        value = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", value)

    def test_email_type_generates_email(self, gen):
        f = _field(element_type="input-email")
        value = gen.generate_value(f)
        assert "@" in value and "." in value.split("@")[1]

    def test_number_type_within_min_max(self, gen):
        f = _field(element_type="input-number", min="18", max="120")
        value = gen.generate_value(f)
        assert 18 <= int(value) <= 120

    def test_number_type_no_bounds_returns_simple_number(self, gen):
        f = _field(element_type="input-number")
        value = gen.generate_value(f)
        assert int(value) >= 0

    def test_select_returns_first_non_empty_option(self, gen):
        f = _field(element_type="select", available_options="India, USA, UK")
        value = gen.generate_value(f)
        assert value == "India"

    def test_radio_returns_first_option(self, gen):
        f = _field(element_type="radio", available_options="Yes, No")
        value = gen.generate_value(f)
        assert value == "Yes"

    def test_checkbox_returns_checked(self, gen):
        f = _field(element_type="checkbox", available_options="checked, unchecked")
        value = gen.generate_value(f)
        assert value == "checked"

    def test_maxlength_respected_on_fallback(self, gen):
        f = _field(element_type="input-text", maxlength="5")
        value = gen.generate_value(f)
        assert len(value) <= 5


class TestL2Autocomplete:
    def test_autocomplete_email(self, gen):
        f = _field(autocomplete="email")
        assert "@" in gen.generate_value(f)

    def test_autocomplete_given_name(self, gen):
        f = _field(autocomplete="given-name")
        v = gen.generate_value(f)
        assert v == "John"

    def test_autocomplete_family_name(self, gen):
        f = _field(autocomplete="family-name")
        assert gen.generate_value(_field(autocomplete="family-name")) == "Doe"

    def test_autocomplete_postal_code(self, gen):
        assert gen.generate_value(_field(autocomplete="postal-code")) == "94105"

    def test_autocomplete_cc_number(self, gen):
        assert gen.generate_value(_field(autocomplete="cc-number")) == "4111111111111111"

    def test_autocomplete_unknown_token_falls_through(self, gen):
        # Should fall through to fallback, not crash
        v = gen.generate_value(_field(autocomplete="bogus-token"))
        assert v == "Test 1234"
