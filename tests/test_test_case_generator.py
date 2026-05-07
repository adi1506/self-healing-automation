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


class TestGenerateOrchestrator:
    def test_generate_produces_happy_path_plus_one_negative_per_field_compact(self, gen):
        fields = [
            _field(element_name="Email", element_type="input-email", required=True),
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        rows = gen.generate(fields, page_context={}, mode="compact")
        # 1 happy path + 2 negatives
        assert len(rows) == 3
        happy = rows[0]
        assert happy["test_case_name"] == "Happy path"
        assert "@" in happy["values"]["Email"]
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", happy["values"]["Customer Reference"])

        # Each negative row has the same fields populated, only the targeted field is invalid
        neg_email = next(r for r in rows if "Email" in r["test_case_name"])
        assert "@" not in neg_email["values"]["Email"]
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", neg_email["values"]["Customer Reference"])

    def test_generate_produces_ai_context_column(self, gen):
        fields = [_field(element_name="Email", element_type="input-email", required=True)]
        rows = gen.generate(fields, page_context={}, mode="compact")
        for r in rows:
            assert "ai_context" in r
            assert r["ai_context"] == ""

    def test_generate_thorough_mode(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        rows = gen.generate(fields, page_context={}, mode="thorough")
        # Happy + (pattern, maxlength, required) = 4
        assert len(rows) == 4


class TestAIEnrichment:
    def test_ai_called_when_field_is_bare_freetext_and_ai_context_present(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = "Senior citizen value"
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        rows = gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={"title": "Reg", "h1": "Sign up", "first_paragraph": ""},
            mode="compact",
            ai_contexts_by_row={0: "Senior citizen"},
        )
        # Happy path row uses the AI value
        assert rows[0]["values"]["Bio"] == "Senior citizen value"
        ai.generate_value.assert_called()

    def test_ai_not_called_when_l1_resolves(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        gen.generate(
            [_field(element_name="Email", element_type="input-email")],
            page_context={}, mode="compact",
        )
        # L1 resolves email type, so AI is not called
        ai.generate_value.assert_not_called()

    def test_ai_falls_back_to_heuristic_when_returns_none(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = None  # AI fails
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        rows = gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={}, mode="compact",
            ai_contexts_by_row={0: "Senior citizen"},
        )
        # Falls back to "Test 1234"
        assert rows[0]["values"]["Bio"] == "Test 1234"

    def test_per_field_rule_passed_to_ai(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = "rahul@gmail.com"
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={}, mode="compact",
            per_field_rules={"Bio": "Make it about banking"},
            ai_contexts_by_row={0: ""},
        )
        call_kwargs = ai.generate_value.call_args.kwargs
        assert call_kwargs["per_field_rule"] == "Make it about banking"
