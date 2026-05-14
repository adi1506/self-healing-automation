"""Verify Setter.set_fields_on_page works against an externally-opened page.

The multi-page runner walks several URLs in one browser session, so the
field-setting logic must accept an existing Playwright page rather than
launching its own browser. This test loads a local HTML fixture in an
externally-managed browser, hands the page to the new helper, and asserts
the same result shape as Setter.set_fields would return.
"""
from __future__ import annotations

import asyncio
import os
import pathlib

from playwright.async_api import async_playwright

from core.browser_launch import launch_browser_and_page
from core.scanner import Scanner
from core.setter import Setter


FIXTURE = pathlib.Path(__file__).parent.parent / "test_form" / "sample_form.html"


def _file_url(p: pathlib.Path) -> str:
    return p.absolute().as_uri()


def test_set_fields_on_page_against_externally_opened_page():
    if not FIXTURE.exists():
        import pytest
        pytest.skip(f"fixture {FIXTURE} missing")

    url = _file_url(FIXTURE)
    scanner = Scanner()
    elements = scanner.scan(url)
    assert elements, "fixture should expose at least one scannable element"

    setter = Setter()
    test_data = {e["element_name"]: "x" for e in elements
                 if e["element_type"] not in ("button",)}

    async def _run():
        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            results = await setter.set_fields_on_page(
                page, elements, test_data, click_submit=False,
            )
            await browser.close()
            return results

    results = asyncio.get_event_loop().run_until_complete(_run())
    assert isinstance(results, list)
    # Every editable field in test_data should produce a result row.
    assert len(results) == len(test_data)
    for r in results:
        assert "status" in r
        assert "element_name" in r


def test_set_fields_url_path_still_works():
    """Regression: existing Setter.set_fields(url, ...) callers must keep
    working — the runner re-implementation must not break single-page runs."""
    if not FIXTURE.exists():
        import pytest
        pytest.skip(f"fixture {FIXTURE} missing")

    url = _file_url(FIXTURE)
    scanner = Scanner()
    elements = scanner.scan(url)
    setter = Setter()
    test_data = {e["element_name"]: "y" for e in elements
                 if e["element_type"] not in ("button",)}

    results = setter.set_fields(url, elements, test_data, click_submit=False)
    assert isinstance(results, list)
    assert len(results) == len(test_data)
