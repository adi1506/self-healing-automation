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
            await find_element_by_fingerprint(page, fp)
        await browser.close()
