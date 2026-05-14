from __future__ import annotations

import os
from difflib import SequenceMatcher
from playwright.async_api import async_playwright, Error as PlaywrightError
from core.runner_utils import (
    INSTALL_SUBMIT_PROBES_JS,
    READ_SUBMIT_PROBES_JS,
    interpret_submit_probes,
)
from core.scanner import _run_async, _wait_for_interactive
from core.browser_launch import launch_browser_and_page

# Below this similarity the supplied value and the closest live option are
# different enough that we'd rather fail loudly than silently fill the wrong
# choice. 0.7 matches the threshold the healer uses for attribute matching.
SELECT_FUZZY_THRESHOLD = 0.7


def _resolve_select_option(
    requested: str, live_options: list[tuple[str, str]]
) -> tuple[str, str] | None:
    """Map a requested label to one of the select's actual <option>s.

    live_options: [(value, text), ...] read straight from the DOM at run time.
    Returns the (value, text) of the matched option, or None when nothing is
    close enough — at which point the caller should fail fast with a clear
    error rather than letting Playwright's select_option time out for 30s.

    Match precedence:
      1. Exact match on text or value (case-sensitive)
      2. Case-insensitive match on text or value, trimmed
      3. Fuzzy match (SequenceMatcher ratio >= SELECT_FUZZY_THRESHOLD)
    """
    if not live_options:
        return None

    for value, text in live_options:
        if requested == text or requested == value:
            return (value, text)

    needle = requested.strip().lower()
    for value, text in live_options:
        if needle == (text or "").strip().lower() or needle == (value or "").strip().lower():
            return (value, text)

    best: tuple[float, tuple[str, str]] | None = None
    for value, text in live_options:
        # Score against whichever of value/text is more meaningful so options
        # like value="us" text="USA" are reachable from either side.
        score = max(
            SequenceMatcher(None, needle, (text or "").strip().lower()).ratio(),
            SequenceMatcher(None, needle, (value or "").strip().lower()).ratio(),
        )
        if best is None or score > best[0]:
            best = (score, (value, text))
    if best and best[0] >= SELECT_FUZZY_THRESHOLD:
        return best[1]
    return None


NON_EDITABLE_TYPES = {"button"}


class SelectValueError(Exception):
    """Raised when a select's requested value matches none of the live <option>s.

    Carries the live option list so the caller can surface it instead of
    Playwright's opaque 30-second select_option timeout.
    """

    def __init__(self, requested: str, options: list[str]):
        self.requested = requested
        self.options = options
        preview = ", ".join(options[:8]) + ("…" if len(options) > 8 else "")
        super().__init__(
            f"value {requested!r} not in options [{preview}]"
        )

LOCATOR_PRIORITY = [
    ("locator_id", "css"),
    ("locator_data_testid", "data-testid"),
    ("locator_name", "name"),
    ("locator_css", "css"),
    ("locator_xpath", "xpath"),
    ("locator_label", "label"),
]


