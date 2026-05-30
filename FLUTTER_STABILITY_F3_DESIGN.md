# Flutter Battery — F3 Radio-vs-Radio Disambiguation Case (Design)

Status: approved (brainstorming), awaiting spec review.
Date: 2026-05-30.
Depends on: Step 0 battery (`tests/test_flutter_battery.py`, `tests/dogfood/flutter_harness.py`, `tests/fixtures/flutter_v1.html`/`flutter_v2_relabel.html`, `dogfood-output/flutter-baseline.md`).
Spec context: `FLUTTER_STABILITY_DESIGN.md` §1.1 (disambiguation/relabel threat model), §1.2 (a clean miss beats a wrong match), §1.3 (textless radios collapse to bbox-only scoring), §3.2 (disambiguation/relabel metrics).

---

## 1. Problem

The Step 0 battery's F2 test reports `wrong_heal_count: 0`, but that zero is structurally guaranteed, not earned: F2's move is **vertical-only**, `radio-individual` keeps the same x across v1/v2, and the **foil radio (`radio-non-individual`) is never the recorded target.** So bbox-only scoring is never actually forced to choose between two textless radios. The battery therefore has **no test that can detect a radio-vs-radio mis-heal** — exactly the §1.1 disambiguation failure Goal B exists to fix.

Consequence: a Goal B pass criterion phrased as "drive `wrong_heal_count` to 0" would be hollow, because the baseline can't produce a non-zero value. We need a case where **position and identity disagree**, so the disambiguation behavior is forced into the open and *measured*.

We also do not yet know *which* failure mode current code exhibits, and the modes call for different Goal B work:
1. **Confident wrong heal** — bbox-only grabs the foil. The dangerous, unrecoverable case (§1.2). Goal B must add a signal (sibling-text association) that overrides position.
2. **Unresolved refusal** — the scorer's confidence gate rejects the ambiguous match; the radio click (a blocker step) fails and replay aborts. This is the spec's *preferred* "clean miss > wrong match" behavior (§1.2); the problem is then narrower (a refusal to heal, not a wrong action).
3. **Still correct** — current code somehow picks right.

F3's primary job is to **reveal which mode is real**, on current code, before any fix is planned.

---

## 2. Scope

In scope: one new fixture, one new observe-only test, two one-line registrations, and a baseline-doc addendum. **No production code changes.**

Out of scope: any fix (that is Goal B); visually-identical/adjacent-radio stressors (a different axis — not over-built until F3's number justifies it); horizontal-shift or distractor-radio variants.

---

## 3. Design

### 3.1 New fixture — `tests/fixtures/flutter_v3_swap.html`

Start from `flutter_v1.html`, then **swap the two radios' x positions, each label travelling with its control:**

- `radio-individual` control: x `240 → 380`; its "Individual" label moves alongside (`276 → 416`).
- `radio-non-individual` control: x `380 → 240`; its "Non-Individual" label moves alongside (`416 → 276`).
- `mobile` input and `chevron` button unchanged (isolate the radio variable).
- `data-truth` stays bolted to each control → cross-variant parity preserved: sorted set `{chevron, mobile, radio-individual, radio-non-individual}` identical to v1/v2.
- Ordinal `flt-semantic-node-N` ids renumbered, as a real Flutter rebuild does.
- `flt-semantics-host` keeps explicit `width/height` (the zero-height fix from Step 0).

Why this fixture: the recorded click targeted `radio-individual` at old x=240; after the swap the node at x=240 is the **foil**, while the "Individual" label has moved to x≈416. Position now points at the wrong radio; the label points at the right one. This makes F3 **red on bbox-only scoring and green once Goal B's sibling-text association is in** — the precise target Goal B needs.

### 3.2 Harness registration — `tests/dogfood/flutter_harness.py`

Add a `"v3"` entry to `_VARIANT_FILE` pointing at `flutter_v3_swap.html`. No other harness change (`replay_flutter` already takes a variant string).

### 3.3 Test — `test_F3_radio_swap_disambiguation` (append to `tests/test_flutter_battery.py`)

- Add `_NODE_CENTERS["v3"]` reflecting the swapped geometry: `radio-individual (396,306)`, `radio-non-individual (256,306)`, `mobile (968,313)`, `chevron (1152,312)`.
- Record v1, replay against `"v3"`.
- Build the same `per_step` structure as F2: `recorded_truth = _recorded_truth(s, "v1")` (record variant), `healed_onto = _truth_of(outcome, s.index, "v3")` (replay variant). Compute `wrong_heals` the same way (both sides non-None and differing).
- **Assert only `outcome is not None`.** Do *not* assert `heal_n == element_step_count` — mode 2 (unresolved refusal) legitimately aborts replay before later steps run, lowering `heal_n`, and that abort is itself a valid, spec-preferred outcome we want to observe rather than fail on.
- `print("BASELINE_F3", {...})` with `heal_n`, `element_steps`, `wrong_heal_count`, `failed_index`, `error`, `statuses`, and `per_step` (each entry incl. `recorded_truth`, `healed_onto`, `healed_bbox`, `status`).

By the user's "measure first" choice, F3 asserts nothing about disambiguation correctness yet; it captures the number.

### 3.4 Baseline-doc addendum — `dogfood-output/flutter-baseline.md`

After running F3, append an **F3 section** with the verbatim `BASELINE_F3` dict and a one-line classification of the observed mode (1 confident-wrong-heal / 2 unresolved-refusal / 3 still-correct). That classification is the explicit input to the Goal B plan's pass criteria.

---

## 4. Before/after comparison (required deliverable of the fix plans)

The battery exists to make fix impact measurable. **When any Goal A (speed) or Goal B (accuracy) fix is implemented, the plan's final step must present a clear before→after comparison of the battery numbers** — a table or explicit deltas, not buried prose. Anchors:

- F1 `heal_attempts`: 3 (before) → target ~0 after Goal A (unchanged fields stop healing).
- F3 `wrong_heal_count` / disambiguation mode: F3's measured baseline → target 0 wrong + correct `healed_onto` after Goal B.
- F2 `wrong_heal_count`: 0 (before) → must remain 0 (no regression).

This is a standing requirement, recorded for future sessions.

---

## 5. Risks / non-decisions

- **F3 might reveal mode 2 (refusal), not mode 1 (wrong heal).** Then Goal B's framing shifts from "stop mis-healing" to "heal correctly where it currently refuses" — both are improvements, but the plan must be written against the real mode. This is the reason F3 is observe-first. [Certain this matters]
- **Spatial swap only.** If the real HDB failure is two *visually identical, adjacent* radios (no swap), that is a separate stressor. Deferred until F3's number argues for it. [Likely sufficient for now]
- **Resolver tolerance.** The swap keeps radios 140px apart (same as v1), so `tol=60` still cleanly distinguishes them; no resolver change needed. [Certain]
