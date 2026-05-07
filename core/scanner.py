from __future__ import annotations

import asyncio
import sys
from playwright.async_api import async_playwright


def _run_async(coro):
    """Run an async coroutine from sync code, avoiding event loop conflicts with Streamlit."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class Scanner:
    def scan(self, url: str) -> list[dict]:
        """Scan a web page and extract all interactive elements with multiple locators."""
        return _run_async(self._scan_async(url))

    async def _scan_async(self, url: str) -> list[dict]:
        """Async implementation of scan."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # Allow client-side rendered forms (SPA / schema-driven) to finish
            # building DOM before we extract elements. Static forms hit idle
            # almost immediately; dynamic forms wait for their fetch+render cycle.
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            elements = await self.scan_current_page(page)
            await browser.close()
            return elements

    async def scan_current_page(self, page) -> list[dict]:
        """Extract all interactive elements from an already-loaded Playwright page."""
        elements = []
        sno = 1

        # Scan input fields (text, email, tel, number, etc.)
        inputs = await page.query_selector_all(
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']):not([type='submit'])"
        )
        for inp in inputs:
            elem = await self._extract_input(page, inp, sno)
            if elem:
                elements.append(elem)
                sno += 1

        # Scan textareas
        textareas = await page.query_selector_all("textarea")
        for ta in textareas:
            elem = await self._extract_textarea(page, ta, sno)
            if elem:
                elements.append(elem)
                sno += 1

        # Scan select dropdowns
        selects = await page.query_selector_all("select")
        for sel in selects:
            elem = await self._extract_select(page, sel, sno)
            if elem:
                elements.append(elem)
                sno += 1

        # Scan radio button groups (grouped by name)
        radio_groups = await page.evaluate("""
            () => {
                const radios = document.querySelectorAll('input[type="radio"]');
                const groups = {};
                radios.forEach(r => {
                    if (r.name && !groups[r.name]) {
                        groups[r.name] = true;
                    }
                });
                return Object.keys(groups);
            }
        """)
        for group_name in radio_groups:
            elem = await self._extract_radio_group(page, group_name, sno)
            if elem:
                elements.append(elem)
                sno += 1

        # Scan checkboxes
        checkboxes = await page.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            elem = await self._extract_checkbox(page, cb, sno)
            if elem:
                elements.append(elem)
                sno += 1

        # Scan buttons
        buttons = await page.query_selector_all("button, input[type='submit']")
        for btn in buttons:
            elem = await self._extract_button(page, btn, sno)
            if elem:
                elements.append(elem)
                sno += 1

        return elements

    async def _get_label_text(self, page, element) -> str:
        """Find the label text associated with an element."""
        elem_id = await element.get_attribute("id")
        if elem_id:
            label = await page.query_selector(f"label[for='{elem_id}']")
            if label:
                return (await label.inner_text()).strip()

        # Try parent label
        label_text = await element.evaluate("""
            el => {
                const label = el.closest('label');
                if (label) {
                    const clone = label.cloneNode(true);
                    const inputs = clone.querySelectorAll('input, select, textarea');
                    inputs.forEach(i => i.remove());
                    return clone.textContent.trim();
                }

                // Walk up from the element to find a row-like container that has
                // both a text-bearing child and the input (sibling label pattern).
                let current = el.parentElement;
                for (let depth = 0; depth < 5 && current; depth++) {
                    const children = Array.from(current.children);
                    // A good container has 2+ children, at least one with text only
                    if (children.length >= 2) {
                        for (const child of children) {
                            if (child.contains(el)) continue;
                            if (child.querySelector('input, select, textarea')) continue;
                            const text = child.textContent.trim();
                            if (text && text.length < 100) return text;
                        }
                    }
                    current = current.parentElement;
                }
                return '';
            }
        """)
        return label_text

    async def _get_element_name(self, page, element) -> str:
        """Derive a human-readable name for the element."""
        label = await self._get_label_text(page, element)
        if label:
            return label

        aria_label = await element.get_attribute("aria-label") or ""
        if aria_label:
            return aria_label

        placeholder = await element.get_attribute("placeholder") or ""
        if placeholder:
            return placeholder

        name = await element.get_attribute("name") or ""
        if name:
            return name

        elem_id = await element.get_attribute("id") or ""
        return elem_id

    async def _get_common_locators(self, page, element) -> dict:
        """Extract all locator strategies for an element."""
        elem_id = await element.get_attribute("id") or ""
        name = await element.get_attribute("name") or ""
        data_testid = await element.get_attribute("data-testid") or ""

        css_selector = await element.evaluate("""
            el => {
                const path = [];
                let current = el;
                while (current && current !== document.body) {
                    let selector = current.tagName.toLowerCase();
                    if (current.id) {
                        selector = '#' + current.id;
                        path.unshift(selector);
                        break;
                    } else {
                        const parent = current.parentElement;
                        if (parent) {
                            const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                            if (siblings.length > 1) {
                                selector += ':nth-child(' + (Array.from(parent.children).indexOf(current) + 1) + ')';
                            }
                        }
                        path.unshift(selector);
                    }
                    current = current.parentElement;
                }
                return path.join(' > ');
            }
        """)

        xpath = await element.evaluate("""
            el => {
                if (el.id) return '//*[@id="' + el.id + '"]';
                const parts = [];
                let current = el;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    let index = 0;
                    let sibling = current.previousSibling;
                    while (sibling) {
                        if (sibling.nodeType === Node.ELEMENT_NODE && sibling.tagName === current.tagName) {
                            index++;
                        }
                        sibling = sibling.previousSibling;
                    }
                    const tagName = current.tagName.toLowerCase();
                    const part = index > 0 ? tagName + '[' + (index + 1) + ']' : tagName;
                    parts.unshift(part);
                    current = current.parentElement;
                }
                return '/' + parts.join('/');
            }
        """)

        return {
            "locator_id": f"#{elem_id}" if elem_id else "",
            "locator_name": name,
            "locator_css": css_selector,
            "locator_xpath": xpath,
            "locator_data_testid": data_testid,
        }

    async def _get_constraint_attrs(self, page, element) -> dict:
        """Capture HTML5 validation + semantic attributes for a form field."""
        pattern = await element.get_attribute("pattern") or ""
        title_attr = await element.get_attribute("title") or ""
        minlength = await element.get_attribute("minlength") or ""
        maxlength = await element.get_attribute("maxlength") or ""
        min_val = await element.get_attribute("min") or ""
        max_val = await element.get_attribute("max") or ""
        autocomplete = await element.get_attribute("autocomplete") or ""
        inputmode = await element.get_attribute("inputmode") or ""
        required_attr = await element.get_attribute("required")
        required = required_attr is not None

        helper_text = await element.evaluate("""
            el => {
                // Resolve aria-describedby first
                const describedby = el.getAttribute('aria-describedby');
                if (describedby) {
                    const ref = document.getElementById(describedby);
                    if (ref) return ref.textContent.trim();
                }
                // Walk up to the immediate label/group, look for sibling helper text
                const candidates = ['small', '[class*="hint"]', '[class*="help"]'];
                let parent = el.parentElement;
                for (let i = 0; i < 3 && parent; i++) {
                    for (const sel of candidates) {
                        const node = parent.querySelector(sel);
                        if (node && !node.contains(el)) {
                            const text = node.textContent.trim();
                            if (text) return text;
                        }
                    }
                    parent = parent.parentElement;
                }
                // Immediate text node sibling after the input
                let sib = el.nextSibling;
                while (sib) {
                    if (sib.nodeType === Node.TEXT_NODE) {
                        const t = sib.textContent.trim();
                        if (t) return t;
                    }
                    sib = sib.nextSibling;
                }
                return '';
            }
        """)

        return {
            "pattern": pattern,
            "title_attr": title_attr,
            "minlength": minlength,
            "maxlength": maxlength,
            "min": min_val,
            "max": max_val,
            "autocomplete": autocomplete,
            "inputmode": inputmode,
            "required": required,
            "helper_text": helper_text,
        }

    async def _extract_input(self, page, element, sno: int) -> dict | None:
        """Extract data from an input element."""
        input_type = await element.get_attribute("type") or "text"
        element_name = await self._get_element_name(page, element)
        if not element_name:
            return None

        locators = await self._get_common_locators(page, element)
        constraints = await self._get_constraint_attrs(page, element)
        label_text = await self._get_label_text(page, element)
        placeholder = await element.get_attribute("placeholder") or ""
        value = await element.input_value() or ""

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": f"input-{input_type}",
            **locators,
            **constraints,
            "locator_label": label_text,
            "placeholder": placeholder,
            "available_options": "",
            "current_value": value,
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    async def _extract_textarea(self, page, element, sno: int) -> dict | None:
        """Extract data from a textarea element."""
        element_name = await self._get_element_name(page, element)
        if not element_name:
            return None

        locators = await self._get_common_locators(page, element)
        constraints = await self._get_constraint_attrs(page, element)
        label_text = await self._get_label_text(page, element)
        placeholder = await element.get_attribute("placeholder") or ""
        value = await element.input_value() or ""

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": "textarea",
            **locators,
            **constraints,
            "locator_label": label_text,
            "placeholder": placeholder,
            "available_options": "",
            "current_value": value,
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    async def _extract_select(self, page, element, sno: int) -> dict | None:
        """Extract data from a select dropdown."""
        element_name = await self._get_element_name(page, element)
        if not element_name:
            return None

        locators = await self._get_common_locators(page, element)
        constraints = await self._get_constraint_attrs(page, element)
        label_text = await self._get_label_text(page, element)

        options = await element.evaluate("""
            el => Array.from(el.options)
                .filter(o => o.value !== '')
                .map(o => o.text.trim())
        """)
        selected = await element.evaluate("""
            el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''
        """)

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": "select",
            **locators,
            **constraints,
            "locator_label": label_text,
            "placeholder": "",
            "available_options": ", ".join(options),
            "current_value": "" if not selected or selected.lower().startswith("select") else selected,
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    async def _extract_radio_group(self, page, group_name: str, sno: int) -> dict | None:
        """Extract data from a radio button group."""
        radios = await page.query_selector_all(f"input[type='radio'][name='{group_name}']")
        if not radios:
            return None

        first_radio = radios[0]
        group_label = await first_radio.evaluate("""
            el => {
                // Walk up to find nearest parent container with a direct label child
                // (more specific than fieldset legend — prefer the closer label)
                let parent = el.parentElement;
                for (let i = 0; i < 5 && parent; i++) {
                    const label = parent.querySelector(':scope > label');
                    if (label && !label.querySelector('input')) {
                        return label.textContent.trim();
                    }
                    parent = parent.parentElement;
                }
                // Fallback: fieldset > legend (semantic HTML)
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) return legend.textContent.trim();
                }
                // Try aria-label or aria-labelledby on a parent
                let container = el.closest('[aria-label], [aria-labelledby]');
                if (container) {
                    if (container.getAttribute('aria-label')) return container.getAttribute('aria-label');
                    const labelledBy = container.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labelEl = document.getElementById(labelledBy);
                        if (labelEl) return labelEl.textContent.trim();
                    }
                }
                return '';
            }
        """)
        if not group_label:
            group_label = group_name.capitalize()

        options = []
        selected = ""
        for radio in radios:
            label = await self._get_label_text(page, radio)
            value = await radio.get_attribute("value") or label
            options.append(value)
            is_checked = await radio.is_checked()
            if is_checked:
                selected = value

        locators = await self._get_common_locators(page, first_radio)
        constraints = await self._get_constraint_attrs(page, first_radio)

        return {
            "sno": sno,
            "element_name": group_label,
            "element_type": "radio",
            **locators,
            **constraints,
            "locator_label": group_label,
            "placeholder": "",
            "available_options": ", ".join(options),
            "current_value": selected,
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    async def _extract_checkbox(self, page, element, sno: int) -> dict | None:
        """Extract data from a checkbox."""
        element_name = await self._get_label_text(page, element)
        if not element_name:
            element_name = await element.get_attribute("name") or await element.get_attribute("id") or ""
        if not element_name:
            return None

        locators = await self._get_common_locators(page, element)
        constraints = await self._get_constraint_attrs(page, element)
        is_checked = await element.is_checked()

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": "checkbox",
            **locators,
            **constraints,
            "locator_label": element_name,
            "placeholder": "",
            "available_options": "checked, unchecked",
            "current_value": "checked" if is_checked else "unchecked",
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    def scan_with_context(self, url: str) -> dict:
        """Scan the page returning {'elements': [...], 'page_context': {...}}."""
        return _run_async(self._scan_with_context_async(url))

    async def _scan_with_context_async(self, url: str) -> dict:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            elements = await self.scan_current_page(page)
            page_context = await self._extract_page_context(page)
            await browser.close()
            return {"elements": elements, "page_context": page_context}

    async def _extract_page_context(self, page) -> dict:
        title = (await page.title()) or ""
        h1_el = await page.query_selector("h1")
        h1 = (await h1_el.inner_text()).strip() if h1_el else ""
        first_p_el = await page.query_selector("p")
        first_paragraph = (await first_p_el.inner_text()).strip() if first_p_el else ""
        return {"title": title, "h1": h1, "first_paragraph": first_paragraph}

    async def _extract_button(self, page, element, sno: int) -> dict | None:
        """Extract data from a button."""
        tag = await element.evaluate("el => el.tagName")
        text = (await element.inner_text()).strip() if tag == "BUTTON" else ""
        if not text:
            text = await element.get_attribute("value") or ""
        if not text:
            return None

        locators = await self._get_common_locators(page, element)
        constraints = {
            "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
            "min": "", "max": "", "autocomplete": "", "inputmode": "",
            "required": False, "helper_text": "",
        }

        return {
            "sno": sno,
            "element_name": text,
            "element_type": "button",
            **locators,
            **constraints,
            "locator_label": text,
            "placeholder": "",
            "available_options": "",
            "current_value": "",
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }
