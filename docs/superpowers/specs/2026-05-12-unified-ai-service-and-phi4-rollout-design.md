# Unified AI Service & Phi-4 Rollout — Design

**Date:** 2026-05-12
**Status:** Draft for review
**Author:** Brainstorming session

## 1. Problem & Goals

The app's AI surfaces (element-match healing, recipe drafting, per-field value generation) are wired to Ollama running Mistral 7B by default, with each surface independently constructing its own client, checking availability, parsing JSON, and reading `OLLAMA_HOST` / `OLLAMA_MODEL` env vars. The Settings page at `pages/5_settings.py` shows these values **disabled and env-only**, which makes "switch the AI model" require a server restart with new env vars.

The EC2 target (48 vCPU AMD EPYC 7R32, 93 GB RAM, no GPU) can run materially stronger CPU-friendly models than Mistral 7B if we re-baseline the default and make model selection self-service.

### Goals

1. **Default to Phi-4 14B** on fresh installs — strongest CPU-only single-model choice for our two AI surfaces (structured JSON, multi-constraint reasoning).
2. **Plug-and-play model selection** from the Settings page — list installed Ollama models, pick one, save, take effect immediately (no restart).
3. **Unify AI infrastructure** behind a single `AIService` so existing AI features (test case generator, healer) and new features all benefit from the same connection, settings, caching, and timeouts.
4. **Ship three new AI capabilities** built on top of that foundation:
   - **Scenario Suggester** — seed scenarios from a scanned page.
   - **Healing Rationale (inline)** — surface the model's reasoning in the Healing tab.
   - **Failure Summarizer** — one-paragraph AI summary on failed runs.
5. **Ship two existing-feature enhancements** that preserve the user's current work:
   - **Refine Row (Feature X)** — modify a single dataset row with AI, preserving DOM-locked cells, with diff preview.
   - **Append Rows (Feature Y)** — add N AI-generated rows without overwriting existing ones.
6. **Ship operational guts (Z2, Z3, Z4)** — per-call timeout & cancel, response cache, parallel batch generation.

### Non-goals (deferred to a future iteration)

- Pull-from-UI model installer (Tier B of plug-and-play).
- Per-purpose model overrides (Tier C of plug-and-play).
- AI assertion suggester.
- Semantic negative-test expansion.
- Natural-language scenario authoring.
- Per-cell regenerate-with-hint.
- Confidence threshold for auto-heal (Z1).
- "Explain this value" popovers (Z5).
- Observability panel / AI audit trail (Z6, Z7).
- Disk persistence of the response cache.
- Multi-tenant deployment.

## 2. Model Selection Rationale

CPU inference is memory-bandwidth-bound, not compute-bound. With 93 GB RAM and no GPU, parameter count is the latency dial; Q4_K_M quantization is mandatory. The realistic envelope for a single "balanced" model serving both healing (mid-run, must feel responsive) and test-data generation (async-ish, quality matters) is **8B–14B at Q4_K_M**.

Default choice: **Phi-4 14B Q4_K_M** (~9 GB). Rationale:

- Leads sub-32B benchmarks at 84.8 MMLU; particularly strong on STEM / pattern / multi-constraint reasoning, which maps directly to negative-test value generation and ambiguous element matching.
- Reliable structured JSON output (no `<think>` quirks like Qwen3).
- ~4–7 tok/s on the target box → ~3–5s healing matches, ~5–10s test-data values. Acceptable for both interactive heal and async dataset generation.

The selector also exposes Granite 4 8B (faster, JSON-native) and Qwen3 14B (multilingual, thinking-mode capable) as recommended alternatives for clients whose workloads differ.

## 3. Architecture

### 3.1 New module: `core/ai_service.py`

Single owner of all Ollama interactions. Process-global singleton retrieved via `get_ai_service()`. Lives in `st.session_state` so it survives Streamlit re-runs within a session.

