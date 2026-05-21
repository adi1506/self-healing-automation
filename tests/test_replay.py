import os
import pytest
from playwright.async_api import async_playwright
from core.recording import ElementFingerprint
from core.replay import find_element_by_fingerprint, ElementNotFound


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_find_by_primary_id_locator(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        # Use the first <input>'s id from sample_form.html — known to exist.
        first_input_id = await page.evaluate("() => document.querySelector('input').id")
        if not first_input_id:
            pytest.skip("sample_form.html's first input has no id")
        fp = ElementFingerprint(
            id="el-1",
            primary_locator={"strategy": "id", "value": first_input_id},
            fallback_locators=[],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        loc = await find_element_by_fingerprint(page, fp)
        assert await loc.count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_falls_back_when_primary_misses(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        first_input_name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not first_input_name:
            pytest.skip("sample_form.html's first input has no name")
        fp = ElementFingerprint(
            id="el-2",
            primary_locator={"strategy": "id", "value": "does-not-exist-xyz"},
            fallback_locators=[{"strategy": "name", "value": first_input_name}],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        loc = await find_element_by_fingerprint(page, fp)
        assert await loc.count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_raises_when_nothing_matches(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        fp = ElementFingerprint(
            id="el-3",
            primary_locator={"strategy": "id", "value": "nope"},
            fallback_locators=[{"strategy": "name", "value": "also-nope"}],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        with pytest.raises(ElementNotFound):
            await find_element_by_fingerprint(page, fp, timeout_ms=0)
        await browser.close()


from core.recording import Step, Recording
from core.replay import execute_step


def _fp_for_input_name(name: str, url: str) -> ElementFingerprint:
    return ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": url},
    )


@pytest.mark.asyncio
async def test_execute_fill_step(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        step = Step(index=0, action="fill", element=_fp_for_input_name(name, sample_form_url), value="ACME-42")
        await execute_step(page, step, override=None)
        actual = await page.eval_on_selector(f"[name='{name}']", "el => el.value")
        assert actual == "ACME-42"
        await browser.close()


@pytest.mark.asyncio
async def test_execute_fill_with_override(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        step = Step(index=0, action="fill", element=_fp_for_input_name(name, sample_form_url), value="recorded")
        await execute_step(page, step, override="OVERRIDDEN")
        actual = await page.eval_on_selector(f"[name='{name}']", "el => el.value")
        assert actual == "OVERRIDDEN"
        await browser.close()


from core.replay import replay_recording, ReplayOutcome


@pytest.mark.asyncio
async def test_replay_recording_walks_all_steps(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        await browser.close()

    fp = ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": sample_form_url},
    )
    recording = Recording(
        id="rec-test", name="t", kind="scenario", application_id="app-1",
        created_at="", start_url=sample_form_url,
        steps=[Step(index=0, action="fill", element=fp, value="hello")],
    )
    outcome = await replay_recording(recording, headless=True)
    assert outcome.completed_steps == 1
    assert outcome.failed_step_index is None
    assert outcome.error is None


@pytest.mark.asyncio
async def test_replay_recording_reports_failed_step(sample_form_url):
    bad_fp = ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "id", "value": "no-such-element"},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": sample_form_url},
    )
    recording = Recording(
        id="rec-test", name="t", kind="scenario", application_id="app-1",
        created_at="", start_url=sample_form_url,
        steps=[Step(index=0, action="fill", element=bad_fp, value="x")],
    )
    outcome = await replay_recording(recording, headless=True, element_timeout_ms=0)
    assert outcome.failed_step_index == 0
    assert outcome.error is not None


def test_promote_heals_to_recording_appends_history_and_updates_locator(tmp_path):
    from core.recording import (
        ElementFingerprint, Step, Recording, save_recording, load_recording,
    )
    from core.replay import _promote_heals_to_recording
    from core.replay_healer import HealDecision, CandidateRef

    old_attrs = {"id": "phone", "name": "phone"}
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a1",
        created_at="2026-05-21", start_url="http://x",
        steps=[Step(
            index=0, action="fill", value="555",
            element=ElementFingerprint(
                id="el-phone",
                primary_locator={"strategy": "id", "value": "phone"},
                fallback_locators=[{"strategy": "name", "value": "phone"}],
                attributes=old_attrs,
                page_context={},
            ),
        )],
    )
    path = tmp_path / "rec.yaml"
    save_recording(str(path), rec)

    new_primary = {"strategy": "id", "value": "phone_number"}
    decision = HealDecision(
        method="auto", confidence=0.91,
        new_primary_locator=new_primary,
        new_fallback_locators=[],
        top_k_candidates=[CandidateRef(
            primary_locator=new_primary,
            fallback_locators=[],
            attributes={"id": "phone_number", "name": "phone_number"},
            score=0.91,
        )],
    )

    _promote_heals_to_recording(
        str(path),
        promoted={"el-phone": decision},
        run_id="run-xyz",
    )

    reloaded = load_recording(str(path))
    fp = reloaded.steps[0].element
    assert fp.primary_locator == new_primary
    assert fp.attributes == {"id": "phone_number", "name": "phone_number"}
    assert len(fp.fingerprint_history) == 1
    h = fp.fingerprint_history[0]
    assert h.source == "heal"
    assert h.run_id == "run-xyz"
    assert h.confidence == 0.91
    assert h.previous_primary_locator == {"strategy": "id", "value": "phone"}
    assert h.previous_attributes == old_attrs
    assert reloaded.healed_at  # ISO timestamp set


def test_revert_last_heal_restores_previous_locator(tmp_path):
    from core.recording import (
        ElementFingerprint, Step, Recording, HistoryEntry,
        save_recording, load_recording,
    )
    from core.replay import _revert_last_heal_in_recording

    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a1",
        created_at="2026-05-21", start_url="http://x",
        steps=[Step(
            index=0, action="fill", value="x",
            element=ElementFingerprint(
                id="el-phone",
                primary_locator={"strategy": "id", "value": "phone_number"},
                fallback_locators=[],
                attributes={"id": "phone_number"},
                page_context={},
                fingerprint_history=[
                    HistoryEntry(
                        timestamp="2026-05-21T10:00:00Z",
                        run_id="run-abc",
                        source="heal",
                        confidence=0.91,
                        previous_primary_locator={"strategy": "id", "value": "phone"},
                        previous_fallback_locators=[],
                        previous_attributes={"id": "phone"},
                    ),
                ],
            ),
        )],
    )
    path = tmp_path / "rec.yaml"
    save_recording(str(path), rec)

    _revert_last_heal_in_recording(str(path), fingerprint_id="el-phone")

    reloaded = load_recording(str(path))
    fp = reloaded.steps[0].element
    assert fp.primary_locator == {"strategy": "id", "value": "phone"}
    assert fp.attributes == {"id": "phone"}
    # The revert itself becomes a history entry (so revert is revertable):
    assert len(fp.fingerprint_history) == 1
    assert fp.fingerprint_history[0].source == "heal"
    assert fp.fingerprint_history[0].previous_primary_locator == {
        "strategy": "id", "value": "phone_number",
    }


def test_replay_outcome_skipped_steps_defaults_to_empty_list():
    """New field for tracking field_removed-induced skips, separate from
    `step_results` (which uses status='skipped_removed' per-row) and from
    the post-failure cascade."""
    from core.replay import ReplayOutcome
    outcome = ReplayOutcome()
    assert outcome.skipped_steps == []
    # The two fields must be independent — skipping a step does not set
    # failed_step_index.
    assert outcome.failed_step_index is None


def test_replay_skips_field_removed_on_optional_fill_and_continues(monkeypatch):
    """Simulate: step 1 fill (optional, field removed), step 2 fill (passes).
    Expected: outcome.failed_step_index is None, skipped_steps has 1 entry,
    step 2 ran successfully."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording
    from core.replay_healer import HealDecision

    # Build a 2-step recording: optional fill, then required fill
    fp_optional = ElementFingerprint(
        id="fp-phone",
        primary_locator={"strategy": "id", "value": "phone-input"},
        fallback_locators=[],
        attributes={
            "tag": "input", "type": "tel",
            "html5_constraints": {"required": False, "pattern": "", "maxlength": "", "minlength": "", "min": "", "max": ""},
            "nearest_label_text": "Phone Number", "autocomplete": "tel",
        },
        page_context={"url": "https://example.com/form"},
    )
    fp_email = ElementFingerprint(
        id="fp-email",
        primary_locator={"strategy": "id", "value": "email-input"},
        fallback_locators=[],
        attributes={
            "tag": "input", "type": "email",
            "html5_constraints": {"required": True, "pattern": "", "maxlength": "", "minlength": "", "min": "", "max": ""},
            "nearest_label_text": "Email", "autocomplete": "email",
        },
        page_context={"url": "https://example.com/form"},
    )
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="", start_url="https://example.com/form",
        steps=[
            Step(index=0, action="fill", value="555-1212", element=fp_optional),
            Step(index=1, action="fill", value="user@x.com", element=fp_email),
        ],
    )

    # Mock attempt_heal to return field_removed for fp-phone, real find for fp-email
    async def fake_attempt_heal(page, fp, *, action, ai_matcher=None, force_candidate_index=None):
        if fp.id == "fp-phone":
            return HealDecision.field_removed(diagnostics="phone gone from page")
        # Shouldn't be called for email — it'll resolve via locator.
        return HealDecision.unresolved(diagnostics="unexpected")

    # Patch playwright at the boundary — heaviest mock-out, but smallest
    # surface area we need to fake.
    with patch("core.replay.async_playwright") as mock_pw, \
         patch("core.replay.attempt_heal", side_effect=fake_attempt_heal):
        # Build a chain of mocks that mimics async_playwright().__aenter__()...
        page = MagicMock()
        page.goto = AsyncMock()
        page.url = "https://example.com/form"
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])

        # Locator behavior: count() returns 0 for phone, 1 for email
        def locator_factory(selector):
            loc = MagicMock()
            if "phone" in selector:
                loc.count = AsyncMock(return_value=0)
            else:
                loc.count = AsyncMock(return_value=1)
                loc.first.fill = AsyncMock()
            return loc
        page.locator = MagicMock(side_effect=locator_factory)
        page.screenshot = AsyncMock()

        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.add_init_script = AsyncMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        p_inst = MagicMock()
        p_inst.chromium.launch = AsyncMock(return_value=browser)
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=p_inst)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        outcome = asyncio.run(replay_recording(rec, element_timeout_ms=10))

    assert outcome.failed_step_index is None, f"unexpectedly failed at step {outcome.failed_step_index}: {outcome.error}"
    assert len(outcome.skipped_steps) == 1
    assert outcome.skipped_steps[0]["step_index"] == 0
    assert outcome.skipped_steps[0]["fingerprint_id"] == "fp-phone"
    # Step 1 should have run normally
    assert outcome.completed_steps == 1
    # step_results: phone is skipped_removed, email is passed
    statuses = [r["status"] for r in outcome.step_results]
    assert statuses == ["skipped_removed", "passed"]


def test_replay_does_not_skip_field_removed_on_blocker_step(monkeypatch):
    """If a click step's target is field_removed, the run still fails —
    a click is a blocker. Subsequent steps are marked skipped (cascade)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording
    from core.replay_healer import HealDecision

    fp_btn = ElementFingerprint(
        id="fp-submit",
        primary_locator={"strategy": "id", "value": "submit-btn"},
        fallback_locators=[],
        attributes={
            "tag": "button", "type": "submit",
            "html5_constraints": {"required": False, "pattern": "", "maxlength": "", "minlength": "", "min": "", "max": ""},
            "nearest_label_text": "Sign Up",
        },
        page_context={"url": "https://example.com/form"},
    )
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="", start_url="https://example.com/form",
        steps=[Step(index=0, action="click", element=fp_btn)],
    )

    async def fake_attempt_heal(page, fp, *, action, ai_matcher=None, force_candidate_index=None):
        return HealDecision.field_removed(diagnostics="button gone")

    with patch("core.replay.async_playwright") as mock_pw, \
         patch("core.replay.attempt_heal", side_effect=fake_attempt_heal):
        page = MagicMock()
        page.goto = AsyncMock()
        page.url = "https://example.com/form"
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])
        loc = MagicMock(); loc.count = AsyncMock(return_value=0)
        page.locator = MagicMock(return_value=loc)
        page.screenshot = AsyncMock()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.add_init_script = AsyncMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        p_inst = MagicMock()
        p_inst.chromium.launch = AsyncMock(return_value=browser)
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=p_inst)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)
        outcome = asyncio.run(replay_recording(rec, element_timeout_ms=10))

    assert outcome.failed_step_index == 0
    assert outcome.skipped_steps == []
    assert outcome.step_results[0]["status"] == "failed"
    # Failure error message should mention field_removed for the UI to key on
    assert "field_removed" in (outcome.error or "") or \
           outcome.step_results[0].get("removal_diagnostics")
