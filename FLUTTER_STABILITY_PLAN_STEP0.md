# Flutter Stability — Step 0 (Regression Battery) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CI-runnable Flutter regression battery that measures heal frequency, disambiguation, and relabel/move behavior against static `flt-semantics` fixtures — establishing the baseline numbers that Goal A (speed) and Goal B (accuracy) plans will be written against.

**Architecture:** Mirror the existing dogfood harness ([tests/dogfood/harness.py](tests/dogfood/harness.py)), which records/replays at a **fixed URL** with per-context route interception. New fixtures are local HTML mimicking Flutter's `flt-semantics` accessibility overlay (ordinal ids, sibling-split labels, textless icons). A new `flutter_harness.py` fulfills the fixed URL with chosen fixture HTML so `urls_compatible` passes between variants, and wraps `core.replay.attempt_heal` to count heal attempts — the headline speed metric, which does not exist today.

**Tech Stack:** Python 3, `pytest`, Playwright (async), the existing `core.recorder.RecorderSession` / `core.replay.replay_recording` pipeline.

**Spec:** [FLUTTER_STABILITY_DESIGN.md](FLUTTER_STABILITY_DESIGN.md) §3.

---

## Why this is its own plan

Goal A's and Goal B's pass/fail assertions are *baseline-relative* (e.g. "heal_count drops from N to ~0", "disambiguation accuracy rises from X% to 100%"). You cannot write those numbers honestly before measuring the current behavior. This plan produces the measuring instrument plus the first baseline run. The A/B plans are written afterward, anchored to the numbers this run prints.

The only assertion this plan makes with certainty (proven from code in spec §1.3): **heal fires on every element-bearing Flutter step**, because `_is_flutter_ordinal_locator` strips all ordinal locators and the fast path falls straight through to `attempt_heal`. Everything else is captured as a measured observation for the A/B plans.

---

## File Structure

- Create: `tests/fixtures/flutter_v1.html` — baseline Flutter-style overlay (two sibling-split radios, one aria-labeled input, one textless icon button).
- Create: `tests/fixtures/flutter_v2_relabel.html` — same controls, labels rephrased and positions shifted (axis 2: relabel/move).
- Create: `tests/dogfood/flutter_harness.py` — fixed-URL route fulfillment, recorder subclass, record/replay helpers, heal-attempt counter, a programmatic driver, baseline runner.
- Create: `tests/test_flutter_battery.py` — pytest entry points (`F0` smoke, `F1` baseline heal frequency + disambiguation, `F2` relabel). These are the asserted tests; the harness is exercised through them.

Fixtures and harness live beside the existing dogfood assets and follow their patterns (route interception, `RESULT_JSON`-style observations).

---

## Task 1: Baseline Flutter fixture (`flutter_v1.html`)

**Files:**
- Create: `tests/fixtures/flutter_v1.html`

- [ ] **Step 1: Write the fixture**