```
AIService
├─ connection
│  • client (Ollama)
│  • host  ← data/settings.yaml > OLLAMA_HOST env > "http://localhost:11434"
│  • model ← data/settings.yaml > OLLAMA_MODEL env > "phi4:14b"
│  • is_available() with 30s memo
│  • reload()  — invoked when Settings UI saves
├─ primitive
│  • generate_json(prompt, *, schema=None, timeout=30, cache_key=None) → dict | None
│     - format="json", temperature=0.0
│     - strips <think>...</think> and ```json fences defensively
│     - retries once on invalid JSON with violation feedback
│     - honors response cache and per-call timeout
├─ high-level methods
│  • match_element(old, candidates)             [migrated from AIMatcher]
│  • suggest_recipe(url, elements, goal)        [migrated from AIMatcher]
│  • generate_field_value(field, ctx, ...)      [migrated from AITestData]
│  • suggest_scenarios(page) → list[stub]                          NEW
│  • summarize_run(run_record) → str                               NEW
│  • refine_row(field_defs, current_row, refine_prompt) → dict     NEW
│  • generate_complementary_rows(field_defs, existing, ctx, n)     NEW
└─ observability
   • last_error: str | None
   • last_latency_ms: float | None
```

Prompts move to a new `core/ai_prompts.py` for isolated testability.

### 3.2 Adapters: existing classes preserved as thin wrappers

`core/ai_matcher.py` and `core/ai_test_data.py` keep their **public class names and method signatures**. Their `__init__` resolves the `AIService` singleton instead of constructing their own client. This means `core/healer.py` and `core/test_case_generator.py` change minimally (healer surfaces `rationale`; test_case_generator is untouched).

### 3.3 Settings (Tier A — selector only)

`pages/5_settings.py` is rewritten to:

- Show a green/red connection status with a **Test connection** button.
- Show an editable **Host** text field.
- List installed Ollama models (radio group, populated from `client.list()`).
- **Save selection** writes to `data/settings.yaml` and calls `AIService.reload()`.
- When the recommended default (`phi4:14b`) is missing, show a copy-pastable `ollama pull phi4:14b` hint.
- Show `AIService.last_error` if any.

`data/settings.yaml` shape:

```yaml
ai:
  host: "http://localhost:11434"
  model: "phi4:14b"
