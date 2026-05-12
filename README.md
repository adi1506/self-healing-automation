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

The app has five pages, all browser-driven (no filesystem editing required):

- **Dashboard** — Pass/Fail/Healed counters and recent runs feed.
- **Scenarios** — Build a Scenario (Steps + optional Dataset + Runs + Settings tabs).
  Generate data rows with AI, upload CSV/XLSX in bulk, or edit inline.
  A Scenario with a Dataset runs once per row.
- **Library** — Scanned pages as reusable assets. Single-page or crawl. Download
  element-map Excel for sharing.
- **Reports** — Unified Run history / Healing log / Activity feed.
- **Settings** — Ollama config, storage paths, on-demand migration re-run.

Existing recipes, flows, and per-scan Test Data grids are auto-migrated into
Scenarios on first launch (idempotent — old files remain untouched).

## AI Model

The app uses a local Ollama server. The default and recommended model is **Phi-4 14B**:

```bash
# Install Ollama from https://ollama.com, then:
ollama pull phi4:14b
ollama serve
```

Open the **Settings** page in the app to pick from any installed model. Selection is saved to `data/settings.yaml` and takes effect immediately — no restart.

Alternative models that work well on a CPU-only box:

- `granite4:8b` — fastest, JSON-native.
- `qwen3:14b` — strong multilingual coverage.
- `gemma4:12b` — agent-tuned.
- `mistral:7b` — legacy, smallest footprint.

The tool works fully without Ollama — AI matching is a last-resort heal fallback, and test-data generation falls back to heuristic layers.

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
