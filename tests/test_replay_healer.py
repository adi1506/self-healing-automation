"""Unit tests for the replay-time healer.

These tests cover the pure scoring and decision logic — no browser needed.
Browser-driven integration is covered in tests/test_replay.py and
tests/test_recording_e2e.py.
"""
from __future__ import annotations
import pytest
from core.recording import ElementFingerprint
from core.replay_healer import (
    score_candidate,
    select_match,
    HealDecision,
    is_action_compatible,
    urls_compatible,
    SCORE_THRESHOLD,
    GRAY_LOW,
    MARGIN_REQ,
)


def _fp(**attrs) -> ElementFingerprint:
    """Build a fingerprint from a flat dict of attribute overrides."""
    base = {
        "tag": "input",
        "type": "text",
        "id": "",
        "name": "",
        "class": "",
        "placeholder": "",
        "aria_label": "",
        "role": "",
        "text_content": "",
        "nearest_label_text": "",
        "nearest_landmark_text": "",
        "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
        "html5_constraints": {"pattern": "", "required": False, "maxlength": "", "minlength": "", "min": "", "max": ""},
        "autocomplete": "",
        "xpath": "",
        "css_path": "",
        "neighborhood_signature": "",
    }
    for k, v in attrs.items():
        if k in ("pattern", "required", "maxlength", "minlength", "min", "max"):
            base["html5_constraints"][k] = v
        else:
            base[k] = v
    return ElementFingerprint(
        id="el-test",
        primary_locator={"strategy": "css", "value": "input"},
        fallback_locators=[],
        attributes=base,
        page_context={"url": "https://example.com/form", "section_label": attrs.get("nearest_landmark_text", "")},
    )


# --- score_candidate -------------------------------------------------------

def test_identical_fingerprints_score_max():
    fp = _fp(id="phone", name="phone", type="tel", placeholder="555-1212",
             nearest_label_text="Phone Number", autocomplete="tel")
    score, _matched = score_candidate(fp, fp)
    assert score >= 0.95


def test_id_renamed_but_label_and_name_stable_scores_high():
    stored = _fp(id="phone", name="phone", type="tel",
                 nearest_label_text="Phone Number", autocomplete="tel")
    current = _fp(id="phone_number", name="phone", type="tel",
                  nearest_label_text="Phone Number", autocomplete="tel")
    score, matched = score_candidate(stored, current)
    assert score >= SCORE_THRESHOLD
    assert "nearest_label_text" in matched
    assert "autocomplete" in matched


def test_name_renamed_but_id_and_label_stable_scores_high():
    stored = _fp(id="phone", name="phone", type="tel", nearest_label_text="Phone Number")
    current = _fp(id="phone", name="phone_number", type="tel", nearest_label_text="Phone Number")
    score, _ = score_candidate(stored, current)
    assert score >= SCORE_THRESHOLD


def test_label_rephrase_with_stable_name_and_autocomplete_scores_high():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone", autocomplete="tel")
    current = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    score, _ = score_candidate(stored, current)
    assert score >= SCORE_THRESHOLD


def test_placeholder_rephrase_alone_does_not_crash_score():
    stored = _fp(name="email", type="email", placeholder="Enter email",
                 nearest_label_text="Email", autocomplete="email")
    current = _fp(name="email", type="email", placeholder="Enter your email address",
                  nearest_label_text="Email Address", autocomplete="email")
    score, _ = score_candidate(stored, current)
    assert score >= SCORE_THRESHOLD


def test_unrelated_fields_score_low():
    stored = _fp(id="phone", name="phone", type="tel", nearest_label_text="Phone Number")
    current = _fp(id="zip", name="zip", type="text", nearest_label_text="Zip Code")
    score, _ = score_candidate(stored, current)
    assert score < GRAY_LOW


