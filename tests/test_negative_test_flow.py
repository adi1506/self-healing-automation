"""Reproductions for the negative-test flow bugs found in the rigorous audit.

These cover the three places where `required`-violation negatives were silently
no-op'd, the runner's failure to honor `expected_outcome`, the per-row
AI-context drop, and the unguarded `read_test_data` crash.
"""
from __future__ import annotations

import os
import pytest

from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator


# --------------------------------------------------------------------------- #
# read_test_data must not crash when no scan exists
# --------------------------------------------------------------------------- #
def test_read_test_data_returns_empty_for_unscanned_url(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    assert em.read_test_data("https://never-scanned.example.com") == []


# --------------------------------------------------------------------------- #
# Negatives must propagate per-row AI context to non-target fields
# --------------------------------------------------------------------------- #
def _field(**overrides):
    base = {
        "element_name": "F", "element_type": "input-text",
        "locator_label": "", "placeholder": "", "locator_name": "",
        "locator_id": "", "available_options": "",
        "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
        "min": "", "max": "", "autocomplete": "", "inputmode": "",
        "required": False, "helper_text": "",
    }
    base.update(overrides)
    return base


def test_per_row_ai_context_propagates_to_non_target_fields_in_negatives():
    """When a negative row carries an ai_context, non-target fields should be
    regenerated with that context — not silently inherited from row 0."""
    from unittest.mock import MagicMock
    ai = MagicMock()
    # Return a different value depending on the ai_context so we can detect it
    ai.generate_value.side_effect = lambda field, page_context, per_field_rule, ai_context: (
        f"AI[{ai_context}]" if ai_context else None
    )
    gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml",
                            ai_client=ai)
    fields = [
        _field(element_name="Bio", element_type="textarea"),
        _field(element_name="Age", element_type="input-number",
               min="18", max="120"),
    ]
    rows = gen.generate(
        fields=fields, page_context={}, mode="compact",
        ai_contexts_by_row={0: "ctx0", 1: "ctx_neg"},
    )
    # Row 0 happy path — Bio should reflect AI ctx0
    assert rows[0]["values"]["Bio"] == "AI[ctx0]"
    # Row 1 is the Age negative; its Bio should reflect the row-1 AI context,
    # not the row-0 value.
    age_neg = next(r for r in rows if "Age" in r["test_case_name"])
    assert age_neg["values"]["Bio"] == "AI[ctx_neg]", (
        "Per-row AI context for the negative was dropped — non-target fields "
        "still reflect row 0."
    )


# --------------------------------------------------------------------------- #
# Setter must clear-and-verify when an empty value is explicitly passed
# (so required-violation negatives actually exercise the empty case).
# --------------------------------------------------------------------------- #
@pytest.fixture
def constrained_form_url():
    return "file://" + os.path.abspath(
        "test_form/v9_constrained.html"
    ).replace("\\", "/")


def test_setter_clears_and_verifies_explicit_empty_string(tmp_path, constrained_form_url):
    """An explicit empty string in test_data must clear the field and report
    a result, not be silently skipped. Required-violation negatives depend on
    this."""
    from core.scanner import Scanner
    from core.setter import Setter

    em = ExcelManager(data_dir=str(tmp_path))
    elements = Scanner().scan(constrained_form_url)
    em.save_element_map(constrained_form_url, elements)
    element_map = em.read_element_map(constrained_form_url)

    # Pre-populate by running once with a valid value — proves the field can
    # carry a value before the negative test clears it.
    setter = Setter()
    setter.set_fields(constrained_form_url, element_map, {"First Name": "John"})

    # Now ask for explicit empty — current behavior silently skips.
    results = setter.set_fields(
        constrained_form_url, element_map, {"First Name": ""}
    )
    fn = next((r for r in results if r["element_name"] == "First Name"), None)
    assert fn is not None, (
        "Setter dropped the empty-string value silently — required-violation "
        "negatives can never be exercised."
    )
    assert fn["actual_value"] == ""
    assert fn["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Outcome comparison helper — extract the runner's logic so we can unit-test
# it without spinning up Streamlit.
# --------------------------------------------------------------------------- #
def test_outcome_comparison_failure_expected_with_all_pass_setter_is_unverified():
    """If the row's expected outcome is 'failure' but the setter reports all
    fields PASS, the case status should be 'UNVERIFIED' (we filled the form
    but cannot confirm the form rejected it without click_submit + DOM check),
    not 'PASS'.
    """
    from core.runner_utils import classify_case_outcome

    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "x", "actual_value": "x"},
        {"element_name": "F2", "status": "PASS", "expected_value": "y", "actual_value": "y"},
    ]
    status = classify_case_outcome(
        expected_outcome="failure",
        setter_results=setter_results,
        click_submit=False,
        form_was_rejected=None,
    )
    assert status == "UNVERIFIED"


