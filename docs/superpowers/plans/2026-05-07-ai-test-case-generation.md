# Context-Aware AI Test Case Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "AI Generate Test Cases" feature on the Test Data page that auto-populates the test cases grid with happy-path + negative cases, using DOM constraints + a label dictionary as the deterministic core and Ollama+Mistral as enrichment for free-text fields.

**Architecture:** Three layers compose. (1) Scanner is widened to capture HTML constraint attributes (`pattern`, `min`, `max`, `maxlength`, `required`, `autocomplete`, helper text) plus per-page context. (2) A new `TestCaseGenerator` resolves each field through priority layers (explicit DOM constraint → autocomplete registry → label dictionary → LLM enrichment → fallback) and mechanically derives negative cases from the same constraints. (3) UI on the Test Data page exposes one "AI Generate Test Cases" button, a Compact/Thorough toggle, an "AI Context" column for per-row scenarios, and a per-field rule editor on the Field Reference table.

**Tech Stack:** Python, Streamlit, Playwright, openpyxl, Ollama Python client, `exrex` (regex sampler — new dependency), PyYAML.

---

## File Structure

**New files:**
- `core/test_case_generator.py` — heuristic generator, layered resolution, negative derivation
- `core/ai_test_data.py` — Ollama enrichment with validate-and-retry
- `core/field_rules.py` — read/write the per-URL field-rules sidecar YAML
- `data/field_dictionary.yaml` — seeded semantic-field dictionary
- `test_form/v9_constrained.html` — fixture form with `pattern`/`min`/`max`/`required` attributes
- `tests/test_test_case_generator.py`
- `tests/test_ai_test_data.py`
- `tests/test_field_rules.py`

**Modified files:**
- `core/scanner.py` — capture constraint attributes per element + per-page context
- `core/excel_manager.py` — extend element map with constraint columns; extend test data with `AI Context` column; add `Page Context` sheet read/write
- `pages/2_test_data.py` — add AI Context column, AI Generate button + Compact toggle, per-row 🔄 button, per-field rule editor
- `tests/test_scanner.py` — assert new attributes captured
- `tests/test_excel_manager.py` — assert new columns round-trip
- `tests/test_integration.py` — end-to-end heuristic generation against new fixture
- `requirements.txt` — add `exrex`, `PyYAML` (if not already present)
- `README.md` — document the AI Generate Test Cases feature

---

## Task 1: Add `exrex` dependency and a constrained test fixture

**Files:**
- Modify: `requirements.txt`
- Create: `test_form/v9_constrained.html`

- [ ] **Step 1: Add `exrex` to requirements.txt**

