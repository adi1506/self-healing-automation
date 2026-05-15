# Scenario Recording — Design

**Date:** 2026-05-15
**Status:** Draft for review
**Scope:** Replace scan-driven scenario authoring with browser-based recording as the primary input. The recorder captures a happy-path user journey through Chromium, stores rich multi-attribute element fingerprints, and seeds AI generation of multiple success and failure test cases per recording. Healing operates on the captured fingerprints.

## 1. Problem

Today, scenarios are built by crawling pages, scanning every form field on each page, and asking the AI to assemble plausible scenarios from that flat field list. This produces three pain points:

- **The scanner captures everything; we only need the fields a real test touches.** Inflates storage, dilutes healing signal, generates ghost fields that AI invents test data for.
- **Authentication, dynamic fields, page transitions, and inter-field dependencies have to be inferred** by AI or manually wired through the Steps editor. Brittle and labor-intensive.
- **One recording per scenario is the natural unit, not one scan.** The user's actual flow is the source of truth; pattern inference from real values is more reliable than guessing from a `<input type="text">` tag.

Competitor research (Testim, mabl, Functionize, Applitools) confirms multi-attribute element fingerprints + recorder-as-primary-input is the industry direction. None of them does AI-generated negative test variants from one happy recording — that is this project's differentiator.

## 2. Goals

- One **Recording** is the primary input — a captured happy-path journey through the target app's UI.
- A **Scenario** contains one or more Recordings + AI-generated test cases derived from those Recordings.
- Element capture is rich (multi-attribute fingerprint, 20+ attributes) so healing has signal to work with when locators break.
- Dynamic fields, page transitions, and inter-field dependencies are captured naturally because the recorder observes them happening in real time.
- Authentication is handled by **`storageState` reuse**, refreshed by a human at session start when expired.
- One Recording seeds many AI-generated test cases (success variants + failure variants).
- Server-side rejection patterns are captured during recording so AI failure variants are grounded in real validation rules, not just HTML5 constraints.

## 3. Non-goals

- **EC2 compatibility.** Target deployment is a Windows VM where the user RDPs in and uses the app locally. No browser streaming, no Chrome extension, no WebRTC. (If a network-accessible deployment is needed later, the capture engine is reusable; only the shell changes.)
- **Fully autonomous scheduled runs.** Auth is human-in-the-loop at session start; scheduled-with-no-human runs are out of scope.
- **Solving CAPTCHA/2FA programmatically.** The human solves these during login.
- **Multi-browser support.** Chromium only.
- **Removing the existing scanner/crawler.** Kept as a secondary tool for ad-hoc field discovery; not the primary scenario input.
- **Cross-application scenarios.** A scenario records against one application.

## 4. Architecture overview

Three subsystems, mostly new:

1. **Capture Engine** (`core/capture/`) — injected JavaScript that runs inside the recorder Chromium. Listens to user interactions, builds element fingerprints, tracks DOM mutations between steps, and exposes the captured timeline back to Python via a CDP-bound function.
2. **Recorder** (`core/recorder.py`) — Python side. Launches headed Chromium via Playwright, injects the capture engine on every navigation, owns the recording session lifecycle, persists the resulting Recording JSON.
3. **Auth Session Manager** (`core/auth_session.py`) — owns the per-application `storageState`. Provides the human-in-the-loop refresh flow when expired or missing.

Existing subsystems extended, not replaced:

- `core/scenarios.py` — `Scenario` gets new `kind="recorded"`, plus `recordings: list[Recording]` and `ai_test_cases: list[TestCase]`.
- `core/healer.py` — keeps its 3-phase match logic. Inputs change from scanner-style locator chains to recorder fingerprints (richer; healer benefits without algorithm change).
- `core/test_case_generator.py` — gains a new entry point: generate test cases from a Recording rather than from a scanned page. The existing 4-layer field resolver becomes a fallback only.
- `core/ai_service.py` — new prompt: `generate_test_cases_from_recording()`.
- `core/setter.py` / `core/runner_utils.py` — replay loop walks recorded steps, applying test-case data overrides; uses the fingerprint for find-element instead of the legacy locator chain.

