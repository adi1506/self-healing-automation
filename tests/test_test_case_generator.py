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


class TestL3Dictionary:
    def test_label_pan_number_matches_dictionary(self, gen):
        f = _field(element_name="PAN Number", locator_label="PAN Number")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v)

    def test_label_aadhaar_matches(self, gen):
        f = _field(element_name="Aadhaar", locator_label="Aadhaar")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[0-9]{12}", v)

    def test_name_attr_matches_when_label_doesnt(self, gen):
        f = _field(element_name="Cust Code", locator_label="", locator_name="ifsc_code")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v)

    def test_no_dictionary_match_falls_through_to_fallback(self, gen):
        f = _field(element_name="Xyz Garbage Field", locator_label="Xyz Garbage Field")
        v = gen.generate_value(f)
        assert v == "Test 1234"


class TestNegativeDerivation:
    def test_compact_yields_one_per_field(self, gen):
        fields = [
            _field(element_name="Email", element_type="input-email", required=True),
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
            _field(element_name="Age", element_type="input-number",
                   min="18", max="120", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="compact")
        # One row per field — three rows
        names = [n["field"] for n in negs]
        assert sorted(names) == ["Age", "Customer Reference", "Email"]

    def test_compact_chooses_pattern_over_required(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="compact")
        assert len(negs) == 1
        assert negs[0]["violation"] == "pattern"
        # Value should NOT match the pattern
        assert not re.fullmatch(r"[A-Z]{4}[0-9]{4}", negs[0]["value"])

    def test_thorough_yields_one_per_constraint(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="thorough")
        violations = sorted(n["violation"] for n in negs)
        assert violations == ["maxlength", "pattern", "required"]

    def test_required_only_field_compact_yields_required_violation(self, gen):
        fields = [_field(element_name="City", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        assert len(negs) == 1
        assert negs[0]["violation"] == "required"
        assert negs[0]["value"] == ""

    def test_email_type_negative_has_no_at_sign(self, gen):
        fields = [_field(element_name="Email", element_type="input-email", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        chosen = negs[0]
        assert "@" not in chosen["value"]

    def test_min_max_violation_picks_below_min(self, gen):
        fields = [_field(element_name="Age", element_type="input-number",
                         min="18", max="120", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        chosen = negs[0]
        assert chosen["violation"] in ("min", "max")
        # Either way, the value violates the range
        assert int(chosen["value"]) < 18 or int(chosen["value"]) > 120
