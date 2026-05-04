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
  - Level 3: Ollama (Mistral) semantic matching (optional)
- **History** — Full audit trail of scans, runs, and healing events

## Ollama (Optional)

Level 3 healing uses a local Ollama server running the Mistral model.

```bash
# Install Ollama from https://ollama.com, then:
ollama pull mistral
ollama serve
```

Optional environment overrides:

```bash
set OLLAMA_HOST=http://localhost:11434
set OLLAMA_MODEL=mistral
```

The tool works fully without Ollama — AI matching is only used as a last-resort fallback.

## Multi-Page Flows

Beyond single-page testing, the tool can crawl an entire site and run
multi-page test flows.

### Crawling

On the Scanner page, tick **Crawl entire site (same-domain)**. The crawler
walks every reachable link whose URL stays on the same domain as your start URL,
respects `max_pages` and `max_depth` caps, and saves one element-map Excel per
discovered page (same format as a single-page scan, so healing applies the
same way).

### Recipes

A **recipe** is a small YAML file describing a sequence of UI actions on one
page (e.g. "fill email, fill password, click Sign In") plus optional
assertions about the final state.

On the **Flows** page:

1. Pick a crawled page.
2. Type a goal (e.g. "log in successfully") and click **Suggest with AI** —
   Mistral via Ollama drafts the steps grounded in the actual scanned
   elements (no hallucinated field names).
3. Edit the generated step table.
4. Click **Test live** to watch the recipe run in a visible browser.
5. Click **Save recipe** when it works (button is disabled until a successful
   live test).

Recipes can have `expected_outcome: success` or `failure`. A failure recipe
PASSES when the configured assertions match (e.g. an error message appears).

### Flows

A **flow** is an ordered list of recipes that run in one browser session.
Build one on the Flows page, then run it from the Runner page in **Flow** mode.

## Running Tests

```bash
pytest tests/ -v
```

## Sample Form

A test form is included at `test_form/sample_form.html` for development and testing.
