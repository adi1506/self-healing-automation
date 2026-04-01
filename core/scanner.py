from playwright.async_api import async_playwright


class Scanner:
    async def scan(self, url: str) -> list[dict]:
        """Scan a web page and extract all interactive elements with multiple locators."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle")

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

            await browser.close()
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

    async def _extract_input(self, page, element, sno: int) -> dict | None:
        """Extract data from an input element."""
        input_type = await element.get_attribute("type") or "text"
        element_name = await self._get_element_name(page, element)
        if not element_name:
            return None

        locators = await self._get_common_locators(page, element)
        label_text = await self._get_label_text(page, element)
        placeholder = await element.get_attribute("placeholder") or ""
        value = await element.input_value() or ""

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": f"input-{input_type}",
            **locators,
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
        label_text = await self._get_label_text(page, element)
        placeholder = await element.get_attribute("placeholder") or ""
        value = await element.input_value() or ""

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": "textarea",
            **locators,
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
            "locator_label": label_text,
            "placeholder": "",
            "available_options": ", ".join(options),
            "current_value": selected if selected != "Select Gender" and selected != "Select Country" else "",
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
                const formGroup = el.closest('.form-group');
                if (formGroup) {
                    const label = formGroup.querySelector(':scope > label');
                    if (label) return label.textContent.trim();
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

        return {
            "sno": sno,
            "element_name": group_label,
            "element_type": "radio",
            **locators,
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
        is_checked = await element.is_checked()

        return {
            "sno": sno,
            "element_name": element_name,
            "element_type": "checkbox",
            **locators,
            "locator_label": element_name,
            "placeholder": "",
            "available_options": "checked, unchecked",
            "current_value": "checked" if is_checked else "unchecked",
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }

    async def _extract_button(self, page, element, sno: int) -> dict | None:
        """Extract data from a button."""
        tag = await element.evaluate("el => el.tagName")
        text = (await element.inner_text()).strip() if tag == "BUTTON" else ""
        if not text:
            text = await element.get_attribute("value") or ""
        if not text:
            return None

        locators = await self._get_common_locators(page, element)

        return {
            "sno": sno,
            "element_name": text,
            "element_type": "button",
            **locators,
            "locator_label": text,
            "placeholder": "",
            "available_options": "",
            "current_value": "",
            "status": "NEW",
            "change_details": "",
            "healed_by": "",
        }