def test_autocomplete_match_carries_weight():
    # No id/name/label overlap but autocomplete agrees — should still beat random
    stored = _fp(id="a", name="a", type="tel", autocomplete="tel", nearest_label_text="X")
    similar = _fp(id="b", name="b", type="tel", autocomplete="tel", nearest_label_text="Y")
    different = _fp(id="c", name="c", type="text", autocomplete="email", nearest_label_text="Z")
    s_sim, _ = score_candidate(stored, similar)
    s_diff, _ = score_candidate(stored, different)
    assert s_sim > s_diff


def test_tag_mismatch_penalised():
    stored = _fp(tag="input", type="text", name="addr", nearest_label_text="Address")
    candidate = _fp(tag="textarea", type="", name="addr", nearest_label_text="Address")
    score, _ = score_candidate(stored, candidate)
    # Still scoreable (textarea is fill-compatible) but lower than identical-tag match
    same_tag = _fp(tag="input", type="text", name="addr", nearest_label_text="Address")
    same_score, _ = score_candidate(stored, same_tag)
    assert same_score > score


# --- is_action_compatible -------------------------------------------------

def test_fill_compatible_with_text_input():
    assert is_action_compatible("fill", _fp(tag="input", type="text"))


def test_fill_compatible_with_textarea():
    assert is_action_compatible("fill", _fp(tag="textarea", type=""))


def test_fill_compatible_with_role_textbox():
    assert is_action_compatible("fill", _fp(tag="div", type="", role="textbox"))


def test_fill_not_compatible_with_button():
    assert not is_action_compatible("fill", _fp(tag="button", type=""))


def test_fill_not_compatible_with_select():
    assert not is_action_compatible("fill", _fp(tag="select", type=""))


def test_fill_not_compatible_with_checkbox():
    assert not is_action_compatible("fill", _fp(tag="input", type="checkbox"))


def test_select_compatible_with_select_tag():
    assert is_action_compatible("select", _fp(tag="select", type=""))


def test_select_compatible_with_role_combobox():
    assert is_action_compatible("select", _fp(tag="div", type="", role="combobox"))


def test_click_compatible_with_button_and_anchor():
    assert is_action_compatible("click", _fp(tag="button"))
    assert is_action_compatible("click", _fp(tag="a"))
    assert is_action_compatible("click", _fp(tag="div", role="button"))


def test_check_compatible_with_checkbox():
    assert is_action_compatible("check", _fp(tag="input", type="checkbox"))
    assert is_action_compatible("check", _fp(tag="input", type="radio"))


# --- urls_compatible ------------------------------------------------------

def test_urls_compatible_when_host_and_path_match():
    assert urls_compatible("https://app.example.com/checkout", "https://app.example.com/checkout?step=2")


def test_urls_incompatible_when_path_differs():
    assert not urls_compatible("https://app.example.com/checkout", "https://app.example.com/error")


def test_urls_incompatible_when_host_differs():
    assert not urls_compatible("https://a.example.com/x", "https://b.example.com/x")


def test_urls_compatible_when_stored_url_empty():
    # No stored context = no check
    assert urls_compatible("", "https://app.example.com/x")


# --- select_match (decision) ---------------------------------------------