```

Env vars (`OLLAMA_HOST`, `OLLAMA_MODEL`) populate the file on first launch if it doesn't exist, then are never read again — UI selection wins.

## 4. New AI Capabilities

### 4.1 Scenario Suggester

- **Location:** Scenarios page → New Scenario form, **Suggest scenarios with AI** panel.
- **Method:** `ai_service.suggest_scenarios(page) → list[{"name", "ai_context", "rationale"}]`
- **UX:** results render as cards; **Add as scenario** button creates a Scenario row with the suggested name and a dataset row with the suggested `ai_context`. The existing `ai_test_data.generate_value` path fills the row — no parallel code path.
- **Fallback:** button disabled with tooltip when AI unavailable.

### 4.2 Healing Rationale (inline)

- **Location:** Reports → Healing tab, new **Why** column between *Matched element* and *Confidence*.
- **Cost:** zero new AI calls. The model already returns a `reasoning` field that today is discarded at `core/healer.py:74`. We persist it onto the healing log record and render it.
- **Display:** one-line truncated. Rows healed at Level 1 / Level 2 show `—`.

### 4.3 Failure Summarizer

- **Location:** Reports → Run detail, yellow callout at top of any failed run labeled **AI summary**.
- **Method:** `ai_service.summarize_run(run_record) → str` (max ~80 words).
- **Trigger:** lazy — computed on first open of the run detail page, cached per (run id, model).
- **UX:** spinner *"Summarizing failure with Phi-4…"* on first open.
- **Fallback:** callout doesn't render when AI unavailable.

### 4.4 Refine Row (Feature X)

- **Location:** Dataset tab, **✏️ Refine with AI** icon per row, alongside the existing **🔄 Regenerate this row**.
- **Method:** `ai_service.refine_row(field_defs, current_row, refine_prompt) → dict`
- **Constraint preservation:** DOM-constrained fields (pattern / min / max / maxlength / select-options) are passed to the model as read-only with their current value; the model is instructed not to change them. After return, the service re-validates and reverts any locked field the model touched anyway.
- **UX:** preview-then-apply. Diff table shows `Field | Current | → | New`. Locked fields render as `(locked — unchanged)`. Apply / Discard buttons.
- **Fallback:** icon disabled with tooltip.

### 4.5 Append Rows (Feature Y)

- **Location:** Dataset tab, **+ Add N AI rows with context…** button below the grid.
- **Method:** `ai_service.generate_complementary_rows(field_defs, existing_rows, batch_ctx, n) → list[row]`
- **Prompt design:** the model receives existing rows so it knows what coverage is present and is asked for N complementary variations.
- **Parallelism (Z4):** N rows generated as N parallel `generate_json` calls, capped at 8 concurrent.
- **UX:** spinner *"Generating N rows…"* with a Cancel button (Z2). Rows append to the bottom; existing rows untouched.
- **Fallback:** button disabled with tooltip.

## 5. Operational Guts

### 5.1 Z2 — Per-call timeout and cancel

Every `generate_json` runs in a shared `ThreadPoolExecutor`. Caller passes a `timeout`; on expiry the service cancels the future, closes the HTTP connection (`client._client.close()`), records the timeout in `last_error`, and returns `None`. Ollama may keep generating into the void, but the caller unblocks within ~100ms.

**Default timeouts:**

| Surface | Timeout |
|---|---|
| Element match | 15s |
| Field value (single) | 15s |
| Recipe draft | 45s |
| Scenario suggester | 30s |
| Failure summarizer | 30s |
| Refine row | 30s |
| Append rows (per row) | 15s |

Long-running batch calls (Append Rows, Refine Row) render a Cancel button next to the spinner that triggers the same cancel path.

### 5.2 Z3 — Response cache

`functools.lru_cache(maxsize=512)` on a wrapper of `generate_json` keyed by `(sha256(prompt), model, json_format_flag, temperature)`. In-memory, per-process, LRU eviction.

**Invalidation:**

- Settings save → `AIService.reload()` clears the cache.
- Retry-after-validation-failure bypasses cache (`cache=False`); the mutated prompt would miss anyway, but we're explicit for safety.
- A `PROMPT_VERSION` constant in `ai_prompts.py` participates in the hash so bumping a prompt template invalidates all old entries.

Disk persistence is out of scope.

### 5.3 Z4 — Parallel batch generation

Shared `ThreadPoolExecutor(max_workers=8)` on the `AIService` singleton. Used by Append Rows. Healing element matches remain sequential (mid-run, one element at a time).

## 6. Data Shape Changes

### 6.1 New file: `data/settings.yaml`

```yaml
ai:
  host: "http://localhost:11434"
  model: "phi4:14b"
```

### 6.2 Healing log record — two optional new fields

```yaml
- step: 3
  level: 3              # 1=primary, 2=fallback, 3=AI
  matched_element: "Email"
  rationale: "Both fields collect an email address; placeholders match."   # NEW
  confidence: 0.91                                                          # NEW
