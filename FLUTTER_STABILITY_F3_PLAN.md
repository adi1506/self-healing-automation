# Flutter Battery — F3 Radio-vs-Radio Disambiguation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a position-swap fixture and an observe-only F3 test so the regression battery can detect (and classify) radio-vs-radio mis-heals — the disambiguation failure F2's vertical-only move cannot surface.

**Architecture:** A new fixture `flutter_v3_swap.html` is v1 with the two radios' x positions swapped (each label travels with its control); mobile and chevron stay put. The recorded v1 click targets `radio-individual` at x=240, but in v3 the node at x=240 is the foil — so position and identity disagree. Record v1, replay v3, and measure which of three modes current code produces (confident wrong heal / unresolved refusal / still correct). The test asserts only that replay produced an outcome; the disambiguation result is printed as `BASELINE_F3` and recorded in the baseline doc.

**Tech Stack:** Python 3, `pytest` (asyncio_mode=auto), Playwright (async), the existing `tests/dogfood/flutter_harness.py` record/replay helpers and the bbox-geometry resolver in `tests/test_flutter_battery.py`.

**Spec:** [FLUTTER_STABILITY_F3_DESIGN.md](FLUTTER_STABILITY_F3_DESIGN.md).

---

## Why this is observe-only

F1/F2 assert `heal_n == element_step_count`. F3 must NOT, because a swap can drive current code to an **unresolved refusal** on the radio click (a blocker step), which aborts replay before later steps run and legitimately lowers `heal_n`. That refusal is the spec's *preferred* "clean miss beats a wrong match" behavior (§1.2), so it must be observed, not failed. F3 therefore asserts only `outcome is not None` and prints the numbers; the Goal B plan's hard assertion is chosen against the measured mode.

---

## File Structure

- Create: `tests/fixtures/flutter_v3_swap.html` — v1 with the two radios' x positions swapped.
- Modify: `tests/dogfood/flutter_harness.py` — register `"v3"` in `_VARIANT_FILE` (one line).
- Modify: `tests/test_flutter_battery.py` — add `_NODE_CENTERS["v3"]` and append `test_F3_radio_swap_disambiguation`.
- Modify: `dogfood-output/flutter-baseline.md` — append the measured F3 section.

---

## Task 1: Swap fixture (`flutter_v3_swap.html`)

**Files:**
- Create: `tests/fixtures/flutter_v3_swap.html`

- [ ] **Step 1: Write the fixture**

Create `tests/fixtures/flutter_v3_swap.html` exactly. It is v1 with the two radio CONTROL nodes' `left` swapped (`radio-individual` 240→380, `radio-non-individual` 380→240) and each label moved to sit beside its control (`Individual` 276→416, `Non-Individual` 416→276). Mobile and chevron are unchanged from v1. `data-truth` stays bolted to each control; ordinal ids are renumbered as a Flutter rebuild would. The label text is NOT changed (this is a positional swap, not a relabel) — so position points at the foil while the label still names the true control, which is exactly the signal Goal B must learn to use.

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Flutter v3 swap fixture</title>
<style>
  html, body { margin: 0; padding: 0; }
  flt-semantics-host { display: block; position: relative; width: 1280px; height: 720px; }
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
<!-- v3: the two radios' x positions are SWAPPED vs v1 (each label travels with
     its control); mobile + chevron unchanged. radio-individual now sits where
     radio-non-individual was and vice versa. The recorded v1 click targeted
     radio-individual at x=240; here the node at x=240 is the FOIL. Position
     and identity disagree — the case that forces radio-vs-radio disambiguation. -->
<flt-semantics-host>
  <flt-semantics id="flt-semantic-node-3" style="left:0;top:0;width:1280px;height:720px;">
    <!-- Radio group: Individual — control moved 240 -> 380, label 276 -> 416 -->
    <flt-semantics id="flt-semantic-node-50" role="radio" aria-checked="false"
        data-truth="radio-individual"
        style="left:380px;top:290px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-51"
        style="left:416px;top:292px;width:90px;height:25px;"><span>Individual</span></flt-semantics>
    <!-- Radio group: Non-Individual — control moved 380 -> 240, label 416 -> 276 -->
    <flt-semantics id="flt-semantic-node-52" role="radio" aria-checked="false"
        data-truth="radio-non-individual"
        style="left:240px;top:290px;width:32px;height:32px;"></flt-semantics>
    <flt-semantics id="flt-semantic-node-53"
        style="left:276px;top:292px;width:130px;height:25px;"><span>Non-Individual</span></flt-semantics>
    <!-- Aria-labeled text input — unchanged from v1. -->
    <flt-semantics id="flt-semantic-node-60"
        style="left:760px;top:286px;width:417px;height:54px;">
      <input type="text" aria-label="Mobile No *" data-truth="mobile" />
    </flt-semantics>
    <!-- Textless icon button — unchanged from v1. -->
    <flt-semantics id="flt-semantic-node-70" role="button"
        data-truth="chevron"
        style="left:1140px;top:300px;width:24px;height:24px;"></flt-semantics>
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