def test_select_match_picks_high_confidence_with_margin():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    good = _fp(name="phone_number", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    bad = _fp(name="email", type="email", nearest_label_text="Email", autocomplete="email")
    decision = select_match(stored, [good, bad], action="fill", ai_matcher=None)
    assert decision.method == "auto"
    assert decision.matched_candidate is good
    assert decision.confidence >= SCORE_THRESHOLD


def test_select_match_unresolved_when_top_is_low():
    # When all candidates score below REMOVED_FLOOR AND the rename-guard
    # misses, classification is `field_removed` — the field appears
    # genuinely gone, not just renamed. REMOVED_FLOOR is tied to GRAY_LOW
    # (no dead zone), so any score that didn't reach AI consultation lands
    # in this branch by default.
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number")
    bad1 = _fp(name="email", type="email", nearest_label_text="Email")
    bad2 = _fp(name="zip", type="text", nearest_label_text="Zip")
    decision = select_match(stored, [bad1, bad2], action="fill", ai_matcher=None)
    assert decision.method == "field_removed"
    assert decision.matched_candidate is None


def test_select_match_unresolved_when_margin_too_small():
    # Two equally-good candidates — pick neither
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    twin_a = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    twin_b = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    decision = select_match(stored, [twin_a, twin_b], action="fill", ai_matcher=None)
    # Both ~1.0; margin = 0 < MARGIN_REQ
    assert decision.method == "unresolved"
    assert "margin" in decision.diagnostics.lower() or "ambiguous" in decision.diagnostics.lower()


def test_select_match_skips_action_incompatible_candidates():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number")
    incompatible = _fp(tag="button", type="", name="phone", nearest_label_text="Phone Number")
    decision = select_match(stored, [incompatible], action="fill", ai_matcher=None)
    assert decision.method == "unresolved"


def test_select_match_scopes_by_landmark_when_present():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number",
                 nearest_landmark_text="Billing Address")
    in_scope = _fp(name="phone2", type="tel", nearest_label_text="Phone Number",
                   nearest_landmark_text="Billing Address")
    out_of_scope = _fp(name="phone3", type="tel", nearest_label_text="Phone Number",
                       nearest_landmark_text="Shipping Address")
    decision = select_match(stored, [out_of_scope, in_scope], action="fill", ai_matcher=None)
    # in-scope should win even though both score similarly on attributes,
    # because out-of-scope is filtered.
    assert decision.method == "auto"
    assert decision.matched_candidate is in_scope


def test_select_match_falls_through_when_no_candidate_in_scope():
    # If landmark filter would drop everyone, don't filter.
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number",
                 nearest_landmark_text="Old Section Name")
    candidate = _fp(name="phone", type="tel", nearest_label_text="Phone Number",
                    nearest_landmark_text="Renamed Section")
    decision = select_match(stored, [candidate], action="fill", ai_matcher=None)
    assert decision.method == "auto"
    assert decision.matched_candidate is candidate


def test_select_match_returns_top_three_in_diagnostics():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number")
    c1 = _fp(name="email", nearest_label_text="Email")
    c2 = _fp(name="zip", nearest_label_text="Zip")
    c3 = _fp(name="city", nearest_label_text="City")
    decision = select_match(stored, [c1, c2, c3], action="fill", ai_matcher=None)
    # Diagnostics should mention at least the top candidate's label
    assert "Email" in decision.diagnostics or "Zip" in decision.diagnostics or "City" in decision.diagnostics


def test_heal_decision_carries_new_locators_picked_from_candidate():
    stored = _fp(name="phone", type="tel", nearest_label_text="Phone Number", autocomplete="tel")
    good = _fp(id="phone_v2", name="phone_number", type="tel",
               nearest_label_text="Phone Number", autocomplete="tel")
    decision = select_match(stored, [good], action="fill", ai_matcher=None)
    assert decision.method == "auto"
    # New primary should pick the candidate's id (per recorder priority)
    assert decision.new_primary_locator == {"strategy": "id", "value": "phone_v2"}
    # Fallbacks should include the candidate's name
    assert {"strategy": "name", "value": "phone_number"} in decision.new_fallback_locators


