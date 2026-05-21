import os
import pytest
from playwright.async_api import async_playwright
from core.capture import load_inject_js


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_fingerprint_extraction_on_input_field(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        await page.goto(sample_form_url)
        # Pick any input on the form; sample_form.html has at least an
        # input with id="fName".
        fp = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        assert isinstance(fp["id"], str)
        assert "primary_locator" in fp
        assert "attributes" in fp
        assert fp["attributes"]["tag"] == "input"
        await browser.close()


@pytest.mark.asyncio
async def test_fingerprint_dedup_same_id_for_same_element(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        await page.goto(sample_form_url)
        fp1 = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        fp2 = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        assert fp1["id"] == fp2["id"]
        await browser.close()


@pytest.mark.asyncio
async def test_event_listeners_emit_to_record_fn(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        captured: list[dict] = []
        await page.expose_function("__sha_record", lambda payload: captured.append(payload))
        await page.goto(sample_form_url)
        # Re-init now that __sha_record exists on this page.
        await page.evaluate("() => window.__sha.attachListeners()")
        await page.fill("input", "hello")
        await page.evaluate("() => document.querySelector('input').dispatchEvent(new Event('change', {bubbles:true}))")
        # Give the page a microtask to flush.
        await page.wait_for_timeout(200)
        actions = [c["action"] for c in captured]
        assert "fill" in actions or "input" in actions
        await browser.close()


@pytest.mark.asyncio
async def test_click_on_submit_button_does_not_double_record_submit(sample_form_url):
    """Clicking a submit button inside a form must emit only a `click`, not also
    a `submit`. Two events for one action causes replay to fail on the second
    one after the first one navigates away from the form.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        captured: list[dict] = []
        await page.expose_function("__sha_record", lambda payload: captured.append(payload))
        await page.goto(sample_form_url)
        await page.evaluate("() => window.__sha.attachListeners()")
        # sample_form.html's inline script preventDefaults the submit so the
        # page stays put and we can read what was captured.
        page.on("dialog", lambda d: d.dismiss())
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(200)
        actions = [c["action"] for c in captured]
        assert actions.count("click") == 1
        assert "submit" not in actions
        await browser.close()


@pytest.mark.asyncio
async def test_enter_key_submit_still_emits_submit(sample_form_url):
    """Submitting via Enter (no click on a button) must still record a
    `submit` step — that's the only signal we have for keyboard-driven form
    submission.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        captured: list[dict] = []
        await page.expose_function("__sha_record", lambda payload: captured.append(payload))
        await page.goto(sample_form_url)
        await page.evaluate("() => window.__sha.attachListeners()")
        page.on("dialog", lambda d: d.dismiss())
        # Programmatic submit with no preceding click — equivalent to the user
        # hitting Enter in a text field.
        await page.evaluate(
            "() => document.getElementById('registrationForm').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))"
        )
        await page.wait_for_timeout(200)
        actions = [c["action"] for c in captured]
        assert "submit" in actions
        await browser.close()


@pytest.mark.asyncio
async def test_scan_required_fields_detects_html_required_and_aria_required(tmp_path):
    """Smoke test: a form with one HTML-required field, one aria-required
    field, one optional field. scanRequiredFields returns the two required
    ones."""
    from playwright.async_api import async_playwright
    from core.capture import load_inject_js

    html = """<!doctype html><html><body><form>
        <input id="a" name="a" required>
        <input id="b" name="b" aria-required="true">
        <input id="c" name="c">
    </form></body></html>"""
    page_path = tmp_path / "f.html"
    page_path.write_text(html, encoding="utf-8")

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        await page.goto("file://" + str(page_path).replace("\\", "/"))
        required = await page.evaluate("window.__sha.scanRequiredFields()")
        await b.close()

    ids = sorted([fp["attributes"]["id"] for fp in required])
    assert ids == ["a", "b"]