Open `requirements.txt`, add a line:
```
exrex>=0.11.0
```
(Leave PyYAML as-is; it's used elsewhere in the project.)

- [ ] **Step 2: Install the new dependency**

Run: `pip install -r requirements.txt`
Expected: `exrex` installs successfully.

- [ ] **Step 3: Verify exrex works in a python REPL**

Run: `python -c "import exrex; print(exrex.getone('[A-Z]{4}[0-9]{4}'))"`
Expected: a string like `FINN0316` (4 uppercase letters + 4 digits, value will vary).

- [ ] **Step 4: Create the constrained fixture HTML**

Create `test_form/v9_constrained.html` with the following content (a small form that exercises every constraint type the generator must handle):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Sample Form — Constrained Registration</title>
</head>
<body>
  <h1>Customer Onboarding</h1>
  <p>Register a new customer. Customer reference must follow the FINN bank format.</p>
  <form id="constrainedForm">
    <div>
      <label for="firstName">First Name</label>
      <input type="text" id="firstName" name="firstName" autocomplete="given-name" required />
    </div>
    <div>
      <label for="email">Email</label>
      <input type="email" id="email" name="email" autocomplete="email" required />
    </div>
    <div>
      <label for="custRef">Customer Reference</label>
      <input type="text" id="custRef" name="custRef" required
             pattern="[A-Z]{4}[0-9]{4}" maxlength="8"
             title="4 uppercase letters followed by 4 digits" />
      <small class="hint">Format: ABCD1234 (e.g. FINN0316)</small>
    </div>
    <div>
      <label for="age">Age</label>
      <input type="number" id="age" name="age" required min="18" max="120" />
    </div>
    <div>
      <label for="bio">Short Bio</label>
      <textarea id="bio" name="bio" maxlength="100"></textarea>
    </div>
    <div>
      <label for="country">Country</label>
      <select id="country" name="country" required>
        <option value="">Select Country</option>
        <option value="India">India</option>
        <option value="USA">USA</option>
        <option value="UK">UK</option>
      </select>
    </div>
    <button type="submit">Register</button>
  </form>
</body>
</html>
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt test_form/v9_constrained.html
git commit -m "test: add constrained-form fixture and exrex dependency for test case generation"
```

---

## Task 2: Seed the field dictionary YAML

**Files:**
- Create: `data/field_dictionary.yaml`

- [ ] **Step 1: Create `data/field_dictionary.yaml`**

Create the file with seed entries covering common semantic field types. Each entry has `match` (substrings to look for in label/name/id/placeholder, case-insensitive), `regex` (pattern the generated value must match), and optional `example` (a known-good string).

```yaml
# Field semantic dictionary for the test case generator (Layer 3).
# Match keys are checked case-insensitively as substrings against the field's
# label, name, id, and placeholder. First match wins. Add new entries freely.

pan:
  match: ["pan number", "pan_no", "pan-no", "pan card"]
  regex: "[A-Z]{5}[0-9]{4}[A-Z]"
  example: "ABCDE1234F"
gstin:
  match: ["gstin", "gst number", "gst_no"]
  regex: "[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
  example: "27ABCDE1234F1Z5"
aadhaar:
  match: ["aadhaar", "aadhar"]
  regex: "[0-9]{12}"
  example: "123412341234"
ssn:
  match: ["ssn", "social security"]
  regex: "[0-9]{3}-[0-9]{2}-[0-9]{4}"
  example: "123-45-6789"
ifsc:
  match: ["ifsc"]
  regex: "[A-Z]{4}0[A-Z0-9]{6}"
  example: "HDFC0001234"
iban:
  match: ["iban"]
  regex: "[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}"
  example: "GB82WEST12345698765432"
zip:
  match: ["zip", "zipcode", "zip code", "postal code"]
  regex: "[0-9]{5}"
  example: "94105"
pincode:
  match: ["pincode", "pin code"]
  regex: "[0-9]{6}"
  example: "560001"
ein:
  match: ["ein", "employer id", "employer_id"]
  regex: "[0-9]{2}-[0-9]{7}"
  example: "12-3456789"
vat:
  match: ["vat number", "vat_no", "vat-id"]
  regex: "[A-Z]{2}[0-9]{8,12}"
  example: "GB123456789"
date_of_birth:
  match: ["date of birth", "dob", "birth date", "birthday"]
  regex: "[0-9]{4}-[0-9]{2}-[0-9]{2}"
  example: "1990-01-15"
phone:
  match: ["phone", "mobile", "cell", "telephone"]
  regex: "[0-9]{10}"
  example: "9876543210"
url:
  match: ["website", "url", "homepage"]
  regex: "https://[a-z]+\\.[a-z]{2,3}"
  example: "https://example.com"
credit_card:
  match: ["card number", "credit card", "cc number"]
  regex: "[0-9]{16}"
  example: "4111111111111111"
cvv:
  match: ["cvv", "cvc", "card code"]
  regex: "[0-9]{3}"
  example: "123"
expiry:
  match: ["expiry", "exp date", "expiration"]
  regex: "[0-9]{2}/[0-9]{2}"
  example: "12/29"
ein_short:
  match: ["fein", "tax id"]
  regex: "[0-9]{9}"
  example: "123456789"
country_code:
  match: ["country code", "iso country"]
  regex: "[A-Z]{2}"
  example: "IN"
currency_code:
  match: ["currency", "currency code"]
  regex: "[A-Z]{3}"
  example: "USD"
license_plate:
  match: ["license plate", "plate number", "vehicle reg"]
  regex: "[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{4}"
  example: "MH12AB1234"
```

- [ ] **Step 2: Commit**

```bash
git add data/field_dictionary.yaml
git commit -m "feat: seed semantic field dictionary for test case generator"
```

---

## Task 3: Per-field rules sidecar (`core/field_rules.py`)

**Files:**
- Create: `core/field_rules.py`
- Test: `tests/test_field_rules.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_field_rules.py`:

```python
import os
import tempfile
import pytest
from core.field_rules import FieldRulesStore


@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestFieldRulesStore:
    def test_returns_empty_dict_when_no_sidecar(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        rules = store.read("https://example.com/form")
        assert rules == {}

    def test_round_trip_save_and_read(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail", "city": "Always Mumbai"})
        rules = store.read(url)
        assert rules == {"email": "Always Gmail", "city": "Always Mumbai"}

    def test_save_overwrites_existing(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail"})
        store.save(url, {"email": "Always Yahoo", "city": "Mumbai"})
        rules = store.read(url)
        assert rules == {"email": "Always Yahoo", "city": "Mumbai"}

    def test_two_urls_have_independent_rules(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        store.save("https://a.com/form", {"email": "rule a"})
        store.save("https://b.com/form", {"email": "rule b"})
        assert store.read("https://a.com/form") == {"email": "rule a"}
        assert store.read("https://b.com/form") == {"email": "rule b"}

    def test_save_empty_dict_removes_sidecar_or_writes_empty(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail"})
        store.save(url, {})
        assert store.read(url) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_field_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.field_rules'`

- [ ] **Step 3: Implement `core/field_rules.py`**

Create `core/field_rules.py`:

```python
from __future__ import annotations

import os
import re
import yaml


class FieldRulesStore:
    """Per-URL sidecar storage for plain-English per-field rules.

    Lives at: <data_dir>/<sanitized_url>.field_rules.yaml
    Schema:
        field_rules:
          email: "Always use Gmail addresses"
          city:  "Always Mumbai"
    """

    def __init__(self, data_dir: str = "data/scans"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _sanitize_url(self, url: str) -> str:
        url_no_fragment = re.sub(r"#.*$", "", url)
        sanitized = re.sub(r"https?://", "", url_no_fragment)
        sanitized = re.sub(r"[^a-zA-Z0-9]", "_", sanitized)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return sanitized

    def _path(self, url: str) -> str:
        return os.path.join(self.data_dir, f"{self._sanitize_url(url)}.field_rules.yaml")

    def read(self, url: str) -> dict[str, str]:
        path = self._path(url)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        rules = data.get("field_rules") or {}
        return {str(k): str(v) for k, v in rules.items() if v}

    def save(self, url: str, rules: dict[str, str]) -> None:
        path = self._path(url)
        cleaned = {str(k): str(v) for k, v in (rules or {}).items() if v}
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"field_rules": cleaned}, f, sort_keys=True, allow_unicode=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_field_rules.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/field_rules.py tests/test_field_rules.py
git commit -m "feat(core): add per-URL field rules sidecar storage"
```

---

## Task 4: Extend `core/scanner.py` to capture per-element constraint attributes

**Files:**
- Modify: `core/scanner.py`
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Add a failing test for new attribute capture**

Append to `tests/test_scanner.py`:

```python
import asyncio
import os
import pytest
from core.scanner import Scanner


@pytest.fixture
def constrained_form_url():
    path = os.path.abspath("test_form/v9_constrained.html")
    return f"file:///{path.replace(os.sep, '/')}"


class TestScannerConstraints:
    def test_captures_pattern_and_maxlength(self, constrained_form_url):
        scanner = Scanner()
        elements = scanner.scan(constrained_form_url)
        cust_ref = next(e for e in elements if e["element_name"] == "Customer Reference")
        assert cust_ref["pattern"] == "[A-Z]{4}[0-9]{4}"
        assert str(cust_ref["maxlength"]) == "8"

    def test_captures_min_max_required(self, constrained_form_url):
        scanner = Scanner()
        elements = scanner.scan(constrained_form_url)
        age = next(e for e in elements if e["element_name"] == "Age")
        assert str(age["min"]) == "18"
        assert str(age["max"]) == "120"
        assert age["required"] is True

    def test_captures_autocomplete(self, constrained_form_url):
        scanner = Scanner()
        elements = scanner.scan(constrained_form_url)
        first_name = next(e for e in elements if e["element_name"] == "First Name")
        assert first_name["autocomplete"] == "given-name"

    def test_captures_helper_text_via_sibling(self, constrained_form_url):
        scanner = Scanner()
        elements = scanner.scan(constrained_form_url)
        cust_ref = next(e for e in elements if e["element_name"] == "Customer Reference")
        assert "ABCD1234" in cust_ref["helper_text"]

    def test_legacy_fields_have_blank_constraints(self, constrained_form_url):
        scanner = Scanner()
        elements = scanner.scan(constrained_form_url)
        bio = next(e for e in elements if e["element_name"] == "Short Bio")
        assert bio["pattern"] == ""
        assert str(bio["maxlength"]) == "100"
        assert bio["required"] is False
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_scanner.py::TestScannerConstraints -v`
Expected: FAIL — current scanner does not return `pattern`, `maxlength`, `min`, `max`, `required`, `autocomplete`, `helper_text` keys.

- [ ] **Step 3: Implement constraint extraction in scanner**

In `core/scanner.py`, add a helper method on `Scanner` that pulls all the new attributes for a given Playwright element handle. Insert below `_get_common_locators`:

```python
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
```

- [ ] **Step 4: Wire `_get_constraint_attrs` into each `_extract_*` method**

In `core/scanner.py`, modify `_extract_input`, `_extract_textarea`, `_extract_select`, `_extract_radio_group`, `_extract_checkbox` so they include the constraint attrs in the returned dict. For each method, add this line after `locators = await self._get_common_locators(page, element)` (or `first_radio` for the radio case):

```python
        constraints = await self._get_constraint_attrs(page, element)  # `first_radio` for radios
```

…and merge `**constraints` into the returned dict (e.g. inside the `return {...}` block, after `**locators,` add `**constraints,`).

For `_extract_button`, set blank defaults so the schema is consistent across all element types:

```python
        constraints = {
            "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
            "min": "", "max": "", "autocomplete": "", "inputmode": "",
            "required": False, "helper_text": "",
        }
```

…and merge into the returned dict the same way.

- [ ] **Step 5: Run all scanner tests to verify**

Run: `pytest tests/test_scanner.py -v`
Expected: all tests PASS, including the new `TestScannerConstraints` class.

- [ ] **Step 6: Commit**

```bash
git add core/scanner.py tests/test_scanner.py
git commit -m "feat(scanner): capture HTML5 validation attributes and helper text per field"
```

---

## Task 5: Extend `core/scanner.py` to capture per-page context

**Files:**
- Modify: `core/scanner.py`
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Add a failing test for page-context capture**

Append to `tests/test_scanner.py`:

```python
class TestScannerPageContext:
    def test_scan_returns_page_context(self, constrained_form_url):
        scanner = Scanner()
        result = scanner.scan_with_context(constrained_form_url)
        ctx = result["page_context"]
        assert "Constrained Registration" in ctx["title"]
        assert ctx["h1"] == "Customer Onboarding"
        assert "Register a new customer" in ctx["first_paragraph"]

    def test_scan_with_context_returns_elements_too(self, constrained_form_url):
        scanner = Scanner()
        result = scanner.scan_with_context(constrained_form_url)
        assert isinstance(result["elements"], list)
        assert len(result["elements"]) > 0
        assert result["elements"][0]["element_name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scanner.py::TestScannerPageContext -v`
Expected: FAIL — `Scanner` has no `scan_with_context` method.

- [ ] **Step 3: Implement `scan_with_context`**

In `core/scanner.py`, add a new method to `Scanner`:

```python
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
```

- [ ] **Step 4: Run all scanner tests to verify**

Run: `pytest tests/test_scanner.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/scanner.py tests/test_scanner.py
git commit -m "feat(scanner): capture per-page context (title, h1, first paragraph)"
```

---

## Task 6: Extend `core/excel_manager.py` for new element-map columns

**Files:**
- Modify: `core/excel_manager.py`
- Test: `tests/test_excel_manager.py`

This task adds 9 new columns to the Element Map sheet (`Pattern`, `Title`, `Min Length`, `Max Length`, `Min Value`, `Max Value`, `Required`, `Autocomplete`, `Helper Text`) appended after the existing `Last Scanned` column. Old Excel files keep working — the extra columns will read as `""` for legacy data.

- [ ] **Step 1: Add a failing test for round-tripping new columns**

Append to `tests/test_excel_manager.py`:

```python
def test_element_map_round_trips_constraint_columns(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    url = "http://example.com/form"
    elements = [
        {
            "sno": 1, "element_name": "Customer Reference", "element_type": "input-text",
            "locator_id": "#custRef", "locator_name": "custRef", "locator_css": "input#custRef",
            "locator_xpath": "//*[@id='custRef']", "locator_data_testid": "",
            "locator_label": "Customer Reference", "placeholder": "",
            "available_options": "", "current_value": "",
            "status": "NEW", "change_details": "", "healed_by": "",
            "pattern": "[A-Z]{4}[0-9]{4}", "title_attr": "4 letters + 4 digits",
            "minlength": "", "maxlength": "8",
            "min": "", "max": "", "autocomplete": "",
            "inputmode": "", "required": True,
            "helper_text": "Format: ABCD1234",
        },
    ]
    em.save_element_map(url, elements)
    read_back = em.read_element_map(url)
    assert read_back[0]["pattern"] == "[A-Z]{4}[0-9]{4}"
    assert read_back[0]["maxlength"] in (8, "8")
    assert read_back[0]["required"] is True
    assert read_back[0]["helper_text"] == "Format: ABCD1234"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_excel_manager.py::test_element_map_round_trips_constraint_columns -v`
Expected: FAIL — read returns blank values because the new columns aren't part of `ELEMENT_MAP_KEYS`.

- [ ] **Step 3: Update headers and keys in `core/excel_manager.py`**

Replace the `ELEMENT_MAP_HEADERS` and `ELEMENT_MAP_KEYS` constants. Note: `Last Scanned` stays at column 16 to keep old files readable; new columns are appended at 17–25.

```python
ELEMENT_MAP_HEADERS = [
    "S.No", "Element Name", "Element Type",
    "Locator ID", "Locator Name", "Locator CSS", "Locator XPath",
    "Locator Data-TestID", "Locator Label",
    "Placeholder", "Available Options", "Current Value",
    "Status", "Change Details", "Healed By", "Last Scanned",
    "Pattern", "Title", "Min Length", "Max Length",
    "Min Value", "Max Value", "Required", "Autocomplete", "Helper Text",
]

LAST_SCANNED_COL = 16

# Position-aligned keys for Element Map; None marks the Last Scanned column
# which is written separately as a timestamp.
EXTENDED_ELEMENT_MAP_KEYS = [
    "sno", "element_name", "element_type",
    "locator_id", "locator_name", "locator_css", "locator_xpath",
    "locator_data_testid", "locator_label",
    "placeholder", "available_options", "current_value",
    "status", "change_details", "healed_by",
    None,  # column 16 = "Last Scanned"
    "pattern", "title_attr", "minlength", "maxlength",
    "min", "max", "required", "autocomplete", "helper_text",
]

# Kept for any callers that iterate the original 15 keys
ELEMENT_MAP_KEYS = [k for k in EXTENDED_ELEMENT_MAP_KEYS[:15]]
```

- [ ] **Step 4: Update `save_element_map` to write new columns**

In `save_element_map`, replace the row-write loop:

```python
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row_idx, elem in enumerate(elements, 2):
            for col_idx, key in enumerate(EXTENDED_ELEMENT_MAP_KEYS, 1):
                if key is None:
                    continue
                ws.cell(row=row_idx, column=col_idx, value=elem.get(key, ""))
            ws.cell(row=row_idx, column=LAST_SCANNED_COL, value=timestamp)

            status = elem.get("status", "")
            if status in STATUS_FILLS:
                fill = STATUS_FILLS[status]
                for col_idx in range(1, len(ELEMENT_MAP_HEADERS) + 1):
                    ws.cell(row=row_idx, column=col_idx).fill = fill
```

- [ ] **Step 5: Update `read_element_map` to read new columns**

Replace the read loop:

```python
        for row in range(2, ws.max_row + 1):
            if ws.cell(row=row, column=1).value is None:
                break
            elem = {}
            for col_idx, key in enumerate(EXTENDED_ELEMENT_MAP_KEYS, 1):
                if key is None:
                    continue
                raw = ws.cell(row=row, column=col_idx).value
                elem[key] = raw if raw is not None else ""
            # Last Scanned at column 16
            raw_ts = ws.cell(row=row, column=LAST_SCANNED_COL).value
            elem["last_scanned"] = raw_ts if raw_ts is not None else ""
            # Coerce booleans for `required` (it round-trips through Excel as True/False or "")
            req = elem.get("required", "")
            elem["required"] = bool(req) if req != "" else False
            elements.append(elem)
        return elements
```

- [ ] **Step 6: Run the new test to verify it passes**

Run: `pytest tests/test_excel_manager.py::test_element_map_round_trips_constraint_columns -v`
Expected: PASS.

- [ ] **Step 7: Run all excel_manager tests to verify nothing regressed**

Run: `pytest tests/test_excel_manager.py -v`
Expected: all tests PASS (including pre-existing tests over the original column set).

- [ ] **Step 8: Commit**

```bash
git add core/excel_manager.py tests/test_excel_manager.py
git commit -m "feat(excel): persist HTML constraint attributes in Element Map sheet"
```

---

## Task 7: Add "AI Context" column to the Test Data sheet

**Files:**
- Modify: `core/excel_manager.py`
- Test: `tests/test_excel_manager.py`

The `Test Data` sheet currently has columns `S.No | Test Case Name | <field_1> | <field_2> | ...`. We're inserting an `AI Context` column at position 3, before the field columns.

- [ ] **Step 1: Add a failing test**

Append to `tests/test_excel_manager.py`:

```python
def test_test_data_sheet_includes_ai_context_column(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    url = "http://example.com/form"
    elements = [
        {"sno": 1, "element_name": "Email", "element_type": "input-email",
         "locator_id": "", "locator_name": "email", "locator_css": "", "locator_xpath": "",
         "locator_data_testid": "", "locator_label": "Email",
         "placeholder": "", "available_options": "", "current_value": "",
         "status": "NEW", "change_details": "", "healed_by": "",
         "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
         "min": "", "max": "", "autocomplete": "email", "inputmode": "",
         "required": False, "helper_text": ""},
    ]
    em.save_element_map(url, elements)

    em.save_test_data(url, [
        {"sno": 1, "test_case_name": "Happy path", "ai_context": "Senior citizen", "Email": "a@b.com"},
    ])
    rows = em.read_test_data(url)
    assert rows[0]["AI Context"] == "Senior citizen"
    assert rows[0]["Test Case Name"] == "Happy path"
    assert rows[0]["Email"] == "a@b.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_excel_manager.py::test_test_data_sheet_includes_ai_context_column -v`
Expected: FAIL — the Test Data sheet has no AI Context column.

- [ ] **Step 3: Update sheet creation in `save_element_map`**

In `core/excel_manager.py`, find this block in `save_element_map`:

```python
        if "Test Data" not in wb.sheetnames:
            ws_td = wb.create_sheet("Test Data")
            ws_td.cell(row=1, column=1, value="S.No")
            ws_td.cell(row=1, column=2, value="Test Case Name")
            col = 3
            for elem in elements:
                if elem.get("element_type") not in NON_EDITABLE_TYPES:
                    ws_td.cell(row=1, column=col, value=elem["element_name"])
                    col += 1
```

Replace with:

```python
        if "Test Data" not in wb.sheetnames:
            ws_td = wb.create_sheet("Test Data")
            ws_td.cell(row=1, column=1, value="S.No")
            ws_td.cell(row=1, column=2, value="Test Case Name")
            ws_td.cell(row=1, column=3, value="AI Context")
            col = 4
            for elem in elements:
                if elem.get("element_type") not in NON_EDITABLE_TYPES:
                    ws_td.cell(row=1, column=col, value=elem["element_name"])
                    col += 1
        else:
            ws_td = wb["Test Data"]
            # Backfill AI Context column for older sheets that don't have it
            if ws_td.cell(row=1, column=3).value != "AI Context":
                ws_td.insert_cols(3)
                ws_td.cell(row=1, column=3, value="AI Context")
```

Also update the `else:` branch that already exists below for adding new field columns — those `next_col` calculations keep working because `ws_td.max_column` accounts for the new column.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `pytest tests/test_excel_manager.py::test_test_data_sheet_includes_ai_context_column -v`
Expected: PASS.

- [ ] **Step 5: Run all excel_manager tests**

Run: `pytest tests/test_excel_manager.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/excel_manager.py tests/test_excel_manager.py
git commit -m "feat(excel): add AI Context column to Test Data sheet"
```

---

## Task 8: Heuristic generator — Layer 1 (DOM constraints) + dispatch skeleton

**Files:**
- Create: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

- [ ] **Step 1: Write failing tests for Layer 1**

Create `tests/test_test_case_generator.py`:

```python
import re
import pytest
from core.test_case_generator import TestCaseGenerator


@pytest.fixture
def gen():
    return TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml")


def _field(**overrides):
    base = {
        "element_name": "Field", "element_type": "input-text",
        "locator_label": "", "placeholder": "", "locator_name": "",
        "locator_id": "", "available_options": "",
        "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
        "min": "", "max": "", "autocomplete": "", "inputmode": "",
        "required": False, "helper_text": "",
    }
    base.update(overrides)
    return base


class TestL1DOMConstraints:
    def test_pattern_generates_matching_value(self, gen):
        f = _field(pattern="[A-Z]{4}[0-9]{4}")
        value = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", value)

    def test_email_type_generates_email(self, gen):
        f = _field(element_type="input-email")
        value = gen.generate_value(f)
        assert "@" in value and "." in value.split("@")[1]

    def test_number_type_within_min_max(self, gen):
        f = _field(element_type="input-number", min="18", max="120")
        value = gen.generate_value(f)
        assert 18 <= int(value) <= 120

    def test_number_type_no_bounds_returns_simple_number(self, gen):
        f = _field(element_type="input-number")
        value = gen.generate_value(f)
        assert int(value) >= 0

    def test_select_returns_first_non_empty_option(self, gen):
        f = _field(element_type="select", available_options="India, USA, UK")
        value = gen.generate_value(f)
        assert value == "India"

    def test_radio_returns_first_option(self, gen):
        f = _field(element_type="radio", available_options="Yes, No")
        value = gen.generate_value(f)
        assert value == "Yes"

    def test_checkbox_returns_checked(self, gen):
        f = _field(element_type="checkbox", available_options="checked, unchecked")
        value = gen.generate_value(f)
        assert value == "checked"

    def test_maxlength_respected_on_fallback(self, gen):
        f = _field(element_type="input-text", maxlength="5")
        value = gen.generate_value(f)
        assert len(value) <= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestL1DOMConstraints -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `core/test_case_generator.py` with Layer 1**

Create `core/test_case_generator.py`:

```python
from __future__ import annotations

import os
import re
import yaml
import exrex


class TestCaseGenerator:
    """Heuristic + (later) AI-enriched generator of test case values for form fields.

    Layered resolution per field, first hit wins:
      L1 — explicit DOM constraints (pattern, type, min/max, options)
      L2 — autocomplete token registry
      L3 — semantic label/name dictionary
      L4 — LLM enrichment (added in a later task)
      Fallback — generic typed string honoring maxlength
    """

    def __init__(self, field_dictionary_path: str = "data/field_dictionary.yaml",
                 ai_client=None):
        self.ai_client = ai_client
        self._dictionary = self._load_dictionary(field_dictionary_path)

    def _load_dictionary(self, path: str) -> dict:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # --------------------------------------------------------------------- L1
    def generate_value(self, field: dict) -> str:
        """Return one valid value for the given field via the layered resolver."""
        v = self._l1_dom_constraint(field)
        if v is not None:
            return v
        # Layers 2, 3, 4 added in subsequent tasks
        return self._fallback(field)

    def _l1_dom_constraint(self, field: dict) -> str | None:
        etype = (field.get("element_type") or "").lower()
        pattern = field.get("pattern") or ""
        if pattern:
            try:
                return exrex.getone(pattern)
            except Exception:
                pass

        if etype == "select" or etype == "radio":
            opts = self._parse_options(field.get("available_options", ""))
            return opts[0] if opts else ""

        if etype == "checkbox":
            return "checked"

        if etype == "input-email":
            return "test.user@example.com"

        if etype == "input-number" or etype == "input-range":
            lo = self._to_number(field.get("min"))
            hi = self._to_number(field.get("max"))
            if lo is not None and hi is not None:
                return str((lo + hi) // 2 if isinstance(lo, int) and isinstance(hi, int) else (lo + hi) / 2)
            if lo is not None:
                return str(lo)
            if hi is not None:
                return str(hi)
            return "42"

        if etype == "input-tel":
            return "9876543210"

        if etype == "input-date":
            return "2000-01-15"

        if etype == "input-url":
            return "https://example.com"

        return None

    # ---------------------------------------------------------------- helpers
    def _parse_options(self, raw: str) -> list[str]:
        return [o.strip() for o in (raw or "").split(",") if o.strip()]

    def _to_number(self, val):
        if val in (None, ""):
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

    def _fallback(self, field: dict) -> str:
        maxlen = self._to_number(field.get("maxlength"))
        base = "Test 1234"
        if maxlen and maxlen < len(base):
            return base[:maxlen]
        return base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_case_generator.py::TestL1DOMConstraints -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): heuristic Layer 1 — DOM constraint based value generation"
```

---

## Task 9: Heuristic generator — Layer 2 (autocomplete registry)

**Files:**
- Modify: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

- [ ] **Step 1: Add failing tests for Layer 2**

Append to `tests/test_test_case_generator.py`:

```python
class TestL2Autocomplete:
    def test_autocomplete_email(self, gen):
        f = _field(autocomplete="email")
        assert "@" in gen.generate_value(f)

    def test_autocomplete_given_name(self, gen):
        f = _field(autocomplete="given-name")
        v = gen.generate_value(f)
        assert v == "John"

    def test_autocomplete_family_name(self, gen):
        f = _field(autocomplete="family-name")
        assert gen.generate_value(_field(autocomplete="family-name")) == "Doe"

    def test_autocomplete_postal_code(self, gen):
        assert gen.generate_value(_field(autocomplete="postal-code")) == "94105"

    def test_autocomplete_cc_number(self, gen):
        assert gen.generate_value(_field(autocomplete="cc-number")) == "4111111111111111"

    def test_autocomplete_unknown_token_falls_through(self, gen):
        # Should fall through to fallback, not crash
        v = gen.generate_value(_field(autocomplete="bogus-token"))
        assert v == "Test 1234"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestL2Autocomplete -v`
Expected: FAIL — autocomplete is currently ignored.

- [ ] **Step 3: Implement Layer 2**

In `core/test_case_generator.py`, add the autocomplete registry as a class constant and a new method, then wire it into `generate_value`.

Add at module level (above the class):

```python
AUTOCOMPLETE_REGISTRY = {
    "email": "test.user@example.com",
    "tel": "9876543210",
    "tel-national": "9876543210",
    "given-name": "John",
    "family-name": "Doe",
    "name": "John Doe",
    "username": "testuser",
    "new-password": "Passw0rd!",
    "current-password": "Passw0rd!",
    "organization": "Acme Inc",
    "street-address": "123 Main St",
    "address-line1": "123 Main St",
    "address-line2": "Apt 4B",
    "address-level2": "Springfield",
    "address-level1": "CA",
    "postal-code": "94105",
    "country": "US",
    "country-name": "United States",
    "bday": "1990-01-15",
    "url": "https://example.com",
    "cc-name": "John Doe",
    "cc-number": "4111111111111111",
    "cc-exp": "12/29",
    "cc-csc": "123",
    "cc-type": "Visa",
}
```

Add a new method on `TestCaseGenerator`:

```python
    def _l2_autocomplete(self, field: dict) -> str | None:
        token = (field.get("autocomplete") or "").strip().lower()
        if not token:
            return None
        return AUTOCOMPLETE_REGISTRY.get(token)
```

Update `generate_value` to call L2 between L1 and fallback:

```python
    def generate_value(self, field: dict) -> str:
        v = self._l1_dom_constraint(field)
        if v is not None:
            return v
        v = self._l2_autocomplete(field)
        if v is not None:
            return v
        # Layers 3, 4 added in subsequent tasks
        return self._fallback(field)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_case_generator.py -v`
Expected: all tests PASS (Layer 1 + Layer 2).

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): heuristic Layer 2 — autocomplete-token registry"
```

---

## Task 10: Heuristic generator — Layer 3 (label/name dictionary)

**Files:**
- Modify: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

- [ ] **Step 1: Add failing tests for Layer 3**

Append to `tests/test_test_case_generator.py`:

```python
class TestL3Dictionary:
    def test_label_pan_number_matches_dictionary(self, gen):
        f = _field(element_name="PAN Number", locator_label="PAN Number")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v)

    def test_label_aadhaar_matches(self, gen):
        f = _field(element_name="Aadhaar", locator_label="Aadhaar")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[0-9]{12}", v)

    def test_name_attr_matches_when_label_doesnt(self, gen):
        f = _field(element_name="Cust Code", locator_label="", locator_name="ifsc_code")
        v = gen.generate_value(f)
        assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v)

    def test_no_dictionary_match_falls_through_to_fallback(self, gen):
        f = _field(element_name="Xyz Garbage Field", locator_label="Xyz Garbage Field")
        v = gen.generate_value(f)
        assert v == "Test 1234"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestL3Dictionary -v`
Expected: FAIL — dictionary lookup not yet wired.

- [ ] **Step 3: Implement Layer 3**

In `core/test_case_generator.py`, add a new method on `TestCaseGenerator`:

```python
    def _l3_dictionary(self, field: dict) -> str | None:
        haystack_parts = [
            field.get("element_name", ""),
            field.get("locator_label", ""),
            field.get("locator_name", ""),
            field.get("locator_id", ""),
            field.get("placeholder", ""),
        ]
        haystack = " ".join(p for p in haystack_parts if p).lower()
        if not haystack:
            return None
        for entry in self._dictionary.values():
            for needle in entry.get("match", []):
                if needle.lower() in haystack:
                    regex = entry.get("regex")
                    if regex:
                        try:
                            return exrex.getone(regex)
                        except Exception:
                            pass
                    return entry.get("example", "")
        return None
```

Update `generate_value` to call L3 between L2 and fallback:

```python
    def generate_value(self, field: dict) -> str:
        v = self._l1_dom_constraint(field)
        if v is not None:
            return v
        v = self._l2_autocomplete(field)
        if v is not None:
            return v
        v = self._l3_dictionary(field)
        if v is not None:
            return v
        # Layer 4 (LLM) added in a subsequent task
        return self._fallback(field)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_case_generator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): heuristic Layer 3 — semantic label/name dictionary lookup"
```

---

## Task 11: Negative case derivation — Compact (default) and Thorough modes

**Files:**
- Modify: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

- [ ] **Step 1: Add failing tests for negative derivation**

Append to `tests/test_test_case_generator.py`:

```python
class TestNegativeDerivation:
    def test_compact_yields_one_per_field(self, gen):
        fields = [
            _field(element_name="Email", element_type="input-email", required=True),
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
            _field(element_name="Age", element_type="input-number",
                   min="18", max="120", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="compact")
        # One row per field — three rows
        names = [n["field"] for n in negs]
        assert sorted(names) == ["Age", "Customer Reference", "Email"]

    def test_compact_chooses_pattern_over_required(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="compact")
        assert len(negs) == 1
        assert negs[0]["violation"] == "pattern"
        # Value should NOT match the pattern
        assert not re.fullmatch(r"[A-Z]{4}[0-9]{4}", negs[0]["value"])

    def test_thorough_yields_one_per_constraint(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        negs = gen.derive_negatives(fields, mode="thorough")
        violations = sorted(n["violation"] for n in negs)
        assert violations == ["maxlength", "pattern", "required"]

    def test_required_only_field_compact_yields_required_violation(self, gen):
        fields = [_field(element_name="City", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        assert len(negs) == 1
        assert negs[0]["violation"] == "required"
        assert negs[0]["value"] == ""

    def test_email_type_negative_has_no_at_sign(self, gen):
        fields = [_field(element_name="Email", element_type="input-email", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        chosen = negs[0]
        assert "@" not in chosen["value"]

    def test_min_max_violation_picks_below_min(self, gen):
        fields = [_field(element_name="Age", element_type="input-number",
                         min="18", max="120", required=True)]
        negs = gen.derive_negatives(fields, mode="compact")
        chosen = negs[0]
        assert chosen["violation"] in ("min", "max")
        # Either way, the value violates the range
        assert int(chosen["value"]) < 18 or int(chosen["value"]) > 120
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestNegativeDerivation -v`
Expected: FAIL — `derive_negatives` not implemented.

- [ ] **Step 3: Implement negative derivation**

In `core/test_case_generator.py`, add the following methods to `TestCaseGenerator`:

```python
    # Priority order for Compact mode — most distinctive first
    _COMPACT_PRIORITY = ["pattern", "min", "max", "maxlength", "minlength", "type_email", "type_number", "required"]

    def derive_negatives(self, fields: list[dict], mode: str = "compact") -> list[dict]:
        """Return negative test descriptors. Each item:
            {field, violation, value}
        Mode 'compact' yields one row per field; 'thorough' yields one per violatable constraint.
        """
        results = []
        for f in fields:
            if (f.get("element_type") or "").lower() in ("button",):
                continue
            negatives = self._negatives_for_field(f)
            if not negatives:
                continue
            if mode == "compact":
                chosen = self._pick_compact(negatives)
                if chosen:
                    results.append(chosen)
            else:
                results.extend(negatives)
        return results

    def _negatives_for_field(self, field: dict) -> list[dict]:
        name = field.get("element_name", "")
        etype = (field.get("element_type") or "").lower()
        out = []

        if field.get("pattern"):
            base = self.generate_value(field)  # a valid value
            mutated = base.lower() if base != base.lower() else base[:-1] if len(base) > 1 else "x"
            out.append({"field": name, "violation": "pattern", "value": mutated})

        lo = self._to_number(field.get("min"))
        hi = self._to_number(field.get("max"))
        if lo is not None:
            out.append({"field": name, "violation": "min", "value": str(lo - 1)})
        if hi is not None:
            out.append({"field": name, "violation": "max", "value": str(hi + 1)})

        maxlen = self._to_number(field.get("maxlength"))
        if maxlen and maxlen > 0:
            out.append({"field": name, "violation": "maxlength", "value": "x" * (int(maxlen) + 1)})

        minlen = self._to_number(field.get("minlength"))
        if minlen and minlen > 1:
            out.append({"field": name, "violation": "minlength", "value": "x" * (int(minlen) - 1)})

        if etype == "input-email":
            out.append({"field": name, "violation": "type_email", "value": "notanemail"})
        if etype == "input-number":
            out.append({"field": name, "violation": "type_number", "value": "abc"})

        if field.get("required"):
            out.append({"field": name, "violation": "required", "value": ""})

        return out

    def _pick_compact(self, negatives: list[dict]) -> dict | None:
        by_violation = {n["violation"]: n for n in negatives}
        for v in self._COMPACT_PRIORITY:
            if v in by_violation:
                return by_violation[v]
        return negatives[0] if negatives else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_case_generator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): mechanical negative case derivation, Compact + Thorough modes"
```

---

## Task 12: Top-level `generate()` orchestrator (heuristic only, no LLM yet)

**Files:**
- Modify: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

This task adds the high-level `generate()` method that produces a list of full test case rows (happy path + negatives), one cell per field, with all other fields filled valid for negative rows.

- [ ] **Step 1: Add failing test**

Append to `tests/test_test_case_generator.py`:

```python
class TestGenerateOrchestrator:
    def test_generate_produces_happy_path_plus_one_negative_per_field_compact(self, gen):
        fields = [
            _field(element_name="Email", element_type="input-email", required=True),
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        rows = gen.generate(fields, page_context={}, mode="compact")
        # 1 happy path + 2 negatives
        assert len(rows) == 3
        happy = rows[0]
        assert happy["test_case_name"] == "Happy path"
        assert "@" in happy["values"]["Email"]
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", happy["values"]["Customer Reference"])

        # Each negative row has the same fields populated, only the targeted field is invalid
        neg_email = next(r for r in rows if "Email" in r["test_case_name"])
        assert "@" not in neg_email["values"]["Email"]
        assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", neg_email["values"]["Customer Reference"])

    def test_generate_produces_ai_context_column(self, gen):
        fields = [_field(element_name="Email", element_type="input-email", required=True)]
        rows = gen.generate(fields, page_context={}, mode="compact")
        for r in rows:
            assert "ai_context" in r
            assert r["ai_context"] == ""

    def test_generate_thorough_mode(self, gen):
        fields = [
            _field(element_name="Customer Reference", pattern="[A-Z]{4}[0-9]{4}",
                   maxlength="8", required=True),
        ]
        rows = gen.generate(fields, page_context={}, mode="thorough")
        # Happy + (pattern, maxlength, required) = 4
        assert len(rows) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestGenerateOrchestrator -v`
Expected: FAIL — `generate()` not implemented.

- [ ] **Step 3: Implement `generate()`**

In `core/test_case_generator.py`, add to `TestCaseGenerator`:

```python
    def generate(
        self,
        fields: list[dict],
        page_context: dict | None = None,
        mode: str = "compact",
        per_field_rules: dict[str, str] | None = None,
        ai_contexts_by_row: dict[int, str] | None = None,
    ) -> list[dict]:
        """Produce a list of test case rows.

        Each row is {test_case_name, ai_context, values: {field_name: str}}.
        Row 0 is the happy path. Rows 1..N are negatives derived per `mode`.
        Per-field rules and per-row AI contexts are accepted now and used by
        the AI enrichment task; for the heuristic-only path they're ignored.
        """
        editable = [f for f in fields if (f.get("element_type") or "").lower() not in ("button",)]
        valid_values = {f["element_name"]: self.generate_value(f) for f in editable}

        rows = [{
            "test_case_name": "Happy path",
            "ai_context": "",
            "values": dict(valid_values),
        }]

        for neg in self.derive_negatives(editable, mode=mode):
            row_values = dict(valid_values)
            row_values[neg["field"]] = neg["value"]
            rows.append({
                "test_case_name": f"{neg['field']}: {self._violation_label(neg['violation'])}",
                "ai_context": "",
                "values": row_values,
            })
        return rows

    def _violation_label(self, violation: str) -> str:
        return {
            "pattern": "invalid format",
            "min": "below min",
            "max": "above max",
            "maxlength": "too long",
            "minlength": "too short",
            "type_email": "not an email",
            "type_number": "not a number",
            "required": "missing required",
        }.get(violation, violation)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_case_generator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): top-level generate() orchestrator producing happy + negative rows"
```

---

## Task 13: AI enrichment module — `core/ai_test_data.py`

**Files:**
- Create: `core/ai_test_data.py`
- Test: `tests/test_ai_test_data.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ai_test_data.py`:

```python
import os
from unittest.mock import MagicMock, patch
import pytest
from core.ai_test_data import AITestData


@pytest.fixture
def ai():
    a = AITestData(host="http://localhost:11434", model="mistral")
    a._available = True
    return a


def _field(**overrides):
    base = {
        "element_name": "Email", "element_type": "input-email",
        "locator_label": "Email", "placeholder": "",
        "pattern": "", "title_attr": "", "minlength": "", "maxlength": "",
        "min": "", "max": "", "autocomplete": "", "inputmode": "",
        "required": False, "helper_text": "",
    }
    base.update(overrides)
    return base


class TestAITestData:
    def test_returns_value_from_valid_json(self, ai):
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.return_value = {"response": '{"value": "alice@gmail.com"}'}
            v = ai.generate_value(
                field=_field(),
                page_context={"title": "Reg", "h1": "Sign up", "first_paragraph": ""},
                per_field_rule="Use Gmail addresses",
                ai_context="Senior citizen",
            )
            assert v == "alice@gmail.com"

    def test_retries_on_invalid_json_then_returns_value(self, ai):
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.side_effect = [
                {"response": "not json at all"},
                {"response": '{"value": "bob@gmail.com"}'},
            ]
            v = ai.generate_value(field=_field(), page_context={},
                                  per_field_rule="", ai_context="")
            assert v == "bob@gmail.com"

    def test_returns_none_when_value_violates_constraints_twice(self, ai):
        # Field has a strict pattern; LLM returns non-matching strings both times
        f = _field(element_type="input-text", pattern="[A-Z]{4}[0-9]{4}")
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.side_effect = [
                {"response": '{"value": "lowercase"}'},
                {"response": '{"value": "stillbad"}'},
            ]
            v = ai.generate_value(field=f, page_context={}, per_field_rule="", ai_context="")
            assert v is None

    def test_returns_none_when_unavailable(self):
        a = AITestData(host="http://localhost:11434", model="mistral")
        a._available = False
        v = a.generate_value(field=_field(), page_context={}, per_field_rule="", ai_context="")
        assert v is None

    def test_respects_pattern_validates_correct_value(self, ai):
        f = _field(element_type="input-text", pattern="[A-Z]{4}[0-9]{4}")
        with patch.object(ai.client, "generate") as mock_gen:
            mock_gen.return_value = {"response": '{"value": "FINN0316"}'}
            v = ai.generate_value(field=f, page_context={}, per_field_rule="", ai_context="")
            assert v == "FINN0316"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ai_test_data.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `core/ai_test_data.py`**

Create `core/ai_test_data.py`:

```python
from __future__ import annotations

import json
import os
import re

try:
    import ollama
except ImportError:
    ollama = None


class AITestData:
    """Per-cell LLM enrichment for the test case generator.

    Calls Ollama (Mistral by default) one field at a time with `format=json`,
    validates the returned value against the field's DOM constraints, retries
    once on failure, and returns None if it can't produce a valid value.
    """

    def __init__(self, host: str = "", model: str = ""):
        self.host = host or os.environ.get("OLLAMA_HOST", "")
        self.model = model or os.environ.get("OLLAMA_MODEL", "mistral")
        if ollama is not None:
            self.client = ollama.Client(host=self.host) if self.host else ollama.Client()
        else:
            self.client = None
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        if self.client is None:
            self._available = False
            return False
        try:
            self.client.list()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def generate_value(
        self,
        field: dict,
        page_context: dict,
        per_field_rule: str = "",
        ai_context: str = "",
    ) -> str | None:
        if not self.is_available():
            return None

        prompt = self._build_prompt(field, page_context, per_field_rule, ai_context)
        value, violation = self._call_and_validate(prompt, field)
        if value is not None:
            return value
        # Retry once with feedback
        retry_prompt = (
            prompt
            + f"\n\nYour previous answer violated: {violation}. Try again. "
              f"Return strict JSON only."
        )
        value, _ = self._call_and_validate(retry_prompt, field)
        return value

    def _call_and_validate(self, prompt: str, field: dict) -> tuple[str | None, str]:
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={"temperature": 0.0},
            )
        except Exception:
            return None, "ollama call failed"
        try:
            payload = json.loads(response.get("response", ""))
        except (ValueError, TypeError):
            return None, "invalid JSON"
        value = payload.get("value")
        if not isinstance(value, str):
            return None, "value not a string"
        violation = self._validate_against_constraints(value, field)
        if violation:
            return None, violation
        return value, ""

    def _validate_against_constraints(self, value: str, field: dict) -> str:
        pattern = field.get("pattern") or ""
        if pattern and not re.fullmatch(pattern, value):
            return f"pattern {pattern}"
        maxlen = field.get("maxlength")
        if maxlen and isinstance(maxlen, (int, str)) and str(maxlen).isdigit():
            if len(value) > int(maxlen):
                return f"maxlength {maxlen}"
        minlen = field.get("minlength")
        if minlen and isinstance(minlen, (int, str)) and str(minlen).isdigit():
            if len(value) < int(minlen):
                return f"minlength {minlen}"
        etype = (field.get("element_type") or "").lower()
        if etype == "input-email" and "@" not in value:
            return "type_email"
        if etype == "input-number":
            try:
                n = float(value)
            except ValueError:
                return "type_number"
            for bound, op in [("min", lambda v, b: v < b), ("max", lambda v, b: v > b)]:
                b = field.get(bound)
                if b not in ("", None):
                    try:
                        if op(n, float(b)):
                            return f"{bound} {b}"
                    except (TypeError, ValueError):
                        pass
        return ""

    def _build_prompt(
        self, field: dict, page_context: dict, per_field_rule: str, ai_context: str
    ) -> str:
        constraints = self._summarize_constraints(field)
        ctx_line = ". ".join(
            v for v in (page_context.get("title", ""),
                        page_context.get("h1", ""),
                        page_context.get("first_paragraph", "")) if v
        ) or "none"
        return (
            "You are generating one value for a single form field.\n"
            f"Page context: {ctx_line}\n"
            f"Field label: {field.get('locator_label') or field.get('element_name', '')}\n"
            f"Field name: {field.get('locator_name', '')}\n"
            f"Field type: {field.get('element_type', '')}\n"
            f"Helper text: {field.get('helper_text') or 'none'}\n"
            f"DOM constraints: {constraints or 'none'}\n"
            f"Per-field rule: {per_field_rule or 'none'}\n"
            f"Test case scenario: {ai_context or 'default valid value'}\n"
            "Return strict JSON only: {\"value\": \"<generated value>\"}"
        )

    def _summarize_constraints(self, field: dict) -> str:
        parts = []
        if field.get("pattern"): parts.append(f"pattern={field['pattern']}")
        if field.get("maxlength"): parts.append(f"maxlength={field['maxlength']}")
        if field.get("minlength"): parts.append(f"minlength={field['minlength']}")
        if field.get("min") not in ("", None): parts.append(f"min={field['min']}")
        if field.get("max") not in ("", None): parts.append(f"max={field['max']}")
        if field.get("required"): parts.append("required")
        if field.get("autocomplete"): parts.append(f"autocomplete={field['autocomplete']}")
        return ", ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ai_test_data.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/ai_test_data.py tests/test_ai_test_data.py
git commit -m "feat(ai): per-cell LLM enrichment with validate-and-retry"
```

---

## Task 14: Wire AI enrichment into the generator

**Files:**
- Modify: `core/test_case_generator.py`
- Test: `tests/test_test_case_generator.py`

The generator currently falls back to `"Test 1234"` when L1/L2/L3 all miss. With AI, the order becomes: L1 → L2 → L3 → AI (if available and either `per_field_rule` or `ai_context` is non-empty, OR the field is bare free-text) → fallback.

- [ ] **Step 1: Add failing test**

Append to `tests/test_test_case_generator.py`:

```python
class TestAIEnrichment:
    def test_ai_called_when_field_is_bare_freetext_and_ai_context_present(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = "Senior citizen value"
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        rows = gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={"title": "Reg", "h1": "Sign up", "first_paragraph": ""},
            mode="compact",
            ai_contexts_by_row={0: "Senior citizen"},
        )
        # Happy path row uses the AI value
        assert rows[0]["values"]["Bio"] == "Senior citizen value"
        ai.generate_value.assert_called()

    def test_ai_not_called_when_l1_resolves(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        gen.generate(
            [_field(element_name="Email", element_type="input-email")],
            page_context={}, mode="compact",
        )
        # L1 resolves email type, so AI is not called
        ai.generate_value.assert_not_called()

    def test_ai_falls_back_to_heuristic_when_returns_none(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = None  # AI fails
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        rows = gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={}, mode="compact",
            ai_contexts_by_row={0: "Senior citizen"},
        )
        # Falls back to "Test 1234"
        assert rows[0]["values"]["Bio"] == "Test 1234"

    def test_per_field_rule_passed_to_ai(self):
        from unittest.mock import MagicMock
        ai = MagicMock()
        ai.generate_value.return_value = "rahul@gmail.com"
        gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml", ai_client=ai)
        gen.generate(
            [_field(element_name="Bio", element_type="textarea")],
            page_context={}, mode="compact",
            per_field_rules={"Bio": "Make it about banking"},
            ai_contexts_by_row={0: ""},
        )
        call_kwargs = ai.generate_value.call_args.kwargs
        assert call_kwargs["per_field_rule"] == "Make it about banking"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_case_generator.py::TestAIEnrichment -v`
Expected: FAIL — `generate()` doesn't yet call the AI client.

- [ ] **Step 3: Wire AI into the generator**

In `core/test_case_generator.py`, add a new method and update `generate()`:

```python
    def _resolve_value(
        self,
        field: dict,
        page_context: dict,
        per_field_rule: str,
        ai_context: str,
    ) -> str:
        """Resolve a single field's value through L1→L4 + fallback."""
        v = self._l1_dom_constraint(field)
        if v is not None:
            return v
        v = self._l2_autocomplete(field)
        if v is not None:
            return v
        v = self._l3_dictionary(field)
        if v is not None:
            return v
        # L4: AI enrichment if client present and we have any context to work with
        if self.ai_client is not None and (per_field_rule or ai_context or self._is_bare_freetext(field)):
            ai_value = self.ai_client.generate_value(
                field=field,
                page_context=page_context,
                per_field_rule=per_field_rule,
                ai_context=ai_context,
            )
            if ai_value is not None:
                return ai_value
        return self._fallback(field)

    def _is_bare_freetext(self, field: dict) -> bool:
        etype = (field.get("element_type") or "").lower()
        return etype in ("input-text", "textarea") and not (
            field.get("pattern") or field.get("autocomplete") or field.get("maxlength")
        )
```

Replace the body of `generate()` so it calls `_resolve_value` instead of `generate_value` for each cell, and threads per-field rules + per-row context through:

```python
    def generate(
        self,
        fields: list[dict],
        page_context: dict | None = None,
        mode: str = "compact",
        per_field_rules: dict[str, str] | None = None,
        ai_contexts_by_row: dict[int, str] | None = None,
    ) -> list[dict]:
        page_context = page_context or {}
        per_field_rules = per_field_rules or {}
        ai_contexts_by_row = ai_contexts_by_row or {}
        editable = [f for f in fields if (f.get("element_type") or "").lower() not in ("button",)]

        def values_for_row(row_index: int) -> dict[str, str]:
            ctx = ai_contexts_by_row.get(row_index, "")
            return {
                f["element_name"]: self._resolve_value(
                    f, page_context, per_field_rules.get(f["element_name"], ""), ctx
                )
                for f in editable
            }

        rows = [{
            "test_case_name": "Happy path",
            "ai_context": ai_contexts_by_row.get(0, ""),
            "values": values_for_row(0),
        }]

        # Negatives reuse the row-0 valid values for the non-targeted fields
        valid_values = rows[0]["values"]
        for i, neg in enumerate(self.derive_negatives(editable, mode=mode), start=1):
            row_values = dict(valid_values)
            row_values[neg["field"]] = neg["value"]
            rows.append({
                "test_case_name": f"{neg['field']}: {self._violation_label(neg['violation'])}",
                "ai_context": ai_contexts_by_row.get(i, ""),
                "values": row_values,
            })
        return rows
```

Also keep `generate_value(field)` as a thin wrapper for the older single-field tests:

```python
    def generate_value(self, field: dict) -> str:
        return self._resolve_value(field, page_context={}, per_field_rule="", ai_context="")
```

- [ ] **Step 4: Run all generator tests**

Run: `pytest tests/test_test_case_generator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/test_case_generator.py tests/test_test_case_generator.py
git commit -m "feat(generator): plug AI enrichment in as Layer 4 with graceful fallback"
```

---

## Task 15: Persist page context (`Page Context` sheet)

**Files:**
- Modify: `core/excel_manager.py`
- Modify: `core/scanner.py` (none — scanner already returns it; the page that uses Scanner saves it)
- Test: `tests/test_excel_manager.py`

The page context (title/h1/first paragraph) is per-URL, not per-element. We add a new `Page Context` sheet with one row.

- [ ] **Step 1: Add failing test**

Append to `tests/test_excel_manager.py`:

```python
def test_page_context_round_trip(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    url = "http://example.com/form"
    em.save_element_map(url, [])  # ensure workbook exists
    em.save_page_context(url, {
        "title": "Customer Onboarding",
        "h1": "Sign up",
        "first_paragraph": "Welcome to FINN bank.",
    })
    ctx = em.read_page_context(url)
    assert ctx["title"] == "Customer Onboarding"
    assert ctx["h1"] == "Sign up"
    assert ctx["first_paragraph"] == "Welcome to FINN bank."

def test_page_context_returns_empty_when_unset(tmp_path):
    em = ExcelManager(data_dir=str(tmp_path))
    url = "http://example.com/form"
    em.save_element_map(url, [])
    assert em.read_page_context(url) == {"title": "", "h1": "", "first_paragraph": ""}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_excel_manager.py -k page_context -v`
Expected: FAIL — methods do not exist.

- [ ] **Step 3: Implement `save_page_context` and `read_page_context`**

In `core/excel_manager.py`, add:

```python
PAGE_CONTEXT_HEADERS = ["Title", "H1", "First Paragraph"]
```

Add to the `ExcelManager` class:

```python
    def save_page_context(self, url: str, ctx: dict) -> None:
        path = self.get_excel_path(url)
        if not os.path.exists(path):
            return
        wb = self._load_workbook(path)
        if "Page Context" in wb.sheetnames:
            del wb["Page Context"]
        ws = wb.create_sheet("Page Context")
        for col, header in enumerate(PAGE_CONTEXT_HEADERS, 1):
            ws.cell(row=1, column=col, value=header)
        ws.cell(row=2, column=1, value=ctx.get("title", ""))
        ws.cell(row=2, column=2, value=ctx.get("h1", ""))
        ws.cell(row=2, column=3, value=ctx.get("first_paragraph", ""))
        self._save_workbook(wb, path)

    def read_page_context(self, url: str) -> dict:
        path = self.get_excel_path(url)
        if not os.path.exists(path):
            return {"title": "", "h1": "", "first_paragraph": ""}
        wb = self._load_workbook(path)
        if "Page Context" not in wb.sheetnames:
            return {"title": "", "h1": "", "first_paragraph": ""}
        ws = wb["Page Context"]
        return {
            "title": ws.cell(row=2, column=1).value or "",
            "h1": ws.cell(row=2, column=2).value or "",
            "first_paragraph": ws.cell(row=2, column=3).value or "",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_excel_manager.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/excel_manager.py tests/test_excel_manager.py
git commit -m "feat(excel): persist per-URL page context in Page Context sheet"
```

---

## Task 16: Hook scanner page context into the scan workflow

**Files:**
- Modify: `pages/1_scanner.py`

The Scanner page currently calls `Scanner().scan(url)` and saves only the elements. Switch to `scan_with_context` and persist the context.

- [ ] **Step 1: Find the scan call site**

Run: `grep -n "scanner.scan\|Scanner()" pages/1_scanner.py`
Note the line where `scan(url)` is invoked.

- [ ] **Step 2: Update the scan call to capture and save page context**

In `pages/1_scanner.py`, replace the line:
```python
elements = scanner.scan(url)
```
with:
```python
result = scanner.scan_with_context(url)
elements = result["elements"]
page_context = result["page_context"]
```
…and after the existing `excel_manager.save_element_map(url, elements)` call, add:
```python
excel_manager.save_page_context(url, page_context)
```

If the scanner page has multiple call sites for `scan(url)` (single-page mode and crawl mode), update only the single-page mode for v1; the crawl loop can keep using `scan` (per-page context is less useful when crawling).

- [ ] **Step 3: Run the existing scanner page tests if any**

Run: `pytest tests/ -k scanner -v`
Expected: all tests PASS.

- [ ] **Step 4: Manually verify in the running app (smoke test)**

```bash
streamlit run app.py
```

In the browser:
1. Open the Scanner page.
2. Enter `file:///<absolute path>/test_form/v9_constrained.html`.
3. Click Scan.
4. Open the saved Excel from `data/scans/`. Verify the `Page Context` sheet has Title=`Sample Form — Constrained Registration`, H1=`Customer Onboarding`, First Paragraph contains `Register a new customer`.

Stop the app (Ctrl+C).

- [ ] **Step 5: Commit**

```bash
git add pages/1_scanner.py
git commit -m "feat(ui): persist per-page context on scan for AI test case generation"
```

---

## Task 17: UI — Add AI Context column to the Test Data grid

**Files:**
- Modify: `pages/2_test_data.py`

The Test Data page currently builds the grid with columns `[S.No, Test Case Name] + editable_names`. Insert `AI Context` between `Test Case Name` and the field columns.

- [ ] **Step 1: Update the column definition and grid construction**

In `pages/2_test_data.py`, find the lines:
```python
    columns = ["S.No", "Test Case Name"] + editable_names
    if test_data:
        rows = []
        for td in test_data:
            row = {
                "S.No": td.get("S.No", ""),
                "Test Case Name": td.get("Test Case Name", ""),
            }
            for name in editable_names:
                row[name] = td.get(name, "")
            rows.append(row)
        df = pd.DataFrame(rows, columns=columns)
    else:
        df = pd.DataFrame([{col: "" for col in columns}], columns=columns)
        df["S.No"] = 1
```

Replace with:

```python
    columns = ["S.No", "Test Case Name", "AI Context"] + editable_names
    if test_data:
        rows = []
        for td in test_data:
            row = {
                "S.No": td.get("S.No", ""),
                "Test Case Name": td.get("Test Case Name", ""),
                "AI Context": td.get("AI Context", ""),
            }
            for name in editable_names:
                row[name] = td.get(name, "")
            rows.append(row)
        df = pd.DataFrame(rows, columns=columns)
    else:
        df = pd.DataFrame([{col: "" for col in columns}], columns=columns)
        df["S.No"] = 1
```

- [ ] **Step 2: Update the Save handler to persist `AI Context`**

In the same file, find:
```python
    if st.button("Save", type="primary"):
        save_rows = []
        for idx, row in edited_df.iterrows():
            row_dict = {"sno": idx + 1, "test_case_name": row.get("Test Case Name", "")}
            for name in editable_names:
                row_dict[name] = row.get(name, "")
            save_rows.append(row_dict)

        excel_manager.save_test_data(url, save_rows)
        st.success("Test data saved!")
```

Replace with:

```python
    if st.button("Save", type="primary"):
        save_rows = []
        for idx, row in edited_df.iterrows():
            row_dict = {
                "sno": idx + 1,
                "test_case_name": row.get("Test Case Name", ""),
                "ai_context": row.get("AI Context", ""),
            }
            for name in editable_names:
                row_dict[name] = row.get(name, "")
            save_rows.append(row_dict)

        excel_manager.save_test_data(url, save_rows)
        st.success("Test data saved!")
```

- [ ] **Step 3: Smoke test in the app**

```bash
streamlit run app.py
```

Open the Test Data page, pick a previously scanned URL, verify the grid has the `AI Context` column between `Test Case Name` and the first field column. Add some text to AI Context, save, reload — the value persists.

Stop the app.

- [ ] **Step 4: Commit**

```bash
git add pages/2_test_data.py
git commit -m "feat(ui): add AI Context column to Test Data grid"
```

---

## Task 18: UI — "AI Generate Test Cases" button + Compact toggle

**Files:**
- Modify: `pages/2_test_data.py`

- [ ] **Step 1: Add the button + toggle and wire generation**

In `pages/2_test_data.py`, near the top of the file, update the imports:

```python
import streamlit as st
import pandas as pd
from core.excel_manager import ExcelManager
from core.test_case_generator import TestCaseGenerator
from core.ai_test_data import AITestData
from core.field_rules import FieldRulesStore
```

Then, just before the `st.subheader("Test Cases")` line, insert:

```python
    rules_store = FieldRulesStore(data_dir=DATA_DIR)
    field_rules = rules_store.read(url)
    page_context = excel_manager.read_page_context(url)

    col_btn, col_toggle = st.columns([1, 2])
    with col_btn:
        do_generate = st.button("AI Generate Test Cases", type="secondary")
    with col_toggle:
        compact = st.checkbox("Compact negatives (one per field)", value=True)
        overwrite = st.checkbox("Overwrite existing values", value=False)

    if do_generate:
        ai = AITestData()
        generator = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        # Pull AI context from any rows the user already typed
        existing_rows = excel_manager.read_test_data(url) or []
        ai_contexts_by_row = {
            i: (r.get("AI Context") or "") for i, r in enumerate(existing_rows)
        }

        rows = generator.generate(
            fields=element_map,
            page_context=page_context,
            mode="compact" if compact else "thorough",
            per_field_rules=field_rules,
            ai_contexts_by_row=ai_contexts_by_row,
        )

        # Merge with existing user-entered values unless overwrite is checked
        save_rows = []
        for i, generated in enumerate(rows):
            existing = existing_rows[i] if i < len(existing_rows) else {}
            row_dict = {
                "sno": i + 1,
                "test_case_name": existing.get("Test Case Name") or generated["test_case_name"],
                "ai_context": existing.get("AI Context") or generated["ai_context"],
            }
            for name in editable_names:
                user_val = (existing.get(name) or "").strip()
                gen_val = generated["values"].get(name, "")
                if overwrite or not user_val:
                    row_dict[name] = gen_val
                else:
                    row_dict[name] = user_val
            save_rows.append(row_dict)

        excel_manager.save_test_data(url, save_rows)
        st.success(f"Generated {len(save_rows)} test cases.")
        st.rerun()

        if not ai.is_available():
            st.info("Ollama not reachable — used heuristic generation only. "
                    "AI Context columns were ignored. "
                    "Start `ollama serve` to enable AI enrichment.")
```

- [ ] **Step 2: Smoke test in the app**

```bash
streamlit run app.py
```

In the browser:
1. Scanner page: scan `file:///.../test_form/v9_constrained.html` if not already done.
2. Test Data page: pick that URL.
3. Click `AI Generate Test Cases` (Compact checked by default).
4. Verify: ~6-7 rows appear (1 happy + 1 negative per constrained field). The Customer Reference happy-path cell contains a value matching `[A-Z]{4}[0-9]{4}`. The Age cell contains a number between 18 and 120.
5. Toggle to Thorough, click again — more rows appear.

Stop the app.

- [ ] **Step 3: Commit**

```bash
git add pages/2_test_data.py
git commit -m "feat(ui): AI Generate Test Cases button with Compact/Thorough toggle"
```

---

## Task 19: UI — Per-row 🔄 regenerate button

**Files:**
- Modify: `pages/2_test_data.py`

Streamlit's `data_editor` does not natively support per-row action buttons. The simplest reliable UX is a small column rendered above/below the grid that lets the user pick a row index to regenerate. This task implements that.

- [ ] **Step 1: Add a "Regenerate row" widget**

In `pages/2_test_data.py`, just below the `if do_generate:` block from Task 18, insert:

```python
    st.divider()
    st.caption("Regenerate one row using its current AI Context (preserves manual values).")
    col_pick, col_regen = st.columns([1, 1])
    with col_pick:
        existing_rows = excel_manager.read_test_data(url) or []
        row_options = [f"Row {i+1}: {r.get('Test Case Name', '(unnamed)')}"
                       for i, r in enumerate(existing_rows)]
        chosen = st.selectbox("Row to regenerate", options=row_options) if row_options else None
    with col_regen:
        do_regen = st.button("🔄 Regenerate this row", disabled=not row_options)

    if do_regen and chosen:
        row_idx = row_options.index(chosen)
        ai = AITestData()
        generator = TestCaseGenerator(
            field_dictionary_path="data/field_dictionary.yaml",
            ai_client=ai if ai.is_available() else None,
        )
        target = existing_rows[row_idx]
        ai_ctx = target.get("AI Context", "")
        # Resolve a value per editable field, preserving manual entries
        new_row = dict(target)
        for f in element_map:
            if f.get("element_type") == "button":
                continue
            name = f["element_name"]
            existing_val = (target.get(name) or "").strip()
            if existing_val:
                continue  # preserve manual value
            new_row[name] = generator._resolve_value(
                field=f, page_context=page_context,
                per_field_rule=field_rules.get(name, ""), ai_context=ai_ctx,
            )

        # Save back: rebuild full save_rows list, replacing only this index
        save_rows = []
        for i, r in enumerate(existing_rows):
            source = new_row if i == row_idx else r
            save_rows.append({
                "sno": i + 1,
                "test_case_name": source.get("Test Case Name", ""),
                "ai_context": source.get("AI Context", ""),
                **{name: source.get(name, "") for name in editable_names},
            })
        excel_manager.save_test_data(url, save_rows)
        st.success(f"Regenerated row {row_idx + 1}.")
        st.rerun()
```

- [ ] **Step 2: Smoke test in the app**

```bash
streamlit run app.py
```

1. Generate test cases.
2. Pick a row, edit its AI Context, save the grid.
3. Use the row picker, click 🔄. The row's empty cells fill using the new AI Context; manual cells stay.

Stop the app.

- [ ] **Step 3: Commit**

```bash
git add pages/2_test_data.py
git commit -m "feat(ui): per-row regenerate button preserving manual values"
```

---

## Task 20: UI — Per-field rule editor on the Field Reference table

**Files:**
- Modify: `pages/2_test_data.py`

- [ ] **Step 1: Make the Field Reference table editable with a Rule column**

In `pages/2_test_data.py`, find:
```python
    st.divider()
    st.subheader("Field Reference")
    ref_data = []
    for elem in element_map:
        if elem["element_type"] in ("button",):
            continue
        ref = {
            "Field": elem["element_name"],
            "Type": elem["element_type"],
            "Available Options": elem.get("available_options", ""),
        }
        ref_data.append(ref)
    st.dataframe(ref_data, use_container_width=True)
```

Replace with:

```python
    st.divider()
    st.subheader("Field Reference & Per-Field Rules")
    st.caption("Per-field rules are plain-English instructions sent to the AI for every "
               "test case row. Example: \"Always use Gmail addresses\".")
    ref_rows = []
    for elem in element_map:
        if elem["element_type"] in ("button",):
            continue
        name = elem["element_name"]
        ref_rows.append({
            "Field": name,
            "Type": elem["element_type"],
            "Available Options": elem.get("available_options", ""),
            "Per-field rule": field_rules.get(name, ""),
        })
    ref_df = pd.DataFrame(ref_rows)
    edited_ref = st.data_editor(
        ref_df,
        use_container_width=True,
        disabled=["Field", "Type", "Available Options"],
        column_config={
            "Per-field rule": st.column_config.TextColumn(
                "Per-field rule",
                help="Plain-English rule applied to this field across all test cases.",
            ),
        },
        key="field_rules_editor",
    )
    if st.button("Save Per-Field Rules"):
        new_rules = {
            row["Field"]: (row["Per-field rule"] or "").strip()
            for _, row in edited_ref.iterrows()
            if (row["Per-field rule"] or "").strip()
        }
        rules_store.save(url, new_rules)
        st.success("Per-field rules saved.")
        st.rerun()
```

- [ ] **Step 2: Smoke test**

```bash
streamlit run app.py
```

1. Open Test Data page.
2. Scroll to Field Reference table; type a rule like "Always use Gmail" for the Email field.
3. Click "Save Per-Field Rules".
4. Click "AI Generate Test Cases" again.
5. With Ollama running, the Email cells should reflect the rule. Without Ollama, the heuristic ignores the rule (expected — rules are AI-only).

Stop the app.

- [ ] **Step 3: Commit**

```bash
git add pages/2_test_data.py
git commit -m "feat(ui): per-field rule editor on the Field Reference table"
```

---

## Task 21: End-to-end integration test

**Files:**
- Test: `tests/test_integration.py`

- [ ] **Step 1: Add an end-to-end test for heuristic generation**

Append to `tests/test_integration.py`:

```python
import os
import re
import pytest
from core.scanner import Scanner
from core.test_case_generator import TestCaseGenerator


@pytest.fixture
def constrained_url():
    path = os.path.abspath("test_form/v9_constrained.html")
    return f"file:///{path.replace(os.sep, '/')}"


def test_end_to_end_heuristic_generation(constrained_url):
    """Scan v9_constrained.html → generate cases → assert quality."""
    result = Scanner().scan_with_context(constrained_url)
    elements = result["elements"]
    page_context = result["page_context"]

    gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml")
    rows = gen.generate(elements, page_context=page_context, mode="compact")

    # First row is happy path; every constrained field has a valid value
    happy = rows[0]
    assert happy["test_case_name"] == "Happy path"
    cust_ref_val = happy["values"]["Customer Reference"]
    assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", cust_ref_val), \
        f"Customer Reference {cust_ref_val!r} does not match pattern"
    age_val = int(happy["values"]["Age"])
    assert 18 <= age_val <= 120

    # At least one negative row per constrained field
    negative_field_names = {r["test_case_name"].split(":")[0] for r in rows[1:]}
    for required_field in {"Customer Reference", "Age", "Email", "First Name", "Country"}:
        assert required_field in negative_field_names, \
            f"missing negative case for {required_field}"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_integration.py::test_end_to_end_heuristic_generation -v`
Expected: PASS.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end heuristic test case generation against constrained fixture"
```

---

## Task 22: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an "AI-Generated Test Cases" section**

In `README.md`, find the existing "Features" list. Below the existing features, add a new top-level section before "Multi-Page Flows":

```markdown
## AI-Generated Test Cases

The Test Data Manager can auto-populate the test cases grid from a scanned form.
Click **AI Generate Test Cases** and the tool produces:

- One **happy-path** row with valid values for every field.
- One **negative** row per field, varying the most distinctive constraint
  (Compact mode, default). Switch to **Thorough** to get one negative row per
  violatable constraint per field.

How values are chosen, in priority order:

1. Explicit DOM constraints — `pattern`, `min`, `max`, `maxlength`, `type`, `required`.
2. The `autocomplete` token registry (e.g. `email`, `tel`, `postal-code`).
3. A label dictionary at `data/field_dictionary.yaml` (PAN, GSTIN, SSN, etc. — extend freely).
4. AI enrichment via Ollama+Mistral when the field has no explicit constraints
   and you've supplied an **AI Context** (per row) or **Per-field rule** (per column).
5. A typed fallback string respecting `maxlength`.

### AI Context (per row)

The grid has an **AI Context** column. Type a plain-English scenario for any
row — e.g. *"Senior citizen from Mumbai"* — and the AI fills empty cells in
that row to match. Click the **🔄 Regenerate this row** button to refresh that
row using the new context.

### Per-field rules (per column)

The Field Reference table at the bottom of the page has an editable
**Per-field rule** column. Type instructions like *"Always use Gmail addresses"*
or *"Format: 4 letters + 4 digits"* and they apply to every row for that field.
Rules are stored in `data/scans/<sanitized_url>.field_rules.yaml` and survive
rescans.

### Without Ollama

The heuristic layers (1–3) and fallback work fully without Ollama. AI Context
and Per-field rules are simply ignored when Ollama isn't reachable.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document AI-generated test cases on the Test Data page"
```

---

## Self-Review Notes

After writing this plan, I checked it against the spec:

- **Spec coverage:** All sections covered. Scanner extension (Tasks 4, 5). Excel extension (Tasks 6, 7, 15). TestCaseGenerator with all four layers + negatives + orchestrator (Tasks 8–12, 14). Per-field rules sidecar (Task 3). Field dictionary (Task 2). AI enrichment with validate-and-retry (Task 13). UI: AI Context column (17), AI Generate button + Compact toggle (18), per-row regenerate (19), per-field rule editor (20). Integration test (21). README (22).
- **Placeholder scan:** No TBDs or "implement later" steps. Every code-changing step shows the actual code.
- **Type consistency:** Method names verified — `generate_value`, `generate`, `derive_negatives`, `_resolve_value`, `_l1_dom_constraint`, `_l2_autocomplete`, `_l3_dictionary` are referenced consistently across tasks. `AITestData.generate_value` keyword arguments (`field`, `page_context`, `per_field_rule`, `ai_context`) match between Task 13 (definition) and Task 14 (caller).
- **Storage path:** Spec says `data/scans/<sanitized_url>.field_rules.yaml`; Task 3 implements exactly that.
- **Backward compatibility:** Element map keeps `Last Scanned` at column 16; new constraint columns appended at 17–25 — old Excels still readable, new code returns `""` for missing columns. AI Context column is auto-inserted into existing Test Data sheets via `ws_td.insert_cols(3)` in Task 7.
