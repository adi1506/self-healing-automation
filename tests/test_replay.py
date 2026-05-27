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
    # Required + recorded value → non-skippable fill, so a missing target
    # surfaces as a real failure (not a skip-and-continue). See
    # _is_step_skippable: optional fills are now safely skipped on
    # unresolved/field_removed verdicts, so this test pins the
    # *non-skippable* fill case to keep failure-reporting coverage.
    bad_fp = ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "id", "value": "no-such-element"},
        fallback_locators=[],
        attributes={
            "tag": "input",
            "html5_constraints": {"required": True, "pattern": "", "maxlength": "", "minlength": "", "min": "", "max": ""},
        },
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


def test_is_flutter_ordinal_locator_recognizes_all_strategies():
    """A Flutter ordinal id can show up in any of three strategies the
    recorder emits — all must be detected so they're filtered out of
    the locator chain at replay time."""
    from core.replay import _is_flutter_ordinal_locator

    assert _is_flutter_ordinal_locator({"strategy": "id", "value": "flt-semantic-node-45"})
    assert _is_flutter_ordinal_locator({"strategy": "css", "value": "flt-semantics#flt-semantic-node-45"})
    assert _is_flutter_ordinal_locator({"strategy": "xpath", "value": "//*[@id='flt-semantic-node-45']"})
    # Real ids, not ordinals — must NOT be filtered.
    assert not _is_flutter_ordinal_locator({"strategy": "id", "value": "username"})
    assert not _is_flutter_ordinal_locator({"strategy": "css", "value": "#login-button"})
    assert not _is_flutter_ordinal_locator({"strategy": "xpath", "value": "//button[text()='Submit']"})
    # Edge: the literal phrase "flt-semantic-node" without a digit suffix
    # is NOT an ordinal (could be a class name etc.) — be conservative.
    assert not _is_flutter_ordinal_locator({"strategy": "css", "value": "flt-semantic-node-host"})


def test_find_element_skips_flutter_ordinal_locators_and_goes_to_healer():
    """End-to-end: a fingerprint whose entire locator chain is Flutter
    ordinals (id, css, xpath all referencing flt-semantic-node-N) must
    not match by coincidence on the live page. Filter the chain to
    empty, fall through to the healer, and let it pick by text/bbox."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import ElementFingerprint
    from core.replay import find_element_by_fingerprint, HealContext
    from core.replay_healer import HealDecision

    fp = ElementFingerprint(
        id="fp-tile",
        primary_locator={"strategy": "id", "value": "flt-semantic-node-45"},
        fallback_locators=[
            {"strategy": "css", "value": "flt-semantics#flt-semantic-node-45"},
            {"strategy": "xpath", "value": "//*[@id='flt-semantic-node-45']"},
        ],
        attributes={"tag": "flt-semantics", "text_content": "Create Application"},
        page_context={"url": "https://x/dashboard"},
    )

    healer_called = {"yes": False}

    async def fake_heal(page, fingerprint, *, action, ai_matcher=None, force_candidate_index=None):
        healer_called["yes"] = True
        return HealDecision(
            method="auto",
            confidence=0.95,
            new_primary_locator={"strategy": "css", "value": "[data-testid='create-app']"},
        )

    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    # Live page DOES have flt-semantic-node-45 (just a coincidence — it's
    # some random other element this session). If the chain weren't
    # stripped, locator.count() would return 1 and we'd return the wrong
    # element. After the fix, the locator is filtered out and never even
    # tried.
    coincidental_loc = MagicMock()
    coincidental_loc.count = AsyncMock(return_value=1)
    healed_loc = MagicMock()
    healed_loc.count = AsyncMock(return_value=1)
    def locator_factory(selector):
        if "create-app" in selector:
            return healed_loc
        return coincidental_loc
    page.locator = MagicMock(side_effect=locator_factory)

    ctx = HealContext()
    ctx.action = "click"

    with patch("core.replay.attempt_heal", side_effect=fake_heal):
        result = asyncio.run(find_element_by_fingerprint(
            page, fp, timeout_ms=10, heal_context=ctx,
        ))

    assert healer_called["yes"], (
        "healer must be invoked when all stored locators are Flutter ordinals; "
        "otherwise the coincidental-match bug would silently return wrong element"
    )
    assert result is healed_loc


def test_normalize_url_for_compare_drops_query_keeps_hash():
    from core.replay import _normalize_url_for_compare
    # Query string is noise (trackers, session tokens) and must be dropped.
    assert _normalize_url_for_compare("https://x.com/app?ref=email") == \
           _normalize_url_for_compare("https://x.com/app?ref=twitter")
    # Fragment is the SPA route for hash-routed apps (Flutter web) and
    # must be preserved.
    a = _normalize_url_for_compare("https://x.com/app/#/dashboard")
    b = _normalize_url_for_compare("https://x.com/app/#/newApplication")
    assert a != b
    assert "dashboard" in a and "newApplication" in b
    # Trailing slash on path is normalized away so /app and /app/ match.
    assert _normalize_url_for_compare("https://x.com/app") == \
           _normalize_url_for_compare("https://x.com/app/")
    # Empty is empty (caller skips the check in this case).
    assert _normalize_url_for_compare("") == ""


def test_replay_warns_when_live_page_diverges_from_recorded_url():
    """If the replay is on /dashboard but the step's element was recorded
    on /newApplication, surface a page_context_warning entry. The step
    still attempts to execute — the warning is informational, not fatal."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording

    fp = ElementFingerprint(
        id="fp-radio",
        primary_locator={"strategy": "id", "value": "some-radio"},
        fallback_locators=[],
        attributes={"tag": "input", "type": "radio"},
        page_context={"url": "https://app.example.com/webapp/#/internal/newApplication"},
    )
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="",
        start_url="https://app.example.com/webapp",
        steps=[Step(index=0, action="click", value=None, element=fp)],
    )

    with patch("core.replay.async_playwright") as mock_pw:
        page = MagicMock()
        page.goto = AsyncMock()
        # Live URL is the dashboard — does NOT match the recorded URL.
        page.url = "https://app.example.com/webapp/#/internal/dashboard"
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])

        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        loc.first.click = AsyncMock()
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

    assert len(outcome.page_context_warnings) == 1
    w = outcome.page_context_warnings[0]
    assert w["step_index"] == 0
    assert "newApplication" in w["expected_url"]
    assert "dashboard" in w["actual_url"]
    # Warning-only: the step is allowed to attempt execution; the mocked
    # locator resolves so the run "passes". The warning's purpose is
    # diagnostic, not gating.
    assert outcome.step_results[0].get("page_context_warning") == w