def test_outcome_comparison_success_expected_with_all_pass_is_pass():
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "x", "actual_value": "x"},
    ]
    status = classify_case_outcome(
        expected_outcome="success",
        setter_results=setter_results,
        click_submit=False,
        form_was_rejected=None,
    )
    assert status == "PASS"


def test_outcome_comparison_failure_expected_with_form_rejected_is_pass():
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "", "actual_value": ""},
    ]
    status = classify_case_outcome(
        expected_outcome="failure",
        setter_results=setter_results,
        click_submit=True,
        form_was_rejected=True,
    )
    assert status == "PASS"


def test_outcome_comparison_failure_expected_with_form_accepted_is_fail():
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "x", "actual_value": "x"},
    ]
    status = classify_case_outcome(
        expected_outcome="failure",
        setter_results=setter_results,
        click_submit=True,
        form_was_rejected=False,
    )
    assert status == "FAIL"


def test_outcome_comparison_success_with_setter_failures_is_fail():
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "x", "actual_value": "x"},
        {"element_name": "F2", "status": "FAIL", "expected_value": "y", "actual_value": "ELEMENT NOT FOUND"},
    ]
    status = classify_case_outcome(
        expected_outcome="success",
        setter_results=setter_results,
        click_submit=False,
        form_was_rejected=None,
    )
    assert status == "FAIL"


# --------------------------------------------------------------------------- #
# Browser-blocked input (e.g. alphabets in <input type="number">) must not
# crash the run, and must be interpreted as "form rejected" for negatives.
# --------------------------------------------------------------------------- #
def test_setter_records_blocked_status_when_browser_refuses_input(
    tmp_path, constrained_form_url
):
    """Filling alphabets into <input type="number"> raises in Playwright.
    The setter must catch that, record a BLOCKED row, and let the run
    continue rather than propagating the exception."""
    from core.scanner import Scanner
    from core.setter import Setter

    em = ExcelManager(data_dir=str(tmp_path))
    elements = Scanner().scan(constrained_form_url)
    em.save_element_map(constrained_form_url, elements)
    element_map = em.read_element_map(constrained_form_url)

    setter = Setter()
    results = setter.set_fields(
        constrained_form_url, element_map, {"Age": "abc"}
    )

    age = next((r for r in results if r["element_name"] == "Age"), None)
    assert age is not None, "Age row missing — setter likely crashed."
    assert age["status"] == "BLOCKED", (
        f"Expected BLOCKED for alphabets-in-number-field, got {age['status']!r}"
    )
    assert age["actual_value"].startswith("BROWSER BLOCKED:")


def test_outcome_comparison_failure_expected_with_blocked_field_is_pass():
    """Browser-level widget rejection IS the form rejecting the input — for a
    negative case, that should classify as PASS even without a submit signal."""
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "Age", "status": "BLOCKED",
         "expected_value": "abc", "actual_value": "BROWSER BLOCKED: ..."},
    ]
    status = classify_case_outcome(
        expected_outcome="failure",
        setter_results=setter_results,
        click_submit=False,
        form_was_rejected=None,
    )
    assert status == "PASS"


def test_outcome_comparison_success_expected_with_blocked_field_is_fail():
    """Browser-blocked input on a happy-path case means the test data was
    incompatible with the field type — that should fail loudly, not pass."""
    from core.runner_utils import classify_case_outcome
    setter_results = [
        {"element_name": "F1", "status": "PASS", "expected_value": "x", "actual_value": "x"},
        {"element_name": "Age", "status": "BLOCKED",
         "expected_value": "abc", "actual_value": "BROWSER BLOCKED: ..."},
    ]
    status = classify_case_outcome(
        expected_outcome="success",
        setter_results=setter_results,
        click_submit=False,
        form_was_rejected=None,
    )
    assert status == "FAIL"