Create `tests/fixtures/flutter_v1.html` exactly:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Flutter v1 fixture</title>
<style>
  html, body { margin: 0; padding: 0; }
  flt-semantics-host { display: block; position: relative; }
  flt-semantics {
    display: block; position: absolute; box-sizing: border-box;
    overflow: visible;
  }
  flt-semantics[role="radio"] { border: 1px solid #888; border-radius: 50%; }
  flt-semantics[aria-checked="true"] { background: #1976d2; }
  flt-semantics input { width: 100%; height: 100%; box-sizing: border-box; }
  flt-semantics span { font: 14px system-ui; }
</style>
</head>
<body>
<!-- Mimics Flutter web: a <flt-semantics-host> accessibility overlay whose
     interactive nodes carry render-order ordinal ids (flt-semantic-node-N).
     The radio CONTROL nodes are textless; their visible label lives in an
     adjacent sibling node — the structure that breaks identity today. -->
<flt-semantics-host>
  <!-- Viewport-spanning root: must be ignored by the recorder/healer. -->
  <flt-semantics id="flt-semantic-node-0" style="left:0;top:0;width:1280px;height:720px;">
    <!-- Radio group: Individual -->
    <flt-semantics id="flt-semantic-node-70" role="radio" aria-checked="false"
        data-truth="radio-individual"
        style="left:240px;top:290px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-71"
        style="left:276px;top:292px;width:90px;height:25px;"><span>Individual</span></flt-semantics>
    <!-- Radio group: Non-Individual -->
    <flt-semantics id="flt-semantic-node-72" role="radio" aria-checked="false"
        data-truth="radio-non-individual"
        style="left:380px;top:290px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-73"
        style="left:416px;top:292px;width:130px;height:25px;"><span>Non-Individual</span></flt-semantics>
    <!-- Aria-labeled text input (Tier 1 in Goal A; has its own identity). -->
    <flt-semantics id="flt-semantic-node-80"
        style="left:760px;top:286px;width:417px;height:54px;">
      <input type="text" aria-label="Mobile No *" data-truth="mobile" />
    </flt-semantics>
    <!-- Textless icon button (no text, no aria): stays on the heal path. -->
    <flt-semantics id="flt-semantic-node-90" role="button"
        data-truth="chevron"
        style="left:1140px;top:300px;width:24px;height:24px;"></flt-semantics>
  </flt-semantics>
</flt-semantics-host>
<script>
  // Radios toggle aria-checked like real radio controls so the recorder
  // captures a meaningful interaction and replay can verify state.
  var radios = document.querySelectorAll('flt-semantics[role="radio"]');
  radios.forEach(function (r) {
    r.addEventListener('click', function () {
      radios.forEach(function (x) { x.setAttribute('aria-checked', 'false'); });
      r.setAttribute('aria-checked', 'true');
    });
  });
</script>
</body>
</html>
```

- [ ] **Step 2: Verify it loads and exposes the expected nodes**

Run:
```bash
python -c "from pathlib import Path; h=Path('tests/fixtures/flutter_v1.html').read_text(); assert 'flt-semantics-host' in h and h.count('role=\"radio\"')==2 and 'aria-label=\"Mobile No *\"' in h; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/flutter_v1.html
git commit -m "test(flutter): add baseline flt-semantics fixture v1"
```

---

## Task 2: Relabel/move fixture (`flutter_v2_relabel.html`)

**Files:**
- Create: `tests/fixtures/flutter_v2_relabel.html`

- [ ] **Step 1: Write the fixture**

Create `tests/fixtures/flutter_v2_relabel.html` — same controls, but labels rephrased ("Individual"→"Single Applicant", "Non-Individual"→"Joint Applicant", "Mobile No *"→"Phone Number *"), positions shifted by +40px y and the radios swapped in x order, and the ordinal ids renumbered (as Flutter does between builds):

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Flutter v2 relabel fixture</title>
<style>
  html, body { margin: 0; padding: 0; }
  flt-semantics-host { display: block; position: relative; }
  flt-semantics {
    display: block; position: absolute; box-sizing: border-box;
    overflow: visible;
  }
  flt-semantics[role="radio"] { border: 1px solid #888; border-radius: 50%; }
  flt-semantics[aria-checked="true"] { background: #1976d2; }
  flt-semantics input { width: 100%; height: 100%; box-sizing: border-box; }
  flt-semantics span { font: 14px system-ui; }
</style>
</head>
<body>
<flt-semantics-host>
  <flt-semantics id="flt-semantic-node-2" style="left:0;top:0;width:1280px;height:720px;">
    <!-- Radios moved down 40px; ids renumbered; labels rephrased. -->
    <flt-semantics id="flt-semantic-node-44" role="radio" aria-checked="false"
        data-truth="radio-individual"
        style="left:240px;top:330px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-45"
        style="left:276px;top:332px;width:130px;height:25px;"><span>Single Applicant</span></flt-semantics>
    <flt-semantics id="flt-semantic-node-46" role="radio" aria-checked="false"
        data-truth="radio-non-individual"
        style="left:420px;top:330px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-47"
        style="left:456px;top:332px;width:130px;height:25px;"><span>Joint Applicant</span></flt-semantics>
    <flt-semantics id="flt-semantic-node-50"
        style="left:760px;top:326px;width:417px;height:54px;">
      <input type="text" aria-label="Phone Number *" data-truth="mobile" />
    </flt-semantics>
    <flt-semantics id="flt-semantic-node-60" role="button"
        data-truth="chevron"
        style="left:1140px;top:340px;width:24px;height:24px;"></flt-semantics>
  </flt-semantics>
</flt-semantics-host>
<script>
  var radios = document.querySelectorAll('flt-semantics[role="radio"]');
  radios.forEach(function (r) {
    r.addEventListener('click', function () {
      radios.forEach(function (x) { x.setAttribute('aria-checked', 'false'); });
      r.setAttribute('aria-checked', 'true');
    });
  });
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the `data-truth` anchors are preserved across variants**

The `data-truth` attribute is the ground-truth identity used by tests to check whether a heal landed on the *correct* element across relabeling. It must be identical in both fixtures.

Run:
```bash
python -c "from pathlib import Path; import re; v1=Path('tests/fixtures/flutter_v1.html').read_text(); v2=Path('tests/fixtures/flutter_v2_relabel.html').read_text(); g=lambda s: sorted(re.findall(r'data-truth=\"([^\"]+)\"', s)); assert g(v1)==g(v2)==['chevron','mobile','radio-individual','radio-non-individual'], (g(v1),g(v2)); print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/flutter_v2_relabel.html
git commit -m "test(flutter): add relabel/move fixture v2"
```

---

## Task 3: Flutter harness (`flutter_harness.py`)

**Files:**
- Create: `tests/dogfood/flutter_harness.py`

This mirrors [tests/dogfood/harness.py](tests/dogfood/harness.py) but (a) fulfills a **fixed URL** with a chosen local fixture file so v1 and v2 share a host+path (heal `urls_compatible` passes), and (b) adds a heal-attempt counter.

- [ ] **Step 1: Write the harness module**

Create `tests/dogfood/flutter_harness.py` exactly:

```python
"""Flutter-fixture harness: record/replay against local flt-semantics HTML.

Mirrors tests/dogfood/harness.py, but instead of the live netlify site it
fulfills a FIXED URL with a chosen local fixture's HTML. Using one stable
URL for every variant is required: core.replay_healer.urls_compatible
compares host+path, so serving v1 and v2 from different file paths would
make every heal a URL-context mismatch. Route interception keeps the URL
constant while swapping the body.
"""
from __future__ import annotations
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright.async_api import async_playwright as _real_apw, BrowserContext

from core.capture import load_inject_js
from core.recording import Recording, save_recording
from core.recorder import RecorderSession
from core import replay as replay_mod

FIXED_URL = "http://flutter.fixture.test/app"
_FIXTURES = _ROOT / "tests" / "fixtures"
_VARIANT_FILE = {
    "v1": _FIXTURES / "flutter_v1.html",
    "v2": _FIXTURES / "flutter_v2_relabel.html",
}


async def _install_fixture_route(ctx: BrowserContext, variant: str) -> None:
    """Fulfill the top-level document at FIXED_URL with the variant's HTML."""
    html = _VARIANT_FILE[variant].read_text(encoding="utf-8")

    async def handler(route):
        req = route.request
        if req.url.rstrip("/") == FIXED_URL.rstrip("/"):
            await route.fulfill(status=200, content_type="text/html", body=html)
        else:
            await route.continue_()

    await ctx.route("**/*", handler)


class _FlutterRecorderSession(RecorderSession):
    """RecorderSession that fulfills FIXED_URL with a fixture before goto."""

    def __init__(self, *args, variant: str = "v1", **kwargs):
        super().__init__(*args, **kwargs)
        self._variant = variant

    async def start(self, start_url: str) -> None:
        self._start_url = start_url
        self._start_ts = time.time()
        self._pw = await _real_apw().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        ctx_kwargs = {}
        if self.storage_state:
            ctx_kwargs["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        await _install_fixture_route(self._context, self._variant)
        await self._context.add_init_script(load_inject_js())
        self.page = await self._context.new_page()
        await self.page.expose_function("__sha_record", self._on_event)
        await self.page.goto(start_url)
        await self.page.evaluate("() => window.__sha && window.__sha.attachListeners()")


async def _drive_flutter_v1(page) -> None:
    """Click the Individual radio, fill Mobile No, click the chevron.

    Uses the data-truth attribute to target nodes unambiguously at RECORD
    time. The recorder captures Flutter fingerprints (ordinal ids, etc.);
    these data-truth hooks are only for driving and for ground-truth
    assertions, never used by the recorder/healer themselves.
    """
    await page.wait_for_selector("flt-semantics-host", timeout=10000)
    await page.click('flt-semantics[data-truth="radio-individual"]')
    await page.fill('flt-semantics[data-truth="mobile"] input', "5551234567")
    await page.click('flt-semantics[data-truth="chevron"]')
    await page.wait_for_timeout(300)


async def record_flutter(scratch_dir: str, variant: str = "v1",
                         name: str = "flutter") -> Recording:
    """Record the v1 happy path. Saves to scratch_dir/recording.yaml."""
    session = _FlutterRecorderSession(
        application_id="flutter-fixture-app", headless=True, variant=variant)
    await session.start(start_url=FIXED_URL)
    try:
        await _drive_flutter_v1(session.page)
    finally:
        rec = await session.stop(name=name)
    out_path = Path(scratch_dir) / "recording.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_recording(str(out_path), rec)
    return rec


# --- Replay with fixed-URL fulfillment (mirror of harness._PWWrapper) ------

class _PWWrapper:
    def __init__(self, variant: str):
        self._variant = variant
        self._pw = None

    async def __aenter__(self):
        self._pw = await _real_apw().start()
        return _PWProxy(self._pw, self._variant)

    async def __aexit__(self, *a):
        await self._pw.stop()


class _PWProxy:
    def __init__(self, pw, variant):
        self._pw = pw
        self._variant = variant

    def __getattr__(self, name):
        if name == "chromium":
            return _BrowserTypeProxy(self._pw.chromium, self._variant)
        return getattr(self._pw, name)


class _BrowserTypeProxy:
    def __init__(self, bt, variant):
        self._bt = bt
        self._variant = variant

    async def launch(self, **kwargs):
        return _BrowserProxy(await self._bt.launch(**kwargs), self._variant)


class _BrowserProxy:
    def __init__(self, browser, variant):
        self._browser = browser
        self._variant = variant

    async def new_context(self, **kwargs):
        ctx = await self._browser.new_context(**kwargs)
        await _install_fixture_route(ctx, self._variant)
        return ctx

    async def close(self):
        await self._browser.close()


@contextmanager
def count_heal_attempts():
    """Wrap core.replay.attempt_heal to count invocations.

    attempt_heal is the full scan+score heal pass that fires whenever the
    fast path misses. Counting it is the headline speed metric (spec §3.2);
    no such counter exists in production code. Yields a one-key dict whose
    'n' is updated in place.
    """
    counter = {"n": 0}
    original = replay_mod.attempt_heal

    async def _wrapped(*args, **kwargs):
        counter["n"] += 1
        return await original(*args, **kwargs)

    replay_mod.attempt_heal = _wrapped
    try:
        yield counter
    finally:
        replay_mod.attempt_heal = original


async def replay_flutter(recording: Recording, variant: str, scratch_dir: str,
                         *, recording_path: Optional[str] = None):
    """Replay `recording` against the given fixture variant. Returns
    (ReplayOutcome, heal_attempt_count)."""
    original = replay_mod.async_playwright
    replay_mod.async_playwright = lambda: _PWWrapper(variant)
    try:
        with count_heal_attempts() as counter:
            outcome = await replay_mod.replay_recording(
                recording,
                recording_path=recording_path,
                headless=True,
                healing_enabled=True,
                screenshot_dir=str(Path(scratch_dir) / "screenshots"),
            )
        return outcome, counter["n"]
    finally:
        replay_mod.async_playwright = original


def element_step_count(recording: Recording) -> int:
    """Number of steps that carry an element (i.e. reach the heal path)."""
    return sum(1 for s in recording.steps if s.element is not None)
```

- [ ] **Step 2: Import-smoke the module**

Run:
```bash
python -c "import tests.dogfood.flutter_harness as f; print(f.FIXED_URL); print(sorted(f._VARIANT_FILE))"
```
Expected:
```
http://flutter.fixture.test/app
['v1', 'v2']
```

- [ ] **Step 3: Commit**

```bash
git add tests/dogfood/flutter_harness.py
git commit -m "test(flutter): fixed-URL record/replay harness with heal-attempt counter"
```

---

## Task 4: Smoke test — record + replay v1 (`F0`)

**Files:**
- Create: `tests/test_flutter_battery.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_flutter_battery.py`:

```python
"""Flutter regression battery (spec §3). Measures heal frequency,
disambiguation, and relabel/move behavior against flt-semantics fixtures.

These tests assert the ONE thing certain from code today — heal fires on
every element-bearing Flutter step — and print baseline observations the
Goal A / Goal B plans are written against.
"""
import asyncio
from pathlib import Path

import pytest

from tests.dogfood.flutter_harness import (
    record_flutter, replay_flutter, element_step_count,
)


def _truth_of(outcome, step_index):
    """Ground-truth data-truth of the element a step healed onto, if any.

    Reads the healed candidate's attributes captured in step_results. The
    healer copies the matched candidate's attributes; data-truth rides
    along on the live node so it is present on the chosen candidate.
    """
    if step_index >= len(outcome.step_results):
        return None
    r = outcome.step_results[step_index]
    healed = r.get("healed") or {}
    attrs = healed.get("candidate_attrs") or {}
    return attrs.get("data-truth")


@pytest.mark.asyncio
async def test_F0_record_replay_v1_smoke(tmp_path):
    rec = await record_flutter(str(tmp_path), variant="v1", name="F0")
    assert element_step_count(rec) >= 3, "expected radio + fill + chevron steps"
    outcome, heal_n = await replay_flutter(
        rec, "v1", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))
    assert outcome is not None
    assert heal_n >= 1, "Flutter steps must reach the heal path"
```

- [ ] **Step 2: Run it to confirm it fails (harness/fixtures wired end-to-end)**

Run:
```bash
python -m pytest tests/test_flutter_battery.py::test_F0_record_replay_v1_smoke -v
```
Expected on first run before fixtures/harness are correct: FAIL or ERROR (missing nodes, no steps recorded, or import error). Once Tasks 1–3 are in place: PASS.

- [ ] **Step 3: If it fails, debug fixture/driver interaction, not the assertion**

Common causes and fixes (do NOT weaken the assertion):
- `0 steps recorded` → the recorder's Flutter click path needs `flt-semantics-host` present and the click to land on a `flt-semantics`. Confirm `_drive_flutter_v1` selectors resolve: `python -m pytest -s` and check for `RESULT`/exceptions.
- `pytest-asyncio` not configured → ensure `pytest.ini` has `asyncio_mode = auto` (it exists in repo root; confirm it lists this mode).

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
python -m pytest tests/test_flutter_battery.py::test_F0_record_replay_v1_smoke -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_flutter_battery.py
git commit -m "test(flutter): F0 record+replay smoke against v1 fixture"
```

---

## Task 5: Baseline heal frequency + disambiguation (`F1`)

**Files:**
- Modify: `tests/test_flutter_battery.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_flutter_battery.py`:

```python
@pytest.mark.asyncio
async def test_F1_baseline_heal_frequency_and_disambiguation(tmp_path, capsys):
    """Record v1, replay v1 UNCHANGED.

    Certain assertion (spec §1.3): every element-bearing step heals, because
    all Flutter locators are ordinal and get stripped. heal_n must equal the
    element-step count — this is the speed baseline Goal A must drive to ~0.

    Disambiguation is recorded as an OBSERVATION (which radio the click
    healed onto) for the Goal B plan; not asserted here because current
    bbox-only behavior is exactly what we're measuring.
    """
    rec = await record_flutter(str(tmp_path), variant="v1", name="F1")
    n_elem = element_step_count(rec)
    outcome, heal_n = await replay_flutter(
        rec, "v1", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))

    # Find the radio-click step by recorded role.
    radio_idx = next(
        (s.index for s in rec.steps
         if s.element and (s.element.attributes.get("role") or "") == "radio"),
        None,
    )
    radio_healed_truth = _truth_of(outcome, radio_idx) if radio_idx is not None else None

    print("BASELINE_F1", {
        "element_steps": n_elem,
        "heal_attempts": heal_n,
        "radio_step_index": radio_idx,
        "radio_healed_onto": radio_healed_truth,   # want 'radio-individual'
        "statuses": [r.get("status") for r in outcome.step_results],
        "failed_index": outcome.failed_step_index,
    })

    # CERTAIN assertion: heal fires on every element-bearing step.
    assert heal_n == n_elem, (
        f"expected heal on every element step ({n_elem}), got {heal_n}")
```

- [ ] **Step 2: Run it**

Run:
```bash
python -m pytest tests/test_flutter_battery.py::test_F1_baseline_heal_frequency_and_disambiguation -v -s
```
Expected: PASS, and a `BASELINE_F1 {...}` line printed. Record the `heal_attempts`, `radio_healed_onto`, and `failed_index` values — these seed the Goal A/B plans.

- [ ] **Step 3: Commit**

```bash
git add tests/test_flutter_battery.py
git commit -m "test(flutter): F1 baseline heal-frequency + disambiguation observation"
```

---

## Task 6: Relabel/move baseline (`F2`)

**Files:**
- Modify: `tests/test_flutter_battery.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_flutter_battery.py`:

```python
@pytest.mark.asyncio
async def test_F2_relabel_move_baseline(tmp_path, capsys):
    """Record v1, replay against v2 (labels rephrased, radios moved, ids
    renumbered).

    Certain assertion: heal still fires on every element step (same ordinal
    strip). The RELABEL CORRECTNESS — did each control heal onto the node
    with the matching data-truth — is recorded as the baseline observation
    Goal B must improve, and as the 'zero wrong heals' bar Goal A's
    uniqueness guard must hold.
    """
    rec = await record_flutter(str(tmp_path), variant="v1", name="F2")
    n_elem = element_step_count(rec)
    outcome, heal_n = await replay_flutter(
        rec, "v2", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))

    per_step = []
    for s in rec.steps:
        if s.element is None:
            continue
        per_step.append({
            "index": s.index,
            "action": s.action,
            "recorded_role": s.element.attributes.get("role") or "",
            "recorded_truth": s.element.attributes.get("data-truth") or "",
            "status": outcome.step_results[s.index].get("status")
                      if s.index < len(outcome.step_results) else "missing",
            "healed_onto": _truth_of(outcome, s.index),
        })

    wrong_heals = [p for p in per_step
                   if p["healed_onto"] is not None
                   and p["recorded_truth"]
                   and p["healed_onto"] != p["recorded_truth"]]

    print("BASELINE_F2", {
        "element_steps": n_elem,
        "heal_attempts": heal_n,
        "per_step": per_step,
        "wrong_heal_count": len(wrong_heals),
        "failed_index": outcome.failed_step_index,
        "error": outcome.error,
    })

    # CERTAIN assertion: heal fires on every element-bearing step.
    assert heal_n == n_elem, (
        f"expected heal on every element step ({n_elem}), got {heal_n}")
```

- [ ] **Step 2: Run it**

Run:
```bash
python -m pytest tests/test_flutter_battery.py::test_F2_relabel_move_baseline -v -s
```
Expected: PASS, with a `BASELINE_F2 {...}` line. Record `wrong_heal_count` and the `per_step` `healed_onto` values — this is the relabel/move baseline the Goal B plan must improve and the Goal A uniqueness guard must not worsen.

- [ ] **Step 3: Commit**

```bash
git add tests/test_flutter_battery.py
git commit -m "test(flutter): F2 relabel/move baseline observation"
```

---

## Task 7: Normal-site regression guard + baseline capture

**Files:**
- Create: `dogfood-output/flutter-baseline.md`

- [ ] **Step 1: Confirm the existing static-HTML suite is unaffected**

The Flutter fixtures and harness are additive (new files only); no production code changed in this plan. Confirm the existing battery imports still resolve:

Run:
```bash
python -c "import tests.dogfood.run_tests, tests.dogfood.harness; print('existing suite imports ok')"
```
Expected: `existing suite imports ok`

- [ ] **Step 2: Run the full Flutter battery and capture the baseline**

Run:
```bash
python -m pytest tests/test_flutter_battery.py -v -s
```
Expected: all three tests PASS, with `BASELINE_F1` and `BASELINE_F2` lines.

- [ ] **Step 3: Record the baseline numbers**

Create `dogfood-output/flutter-baseline.md` and paste the printed `BASELINE_F1` and `BASELINE_F2` dicts verbatim, plus a one-line reading of each:
- F1 `heal_attempts` vs `element_steps` → confirms heal-on-every-field (the speed target).
- F1 `radio_healed_onto` → whether unchanged-page disambiguation is already correct.
- F2 `wrong_heal_count` and per-step `healed_onto` → the relabel/move accuracy baseline.

```bash
git add dogfood-output/flutter-baseline.md
git commit -m "docs(flutter): record Step 0 battery baseline numbers"
```

---

## Self-Review (completed)

**Spec coverage (spec §3):**
- §3.1 fixtures (v1 baseline, v2 relabel) → Tasks 1, 2.
- §3.2 metrics: heal_count → `count_heal_attempts` (Task 3) + F1/F2 assertions; disambiguation → F1 observation; relabel rate → F2 observation; normal-site regression → Task 7 Step 1.

**Placeholder scan:** No TBD/TODO. Every code step contains full file content or full appended function. The one deliberately-unasserted area (disambiguation/relabel correctness) is explicitly an *observation*, with the reason stated (baseline-relative, belongs to A/B plans) — not a placeholder.

**Type/name consistency:** `record_flutter`, `replay_flutter`, `element_step_count`, `count_heal_attempts`, `FIXED_URL`, `_VARIANT_FILE`, `_install_fixture_route`, `_truth_of`, `data-truth` are used identically across Tasks 3–7. `replay_flutter` returns `(outcome, heal_n)` everywhere it's called. The `data-truth` attribute name matches between fixtures (Tasks 1–2) and assertions (Tasks 4–6).

**Known assumption to verify during execution (Task 4 Step 3 covers it):** that driving `flt-semantics[role=radio]` clicks through the recorder produces element-bearing steps. If the recorder coalesces or drops any of the three interactions, adjust the `>= 3` smoke threshold in F0 to the actual count and keep F1/F2's `heal_n == n_elem` invariant (which is independent of the exact count).

---

## Execution Handoff

After this plan runs and the baseline is recorded, the **Goal A (speed)** and **Goal B (accuracy)** plans get written against the captured numbers.