def test_replay_does_not_warn_when_spa_redirect_settles_within_window():
    """SPA initial-redirect false-positive guard: if the actual URL has no
    fragment but the expected URL does, give the app a short window for
    the hash route to appear. If it converges, no warning. This is the
    Flutter web case: page.goto returns on /webapp before the in-app
    router has redirected to /webapp/#/internal/login."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording

    fp = ElementFingerprint(
        id="fp-username",
        primary_locator={"strategy": "id", "value": "username"},
        fallback_locators=[],
        attributes={"tag": "input", "type": "text"},
        page_context={"url": "https://app.example.com/webapp/#/internal/login"},
    )
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="",
        start_url="https://app.example.com/webapp",
        steps=[Step(index=0, action="fill", value="x", element=fp)],
    )

    with patch("core.replay.async_playwright") as mock_pw:
        page = MagicMock()
        page.goto = AsyncMock()
        # Initial state: page.goto just returned, hash route not yet set
        page.url = "https://app.example.com/webapp"

        # The SPA "redirect" — after the first wait_for_timeout, the hash
        # route appears. Simulates Flutter's in-app router catching up.
        async def settle_url(ms):
            page.url = "https://app.example.com/webapp/#/internal/login"
        page.wait_for_timeout = AsyncMock(side_effect=settle_url)
        page.evaluate = AsyncMock(return_value=[])
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        loc.first.fill = AsyncMock()
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

    assert outcome.page_context_warnings == [], (
        f"SPA-redirect timing should suppress the warning once URL converges; "
        f"got: {outcome.page_context_warnings}"
    )


def test_replay_does_not_warn_when_only_query_string_differs():
    """Query strings (tracking params, session ids) are stripped before
    comparison — they must not produce false-positive warnings."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording

    fp = ElementFingerprint(
        id="fp-btn",
        primary_locator={"strategy": "id", "value": "btn"},
        fallback_locators=[],
        attributes={"tag": "button"},
        page_context={"url": "https://app.example.com/page?ref=email"},
    )
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="",
        start_url="https://app.example.com/page",
        steps=[Step(index=0, action="click", value=None, element=fp)],
    )

    with patch("core.replay.async_playwright") as mock_pw:
        page = MagicMock()
        page.goto = AsyncMock()
        page.url = "https://app.example.com/page?ref=twitter"  # diff query, same page
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        loc.first.click = AsyncMock()
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

    assert outcome.page_context_warnings == []
    assert "page_context_warning" not in outcome.step_results[0]


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
    assert outcome.skipped_steps[0]["reason"] == "field_removed"
    # Step 1 should have run normally
    assert outcome.completed_steps == 1
    # step_results: phone is skipped_removed, email is passed
    statuses = [r["status"] for r in outcome.step_results]
    assert statuses == ["skipped_removed", "passed"]
    assert outcome.step_results[0]["skip_reason"] == "field_removed"


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