- [ ] **Step 2: Verify the swap and the cross-variant `data-truth` parity**

The swap is the whole point, and `data-truth` must stay identical across all variants (tests resolve identity by it indirectly). Run:

```bash
python -c "from pathlib import Path; import re; v1=Path('tests/fixtures/flutter_v1.html').read_text(); v3=Path('tests/fixtures/flutter_v3_swap.html').read_text(); g=lambda s: sorted(re.findall(r'data-truth=\"([^\"]+)\"', s)); assert g(v1)==g(v3)==['chevron','mobile','radio-individual','radio-non-individual'], (g(v1),g(v3)); ri=re.search(r'data-truth=\"radio-individual\"[^>]*style=\"left:(\d+)px', v3.replace(chr(10),' ')); rn=re.search(r'data-truth=\"radio-non-individual\"[^>]*style=\"left:(\d+)px', v3.replace(chr(10),' ')); assert ri and ri.group(1)=='380', ('radio-individual left', ri and ri.group(1)); assert rn and rn.group(1)=='240', ('radio-non-individual left', rn and rn.group(1)); print('ok')"
```
Expected: `ok`

(The regex tolerates the attribute order in the fixture: `role` then `aria-checked` then `data-truth` then `style`. If it fails to match `left`, confirm the `data-truth` and `style` attributes are on the control element as written above.)

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/flutter_v3_swap.html
git commit -m "test(flutter): add v3 radio position-swap fixture"
```

---

## Task 2: Register the `v3` variant in the harness

**Files:**
- Modify: `tests/dogfood/flutter_harness.py`

- [ ] **Step 1: Add the `v3` entry to `_VARIANT_FILE`**

The current `_VARIANT_FILE` dict reads:

```python
_VARIANT_FILE = {
    "v1": _FIXTURES / "flutter_v1.html",
    "v2": _FIXTURES / "flutter_v2_relabel.html",
}
```

Change it to:

```python
_VARIANT_FILE = {
    "v1": _FIXTURES / "flutter_v1.html",
    "v2": _FIXTURES / "flutter_v2_relabel.html",
    "v3": _FIXTURES / "flutter_v3_swap.html",
}
```

No other harness change is needed — `replay_flutter(recording, variant, ...)` already passes the variant string straight through to the route installer.

- [ ] **Step 2: Import-smoke and confirm v3 resolves to a real file**

Run:
```bash
python -c "import tests.dogfood.flutter_harness as f; print(sorted(f._VARIANT_FILE)); print(f._VARIANT_FILE['v3'].exists())"
```
Expected:
```
['v1', 'v2', 'v3']
True
```

- [ ] **Step 3: Commit**

```bash
git add tests/dogfood/flutter_harness.py
git commit -m "test(flutter): register v3 swap variant in harness"
```

---

## Task 3: F3 observe-only test + `v3` geometry

**Files:**
- Modify: `tests/test_flutter_battery.py`

- [ ] **Step 1: Add the `v3` geometry to `_NODE_CENTERS`**

The current `_NODE_CENTERS` dict has `"v1"` and `"v2"` keys. Add a `"v3"` key reflecting the swapped control positions (centers = left+16, top+16 for radios; mobile/chevron unchanged from v1). Insert it as a new entry inside the `_NODE_CENTERS` dict, after the `"v2"` block:

```python
    "v3": {
        "radio-individual":     (396, 306),   # node-50: 380+16, 290+16 (moved right)
        "radio-non-individual": (256, 306),   # node-52: 240+16, 290+16 (moved left)
        "mobile":               (968, 313),   # node-60: 760+208, 286+27 (unchanged)
        "chevron":              (1152, 312),  # node-70: 1140+12, 300+12 (unchanged)
    },
