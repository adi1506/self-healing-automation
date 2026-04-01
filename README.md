# Self-Healing Test Automation

A Streamlit-based tool that scans web forms, manages test data, populates fields, verifies values, and automatically heals broken selectors using a 3-level fallback chain.

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

## Running

```bash
streamlit run app.py
```

## Features

- **Scanner** — Extract all form elements with multiple locator strategies
- **Test Data Manager** — Manage multiple test case data sets inline
- **Runner** — Populate forms, verify values, take screenshots
- **Self-Healer** — Auto-detect and fix broken selectors:
  - Level 1: Try alternative stored selectors
  - Level 2: Attribute-based fingerprint matching
  - Level 3: Gemini AI semantic matching (optional)
- **History** — Full audit trail of scans, runs, and healing events

## Gemini API (Optional)

Set your Gemini API key in the sidebar or as an environment variable:

```bash
set GEMINI_API_KEY=your-key-here
```

The tool works fully without Gemini — AI matching is only used as a last-resort fallback.

## Running Tests

```bash
pytest tests/ -v
```

## Sample Form

A test form is included at `test_form/sample_form.html` for development and testing.
