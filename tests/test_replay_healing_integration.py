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
    ElementFingerprint, Step, Recording, save_recording, load_recording,
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
    # `required=True` + recorded value pins this as a non-skippable fill.
    # The skip-and-continue path only applies to skippable steps; for the
    # "must fail loudly, must not silently heal to an unrelated field"
    # guarantee, the test needs a blocker. See _is_step_skippable.
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
            "html5_constraints": {"pattern": "", "required": True, "maxlength": "",
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


@pytest.mark.asyncio
async def test_passing_scenario_promotes_heals_to_recording(tmp_path):
    """Recording targets v1 schema; we replay against v2 (id changes only).
    Healer should fire, scenario should pass, recording.json should be
    updated with new primary locator + history."""
    fp = _fp(
        "el-firstname",
        primary_strategy="id",
        primary_value="fName",  # v1 id; v2 renames to firstName
        attrs={
            "tag": "input", "type": "text", "id": "fName",
            "name": "firstName", "nearest_label_text": "First Name",
            "placeholder": "Enter first name", "aria_label": "",
            "role": "", "autocomplete": "given-name",
            "html5_constraints": {
                "pattern": "", "required": True, "maxlength": "",
                "minlength": "", "min": "", "max": "",
            },
        },
    )
    # Strip name fallback so the heal path fires (v2 still has name="firstName").
    fp.fallback_locators = []
    rec = Recording(
        id="rec-test", name="t", kind="scenario",
        application_id="app-1", created_at="2026-05-21",
        start_url=_file_url("v2_id_changes.html"),
        steps=[
            Step(index=0, action="fill", value="Alice", element=fp),
        ],
    )
    rec_path = tmp_path / "rec.yaml"
    save_recording(str(rec_path), rec)

    outcome = await replay_recording(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True,
        healing_enabled=True,
    )
    assert outcome.failed_step_index is None
    assert outcome.healed_steps == 1
    assert len(outcome.promoted_heals) == 1

    reloaded = load_recording(str(rec_path))
    new_id = reloaded.steps[0].element.primary_locator.get("value")
    assert new_id != "fName"  # locator was rewritten away from the stale v1 id
    assert len(reloaded.steps[0].element.fingerprint_history) == 1


@pytest.mark.asyncio
async def test_failing_scenario_does_not_promote_heals(tmp_path):
    """Step 0 fills a field (heal works), step 1 targets a field that
    doesn't exist on the page (forced failure). Recording must NOT be
    updated."""
    from core.recording import save_recording, load_recording
    fp_first = _fp(
        "el-firstname",
        primary_strategy="id", primary_value="fName",
        attrs={
            "tag": "input", "type": "text",
            "nearest_label_text": "First Name",
            "autocomplete": "given-name",
            "html5_constraints": {"pattern": "", "required": False,
                                  "maxlength": "", "minlength": "", "min": "", "max": ""},
        },
    )
    # required=True so step 1 is a non-skippable blocker — pins that
    # promotion is correctly withheld on a real run failure (not on a
    # skip).
    fp_nonexistent = _fp(
        "el-bogus",
        primary_strategy="id", primary_value="nope_does_not_exist",
        attrs={
            "tag": "input", "type": "text",
            "nearest_label_text": "Nothing Like This",
            "html5_constraints": {"pattern": "", "required": True,
                                  "maxlength": "", "minlength": "", "min": "", "max": ""},
        },
    )
    rec = Recording(
        id="rec-fail", name="t", kind="scenario",
        application_id="app-1", created_at="2026-05-21",
        start_url=_file_url("v2_id_changes.html"),
        steps=[
            Step(index=0, action="fill", value="Alice", element=fp_first),
            Step(index=1, action="fill", value="X", element=fp_nonexistent),
        ],
    )
    rec_path = tmp_path / "rec.yaml"
    save_recording(str(rec_path), rec)

    from core.replay import replay_recording
    outcome = await replay_recording(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True, healing_enabled=True,
    )
    assert outcome.failed_step_index == 1
    assert outcome.promoted_heals == []
    reloaded = load_recording(str(rec_path))
    assert reloaded.steps[0].element.fingerprint_history == []
    assert reloaded.steps[0].element.primary_locator == {"strategy": "id", "value": "fName"}


@pytest.mark.asyncio
async def test_replay_with_force_runner_up_uses_second_best(tmp_path):
    """Pre-scenario: page has two phone-like fields. Without override the
    healer picks `phone_number`. With force_runner_up the healer picks
    the runner-up `mobile`."""
    from core.recording import save_recording, load_recording

    html = """<!doctype html><html><body><form>
        <label for="phone_number">Phone</label>
        <input id="phone_number" name="phone_number" autocomplete="tel">
        <label for="mobile">Mobile</label>
        <input id="mobile" name="mobile" autocomplete="tel">
    </form></body></html>"""
    page_path = tmp_path / "two_phones.html"
    page_path.write_text(html, encoding="utf-8")
    url = "file://" + str(page_path).replace("\\", "/")

    # required=True so the default-replay path runs the failure branch
    # (which sets heal_diagnostics), letting the test assert the top_k
    # ordering surfaced in diagnostics. An optional fill would now skip
    # via the skip-and-continue path and skip-results carry the diagnostic
    # under `removal_diagnostics` instead — which would be a different
    # contract for the runner-up retry.
    fp = _fp(
        "el-phone",
        primary_strategy="id", primary_value="phone",  # doesn't exist on page
        attrs={
            "tag": "input", "type": "text", "id": "phone", "name": "phone",
            "nearest_label_text": "Phone", "autocomplete": "tel",
            "html5_constraints": {"pattern": "", "required": True,
                                  "maxlength": "", "minlength": "", "min": "", "max": ""},
        },
    )
    rec = Recording(
        id="r-tp", name="t", kind="scenario", application_id="a",
        created_at="2026-05-21", start_url=url,
        steps=[Step(index=0, action="fill", value="555", element=fp)],
    )
    rec_path = tmp_path / "rec.yaml"
    save_recording(str(rec_path), rec)

    from core.replay import replay_recording

    # Default: top candidate scores in the gray zone with no AI matcher,
    # so the heal stays unresolved — but diagnostics confirm phone_number
    # ranks #1 and mobile ranks #2. That ordering is what force_runner_up
    # indexes into.
    out_default = await replay_recording(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True, healing_enabled=True,
        promote_on_pass=False,
        element_timeout_ms=1000,
    )
    diag = out_default.step_results[0].get("heal_diagnostics", "")
    assert "#1 'Phone'" in diag and "#2 'Mobile'" in diag, diag

    # Re-replay with force_runner_up: the forced path bypasses the
    # gray-zone gate and picks top_k index 1 (= mobile).
    out_forced = await replay_recording(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True, healing_enabled=True,
        promote_on_pass=False,
        force_runner_up={"el-phone": 1},
    )
    assert out_forced.healed_steps == 1, out_forced.error
    healed_forced = out_forced.step_results[0].get("healed")
    assert healed_forced["new_primary_locator"]["value"] == "mobile"


@pytest.mark.asyncio
async def test_detects_new_required_field_on_submit_failure(tmp_path):
    """Form gains a required `country` field. Scenario fills name, clicks
    submit, next-step assertion fails. Replay should detect the new required
    field in outcome.new_required_fields_detected."""
    from core.recording import save_recording, load_recording

    html = """<!doctype html><html><body><form id="f" onsubmit="
        const c = document.getElementById('country');
        if (!c.value) {
            const e = document.createElement('div');
            e.className = 'error-message';
            e.id = 'country-error';
            e.textContent = 'Country is required';
            c.parentElement.appendChild(e);
            return false;
        }
        document.body.innerHTML = '<h1 id=done>Done</h1>';
        return false;
    ">
        <input id="name" name="name" required>
        <input id="country" name="country" aria-required="true">
        <button type="submit" id="submit-btn">Submit</button>
    </form></body></html>"""
    page_path = tmp_path / "f.html"
    page_path.write_text(html, encoding="utf-8")
    url = "file://" + str(page_path).replace("\\", "/")

    fp_name = _fp("el-name", primary_strategy="id", primary_value="name",
                  attrs={"tag": "input", "type": "text", "id": "name", "name": "name",
                         "nearest_label_text": "Name",
                         "html5_constraints": {"pattern": "", "required": True,
                                               "maxlength": "", "minlength": "", "min": "", "max": ""}})
    fp_submit = _fp("el-submit", primary_strategy="id", primary_value="submit-btn",
                    attrs={"tag": "button", "type": "submit", "id": "submit-btn",
                           "nearest_label_text": "Submit",
                           "html5_constraints": {"pattern": "", "required": False,
                                                 "maxlength": "", "minlength": "", "min": "", "max": ""}})
    fp_done = _fp("el-done", primary_strategy="id", primary_value="done",
                  attrs={"tag": "h1", "type": "", "id": "done",
                         "nearest_label_text": "",
                         "html5_constraints": {"pattern": "", "required": False,
                                               "maxlength": "", "minlength": "", "min": "", "max": ""}})

    rec = Recording(
        id="r-n2", name="t", kind="scenario", application_id="a",
        created_at="2026-05-21", start_url=url,
        steps=[
            Step(index=0, action="fill", value="Alice", element=fp_name),
            Step(index=1, action="click", value=None, element=fp_submit),
            Step(index=2, action="click", value=None, element=fp_done),
        ],
    )
    rec_path = tmp_path / "rec.yaml"
    save_recording(str(rec_path), rec)

    from core.replay import replay_recording
    outcome = await replay_recording(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True, healing_enabled=True,
        promote_on_pass=False,
    )
    assert outcome.failed_step_index == 2  # the "Done" step never loaded
    assert outcome.new_required_fields_detected
    names = [
        nr["fingerprint"]["attributes"].get("id") or nr["fingerprint"]["attributes"].get("name")
        for nr in outcome.new_required_fields_detected
    ]
    assert "country" in names


@pytest.mark.asyncio
async def test_auto_retry_fills_new_required_field_and_passes(tmp_path):
    """Same fixture as C2's test, but the auto-retry path should kick in,
    fill country, and the scenario should pass."""
    from core.recording import save_recording, load_recording

    html = """<!doctype html><html><body><form id="f" onsubmit="
        const c = document.getElementById('country');
        if (!c.value) {
            const e = document.createElement('div');
            e.className = 'error-message';
            e.id = 'country-error';
            e.textContent = 'Country is required';
            c.parentElement.appendChild(e);
            return false;
        }
        document.body.innerHTML = '<h1 id=done>Done</h1>';
        return false;
    ">
        <input id="name" name="name" required>
        <input id="country" name="country" aria-required="true">
        <button type="submit" id="submit-btn">Submit</button>
    </form></body></html>"""
    page_path = tmp_path / "f.html"
    page_path.write_text(html, encoding="utf-8")
    url = "file://" + str(page_path).replace("\\", "/")

    fp_name = _fp("el-name", primary_strategy="id", primary_value="name",
                  attrs={"tag": "input", "type": "text", "id": "name", "name": "name",
                         "nearest_label_text": "Name",
                         "html5_constraints": {"pattern": "", "required": True,
                                               "maxlength": "", "minlength": "", "min": "", "max": ""}})
    fp_submit = _fp("el-submit", primary_strategy="id", primary_value="submit-btn",
                    attrs={"tag": "button", "type": "submit", "id": "submit-btn",
                           "nearest_label_text": "Submit",
                           "html5_constraints": {"pattern": "", "required": False,
                                                 "maxlength": "", "minlength": "", "min": "", "max": ""}})
    fp_done = _fp("el-done", primary_strategy="id", primary_value="done",
                  attrs={"tag": "h1", "type": "", "id": "done",
                         "nearest_label_text": "",
                         "html5_constraints": {"pattern": "", "required": False,
                                               "maxlength": "", "minlength": "", "min": "", "max": ""}})

    rec = Recording(
        id="r-c4", name="t", kind="scenario", application_id="a",
        created_at="2026-05-21", start_url=url,
        steps=[
            Step(index=0, action="fill", value="Alice", element=fp_name),
            Step(index=1, action="click", value=None, element=fp_submit),
            Step(index=2, action="click", value=None, element=fp_done),
        ],
    )
    rec_path = tmp_path / "rec.yaml"
    save_recording(str(rec_path), rec)

    from core.replay import replay_recording_with_auto_fill
    outcome = await replay_recording_with_auto_fill(
        load_recording(str(rec_path)),
        recording_path=str(rec_path),
        headless=True, healing_enabled=True,
        promote_on_pass=False,
    )
    # The original run fails, then auto-retry inserts country=... and passes
    assert outcome.failed_step_index is None
    assert outcome.auto_filled_fields  # list of dicts
    assert outcome.original_failure  # carries the original failure info
    # The auto-filled list should include the country field
    fingerprints = [af["fingerprint_id"] for af in outcome.auto_filled_fields]
    assert any("country" in fid or af["attributes"].get("id") == "country"
               for fid, af in zip(fingerprints, outcome.auto_filled_fields))