UI:

- New Streamlit page: `pages/6_recordings.py` for managing applications and login recordings.
- Scenario detail (`ui/scenarios/detail.py`) gains a **Recordings** tab and a **Test Cases** tab. Existing Steps + Dataset tabs are deprecated for `kind="recorded"` scenarios (kept for backwards compatibility on single-page/multi-page scenarios).

## 5. Data model

### 5.1 Application

A new top-level entity. `core/applications.py` (new file).

```python
@dataclass
class Application:
    id: str                            # uuid
    name: str                          # "FinnOne Neo", "HDB Financial"
    base_url_pattern: str              # used to match a target URL to this app
    login_recording_id: str | None     # reference to the Recording that performs login
    storage_state_path: str | None     # encrypted file path, relative to data dir
    storage_state_captured_at: datetime | None
    storage_state_expires_at: datetime | None  # estimated from cookie max-age
    success_signal: SuccessSignal | None       # how to detect "I'm logged in"
```

Stored in `data/applications/<id>.yaml`. The `storage_state_path` points to `data/storage_states/<id>.enc`.

### 5.2 Recording

```python
@dataclass
class Recording:
    id: str
    name: str                          # user-supplied
    kind: Literal["login", "scenario"]
    application_id: str
    created_at: datetime
    start_url: str
    steps: list[Step]
    success_signal: SuccessSignal | None  # only for login recordings
```

Stored inline in the Scenario YAML for `kind="scenario"` Recordings; stored under `data/applications/<app_id>/login_recording.yaml` for `kind="login"` (so it can be reused across all scenarios for that app).

### 5.3 Step

```python
@dataclass
class Step:
    index: int
    action: Literal["fill", "click", "select", "check", "uncheck", "press", "navigate", "wait"]
    element: ElementFingerprint | None   # None for navigate/wait
    value: str | None                    # filled text, selected option, key pressed
    timestamp_ms: int                    # offset from recording start
    revealed_elements: list[str]         # element fingerprint ids that became visible/enabled after this step
    hidden_elements: list[str]           # element fingerprint ids that became hidden/disabled
    network: list[NetworkCapture]        # HTTP traffic tagged to this step
    error_elements: list[ElementFingerprint]  # error/warning nodes that appeared within 2s after this step
```

`revealed_elements` and `hidden_elements` together form the **DOM diff** that solves the inter-dependent-fields problem: at replay time, before executing step N+1, the runner waits for the elements step N revealed.

### 5.4 ElementFingerprint

```python
@dataclass
class ElementFingerprint:
    id: str                              # uuid, stable across the recording — same physical element gets the same id across every step that touches it (dedup key: xpath + neighborhood_signature)
    primary_locator: dict                # {strategy: "id"|"data-testid"|"name"|"css"|"xpath", value: str}; same shape as today's scanner output
    fallback_locators: list[dict]        # ordered alternates for healing, same shape
    attributes: dict                     # see below
    page_context: dict                   # url, section heading, fieldset legend
```

The injected JS assigns a stable `id` to each interactive element on first encounter, keyed by its xpath + neighborhood signature. Subsequent events targeting the same element reuse that id, so `Step.revealed_elements` / `Step.hidden_elements` can reference the same element across the timeline.

`attributes` payload (captured by injected JS):

```yaml
tag: input
type: text
id: pan
name: pan
class: form-control required-field
placeholder: "Enter PAN"
aria_label: "PAN number"
role: textbox
text_content: ""
nearest_label_text: "PAN"
nearest_landmark_text: "Personal Details"
sibling_text_before: ""
sibling_text_after: "(10 alphanumeric characters)"
bbox: {x: 120, y: 340, width: 280, height: 32}
html5_constraints:
  pattern: "[A-Z]{5}[0-9]{4}[A-Z]{1}"
  required: true
  maxlength: 10
  minlength: 10
autocomplete: ""
xpath: "//form[@id='kyc']/div[3]/input[@name='pan']"
css_path: "form#kyc > div:nth-child(3) > input[name='pan']"
neighborhood_signature: "input.required-field [pan]"  # hash of nearby siblings
```

