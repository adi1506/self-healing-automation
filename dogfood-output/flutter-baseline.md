# Flutter Stability — Step 0 Baseline

Captured: 2026-05-30. Battery: `tests/test_flutter_battery.py` (F0/F1/F2) against
local `flt-semantics` fixtures (`tests/fixtures/flutter_v1.html`,
`flutter_v2_relabel.html`) via `tests/dogfood/flutter_harness.py`. No production
code was changed in Step 0; these are baseline numbers on current code, against
which the Goal A (speed) and Goal B (accuracy) plans are written.

## F1 — heal frequency + disambiguation (record v1, replay v1 unchanged)

```
BASELINE_F1 {'element_steps': 3, 'heal_attempts': 3, 'radio_step_index': 0, 'radio_healed_onto': 'radio-individual', 'radio_healed_bbox': {'x': 240, 'y': 290, 'width': 32, 'height': 32}, 'statuses': ['passed', 'passed', 'passed'], 'failed_index': None}
```

- **heal_attempts vs element_steps:** 3 vs 3 — heal fires on EVERY element-bearing
  Flutter step (ordinal locators are stripped, fast path misses, `attempt_heal` runs).
  This is the speed baseline Goal A must drive toward ~0 on unchanged replay.
- **radio_healed_onto:** radio-individual — on the unchanged page the radio click
  resolves to the correct radio, so unchanged-page disambiguation is already correct here.

## F2 — relabel/move (record v1, replay v2: labels rephrased, radios moved +40px, ids renumbered)

```
BASELINE_F2 {'element_steps': 3, 'heal_attempts': 3, 'per_step': [{'index': 0, 'action': 'click', 'recorded_role': 'radio', 'recorded_truth': 'radio-individual', 'status': 'passed', 'healed_onto': 'radio-individual', 'healed_bbox': {'x': 240, 'y': 330, 'width': 32, 'height': 32}}, {'index': 1, 'action': 'fill', 'recorded_role': '', 'recorded_truth': 'mobile', 'status': 'passed', 'healed_onto': 'mobile', 'healed_bbox': {'x': 760, 'y': 326, 'width': 417, 'height': 54}}, {'index': 2, 'action': 'click', 'recorded_role': 'button', 'recorded_truth': 'chevron', 'status': 'passed', 'healed_onto': 'chevron', 'healed_bbox': {'x': 1140, 'y': 340, 'width': 24, 'height': 24}}], 'wrong_heal_count': 0, 'failed_index': None, 'error': None}
```

- **heal_attempts vs element_steps:** 3 vs 3 — same heal-on-every-step behavior.
- **wrong_heal_count:** 0 — across the 3 controls, each healed onto its matching
  logical identity (recorded-against-v1 vs healed-against-v2) despite the relabel/move.
- **per_step healed_onto:** radio-individual→radio-individual, mobile→mobile,
  chevron→chevron.

## Method notes / caveats (read before writing Goal A/B plans)

- **Identity is resolved by bbox geometry, not `data-truth`.** `buildFingerprint`
  (`core/capture/inject.js`) captures a fixed attribute allowlist that does not include
  `data-truth`; only `bbox` survives onto fingerprints. The battery therefore resolves which
  fixture node a step recorded/healed onto by nearest-center bbox match against known fixture
  geometry (`_NODE_CENTERS` in the test). This is test-only; no production code changed.
- **F2's move is vertical-only and does not exercise radio-vs-radio confusion.** radio-individual
  keeps the same x across v1/v2 and the foil radio (radio-non-individual) is never the recorded
  target in this flow, so `wrong_heal_count: 0` is a clean baseline but NOT a hard guard against
  left/right radio mis-heals. Closing that gap (a flow that records on the foil radio, and/or a
  horizontal move) is for the Goal B accuracy plan.
- **The one certain assertion** (heal fires on every element step → `heal_n == element_step_count`)
  is asserted in F1 and F2; everything else here is a measured observation.

## F3 — radio-vs-radio disambiguation (record v1, replay v3: the two radios' x positions swapped)

```
BASELINE_F3 {'element_steps': 3, 'heal_attempts': 3, 'per_step': [{'index': 0, 'action': 'click', 'recorded_role': 'radio', 'recorded_truth': 'radio-individual', 'status': 'passed', 'healed_onto': 'radio-non-individual', 'healed_bbox': {'x': 240, 'y': 290, 'width': 32, 'height': 32}}, {'index': 1, 'action': 'fill', 'recorded_role': '', 'recorded_truth': 'mobile', 'status': 'passed', 'healed_onto': 'mobile', 'healed_bbox': {'x': 760, 'y': 286, 'width': 417, 'height': 54}}, {'index': 2, 'action': 'click', 'recorded_role': 'button', 'recorded_truth': 'chevron', 'status': 'passed', 'healed_onto': 'chevron', 'healed_bbox': {'x': 1140, 'y': 300, 'width': 24, 'height': 24}}], 'wrong_heal_count': 1, 'failed_index': None, 'error': None}
```

- **Why this case exists:** F2's move is vertical-only and never targets the foil radio, so its `wrong_heal_count: 0` cannot detect a radio-vs-radio mis-heal. In v3 the recorded `radio-individual` click (x=240) lands on the foil's position, so position and identity disagree — the first case that forces disambiguation.
- **Observed mode:** Mode 1 (confident-wrong-heal) — the `radio-individual` click (index 0) healed onto `radio-non-individual` (`wrong_heal_count: 1`, `healed_bbox` x=240 = the foil's swapped-in position) and committed it as `status: passed` with `heal_attempts == element_steps == 3` and `failed_index: None`. Current bbox-only scoring follows position, not identity: it grabs the nearest node to the recorded click and never detects that the identity is wrong.
- **Goal B target:** after sibling-text association, the radio click must heal onto `radio-individual` (the label that travelled with it) → `wrong_heal_count: 0` AND `healed_onto: radio-individual`. F2's `wrong_heal_count: 0` must not regress.