```

Backward compatible — pre-existing records without these fields render `—` in the new **Why** column.

### 6.3 Run record — one optional new field

```yaml
ai_summary: "Run failed at step 4 (Submit). Two selectors auto-healed..."
ai_summary_model: "phi4:14b"
```

`ai_summary_model` is stored so we can detect a model change and regenerate the summary if needed (out of scope this iteration — just future-proofing).

### 6.4 Scenario — no schema change

Suggester populates existing fields (`name`, dataset row `ai_context`). The Suggest panel itself is UI-only state.

## 7. Affected Files

**New:**

- `core/ai_service.py` — the service.
- `core/ai_prompts.py` — extracted prompt builders (testable in isolation).
- `data/settings.yaml` — created on first launch.
- `tests/test_ai_service.py`
- `tests/test_scenario_suggester.py`
- `tests/test_failure_summarizer.py`
- `tests/test_refine_row.py`
- `tests/test_append_rows.py`

**Refactored (public contract preserved):**

- `core/ai_matcher.py` — thin adapter over `AIService`.
- `core/ai_test_data.py` — thin adapter over `AIService`.

**Minor changes:**

- `core/healer.py` — at line 74, store `rationale` and `confidence` on the heal record.
- `pages/5_settings.py` — full rewrite for the selector UX.
- `ui/scenarios/dataset_tab.py` — add Refine Row icon, Append Rows button, AI-disabled tooltips.
- `pages/4_reports.py` — Healing tab gets **Why** column; Run detail gets **AI summary** callout.
- `pages/3_scenarios.py` — Scenarios → New form gets the **Suggest scenarios with AI** panel.
- `README.md` — update Ollama section to recommend `phi4:14b` and point at the Settings selector.

**Untouched:**

- `core/test_case_generator.py` — the AI client contract it depends on is preserved.

## 8. Testing Strategy

### New tests

- **`tests/test_ai_service.py`** — mocked `ollama.Client`. Covers: settings.yaml round-trip, env-var fallback, `reload()`, timeout cancellation, cache hit/miss, `<think>` and ```json fence stripping, retry-on-invalid-JSON with violation feedback, model-change cache invalidation.
- **`tests/test_scenario_suggester.py`** — given a page fixture, asserts the service returns non-empty list with required fields (`name`, `ai_context`, `rationale`).
- **`tests/test_failure_summarizer.py`** — given a run record fixture, asserts a non-empty string ≤ ~80 words. Asserts caching: second call with same run+model is a cache hit.
- **`tests/test_refine_row.py`** — asserts DOM-locked fields cannot be mutated even when the mocked model "tries to" return new values for them. Asserts AI-fillable fields are mutated per the refine prompt.
- **`tests/test_append_rows.py`** — asserts N rows produced, asserts no exact duplicate of an existing `ai_context`, asserts parallel execution (count of concurrent calls observed in the mock).

### Updated tests

- `tests/test_ai_matcher.py`, `tests/test_ai_test_data.py`, `tests/test_ai_matcher_recipe.py` — patched to inject a fake `AIService`. Existing assertions remain valid because public contracts didn't change. They become adapter tests.

### Manual smoke on EC2

1. Pull `phi4:14b` on the EC2; launch the app.
2. Open Settings, confirm Phi-4 is selected, hit Test connection.
3. Run an existing scenario with the v8 form; time happy-path dataset generation.
4. Force a heal scenario; confirm rationale shows in Reports → Healing.
5. Open Scenarios → New, run Suggest on a scanned page, add one suggestion, run it.
6. Force a failure; open Reports → Run detail; confirm AI summary renders.
7. Refine one row in an existing dataset; confirm DOM-locked cells stay locked.
8. Append 3 rows with a batch context; confirm existing rows untouched.

## 9. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Phi-4 14B not pulled on fresh EC2; clients confused. | High | Settings page shows the exact `ollama pull phi4:14b` command when default missing. README updated. |
| Phi-4 14B too slow on smaller boxes. | Medium | Selector lets clients pick Granite 4 8B or Mistral 7B with no code change. |
| Markdown fences or `<think>` blocks break JSON parsing. | Medium | `generate_json` strips both defensively before `json.loads`. |
| Stale cache after prompt template change. | Low | `PROMPT_VERSION` constant participates in cache key. |
| Failure summarizer cost balloons if user opens many failed runs. | Low | Per-run cache; one summary per (run, model). |
| Streamlit re-runs spam new connections. | Low | `AIService` singleton in `st.session_state`. |
| Singleton conflicts across browser sessions on same EC2. | Low | One Streamlit process = one `AIService`; single-tenant deployment is in scope, multi-tenant is not. |

## 10. Implementation Sequencing

Each step is independently shippable.

1. Land `AIService` + `data/settings.yaml` + adapters in `ai_matcher.py` / `ai_test_data.py`. **Existing tests must stay green.** No new features yet.
2. Land Settings page selector (Section 3.3).
3. Land Healing Rationale surface (cheapest visible win).
4. Land Z2 timeout/cancel and Z3 cache (foundational for everything below).
5. Land Refine Row (Feature X).
6. Land Append Rows + Z4 parallelism (Feature Y).
7. Land Failure Summarizer.
8. Land Scenario Suggester.
