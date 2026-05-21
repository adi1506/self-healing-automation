"""End-to-end replay-healing tests against the test_form fixtures.

Records-by-construction (we build the Recording objects directly rather
than capturing them through the recorder) so the tests stay deterministic
and fast. The recorder + capture pipeline is exercised in
tests/test_recording_e2e.py — these tests focus on the heal-on-miss path.
"""
from __future__ import annotations
import os
import pytest

from core.recording import (
    ElementFingerprint, Step, Recording, save_recording,
)
from core.replay import replay_recording


def _file_url(name: str) -> str:
    return "file://" + os.path.abspath(f"test_form/{name}").replace("\\", "/")


def _fp(
    fp_id: str,
    *,
    primary_strategy: str,
    primary_value: str,
    fallbacks: list[dict] | None = None,
    attrs: dict | None = None,
) -> ElementFingerprint:
    return ElementFingerprint(
        id=fp_id,
        primary_locator={"strategy": primary_strategy, "value": primary_value},
        fallback_locators=list(fallbacks or []),
        attributes=attrs or {},
        page_context={"url": "", "section_label": ""},
    )


@pytest.mark.asyncio
async def test_heals_when_id_and_name_both_renamed():
    """Stored fingerprint's id+name no longer exist; healer must relocate
    by label + placeholder + tag/type."""
    fp = _fp(
        "el-firstname",
        primary_strategy="id",
        primary_value="legacy_first_name_id",  # not present on either schema
        fallbacks=[
            {"strategy": "name", "value": "legacy_first_name_name"},
        ],
        attrs={
            "tag": "input",
            "type": "text",
            "id": "legacy_first_name_id",
            "name": "legacy_first_name_name",
            "placeholder": "Enter first name",
            "nearest_label_text": "First Name",
            "aria_label": "",
            "role": "",
            "html5_constraints": {"pattern": "", "required": False, "maxlength": "",
                                  "minlength": "", "min": "", "max": ""},
            "autocomplete": "",
        },
    )
    recording = Recording(
        id="rec-heal-test-1",
        name="heal-test-1",
        kind="scenario",
        application_id="test-app",
        created_at="",
        start_url=_file_url("v2_id_changes.html"),
        steps=[
            Step(index=0, action="fill", element=fp, value="Alice"),
        ],
    )
    outcome = await replay_recording(recording, headless=True)
    assert outcome.error is None, f"expected heal to succeed, got: {outcome.error}"
    assert outcome.completed_steps == 1
    assert outcome.healed_steps == 1
    result = outcome.step_results[0]
    assert result["status"] == "passed"
    assert result.get("healed") is not None
    healed = result["healed"]
    assert healed["method"] in ("auto", "ai-confirmed")
    assert healed["new_primary_locator"]["value"] in ("firstName", "fName")
    # Confidence is reported as a 0..1 float
    assert 0.0 <= healed["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_records_unresolved_when_field_truly_absent():
    """Asking the healer to relocate something that doesn't exist on the
    target page must NOT silently heal to an unrelated field — it must
    fail loudly with diagnostics."""
    fp = _fp(
        "el-nonexistent",
        primary_strategy="id",
        primary_value="this_field_does_not_exist",
        attrs={
            "tag": "input",
            "type": "text",
            "id": "this_field_does_not_exist",
            "name": "blood_type",
            "placeholder": "Enter your blood type",
            "nearest_label_text": "Blood Type",
            "autocomplete": "",
            "role": "",
            "aria_label": "",
            "html5_constraints": {"pattern": "", "required": False, "maxlength": "",
                                  "minlength": "", "min": "", "max": ""},
        },
    )
    recording = Recording(
        id="rec-heal-test-2",
        name="heal-test-2",
        kind="scenario",
        application_id="test-app",
        created_at="",
        start_url=_file_url("v2_id_changes.html"),
        steps=[
            Step(index=0, action="fill", element=fp, value="O+"),
        ],
    )
    outcome = await replay_recording(recording, headless=True, element_timeout_ms=1000)
    assert outcome.error is not None
    result = outcome.step_results[0]
    assert result["status"] == "failed"
    # Healer should have run and surfaced diagnostics
    assert result.get("heal_diagnostics"), (
        "expected healer diagnostics on failed step, "
        f"got: {result}"
    )


@pytest.mark.asyncio
async def test_heal_cache_avoids_rescanning_on_same_element():
    """If two steps touch the same fingerprint id and the first one heals,
    the second should hit the cache. We exercise the cache by running a
    fill then a follow-on click — both heal targets — and assert both
    steps pass with the same matched candidate."""
    # A fingerprint for the v1 First Name field whose locators all miss on v2.
    fname_fp = _fp(
        "el-fname",
        primary_strategy="id",
        primary_value="fName",  # v1 id, absent on v2
        fallbacks=[],
        attrs={
            "tag": "input", "type": "text",
            "id": "fName", "name": "firstName",
            "placeholder": "Enter first name",
            "nearest_label_text": "First Name",
            "autocomplete": "", "role": "", "aria_label": "",
            "html5_constraints": {"pattern": "", "required": False, "maxlength": "",
                                  "minlength": "", "min": "", "max": ""},
        },
    )
    # v2 keeps name="firstName" so this would resolve via the name fallback —
    # strip it to force a heal.
    fname_fp.fallback_locators = []

    # Two steps on the same physical element (same fp id). Second one should
    # use the cached heal — no second scan.
    rec = Recording(
        id="rec-heal-cache",
        name="heal-cache",
        kind="scenario",
        application_id="test-app",
        created_at="",
        start_url=_file_url("v2_id_changes.html"),
        steps=[
            Step(index=0, action="fill", element=fname_fp, value="Alice"),
            Step(index=1, action="fill", element=fname_fp, value="Bob"),
        ],
    )
    outcome = await replay_recording(rec, headless=True)
    assert outcome.error is None, outcome.error
    assert outcome.completed_steps == 2
    # First step records the heal; second step hits cache (also flagged healed).
    assert outcome.healed_steps == 2
    assert outcome.step_results[0]["healed"]["new_primary_locator"] == \
           outcome.step_results[1]["healed"]["new_primary_locator"]