def test_heal_decision_preserves_top_k_candidates():
    from core.replay_healer import select_match, CandidateRef
    stored = _fp(
        id="phone_v1", name="phone", nearest_label_text="Phone",
        placeholder="(555) 555-5555", autocomplete="tel",
    )
    cand_good = _fp(
        id="phone_v2", name="phone_number", nearest_label_text="Phone",
        placeholder="(555) 555-5555", autocomplete="tel",
    )
    cand_mid = _fp(
        id="mobile", name="mobile", nearest_label_text="Mobile Number",
        placeholder="(555) 555-5555", autocomplete="tel",
    )
    cand_low = _fp(
        id="zip", name="zip", nearest_label_text="ZIP Code",
        placeholder="12345", autocomplete="postal-code",
    )
    decision = select_match(stored, [cand_good, cand_mid, cand_low], action="fill")
    assert decision.method in ("auto", "ai-confirmed")
    assert isinstance(decision.top_k_candidates, list)
    assert 1 <= len(decision.top_k_candidates) <= 3
    assert isinstance(decision.top_k_candidates[0], CandidateRef)
    # Top must be the actual chosen one
    assert decision.top_k_candidates[0].attributes.get("name") == "phone_number"
    # Scores must be in descending order
    scores = [c.score for c in decision.top_k_candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_attempt_heal_honours_force_candidate_index(monkeypatch):
    """When `force_candidate_index=1` is provided, attempt_heal returns the
    runner-up rather than the top scorer."""
    from core.replay_healer import attempt_heal

    stored = _fp(name="phone", nearest_label_text="Phone", autocomplete="tel")

    top = _fp(name="phone_number", nearest_label_text="Phone", autocomplete="tel")
    runner = _fp(name="mobile", nearest_label_text="Mobile", autocomplete="tel")

    class FakePage:
        url = "https://example.com/form"
        async def evaluate(self, *a, **kw):
            return [
                {
                    "id": "el-1",
                    "primary_locator": {"strategy": "name", "value": "phone_number"},
                    "fallback_locators": [],
                    "attributes": top.attributes,
                    "page_context": {"url": "https://example.com/form", "section_label": ""},
                },
                {
                    "id": "el-2",
                    "primary_locator": {"strategy": "name", "value": "mobile"},
                    "fallback_locators": [],
                    "attributes": runner.attributes,
                    "page_context": {"url": "https://example.com/form", "section_label": ""},
                },
            ]

    page = FakePage()
    # Without override → returns top scorer
    d1 = await attempt_heal(page, stored, action="fill")
    assert d1.new_primary_locator["value"] == "phone_number"

    # With override → returns runner-up
    d2 = await attempt_heal(page, stored, action="fill", force_candidate_index=1)
    assert d2.new_primary_locator["value"] == "mobile"


def test_field_removed_constructor():
    """field_removed is a new method distinct from unresolved — top-score
    was too low AND no rename-guard hit."""
    decision = HealDecision.field_removed(
        diagnostics="best candidate scored 0.22; no autocomplete/name match",
        runner_up_score=0.22,
    )
    assert decision.method == "field_removed"
    assert decision.diagnostics.startswith("best candidate scored")
    assert decision.matched_candidate is None
    assert decision.new_primary_locator is None


def test_rename_guard_matches_autocomplete():
    """If a live candidate shares the stored autocomplete value exactly,
    rename_guard_hit returns True regardless of label/name divergence."""
    from core.replay_healer import _rename_guard_hit
    stored = _fp(autocomplete="tel", name="phone", nearest_label_text="Phone Number")
    candidates = [
        _fp(autocomplete="tel", name="mobile_number", nearest_label_text="Mobile (for SMS)"),
    ]
    assert _rename_guard_hit(stored, candidates) is True


def test_rename_guard_matches_name_non_generic():
    """Non-generic stored `name` (length >= 3, not 'input'/'field') matching
    a live candidate counts as a rename-guard hit."""
    from core.replay_healer import _rename_guard_hit
    stored = _fp(name="customer_phone", autocomplete="")
    candidates = [_fp(name="customer_phone", nearest_label_text="Different label")]
    assert _rename_guard_hit(stored, candidates) is True


def test_rename_guard_ignores_generic_name():
    """Generic stored names like 'input' or 'field' must not trigger the
    guard — they'd false-positive against random unrelated inputs."""
    from core.replay_healer import _rename_guard_hit
    stored = _fp(name="input", autocomplete="")
    candidates = [_fp(name="input")]
    assert _rename_guard_hit(stored, candidates) is False


def test_rename_guard_empty_stored_returns_false():
    """If stored has neither autocomplete nor name, there's nothing to
    guard against — return False (let the score decide)."""
    from core.replay_healer import _rename_guard_hit
    stored = _fp(autocomplete="", name="")
    candidates = [_fp(name="anything")]
    assert _rename_guard_hit(stored, candidates) is False


def test_rename_guard_no_candidate_matches():
    """Stored autocomplete is `tel` but no live candidate has it → False."""
    from core.replay_healer import _rename_guard_hit
    stored = _fp(autocomplete="tel")
    candidates = [_fp(autocomplete="email"), _fp(autocomplete="")]
    assert _rename_guard_hit(stored, candidates) is False


def test_select_match_field_removed_when_score_below_floor_and_no_guard():
    """All candidates score low + rename-guard misses → field_removed."""
    stored = _fp(
        id="phone-input", name="phone", autocomplete="tel",
        nearest_label_text="Phone Number", type="tel",
    )
    candidates = [
        _fp(id="search-q", name="q", autocomplete="off",
            nearest_label_text="Search", type="search"),
        _fp(id="btn-go", name="go", tag="button",
            nearest_label_text="Submit"),
    ]
    decision = select_match(stored, candidates, action="fill")
    assert decision.method == "field_removed"
    assert "below floor" in decision.diagnostics.lower() or \
           "appears removed" in decision.diagnostics.lower()


def test_select_match_unresolved_not_field_removed_when_guard_hits():
    """Top score is BELOW REMOVED_FLOOR but autocomplete matches → the
    rename-guard sub-branch keeps `unresolved` (fails safe) rather than
    classifying as field_removed.

    Fixture design: stored has many present features so unmatched ones
    drag the weighted score down; candidate matches only on autocomplete
    (the high-signal rename-guard attribute) and diverges on everything
    else. Action-compat still passes because `type='text'` is fill-able.
    """
    stored = _fp(
        id="phone-input-zzz", name="phone_number_v2", autocomplete="tel",
        placeholder="(555) 555-5555", aria_label="Phone",
        nearest_label_text="Phone Number", type="tel",
        pattern=r"\d{3}-\d{3}-\d{4}", role="textbox",
    )
    # Candidate matches autocomplete only; everything else diverges.
    candidates = [
        _fp(
            id="abcdefg", name="xyz", autocomplete="tel",
            placeholder="Search products and brands today",
            aria_label="Quick site lookup",
            nearest_label_text="What are you looking for here today?",
            type="text",  # diverges from 'tel' but still fill-compatible
            pattern="", role="searchbox",
        ),
    ]
    decision = select_match(stored, candidates, action="fill")
    # Must be unresolved (not field_removed) AND diagnostics must mention
    # the rename-guard fired — otherwise we'd be passing via the wrong
    # code path (e.g. gray-zone fallthrough).
    assert decision.method == "unresolved"
    assert "rename-guard" in decision.diagnostics.lower()


def test_select_match_gray_zone_unchanged():
    """When the top score is in [REMOVED_FLOOR, SCORE_THRESHOLD) and AI is
    off / unavailable, classification stays `unresolved` (gray zone with
    no confirmation) and does NOT fall through to `field_removed`.

    REMOVED_FLOOR is now tied to GRAY_LOW, so this also pins that the gray
    zone is the only `unresolved` band — anything below GRAY_LOW becomes
    `field_removed` (or rename-guard `unresolved` if guard fires)."""
    stored = _fp(name="phone", nearest_label_text="Phone Number", type="tel")
    candidates = [
        _fp(name="phone_secondary", nearest_label_text="Phone (alt)", type="tel"),
    ]
    decision = select_match(stored, candidates, action="fill")
    assert decision.method == "unresolved"
    # Must not have come through the below-floor (field_removed/guard-hit) branch.
    assert "below removed-floor" not in decision.diagnostics.lower()
    assert "rename-guard" not in decision.diagnostics.lower()