20+ attributes. Healer matches across all of them in priority order.

### 5.5 SuccessSignal

```python
@dataclass
class SuccessSignal:
    url_pattern: str                     # substring or regex the post-login URL contains
    required_elements: list[ElementFingerprint]   # at least one must be visible
    forbidden_elements: list[ElementFingerprint]  # none may be visible (e.g., login form)
    captured_at: datetime
```

### 5.6 NetworkCapture

```python
@dataclass
class NetworkCapture:
    url: str
    method: str
    status: int
    request_body: str                    # truncated to 4 KB
    response_body: str                   # truncated to 4 KB
    response_headers: dict
```

Captured via CDP `Network.requestWillBeSent` / `Network.responseReceived`. Tagged to the step that triggered the request.

### 5.7 TestCase

```python
@dataclass
class TestCase:
    id: str
    name: str
    seed_recording_id: str
    mode: Literal["success", "failure"]
    data_overrides: dict[str, str]       # element_fingerprint_id -> value override
    expected_outcome: Literal["pass", "fail"]
    expected_rejection_field: str | None # for failure mode: which field should reject
    rationale: str                       # AI's explanation of what this case tests
```

A TestCase doesn't have its own step list. It points to a seed Recording and overrides values at specific elements. Replay walks the seed's steps, substituting overridden values.

### 5.8 Scenario (extended)

```python
@dataclass
class Scenario:
    name: str
    kind: Literal["single-page", "multi-page", "recorded"]  # "recorded" is new
    # existing fields preserved for single-page and multi-page kinds
    pages: list[dict] = field(default_factory=list)
    # new fields for "recorded" kind
    application_id: str | None = None
    recordings: list[Recording] = field(default_factory=list)
    ai_test_cases: list[TestCase] = field(default_factory=list)
```

## 6. Capture engine

A single JS file (`core/capture/inject.js`) injected via Playwright's `page.add_init_script()` on every navigation in the recording context.

Responsibilities:

1. **Event interception.** Listen for `click`, `input`, `change`, `submit`, `keydown` events on the document (capture phase). For each event, build an `ElementFingerprint` from the target.
2. **MutationObserver.** Single `MutationObserver` watching the whole subtree. Buffer mutations. When an event fires, flush the buffer and attribute the mutations to that event. Compute revealed/hidden fingerprint IDs by diffing pre-event vs. post-event visibility of all interactive elements.
3. **Network — handled Python-side, not by JS.** Playwright's `page.on("response")` is more reliable than fetch/XHR monkey-patching, and CSPs don't interfere with it. JS doesn't try to capture network.
4. **Communication channel.** The JS calls `window.__sha_record(event_payload)` for each event. Playwright exposes that name via `page.expose_function("__sha_record", on_event_callback)`. The Python side appends to the in-memory step list.

Fingerprint construction in JS (per element):

- Walk up the DOM tree to find the nearest `<label>`, `<legend>`, `<h1>-<h6>`, `[role=group]` — extract their text.
- Read all attributes, `getBoundingClientRect()`, `getComputedStyle()` for visibility and display.
- Generate XPath and CSS path (deterministic algorithm: prefer `id`, then `nth-of-type` rather than indexed nth-child to be less position-sensitive).
- Hash an N-character "neighborhood signature" from the tag+attributes of the 3 nearest siblings (above and below in DOM order). Used by healing as a tiebreaker.

## 7. Recording lifecycle

### 7.1 Starting a recording

UI flow (Streamlit, `pages/6_recordings.py` and Scenario detail):

