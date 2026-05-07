# Context-Aware AI Test Case Generation — Design

**Date:** 2026-05-07
**Status:** Draft for review
**Scope:** Additive feature on the Test Data Manager page. Existing manual test data entry remains unchanged.

## 1. Problem

The current Test Data Manager requires the user to type every test value into a grid by hand, one cell at a time. For a form with 15 fields and 5 test cases that's 75 cells. Worse, the user has to *know* the format constraints — that the Customer Reference field expects `4 alphabets + 4 digits` (e.g. `FINN0316`), that the email must be a real-looking address, that the age must be between 18 and 120.

We want the tool to generate sensible test cases automatically given only the URL the user has already scanned, with **zero additional friction at scan time**. The user should be able to:

1. Click one button and get a populated grid of valid (happy-path) and invalid (negative) test cases for every field, with values that respect each field's actual format constraints.
2. Optionally guide the AI by writing per-field rules ("emails for this form should always be Gmail") and per-row scenarios ("Senior citizen from Mumbai") in plain English.
3. Edit any cell manually if they don't like what was generated.

## 2. Goals

- **Context-aware value generation** — values that match each field's real constraints (`pattern`, `maxlength`, `min`/`max`, `type`, etc.), not just dummy strings.
- **Mechanical negative case derivation** — every constraint that exists implies a negative case (required → empty, pattern → mutated value, maxlength → too long, range → out of range).
- **Two layers of optional user guidance** — column-wise rules (per-field) and row-wise scenarios (per-row), both in plain English.
- **Graceful degradation** — if Ollama is down, the heuristic layer alone still produces useful test cases.

## 3. Non-goals

- Cross-field semantic dependencies in v1 (e.g. confirm-password matching, end-date-after-start-date). Deferred.
- Cloud LLM integration (Anthropic, OpenAI). Mistral via the existing Ollama integration only.
- Auto-running generation immediately after a scan. The user clicks a button when they're ready.
- Replacing manual entry. The existing free-form grid editing remains the default.
- Generating test cases for buttons or non-editable elements (already excluded from the grid today).

## 4. Architecture

```
[Scanned URL]
    │
    ├─► Element map (Excel) — extended with new constraint columns
    │       (pattern, maxlength, minlength, min, max, required,
    │        autocomplete, helper_text, page_context)
    │
    ▼
[Test Data Manager page]
    │
    ├─► User edits per-field instructions (sidecar YAML, persists across rescans)
    │
    ├─► User types per-row scenario hints in the "AI Context" column
    │
    └─► User clicks "AI Generate Test Cases"
            │
            ▼
        [TestCaseGenerator]
            │
            ├── L1: explicit DOM constraints   (deterministic)
            ├── L2: autocomplete token registry (deterministic)
            ├── L3: label/name dictionary       (deterministic)
            ├── L4: LLM enrichment              (Ollama, gated, validated)
            └── Fallback: typed generic string
            │
            ▼
        [Negative case derivation] — Compact (default) or Thorough mode
            │
            ▼
        Populated grid
```

Each unit has one responsibility:

- **Scanner extension** — capture additional DOM constraints already present on each element. Read-only widening of what's already extracted.
- **TestCaseGenerator** — given a field's constraints + optional per-field rule + optional per-row context, produce one value. Pure function, easy to test.
- **NegativeCaseDeriver** — given the same constraint metadata, mechanically emit negative test case rows. Two modes: **Compact** (default — one negative row per field, choosing the most distinctive constraint) and **Thorough** (one negative row per violatable constraint per field).
- **AITestData** — separate module from `ai_matcher`; called only when the heuristic layers can't decide and Ollama is available.
- **FieldDictionary** — static YAML of common semantic field types (PAN, GSTIN, SSN, IBAN, etc.); user-extensible.

## 5. Components

### 5.1 Extended: `core/scanner.py`

Capture per-element attributes the current scanner ignores:

- `pattern` — HTML5 regex constraint
- `title` — typically the human-readable hint paired with `pattern`
- `minlength`, `maxlength`
- `min`, `max`, `step` (number, range, date)
- `required`
- `inputmode`
- `autocomplete` (rich semantic hint: `email`, `tel`, `postal-code`, `cc-number`, `given-name`, etc.)
- Resolved `aria-describedby` text
- Nearest sibling helper text (`<small>`, `[class*=hint]`, `[class*=help]`, immediate text node after the input)

Capture once per scan (not per element):

- Page `<title>`
- First `<h1>`
- First paragraph of body text