def test_replay_skips_unresolved_on_optional_fill_and_continues(monkeypatch):
    """When the healer returns `unresolved` for an optional fill (gray-zone
    score, no AI confirmation), the step is skipped with reason='unresolved'
    and the run continues. Distinct from field_removed: the field MAY still
    be on the page, the healer just couldn't commit a heal.

    Status code is `skipped_unresolved` (not `skipped_removed`) so the UI
    can show distinct copy."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.recording import Recording, Step, ElementFingerprint
    from core.replay import replay_recording
    from core.replay_healer import HealDecision

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

    async def fake_attempt_heal(page, fp, *, action, ai_matcher=None, force_candidate_index=None):
        if fp.id == "fp-phone":
            return HealDecision.unresolved(diagnostics="best candidate scored 0.65, no AI to confirm")
        return HealDecision.unresolved(diagnostics="unexpected")

    with patch("core.replay.async_playwright") as mock_pw, \
         patch("core.replay.attempt_heal", side_effect=fake_attempt_heal):
        page = MagicMock()
        page.goto = AsyncMock()
        page.url = "https://example.com/form"
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])

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

    assert outcome.failed_step_index is None, \
        f"unexpectedly failed at step {outcome.failed_step_index}: {outcome.error}"
    assert len(outcome.skipped_steps) == 1
    assert outcome.skipped_steps[0]["step_index"] == 0
    assert outcome.skipped_steps[0]["fingerprint_id"] == "fp-phone"
    assert outcome.skipped_steps[0]["reason"] == "unresolved"
    statuses = [r["status"] for r in outcome.step_results]
    assert statuses == ["skipped_unresolved", "passed"]
    assert outcome.step_results[0]["skip_reason"] == "unresolved"
    # Removal-diagnostics field carries the healer's explanation
    assert "0.65" in outcome.step_results[0]["removal_diagnostics"]


def test_replay_does_not_skip_unresolved_on_blocker_step(monkeypatch):
    """Even if the healer is `unresolved` (not `field_removed`), a click
    step is still a blocker — the run fails. Pins that the broadened
    skip path doesn't accidentally swallow blocker failures."""
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
        return HealDecision.unresolved(diagnostics="best candidate scored 0.70 — gray zone, no AI")

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


def test_auto_fill_wrapper_preserves_skipped_steps_from_first_run(monkeypatch):
    """If first run skipped a field_removed step AND detected a new
    required field, the retry's outcome must still carry the
    skipped_steps from the first run (the skip is real, regardless of
    whether the retry confirms it again)."""
    from core.replay import ReplayOutcome, replay_recording_with_auto_fill
    from unittest.mock import patch
    import asyncio
    from core.recording import Recording

    # First-call outcome: 1 skipped step + 1 failure + new_required_fields detected
    first = ReplayOutcome()
    first.failed_step_index = 1
    first.skipped_steps = [{
        "step_index": 0, "action": "fill",
        "fingerprint_id": "fp-phone", "field_label": "Phone",
        "diagnostics": "phone removed",
    }]
    first.new_required_fields_detected = [{
        "fingerprint": {
            "id": "fp-newfield",
            "primary_locator": {"strategy": "id", "value": "new"},
            "fallback_locators": [], "attributes": {"tag": "input", "type": "text"},
            "page_context": {},
        },
        "error_text": "Required",
    }]
    # Second-call outcome: passed, skipped_steps empty (retry didn't see the
    # phone-skip because the recording for retry only had the email step
    # + auto-filled new-field — phone was original).
    second = ReplayOutcome()
    second.failed_step_index = None
    second.skipped_steps = []
    second.completed_steps = 2

    call_count = {"n": 0}
    async def fake_replay(*args, **kwargs):
        call_count["n"] += 1
        return first if call_count["n"] == 1 else second

    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="", start_url="https://x.com", steps=[],
    )
    with patch("core.replay.replay_recording", side_effect=fake_replay):
        result = asyncio.run(replay_recording_with_auto_fill(rec))

    assert result.failed_step_index is None  # retry passed
    assert len(result.skipped_steps) == 1
    assert result.skipped_steps[0]["fingerprint_id"] == "fp-phone"