1. User opens "New Scenario" → picks **Recorded** as the kind → chooses an existing Application or creates a new one (name + base URL).
2. If the Application has no `login_recording_id` yet, the UI redirects to "Record Login first." Otherwise proceeds.
3. If `storageState` is missing or expired:
   - UI shows a panel: *"This app's session needs refreshing. Click Start, log in normally in the browser that opens, then click 'I'm logged in now.'"*
   - User clicks **Start**. Backend launches headed Chromium at the Application's login URL.
   - User logs in, manually solving CAPTCHA / 2FA / SSO / "I'm not a robot" / module dropdown / whatever.
   - User clicks **"I'm logged in now"** in Streamlit.
   - Backend waits up to 5 seconds for activity to settle (no network for 2 s, no DOM mutations for 2 s) — this is **Option A+**.
   - Backend snapshots current URL + the top 5 newly-visible interactive elements ranked by attribute uniqueness (elements present after login that were NOT visible on the login page itself). These become the candidate `required_elements` for the success signal; user confirms or edits the picks.
   - Backend extracts `storageState` via `context.storage_state()`, encrypts, persists with estimated expiry.
4. If `storageState` is valid, backend launches a new context preloaded with it.
5. Backend navigates to the scenario's `start_url` (user supplies on the "New Recording" form).
6. Recording is now live. User interacts; every interaction streams into the timeline.
7. User clicks **Stop Recording** in Streamlit. Backend closes the page, persists the Recording JSON, returns user to the Scenario detail page.

### 7.2 Multiple recordings per scenario

The Scenario detail "Recordings" tab shows the list. User can:

- Add another recording (alternate happy path, intentional failure path, edge case path).
- Rename / delete recordings.
- Mark one as the **primary seed** (the one AI uses as the default base for test cases; others are reference data for failure pattern mining).

### 7.3 Server response capture

`page.on("response")` is registered for the entire recording context. Each response is tagged with the index of the current "in-flight" step (the most recent user action). Stored under `Step.network`. Truncated to 4 KB per body. Headers are filtered to a safelist (`content-type`, `location`, `set-cookie` — though set-cookie is stored encrypted alongside storageState, not in the recording).

When a step's network capture contains a 4xx response, the response body and any DOM error elements appearing within 2 s of the response are tagged as **rejection evidence** and made available to the AI for failure-variant grounding.

### 7.4 Encryption

`storageState` JSON is encrypted at rest using `cryptography.fernet` with a key derived from a value in `settings.yaml` (`storage_state_key`). Settings file is gitignored. Production-grade KMS is out of scope; revisit when the security model justifies it.

## 8. AI test case generation

New function in `core/ai_service.py`: `generate_test_cases_from_recording(recording, count_success=3, count_failure=3)`. Counts are caller-overridable; the UI Generate button exposes them as inputs with defaults of 3 each.

Inputs to the prompt:

- The Recording's step list (action + element name + value).
- Each filled element's HTML5 constraints (from fingerprint attributes).
- Each filled element's nearest_label_text + placeholder + sibling_text (semantic hints).
- Server rejection evidence from any 4xx responses captured during recording.
- Recorded happy-path values (one in-context example per field).

Output: `list[TestCase]` with `mode`, `data_overrides`, `expected_outcome`, `expected_rejection_field`, `rationale`.

The AI does **not** generate steps. It generates value overrides only. This keeps the prompt focused and the output deterministic in structure.

Failure-mode grounding sources, in priority order:

1. **Captured server rejection patterns** — if recording shows the server returns `422 {"errors": {"pan": "..."}}` for invalid PANs, AI prefers generating PAN failures over guessing.
2. **HTML5 constraints** — `pattern` violations, `required` empty, length violations.
3. **Semantic hints** — placeholder text like "must be 10 digits" parsed for constraints.
4. **LLM general knowledge** — last resort: known formats for common fields (email, phone, SSN, PAN, GST).

Generated test cases are saved to the Scenario; user can edit values in the Test Cases tab before running.

## 9. Replay

Replay uses the existing Playwright runner with two changes:

1. **Find-element call goes through `ElementFingerprint`** rather than the legacy 5-strategy locator chain. The fingerprint's `primary_locator` is tried first; on failure, `fallback_locators` in order; on full failure, the healer is invoked with the full fingerprint.
2. **Inter-step waits respect `revealed_elements`.** Before executing step N+1, the runner waits up to 10 s for any fingerprint in step N's `revealed_elements` to become visible. Eliminates "fill field B before B is rendered" races that today require manual `wait` steps.

