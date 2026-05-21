"""Runner result classification.

Decouples the policy "did this test case pass?" from the Streamlit page so
it can be unit-tested. Used by pages/3_runner.py to compare what the setter
observed against the row's `Expected Outcome`.
"""
from __future__ import annotations

import math


_METADATA_KEYS = {"__expected_outcome", "__test_name"}


def is_blank_dataset_row(row: dict) -> bool:
    """True when a dataset row has no field values worth running.

    A row is blank when every non-metadata cell is None, NaN (pandas fills
    missing cells with float NaN when the data_editor materializes), or an
    empty/whitespace string. Such rows arise from the data_editor's dynamic
    "+" placeholder, an untouched "+ Add empty row" click, or an upload with
    a trailing blank line. Running them executes the setter against no
    fields, which then vacuously reports PASS and inflates the per-row count
    above what the user sees in the editor.
    """
    for k, v in row.items():
        if k in _METADATA_KEYS:
            continue
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        if str(v).strip() == "":
            continue
        return False
    return True


def classify_case_outcome(
    expected_outcome: str,
    setter_results: list[dict],
    click_submit: bool,
    form_was_rejected: bool | None,
) -> str:
    """Map (expected_outcome, setter results, optional submit feedback) → status.

    Returns one of:
      - "PASS"         the test case behaved as expected
      - "FAIL"         the test case demonstrably did not behave as expected
      - "UNVERIFIED"   we cannot confirm either way (typically: a 'failure'
                       case where submit was not clicked, so we have no DOM
                       signal of acceptance/rejection)

    Policy:
      expected=success → PASS iff every setter row is PASS. A setter FAIL or
        BLOCKED means a field couldn't be located, filled, or was refused by
        the widget — any of which breaks the happy path.
      expected=failure → PASS iff the form was rejected. A field-level BLOCKED
        counts as rejection on its own (the browser's widget guard is the
        first line of form validation), so we don't need the submit probe to
        confirm. Otherwise we need click_submit + DOM signal; without them
        we report UNVERIFIED rather than a misleading PASS.
    """
    expected = (expected_outcome or "success").lower()
    setter_all_pass = all(r.get("status") == "PASS" for r in setter_results)
    any_blocked = any(r.get("status") == "BLOCKED" for r in setter_results)

    if expected == "success":
        if not setter_all_pass:
            return "FAIL"
        # If we have a submit signal and the form was actually rejected, the
        # happy path didn't really pass even if the setter populated cleanly.
        if click_submit and form_was_rejected is True:
            return "FAIL"
        return "PASS"

    # expected == "failure"
    if any_blocked:
        # The browser-level widget guard refused the value — that IS the form
        # rejecting the input, regardless of whether submit was clicked.
        return "PASS"
    if not click_submit or form_was_rejected is None:
        return "UNVERIFIED"
    return "PASS" if form_was_rejected else "FAIL"


# Install probes BEFORE clicking submit. Captures whether the browser
# dispatched any `invalid` event (HTML5 validation rejected the form) or
# whether the form's `submit` event fired (form was accepted).
INSTALL_SUBMIT_PROBES_JS = """
() => {
  window.__validationFired = false;
  window.__submitFired = false;
  document.querySelectorAll('input, select, textarea').forEach(el => {
    el.addEventListener('invalid', () => { window.__validationFired = true; }, true);
  });
  document.querySelectorAll('form').forEach(f => {
    f.addEventListener('submit', () => { window.__submitFired = true; }, true);
  });
}
"""

# Read the probes AFTER the click. `hasGlobals` = false means the page
# navigated (form submitted and reloaded), which we treat as "accepted".
READ_SUBMIT_PROBES_JS = """
() => ({
  hasGlobals: typeof window.__validationFired !== 'undefined',
  validationFired: !!window.__validationFired,
  submitFired: !!window.__submitFired,
})
"""


def interpret_submit_probes(probes: dict | None) -> bool | None:
    """Map probe readings to (was the form rejected?).

    Returns:
      True   — HTML5 validation fired an `invalid` event (rejected)
      False  — submit event fired or page navigated (accepted)
      None   — no signal observed (e.g. submit button not found)
    """
    if probes is None:
        return None
    if not probes.get("hasGlobals"):
        # Page navigated → form was actually submitted by the browser.
        return False
    if probes.get("validationFired"):
        return True
    if probes.get("submitFired"):
        return False
    return None