def test_record_time_fields_roundtrip(tmp_path):
    """A Recording with record_time_fields survives save/load and old
    recordings without the key load with an empty list (migration)."""
    from core.recording import Recording, save_recording, load_recording

    fields = [
        {
            "id": "el-1", "name": "firstName",
            "nearest_label_text": "First Name", "autocomplete": "given-name",
            "tag": "input", "is_required": True,
        },
        {
            "id": "el-2", "name": "newsletter",
            "nearest_label_text": "Subscribe to newsletter",
            "autocomplete": "", "tag": "input", "is_required": False,
        },
    ]
    rec = Recording(
        id="r1", name="t", kind="scenario", application_id="a",
        created_at="", start_url="https://x", steps=[],
        record_time_fields=fields,
    )
    path = tmp_path / "rec.yaml"
    save_recording(str(path), rec)
    reloaded = load_recording(str(path))
    assert reloaded.record_time_fields == fields

    # Migration: a YAML file with no record_time_fields key (older recording
    # from before this feature) must load with an empty list, not crash.
    import yaml
    legacy_path = tmp_path / "legacy.yaml"
    legacy_data = dict(rec.to_dict())
    legacy_data.pop("record_time_fields", None)
    legacy_path.write_text(yaml.safe_dump(legacy_data), encoding="utf-8")
    legacy = load_recording(str(legacy_path))
    assert legacy.record_time_fields == []


def test_schema_diff_finds_new_field_with_matching_label():
    """Recording snapshot has firstName, lastName. Live scan has those plus
    dateOfBirth. Schema diff should flag dateOfBirth as new, regardless of
    whether it's marked required. is_required is read from the live DOM."""
    from core.replay import _schema_diff_new_fields

    record = [
        {"id": "el-1", "name": "firstName", "nearest_label_text": "First Name",
         "autocomplete": "given-name", "tag": "input", "is_required": True},
        {"id": "el-2", "name": "lastName", "nearest_label_text": "Last Name",
         "autocomplete": "family-name", "tag": "input", "is_required": True},
    ]
    # Live scan: same two fields plus a new optional dateOfBirth
    snapshot = {
        3: [
            {"id": "live-1", "attributes": {"name": "firstName",
                                             "nearest_label_text": "First Name",
                                             "is_required": True, "tag": "input"}},
            {"id": "live-2", "attributes": {"name": "lastName",
                                             "nearest_label_text": "Last Name",
                                             "is_required": True, "tag": "input"}},
            {"id": "live-3", "attributes": {"name": "dateOfBirth",
                                             "nearest_label_text": "Date of Birth",
                                             "is_required": False, "tag": "input"}},
        ],
    }
    new = _schema_diff_new_fields(
        record_time_fields=record,
        pre_submit_schema_snapshot=snapshot,
        already_detected_keys=set(),
    )
    assert len(new) == 1
    assert new[0]["discovery"] == "schema_diff"
    assert new[0]["fingerprint"]["attributes"]["name"] == "dateOfBirth"
    assert new[0]["is_required"] is False  # read from live DOM, optional here
    assert new[0]["submit_step_index"] == 3


def test_schema_diff_ignores_fields_present_at_record_time():
    """If the recording already knows about a field (e.g., an unfilled
    newsletter checkbox), re-replay against the same form should NOT
    flag it as new — even though no step fills it."""
    from core.replay import _schema_diff_new_fields

    record = [
        {"id": "el-1", "name": "email", "nearest_label_text": "Email",
         "autocomplete": "email", "tag": "input", "is_required": True},
        {"id": "el-2", "name": "newsletter", "nearest_label_text": "Newsletter",
         "autocomplete": "", "tag": "input", "is_required": False},
    ]
    snapshot = {
        2: [
            {"id": "live-1", "attributes": {"name": "email",
                                             "nearest_label_text": "Email",
                                             "is_required": True, "tag": "input"}},
            {"id": "live-2", "attributes": {"name": "newsletter",
                                             "nearest_label_text": "Newsletter",
                                             "is_required": False, "tag": "input"}},
        ],
    }
    new = _schema_diff_new_fields(
        record_time_fields=record,
        pre_submit_schema_snapshot=snapshot,
        already_detected_keys=set(),
    )
    assert new == []