```

The result must be a valid dict literal with three keys `"v1"`, `"v2"`, `"v3"`. Do not modify the existing `"v1"`/`"v2"` blocks or any helper function.

- [ ] **Step 2: Append the F3 test**

Append this test to the end of `tests/test_flutter_battery.py`. It reuses the existing helpers `record_flutter`, `replay_flutter`, `element_step_count`, `_recorded_truth`, `_truth_of`, `_healed_bbox` unchanged.

```python
@pytest.mark.asyncio
async def test_F3_radio_swap_disambiguation(tmp_path, capsys):
    """Record v1, replay against v3 (the two radios' x positions SWAPPED,
    each label travelling with its control; mobile + chevron unchanged).

    Unlike F1/F2, position and identity DISAGREE: the recorded click targeted
    radio-individual at x=240, but in v3 the node at x=240 is the foil
    (radio-non-individual). With textless radios the scorer collapses onto
    bbox (spec §1.3), so this is the first case that can surface a
    radio-vs-radio mis-heal — the §1.1 disambiguation failure Goal B fixes.

    OBSERVE-ONLY: we do NOT assert heal_n == element_step_count. An
    unresolved refusal (the spec-preferred 'clean miss beats a wrong match',
    §1.2) legitimately aborts replay at the blocker radio click and lowers
    heal_n. We assert only that replay produced an outcome and print
    BASELINE_F3, so the Goal B plan is written against the real failure mode.
    """
    rec = await record_flutter(str(tmp_path), variant="v1", name="F3")
    n_elem = element_step_count(rec)
    outcome, heal_n = await replay_flutter(
        rec, "v3", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))

    assert outcome is not None

    per_step = []
    for s in rec.steps:
        if s.element is None:
            continue
        recorded_truth = _recorded_truth(s, "v1")
        healed_onto = _truth_of(outcome, s.index, "v3")
        per_step.append({
            "index": s.index,
            "action": s.action,
            "recorded_role": s.element.attributes.get("role") or "",
            "recorded_truth": recorded_truth,   # against v1 (record variant)
            "status": outcome.step_results[s.index].get("status")
                      if s.index < len(outcome.step_results) else "missing",
            "healed_onto": healed_onto,         # against v3 (replay variant)
            "healed_bbox": _healed_bbox(outcome, s.index),
        })

    wrong_heals = [p for p in per_step
                   if p["healed_onto"] is not None
                   and p["recorded_truth"]
                   and p["healed_onto"] != p["recorded_truth"]]

    print("BASELINE_F3", {
        "element_steps": n_elem,
        "heal_attempts": heal_n,
        "per_step": per_step,
        "wrong_heal_count": len(wrong_heals),
        "failed_index": outcome.failed_step_index,
        "error": outcome.error,
    })
```

- [ ] **Step 3: Run the F3 test and capture the baseline**

Run:
```bash
python -m pytest tests/test_flutter_battery.py::test_F3_radio_swap_disambiguation -v -s
```
Expected: PASS (the only assertion is `outcome is not None`), with a `BASELINE_F3 {...}` line. **Copy that dict verbatim** — it is recorded in Task 4. Then classify the observed mode from the dict:
- **Mode 1 (confident wrong heal):** the radio step has `status: "passed"`, `heal_attempts == element_steps` (3), and the radio's `healed_onto == "radio-non-individual"` while `recorded_truth == "radio-individual"` → `wrong_heal_count >= 1`.
- **Mode 2 (unresolved refusal):** `failed_index` is the radio step's index, `heal_attempts < element_steps`, the radio step status is not `"passed"`, and `wrong_heal_count == 0` (no committed heal to be wrong).
- **Mode 3 (still correct):** radio `healed_onto == "radio-individual"`, `wrong_heal_count == 0`.

Record which mode in the report. Do NOT change the test to force any particular mode — the number is the result.

- [ ] **Step 4: Confirm the rest of the battery still passes**

Run:
```bash
python -m pytest tests/test_flutter_battery.py -v -s
```
Expected: F0, F1, F2, F3 all PASS, with BASELINE_F1/F2/F3 lines. (F0–F2 are unaffected; the `_NODE_CENTERS` edit only added a key.)

- [ ] **Step 5: Commit**

```bash
git add tests/test_flutter_battery.py
git commit -m "test(flutter): F3 observe-only radio-swap disambiguation baseline"
```

---

## Task 4: Record the F3 baseline + mode classification

**Files:**
- Modify: `dogfood-output/flutter-baseline.md`

- [ ] **Step 1: Append the F3 section**

Append the following section to the end of `dogfood-output/flutter-baseline.md`, filling in `<PASTE …>` with the verbatim `BASELINE_F3` dict from Task 3 Step 3 and `<MODE …>` with the classified mode and a one-line reading. Do not invent numbers — use the real captured output.

```markdown
## F3 — radio-vs-radio disambiguation (record v1, replay v3: the two radios' x positions swapped)

