import os
from playwright.async_api import async_playwright
from core.scanner import _run_async


NON_EDITABLE_TYPES = {"button"}

LOCATOR_PRIORITY = [
    ("locator_id", "css"),
    ("locator_data_testid", "data-testid"),
    ("locator_name", "name"),
    ("locator_css", "css"),
    ("locator_xpath", "xpath"),
    ("locator_label", "label"),
]


class Setter:
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
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            results = []

            for elem in element_map:
                elem_name = elem["element_name"]
                elem_type = elem["element_type"]

                if elem_type in NON_EDITABLE_TYPES:
                    continue

                value = test_data.get(elem_name, "")
                if not value:
                    continue

                handle = await self._find_element(page, elem)
                if not handle:
                    results.append({
                        "element_name": elem_name,
                        "expected_value": value,
                        "actual_value": "ELEMENT NOT FOUND",
                        "status": "FAIL",
                    })
                    continue

                await self._set_value(page, handle, elem, value)
                actual = await self._read_value(page, handle, elem)

                results.append({
                    "element_name": elem_name,
                    "expected_value": value,
                    "actual_value": actual,
                    "status": "PASS" if actual == value else "FAIL",
                })

            if click_submit:
                submit_elem = next(
                    (e for e in element_map if e["element_type"] == "button"),
                    None,
                )
                if submit_elem:
                    btn = await self._find_element(page, submit_elem)
                    if btn:
                        await btn.click()
                        await page.wait_for_timeout(1000)

            if screenshot_dir and run_id:
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_path = os.path.join(screenshot_dir, f"{run_id}.png")
                await page.screenshot(path=screenshot_path, full_page=True)

            await browser.close()
            return results

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

    async def _set_value(self, page, handle, elem: dict, value: str):
        """Set a value on an element based on its type."""
        elem_type = elem["element_type"]

        if elem_type in ("input-text", "input-email", "input-tel", "input-number", "textarea"):
            await handle.click()
            await handle.fill("")
            await handle.fill(value)

        elif elem_type == "select":
            await handle.select_option(label=value)

        elif elem_type == "radio":
            name = elem.get("locator_name", "")
            if name:
                radio = await page.query_selector(
                    f"input[type='radio'][name='{name}'][value='{value}']"
                )
                if radio:
                    await radio.click()

        elif elem_type == "checkbox":
            is_checked = await handle.is_checked()
            should_be_checked = value.lower() == "checked"
            if is_checked != should_be_checked:
                await handle.click()

    async def _read_value(self, page, handle, elem: dict) -> str:
        """Read the current value from an element for verification."""
        elem_type = elem["element_type"]

        if elem_type in ("input-text", "input-email", "input-tel", "input-number", "textarea"):
            return await handle.input_value() or ""

        elif elem_type == "select":
            return await handle.evaluate(
                "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''"
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