class Setter:
    def __init__(self):
        # Populated after each set_fields call. None when click_submit was
        # False (we have no DOM signal to read), True/False otherwise.
        # The runner reads this to classify failure-expected test cases.
        self.last_form_rejected: bool | None = None

    def set_fields(
        self,
        url: str,
        element_map: list[dict],
        test_data: dict,
        screenshot_dir: str | None = None,
        run_id: str | None = None,
        click_submit: bool = False,
    ) -> list[dict]:
        """
        Populate form fields from test data and verify values.
        Returns list of verification result dicts.
        """
        return _run_async(self._set_fields_async(
            url, element_map, test_data, screenshot_dir, run_id, click_submit
        ))

    async def set_fields_on_page(
        self,
        page,
        element_map: list[dict],
        test_data: dict,
        screenshot_dir: str | None = None,
        run_id: str | None = None,
        click_submit: bool = False,
    ) -> list[dict]:
        """Run the field-setting logic against an already-open Playwright page.

        The browser/page lifetime is the caller's responsibility. Used by the
        multi-page runner so cookies/localStorage carry across pages within
        one session. Single-page callers should keep using `set_fields(url, ...)`,
        which launches its own browser and delegates here.
        """
        # Wait for client-side rendered forms to finish building DOM before
        # interacting. Static forms hit idle immediately; SPAs wait for fetch+render.
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await _wait_for_interactive(page, deadline_seconds=12)

        results = []

        for elem in element_map:
            elem_name = elem["element_name"]
            elem_type = elem["element_type"]

            if elem_type in NON_EDITABLE_TYPES:
                continue

            # Only skip when the field is absent from test_data. An
            # explicit empty string MUST be exercised (clear the field)
            # — that's how required-violation negatives prove the form
            # rejects empties.
            if elem_name not in test_data:
                continue
            value = "" if test_data[elem_name] is None else str(test_data[elem_name])

            handle = await self._find_element(page, elem)
            if not handle:
                results.append({
                    "element_name": elem_name,
                    "expected_value": value,
                    "actual_value": "ELEMENT NOT FOUND",
                    "status": "FAIL",
                })
                continue

            try:
                applied = await self._set_value(page, handle, elem, value)
            except SelectValueError as e:
                # The supplied value doesn't match any of the select's live
                # options. Not a browser-level rejection (the widget would
                # accept any of its options); it's stale or wrong test data.
                # Surface the option list so the user can fix it.
                results.append({
                    "element_name": elem_name,
                    "expected_value": value,
                    "actual_value": f"VALUE NOT IN OPTIONS: {e}",
                    "status": "FAIL",
                })
                continue
            except PlaywrightError as e:
                # Playwright refused the interaction — most commonly because
                # the widget itself rejects the value (e.g. typing letters
                # into <input type="number">). The browser blocking the
                # input IS the form rejecting it; record it distinctly so
                # the runner can interpret it correctly for negative cases.
                reason = str(e).splitlines()[0][:160]
                results.append({
                    "element_name": elem_name,
                    "expected_value": value,
                    "actual_value": f"BROWSER BLOCKED: {reason}",
                    "status": "BLOCKED",
                })
                continue

            actual = await self._read_value(page, handle, elem)

            # Compare against the value actually applied (which can differ
            # from the requested value for selects whose label we had to
            # fuzzy-resolve). This is the right yardstick: the form holds
            # the resolved option now, and that's what's stored.
            results.append({
                "element_name": elem_name,
                "expected_value": value,
                "actual_value": actual,
                "status": "PASS" if actual == applied else "FAIL",
            })

        self.last_form_rejected = None
        if click_submit:
            await self._run_submit_probe(page, element_map, screenshot_dir, run_id)

        if screenshot_dir and run_id:
            os.makedirs(screenshot_dir, exist_ok=True)
            screenshot_path = os.path.join(screenshot_dir, f"{run_id}.png")
            await page.screenshot(path=screenshot_path, full_page=True)

        return results

    async def _run_submit_probe(self, page, element_map, screenshot_dir, run_id):
        # caller has reset self.last_form_rejected to None already
        submit_elem = next(
            (e for e in element_map if e["element_type"] == "button"),
            None,
        )
        probes = None
        if submit_elem:
            btn = await self._find_element(page, submit_elem)
            if btn:
                # Install listeners BEFORE clicking — they capture the
                # `invalid` event (HTML5 rejection) and `submit` event
                # (acceptance) the browser dispatches synchronously.
                try:
                    await page.evaluate(INSTALL_SUBMIT_PROBES_JS)
                except Exception:
                    pass
                await btn.click()
                await page.wait_for_timeout(1000)
                try:
                    probes = await page.evaluate(READ_SUBMIT_PROBES_JS)
                except Exception:
                    probes = None
        self.last_form_rejected = interpret_submit_probes(probes)

    async def _set_fields_async(
        self,
        url: str,
        element_map: list[dict],
        test_data: dict,
        screenshot_dir: str | None = None,
        run_id: str | None = None,
        click_submit: bool = False,
    ) -> list[dict]:
        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                return await self.set_fields_on_page(
                    page, element_map, test_data, screenshot_dir, run_id, click_submit,
                )
            finally:
                await browser.close()

    async def _find_element(self, page, elem: dict):
        """Try locators in priority order to find the element."""
        for locator_key, locator_type in LOCATOR_PRIORITY:
            locator_value = elem.get(locator_key, "")
            if not locator_value:
                continue

            try:
                if locator_type == "css":
                    handle = await page.query_selector(locator_value)
                elif locator_type == "xpath":
                    handle = await page.query_selector(f"xpath={locator_value}")
                elif locator_type == "name":
                    handle = await page.query_selector(f"[name='{locator_value}']")
                elif locator_type == "data-testid":
                    handle = await page.query_selector(f"[data-testid='{locator_value}']")
                elif locator_type == "label":
                    handle = await page.query_selector(
                        f"label:has-text('{locator_value}') input, "
                        f"label:has-text('{locator_value}') select, "
                        f"label:has-text('{locator_value}') textarea"
                    )
                    if not handle:
                        label = await page.query_selector(f"label:has-text('{locator_value}')")
                        if label:
                            for_id = await label.get_attribute("for")
                            if for_id:
                                handle = await page.query_selector(f"#{for_id}")
                else:
                    continue

                if handle:
                    return handle
            except Exception:
                continue

        return None

    async def _set_value(self, page, handle, elem: dict, value: str) -> str:
        """Set a value on an element based on its type.

        Returns the value actually applied. For selects this can differ from
        the requested value when fuzzy/case-insensitive matching resolved to
        a different option label — the caller compares against the returned
        value so e.g. "USA " (trailing space) → "USA" still counts as PASS.
        For other element types it just echoes back the requested value.
        """
        elem_type = elem["element_type"]

        if elem_type in (
            "input-text", "input-email", "input-tel", "input-number",
            "input-password", "input-search", "input-url", "textarea",
            "input-date", "input-datetime-local", "input-month", "input-week",
            "input-time", "input-color", "input-range",
        ):
            await handle.click()
            await handle.fill("")
            if value:
                await handle.fill(value)
            return value

        elif elem_type == "select":
            if value:
                live_options = await handle.evaluate(
                    "el => Array.from(el.options).map(o => [o.value, (o.text || '').trim()])"
                )
                # Tuple-ize (JS returns lists)
                live_options = [(v, t) for v, t in live_options]
                resolved = _resolve_select_option(value, live_options)
                if resolved is None:
                    # Build the user-visible option list excluding obvious
                    # placeholders so the error doesn't tell the user they
                    # could have used "Select Country" — same heuristic the
                    # scanner uses when capturing available_options.
                    option_texts = []
                    for i, (v, t) in enumerate(live_options):
                        if not v:
                            continue
                        text = (t or "").strip()
                        if i == 0 and text and text.lower().startswith(("select ", "choose ", "pick ", "--", "—")):
                            continue
                        option_texts.append(text or v)
                    # SelectValueError carries the option list so the runner
                    # can show the user exactly what was on the page — far
                    # more actionable than Playwright's generic "did not find
                    # some options" after a 30s timeout.
                    raise SelectValueError(value, option_texts)
                # Select by value when it's distinct (handles forms where text
                # is decorative). Otherwise fall back to label.
                target_value, target_text = resolved
                if target_value and target_value != target_text:
                    await handle.select_option(value=target_value)
                else:
                    await handle.select_option(label=target_text)
                # Echo back what the form will actually report on read-back —
                # the option's visible text — so the PASS comparison lines up
                # when fuzzy matching changed the value (e.g. "USA " → "USA").
                return target_text
            else:
                # Reset to the empty/placeholder option so required-violation
                # negatives can be exercised.
                await handle.evaluate("el => { el.selectedIndex = 0; el.dispatchEvent(new Event('change', {bubbles: true})); }")
                return value

        elif elem_type == "radio":
            name = elem.get("locator_name", "")
            if value and name:
                radio = await page.query_selector(
                    f"input[type='radio'][name='{name}'][value='{value}']"
                )
                if radio:
                    await radio.click()
            elif not value and name:
                # Uncheck via DOM — radios have no native "uncheck" gesture.
                await page.evaluate(
                    "name => document.querySelectorAll(`input[type='radio'][name='${name}']`).forEach(r => r.checked = false)",
                    name,
                )
            return value

        elif elem_type == "checkbox":
            is_checked = await handle.is_checked()
            should_be_checked = value.lower() == "checked"
            if is_checked != should_be_checked:
                await handle.click()
            return value

        return value

    async def _read_value(self, page, handle, elem: dict) -> str:
        """Read the current value from an element for verification."""
        elem_type = elem["element_type"]

        if elem_type in (
            "input-text", "input-email", "input-tel", "input-number",
            "input-password", "input-search", "input-url", "textarea",
            "input-date", "input-datetime-local", "input-month", "input-week",
            "input-time", "input-color", "input-range",
        ):
            return await handle.input_value() or ""

        elif elem_type == "select":
            # Report "" when the placeholder/empty option is selected so an
            # explicit empty-value assertion (required-violation negative)
            # can match.
            return await handle.evaluate(
                "el => { const o = el.options[el.selectedIndex]; "
                "if (!o) return ''; "
                "if (!o.value) return ''; "
                "return o.text.trim(); }"
            )

        elif elem_type == "radio":
            name = elem.get("locator_name", "")
            if name:
                checked = await page.query_selector(
                    f"input[type='radio'][name='{name}']:checked"
                )
                if checked:
                    return await checked.get_attribute("value") or ""
            return ""

        elif elem_type == "checkbox":
            is_checked = await handle.is_checked()
            return "checked" if is_checked else "unchecked"

        return ""
