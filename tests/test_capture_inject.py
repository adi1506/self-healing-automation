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
