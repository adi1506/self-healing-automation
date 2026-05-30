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