def test_schema_diff_matches_by_label_when_name_differs():
    """Fuzzy match on nearest_label_text catches a label like 'First Name'
    matching record-time 'First name' (case + spacing tolerated)."""
    from core.replay import _schema_diff_new_fields

    record = [
        {"id": "el-1", "name": "fName", "nearest_label_text": "First name",
         "autocomplete": "", "tag": "input", "is_required": True},
    ]
    snapshot = {
        1: [
            {"id": "live-1", "attributes": {"name": "first_name",
                                             "nearest_label_text": "First Name",
                                             "is_required": True, "tag": "input"}},
        ],
    }
    new = _schema_diff_new_fields(
        record_time_fields=record,
        pre_submit_schema_snapshot=snapshot,
        already_detected_keys=set(),
    )
    # Same field, just renamed at the attribute level — must not flag.
    assert new == []


def test_schema_diff_is_required_required_flag_set_when_live_dom_says_so():
    """A newly-added field marked required in the live DOM must carry
    is_required=True so the banner can flip to the red/alert variant."""
    from core.replay import _schema_diff_new_fields

    record = [
        {"id": "el-1", "name": "email", "nearest_label_text": "Email",
         "autocomplete": "email", "tag": "input", "is_required": True},
    ]
    snapshot = {
        2: [
            {"id": "live-1", "attributes": {"name": "email",
                                             "nearest_label_text": "Email",
                                             "is_required": True, "tag": "input"}},
            {"id": "live-2", "attributes": {"name": "dateOfBirth",
                                             "nearest_label_text": "Date of Birth",
                                             "is_required": True, "tag": "input"}},
        ],
    }
    new = _schema_diff_new_fields(
        record_time_fields=record,
        pre_submit_schema_snapshot=snapshot,
        already_detected_keys=set(),
    )
    assert len(new) == 1
    assert new[0]["is_required"] is True


def test_is_required_propagates_through_wrapper_to_auto_filled_fields(monkeypatch):
    """outcome.new_required_fields_detected[i]['is_required'] must land
    intact on outcome.auto_filled_fields[i]['is_required'] after the
    wrapper runs the proactive (no-rerun) path."""
    import asyncio
    from unittest.mock import patch
    from core.recording import Recording
    from core.replay import ReplayOutcome, replay_recording_with_auto_fill

    inner = ReplayOutcome()
    inner.failed_step_index = None
    inner.new_required_fields_detected = [
        {
            "fingerprint": {
                "id": "fp-dob",
                "primary_locator": {"strategy": "id", "value": "dob"},
                "fallback_locators": [],
                "attributes": {"tag": "input", "type": "date",
                               "name": "dateOfBirth",
                               "nearest_label_text": "Date of Birth",
                               "is_required": True},
                "page_context": {},
            },
            "error_text": "",
            "discovery": "schema_diff",
            "submit_step_index": 0,
            "is_required": True,
        },
        {
            "fingerprint": {
                "id": "fp-news",
                "primary_locator": {"strategy": "id", "value": "news"},
                "fallback_locators": [],
                "attributes": {"tag": "input", "type": "checkbox",
                               "name": "newsletter",
                               "nearest_label_text": "Newsletter",
                               "is_required": False},
                "page_context": {},
            },
            "error_text": "",
            "discovery": "schema_diff",
            "submit_step_index": 0,
            "is_required": False,
        },
    ]

    async def fake_replay(*args, **kwargs):
        return inner

    # Stub the AI value generator — heuristic fallback is fine; we only
    # care about the is_required propagation, not the value content.
    with patch("core.replay.replay_recording", side_effect=fake_replay):
        rec = Recording(
            id="r1", name="t", kind="scenario", application_id="a",
            created_at="", start_url="https://x", steps=[],
        )
        result = asyncio.run(replay_recording_with_auto_fill(rec))

    assert result.failed_step_index is None
    assert len(result.auto_filled_fields) == 2
    by_fp = {af["fingerprint_id"]: af for af in result.auto_filled_fields}
    assert by_fp["fp-dob"]["is_required"] is True
    assert by_fp["fp-news"]["is_required"] is False


def test_schema_diff_does_not_duplicate_already_detected_entry():
    """If post_submit_failure already flagged dateOfBirth (via error
    message), schema_diff should not append a duplicate entry for the
    same (name, label) pair."""
    from core.replay import _schema_diff_new_fields

    record = [
        {"id": "el-1", "name": "email", "nearest_label_text": "Email",
         "autocomplete": "email", "tag": "input", "is_required": True},
    ]
    snapshot = {
        2: [
            {"id": "live-1", "attributes": {"name": "dateOfBirth",
                                             "nearest_label_text": "Date of Birth",
                                             "is_required": True, "tag": "input"}},
        ],
    }
    already = {("dateofbirth", "date of birth")}
    new = _schema_diff_new_fields(
        record_time_fields=record,
        pre_submit_schema_snapshot=snapshot,
        already_detected_keys=already,
    )
    assert new == []