Replay flow:

1. Load Application → resolve `storageState`. If missing/expired, prompt user for human-in-the-loop refresh.
2. New Playwright context with `storage_state=...`.
3. Navigate to seed Recording's `start_url`.
4. For each step in the seed Recording, in order:
   - Apply the test case's `data_overrides[step.element.id]` if present, else use the step's recorded value.
   - Find the element via fingerprint.
   - Perform the action.
   - Wait for any `revealed_elements` from this step to materialize before continuing.
5. After last step, capture outcome — for `failure` test cases, check whether the expected `expected_rejection_field` shows an error. For `success` test cases, check that no error elements are present and any user-defined post-condition is met.
6. Classify PASS / FAIL / HEALED / UNVERIFIED.

## 10. Healing integration

`core/healer.py`'s 3-phase matcher is preserved. Inputs change:

- Phase 1 (greedy locator match): now tries `primary_locator` then each `fallback_locator`.
- Phase 2 (attribute fingerprint similarity): scores against the full `attributes` dict; richer than today's scanner output → higher confidence at the same threshold.
- Phase 2b/3 (AI confirmation on gray-zone candidates): unchanged.

When the healer finds a new match, it writes back to the recording's `ElementFingerprint.primary_locator`; the previous locator is prepended to `fallback_locators`. The Scenario YAML is updated in place. Per-run heal events are logged in the run history so a regression can be traced to the run that introduced the new locator.

## 11. UI changes

### 11.1 New page: `pages/6_recordings.py`

Top-level page for managing Applications.

- List of applications (name, base URL, login status, storageState health: green/yellow/red).
- "New Application" button.
- Per-application: edit name, re-record login, force-expire storageState, view login Recording timeline.

### 11.2 Scenario detail tabs (recorded kind)

For `kind="recorded"` scenarios:

- **Recordings** tab: list of recordings, "Add Recording" button, per-recording timeline view (steps with action + element label + value).
- **Test Cases** tab: list of AI-generated test cases + a "Generate" button to (re)run AI generation. Per-test-case: edit overrides inline (a small data editor), run individually.
- **Runs** tab: unchanged from today, shows past run results.
- Steps tab and Dataset tab are hidden for this kind.

### 11.3 Scenario creation flow

New entry point: "Record a Scenario" button on the dashboard, alongside "Scan a URL." Picking Record → asks for Application → checks storageState → opens recorder → user records → returns to Scenario detail with one Recording attached and Test Cases tab empty (user clicks Generate when ready).

## 12. Migration

Existing single-page and multi-page scenarios continue to work unchanged. The `kind` field discriminates UI behavior. No data migration required. The scanner/crawler stays functional for ad-hoc field discovery.

## 13. Phasing

Implementation plan will sequence as:

1. Capture engine + injected JS + Python recorder (foundation).
2. Application + storageState management + human-in-the-loop auth.
3. Recording persistence + Scenario schema extension.
4. Replay using fingerprints (without test-case multiplication — just play back the seed Recording).
5. Healing integration on fingerprints.
6. AI test case generation from recordings.
7. UI: recordings page, scenario detail tabs.
8. Server response capture + failure-mode grounding.

Demoable milestone: end of step 4 — user can record a scenario and replay the same recording with healing.

## 14. Open questions

- **storageState expiry detection.** Cookie `max-age` gives an upper bound but apps often invalidate sessions server-side before that. Initial approach: trust `max-age`; on a 401/403/redirect-to-login mid-run, fail the run and force refresh on next start. Refine after observing real expiry behavior.
- **Re-recording when the app changes.** If the app adds a new required field, the recorder won't know — the user has to re-record. Out of scope: detecting "the page has new mandatory fields not in the recording" and warning.
- **Concurrent recordings.** Single-user assumed (RDP-in workflow). If multiple users ever share the VM, port allocation for recording Chromium needs a small lock — deferred.