```
<PASTE THE VERBATIM BASELINE_F3 {...} LINE HERE>
```

- **Why this case exists:** F2's move is vertical-only and never targets the foil radio, so its `wrong_heal_count: 0` cannot detect a radio-vs-radio mis-heal. In v3 the recorded `radio-individual` click (x=240) lands on the foil's position, so position and identity disagree — the first case that forces disambiguation.
- **Observed mode:** <MODE 1 confident-wrong-heal / 2 unresolved-refusal / 3 still-correct> — <one-line reading: e.g. "radio-individual click healed onto radio-non-individual (wrong_heal_count=1): current bbox-only scoring follows position, not identity" OR "replay refused to heal the ambiguous radio click and aborted at step N (the spec-preferred clean miss): wrong_heal_count=0, failed_index=N">.
- **Goal B target:** after sibling-text association, the radio click must heal onto `radio-individual` (the label that travelled with it) → `wrong_heal_count: 0` AND `healed_onto: radio-individual`. F2's `wrong_heal_count: 0` must not regress.
```

- [ ] **Step 2: Commit**

```bash
git add dogfood-output/flutter-baseline.md
git commit -m "docs(flutter): record F3 radio-swap disambiguation baseline"
```

---

## Self-Review (completed)

**Spec coverage (FLUTTER_STABILITY_F3_DESIGN.md):**
- §3.1 swap fixture → Task 1 (full HTML, swap verified in Step 2).
- §3.2 harness `v3` registration → Task 2.
- §3.3 `_NODE_CENTERS["v3"]` + observe-only test asserting only `outcome is not None` → Task 3.
- §3.4 baseline-doc addendum with mode classification → Task 4.
- §4 before/after comparison → NOT an F3 task; it is a standing requirement on the Goal A/B fix plans, restated in the Execution Handoff below so it is not lost.
- §5 risks (refusal vs wrong-heal, spatial-only, tolerance) → reflected in the observe-only encoding (Task 3) and mode classification (Tasks 3–4); no code tasks needed.

**Placeholder scan:** The only intentional fill-ins are the verbatim measured `BASELINE_F3` dict and the mode classification in Task 4 — these are *outputs* of running Task 3, not unspecified design. Every code/HTML step contains complete content.

**Type/name consistency:** `record_flutter`, `replay_flutter`, `element_step_count`, `_recorded_truth(step, variant)`, `_truth_of(outcome, idx, variant)`, `_healed_bbox(outcome, idx)`, `_NODE_CENTERS`, `_VARIANT_FILE` all match the names in the current `tests/test_flutter_battery.py` and `tests/dogfood/flutter_harness.py`. `replay_flutter` returns `(outcome, heal_n)`. The `"v3"` key is used identically in the fixture mapping (Task 2) and the geometry map (Task 3). Node centers in Task 3 match the fixture coordinates in Task 1 (radio-individual 380→396, radio-non-individual 240→256).

**Known assumption to verify during execution:** that the swap actually drives a measurable outcome (mode 1 or 2) rather than mode 3. If current code somehow still heals correctly (`wrong_heal_count: 0`, `healed_onto: radio-individual`), record that as mode 3 and escalate to the controller — it would mean the swap is not adversarial enough and a harder stressor (e.g. recording on the foil radio, or a horizontal move that also collapses the bbox gap) is needed before Goal B has a target. Do NOT weaken the resolver or fixtures to manufacture a wrong heal.

---

## Execution Handoff

After this plan runs and F3's mode is recorded, the **Goal A (speed)** and **Goal B (accuracy)** plans are written. Per [FLUTTER_STABILITY_F3_DESIGN.md](FLUTTER_STABILITY_F3_DESIGN.md) §4, each of those plans MUST end with a step that re-runs the full battery and presents a clear **before → after** comparison (table or explicit deltas): F1 `heal_attempts` 3 → ~0 (Goal A); F3 `wrong_heal_count`/`healed_onto` → 0 wrong + correct (Goal B); F2 `wrong_heal_count` 0 → still 0 (no regression).
