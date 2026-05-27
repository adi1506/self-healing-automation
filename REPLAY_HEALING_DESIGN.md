# Replay-time Healing — Design

Status: draft, awaiting review.
Scope: replay path only (recorder + `ElementFingerprint` data model). Not the scanner/Excel `Healer` — that stays as-is for the admin element-map flow.

---

## 1. Problem

`core.replay.find_element_by_fingerprint` ([core/replay.py:32](core/replay.py#L32)) tries the stored `primary_locator` + each `fallback_locator` (`id, data-testid, name, css, xpath`) and raises `ElementNotFound` when none match. When an app's selectors drift (id rename, name change, DOM restructure, label rephrase), every dependent step fails and the run halts.

The recorder already captures rich attributes per element ([core/capture/inject.js:136-176](core/capture/inject.js#L136-L176)) — 15 attribute fields + 2 page-context fields. Today nothing on the replay side uses any of them. Goal: make replay attempt a generic, attribute-driven heal whenever locator resolution fails, succeed the step when the heal is confident, and surface the change to the user.

Non-goals (this iteration): healing `success_signal.required_elements` / `forbidden_elements`; healing recordings whose action contract changes (e.g. `fill` → `select` because an input became a dropdown); cross-page heals (URL changed entirely).

## 2. Algorithm

### 2.1 Trigger

In `find_element_by_fingerprint`, on locator exhaustion **before** raising `ElementNotFound`, call the healer. If the healer returns a `Locator`, use it. If it returns `None`, raise `ElementNotFound` with the healer's diagnostic appended (top-3 candidate scores).

### 2.2 Live candidate enumeration

Inject a small JS snippet that runs in-page and returns one fingerprint per **interactive** element on the current `Page`. Interactive = matches `input, select, textarea, button, a[href], [role=button], [role=textbox], [role=combobox], [role=checkbox], [role=radio]`. Reuse `buildFingerprint` from [core/capture/inject.js:136](core/capture/inject.js#L136) — wrap it in a `window.__sha.scanAll()` helper that returns `[fp, fp, …]` for every match. **No** navigation, **no** state mutation. The page is mid-flow (logged in, partial form, modal open) and must stay that way.

Cost: ~30–80ms on a typical form. Acceptable on a failure path.

### 2.3 Hard guards (filter before scoring)

A candidate is **excluded outright** if any of:

1. **Action incompatibility.** Stored step's `action` cannot run against the candidate's `tag`/`type`:
   - `fill` requires `<input>` (non-checkbox/radio) or `<textarea>` or `[role=textbox]`.
   - `select` requires `<select>` or `[role=combobox]`.
   - `check`/`uncheck` requires `<input type=checkbox|radio>` or `[role=checkbox|radio]`.
   - `click`/`submit` accepts `<button>, <a>, [role=button], <input type=submit|button>`.

   Failing this guard isn't a heal — it's a recording-rewrite case. Surface as `ActionIncompatible` (subclass of `ElementNotFound`).

2. **URL mismatch on a same-app context.** If `fp.page_context.url` host+path differs from `page.url` host+path, refuse. URL drift = flow bug, not locator bug.

3. **Section scope (soft hard-guard).** If `fp.attributes.nearest_landmark_text` is non-empty AND at least one candidate shares that landmark, drop candidates that don't. If no candidate shares it, fall through without filtering (the section itself may have been renamed).

### 2.4 Scoring

For each remaining candidate, compute a weighted similarity in `[0, 1]`.

```
score = sum(weight_i * feature_score_i)
```

Feature scores in `[0, 1]`:

| Feature | Weight | Score function |
|---|---:|---|
| `autocomplete` match | 0.20 | 1.0 if both non-empty and equal; 0.5 if both empty; else 0.0 |
| `nearest_label_text` similarity | 0.20 | `SequenceMatcher(stored.lower(), candidate.lower()).ratio()` if either non-empty, else 0.5 |
| `name` attribute similarity | 0.15 | exact = 1.0; `SequenceMatcher` ratio if either non-empty; 0.5 if both empty |
| `id` attribute similarity | 0.10 | exact = 1.0; `SequenceMatcher` ratio (catches `phone` → `phone_number`) |
| `tag` + `type` match | 0.10 | 1.0 both equal; 0.5 tag-equal only; 0.0 otherwise. (Hard guard already drops the worst cases.) |
| `placeholder` similarity | 0.10 | `SequenceMatcher` ratio; 0.5 neutral if both empty |
| `aria_label` similarity | 0.05 | `SequenceMatcher` ratio; 0.5 neutral if both empty |
| `html5_constraints.pattern` match | 0.05 | 1.0 both non-empty and equal; 0.5 if both empty; 0.0 else |
| `role` match | 0.05 | 1.0 equal; 0.5 if both empty; 0.0 else |

Weights sum to 1.00. Initial values are heuristic — tune against a corpus of real recordings in iteration 2. [Likely good starting point; will need empirical tuning]

Explicitly **not** scored: `class` (Tailwind-class churn), `bbox` (layout-fragile), `xpath` / `css_path` / `neighborhood_signature` (already exhausted as locators), `text_content` (empty for inputs, equals label for buttons → redundant).

### 2.5 Decision

Sort candidates by score descending. Let `top` be the best, `runner_up` be the second-best (or `0.0` if none).

```
HIGH_CONFIDENCE = 0.80
GRAY_LOW       = 0.55
MARGIN_REQ     = 0.10   # top must beat runner_up by this much
```

- If `top.score >= HIGH_CONFIDENCE` AND `top.score - runner_up.score >= MARGIN_REQ`: **auto-heal**.
- Else if `top.score >= GRAY_LOW` AND AI matcher is available: ask `AIMatcher` to confirm. On `confidence >= 0.7`, **AI-confirmed heal**. Else: **unresolved**.
- Else: **unresolved**.

Thresholds are conservative on purpose — a wrong heal that silently passes a step is worse than an honest UNRESOLVED that the user investigates. [Certain — false positives are unrecoverable in a CI-style replay]

### 2.6 Output

The healer returns:

```python
@dataclass
class HealResult:
    locator: Optional[Locator]              # None if unresolved
    new_primary_locator: Optional[dict]     # picked from candidate's own fingerprint
    new_fallback_locators: list[dict]
    confidence: float                       # 0..1
    matched_by: list[str]                   # feature names that scored >= 0.8
    candidate_attrs: dict                   # candidate's full attributes (for audit)
    runner_up_score: float
    method: str                             # "auto" | "ai-confirmed" | "unresolved"
    diagnostics: str                        # top-3 candidates summary, used on failure
```

`new_primary_locator` / `new_fallback_locators` are picked from the matched candidate's fingerprint using the same priority as the recorder (`id → data-testid → name → css_path`), so the persisted heal aligns with how future recordings would store this element.

## 3. Module layout

```
core/
  replay.py            # unchanged surface; calls into healer on miss
  replay_healer.py     # NEW
  capture/
    inject.js          # extended: window.__sha.scanAll()
```

`core/replay_healer.py` — public API:

```python
async def attempt_heal(
    page: Page,
    stored: ElementFingerprint,
    action: str,
    *,
    ai_matcher: Optional[AIMatcher] = None,
    high_confidence: float = 0.80,
    gray_low: float = 0.55,
    margin_req: float = 0.10,
) -> HealResult: ...
```

Pure-async function. No global state. AI matcher injected, optional (offline runs heal heuristically only). Thresholds parameterized so we can tune per-recording or globally without code edits.

Replay integration ([core/replay.py:32-67](core/replay.py#L32-L67)) — minimal diff:

```python
# inside find_element_by_fingerprint, after the polling loop's final timeout
heal = await attempt_heal(page, fp, action=current_action, ai_matcher=...)
if heal.locator is not None:
    _record_heal_on_step(step_index, heal)   # see §4
    return heal.locator
raise ElementNotFound(f"... ; healer diagnostic: {heal.diagnostics}")
```

`find_element_by_fingerprint` needs to know `action` — currently it doesn't. Plumb `action` through as a parameter (called only from `execute_step` which has it).

## 4. Persistence

Two layers:

### 4.1 Per-run, in-memory heal cache

`replay_recording` maintains a `dict[str, dict]` mapping `fp.id → {primary_locator, fallback_locators}`. Populated on first successful heal for that id. `find_element_by_fingerprint` consults the cache before trying stored locators on subsequent steps that touch the same element. Lifetime = one run. Invalidated on navigation? **No** — the cache is keyed by element id, and the cache entry is the new locator on the current page; if the page navigates, the next step's element will have a different `fp.id` (different physical element), so the cache entry won't be hit. Simpler than tracking URL invalidation. [Certain]

### 4.2 Cross-run, written back to `recording.json`

**Default: off.** Heals live in the run report; user decides to apply.

Run report ([ui/scenarios/detail.py:915](ui/scenarios/detail.py#L915)) gains a section per heal showing: step index, old locator → new locator, confidence, which features matched, screenshot before+after. A single "Apply N heals to recording" button overwrites the affected `ElementFingerprint`s in the YAML, bumping `recording.created_at` or adding a `healed_at` field.

**Opt-in auto-persist.** A scenario-level flag `auto_persist_heals: bool = false`. When true, every heal whose `confidence >= 0.90` (note: stricter than the heal threshold) is written back at end-of-run, no UI prompt. Useful for nightly headless runs the user already trusts. Other heals still surface for review.

Rationale for default-off: an incorrect auto-heal becomes the new "truth" and silently corrupts the recording for every future run. Once the user has eyeballed a few heals and trusts the matcher on their app, they flip the flag. [Certain — irreversible mutation must be opt-in]

## 5. UI surface

### 5.1 `ReplayOutcome.step_results` schema extension

Add field `healed: dict | None`:

```python
{
    "old_primary_locator": {"strategy": "name", "value": "phone"},
    "new_primary_locator": {"strategy": "name", "value": "phone_number"},
    "new_fallback_locators": [...],
    "confidence": 0.91,
    "matched_by": ["nearest_label_text", "autocomplete", "tag+type"],
    "candidate_attrs": { ... },           # full fingerprint of matched candidate
    "runner_up_score": 0.42,
    "method": "auto",
}
```

`None` when no heal occurred. Backward-compatible — older runs / replays without healing serialize the field as missing.

### 5.2 `_render_step_report`

- A step that healed renders with a distinct icon (proposing 🩹) in place of ✅, label suffix `· healed`, and `confidence` shown inline.
- Expander shows `old → new` locator diff and `matched_by` features as chips.
- Failed steps where the healer ran but couldn't heal expand to show the top-3 candidates and their scores ("best candidate scored 0.42, below 0.55 threshold — was this the right field?").

### 5.3 Run-level summary

Above the per-step report, a banner:

> Replay completed: 6 passed, 1 healed, 0 failed. [Review heals] [Apply 1 heal to recording]

`[Apply N heals]` is the button gated on `auto_persist_heals=false`. Hidden when nothing to apply.

## 6. Failure surfaces (what users will see when it doesn't work)

| Situation | Behavior |
|---|---|
| Healer finds a match ≥ 0.80, margin ≥ 0.10 | Step passes, 🩹 icon, heal recorded |
| Healer finds 0.55–0.80 match, AI confirms | Step passes, 🩹 icon, `method: "ai-confirmed"`, AI rationale in expander |
| Healer finds 0.55–0.80, AI offline or unconfirmed | Step fails, error includes top-3 candidates |
| Best score < 0.55 | Step fails, error includes top-3 candidates |
| All candidates fail action-compat guard | Step fails with `ActionIncompatible: stored action 'fill' requires text input, but best candidate is <select>` — suggests re-recording |
| `page_context.url` host/path mismatches | Step fails with `UrlContextMismatch: recorded on /checkout, replaying on /error` |
| Element legitimately removed (no candidates pass section scope or above floor 0.3) | Step fails with `ElementNotFound: no candidate above floor — likely removed` |

Every failure mode carries enough context for the user to decide: re-record, fix the app, lower thresholds, or tag the recording dead.

## 7. Risks and explicit non-decisions

- **Weight tuning is heuristic v1.** The proposed weights are reasonable [Likely] but not validated against a real recording corpus. Plan: log every heal attempt's per-feature scores to `data/replay_runs/<id>/heal_log.jsonl`, then after ~20 real heals across diverse apps, fit weights empirically. Don't block v1 on this.

- **`SequenceMatcher` is character-similarity, not semantic.** `phone` vs `phone_number` scores ~0.59 — borderline. `username` vs `email` scores ~0.31 — fine. But `country` vs `nation` scores ~0.18 despite being semantically identical. Semantic similarity (via embedding) would help but is a separate component (cost, latency, offline-mode break). AI confirmation in the gray zone covers the hardest semantic cases for now. [Likely — accept the limitation in v1]

- **Multi-frame pages and shadow DOM not addressed.** The injected scan walks the top-level document only. Iframes and open shadow roots are blind. Out of scope for v1, document as known limitation.

- **success_signal healing deferred.** The same machinery would apply to `required_elements` / `forbidden_elements` but those run at end-of-replay, not per-step. Skip for v1.

- **Concurrent heals during the same step.** If a step's locator-resolution polling loop runs healer multiple times during 5s, we'd re-scan. Cheap-ish but wasteful. Mitigation: cache the live scan result for `poll_ms * N` within a single `find_element_by_fingerprint` call. Trivial.

- **Race with dynamic content.** Some forms render schema asynchronously (the test form does — see [core/replay.py:42-67](core/replay.py#L42-L67)). The existing 5-second polling loop already covers this for the locator path. The healer runs **after** that timeout, so by the time it scans, the page should be settled. If we move healing inside the polling loop, we'd thrash. Keep healing post-timeout. [Certain]

## 8. Implementation order

1. **Extend `inject.js`** with `window.__sha.scanAll()` — returns array of fingerprints for all interactive elements.
2. **Build `core/replay_healer.py`** — pure scoring + decision logic. Unit tests with synthetic fingerprint pairs covering each change class from §2.3. No browser needed for these tests.
3. **Wire into `find_element_by_fingerprint`** — plumb `action` through, call `attempt_heal` on miss, in-memory cache.
4. **Extend `ReplayOutcome.step_results`** schema + serialization.
5. **Update `_render_step_report`** to render the healed icon, expander details, and run-level banner.
6. **Apply-heals button** writing back to `recording.json`.
7. **Auto-persist flag** on scenarios (Settings page or scenario detail).
8. **(Iter 2)** Heal logging + weight retuning.
9. **(Iter 2)** Shadow DOM / iframe support if real recordings need it.

Items 1-5 are the MVP. 6-7 make it usable long-term. 8-9 are quality and reach.

## 9. Test plan

- **Unit tests for `replay_healer.py`** (no browser): table of `(stored_fp, candidates, expected_match_index_or_unresolved)` covering each change class in §2.3 — id rename, name rename, label rephrase, placeholder rephrase, structural drift, ambiguous-twin, removed-element, action-incompat.
- **Integration test** using `test_form/sample_form.html` (v1 schema) and `test_form/v2_id_changes.html` (v2 schema): record on v1, replay on v2, assert all steps either pass or heal, no unrelated failures.
- **Negative integration test**: a `v2_field_removed.html` variant — assert the removed field's step fails with `ElementNotFound: no candidate above floor`, not a wrong heal.
- **Ambiguity test**: two "Phone Number" fields in different sections — assert section-scope filter routes the heal to the correct one.

## 10. Removed-Field and Unresolved-Field Handling

The replay loop's skip-and-continue path fires on any heal verdict that
means "we couldn't find this element" — either `field_removed` or
`unresolved` — provided the step is skippable.

**Verdict classification** (in `core/replay_healer.select_match`):

- `field_removed` — top candidate scored below `REMOVED_FLOOR` AND the
  rename-guard didn't fire. The field looks genuinely gone from the page.
- `unresolved` — either (a) top score is in the gray zone
  `[GRAY_LOW, SCORE_THRESHOLD)` and AI didn't confirm, (b) the margin over
  the runner-up is too small, or (c) the rename-guard fired below
  `REMOVED_FLOOR` (drastic rename suspected, fail-safe).

`REMOVED_FLOOR` is tied to `GRAY_LOW` (no dead zone). Earlier versions set
`REMOVED_FLOOR = 0.40` and `GRAY_LOW = 0.55`, which left a `[0.40, 0.55)`
band where the healer returned `unresolved` but the skip-and-continue path
didn't fire — every removal whose top accidental candidate scored 0.40-0.55
became a hard run failure. The rename-guard is the safety mechanism;
tightening the floor was redundant. Removing the dead zone gets the skip
path back in business.

**Skip vs. fail decision** (in `core.replay._is_step_skippable`):

- **Skippable steps** (optional fills, optional selects/checks, required
  fills with no recorded value) → recorded in
  `ReplayOutcome.skipped_steps` with `reason` ∈ {`field_removed`,
  `unresolved`}, step status set to `skipped_removed` or
  `skipped_unresolved` respectively, loop continues.
- **Blocker steps** (click, submit, navigate, press, required fills with
  a recorded value, required selects/checks) → fail the run regardless of
  verdict. `field_removed` blockers also surface the "Add step manually"
  CTA in the UI.
- **Click-precursor exception**: a `click` whose target is an
  `input`/`textarea`/`select` AND whose immediate next step targets the
  *same* fingerprint id with a `fill`/`select`/`check`/`uncheck` is
  treated as a recorder focus artefact (tab/click into field before
  typing). It is skippable when the element is gone — the following step
  carries the real intent and is itself evaluated for skippability on the
  next loop iteration. Clicks on buttons, anchors, or any element with no
  same-id follow-up remain blockers (they advance the flow).

The report distinguishes the two skip reasons explicitly so users can tell
"the field is gone" from "the healer wasn't confident." A `field_removed`
skip is essentially safe — there's nothing on the page that resembles the
target. An `unresolved` skip is a softer signal: the page has *something*
that looks plausible but the healer wouldn't commit. Both surface the
healer's diagnostics in the per-step expander.

### Rename-guard rationale

A drastic rename (label, id, name all changed) can produce a top-candidate
score below 0.40 even though the field still exists. Two attributes survive
most renames:

- `autocomplete` — browser-defined vocabulary (`tel`, `email`, `new-password`,
  ...). Devs rarely change these because they break browser autofill.
- Non-generic `name` — the form-submission name. Often stable across UI
  redesigns because the backend depends on it.

If either matches a live candidate exactly, the field is renamed, not
removed, and we fail-safe with `unresolved`.

### Explicitly out of scope

Detecting fields that moved to a different page or step. We treat moved
fields as removed (the user will manually re-record on the new page).