These are stored in the element map Excel as new columns. Old element maps without these columns load with blank values — fully backward-compatible.

### 5.2 New: `core/test_case_generator.py`

Public surface:

```python
class TestCaseGenerator:
    def __init__(self, ollama_client=None, field_dictionary_path="data/field_dictionary.yaml"):
        ...

    def generate(
        self,
        elements: list[dict],
        page_context: dict,
        per_field_rules: dict[str, str] | None = None,
        per_row_intents: list[str] | None = None,
    ) -> list[dict]:
        """Returns a list of test case rows ready to write to the grid."""
```

Per-field resolution layers, walked in order, first hit wins:

| Layer | Source | Example |
|---|---|---|
| L1 | `pattern` attribute | `[A-Z]{4}[0-9]{4}` → `exrex.getone(...)` → `FINN0316` |
| L1 | `type=email/number/date` + min/max | midpoint, real-looking email |
| L1 | `select` / `radio` | first non-placeholder option |
| L2 | `autocomplete` token | `email` → `test.user@example.com`; `cc-number` → `4111111111111111` |
| L3 | label/name dictionary match | label contains "PAN" → `ABCDE1234F` |
| L4 | LLM enrichment (gated) | free-text field, no constraints, AI Context present → call Ollama |
| Fallback | typed generic string honoring `maxlength` | `Test 1234` |

Negative case derivation runs over the same metadata. Per violatable constraint:

| Constraint | Negative case generated |
|---|---|
| `required` | empty value |
| `pattern` | mutated value (lowercased / wrong length) |
| `maxlength=N` | string of length N+1 |
| `minlength=N` | string of length N-1 |
| `min=X` | X-1 |
| `max=X` | X+1 |
| `type=email` | `notanemail` |
| `type=number` | `abc` |

**Two modes:**

- **Compact (default)**: one negative row per field, choosing the single most distinctive constraint. Priority order when a field has multiple constraints: `pattern` > `min`/`max` > `maxlength`/`minlength` > `type=email`/`number` > `required`. A 15-field form with 3 constraints each yields ~15 negative rows instead of ~45.
- **Thorough**: one negative row per violatable constraint per field. Same form yields ~45 negative rows.

In both modes, every negative row has only the field-under-test set to an invalid value; all other fields are populated with valid heuristic values so the rejection cause is unambiguous.

Each generated row contains:

- `test_case_name` — auto-generated label like `Happy path` or `customer_ref: invalid format`
- `ai_context` — empty unless the user typed one before clicking generate
- one column per field with the generated value

### 5.3 New: `core/ai_test_data.py`

Separate from `ai_matcher` because the prompt shape, output validation, and failure handling are different.

Called per cell, not per row, so Mistral's small working memory doesn't have to track 15 fields at once.

Prompt template (sent with Ollama `format="json"`):

```
You are generating one value for a form field.

Page context: {title}. {h1}. {first_paragraph}
Field: {label} (name={name}, type={type})
Helper text: {helper_text or "none"}
Constraints: {dom_constraints_summary or "none"}
Per-field rule: {per_field_rule or "none"}
Test case scenario: {ai_context or "default valid value"}

Return strict JSON: {"value": "<generated value>"}
```

Output handling:

1. Parse JSON. If parse fails, retry once.
2. Validate the returned value against the DOM constraints (regex match, length, range, type).
3. If validation fails, re-prompt once with `Your previous answer "<X>" violated <constraint>. Try again.`
4. If still invalid, fall back to the heuristic generator silently.

### 5.4 New: `data/field_dictionary.yaml`

Seed entries (~20). User-extensible without code changes.

```yaml
pan:
  match: ["pan", "pan number", "pan_no"]
  regex: "[A-Z]{5}[0-9]{4}[A-Z]"
  example: "ABCDE1234F"
gstin:
  match: ["gstin", "gst number"]
  regex: "[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
aadhaar:
  match: ["aadhaar", "aadhar"]
  regex: "[0-9]{12}"
ssn:
  match: ["ssn", "social security"]
  regex: "[0-9]{3}-[0-9]{2}-[0-9]{4}"
ifsc:
  match: ["ifsc"]
  regex: "[A-Z]{4}0[A-Z0-9]{6}"
# ... approximately 15 more
```

Match logic: case-insensitive substring search across the field's label, name, id, and placeholder.

### 5.5 New: per-field rule storage

Per-field rules are user-typed plain-English instructions like "emails should always be @company.com". They live in a sidecar YAML next to the element map Excel:

```
data/scans/<sanitized_url>/element_map.xlsx       # existing, gets new columns
data/scans/<sanitized_url>/field_rules.yaml       # new, optional
```

Schema:

```yaml
field_rules:
  email: "Always use Gmail addresses"
  city: "Always Mumbai"
  age: "Always between 25 and 35"
```

Stored in a sidecar (not in the Excel) so it survives a rescan. The element map Excel is regenerated on every scan; field rules are not.

### 5.6 Modified: `pages/2_test_data.py`

Three additions to the existing page:

**A. New "AI Context" column on the test cases grid**
Sits between `Test Case Name` and the field columns. Free-text. Empty by default. The user types per-row scenarios here ("Senior citizen from Mumbai", "User with the longest possible name", "Empty required fields case").

**B. New "AI Generate Test Cases" button**
Above the data editor, with a "Compact negatives" checkbox next to it (checked by default — Compact mode). When clicked:

1. Reads the current grid state (preserves any rows the user already filled).
2. If the grid is empty, generates a baseline set: one happy-path row plus negative rows per the selected mode (Compact = one per field; Thorough = one per violatable constraint per field).
3. If rows exist with empty cells and an `AI Context` value, fills the empty cells in those rows using the context.
4. If rows exist with empty cells and no `AI Context`, fills using heuristics + per-field rules only.
5. Existing user-entered values are never overwritten unless an "Overwrite all" checkbox is ticked.

The button is named "AI Generate Test Cases" regardless of whether the heuristic or LLM path actually ran. (Per product requirement: AI-branding is client-facing.)

**B2. Per-row regenerate button (🔄)**
A button column at the start of each row in the test cases grid. Clicking it clears every empty-or-non-manual cell in that row and re-runs generation for just that row, using the row's current `AI Context` and the per-field rules. Manually-entered cells are preserved.

Single-cell regeneration is supported through the natural workflow: clear the cell value in the grid, click "AI Generate Test Cases", only that empty cell is regenerated. Documented as a tooltip on the row regenerate button.

**C. Editable instruction column on the Field Reference table**
The Field Reference table at the bottom of the page already lists each field and its options. Add an editable "Per-field rule" column. Edits save to `field_rules.yaml` on a Save button click.

## 6. Failure modes

| Condition | Behaviour |
|---|---|
| Ollama not reachable | Banner: "AI enrichment unavailable; heuristic generation only." Button still works; rows requiring LLM enrichment leave those cells blank with a placeholder. |
| LLM returns invalid JSON twice | Fall back to heuristic silently. |
| LLM returns value violating DOM constraints twice | Fall back to heuristic silently. |
| `exrex` not installed | Startup warning banner; pattern fields fall back to a fixed safe string. |
| `field_dictionary.yaml` missing or malformed | Startup warning; L3 layer skipped. |
| Field has no constraints, no autocomplete, no dictionary hit, AI Context empty, Ollama down | Cell left blank with placeholder text "(specify intent or fill manually)". |

## 7. Tests

- `tests/test_test_case_generator.py`
  - L1 pattern → exrex sample matches the regex
  - L1 numeric type with min/max → value within range
  - L1 select → first non-placeholder option
  - L2 autocomplete tokens → registry lookup correct for each token
  - L3 dictionary match → correct generator selected for label variants
  - Fallback respects `maxlength`
  - Negative derivation: one row per violatable constraint, each value violates exactly one constraint
  - Per-field rule injection into LLM prompt
  - Per-row AI Context injection into LLM prompt
- `tests/test_ai_test_data.py`
  - Mocked Ollama client returning valid JSON → value returned
  - Mocked client returning invalid JSON twice → falls back to heuristic silently
  - Mocked client returning value violating constraint → re-prompts once, then falls back silently
- `tests/test_scanner.py` (extend existing)
  - New attributes captured for inputs in `test_form/v1_baseline.html`
  - Old element maps (without new columns) still load
- `tests/test_integration.py` (extend existing)
  - End-to-end: scan → generate (heuristic only, no LLM dependency in CI) → grid populated correctly for `test_form/v1_baseline.html`

LLM-dependent tests use a mocked Ollama client. CI never depends on Ollama being available.

## 8. Out of scope (deferred)

- Cross-field semantic dependencies (confirm-password, date ranges).
- Cloud LLM integration.
- Bulk regeneration UI (per-column "regenerate this field across all rows").
- Test case execution prioritisation (e.g. run negative cases first).
- Localisation of generated values beyond what the field dictionary already supports.
- Auto-generation triggered immediately after scan.

## 9. Open questions

None at design time.
