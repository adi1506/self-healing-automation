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


# Known fixture node geometry: center point (x, y) of each control, per
# variant, computed from the fixtures' fixed left/top/width/height. Used to
# resolve which ground-truth node a heal landed on WITHOUT relying on
# data-truth (which buildFingerprint does not capture into fingerprints).
_NODE_CENTERS = {
    "v1": {
        "radio-individual":     (256, 306),   # node-70: 240+16, 290+16
        "radio-non-individual": (396, 306),   # node-72: 380+16, 290+16
        "mobile":               (968, 313),   # node-80: 760+208, 286+27
        "chevron":              (1152, 312),  # node-90: 1140+12, 300+12
    },
    "v2": {
        "radio-individual":     (256, 346),   # node-44: 240+16, 330+16
        "radio-non-individual": (436, 346),   # node-46: 420+16, 330+16
        "mobile":               (968, 353),   # node-50: 760+208, 326+27
        "chevron":              (1152, 352),  # node-60: 1140+12, 340+12
    },
    "v3": {
        "radio-individual":     (396, 306),   # node-50: 380+16, 290+16 (moved right)
        "radio-non-individual": (256, 306),   # node-52: 240+16, 290+16 (moved left)
        "mobile":               (968, 313),   # node-60: 760+208, 286+27 (unchanged)
        "chevron":              (1152, 312),  # node-70: 1140+12, 300+12 (unchanged)
    },
}


def _nearest_truth(cx, cy, variant, tol=60):
    """Nearest known fixture-node identity to point (cx, cy) in `variant`,
    or None if the nearest is farther than `tol` px."""
    centers = _NODE_CENTERS.get(variant, {})
    best, best_d = None, None
    for truth, (tx, ty) in centers.items():
        d = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
        if best_d is None or d < best_d:
            best, best_d = truth, d
    return best if best_d is not None and best_d <= tol else None


def _recorded_truth(step, variant, tol=60):
    """Ground-truth identity of the node a RECORDED step targeted, resolved
    by the recorded element's bbox center against `variant` geometry."""
    if step.element is None:
        return None
    bbox = step.element.attributes.get("bbox") or {}
    if not bbox:
        return None
    cx = bbox.get("x", 0) + bbox.get("width", 0) / 2
    cy = bbox.get("y", 0) + bbox.get("height", 0) / 2
    return _nearest_truth(cx, cy, variant, tol)


def _truth_of(outcome, step_index, variant, tol=60):
    """Ground-truth identity of the node a step healed onto, resolved by
    geometry (NOT data-truth, which fingerprints don't carry).

    Returns the data-truth name of the nearest known fixture node to the
    healed candidate's bbox center, or None if no heal committed or the
    nearest node is farther than `tol` pixels (ambiguous / off-target).
    """
    if step_index >= len(outcome.step_results):
        return None
    r = outcome.step_results[step_index]
    healed = r.get("healed") or {}
    attrs = healed.get("candidate_attrs") or {}
    bbox = attrs.get("bbox") or {}
    if not bbox:
        return None
    cx = bbox.get("x", 0) + bbox.get("width", 0) / 2
    cy = bbox.get("y", 0) + bbox.get("height", 0) / 2
    return _nearest_truth(cx, cy, variant, tol)


def _healed_bbox(outcome, step_index):
    """Raw healed-candidate bbox for a step (for baseline observation), or None."""
    if step_index >= len(outcome.step_results):
        return None
    healed = (outcome.step_results[step_index].get("healed") or {})
    return (healed.get("candidate_attrs") or {}).get("bbox")


@pytest.mark.asyncio
async def test_F0_record_replay_v1_smoke(tmp_path):
    rec = await record_flutter(str(tmp_path), variant="v1", name="F0")
    assert element_step_count(rec) >= 3, "expected radio + fill + chevron steps"
    outcome, heal_n = await replay_flutter(
        rec, "v1", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))
    assert outcome is not None
    assert heal_n >= 1, "Flutter steps must reach the heal path"


@pytest.mark.asyncio
async def test_F1_baseline_heal_frequency_and_disambiguation(tmp_path, capsys):
    """Record v1, replay v1 UNCHANGED.

    Certain assertion (spec §1.3): every element-bearing step heals, because
    all Flutter locators are ordinal and get stripped. heal_n must equal the
    element-step count — this is the speed baseline Goal A must drive to ~0.

    Disambiguation is recorded as an OBSERVATION (which radio the click
    healed onto, resolved by bbox geometry) for the Goal B plan; not asserted
    here because current bbox-only behavior is exactly what we're measuring.
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
    radio_healed_truth = _truth_of(outcome, radio_idx, "v1") if radio_idx is not None else None

    print("BASELINE_F1", {
        "element_steps": n_elem,
        "heal_attempts": heal_n,
        "radio_step_index": radio_idx,
        "radio_healed_onto": radio_healed_truth,   # want 'radio-individual'
        "radio_healed_bbox": _healed_bbox(outcome, radio_idx) if radio_idx is not None else None,
        "statuses": [r.get("status") for r in outcome.step_results],
        "failed_index": outcome.failed_step_index,
    })

    # CERTAIN assertion: heal fires on every element-bearing step.
    assert heal_n == n_elem, (
        f"expected heal on every element step ({n_elem}), got {heal_n}")


@pytest.mark.asyncio
async def test_F2_relabel_move_baseline(tmp_path, capsys):
    """Record v1, replay against v2 (labels rephrased, radios moved, ids
    renumbered).

    Certain assertion: heal still fires on every element step (same ordinal
    strip). The RELABEL CORRECTNESS — did each control heal onto the node
    with the matching identity — is recorded as the baseline observation
    Goal B must improve, and as the 'zero wrong heals' bar Goal A's
    uniqueness guard must hold. Identity is resolved by bbox geometry
    (recorded step against v1, healed candidate against v2), because
    fingerprints do not carry data-truth.
    """
    rec = await record_flutter(str(tmp_path), variant="v1", name="F2")
    n_elem = element_step_count(rec)
    outcome, heal_n = await replay_flutter(
        rec, "v2", str(tmp_path), recording_path=str(tmp_path / "recording.yaml"))

    per_step = []
    for s in rec.steps:
        if s.element is None:
            continue
        recorded_truth = _recorded_truth(s, "v1")
        healed_onto = _truth_of(outcome, s.index, "v2")
        per_step.append({
            "index": s.index,
            "action": s.action,
            "recorded_role": s.element.attributes.get("role") or "",
            "recorded_truth": recorded_truth,
            "status": outcome.step_results[s.index].get("status")
                      if s.index < len(outcome.step_results) else "missing",
            "healed_onto": healed_onto,
            "healed_bbox": _healed_bbox(outcome, s.index),
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
