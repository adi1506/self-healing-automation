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
